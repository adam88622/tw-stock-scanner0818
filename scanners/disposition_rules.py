"""
注意/處置股規則引擎

根據台灣證交所「公布注意交易資訊作業要點」整理出最常觸發的量化條件。
本模組讀取 daily_prices 計算每檔股票對每條規則的「觸發距離」,
分數 0-100,越高表示越接近觸發。

主要量化條件 (依公開要點整理):
  R1: 最近 6 營業日累積漲跌幅 ≥ ±32%   (一)
  R2: 最近 1 營業日漲跌幅 ≥ ±10% (接近單日漲跌停)
  R3: 最近 6 營業日累積週轉率 ≥ 50%
  R4: 連續 3 日漲跌幅同向且累積幅度 ≥ 18%

處置升級規則 (大致):
  D1: 連續 3 日入注意 → 第一次處置 (5 分鐘撮合)
  D2: 30 日內 第二次處置 → 升級為第二次 (10 分鐘撮合 + 預收款)

風險分數 (0-100):
  - 已在處置期間: 100
  - 規則觸發接近度: 0-90 (其中 R1 權重 40, R2 權重 20, R3 權重 20, R4 權重 20)

輸出:
  list[dict] {stock_id, name, score, in_disposition, hits: [..], close_price, change_pct}
"""
import logging
from models.database import get_conn

logger = logging.getLogger(__name__)


def _to_pct(numerator, denom):
    if denom is None or denom == 0:
        return 0.0
    return (numerator / denom) * 100.0


def _cumulative_change_pct(closes):
    """closes: [oldest, ..., newest], 回傳 (newest/oldest - 1) * 100"""
    if not closes or len(closes) < 2 or closes[0] in (0, None):
        return 0.0
    return (closes[-1] / closes[0] - 1) * 100.0


def evaluate_stock(stock_id, name, daily_rows):
    """
    daily_rows: list[Row] 從 daily_prices 取最近 N 日,日期升冪
    每 row 含 close_price, volume, change_pct
    """
    n = len(daily_rows)
    if n < 6:
        return None

    closes = [r["close_price"] for r in daily_rows]
    volumes = [r["volume"] or 0 for r in daily_rows]
    changes = [r["change_pct"] or 0 for r in daily_rows]

    # 取最近 6 日切片
    last6 = daily_rows[-6:]
    last6_closes = [r["close_price"] for r in last6]
    last6_changes = [r["change_pct"] or 0 for r in last6]

    # R1: 6 日累積漲跌幅
    cum_pct_6d = _cumulative_change_pct(last6_closes)
    abs_cum_6d = abs(cum_pct_6d)
    r1_score = min(abs_cum_6d / 32.0 * 100.0, 100.0)

    # R2: 最近一日漲跌幅 (接近單日漲跌停)
    last_chg = changes[-1] if changes else 0
    abs_last = abs(last_chg)
    r2_score = min(abs_last / 9.5 * 100.0, 100.0)  # 9.5% 算逼近

    # R3: 6 日累積週轉率(以股本估算需要 outstanding_shares,先以「6 日均量 / 60 日均量」當代理)
    # 真正週轉率 = volume / outstanding_shares
    # 此處用「最近 6 日均量是 60 日均量的幾倍」作為「成交異常」的代理
    if n >= 60:
        avg6 = sum(volumes[-6:]) / 6
        avg60 = sum(volumes[-60:]) / 60
        vol_ratio = avg6 / avg60 if avg60 > 0 else 0
        r3_score = min((vol_ratio - 1.0) / 4.0 * 100.0, 100.0)  # 5x 量算 100 分
        r3_score = max(r3_score, 0)
    else:
        vol_ratio = None
        r3_score = 0

    # R4: 連續 3 日同向漲跌
    last3 = last6_changes[-3:]
    same_direction = all(c > 0 for c in last3) or all(c < 0 for c in last3)
    cum_3d = sum(last3) if same_direction else 0
    r4_score = min(abs(cum_3d) / 18.0 * 100.0, 100.0)

    # 加權總分
    score = (
        r1_score * 0.40 +
        r2_score * 0.20 +
        r3_score * 0.20 +
        r4_score * 0.20
    )

    hits = []
    if r1_score >= 80:
        hits.append(f"R1: 6日累積{cum_pct_6d:+.1f}%")
    if r2_score >= 80:
        hits.append(f"R2: 單日{last_chg:+.1f}%")
    if r3_score >= 60 and vol_ratio:
        hits.append(f"R3: 量能{vol_ratio:.1f}x")
    if r4_score >= 60:
        hits.append(f"R4: 連3日{cum_3d:+.1f}%")

    return {
        "stock_id": stock_id,
        "name": name,
        "score": round(score, 1),
        "r1": round(r1_score, 1),
        "r2": round(r2_score, 1),
        "r3": round(r3_score, 1),
        "r4": round(r4_score, 1),
        "cum_pct_6d": round(cum_pct_6d, 2),
        "last_change": round(last_chg, 2),
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "close_price": closes[-1],
        "hits": hits,
    }


