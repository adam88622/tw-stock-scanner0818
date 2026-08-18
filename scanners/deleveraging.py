"""
去槓桿壓力儀表板 — 即時資料管線 (Phase B)

核心函式 build_indicators() -> dict，產出與 data/deleveraging_snapshot.json
相同結構的 IND dict，供 app.py 的 /deleveraging route 注入模板。

架構：向前追加 (forward-append)，不回補
--------------------------------------------------
快照已含每條 series 的完整 1518 日歷史（2010→2026-07-14）。本管線：
  1. 載入快照為歷史基底。
  2. 接活 (live=True)：對每條 series，從「最後有效日之後」用 scanners.deleveraging_sources
     的 fetcher 抓單日代理值向前延伸（填補信用序列 07-10~ 的洞、並追加更新交易日）。
  3. 對系統性口徑落差的序列 (margin_total / maint_wavg / maint_low140_share) 施乘法接點校正。
  4. 重算 rolling 百分位 / unwind / momentum / foreign_sell / composite。
  5. 快取結果 (data/deleveraging_live.json)，同 key 重複呼叫直接回傳。

鐵則 (correctness gate)
--------------------------------------------------
live=False（或即時來源全回 None）時，build_indicators() 逐位元重現快照：
composite.score==78.14、每個 composite.parts、每條 series、unwind、latest、pctl
全部一致（浮點容差 1e-6）。既有（快照）的最新日一律「信任、不重算」，
只有真正取得的新資料才用本模組的函式現算 —— forward-append 的正確語意。

接點校正 (calibration)
--------------------------------------------------
即時代理值（公開資料）與快照專有值 (QUANTDATA) 有系統性口徑落差，直接接上會讓
rolling 百分位在接點跳動。對 margin_total / maint_wavg / maint_low140_share 各算一個
乘法校正常數 = 快照該序列最後有效值 / fetcher 在「同一天」回傳值；之後每個新日的
fetcher 值乘上此常數再接上。校正常數在首次接活算一次並存入快取 meta，之後沿用。
（實測校正 anchor=2026-07-09：margin_total≈1.118、maint_wavg≈0.956、maint_low140_share≈1.466；
 taiex/daytrade/margin_util/short_* 實測已吻合，factor=1。）

反解結果（見檔尾 DECODING NOTES）
--------------------------------------------------
* rolling 百分位：window=1250「陣列列」、point-in-time、含當日、weak：
  pctl = #{窗內值 <= 當日值}/n × 100。
* unwind：baseline=margin_total@config.baseline_date、peak=episode 內 margin_total 最高、
  current=最新 margin_total；subscore = excess_now/excess_peak =(cur-base)/(peak-base)。
* foreign_sell_pressure subscore = clip(0.5 + foreign_sell_z/4, 0, 1)；
  foreign_sell_z = -(cur-mean)/std of foreign_net_20d over ~1250 窗。
* margin_momentum subscore：快照最新=0.25；既有最新日沿用，新日用 provisional 映射。
"""

import os
import json
import math
import sqlite3

# --- 路徑 ---
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_THIS_DIR)
SNAPSHOT_PATH = os.path.join(PROJECT_ROOT, 'data', 'deleveraging_snapshot.json')
CACHE_PATH = os.path.join(PROJECT_ROOT, 'data', 'deleveraging_live.json')
DB_PATH = os.path.join(PROJECT_ROOT, 'db', 'scanner.db')

# --- 常數 ---
PCTL_WINDOW = 1250          # rolling 百分位視窗（陣列列數）
FOREIGN_Z_WINDOW = 1250     # 外資賣壓 z 視窗

# 需接點乘法校正的序列 -> (來源, fetcher 回傳鍵)
_CALIB_SPEC = {
    'margin_total': ('margin', 'margin_total'),
    'maint_wavg': ('maint', 'maint_wavg'),
    'maint_low140_share': ('maint', 'maint_low140_share'),
}

