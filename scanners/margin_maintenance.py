"""單檔融資維持率查詢 — 核心模組（同步 requests，移植自 0718 FastAPI 專案）。

演算法（純函式）照抄自 0718，已於該專案驗證：
  * compute_maintenance_ratio / classify_warning / trim_recent_continuous（維持率與警戒）
  * roll_cost（融資成本加權平均成本法單日遞迴）
  * validate_stock_code / CodeError（代號驗證）
  * detect_session（交易時段判斷）

資料策略：以本網站既有 DB（db/scanner.db，唯讀）為主，缺的才即時抓線上：
  * N 日收盤、最新收盤、單日全市場收盤 → DB。
  * 融資買進/餘額全市場單日快照 → TWSE MI_MARGN + TPEx margin balance（唯一碰網路處之一）。
  * 現價（盤中即時）→ TWSE MIS getStockInfo（best-effort，抓不到退回 DB 收盤）。

融資成本以種子（data/margin_cost_seed.json，2026-07-17，1744 檔）逐日滾到今日；
歷史交易日融資快照永久快取到 data/margin_snapshots/，整體滾動結果每日快取到
data/margin_cost_live.json，讓第二次查詢是毫秒級。抓不到融資快照時，成本優雅
降級為 N 日均價，仍能算出維持率，不 crash。

CLI 自測：`python -m scanners.margin_maintenance 2330`
"""

from __future__ import annotations

import os
import re
import json
import math
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# 路徑
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_THIS_DIR)
try:
    from config import DB_PATH  # 網站慣例
except Exception:  # pragma: no cover - 保底
    DB_PATH = os.path.join(PROJECT_ROOT, 'db', 'scanner.db')

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
SEED_PATH = os.path.join(DATA_DIR, 'margin_cost_seed.json')
SNAP_DIR = os.path.join(DATA_DIR, 'margin_snapshots')
LIVE_CACHE_PATH = os.path.join(DATA_DIR, 'margin_cost_live.json')

# ---------------------------------------------------------------------------
# 常數（照抄 0718 config）
# ---------------------------------------------------------------------------
MARGIN_RATE = 0.6          # 台股融資成數
DEFAULT_N = 20             # N 日均價預設天數
N_MIN = 1
N_MAX = 250
WARN_DANGER = 130.0        # < 130 追繳
WARN_SAFE = 166.67         # >= 166.67 安全（回本基準，1/0.6）
SPLIT_STEP = 0.35          # 除權/分割斷點門檻（相鄰日變動 >35% 視為未還原價斷點）

HTTP_TIMEOUT = 8
_MAX_ROLL_DAYS = 400
_RESULT_TTL = 300          # 秒，整體滾動結果記憶體快取存活時間

# 交易時段
SESSION_START = time(9, 0)
SESSION_END = time(13, 30)
TZ = "Asia/Taipei"

# ---------------------------------------------------------------------------
# 資料源端點 / Header（照抄 0718 config）
# ---------------------------------------------------------------------------
TWSE_MI_MARGN_URL = (
    "https://www.twse.com.tw/exchangeReport/MI_MARGN"
    "?response=json&date={date}&selectType=ALL"
)
TPEX_MARGIN_BALANCE_URL = (
    "https://www.tpex.org.tw/www/zh-tw/margin/balance"
    "?date={date}&response=json&id="
)
TPEX_MARGIN_REFERER = "https://www.tpex.org.tw/zh-tw/mainboard/margin/balance.html"
MIS_STOCK_INFO_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    "?ex_ch={prefix}_{code}.tw&json=1&delay=0"
)
MIS_REFERER = "https://mis.twse.com.tw/stock/index.jsp"
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

_WARRANT_PREFIX = "91"
_CODE_PATTERN = re.compile(r"^\d{4}$")

DISCLAIMER = ("融資成本以加權平均成本法推估(種子2026-07-17逐日滾動)，"
              "非真實對帳成本，僅供參考。")


