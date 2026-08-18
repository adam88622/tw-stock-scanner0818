"""
台指選擇權（TXO）支撐 / 壓力 / Max Pain 計算模組（scanner）。

資料源：models.database 的 option_daily 表（由 FN-001 scraper 抓、FN-002 helper 入庫，
        僅存「一般」時段，唯一鍵 (date, contract, strike, cp)）。

計算：
  壓力 resistance = 該契約 Call OI 最大的履約價
  支撐 support    = 該契約 Put  OI 最大的履約價
  Max Pain        = 使「所有 Call/Put 買方履約總價值（賣方需支付）」最小的結算價
                    （即多數買方最痛、賣方最省的價位；見 _max_pain 公式）

tie-break（同 OI / 同痛苦值併列時）：一律取「較低履約價」。
  作法：所有掃描皆依履約價「升冪」進行，只有「嚴格更大 / 嚴格更小」才替換勝出者，
       相等時保留先出現（即較低）的履約價，確保結果可重現。

回傳結構為 /api/option-sr 的唯一資料來源（見架構文件「API JSON 契約」）；
查無資料一律回 {'ok': False, ...} 完整空結構，不拋例外（程式例外才由 route 轉 500）。
"""
import logging

from models.database import (
    get_conn,
    get_latest_option_date,
    get_option_contracts,
    get_option_dates,
    get_option_rows,
)

logger = logging.getLogger(__name__)