# fetcher 可供給的原始（未校正）series -> (來源, 鍵)
#   'margin'=fetch_margin_market, 'maint'=fetch_maint_proxy,
#   'taiex'=fetch_taiex_daily,    'daytrade'=fetch_daytrade
_SOURCE_SERIES = {
    'margin_total': ('margin', 'margin_total'),
    'margin_util': ('margin', 'margin_util'),
    'short_balance': ('margin', 'short_balance'),
    'short_margin_ratio': ('margin', 'short_margin_ratio'),
    'maint_wavg': ('maint', 'maint_wavg'),
    'maint_low140_share': ('maint', 'maint_low140_share'),
    'maint_low130_share': ('maint', 'maint_low130_share'),
    'taiex_close': ('taiex', 'close'),
    'daytrade_ratio': ('daytrade', None),
}

# raw series -> pctl series（接活後重算百分位用）
_PCTL_OF = {
    'margin_total': 'pctl_margin_total',
    'turn_heat': 'pctl_turn_heat',
    'margin_util': 'pctl_margin_util',
    'maint_low140_share': 'pctl_maint_low140_share',
    'rv20': 'pctl_rv20',
    'turn_val': 'pctl_turn_val',
}


# =====================================================================
# 即時來源 fetcher 介面（scanners/deleveraging_sources.py，另一 agent 產出）
# ---------------------------------------------------------------------
#   fetch_taiex_daily(date)   -> {'close','high','low'} | None
#   fetch_margin_market(date) -> {'margin_total','margin_util',
#                                 'short_balance','short_margin_ratio'} | None
#   fetch_maint_proxy(date)   -> {'maint_wavg','maint_low140_share',
#                                 'maint_low130_share'} | None
#   fetch_daytrade(date)      -> float(%) | None
# import 失敗 / fetcher 例外 / 回 None 時，該 series 該日追加 None（不可用 0），並設 partial。
# =====================================================================
try:
    from scanners import deleveraging_sources as _sources
except Exception:
    _sources = None


def _safe(fname, date):
    if _sources is None:
        return None
    fn = getattr(_sources, fname, None)
    if fn is None:
        return None
    try:
        return fn(date)
    except Exception:
        return None


def fetch_taiex_daily(date):
    r = _safe('fetch_taiex_daily', date)
    return r if isinstance(r, dict) else None


def fetch_margin_market(date):
    r = _safe('fetch_margin_market', date)
    return r if isinstance(r, dict) else None


def fetch_maint_proxy(date):
    r = _safe('fetch_maint_proxy', date)
    return r if isinstance(r, dict) else None


def fetch_daytrade(date):
    r = _safe('fetch_daytrade', date)
    try:
        return float(r) if r is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_bundle(date):
    """一次抓齊某日四來源：{'margin','maint','taiex','daytrade'}。"""
    return {
        'margin': fetch_margin_market(date),
        'maint': fetch_maint_proxy(date),
        'taiex': fetch_taiex_daily(date),
        'daytrade': fetch_daytrade(date),
    }


def _bundle_value(bundle, src, key):
    b = bundle.get(src)
    if src == 'daytrade':
        return b  # float 或 None
    if not isinstance(b, dict):
        return None
    return b.get(key)


# =====================================================================
# 百分位函式（反解自快照，逐日比對驗證）
# =====================================================================
def rolling_percentile(series, idx, window=PCTL_WINDOW):
    """Point-in-time rolling 百分位（weak，含當日）：
    取 [idx-window+1, idx] 內非 None 值為母體，回 #{值 <= series[idx]}/n × 100。"""
    cur = series[idx]
    if cur is None:
        return None
    lo = max(0, idx - window + 1)
    vals = [series[j] for j in range(lo, idx + 1) if series[j] is not None]
    if not vals:
        return None
    le = sum(1 for v in vals if v <= cur)
    return le / len(vals) * 100.0


def _last_valid_index(series):
    for i in range(len(series) - 1, -1, -1):
        if series[i] is not None:
            return i
    return None


def _last_valid(series):
    i = _last_valid_index(series)
    return None if i is None else series[i]


