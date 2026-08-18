# -*- coding: utf-8 -*-
"""
去槓桿壓力儀表板 — 公開資料「單日」抓取函式
================================================

原儀表板的整戶維持率等指標來自內網 QUANTDATA 專有資料，公開抓不到。
本模組提供 **純公開資料** 的每日單日代理值抓取函式，供即時管線向前追加。

設計原則
--------
* 每個函式只抓「單一交易日」的值。
* 抓不到（假日 / 尚未公布 / 網路失敗）一律回 None，**絕不 crash、絕不回 0 混充**。
* 全部帶 timeout 與 User-Agent header。
* DB 一律唯讀連線（file:...?mode=ro&immutable=1），絕不寫入（scanner.db 3GB+WAL）。

單位口徑（對齊 data/deleveraging_snapshot.json 的 latest / latest_extra）
------------------------------------------------------------------------
    margin_total        融資餘額市值（兆元）    ≈ Σ(融資餘額張 × 收盤價 × 1000)/1e12
    margin_util         融資使用率（%）         TWSE 個股融資使用率之融資餘額加權平均
    short_balance       融券餘額（萬張）        (TWSE+TPEx 融券今日餘額張)/1e4
    short_margin_ratio  券資比（%）             融券張 / 融資張 × 100
    maint_wavg          大盤融資維持率代理（%） 融資市值 / 官方融資金額 × 100
    maint_low140_share  維持率<140% 融資金額佔比（%）
    maint_low130_share  維持率<130% 融資金額佔比（%）
    close/high/low       加權指數 OHLC
    daytrade_ratio      當沖成交值佔比（%）     TWSE (買占比+賣占比)/2

各公式之研究出處與推導見 docs/margin_maint_proxy_method.md。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "scanner.db"
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "deleveraging_snapshot.json"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tw-stock-scanner/1.0)"}
_TIMEOUT = 25

# 融資成數（維持率分佈代理用）：上市 60%、上櫃 50%（現行主管機關規定）
_MARGIN_RATE_TWSE = 0.60
_MARGIN_RATE_TPEX = 0.50
# 融資成本基準：以近 N 個交易日收盤均價代理「在外融資部位的平均進場價」
_COST_WINDOW = 60


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _norm_date(date_yyyymmdd: str) -> str:
    """'2026-07-09' / '20260709' -> '20260709'"""
    return str(date_yyyymmdd).replace("-", "").replace("/", "").strip()


def _iso_date(date_yyyymmdd: str) -> str:
    """-> 'YYYY-MM-DD'（給 DB 用）"""
    d = _norm_date(date_yyyymmdd)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _roc_slash(date_yyyymmdd: str) -> str:
    """-> 'YYYY/MM/DD'（給 TPEx 新站用；TPEx 已改用西元年）"""
    d = _norm_date(date_yyyymmdd)
    return f"{d[:4]}/{d[4:6]}/{d[6:8]}"


def _to_int(x) -> Optional[int]:
    try:
        s = str(x).replace(",", "").strip()
        if s in ("", "-", "--"):
            return None
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _to_float(x) -> Optional[float]:
    try:
        s = str(x).replace(",", "").strip()
        if s in ("", "-", "--"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _get_json(url: str) -> Optional[dict]:
    """抓 JSON，任何錯誤回 None（不 crash）。"""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        if "json" not in ct.lower():
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("抓取失敗 %s -> %s", url, e)
        return None


def _ro_conn() -> Optional[sqlite3.Connection]:
    """scanner.db 唯讀連線（immutable，絕不寫入）。"""
    try:
        uri = f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:  # noqa: BLE001
        logger.warning("開啟 scanner.db 失敗: %s", e)
        return None


# ---------------------------------------------------------------------------
# 來源：個股收盤價 / 融資成本基準均價（scanner.db, 唯讀）
# ---------------------------------------------------------------------------
def _fetch_close_prices(date_yyyymmdd: str) -> dict:
    """{stock_id: close_price}（當日）。抓不到回 {}。"""
    iso = _iso_date(date_yyyymmdd)
    conn = _ro_conn()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT stock_id, close_price FROM daily_prices "
            "WHERE date = ? AND close_price IS NOT NULL",
            (iso,),
        ).fetchall()
        return {r["stock_id"]: r["close_price"] for r in rows if r["close_price"]}
    except Exception as e:  # noqa: BLE001
        logger.warning("讀取收盤價失敗: %s", e)
        return {}
    finally:
        conn.close()


def _fetch_avg_cost(date_yyyymmdd: str, window: int = _COST_WINDOW) -> dict:
    """{stock_id: 近 window 交易日收盤均價}（含當日以前）。抓不到回 {}。"""
    iso = _iso_date(date_yyyymmdd)
    conn = _ro_conn()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT stock_id, AVG(close_price) AS ac FROM (
                SELECT stock_id, close_price,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_id ORDER BY date DESC
                       ) AS rn
                FROM daily_prices
                WHERE date <= ? AND close_price IS NOT NULL
            ) WHERE rn <= ?
            GROUP BY stock_id
            """,
            (iso, window),
        ).fetchall()
        return {r["stock_id"]: r["ac"] for r in rows if r["ac"]}
    except Exception as e:  # noqa: BLE001
        logger.warning("讀取融資成本均價失敗: %s", e)
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 來源：TWSE / TPEx 融資融券
# ---------------------------------------------------------------------------
def _fetch_twse_margin(date_yyyymmdd: str) -> Optional[dict]:
    """
    TWSE MI_MARGN（selectType=ALL）。
    回 {
        'summary': {'m_lots','s_lots','m_amt_kd'},   # 大盤合計（張 / 融資金額仟元）
        'stocks': [{'id','m_lots','limit','s_lots'}] # 個股
    }
    抓不到回 None。
    """
    d = _norm_date(date_yyyymmdd)
    url = (
        "https://www.twse.com.tw/exchangeReport/MI_MARGN"
        f"?response=json&date={d}&selectType=ALL"
    )
    j = _get_json(url)
    if not j or j.get("stat") != "OK":
        return None
    tables = j.get("tables") or []
    if len(tables) < 2:
        return None

    # 大盤合計（table0）
    summary = {}
    for row in tables[0].get("data") or []:
        item = str(row[0])
        if item.startswith("融資(交"):
            summary["m_lots"] = _to_int(row[5])
        elif item.startswith("融券(交"):
            summary["s_lots"] = _to_int(row[5])
        elif item.startswith("融資金額"):
            summary["m_amt_kd"] = _to_int(row[5])  # 仟元
    if summary.get("m_lots") is None:
        return None

    # 個股（table1）：0代號 1名稱 ... 6融資今餘 7次一限額 ... 12融券今餘
    stocks = []
    for row in tables[1].get("data") or []:
        sid = str(row[0]).strip()
        if not sid or not sid[0].isdigit():
            continue
        stocks.append(
            {
                "id": sid,
                "m_lots": _to_int(row[6]) or 0,
                "limit": _to_int(row[7]) or 0,
                "s_lots": _to_int(row[12]) or 0,
            }
        )
    return {"summary": summary, "stocks": stocks}