# ===========================================================================
# 例外
# ===========================================================================
class CodeError(ValueError):
    """代號格式或種類不支援時拋出，帶原始 `code` 與人類可讀 `reason`。"""

    def __init__(self, reason: str, code: str) -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


class MarketNotFoundError(Exception):
    """DB 查無此代號時拋出。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("查無此代號：%s" % code)


# ===========================================================================
# 純函式（照抄 0718，無 I/O）
# ===========================================================================
def validate_stock_code(code: str) -> str:
    """驗證並正規化股票代號（4 碼數字；91 開頭權證不支援）。"""
    normalized = (code or "").strip()
    if not _CODE_PATTERN.match(normalized):
        raise CodeError("代號需為 4 碼數字", code)
    if normalized.startswith(_WARRANT_PREFIX):
        raise CodeError("不支援 91 開頭（權證/牛熊證）", code)
    return normalized


def trim_recent_continuous(closes, max_step=SPLIT_STEP):
    """回傳「最近一段連續、無公司行為斷點」的收盤序列（由舊到新）。

    自最新往回掃，遇相鄰兩日變動 >max_step（預設 35%）視為除權/分割斷點即截斷。
    回傳 `(trimmed, was_trimmed)`。
    """
    if len(closes) <= 1:
        return list(closes), False

    kept = [closes[-1]]
    trimmed = False
    for i in range(len(closes) - 2, -1, -1):
        newer = kept[-1]
        older = closes[i]
        if older <= 0 or newer <= 0:
            trimmed = True
            break
        if abs(newer - older) / older > max_step:
            trimmed = True
            break
        kept.append(older)

    kept.reverse()
    return kept, trimmed


def compute_maintenance_ratio(price, n_avg, rate=MARGIN_RATE):
    """維持率 = price / (n_avg * rate) * 100（四捨五入 2 位）。

    price 為 None，或 n_avg 為 None/<=0 時回 None（避免 Inf/NaN）。
    """
    if price is None or n_avg is None or n_avg <= 0:
        return None
    return round(price / (n_avg * rate) * 100, 2)


def classify_warning(ratio):
    """依維持率分警戒等級：None→na；<130→danger；>=166.67→safe；其餘→warn。"""
    if ratio is None:
        return "na"
    if ratio < WARN_DANGER:
        return "danger"
    if ratio >= WARN_SAFE:
        return "safe"
    return "warn"


def roll_cost(prev, buy, balance, close):
    """單日遞迴：以今日買進佔今日餘額為權重，把成本朝今日收盤移動。

    balance<=0 回 prev（無部位不更新）；權重 clamp 至 [0,1]。
    """
    if balance <= 0:
        return prev
    w = buy / balance
    if w < 0:
        w = 0.0
    elif w > 1:
        w = 1.0
    return prev + w * (close - prev)


def detect_session(now=None):
    """回傳 "intraday" 或 "closed"（僅週一~五 09:00~13:30 Asia/Taipei 為 intraday）。"""
    tz = ZoneInfo(TZ)
    current = datetime.now(tz) if now is None else (
        now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz))
    if current.weekday() >= 5:
        return "closed"
    if SESSION_START <= current.time() <= SESSION_END:
        return "intraday"
    return "closed"


# ===========================================================================
# 種子
# ===========================================================================
_seed_cache = None


def load_seed():
    """載入種子融資成本與種子日期，回 `(dict{code:float}, date)`。缺檔回 `({}, 很舊日期)`。"""
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache
    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        cost = {str(k): float(v) for k, v in raw.get("cost", {}).items()}
        seed_date = datetime.strptime(raw["seed_date"], "%Y-%m-%d").date()
    except Exception:
        cost, seed_date = {}, date(2000, 1, 1)
    _seed_cache = (cost, seed_date)
    return _seed_cache


# ===========================================================================
# DB 存取（唯讀，比照 scanners/deleveraging.py）
# ===========================================================================
def _db_connect():
    uri = 'file:%s?mode=ro&immutable=1' % DB_PATH.replace('\\', '/')
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_stock_info(conn, code):
    """查 stocks → (name, market)；查無回 None。market ∈ {'twse','tpex'}。"""
    row = conn.execute(
        "SELECT name, market FROM stocks WHERE stock_id = ?", (code,)).fetchone()
    if row is None:
        return None
    return row["name"], row["market"]


def _db_recent_closes(conn, code, n):
    """最近 n 筆有效 close_price（由舊到新）。"""
    rows = conn.execute(
        "SELECT close_price FROM daily_prices "
        "WHERE stock_id = ? AND close_price IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", (code, n)).fetchall()
    closes = [float(r["close_price"]) for r in rows]
    closes.reverse()
    return closes


def _db_latest_close(conn, code):
    """最新一筆 (close_price, date)；無回 None。"""
    row = conn.execute(
        "SELECT close_price, date FROM daily_prices "
        "WHERE stock_id = ? AND close_price IS NOT NULL "
        "ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    if row is None:
        return None
    return float(row["close_price"]), row["date"]


def _db_closes_on_date(conn, date_str):
    """某日全市場 {code: close_price}。"""
    rows = conn.execute(
        "SELECT stock_id, close_price FROM daily_prices "
        "WHERE date = ? AND close_price IS NOT NULL", (date_str,)).fetchall()
    return {r["stock_id"]: float(r["close_price"]) for r in rows}


def _db_trading_dates_after(conn, date_str):
    """daily_prices 中 > date_str 的 DISTINCT 交易日（'YYYY-MM-DD' 升冪）。"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_prices WHERE date > ? ORDER BY date",
        (date_str,)).fetchall()
    return [r["date"] for r in rows]