# =====================================================================
# unwind — 完全由 margin_total series 重算
# =====================================================================
def compute_unwind(ind):
    """回傳 (unwind_dict, subscore)。baseline=margin_total@baseline_date、
    peak=baseline_date 起 margin_total 最高、current=最新有效 margin_total。"""
    dates = ind['dates']
    mt = ind['series']['margin_total']
    base_date = ind['config']['baseline_date'].replace('-', '')
    try:
        bidx = dates.index(base_date)
    except ValueError:
        u = ind['unwind']
        ep = u['excess_peak']
        return u, (u['excess_now'] / ep if ep else 0.0)
    baseline = mt[bidx]

    peak = None
    peak_date = None
    for j in range(bidx, len(mt)):
        if mt[j] is None:
            continue
        if peak is None or mt[j] > peak:
            peak, peak_date = mt[j], dates[j]

    cidx = _last_valid_index(mt)
    current, current_date = mt[cidx], dates[cidx]

    excess_peak = peak - baseline
    excess_now = current - baseline
    subscore = (excess_now / excess_peak) if excess_peak else 0.0

    def _d(s):
        return '%s-%s-%s' % (s[0:4], s[4:6], s[6:8]) if s and len(s) == 8 else s

    unwind = {
        'peak': peak, 'peak_date': _d(peak_date),
        'baseline': baseline, 'baseline_date': ind['config']['baseline_date'],
        'current': current, 'current_date': _d(current_date),
        'U': 1.0 - subscore, 'excess_peak': excess_peak, 'excess_now': excess_now,
    }
    return unwind, subscore


# =====================================================================
# momentum / foreign_sell
# =====================================================================
def margin_d5_pct(ind):
    mt = ind['series']['margin_total']
    valid = [v for v in mt if v is not None]
    if len(valid) < 6:
        return None
    last, five = valid[-1], valid[-6]
    return (last - five) / five * 100.0 if five else None


def momentum_subscore_from_d5(d5):
    """provisional 融資五日動能 subscore（新交易日用；既有最新日沿用快照 0.25）。
    單調線性 clip：clip(0.5 + d5/8, 0, 1)。"""
    if d5 is None:
        return 0.5
    return min(1.0, max(0.0, 0.5 + d5 / 8.0))


def foreign_sell_z(ind, window=FOREIGN_Z_WINDOW):
    fn = ind['series']['foreign_net_20d']
    idx = _last_valid_index(fn)
    if idx is None:
        return None
    lo = max(0, idx - window + 1)
    vals = [fn[j] for j in range(lo, idx + 1) if fn[j] is not None]
    if len(vals) < 2:
        return None
    cur = fn[idx]
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    if sd == 0:
        return None
    return -(cur - mean) / sd


def foreign_sell_subscore(z):
    """subscore = clip(0.5 + z/4, 0, 1)。反解：z=1.6712 -> 0.9178。"""
    if z is None:
        return 0.5
    return min(1.0, max(0.0, 0.5 + z / 4.0))


# =====================================================================
# composite 重算
# =====================================================================
_PCTL_SERIES = {
    'margin_total_pctl': 'pctl_margin_total',
    'turn_heat_pctl': 'pctl_turn_heat',
    'margin_util_pctl': 'pctl_margin_util',
    'maint_inverse_pctl': 'pctl_maint_inverse',
    'low140_pctl': 'pctl_maint_low140_share',
    'rv20_pctl': 'pctl_rv20',
    'turn_val_pctl': 'pctl_turn_val',
}


def recompute_composite(ind, momentum_sub, foreign_sub, unwind_sub):
    """以最新有效值重算 composite（就地更新 ind['composite']，保留 zone/zone_label）。"""
    weights = ind['config']['weights']
    subs = {}
    for wk in weights:
        if wk in _PCTL_SERIES:
            p = _last_valid(ind['series'][_PCTL_SERIES[wk]])
            subs[wk] = (p / 100.0) if p is not None else 0.0
        elif wk == 'unwind_remaining':
            subs[wk] = unwind_sub
        elif wk == 'margin_momentum':
            subs[wk] = momentum_sub
        elif wk == 'foreign_sell_pressure':
            subs[wk] = foreign_sub
        else:
            subs[wk] = 0.0

    score = sum(weights[wk] * subs[wk] for wk in weights)

    part_keys = list(ind['composite']['parts'].keys())
    weight_keys = list(weights.keys())
    new_parts = {pk: weights[wk] * subs[wk] for pk, wk in zip(part_keys, weight_keys)}

    ind['composite']['score'] = round(score, 2)
    ind['composite']['parts'] = new_parts
    return ind