def _fetch_tpex_margin(date_yyyymmdd: str) -> Optional[dict]:
    """
    TPEx 上櫃融資融券餘額。
    回 {'summary': {'m_lots','s_lots','m_amt_kd'}, 'stocks': [{'id','m_lots','limit','s_lots'}]}
    抓不到回 None（上櫃缺資料時，可由呼叫端降級為僅 TWSE）。
    """
    dd = _roc_slash(date_yyyymmdd)
    url = (
        "https://www.tpex.org.tw/www/zh-tw/margin/balance"
        f"?date={dd}&type=Daily&response=json"
    )
    j = _get_json(url)
    if not j or str(j.get("stat", "")).lower() not in ("ok", "0"):
        return None
    tables = j.get("tables") or []
    if not tables:
        return None
    table = tables[0]

    # 合計（summary）：['','合計(張)', 前資, 資買, 資賣, 現償, 資餘額(6), ...,券餘額(14)]
    #                  ['','融資金(仟元)', ..., 融資金餘額(6), ...]
    summary = {}
    for row in table.get("summary") or []:
        label = str(row[1]) if len(row) > 1 else ""
        if "合計" in label:
            summary["m_lots"] = _to_int(row[6])
            summary["s_lots"] = _to_int(row[14]) if len(row) > 14 else None
        elif "融資金" in label:
            summary["m_amt_kd"] = _to_int(row[6])  # 仟元
    if summary.get("m_lots") is None:
        return None

    # 個股：0代號 1名稱 ... 6資餘額 ... 9資限額 ... 14券餘額
    stocks = []
    for row in table.get("data") or []:
        sid = str(row[0]).strip()
        if not sid or not sid[0].isdigit():
            continue
        stocks.append(
            {
                "id": sid,
                "m_lots": _to_int(row[6]) or 0,
                "limit": _to_int(row[9]) or 0,
                "s_lots": _to_int(row[14]) if len(row) > 14 else 0,
            }
        )
    return {"summary": summary, "stocks": stocks}


