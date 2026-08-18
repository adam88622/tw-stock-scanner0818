"""
電子期 vs 金融期 相對強弱指標（te-tf-strength）強弱計算模組（群組 B）

資料源：
  群益報價服務（CAPITAL_QUOTE_URL/api/index-futures）：TE（電子期）/ TF（金融期）
  類股指數期即時報價。比照 futures_basis 的 ok:false 結構化錯誤慣例，缺價不靜默以 0。

計算：
  base       = ref（>0 時），否則 fallback open
  chg_pct    = (close − base) / base × 100（TE、TF 各算）
  strength   = TE 漲跌% − TF 漲跌%（strength_raw；smooth_n>0 時再做移動平均）
  color      = strength>0 → red（電子強於金融）／ <0 → green ／ ==0 → flat

落地：
  盤中即時點寫入 te_tf_strength_intraday（date+ts 去重，失敗只 log 不中斷 API）；
  收盤摘要寫入 te_tf_strength_history（high/low/收盤 strength 與 te/tf 漲跌%）。

DB helper（upsert_te_tf_intraday / get_te_tf_intraday_series / upsert_te_tf_history）
由群組 C 新增於 models/database.py，本模組依架構介面契約呼叫。
"""
import logging
from datetime import date, datetime

import requests

from config import CAPITAL_QUOTE_URL
from models.database import (
    get_conn,
    upsert_te_tf_intraday,
    get_te_tf_intraday_series,
    upsert_te_tf_history,
)

logger = logging.getLogger(__name__)


def _today():
    """今日日期（沿用 futures_basis.py 慣例：date.today().isoformat()）。"""
    return date.today().isoformat()


def _parse_float(val):
    """寬鬆轉 float，無效值回 None（比照 futures_basis._parse_float）。"""
    if val is None or val in ('-', '', '--'):
        return None
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return None


def fetch_index_futures():
    """
    B1：HTTP 讀取 TE/TF 指數期報價。

    GET CAPITAL_QUOTE_URL/api/index-futures（timeout=8，比照 _fetch_capital_futures）。
    解析 index_futures 取 TE/TF；任一檔 close 無或 ≤0 → ok:false + 明確中文 error
    （不可靜默以 0 計算）；連線失敗 → ok:false 結構化錯誤。

    回傳：
      {
        "ok": bool,
        "error": str | None,
        "TE": {"close","ref","open","ts","volume"} | None,
        "TF": {...} | None,
        "quote_status": {"connected","last_sweep","te_priced","tf_priced"},
      }
    """
    url = CAPITAL_QUOTE_URL.rstrip('/') + '/api/index-futures'
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"群益指數期報價服務連線失敗: {e}")
        return {
            'ok': False,
            'error': f'無法連線群益報價服務（{CAPITAL_QUOTE_URL}）：{e}',
            'TE': None,
            'TF': None,
            'quote_status': {
                'connected': False, 'last_sweep': None,
                'te_priced': False, 'tf_priced': False,
            },
        }

    index_futures = data.get('index_futures', {}) or {}
    status = data.get('status', {}) or {}

    def _extract(prod):
        raw = index_futures.get(prod) or {}
        close = _parse_float(raw.get('close'))
        if close is None or close <= 0:
            return None
        return {
            'close': close,
            'bid': _parse_float(raw.get('bid')),
            'ask': _parse_float(raw.get('ask')),
            'ref': _parse_float(raw.get('ref')),
            'open': _parse_float(raw.get('open')),
            'ts': raw.get('ts'),
            'volume': raw.get('volume'),
        }

    te = _extract('TE')
    tf = _extract('TF')

    quote_status = {
        'connected': bool(status.get('connected')),
        'last_sweep': status.get('last_sweep'),
        'te_priced': te is not None,
        'tf_priced': tf is not None,
    }

    if te is None and tf is None:
        error = '無法取得電子期(TE)與金融期(TF)有效報價'
    elif te is None:
        error = '無法取得電子期(TE)有效報價'
    elif tf is None:
        error = '無法取得金融期(TF)有效報價'
    else:
        error = None

    return {
        'ok': error is None,
        'error': error,
        'TE': te,
        'TF': tf,
        'quote_status': quote_status,
    }


def compute_strength(te, tf, smooth_n=0, series_hist=None):
    """
    B2：純函式，計算 TE/TF 相對強弱。

    price      = 中價 (bid+ask)/2（買賣價皆>0 時），否則 fallback 成交價 close。
                 電子期/金融期超低量，成交價常凍住，用中價才能反映盤中即時變化。
    base       = ref（>0 時），否則 fallback open；兩檔 base_source 不一致記 "mixed"
    chg_pct    = (price − base) / base × 100
    strength_raw = te_chg_pct − tf_chg_pct
    smooth_n>0 : 取 series_hist 末 (smooth_n−1) 點 strength 與本次 raw 平均
    color      : strength>0 → red / <0 → green / ==0 → flat

    回傳形狀見架構 B2。
    """
    def _chg(q):
        bid = q.get('bid')
        ask = q.get('ask')
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            price = (bid + ask) / 2.0
        else:
            price = q['close']
        ref = q.get('ref')
        open_ = q.get('open')
        if ref is not None and ref > 0:
            base = ref
            src = 'ref'
        else:
            base = open_
            src = 'open'
        if base is None or base == 0:
            return 0.0, src, price
        return (price - base) / base * 100.0, src, price

    te_chg_pct, te_src, te_price = _chg(te)
    tf_chg_pct, tf_src, tf_price = _chg(tf)

    base_source = te_src if te_src == tf_src else 'mixed'

    strength_raw = te_chg_pct - tf_chg_pct

    strength = strength_raw
    if smooth_n and smooth_n > 0:
        hist = series_hist or []
        # 取末 (smooth_n-1) 點的 strength 與本次 raw 平均
        tail = hist[-(smooth_n - 1):] if smooth_n > 1 else []
        vals = []
        for h in tail:
            try:
                s = h['strength']
            except (KeyError, TypeError, IndexError):
                s = None
            if s is not None:
                vals.append(float(s))
        vals.append(strength_raw)
        strength = sum(vals) / len(vals)

    if strength > 0:
        color = 'red'
    elif strength < 0:
        color = 'green'
    else:
        color = 'flat'

    ts = te.get('ts') or tf.get('ts') or datetime.now().strftime('%H:%M:%S')

    return {
        'strength': round(strength, 4),
        'strength_raw': round(strength_raw, 4),
        'te_chg_pct': round(te_chg_pct, 4),
        'tf_chg_pct': round(tf_chg_pct, 4),
        'color': color,
        'base_source': base_source,
        'te_close': round(te_price, 2),
        'tf_close': round(tf_price, 2),
        'ts': ts,
    }