# =====================================================================
# DB helpers（唯讀）
# =====================================================================
def _db_connect():
    uri = 'file:%s?mode=ro&immutable=1' % DB_PATH.replace('\\', '/')
    return sqlite3.connect(uri, uri=True)


def _db_market_dates_after(asof_yyyymmdd):
    """DB institutional 中 > asof 的交易日（'YYYYMMDD' 升冪）。"""
    if not os.path.exists(DB_PATH):
        return []
    asof_dash = '%s-%s-%s' % (asof_yyyymmdd[0:4], asof_yyyymmdd[4:6], asof_yyyymmdd[6:8])
    try:
        conn = _db_connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT date FROM institutional WHERE date > ? ORDER BY date",
                (asof_dash,)).fetchall()
        finally:
            conn.close()
        return [r[0].replace('-', '') for r in rows]
    except Exception:
        return []


def _db_turn_val_daily(date_yyyymmdd):
    """DB daily_prices 全市場 sum(trade_value)；<=0（未填值→不可靠）回 None。"""
    if not os.path.exists(DB_PATH):
        return None
    d = '%s-%s-%s' % (date_yyyymmdd[0:4], date_yyyymmdd[4:6], date_yyyymmdd[6:8])
    try:
        conn = _db_connect()
        try:
            r = conn.execute(
                "SELECT SUM(trade_value) FROM daily_prices WHERE date = ?", (d,)).fetchone()
        finally:
            conn.close()
        v = r[0] if r else None
        return v if (v is not None and v > 0) else None
    except Exception:
        return None


# =====================================================================
# 接點校正
# =====================================================================
def compute_calibration(ind):
    """
    對 _CALIB_SPEC 各序列算乘法校正常數 = 快照該序列最後有效值 / fetcher 同一天回傳值。
    fetcher 抓不到基準日 -> factor=1.0（退化為直接接）。
    回 {'factors': {series: factor}, 'anchors': {series: {'date','snap','fetched'}}}。
    """
    dates = ind['dates']
    factors, anchors = {}, {}
    bundle_cache = {}
    for series, (src, key) in _CALIB_SPEC.items():
        ser = ind['series'].get(series)
        li = _last_valid_index(ser) if ser else None
        if li is None:
            factors[series] = 1.0
            continue
        anchor_date = dates[li]
        snap_val = ser[li]
        if anchor_date not in bundle_cache:
            bundle_cache[anchor_date] = _fetch_bundle(anchor_date)
        fetched = _bundle_value(bundle_cache[anchor_date], src, key)
        anchors[series] = {'date': anchor_date, 'snap': snap_val, 'fetched': fetched}
        if fetched is None or fetched == 0 or snap_val is None:
            factors[series] = 1.0
        else:
            factors[series] = snap_val / fetched
    return {'factors': factors, 'anchors': anchors}


def _calibrated(series, raw_val, factors):
    if raw_val is None:
        return None
    return raw_val * factors.get(series, 1.0)


# =====================================================================
# 接活：向前延伸各 series（填補洞 + 追加新日）
# =====================================================================
def _recompute_pctls_at(ind, idx):
    """重算單一 idx 的各 pctl_*（有 raw 才算）。maint_inverse 為 -maint_wavg 之百分位。"""
    series = ind['series']
    for raw, pk in _PCTL_OF.items():
        if pk in series and raw in series:
            val = rolling_percentile(series[raw], idx) if series[raw][idx] is not None else None
            series[pk][idx] = round(val, 4) if val is not None else None
    if 'pctl_maint_inverse' in series and 'maint_wavg' in series:
        mw = series['maint_wavg']
        if mw[idx] is not None:
            inv = [(-v if v is not None else None) for v in mw]
            val = rolling_percentile(inv, idx)
            series['pctl_maint_inverse'][idx] = round(val, 4) if val is not None else None
        else:
            series['pctl_maint_inverse'][idx] = None