def get_active_dispositions(as_of_date):
    """目前正在處置中的股票(period_start <= as_of <= period_end)"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT stock_id, name, period_start, period_end, action, condition
            FROM disposition_announcements
            WHERE is_real_stock = 1
              AND period_start <= ?
              AND period_end >= ?
            ORDER BY period_start DESC
        """, (as_of_date, as_of_date)).fetchall()
    return [dict(r) for r in rows]


def get_oversold_in_disposition(as_of_date, min_drop_pct=30.0,
                                  peak_lookback_days=30,
                                  state='active', recent_days=14):
    """
    「處置打完」候選清單:處置中或剛出關、從近期高點跌幅 >= min_drop_pct 的股票。

    state:
      'active' — 目前還在處置期間
      'recent' — 處置期間結束於最近 recent_days 天內(剛出關)
      'all'    — 上述兩者聯集

    高點定義:處置公告日前 peak_lookback_days 天 ~ 至今的 max(close_price)
    (處置前通常飆漲到頂,出關後回落)

    回傳: list[dict] sorted by drop_pct ASC (跌最多在前)
    """
    if state == 'active':
        where = "period_start <= :as_of AND period_end >= :as_of"
    elif state == 'recent':
        where = "period_end < :as_of AND period_end >= date(:as_of, :recent_off)"
    else:  # all
        where = ("(period_start <= :as_of AND period_end >= :as_of) "
                 "OR (period_end < :as_of AND period_end >= date(:as_of, :recent_off))")
    params = {'as_of': as_of_date, 'recent_off': f'-{recent_days} days'}

    with get_conn() as conn:
        actives = conn.execute(f"""
            SELECT stock_id, name, period_start, period_end, action, condition
            FROM disposition_announcements
            WHERE is_real_stock = 1 AND ({where})
        """, params).fetchall()

        # 同一檔可能有多筆(連續處置),只取最近一筆 period
        latest_by_stock = {}
        for r in actives:
            sid = r['stock_id']
            if sid not in latest_by_stock or r['period_start'] > latest_by_stock[sid]['period_start']:
                latest_by_stock[sid] = dict(r)

        results = []
        for sid, r in latest_by_stock.items():
            # 取 [period_start - peak_lookback_days, as_of_date] 區間的最高 close
            peak_row = conn.execute("""
                SELECT date, close_price
                FROM daily_prices
                WHERE stock_id = ?
                  AND date >= date(?, ? || ' days')
                  AND date <= ?
                ORDER BY close_price DESC
                LIMIT 1
            """, (sid, r['period_start'], f'-{peak_lookback_days}', as_of_date)).fetchone()

            # 取最近一個交易日的收盤
            cur_row = conn.execute("""
                SELECT date, close_price
                FROM daily_prices
                WHERE stock_id = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
            """, (sid, as_of_date)).fetchone()

            if not peak_row or not cur_row or not peak_row['close_price']:
                continue

            peak = peak_row['close_price']
            cur = cur_row['close_price']
            if peak <= 0:
                continue
            drop_pct = (cur / peak - 1) * 100.0  # 負數表示跌幅

            if drop_pct > -min_drop_pct:
                continue  # 跌幅不夠

            # 計算「處置已過 N 日」與「再 N 日出關」
            days_in = _date_diff_days(r['period_start'], as_of_date)
            days_to_release = _date_diff_days(as_of_date, r['period_end'])

            # 判斷狀態
            if r['period_start'] <= as_of_date <= r['period_end']:
                phase = 'active'
            elif r['period_end'] < as_of_date:
                phase = 'recent'  # 已出關
            else:
                phase = 'pending'

            results.append({
                'stock_id': sid,
                'name': r['name'],
                'peak_date': peak_row['date'],
                'peak_price': peak,
                'current_date': cur_row['date'],
                'current_price': cur,
                'drop_pct': round(drop_pct, 2),
                'period_start': r['period_start'],
                'period_end': r['period_end'],
                'action': r['action'],
                'condition': r['condition'],
                'days_in_disposition': days_in,
                'days_to_release': days_to_release,
                'phase': phase,
            })

    results.sort(key=lambda x: x['drop_pct'])  # 跌最多的在最前面
    return results