def _db_latest_trading_date(conn):
    """daily_prices 最新交易日（'YYYY-MM-DD'）；無資料回 None。"""
    row = conn.execute("SELECT MAX(date) AS d FROM daily_prices").fetchone()
    return row["d"] if row and row["d"] else None


def _db_latest_complete_date(conn):
    """最近一個「資料完整」的交易日（'YYYY-MM-DD'）；無資料回 None。

    今天收盤在 DB 常還沒灌齊，用無條件 MAX(date) 會漏掉大半市場。改用「相鄰
    交易日比較」規則（不看絕對量、只看當日相對前一日是否已灌齊，避免完整日
    之間的自然筆數差異誤殺）：

    取最近數個 DISTINCT date（DESC）與各自有效收盤檔數 count。
    從最新日 d0 起：若存在前一交易日 d1 且 count[d0] >= THRESHOLD*count[d1] → 採用 d0；
    否則視 d0 未灌齊，往前退一天，對 d1 vs d2 重複……取第一個「相對其前一日
    已達門檻」的日期。邊界：只有 1 天資料 → 回那天；退到最舊仍無達標 → 回最舊那天。

    THRESHOLD=0.90：以實測相鄰筆數比校準——盤中未灌齊的當日 07-24/07-23=0.857
    需被剔除，而 legit 完整交易日之間的自然波動（07-23/07-22=0.9485、
    07-20/07-17=0.928）需保留。0.90 可乾淨切開兩者；0.95 會誤殺 0.9485/0.928
    的完整日、退到過舊的價格日。
    """
    THRESHOLD = 0.90
    rows = conn.execute(
        "SELECT date, COUNT(close_price) AS c FROM daily_prices "
        "GROUP BY date ORDER BY date DESC LIMIT 8").fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]["date"]
    for i in range(len(rows) - 1):  # 由新到舊，rows[i] vs 前一交易日 rows[i+1]
        cur = rows[i]["c"] or 0
        prev = rows[i + 1]["c"] or 0
        if prev <= 0 or cur >= THRESHOLD * prev:
            return rows[i]["date"]
    return rows[-1]["date"]  # 退到最舊仍無達標 → 回最舊那天


