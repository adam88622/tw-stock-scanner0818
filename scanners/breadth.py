"""
市場廣度指標 — 三層漲跌家數分析 + 投票系統

Layer 1: 權值前 200 大（依成交值排序）
Layer 2: 熱門股族群（量大 + 高周轉率）
Layer 3: 全市場廣度（上市 + 上櫃）

直接讀取 tw-stock-scanner 的 daily_prices DB。
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 台股漲跌停幅度
LIMIT_PCT = 9.5

# 投票門檻
BULL_THRESHOLD = 0.55
BEAR_THRESHOLD = 0.45

# 層權重
W_TOP200 = 0.40
W_HOT = 0.30
W_FULL = 0.30


def _compute_adr(adv, dec):
    if dec == 0:
        return 10.0 if adv > 0 else 1.0
    return round(adv / dec, 3)


def _adr_to_score(adr):
    if adr <= 0:
        return 0.0
    return round(min(adr / (adr + 1.0), 1.0), 4)


def _classify_regime(adr):
    if adr > 2.0:
        return 'STRONG_BULL'
    if adr > 1.0:
        return 'BULL'
    if adr > 0.8:
        return 'NEUTRAL'
    if adr > 0.5:
        return 'BEAR'
    return 'CRASH'


def _analyze_layer(stocks):
    """分析一組股票的廣度。stocks = list of row dicts with change_pct"""
    adv = dec = unch = limit_up = limit_down = 0
    for s in stocks:
        pct = s.get('change_pct') or 0
        if pct > 0.1:
            adv += 1
            if pct >= LIMIT_PCT:
                limit_up += 1
        elif pct < -0.1:
            dec += 1
            if pct <= -LIMIT_PCT:
                limit_down += 1
        else:
            unch += 1
    adr = _compute_adr(adv, dec)
    return {
        'advancers': adv,
        'decliners': dec,
        'unchanged': unch,
        'adr': adr,
        'score': _adr_to_score(adr),
        'regime': _classify_regime(adr),
        'limit_up': limit_up,
        'limit_down': limit_down,
        'total': adv + dec + unch,
    }


def compute_breadth(conn, date_str=None):
    """計算三層廣度指標。

    Args:
        conn: SQLite connection
        date_str: ISO date 'YYYY-MM-DD'，None = 最新日期

    Returns:
        dict with layers + voting result
    """
    # 找日期
    if date_str is None:
        row = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()
        if not row or not row[0]:
            return None
        date_str = row[0]

    # 抓全市場當日資料
    rows = conn.execute("""
        SELECT dp.stock_id, dp.change_pct, dp.volume, dp.trade_value, s.market
        FROM daily_prices dp
        JOIN stocks s ON dp.stock_id = s.stock_id
        WHERE dp.date = ?
          AND dp.close_price IS NOT NULL
          AND dp.volume > 0
    """, (date_str,)).fetchall()

    if not rows:
        logger.warning(f"Breadth: 無資料 {date_str}")
        return None

    all_stocks = [dict(r) for r in rows]

    # === Layer 3: Full Market ===
    full = _analyze_layer(all_stocks)

    # === Layer 1: Top 200 by trade_value ===
    sorted_by_value = sorted(all_stocks, key=lambda s: s.get('trade_value') or 0, reverse=True)
    top200 = _analyze_layer(sorted_by_value[:200])

    # === Layer 2: Hot stocks (成交值前 100 ∪ 成交量前 100) ===
    top_value_ids = {s['stock_id'] for s in sorted_by_value[:100]}
    sorted_by_vol = sorted(all_stocks, key=lambda s: s.get('volume') or 0, reverse=True)
    top_vol_ids = {s['stock_id'] for s in sorted_by_vol[:100]}
    hot_ids = top_value_ids | top_vol_ids
    hot_stocks = [s for s in all_stocks if s['stock_id'] in hot_ids]
    hot = _analyze_layer(hot_stocks)

    # === 投票 ===
    layers = [top200, hot, full]
    vote_bull = sum(1 for l in layers if l['score'] > BULL_THRESHOLD)
    vote_bear = sum(1 for l in layers if l['score'] < BEAR_THRESHOLD)

    composite_score = round(
        top200['score'] * W_TOP200 + hot['score'] * W_HOT + full['score'] * W_FULL, 4
    )

    # Composite regime
    if vote_bull == 3 and composite_score > 0.65:
        composite_regime = 'STRONG_BULL'
    elif vote_bear == 3 and composite_score < 0.35:
        composite_regime = 'CRASH'
    elif vote_bull >= 2 and composite_score > 0.55:
        composite_regime = 'BULL'
    elif vote_bear >= 2 and composite_score < 0.45:
        composite_regime = 'BEAR'
    else:
        composite_regime = 'NEUTRAL'

    return {
        'date': date_str,
        'top200': top200,
        'hot': hot,
        'full': full,
        'vote_bull': vote_bull,
        'vote_bear': vote_bear,
        'vote_neutral': 3 - vote_bull - vote_bear,
        'composite_score': composite_score,
        'composite_regime': composite_regime,
    }


def compute_breadth_history(conn, limit=60):
    """計算最近 N 天的廣度歷史。"""
    dates = conn.execute("""
        SELECT DISTINCT date FROM daily_prices
        ORDER BY date DESC LIMIT ?
    """, (limit,)).fetchall()

    history = []
    for row in reversed(dates):
        d = row[0]
        result = compute_breadth(conn, d)
        if result:
            history.append({
                'date': d,
                'composite_score': result['composite_score'],
                'composite_regime': result['composite_regime'],
                'full_adr': result['full']['adr'],
                'full_adv': result['full']['advancers'],
                'full_dec': result['full']['decliners'],
                'top200_adr': result['top200']['adr'],
                'hot_adr': result['hot']['adr'],
                'vote_bull': result['vote_bull'],
                'vote_bear': result['vote_bear'],
                'limit_up': result['full']['limit_up'],
                'limit_down': result['full']['limit_down'],
            })
    return history
