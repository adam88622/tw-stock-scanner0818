"""
八指標相關性 / 自回歸 / Granger 因果分析

檢驗所有指標兩兩之間的共線性，確保投票系統權重合理。
八指標: AE體制, 信用利差, 市場廣度, 10Y-3M, CP-Spread, Dollar, VIX(COR3M), MOVE
"""

import logging
from statistics import mean, stdev
from itertools import combinations

logger = logging.getLogger(__name__)

INDICATOR_NAMES = {
    'regime': 'AE 體制',
    'credit': '信用利差',
    'breadth': '市場廣度',
    'T10Y3M': '殖利率利差',
    'CP_SPREAD': 'CP 資金壓力',
    'DOLLAR': '美元指數',
    'COR3M': 'VIX 系統風險',
    'MOVE': 'MOVE 國債波動',
}

# 配對中文名稱（用於前端顯示）
PAIR_NAMES = {}
for a in INDICATOR_NAMES:
    for b in INDICATOR_NAMES:
        if a != b:
            PAIR_NAMES[f'{a}_vs_{b}'] = f'{INDICATOR_NAMES[a]} vs {INDICATOR_NAMES[b]}'
            PAIR_NAMES[f'{a}_leads_{b}'] = f'{INDICATOR_NAMES[a]} → {INDICATOR_NAMES[b]}'
            PAIR_NAMES[f'{a}_causes_{b}'] = f'{INDICATOR_NAMES[a]} → {INDICATOR_NAMES[b]}'

INDICATOR_DIMENSION = {
    'regime': '股票異常',
    'credit': '信用風險',
    'breadth': '台股內部',
    'T10Y3M': '景氣政策',
    'CP_SPREAD': '資金流動',
    'DOLLAR': '資金流動',
    'COR3M': '尾部波動',
    'MOVE': '尾部波動',
}


def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 5:
        return None
    x, y = x[:n], y[:n]
    mx, my = mean(x), mean(y)
    sx, sy = stdev(x), stdev(y)
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
    return round(cov / (sx * sy), 4)


def _lag_corr(x, y, max_lag=5):
    results = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x_s, y_s = x[:len(x) - lag] if lag > 0 else x, y[lag:]
        else:
            x_s, y_s = x[-lag:], y[:len(y) + lag]
        n = min(len(x_s), len(y_s))
        if n < 5:
            continue
        r = _pearson(x_s[:n], y_s[:n])
        if r is not None:
            results[lag] = r
    return results


def _simple_granger(x, y, max_lag=3):
    results = {}
    for lag in range(1, max_lag + 1):
        if len(y) < lag + 10:
            continue
        base_resid, full_resid = [], []
        for t in range(lag, min(len(y), len(x))):
            y_pred_base = mean(y[t - lag:t])
            base_resid.append((y[t] - y_pred_base) ** 2)
            x_signal = mean(x[max(0, t - lag):t])
            y_pred_full = y_pred_base * 0.7 + x_signal * 0.3
            full_resid.append((y[t] - y_pred_full) ** 2)
        base_mse = mean(base_resid) if base_resid else 1
        full_mse = mean(full_resid) if full_resid else 1
        improvement = round((base_mse - full_mse) / base_mse * 100, 2) if base_mse > 0 else 0
        results[lag] = improvement
    return results


def _interpret_corr(r):
    if r is None:
        return '資料不足'
    ar = abs(r)
    direction = '正相關' if r > 0 else '負相關'
    if ar >= 0.8:
        return f'極強{direction} — 高度共線風險'
    if ar >= 0.6:
        return f'強{direction} — 需注意權重'
    if ar >= 0.4:
        return f'中度{direction} — 互補性尚可'
    if ar >= 0.2:
        return f'弱{direction} — 獨立性佳'
    return f'幾乎無關 — 完全獨立'


def _collect_all_series(conn):
    """收集八個指標的時間序列，回傳 {indicator: {date: value}}。"""
    from scanners.breadth import compute_breadth

    series = {}

    # AE 體制: recon_error / tau (越高越異常, 0~2)
    rows = conn.execute("SELECT date, recon_error, tau FROM regime_history ORDER BY date").fetchall()
    series['regime'] = {r['date']: min(r['recon_error'] / r['tau'], 2.0) if r['tau'] > 0 else 1.0
                        for r in rows}

    # 信用利差: indicator_value (0~1, 越高越危險)
    rows = conn.execute("SELECT date, indicator_value FROM credit_spread_history ORDER BY date").fetchall()
    series['credit'] = {r['date']: r['indicator_value'] for r in rows}

    # 市場廣度: composite_score (0~1, 越高越多頭)
    dates = conn.execute("""
        SELECT DISTINCT date FROM daily_prices WHERE date >= '2025-04-01' ORDER BY date
    """).fetchall()
    breadth_map = {}
    for row in dates:
        try:
            b = compute_breadth(conn, row['date'])
            if b:
                breadth_map[row['date']] = b['composite_score']
        except Exception:
            continue
    series['breadth'] = breadth_map

    # 五大宏觀指標
    for ind in ['T10Y3M', 'CP_SPREAD', 'DOLLAR', 'COR3M', 'MOVE']:
        rows = conn.execute(
            "SELECT date, value FROM macro_indicators WHERE indicator = ? ORDER BY date",
            (ind,)
        ).fetchall()
        series[ind] = {r['date']: r['value'] for r in rows}

    return series