def _db_all_latest_closes(conn, date_str):
    """某日（通常最新交易日）全市場 {code: close_price}。"""
    return _db_closes_on_date(conn, date_str)


def _db_all_stock_info(conn):
    """整張 stocks → {code: (name, market)}。"""
    rows = conn.execute("SELECT stock_id, name, market FROM stocks").fetchall()
    return {r["stock_id"]: (r["name"], r["market"]) for r in rows}


def _db_bulk_navg(conn, codes, n):
    """對 codes 一次算 N 日均價 → {code: navg}。

    取最近約 ceil(n*1.6) 個交易日的全市場收盤，Python 端 group by code、
    每檔套 trim_recent_continuous 後取最近 N 筆算術平均。codes 為空回 {}。
    """
    codes = set(codes)
    if not codes:
        return {}
    lookback = max(n + 5, math.ceil(n * 1.6))
    date_rows = conn.execute(
        "SELECT DISTINCT date FROM daily_prices ORDER BY date DESC LIMIT ?",
        (lookback,)).fetchall()
    dates = [r["date"] for r in date_rows]  # 由新到舊
    if not dates:
        return {}
    oldest = dates[-1]
    # 一次拉回窗內全市場收盤（由舊到新，方便直接組序列）
    rows = conn.execute(
        "SELECT stock_id, date, close_price FROM daily_prices "
        "WHERE date >= ? AND close_price IS NOT NULL ORDER BY date ASC",
        (oldest,)).fetchall()
    series = {}
    for r in rows:
        code = r["stock_id"]
        if code not in codes:
            continue
        series.setdefault(code, []).append(float(r["close_price"]))
    result = {}
    for code, closes in series.items():
        recent = closes[-n:] if len(closes) > n else closes
        trimmed, _ = trim_recent_continuous(recent)
        if trimmed:
            result[code] = round(sum(trimmed) / len(trimmed), 4)
    return result


# ===========================================================================
# 日期工具
# ===========================================================================
def _to_ymd(date_str):
    """'YYYY-MM-DD' → 'YYYYMMDD'（TWSE 用）。"""
    return date_str.replace("-", "")


def _to_ad_slash(date_str):
    """'YYYY-MM-DD' → 西元 'YYYY/MM/DD'（TPEx 用）。"""
    return date_str.replace("-", "/")


def _today():
    return datetime.now(ZoneInfo(TZ)).date()


# ===========================================================================
# 即時抓取（唯一碰網路處，只抓 DB 缺的）
# ===========================================================================
def _fetch_all_margin_twse(date_str):
    """上市全市場單日融資（TWSE MI_MARGN）→ {code:(買進張, 今日餘額張)}。

    tables[1]：買進 idx2、今日餘額 idx6。失敗/非交易日回 {}。
    """
    url = TWSE_MI_MARGN_URL.format(date=_to_ymd(date_str))
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("stat") not in (None, "OK"):
        return {}
    tables = data.get("tables")
    if not isinstance(tables, list) or len(tables) < 2:
        return {}
    result = {}
    for row in tables[1].get("data") or []:
        if len(row) <= 6:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        try:
            buy = int(str(row[2]).replace(",", "").strip())
            bal = int(str(row[6]).replace(",", "").strip())
        except ValueError:
            continue
        result[code] = (buy, bal)
    return result


def _fetch_all_margin_tpex(date_str):
    """上櫃全市場單日融資（TPEx margin balance）→ {code:(資買張, 資餘額張)}。

    tables[0]：資買 idx3、資餘額 idx6。失敗/非交易日回 {}。
    """
    url = TPEX_MARGIN_BALANCE_URL.format(date=_to_ad_slash(date_str))
    headers = dict(DEFAULT_HEADERS)
    headers["Referer"] = TPEX_MARGIN_REFERER
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    tables = data.get("tables")
    if not isinstance(tables, list) or not tables:
        return {}
    result = {}
    for row in tables[0].get("data") or []:
        if len(row) <= 6:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        try:
            buy = int(str(row[3]).replace(",", "").strip())
            bal = int(str(row[6]).replace(",", "").strip())
        except ValueError:
            continue
        result[code] = (buy, bal)
    return result