def _derive_taiex_metrics(ind, idx):
    """新日 rv20（20 日對數報酬年化波動 %），best-effort。"""
    series = ind['series']
    close = series.get('taiex_close')
    if not close or idx >= len(close) or close[idx] is None:
        return
    if 'rv20' in series:
        vals = [close[j] for j in range(max(0, idx - 20), idx + 1) if close[j] is not None]
        if len(vals) >= 21:
            rets = [math.log(vals[k] / vals[k - 1]) for k in range(1, len(vals))]
            m = sum(rets) / len(rets)
            sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
            series['rv20'][idx] = sd * math.sqrt(252) * 100.0


def _extend_live(ind, factors):
    """
    接活延伸。回傳 actions=[{'date','append'(bool),'filled':[...],'none':[...]}]。
      A. 填補既有日期中「洞」：來源型 series 從其最後有效日之後、且該日期已在 ind['dates']
         中卻為 None（信用序列 07-10~ 洞），用 fetcher 值（校正後）填入。
      B. 追加新交易日：DB institutional 有、> 最新日期、且 fetch_margin_market 齊備者。
    turn_val 用 DB（不可靠→None）；turn_heat/foreign_net 無公開單日源→None（partial）。
    """
    series = ind['series']
    dates = ind['dates']
    actions = []
    touched = set()

    src_last = {s: _last_valid_index(series[s]) for s in _SOURCE_SERIES if s in series}
    hole_start = min((v for v in src_last.values() if v is not None), default=len(dates))
    bundle_cache = {}

    # --- A. 填補既有洞 ---
    for idx in range(hole_start + 1, len(dates)):
        date = dates[idx]
        need = [s for s in _SOURCE_SERIES
                if s in series and series[s][idx] is None
                and src_last.get(s) is not None and idx > src_last[s]]
        if not need:
            continue
        if date not in bundle_cache:
            bundle_cache[date] = _fetch_bundle(date)
        bundle = bundle_cache[date]
        filled, none_list = [], []
        for sname in need:
            src, key = _SOURCE_SERIES[sname]
            raw = _bundle_value(bundle, src, key)
            if raw is None:
                none_list.append(sname)
                continue
            series[sname][idx] = _calibrated(sname, raw, factors) if sname in factors else raw
            filled.append(sname)
        if filled:
            touched.add(idx)
        if none_list:
            ind['partial'] = True
        if filled or none_list:
            actions.append({'date': date, 'append': False, 'filled': filled, 'none': none_list})

    # --- B. 追加新交易日 ---
    for d in _db_market_dates_after(dates[-1]):
        bundle = _fetch_bundle(d)
        if not isinstance(bundle.get('margin'), dict):
            continue
        for k in series:
            series[k].append(None)
        dates.append(d)
        idx = len(dates) - 1
        filled, none_list = [], []
        for sname in _SOURCE_SERIES:
            if sname not in series:
                continue
            src, key = _SOURCE_SERIES[sname]
            raw = _bundle_value(bundle, src, key)
            if raw is None:
                none_list.append(sname)
                continue
            series[sname][idx] = _calibrated(sname, raw, factors) if sname in factors else raw
            filled.append(sname)
        if 'turn_val' in series:
            tv = _db_turn_val_daily(d)
            if tv is not None:
                series['turn_val'][idx] = tv
                filled.append('turn_val')
            else:
                none_list.append('turn_val')
        _derive_taiex_metrics(ind, idx)
        touched.add(idx)
        if none_list:
            ind['partial'] = True
        actions.append({'date': d, 'append': True, 'filled': filled, 'none': none_list})

    for idx in sorted(touched):
        _recompute_pctls_at(ind, idx)

    for k in list(ind['latest'].keys()):
        if k in series:
            lv = _last_valid(series[k])
            if lv is not None:
                ind['latest'][k] = lv

    return actions


# =====================================================================
# 快取
# =====================================================================
def _load_snapshot():
    with open(SNAPSHOT_PATH, encoding='utf-8') as f:
        return json.load(f)


def _read_cache_file():
    """讀整個快取檔（{_key, ind, meta}），不比對 key；不存在/損毀回 None。"""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(key, ind, meta):
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'_key': key, 'ind': ind, 'meta': meta}, f, ensure_ascii=False)
    except Exception:
        pass


