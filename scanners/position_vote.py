"""
持股水位投票系統 — 六指標五維度綜合判定

移除 AE 體制（與多數指標 r>0.55，冗餘同源）
移除 MOVE（與 VIX 同維度 r=0.39）

六指標:
  1. 信用利差紅綠燈   — 信用風險偏好
  2. 市場廣度        — 台股參與度
  3. 10Y-3M 殖利率利差 — 景氣/政策
  4. CP-Treasury Spread — 資金壓力
  5. Broad Dollar Index — 全球美元資金
  6. VIX             — 尾部波動/系統性風險

五大維度: 信用 / 台股內部 / 景氣政策 / 資金流動 / 尾部波動
"""

import logging

logger = logging.getLogger(__name__)

# ── 原始三指標持股對照 ──

REGIME_POSITION = {
    'normal': 100,
    'abnormal': 30,
}

CREDIT_POSITION = {
    'GREEN': 100,
    'YELLOW': 50,
    'RED': 0,
}

BREADTH_POSITION = {
    'STRONG_BULL': 100,
    'BULL': 80,
    'NEUTRAL': 50,
    'BEAR': 30,
    'CRASH': 0,
}

# ── 新五指標持股對照（來自 macro_indicators.py POSITION_MAP）──

MACRO_POSITION = {
    'GREEN': {'T10Y3M': 100, 'CP_SPREAD': 100, 'DOLLAR': 90, 'COR3M': 90, 'MOVE': 90},
    'YELLOW': {'T10Y3M': 60, 'CP_SPREAD': 50, 'DOLLAR': 60, 'COR3M': 50, 'MOVE': 60},
    'RED': {'T10Y3M': 20, 'CP_SPREAD': 0, 'DOLLAR': 30, 'COR3M': 10, 'MOVE': 20},
}

# ── 六指標五維度權重 ──
# 移除 AE 體制（與多數指標 r>0.55，冗餘）
# 移除 MOVE（與 VIX 同維度，r=0.39）

WEIGHTS = {
    # 維度 1: 信用風險 (15%)
    'credit': 0.15,
    # 維度 2: 台股內部結構 (25%)
    'breadth': 0.25,
    # 維度 3: 景氣/政策 (20%)
    'T10Y3M': 0.20,
    # 維度 4: 資金/流動性 (25%) — 兩指標
    'CP_SPREAD': 0.15,
    'DOLLAR': 0.10,
    # 維度 5: 尾部波動 (15%)
    'COR3M': 0.15,
}

# 指標中文名稱
INDICATOR_NAMES = {
    'credit': '信用利差紅綠燈',
    'breadth': '市場廣度',
    'T10Y3M': '殖利率利差 10Y-3M',
    'CP_SPREAD': 'CP-Treasury 資金壓力',
    'DOLLAR': '美元指數',
    'COR3M': 'VIX 系統性風險',
}

INDICATOR_DIMENSION = {
    'credit': '信用風險',
    'breadth': '台股內部',
    'T10Y3M': '景氣政策',
    'CP_SPREAD': '資金流動',
    'DOLLAR': '資金流動',
    'COR3M': '尾部波動',
}