def _fetch_margin_snapshot(date_str):
    """某日全市場（上市+上櫃合併）融資 → {code:(buy_lots, bal_lots)}。

    歷史日（date < 今天）永久快取到 data/margin_snapshots/{YYYYMMDD}.json；今天不快取。
    合併結果為空時視為非交易日/失敗，不寫快取。任一市場失敗僅該市場缺漏。
    """
    is_history = date_str < _today().isoformat()
    cache_file = os.path.join(SNAP_DIR, "%s.json" % _to_ymd(date_str))

    if is_history and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                raw = json.load(f)
            return {k: (int(v[0]), int(v[1])) for k, v in raw.items()}
        except Exception:
            pass  # 快取損毀 → 重抓

    merged = {}
    merged.update(_fetch_all_margin_twse(date_str))
    merged.update(_fetch_all_margin_tpex(date_str))

    if is_history and merged:
        try:
            os.makedirs(SNAP_DIR, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({k: [v[0], v[1]] for k, v in merged.items()},
                          f, ensure_ascii=False)
        except Exception:
            pass
    return merged


def _to_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_realtime_price(code, market):
    """best-effort 打 MIS getStockInfo 取現價（z；無成交'-'→用 y 昨收 fallback）。

    market 'twse'→prefix 'tse'、'tpex'→'otc'。失敗回 None。
    """
    prefix = "tse" if market == "twse" else "otc"
    url = MIS_STOCK_INFO_URL.format(prefix=prefix, code=code)
    headers = dict(DEFAULT_HEADERS)
    headers["Referer"] = MIS_REFERER
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        msg_array = data.get("msgArray") if isinstance(data, dict) else None
        if not msg_array:
            return None
        msg = msg_array[0]
    except Exception:
        return None
    try:
        value = _to_float(msg.get("z"))
        if value is None:
            value = _to_float(msg.get("y"))  # 昨收 fallback
        if value is None:
            return None
        return value
    except Exception:
        return None


# ===========================================================================
# 滾動引擎
# ===========================================================================
# 整體滾動結果記憶體快取：today_key -> (computed_at, costs, last_margin)
_result_cache = {}


def _read_live_cache(today_key):
    if not os.path.exists(LIVE_CACHE_PATH):
        return None
    try:
        with open(LIVE_CACHE_PATH, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if payload.get("date") != today_key:
        return None
    return payload


def _write_live_cache(today_key, costs, last_margin):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LIVE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": today_key,
                       "computed_at": datetime.now(ZoneInfo(TZ)).isoformat(),
                       "costs": costs,
                       "last_margin": last_margin}, f, ensure_ascii=False)
    except Exception:
        pass