def find_disposition_drop_signals(
    as_of_date,
    drop_threshold=30.0,
    freshness_days=7,
    lookback_days=90,
    rise_min_pct=20.0,
    peak_window_pre=30,
    peak_window_post=5,
):
    """
    「漲多處置 → 處置期間跌幅破 N%」訊號選股。

    流程(每筆 disposition 紀錄):
      1. peak = max(close) in [period_start - peak_window_pre, period_start + peak_window_post]
         確認 peak_date 落在 period_start ± peak_window_post 內(代表處置就是因這波漲幅觸發)
      2. 確認漲幅 ≥ rise_min_pct:peak vs (period_start - peak_window_pre) 的收盤漲幅
      3. 從 period_start 往後走到 min(period_end, today),
         找第一天 close ≤ peak × (1 - drop_threshold/100) 的 trigger_date
      4. 如果 trigger_date ≤ period_end → 訊號成立
      5. days_since = today - trigger_date,若 ≤ freshness_days → 標記為新鮮(fresh=True)

    參數:
      drop_threshold: 跌幅閾值,30 表示跌 30%
      freshness_days: 訊號新鮮度,N 天內觸發算新鮮
      lookback_days: 只看最近 N 天有 period_start 的處置(預設 90)
      rise_min_pct: 漲幅門檻,過濾非「漲多型」處置
      peak_window_pre/post: 高點搜尋窗口

    回傳: list[dict] sorted by (fresh DESC, trigger_date DESC)
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT stock_id, name, announce_date, period_start, period_end, action, condition
            FROM disposition_announcements
            WHERE is_real_stock = 1
              AND period_start >= date(?, ? || ' days')
              AND period_start <= ?
        """, (as_of_date, f'-{lookback_days}', as_of_date)).fetchall()

        signals = []
        seen_keys = set()  # avoid (stock_id, period_start) dupes

        for r in rows:
            key = (r['stock_id'], r['period_start'])
            if key in seen_keys:
                continue
            seen_keys.add(key)

            sid = r['stock_id']
            ps = r['period_start']
            pe = r['period_end']

            # 1. peak in [ps - pre, ps + post]
            peak_row = conn.execute("""
                SELECT date, close_price FROM daily_prices
                WHERE stock_id = ?
                  AND date >= date(?, ? || ' days')
                  AND date <= date(?, ? || ' days')
                ORDER BY close_price DESC LIMIT 1
            """, (sid, ps, f'-{peak_window_pre}', ps, f'+{peak_window_post}')).fetchone()
            if not peak_row or not peak_row['close_price']:
                continue
            peak = peak_row['close_price']
            peak_date = peak_row['date']

            # 2. 漲幅檢查:peak vs (ps - peak_window_pre) close
            base_row = conn.execute("""
                SELECT close_price FROM daily_prices
                WHERE stock_id = ? AND date <= date(?, ? || ' days')
                ORDER BY date DESC LIMIT 1
            """, (sid, ps, f'-{peak_window_pre}')).fetchone()
            if not base_row or not base_row['close_price'] or base_row['close_price'] <= 0:
                continue
            rise_pct = (peak / base_row['close_price'] - 1) * 100.0
            if rise_pct < rise_min_pct:
                continue  # 不算漲多型

            # 3. 從 ps 往後找 trigger_date
            trigger_threshold = peak * (1 - drop_threshold / 100.0)
            walk_end = min(pe, as_of_date)
            trigger_row = conn.execute("""
                SELECT date, close_price FROM daily_prices
                WHERE stock_id = ?
                  AND date >= ? AND date <= ?
                  AND close_price <= ?
                ORDER BY date ASC LIMIT 1
            """, (sid, ps, walk_end, trigger_threshold)).fetchone()
            if not trigger_row:
                continue  # 還沒跌破門檻

            trigger_date = trigger_row['date']
            trigger_close = trigger_row['close_price']
            trigger_drop = (trigger_close / peak - 1) * 100.0

            # 取目前最新 close 看訊號後的走勢
            cur_row = conn.execute("""
                SELECT date, close_price FROM daily_prices
                WHERE stock_id = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
            """, (sid, as_of_date)).fetchone()
            cur = cur_row['close_price'] if cur_row else None
            cur_drop = (cur / peak - 1) * 100.0 if cur else None

            days_since = _date_diff_days(trigger_date, as_of_date)
            fresh = (days_since is not None and days_since <= freshness_days)

            in_disposition = ps <= as_of_date <= pe

            signals.append({
                'stock_id': sid,
                'name': r['name'],
                'announce_date': r['announce_date'],
                'period_start': ps,
                'period_end': pe,
                'action': r['action'],
                'condition': r['condition'],
                'peak_date': peak_date,
                'peak_price': peak,
                'rise_pct': round(rise_pct, 1),
                'trigger_date': trigger_date,
                'trigger_close': trigger_close,
                'trigger_drop_pct': round(trigger_drop, 2),
                'current_date': cur_row['date'] if cur_row else None,
                'current_price': cur,
                'current_drop_pct': round(cur_drop, 2) if cur_drop is not None else None,
                'days_since_trigger': days_since,
                'fresh': fresh,
                'in_disposition': in_disposition,
            })

    # fresh first, then by trigger_date desc
    signals.sort(key=lambda x: (-int(x['fresh']), x['trigger_date'] or ''), reverse=True)
    # 上面 sort 順序錯,改成兩段
    signals.sort(key=lambda x: x['trigger_date'] or '', reverse=True)
    signals.sort(key=lambda x: not x['fresh'])  # fresh=True 在前
    return signals