def persist_intraday(now, conn=None):
    """
    B3：把一筆 now（B2 輸出）以「今日日期 + now['ts']」為鍵寫入 intraday。

    conn 未注入 → 自開 get_conn() 並自行 commit()+close()（WAL 模式不 commit 不落盤）。
    conn 注入   → 不負責 commit/close（誰開誰關，避免關掉測試共用連線）。
    寫 DB 失敗只 logger.warning 回 False，絕不拋出（F5：不中斷即時 API）。
    """
    own_conn = conn is None
    c = conn if conn is not None else None
    try:
        if own_conn:
            c = get_conn()
        upsert_te_tf_intraday(
            c,
            _today(),
            now['ts'],
            now['strength'],
            now['te_chg_pct'],
            now['tf_chg_pct'],
            now['te_close'],
            now['tf_close'],
            now['base_source'],
        )
        if own_conn:
            c.commit()
        return True
    except Exception as e:
        logger.warning(f"te-tf-strength 盤中寫入失敗（略過，不中斷）: {e}")
        return False
    finally:
        if own_conn and c is not None:
            try:
                c.close()
            except Exception:
                pass


def build_response(smooth=0):
    """
    B4：編排層，組 /api/te-tf-strength 完整回應（介面契約見架構 §4.2）。

    1. fetch_index_futures()；not ok → 回 ok:false（now:None，series 仍回當日已存供畫圖）。
    2. 讀當日已存 series（get_te_tf_intraday_series）。
    3. compute_strength → now。
    4. persist_intraday（失敗不影響回傳）。
    5. now 追加進 series，回 {ok:True, now, series, quote_status}。
    """
    r = fetch_index_futures()

    # 讀當日已存 series（讀失敗不阻斷，回空 list）
    def _load_series():
        conn = None
        try:
            conn = get_conn()
            rows = get_te_tf_intraday_series(conn, _today())
            series = []
            for row in rows:
                series.append({
                    'ts': row['ts'],
                    'strength': row['strength'],
                    'te_chg_pct': row['te_chg_pct'],
                    'tf_chg_pct': row['tf_chg_pct'],
                })
            return series
        except Exception as e:
            logger.warning(f"te-tf-strength 讀取當日 series 失敗（回空）: {e}")
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    series = _load_series()

    if not r['ok']:
        return {
            'ok': False,
            'error': r['error'],
            'now': None,
            'series': series,
            'quote_status': r['quote_status'],
        }

    now = compute_strength(r['TE'], r['TF'], smooth_n=smooth, series_hist=series)

    # 盤中落點（失敗不影響回傳）
    persist_intraday(now)

    # 把 now 追加進 series（即時點）
    series.append({
        'ts': now['ts'],
        'strength': now['strength'],
        'te_chg_pct': now['te_chg_pct'],
        'tf_chg_pct': now['tf_chg_pct'],
    })

    return {
        'ok': True,
        'error': None,
        'now': now,
        'series': series,
        'quote_status': r['quote_status'],
    }


def summarize_today(date=None, conn=None):
    """
    B5：從 intraday 彙總當日 high/low/收盤(最後ts) strength 與 te/tf 漲跌%，
    呼叫 upsert_te_tf_history 寫摘要。本版不排程，提供函式即可。

    回傳寫入摘要 dict，或 None（當日無 intraday）。
    conn 未注入 → 自開並自行 commit/close；注入則不負責 commit/close。
    """
    target_date = date if date is not None else _today()
    own_conn = conn is None
    c = conn if conn is not None else None
    try:
        if own_conn:
            c = get_conn()
        rows = get_te_tf_intraday_series(c, target_date)
        if not rows:
            return None

        strengths = []
        for row in rows:
            s = row['strength']
            if s is not None:
                strengths.append(float(s))
        if not strengths:
            return None

        strength_high = max(strengths)
        strength_low = min(strengths)
        # 收盤 = 最後一筆 ts（get_te_tf_intraday_series 依 ts ASC）
        last = rows[-1]
        strength_close = last['strength']
        te_chg_pct = last['te_chg_pct']
        tf_chg_pct = last['tf_chg_pct']

        upsert_te_tf_history(
            c,
            target_date,
            strength_close,
            te_chg_pct,
            tf_chg_pct,
            strength_high,
            strength_low,
        )
        if own_conn:
            c.commit()

        return {
            'date': target_date,
            'strength_close': strength_close,
            'te_chg_pct': te_chg_pct,
            'tf_chg_pct': tf_chg_pct,
            'strength_high': strength_high,
            'strength_low': strength_low,
        }
    except Exception as e:
        logger.warning(f"te-tf-strength 當日摘要彙總失敗: {e}")
        return None
    finally:
        if own_conn and c is not None:
            try:
                c.close()
            except Exception:
                pass