def compute_current_costs(today=None):
    """從種子滾動到今日，回 `{code:{'value','as_of','roll_days'}}`。

    對 DB 中 > 種子日的每個交易日：抓融資快照（缺則 skip 該日）、DB 當日收盤，
    對每檔 costs[code]=roll_cost(prev, buy, bal, close)（任一缺 skip 該檔該日）。
    記憶體 TTL 300s + 落地 data/margin_cost_live.json（keyed by today）。
    """
    if today is None:
        today = _today()
    today_key = today.isoformat()
    now = datetime.now(ZoneInfo(TZ))

    cached = _result_cache.get(today_key)
    if cached is not None and (now - cached[0]).total_seconds() < _RESULT_TTL:
        return cached[1]

    disk = _read_live_cache(today_key)
    if disk is not None and isinstance(disk.get("costs"), dict):
        costs = disk["costs"]
        last_margin = disk.get("last_margin") or {"date": None, "balance": {}}
        _result_cache[today_key] = (now, costs, last_margin)
        return costs

    seed_costs, seed_date = load_seed()
    costs_val = dict(seed_costs)
    as_of = seed_date
    roll_days = 0
    last_margin = {"date": None, "balance": {}}

    conn = _db_connect()
    try:
        trading_dates = _db_trading_dates_after(conn, seed_date.isoformat())
        guard = 0
        for d in trading_dates:
            if guard >= _MAX_ROLL_DAYS:
                break
            guard += 1
            margin = _fetch_margin_snapshot(d)
            if not margin:
                continue  # 非交易日/抓不到 → skip
            closes = _db_closes_on_date(conn, d)
            if not closes:
                continue
            for code, prev in list(costs_val.items()):
                bm = margin.get(code)
                px = closes.get(code)
                if bm is None or px is None:
                    continue
                buy, bal = bm
                costs_val[code] = roll_cost(prev, buy, bal, px)
            as_of = datetime.strptime(d, "%Y-%m-%d").date()
            roll_days += 1
            last_margin = {"date": d, "balance": {c: v[1] for c, v in margin.items()}}
    finally:
        conn.close()

    as_of_iso = as_of.isoformat()
    costs = {code: {"value": round(v, 4), "as_of": as_of_iso, "roll_days": roll_days}
             for code, v in costs_val.items()}

    _write_live_cache(today_key, costs, last_margin)
    _result_cache[today_key] = (now, costs, last_margin)
    return costs


def _last_margin_balance(code):
    """從最後一個成功 margin 快照取該檔融資餘額（張）→ (balance_lots|None, date|None)。"""
    today_key = _today().isoformat()
    cached = _result_cache.get(today_key)
    last_margin = None
    if cached is not None:
        last_margin = cached[2]
    if last_margin is None:
        disk = _read_live_cache(today_key)
        if disk is not None:
            last_margin = disk.get("last_margin")
    if not isinstance(last_margin, dict):
        return None, None
    bal = (last_margin.get("balance") or {}).get(code)
    return (int(bal) if bal is not None else None), last_margin.get("date")