def _reproduce_snapshot(ind, mom_sub, fs_sub):
    """純快照重現：既有最新日沿用快照 subscore，unwind 用快照存值算 subscore。"""
    eu = ind['unwind']
    unwind_sub = (eu['excess_now'] / eu['excess_peak']) if eu['excess_peak'] else 0.0
    recompute_composite(ind, mom_sub, fs_sub, unwind_sub)
    return ind


def _snap_subscores(ind):
    """既有最新日（快照）的 momentum / foreign_sell subscore。"""
    weights = ind['config']['weights']
    part_keys = list(ind['composite']['parts'].keys())
    pos = {wk: pk for pk, wk in zip(part_keys, list(weights.keys()))}
    mom = ind['composite']['parts'][pos['margin_momentum']] / weights['margin_momentum']
    fs = foreign_sell_subscore(ind.get('latest_extra', {}).get('foreign_sell_z'))
    return mom, fs


# =====================================================================
# 主入口（route 用）— 純讀取，絕不碰網路
# =====================================================================
def build_indicators(refresh=False):
    """
    route 預設路徑：**不做任何網路抓取**（避免把 25s 的 TWSE/TPEx timeout 疊加放在 request path）。

    * refresh=False（預設，route 呼叫 build_indicators()）：
        - data/deleveraging_live.json 存在 → 直接回快取的 ind（毫秒級）。
        - 快取不存在 → 回純快照重現（也不抓網路；鐵則 composite.score==78.14）。
    * refresh=True：委派 refresh_live(force=True)（會碰網路，供 CLI/排程 debug 用；
        route 不應走這條）。

    即時抓取 + 接點校正 + 洞填補請用 out-of-band 的 refresh_live()（每日 job / CLI）。
    """
    if refresh:
        return refresh_live(force=True)

    cached = _read_cache_file()
    if cached is not None and isinstance(cached.get('ind'), dict):
        return cached['ind']

    ind = _load_snapshot()
    mom_sub, fs_sub = _snap_subscores(ind)
    return _reproduce_snapshot(ind, mom_sub, fs_sub)


# =====================================================================
# 接活（out-of-band）— 唯一碰網路之處，供每日 job / CLI 呼叫
# =====================================================================
def refresh_live(force=False):
    """
    即時抓取 + 接點校正 + 洞填補，結果寫入 data/deleveraging_live.json（{_key,ind,meta}）。
    **唯一碰網路之處。** 回傳寫入（或沿用）的 ind。

    穩健性（不劣化既有好資料）：
      * 同日短路：非 force 且既有快取 _key == 本次 key（同一批交易日）→ 直接回既有，不重抓。
      * 校正 factor 持久化：本次 fetch 某序列 anchor 失敗（fetched=None → factor 退化 1.0）時，
        沿用既有快取 meta 已存的 factor，不退回 1.0。
      * 抓不到新東西（全 None、無 filled）：**保留上一版快取**（有既有快取就回既有、不覆寫）；
        無既有快取才寫入純快照重現作為 baseline。
    """
    ind = _load_snapshot()
    mom_sub, fs_sub = _snap_subscores(ind)
    existing = _read_cache_file()

    if _sources is None:
        # 無來源模組：不覆寫既有；有既有回既有，否則純快照
        if existing is not None and isinstance(existing.get('ind'), dict):
            return existing['ind']
        return _reproduce_snapshot(ind, mom_sub, fs_sub)

    snap_asof = ind.get('asof_market', ind.get('asof', ''))
    probe_new = _db_market_dates_after(snap_asof)
    key = 'live|%s|%s|%d' % (snap_asof, probe_new[-1] if probe_new else '', len(ind['dates']))

    # 同日短路
    if (not force and existing is not None
            and existing.get('_key') == key and isinstance(existing.get('ind'), dict)):
        return existing['ind']

    # 已持久化的 factor（供本次某序列 anchor 失敗時沿用）
    prev_factors = {}
    try:
        prev_factors = dict(existing['meta']['calibration']['factors']) if existing else {}
    except Exception:
        prev_factors = {}

    calib = compute_calibration(ind)   # 網路
    factors = dict(calib['factors'])
    for s in _CALIB_SPEC:
        fetched = calib.get('anchors', {}).get(s, {}).get('fetched')
        if fetched is None and s in prev_factors:
            factors[s] = prev_factors[s]  # 沿用已存好 factor，不退回 1.0
    calib['factors'] = factors

    actions = _extend_live(ind, factors)

    if not any(a['filled'] for a in actions):
        # 這次沒抓到新東西：保留上一版快取（不用全 None 覆寫）
        if existing is not None and isinstance(existing.get('ind'), dict):
            return existing['ind']
        _reproduce_snapshot(ind, mom_sub, fs_sub)
        _write_cache(key, ind, {'calibration': calib, 'actions': actions})
        return ind

    # 有接到新資料：最新日改由現算
    new_last = ind['dates'][-1]
    for _k in ('asof_market', 'asof', 'asof_credit', 'asof_maint', 'date_last'):
        if _k in ind:
            ind[_k] = new_last

    unwind, unwind_sub = compute_unwind(ind)
    ind['unwind'] = unwind
    momentum_sub = momentum_subscore_from_d5(margin_d5_pct(ind))
    foreign_sub = foreign_sell_subscore(foreign_sell_z(ind))
    recompute_composite(ind, momentum_sub, foreign_sub, unwind_sub)

    _write_cache(key, ind, {'calibration': calib, 'actions': actions})
    return ind