def _fmt_strike(v):
    """履約價：整數值輸出為 int（比對樣板的 40000），非整數保留 float。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return v
    return int(f) if f.is_integer() else f


def _fmt_expiry(expiry):
    """'20260701' → '07/01'；格式不符則回原字串。"""
    if expiry and len(str(expiry)) == 8 and str(expiry).isdigit():
        s = str(expiry)
        return f'{s[4:6]}/{s[6:8]}'
    return str(expiry) if expiry else ''


def _contract_label(code, expiry):
    """
    由契約代碼＋到期日組人類可讀標籤。
      '202607W1' → '週選 W1（07/01 到期）'
      '202607F1' → '週選 F1（07/03 到期）'
      '202607'   → '月選（07/15 到期）'   （月選 = YYYYMM 6 碼無後綴）
    """
    code = str(code)
    exp = _fmt_expiry(expiry)
    if len(code) == 6 and code.isdigit():
        base = '月選'
    elif 'W' in code:
        base = '週選 ' + code[code.index('W'):]
    elif 'F' in code:
        base = '週選 ' + code[code.index('F'):]
    else:
        base = code
    return f'{base}（{exp} 到期）' if exp else base


def _pick_default_contract(contracts, date_compact):
    """
    最近到期契約 = 到期日 >= 查詢日 的契約中，expiry 最小者。
    contracts 已依 expiry 升冪；故取第一個 expiry >= date_compact 者。
    若全部皆已到期（罕見），退回 expiry 最大（最晚到期）者。
    """
    for c in contracts:
        if c['expiry'] and str(c['expiry']) >= date_compact:
            return c['contract']
    return contracts[-1]['contract'] if contracts else None


def _max_pain(strikes_oi):
    """
    Max Pain 計算。
    輸入：strikes_oi = {strike: {'call_oi': int, 'put_oi': int}}（缺側已預設 0）。
    公式（對每個候選結算價 K，K 取自實際出現的履約價集合）：
        痛苦 = Σ_i call_oi_i · max(K − strike_i, 0)
             + Σ_j put_oi_j · max(strike_j − K, 0)
      （= 結算價為 K 時，所有價內 Call/Put 買方可拿回的履約總價值，也就是賣方須支付。）
    取使該值「最小」的 K（多數買方最痛、賣方最省）。
    tie-break：升冪掃描，僅「嚴格更小」才替換 → 併列時取較低履約價。
    回傳：(max_pain_strike, pain_value)；無履約價回 (None, None)。
    """
    strikes = sorted(strikes_oi)
    best_strike = None
    best_pain = None
    for k in strikes:
        pain = 0.0
        for s in strikes:
            oi = strikes_oi[s]
            if k > s:
                pain += oi['call_oi'] * (k - s)
            elif k < s:
                pain += oi['put_oi'] * (s - k)
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_strike = k
    return best_strike, best_pain


def _side_out(side):
    """單一 Call/Put 側輸出結構；缺側一律 close/change/change_pct=None、volume/oi=0。"""
    if not side:
        return {'close': None, 'change': None, 'change_pct': None,
                'volume': 0, 'oi': 0}
    return {
        'close': side.get('close'),
        'change': side.get('change'),
        'change_pct': side.get('change_pct'),
        'volume': side.get('volume') or 0,
        'oi': side.get('oi') or 0,
    }


def _empty(error, date=None, contract=None,
           available_dates=None, available_contracts=None):
    """查無資料時的完整空結構（HTTP 200；ok=False）。鍵齊全，前端可正常渲染下拉。"""
    return {
        'ok': False,
        'error': error,
        'date': date,
        'contract': contract,
        'contract_label': None,
        'available_dates': available_dates or [],
        'available_contracts': available_contracts or [],
        'resistance': {},
        'support': {},
        'max_pain': {},
        'stats': {'total_call_oi': 0, 'total_put_oi': 0, 'pc_ratio': 0.0},
        'rows': [],
    }


def compute_option_sr(date=None, contract=None):
    """
    主計算函式：組出 /api/option-sr 所需完整資料結構。

    參數：
      date     (str|None)：ISO 'YYYY-MM-DD'；None → option_daily 最新有資料日。
      contract (str|None)：契約代碼；None → 該日「最近到期」契約。
    回傳：dict（見模組說明 / 架構文件 API JSON 契約）。查無資料回 ok=False 完整空結構。
    """
    conn = get_conn()
    try:
        available_dates = get_option_dates(conn)

        if date is None:
            date = get_latest_option_date(conn)
        if not date:
            return _empty('尚無選擇權資料', date=None, contract=None,
                          available_dates=available_dates)

        contracts_raw = get_option_contracts(conn, date)  # [{contract, expiry}] expiry 升冪
        available_contracts = [
            {'code': c['contract'], 'expiry': c['expiry'],
             'label': _contract_label(c['contract'], c['expiry'])}
            for c in contracts_raw
        ]
        if not contracts_raw:
            return _empty(f'該日無資料（{date}）', date=date, contract=None,
                          available_dates=available_dates,
                          available_contracts=available_contracts)

        date_compact = date.replace('-', '')
        if contract is None:
            contract = _pick_default_contract(contracts_raw, date_compact)

        exp_map = {c['contract']: c['expiry'] for c in contracts_raw}
        if contract not in exp_map:
            return _empty(f'該日無此契約（{contract}）', date=date, contract=None,
                          available_dates=available_dates,
                          available_contracts=available_contracts)

        db_rows = get_option_rows(conn, date, contract)
    finally:
        conn.close()

    if not db_rows:
        return _empty(f'該日 / 契約無資料（{date} {contract}）',
                      date=date, contract=None,
                      available_dates=available_dates,
                      available_contracts=available_contracts)

    # 依 strike 聚合 Call / Put 兩側；同 strike 可能只出現單側
    strike_map = {}  # strike(float) -> {'call': side|None, 'put': side|None}
    for r in db_rows:
        strike = r['strike']
        side = {
            'close': r['close'],
            'change': r['change'],
            'change_pct': r['change_pct'],
            'volume': r['volume'],
            'oi': r['oi'],
        }
        entry = strike_map.setdefault(strike, {'call': None, 'put': None})
        if r['cp'] == 'C':
            entry['call'] = side
        else:
            entry['put'] = side

    sorted_strikes = sorted(strike_map)  # 升冪

    # 缺側 OI 一律 0，避免 KeyError；同時累計支撐/壓力/總量
    oi_map = {}
    res_strike = res_oi = None
    sup_strike = sup_oi = None
    total_call_oi = 0
    total_put_oi = 0
    for strike in sorted_strikes:  # 升冪 → 同 OI 併列取較低履約價
        e = strike_map[strike]
        call_oi = (e['call']['oi'] or 0) if e['call'] else 0
        put_oi = (e['put']['oi'] or 0) if e['put'] else 0
        oi_map[strike] = {'call_oi': call_oi, 'put_oi': put_oi}
        total_call_oi += call_oi
        total_put_oi += put_oi
        if res_oi is None or call_oi > res_oi:   # 嚴格大於才替換
            res_oi = call_oi
            res_strike = strike
        if sup_oi is None or put_oi > sup_oi:
            sup_oi = put_oi
            sup_strike = strike

    mp_strike, mp_pain = _max_pain(oi_map)

    pc_ratio = round(total_put_oi / total_call_oi, 4) if total_call_oi else 0.0

    rows = []
    for strike in sorted_strikes:
        e = strike_map[strike]
        rows.append({
            'strike': _fmt_strike(strike),
            'call': _side_out(e['call']),
            'put': _side_out(e['put']),
            'is_resistance': strike == res_strike,
            'is_support': strike == sup_strike,
            'is_max_pain': strike == mp_strike,
        })

    return {
        'ok': True,
        'error': None,
        'date': date,
        'contract': contract,
        'contract_label': _contract_label(contract, exp_map.get(contract)),
        'available_dates': available_dates,
        'available_contracts': available_contracts,
        'resistance': {'strike': _fmt_strike(res_strike), 'call_oi': res_oi},
        'support': {'strike': _fmt_strike(sup_strike), 'put_oi': sup_oi},
        'max_pain': {'strike': _fmt_strike(mp_strike), 'pain': mp_pain},
        'stats': {
            'total_call_oi': total_call_oi,
            'total_put_oi': total_put_oi,
            'pc_ratio': pc_ratio,
        },
        'rows': rows,
    }