def compute_position_vote(conn):
    """計算八指標投票的建議持股水位。"""
    from models.database import get_credit_spread_history
    from scanners.breadth import compute_breadth
    from scanners.macro_indicators import get_all_latest

    indicators = {}

    # ── 信用利差紅綠燈 ──
    try:
        cs_rows = get_credit_spread_history(conn, limit=1)
        if cs_rows:
            cs = cs_rows[0]
            signal = cs['signal']
            indicators['credit'] = {
                'status': signal,
                'signal': signal,
                'position': CREDIT_POSITION.get(signal, 50),
                'weight': WEIGHTS['credit'],
                'date': cs['date'],
                'detail': f"指標 {cs['indicator_value']:.4f}",
            }
    except Exception as e:
        logger.warning(f"Credit spread vote error: {e}")

    # ── 市場廣度 ──
    try:
        breadth = compute_breadth(conn)
        if breadth:
            regime = breadth['composite_regime']
            if regime in ('STRONG_BULL', 'BULL'):
                signal = 'GREEN'
            elif regime == 'NEUTRAL':
                signal = 'YELLOW'
            else:
                signal = 'RED'
            indicators['breadth'] = {
                'status': regime,
                'signal': signal,
                'position': BREADTH_POSITION.get(regime, 50),
                'weight': WEIGHTS['breadth'],
                'date': breadth['date'],
                'detail': f"ADR {breadth['full']['adr']} · {breadth['vote_bull']}多/{breadth['vote_bear']}空",
            }
    except Exception as e:
        logger.warning(f"Breadth vote error: {e}")

    # ── 宏觀指標 ──
    try:
        macro = get_all_latest(conn)
        for key in ['T10Y3M', 'CP_SPREAD', 'DOLLAR', 'COR3M']:
            if key in macro:
                m = macro[key]
                indicators[key] = {
                    'status': f"{m['value']:.2f}",
                    'signal': m['signal'],
                    'position': m['position'],
                    'weight': WEIGHTS[key],
                    'date': m['date'],
                    'detail': f"值 {m['value']:.2f}",
                }
    except Exception as e:
        logger.warning(f"Macro vote error: {e}")

    # ── 填充缺失指標的預設值 ──
    for key in WEIGHTS:
        if key not in indicators:
            indicators[key] = {
                'status': 'unknown',
                'signal': 'YELLOW',
                'position': 50,
                'weight': WEIGHTS[key],
                'date': None,
                'detail': '無資料',
            }

    # ── 加權計算 ──
    composite = sum(
        indicators[k]['position'] * indicators[k]['weight']
        for k in WEIGHTS
    )
    composite = round(composite)

    # ── 強制上限規則 ──
    caps_applied = []

    if indicators['credit']['signal'] == 'RED':
        if composite > 50:
            caps_applied.append('信用利差 RED → 上限 50%')
        composite = min(composite, 50)

    if indicators.get('breadth', {}).get('status') == 'CRASH':
        if composite > 30:
            caps_applied.append('廣度 CRASH → 上限 30%')
        composite = min(composite, 30)

    if indicators.get('T10Y3M', {}).get('signal') == 'RED':
        if composite > 50:
            caps_applied.append('殖利率倒掛 → 上限 50%')
        composite = min(composite, 50)

    if indicators.get('CP_SPREAD', {}).get('signal') == 'RED':
        if composite > 40:
            caps_applied.append('資金壓力 RED → 上限 40%')
        composite = min(composite, 40)

    composite = max(0, min(100, composite))

    # ── 分級 ──
    if composite >= 80:
        level, action = '積極', '可加碼至滿倉，維持進攻部位'
    elif composite >= 60:
        level, action = '偏多', '維持核心部位，可選擇性加碼'
    elif composite >= 40:
        level, action = '中性', '維持現有部位，觀望為主'
    elif composite >= 20:
        level, action = '防守', '減碼至低水位，僅保留核心持股'
    else:
        level, action = '空手', '建議空手觀望或僅留極少部位'

    # ── 統計 ──
    green_count = sum(1 for v in indicators.values() if v['signal'] == 'GREEN')
    red_count = sum(1 for v in indicators.values() if v['signal'] == 'RED')
    yellow_count = len(indicators) - green_count - red_count

    return {
        'indicators': indicators,
        'composite_position': composite,
        'level': level,
        'action': action,
        'caps_applied': caps_applied,
        'vote_summary': {
            'green': green_count,
            'yellow': yellow_count,
            'red': red_count,
            'total': len(indicators),
        },
        # 相容舊 template 的 key
        'credit': indicators.get('credit', {}),
        'breadth': indicators.get('breadth', {}),
    }