# =====================================================================
# DECODING NOTES / 接點與來源
# ---------------------------------------------------------------------
# 1) 百分位：window=1250「陣列列」、point-in-time、含當日、weak（<= 比例×100）。
#    margin_total/turn_val 於近期 daily-dense 尾段逐日重算與快照 pctl_* 完全吻合
#    （差異僅快照 2 位四捨五入 <0.005）。credit-derived series 之歷史 pctl 係在更密的
#    日頻底層上算，稀疏 1518 點無法重建，故既有最新日一律沿用快照存值。
# 2) unwind：baseline=margin_total@2026-04-30=1.277192、peak=1.700671(2026-06-22)、
#    current=1.576217；excess_now/excess_peak=0.70611 → part 15.5345。✓
# 3) foreign_sell：subscore=clip(0.5+z/4)；z=1.6712 → 0.9178 → part 9.178。✓
# 4) margin_momentum：快照最新=0.25；既有最新日沿用，新日用 provisional 線性 clip。
# 5) 接點校正（compute_calibration）：margin_total / maint_wavg / maint_low140_share 的
#    即時公開代理與快照專有值有系統性口徑落差，各算乘法常數 = 快照最後有效值 /
#    fetcher 同一天值（實測 anchor=2026-07-09：margin_total≈1.576/1.409=1.118、
#    maint_wavg≈162.0/169.4=0.956、maint_low140≈7.22/4.93=1.466）；存入快取 meta。
#    taiex_close/daytrade/margin_util/short_* 實測已吻合，factor=1（不在 _CALIB_SPEC）。
# 6) 接活來源：taiex/margin/maint/daytrade 由 scanners/deleveraging_sources.py 提供
#    （公開資料單日代理）。turn_val 用 DB daily_prices（trade_value 多日為 0 → 不可靠→
#    None）；turn_heat（消化天數）與 foreign_net（DB 為張數，與快照金額口徑不相容）
#    無公開單日源 → 新日追加 None + partial（不可用 0 混充）。
# 7) 架構（比照 run_daily.py：每日 job 灌資料、網頁只讀快取）：
#    build_indicators()（route）純讀快取 / 快照，絕不碰網路（<1s）。
#    refresh_live()（out-of-band，唯一碰網路）由排程 / CLI 呼叫，寫入 live 快取。
#    CLI：`python -m scanners.deleveraging [--force]`。
# =====================================================================


if __name__ == '__main__':
    import sys as _sys
    _force = '--force' in _sys.argv[1:]
    _ind = refresh_live(force=_force)
    print('refresh_live 完成: score=%s zone=%s partial=%s asof_market=%s n_dates=%d' % (
        _ind['composite']['score'], _ind['composite'].get('zone'),
        _ind.get('partial'), _ind.get('asof_market'), len(_ind['dates'])))