# ===========================================================================
# 主入口
# ===========================================================================
def get_stock_maintenance(code, n=DEFAULT_N):
    """查單檔融資維持率，回傳給前端 JS 用的 dict。

    對子資料失敗優雅降級；除 CodeError / MarketNotFoundError 外不裸拋。
    """
    code = validate_stock_code(code)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_N
    n = max(N_MIN, min(N_MAX, n))

    today = _today()
    conn = _db_connect()
    try:
        info = _db_stock_info(conn, code)
        if info is None:
            raise MarketNotFoundError(code)
        name, market = info
        market_label = "上市" if market == "twse" else "上櫃"

        # --- N 日均價 ---
        closes = _db_recent_closes(conn, code, n)
        trimmed_closes, was_trimmed = trim_recent_continuous(closes)
        if trimmed_closes:
            n_avg = round(sum(trimmed_closes) / len(trimmed_closes), 4)
            avg_block = {"value": n_avg, "n": len(trimmed_closes),
                         "trimmed": was_trimmed, "status": "ok"}
        else:
            n_avg = None
            avg_block = {"value": None, "n": 0, "trimmed": False, "status": "no_data"}

        # --- 現價：先即時，退回 DB 收盤 ---
        rt = _fetch_realtime_price(code, market)
        if rt is not None:
            session_label = "即時" if detect_session() == "intraday" else "收盤"
            price_block = {"value": round(rt, 2), "source": "即時報價",
                           "date": today.isoformat(), "status": "ok"}
        else:
            session_label = "收盤"
            latest = _db_latest_close(conn, code)
            if latest is not None:
                price_block = {"value": round(latest[0], 2), "source": "DB收盤",
                               "date": latest[1], "status": "ok"}
            else:
                price_block = {"value": None, "source": "DB收盤",
                               "date": None, "status": "no_data"}
        price_value = price_block["value"]
    finally:
        conn.close()

    # --- 成本：加權融資成本，退回 N 日均價 ---
    try:
        costs = compute_current_costs(today)
    except Exception:
        costs = {}
    wc = costs.get(code)
    if wc is not None and wc.get("value") is not None:
        cost_value = round(wc["value"], 2)
        cost_block = {"value": cost_value, "source": "加權融資成本",
                      "method": "weighted", "as_of": wc.get("as_of"),
                      "roll_days": wc.get("roll_days"), "status": "ok"}
    elif n_avg is not None:
        cost_value = n_avg
        cost_block = {"value": cost_value, "source": "N日均價",
                      "method": "n_day_avg", "as_of": None,
                      "roll_days": None, "status": "ok"}
    else:
        cost_value = None
        cost_block = {"value": None, "source": "N日均價",
                      "method": "n_day_avg", "as_of": None,
                      "roll_days": None, "status": "no_data"}

    # --- 維持率 ---
    ratio_value = compute_maintenance_ratio(price_value, cost_value)
    warning = classify_warning(ratio_value)
    if ratio_value is not None:
        ratio_block = {
            "value": ratio_value, "warning": warning,
            "formula": {"price": price_value, "cost": cost_value,
                        "margin_rate": MARGIN_RATE,
                        "expression": "price / (cost * 0.6) * 100"},
            "status": "ok"}
    else:
        ratio_block = {
            "value": None, "warning": "na",
            "formula": {"price": price_value, "cost": cost_value,
                        "margin_rate": MARGIN_RATE, "expression": None},
            "status": "uncomputable"}

    # --- 融資餘額顯示（best-effort）---
    bal_lots, bal_date = _last_margin_balance(code)
    margin_block = {"balance_lots": bal_lots, "date": bal_date,
                    "status": "ok" if bal_lots is not None else "no_data"}

    return {
        "code": code, "name": name, "market": market_label,
        "session": session_label, "n_requested": n,
        "price": price_block,
        "average": avg_block,
        "cost": cost_block,
        "margin": margin_block,
        "ratio": ratio_block,
        "generated_at": datetime.now(ZoneInfo(TZ)).isoformat(),
        "disclaimer": DISCLAIMER,
    }


# ===========================================================================
# 全市場掃描（F-010）
# ===========================================================================
_scan_cache = {}  # (today, n) -> (computed_at, result)


def _latest_margin_snapshot(today):
    """取「最新一份成功的融資快照」→ (snapshot_dict{code:(buy,bal)}, date_iso)。

    從 > 種子日的交易日由新到舊試（含今日），讀已落地的 margin_snapshots/{ymd}.json
    或即時抓取，取第一個非空者。全抓不到回 ({}, None)。
    """
    _, seed_date = load_seed()
    conn = _db_connect()
    try:
        dates = _db_trading_dates_after(conn, seed_date.isoformat())
    finally:
        conn.close()
    # 確保今日也在候選內（DB 可能尚未有今日列）
    today_iso = today.isoformat() if hasattr(today, "isoformat") else str(today)
    if today_iso not in dates:
        dates.append(today_iso)
    for d in reversed(dates):
        snap = _fetch_margin_snapshot(d)
        if snap:
            return snap, d
    return {}, None