def _date_diff_days(d1, d2):
    """'YYYY-MM-DD' diff in days, d2 - d1"""
    from datetime import date
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return (b - a).days
    except (ValueError, TypeError):
        return None


def scan_risk_universe(as_of_date=None, market=None, top_n=100):
    """
    全市場掃描:對每檔股票算分,回傳 score 由高到低 top_n 名。
    """
    with get_conn() as conn:
        # 找日期 (latest if None)
        if as_of_date is None:
            row = conn.execute("SELECT MAX(date) AS d FROM daily_prices").fetchone()
            as_of_date = row["d"] if row else None
        if not as_of_date:
            return {"as_of_date": None, "results": [], "active_dispositions": []}

        # 取所有有交易的股票
        sql = """
            SELECT DISTINCT s.stock_id, s.name, s.market
            FROM stocks s
            JOIN daily_prices dp ON dp.stock_id = s.stock_id
            WHERE dp.date >= date(?, '-30 days')
        """
        params = [as_of_date]
        if market in ("twse", "tpex"):
            sql += " AND s.market = ?"
            params.append(market)
        stocks = conn.execute(sql, params).fetchall()

        # 取目前處置中
        active = conn.execute("""
            SELECT stock_id FROM disposition_announcements
            WHERE is_real_stock = 1
              AND period_start <= ? AND period_end >= ?
        """, (as_of_date, as_of_date)).fetchall()
        active_set = {r["stock_id"] for r in active}

        results = []
        for s in stocks:
            sid = s["stock_id"]
            # 取最近 60 日資料
            rows = conn.execute("""
                SELECT date, close_price, volume, change_pct
                FROM daily_prices
                WHERE stock_id = ? AND date <= ?
                ORDER BY date DESC LIMIT 60
            """, (sid, as_of_date)).fetchall()
            rows = list(reversed(rows))  # 升冪
            if len(rows) < 6:
                continue
            r = evaluate_stock(sid, s["name"], rows)
            if not r:
                continue
            r["market"] = s["market"]
            r["in_disposition"] = sid in active_set
            results.append(r)

    # 處置中的擺最前 (score = 100)
    for r in results:
        if r["in_disposition"]:
            r["score"] = 100.0

    results.sort(key=lambda x: -x["score"])
    return {
        "as_of_date": as_of_date,
        "results": results[:top_n],
        "active_count": sum(1 for r in results if r["in_disposition"]),
    }