# ---------------------------------------------------------------------------
# (1) fetch_margin_market
# ---------------------------------------------------------------------------
def fetch_margin_market(date_yyyymmdd: str) -> Optional[dict]:
    """
    大盤融資融券（單日）。

    回 {
        'margin_total':        融資餘額市值（兆元）,
        'margin_util':         融資使用率（%）,
        'short_balance':       融券餘額（萬張）,
        'short_margin_ratio':  券資比（%）,
    }
    任一關鍵欄位抓不到 -> None。

    口徑說明（詳見 docs/margin_maint_proxy_method.md）：
    * margin_total 對齊快照的是「融資餘額**市值**」（兆元），
      = Σ(TWSE+TPEx 融資餘額張 × 當日收盤價 × 1000)/1e12。
      快照 latest.margin_total=1.5762、pctl=98.72（近歷史高），且逐日波動與大盤同向，
      對應的正是融資部位市值，而非穩定的融資餘額金額/張數。
    * margin_util 對齊快照的是「TWSE 個股融資使用率之融資餘額加權平均」
      = Σ(餘額張² / 限額張) / Σ(餘額張) × 100（實測 17.17% ≈ 快照 17.12%）。
    * short_balance = (TWSE+TPEx 融券今日餘額張)/1e4（萬張，快照 short_balance_unit=萬張）。
    * short_margin_ratio = 融券張 / 融資張 × 100。
    """
    tw = _fetch_twse_margin(date_yyyymmdd)
    if tw is None:
        return None
    tp = _fetch_tpex_margin(date_yyyymmdd)  # 可能 None -> 降級為僅 TWSE

    prices = _fetch_close_prices(date_yyyymmdd)
    if not prices:
        return None

    # --- 融資餘額市值（兆元）: 個股 融資張 × 收盤價 × 1000 ---
    mv = 0.0
    covered_lots = 0
    total_lots = 0
    src_lists = [tw["stocks"]]
    if tp is not None:
        src_lists.append(tp["stocks"])
    for stocks in src_lists:
        for s in stocks:
            total_lots += s["m_lots"]
            cp = prices.get(s["id"])
            if cp is None or s["m_lots"] <= 0:
                continue
            mv += s["m_lots"] * cp * 1000.0
            covered_lots += s["m_lots"]
    if total_lots <= 0 or covered_lots <= 0:
        return None
    margin_total = mv / 1e12  # 兆元

    # --- 融資使用率（%）: TWSE 融資餘額加權平均個股使用率 ---
    num = 0.0
    den = 0.0
    for s in tw["stocks"]:
        bal, lim = s["m_lots"], s["limit"]
        if bal <= 0 or lim <= 0:
            continue
        num += bal * (bal / lim)  # bal² / lim
        den += bal
    margin_util = (num / den * 100.0) if den > 0 else None

    # --- 融券餘額（萬張）與券資比（%）: 合計張數 ---
    s_lots = tw["summary"].get("s_lots") or 0
    m_lots = tw["summary"].get("m_lots") or 0
    if tp is not None:
        s_lots += tp["summary"].get("s_lots") or 0
        m_lots += tp["summary"].get("m_lots") or 0
    short_balance = s_lots / 1e4 if s_lots else None
    short_margin_ratio = (s_lots / m_lots * 100.0) if m_lots > 0 else None

    return {
        "margin_total": round(margin_total, 6),
        "margin_util": round(margin_util, 6) if margin_util is not None else None,
        "short_balance": round(short_balance, 4) if short_balance is not None else None,
        "short_margin_ratio": (
            round(short_margin_ratio, 6) if short_margin_ratio is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# (2) fetch_maint_proxy
# ---------------------------------------------------------------------------
def fetch_maint_proxy(date_yyyymmdd: str) -> Optional[dict]:
    """
    大盤融資維持率代理值（單日）。

    回 {
        'maint_wavg':        大盤融資維持率代理（%）,
        'maint_low140_share':融資餘額中維持率<140%的融資金額佔比（%）,
        'maint_low130_share':維持率<130% 佔比（%）,
    }
    抓不到回 None。

    公式（實務公開算法，出處見 docs/margin_maint_proxy_method.md）：
      大盤融資維持率 = Σ(融資餘額張ᵢ × 收盤價ᵢ × 1000) / (上市融資金額 + 上櫃融資金額) × 100%
    分母採 TWSE MI_MARGN + TPEx 之官方「融資金額（仟元）」合計。

    低維持率佔比（維持率分佈）：公開資料無「個股融資金額」，故以成本基準代理
      個股融資金額ᵢ ≈ 融資餘額張ᵢ × 成本均價ᵢ × 1000 × 融資成數
      個股維持率ᵢ  = (收盤價ᵢ × 張ᵢ × 1000) / 融資金額ᵢ = 收盤價ᵢ / (成本均價ᵢ × 融資成數)
    成本均價 = 近 60 交易日收盤均價（代理在外融資部位平均進場價）；
    融資成數 上市 60% / 上櫃 50%。low_share = Σ(維持率<門檻之融資金額) / Σ(融資金額)。
    """
    tw = _fetch_twse_margin(date_yyyymmdd)
    if tw is None:
        return None
    tp = _fetch_tpex_margin(date_yyyymmdd)

    prices = _fetch_close_prices(date_yyyymmdd)
    avg_cost = _fetch_avg_cost(date_yyyymmdd)
    if not prices or not avg_cost:
        return None

    # --- 官方融資金額合計（元）---
    amt_kd = tw["summary"].get("m_amt_kd")
    if amt_kd is None:
        return None
    total_amt = amt_kd * 1000.0
    if tp is not None and tp["summary"].get("m_amt_kd") is not None:
        total_amt += tp["summary"]["m_amt_kd"] * 1000.0

    # --- 融資市值（分子）---
    mv = 0.0
    src = [(tw["stocks"], _MARGIN_RATE_TWSE)]
    if tp is not None:
        src.append((tp["stocks"], _MARGIN_RATE_TPEX))
    for stocks, _rate in src:
        for s in stocks:
            cp = prices.get(s["id"])
            if cp is None or s["m_lots"] <= 0:
                continue
            mv += s["m_lots"] * cp * 1000.0
    if mv <= 0 or total_amt <= 0:
        return None
    maint_wavg = mv / total_amt * 100.0

    # --- 維持率分佈（成本基準代理）---
    fin_tot = 0.0
    fin_l140 = 0.0
    fin_l130 = 0.0
    for stocks, rate in src:
        for s in stocks:
            if s["m_lots"] <= 0:
                continue
            cp = prices.get(s["id"])
            ac = avg_cost.get(s["id"])
            if cp is None or ac is None or ac <= 0:
                continue
            fin_amt = s["m_lots"] * ac * 1000.0 * rate  # 個股融資金額代理
            maint = cp / (ac * rate)                    # 個股維持率（倍）
            fin_tot += fin_amt
            if maint < 1.40:
                fin_l140 += fin_amt
            if maint < 1.30:
                fin_l130 += fin_amt

    low140 = (fin_l140 / fin_tot * 100.0) if fin_tot > 0 else None
    low130 = (fin_l130 / fin_tot * 100.0) if fin_tot > 0 else None

    return {
        "maint_wavg": round(maint_wavg, 6),
        "maint_low140_share": round(low140, 6) if low140 is not None else None,
        "maint_low130_share": round(low130, 6) if low130 is not None else None,
    }


# ---------------------------------------------------------------------------
# (3) fetch_taiex_daily
# ---------------------------------------------------------------------------
def fetch_taiex_daily(date_yyyymmdd: str) -> Optional[dict]:
    """
    加權指數（TAIEX）當日 OHLC。
    回 {'close', 'high', 'low'}；抓不到 / 非交易日回 None。
    來源：TWSE MI_5MINS_HIST（發行量加權股價指數 開/高/低/收，整月資料，篩當日）。
    """
    d = _norm_date(date_yyyymmdd)
    url = (
        "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
        f"?response=json&date={d}"
    )
    j = _get_json(url)
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    # fields: ['日期','開盤指數','最高指數','最低指數','收盤指數']；日期為 ROC '115/07/09'
    target_roc = f"{int(d[:4]) - 1911}/{d[4:6]}/{d[6:8]}"
    for row in j["data"]:
        if str(row[0]).strip() == target_roc:
            close = _to_float(row[4])
            high = _to_float(row[2])
            low = _to_float(row[3])
            if close is None:
                return None
            return {"close": close, "high": high, "low": low}
    return None


# ---------------------------------------------------------------------------
# (4) fetch_daytrade
# ---------------------------------------------------------------------------
def fetch_daytrade(date_yyyymmdd: str) -> Optional[float]:
    """
    當日沖銷成交值佔比（%）。
    回 float；抓不到 / 尚未公布回 None（此項原本就允許 pending）。
    來源：TWSE TWTB4U（當日沖銷交易統計資訊）大盤合計列，
    取「買進成交金額占市場比重%」與「賣出成交金額占市場比重%」之平均
    （實測 0709=(42.33+42.42)/2=42.375，與快照一致）。
    """
    d = _norm_date(date_yyyymmdd)
    url = (
        "https://www.twse.com.tw/exchangeReport/TWTB4U"
        f"?response=json&date={d}"
    )
    j = _get_json(url)
    if not j or j.get("stat") != "OK":
        return None
    tables = j.get("tables") or []
    if not tables:
        return None
    data = tables[0].get("data") or []
    if not data:
        return None
    row = data[0]
    # fields: [股數, 股數占比, 買金額, 買占比(3), 賣金額, 賣占比(5)]
    buy_pct = _to_float(row[3])
    sell_pct = _to_float(row[5])
    if buy_pct is None and sell_pct is None:
        return None
    vals = [v for v in (buy_pct, sell_pct) if v is not None]
    return round(sum(vals) / len(vals), 4)


# ---------------------------------------------------------------------------
# 手動測試
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    test_date = sys.argv[1] if len(sys.argv) > 1 else "20260709"
    print(f"=== 測試日 {test_date} ===")
    print("margin_market:", fetch_margin_market(test_date))
    print("maint_proxy  :", fetch_maint_proxy(test_date))
    print("taiex_daily  :", fetch_taiex_daily(test_date))
    print("daytrade     :", fetch_daytrade(test_date))