def scan_market(n=DEFAULT_N):
    """全市場融資維持率警示清單，依維持率升冪（快跌破追繳線者在前）。

    DB-first：重用 compute_current_costs（券商級加權成本，與單檔頁一致），
    只有 costs 缺的股票才退回 N 日均價。universe/融資餘額取最新一份融資快照。
    記憶體快取 TTL 300s（keyed by (today, n)）。
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_N
    n = max(N_MIN, min(N_MAX, n))

    conn = _db_connect()
    try:
        # 成本滾動用真正最新交易日（種子→今天照舊）
        today_iso = _db_latest_trading_date(conn) or _today().isoformat()
        # 全市場現價用「最近一個資料完整」的交易日（今天常還沒灌齊）
        price_date = _db_latest_complete_date(conn) or today_iso
    finally:
        conn.close()

    now = datetime.now(ZoneInfo(TZ))
    cache_key = (today_iso, price_date, n)
    cached = _scan_cache.get(cache_key)
    if cached is not None and (now - cached[0]).total_seconds() < _RESULT_TTL:
        return cached[1]

    today_date = datetime.strptime(today_iso, "%Y-%m-%d").date()

    # 加權成本（全市場）
    try:
        costs = compute_current_costs(today_date)
    except Exception:
        costs = {}

    # universe + 融資餘額（最新一份融資快照）
    snapshot, snap_date = _latest_margin_snapshot(today_date)
    if snapshot:
        universe = {c: bal for c, (buy, bal) in snapshot.items()
                    if bal > 0 and not c.startswith(_WARRANT_PREFIX)}
    else:
        # 退回：用 costs 的 keys 當 universe，balance 標 None
        universe = {c: None for c in costs.keys() if not c.startswith(_WARRANT_PREFIX)}

    conn = _db_connect()
    try:
        latest_closes = _db_all_latest_closes(conn, price_date)
        info_map = _db_all_stock_info(conn)
        # 只對 universe 中不在 costs 的 code 算 N 日均價（省時）
        navg_codes = [c for c in universe if c not in costs]
        navg_map = _db_bulk_navg(conn, navg_codes, n)
    finally:
        conn.close()

    rows = []
    for code, bal in universe.items():
        price = latest_closes.get(code)
        if price is None:
            continue
        wc = costs.get(code)
        if wc is not None and wc.get("value") is not None:
            cost_value = round(wc["value"], 2)
            cost_source = "加權融資成本"
        else:
            nv = navg_map.get(code)
            if nv is None:
                continue
            cost_value = nv
            cost_source = "N日均價"
        ratio = compute_maintenance_ratio(price, cost_value)
        if ratio is None:
            continue
        info = info_map.get(code)
        if info is not None:
            name, market = info
            market_label = "上市" if market == "twse" else "上櫃"
        else:
            name, market_label = code, ""
        rows.append({
            "code": code, "name": name, "market": market_label,
            "price": round(price, 2), "cost": cost_value,
            "cost_source": cost_source,
            "ratio": ratio, "warning": classify_warning(ratio),
            "balance_lots": int(bal) if bal is not None else None,
        })

    # 維持率升冪（None 已被過濾，全為數值）
    rows.sort(key=lambda r: r["ratio"])

    result = {
        "as_of_price": price_date,
        "as_of_margin": snap_date,
        "n": n,
        "count": len(rows),
        "rows": rows,
        "generated_at": now.isoformat(),
    }
    _scan_cache[cache_key] = (now, result)
    return result


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--scan" in args:
        rest = [a for a in args if a != "--scan"]
        _n = int(rest[0]) if rest else DEFAULT_N
        _res = scan_market(_n)
        print("count=%d  as_of_price=%s  as_of_margin=%s  n=%d" % (
            _res["count"], _res["as_of_price"], _res["as_of_margin"], _res["n"]))
        print("--- 前 15 筆（維持率由低到高）---")
        for r in _res["rows"][:15]:
            print("%-6s %-8s %-4s price=%-9s cost=%-9s(%s) ratio=%-8s %-6s bal=%s" % (
                r["code"], r["name"], r["market"], r["price"], r["cost"],
                r["cost_source"], r["ratio"], r["warning"], r["balance_lots"]))
    else:
        _code = args[0] if args else "2330"
        _n = int(args[1]) if len(args) > 1 else DEFAULT_N
        try:
            _result = get_stock_maintenance(_code, _n)
            print(json.dumps(_result, ensure_ascii=False, indent=2))
        except (CodeError, MarketNotFoundError) as _e:
            print("錯誤：%s" % _e)