def run_correlation_analysis(conn):
    """跑完整八指標相關性分析。"""

    all_series = _collect_all_series(conn)
    indicators = list(all_series.keys())

    # ── 所有兩兩配對的 Correlation Matrix ──
    correlation_matrix = {}
    for a, b in combinations(indicators, 2):
        pair_key = f'{a}_vs_{b}'
        cn = PAIR_NAMES.get(pair_key, pair_key)
        dates = sorted(set(all_series[a].keys()) & set(all_series[b].keys()))
        if len(dates) < 5:
            correlation_matrix[pair_key] = {
                'r': None, 'n': len(dates),
                'cn': cn,
                'interpretation': '資料不足',
                'same_dimension': INDICATOR_DIMENSION.get(a) == INDICATOR_DIMENSION.get(b),
            }
            continue
        x = [all_series[a][d] for d in dates]
        y = [all_series[b][d] for d in dates]
        r = _pearson(x, y)
        correlation_matrix[pair_key] = {
            'r': r,
            'n': len(dates),
            'cn': cn,
            'interpretation': _interpret_corr(r),
            'same_dimension': INDICATOR_DIMENSION.get(a) == INDICATOR_DIMENSION.get(b),
        }

    # ── 找出高相關配對 ──
    high_corr = []
    moderate_corr = []
    low_corr = []
    for pair, data in correlation_matrix.items():
        r = data['r']
        if r is None:
            continue
        ar = abs(r)
        entry = {
            'pair': pair,
            'cn': data.get('cn', pair),
            'r': r,
            'n': data['n'],
            'same_dim': data['same_dimension'],
            'interpretation': data['interpretation'],
        }
        if ar >= 0.6:
            high_corr.append(entry)
        elif ar >= 0.3:
            moderate_corr.append(entry)
        else:
            low_corr.append(entry)

    # ── Lag Analysis: 重點配對 ──
    lag_analysis = {}
    focus_pairs = [
        ('credit', 'breadth'),
        ('regime', 'credit'),
        ('COR3M', 'MOVE'),
        ('T10Y3M', 'credit'),
        ('DOLLAR', 'breadth'),
    ]
    for a, b in focus_pairs:
        dates = sorted(set(all_series.get(a, {}).keys()) & set(all_series.get(b, {}).keys()))
        if len(dates) >= 10:
            x = [all_series[a][d] for d in dates]
            y = [all_series[b][d] for d in dates]
            key = f'{a}_leads_{b}'
            lag_analysis[key] = {
                'cn': PAIR_NAMES.get(key, key),
                'data': _lag_corr(x, y),
            }

    # ── Granger: 重點配對 ──
    granger = {}
    for a, b in focus_pairs:
        dates = sorted(set(all_series.get(a, {}).keys()) & set(all_series.get(b, {}).keys()))
        if len(dates) >= 15:
            x = [all_series[a][d] for d in dates]
            y = [all_series[b][d] for d in dates]
            key = f'{a}_causes_{b}'
            granger[key] = {
                'cn': PAIR_NAMES.get(key, key),
                'data': _simple_granger(x, y),
            }

    # ── 建議 ──
    recommendation = _make_recommendation(high_corr, moderate_corr, low_corr)

    # ── 數據摘要 ──
    data_summary = {ind: len(all_series[ind]) for ind in indicators}

    return {
        'correlation_matrix': correlation_matrix,
        'high_corr': high_corr,
        'moderate_corr': moderate_corr,
        'low_corr': low_corr,
        'lag_analysis': lag_analysis,
        'granger_causality': granger,
        'recommendation': recommendation,
        'data_summary': data_summary,
    }


def _make_recommendation(high_corr, moderate_corr, low_corr):
    issues = []
    warnings = []
    ok_items = []

    for entry in high_corr:
        flag = '(同維度)' if entry['same_dim'] else '(跨維度)'
        issues.append(f"{entry['pair']}: r={entry['r']} {flag} — {entry['interpretation']}")

    for entry in moderate_corr:
        if entry['same_dim']:
            ok_items.append(f"{entry['pair']}: r={entry['r']} (同維度預期內)")
        else:
            warnings.append(f"{entry['pair']}: r={entry['r']} — {entry['interpretation']}")

    for entry in low_corr:
        ok_items.append(f"{entry['pair']}: r={entry['r']} — 獨立性佳")

    # 綜合判定
    if not issues:
        if not warnings:
            verdict = '八指標獨立性充分，各維度互補良好。目前權重配置合理。'
        else:
            verdict = f'大部分配對獨立性佳。{len(warnings)} 對中度相關需持續監控，但未達共線紅線。'
    else:
        pairs = ', '.join(e['pair'] for e in high_corr)
        verdict = f'發現 {len(issues)} 對高相關配對（{pairs}），建議降低其中一方權重或合併。'

    return {
        'verdict': verdict,
        'issues': issues,
        'warnings': warnings,
        'ok': ok_items,
        'stats': {
            'total_pairs': len(high_corr) + len(moderate_corr) + len(low_corr),
            'high': len(high_corr),
            'moderate': len(moderate_corr),
            'low': len(low_corr),
        },
    }
