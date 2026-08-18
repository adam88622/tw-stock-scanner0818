"""
期現價差（正/逆價差）掃描模組

資料源：
  1. 群益個股期報價服務（CAPITAL_QUOTE_URL/api/futures）：約 300 檔個股期即時價，
     價格已 ÷100 為真實值，underlying 為標的現股代號（如 2330）。
  2. 現股即時價：scrapers.spot_quote（共用 TTL 快取 + 跨行程熔斷器，來源 MIS）。
     取不到者才 fallback 到 scanner DB daily_prices.close，並標記 spot_stale=True。
     ※ DB 那條路是「上一次 worker 成功寫入的價」，盤中可能落後數十分鐘，
       絕不可當即時價呈現；前端須依 spot_status/spot_stale 如實標示。
     標的中文名一律從 DB stocks 表用代號查（群益/凱基給的名字是亂碼）。

計算：
  基差 basis = 期貨close − 現股價
  基差% basis_pct = basis / 現股價 × 100
  分類：basis>0 → 正價差(Contango)；basis<0 → 逆價差(Backwardation)；==0 → 平水

統計：正價差 N / 逆價差 M / 平水 K / 無現價 J（無現股價列出但不計入正逆統計，不靜默丟棄）
"""
import logging
from datetime import date, datetime

import requests

from config import CAPITAL_QUOTE_URL
from models.database import get_conn

logger = logging.getLogger(__name__)


def _load_active_dispositions(conn, as_of_date=None):
    """
    取「目前有效（處置期間涵蓋 as_of_date）」的處置股，沿用 /disposition 同一資料源：
    disposition_announcements 表（scanners.disposition_rules.get_active_dispositions 同條件）。

    回傳 {stock_id: {action, condition, period_start, period_end}}。
    同一檔若有多筆有效紀錄，取 period_start 最新的一筆。
    讀表失敗（例如資料未回補）一律回空 dict，不影響既有基差計算。
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()
    disp = {}
    try:
        rows = conn.execute(
            """
            SELECT stock_id, name, period_start, period_end, action, condition
            FROM disposition_announcements
            WHERE is_real_stock = 1
              AND period_start <= ?
              AND period_end >= ?
            ORDER BY period_start DESC
            """,
            (as_of_date, as_of_date),
        ).fetchall()
    except Exception as e:
        logger.warning(f"futures_basis 讀取處置股清單失敗（略過標註）: {e}")
        return disp

    for r in rows:
        sid = r['stock_id']
        if sid in disp:
            continue  # 已取較新的 period_start（ORDER BY 由新到舊）
        interval, ilabel = _disposition_interval(r['action'])
        disp[sid] = {
            'action': r['action'],
            'condition': r['condition'],
            'period_start': r['period_start'],
            'period_end': r['period_end'],
            'match_interval': interval,   # 撮合間隔(分鐘): 5 / 20 / 60
            'match_label': ilabel,        # 例「第一次處置·每5分撮合」
        }
    return disp


def _disposition_interval(action):
    """由處置措施推「分盤撮合間隔(分鐘)」。

    TWSE 規則（已用實際公告驗證）：
      第一次處置 → 每 5 分鐘撮合
      第二次(含以上)處置 → 每 20 分鐘撮合
      人工管制撮合 → 每 60 分鐘
    回傳 (interval_min:int|None, label:str)。
    """
    a = str(action or '')
    if '人工' in a:
        return 60, '人工管制·每60分撮合'
    if '第一次' in a:
        return 5, '第一次處置·每5分撮合'
    if '第二次' in a:
        return 20, '第二次處置·每20分撮合'
    if any(k in a for k in ('第三次', '第四次', '第五次')):
        return 20, a + '·每20分撮合'
    return None, a


def _fetch_capital_futures():
    """抓群益個股期報價。回傳 (futures_dict, status_dict)。連不上拋例外。"""
    url = CAPITAL_QUOTE_URL.rstrip('/') + '/api/futures'
    resp = requests.get(url, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    return data.get('futures', {}) or {}, data.get('status', {}) or {}


def _load_db_prices(conn, underlyings):
    """
    用 DB 取現股最新（最大日期）收盤價與中文名。
    回傳 ({stock_id: {'close': float|None, 'name': str}}, latest_date)。

    注意：latest_date 只是「日期」，盤中該列會被 realtime worker 持續覆寫，
    所以它代表的價格新舊取決於 worker 最後一次成功寫入的時間，可能落後數十分鐘。
    呼叫端須把它當 stale 標示，不可當即時價。
    """
    result = {}
    if not underlyings:
        return result, None
    latest_row = conn.execute("SELECT MAX(date) AS d FROM daily_prices").fetchone()
    latest_date = latest_row['d'] if latest_row else None

    placeholders = ','.join('?' * len(underlyings))
    ids = list(underlyings)

    # 中文名
    for r in conn.execute(
        f"SELECT stock_id, name FROM stocks WHERE stock_id IN ({placeholders})", ids
    ).fetchall():
        result.setdefault(r['stock_id'], {})['name'] = r['name']

    # 最新日期收盤價
    if latest_date:
        for r in conn.execute(
            f"SELECT stock_id, close_price FROM daily_prices "
            f"WHERE date = ? AND stock_id IN ({placeholders})",
            [latest_date] + ids,
        ).fetchall():
            result.setdefault(r['stock_id'], {})['close'] = r['close_price']
    return result, latest_date


def _parse_float(val):
    if val is None or val in ('-', '', '--'):
        return None
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return None


def _fetch_spot_prices(conn, stock_ids):
    """
    取現股即時價。實作已抽到 scrapers.spot_quote（TTL 快取 + 跨行程熔斷器）。

    原本此處自帶一份 MIS 抓取：每 20 秒對 228 檔打 5 批，且每個開著的瀏覽器
    分頁各打一輪，是把本機 IP 打到被 MIS 封鎖的主因之一。改為共用層後：
      - TTL 內多分頁併發只實抓一次
      - 連續失敗即熔斷靜默，不再空轉續命封鎖
      - 回傳 meta 讓前端能如實顯示現價的實際時間與來源

    回傳 (prices, meta)。拿不到的標的不放入 prices，
    交由 compute_futures_basis() 逐列 fallback DB 收盤並標記 spot_stale。
    """
    from scrapers.spot_quote import get_spot_prices
    return get_spot_prices(conn, stock_ids)


def compute_futures_basis():
    """
    主計算函式。回傳 dict：
      {
        'ok': bool,
        'error': str|None,
        'rows': [ {product, name, underlying, fut_price, spot_price,
                   basis, basis_pct, volume, status, spot_stale,
                   is_disposition, disposition_info} ],
        'stats': {contango, backwardation, flat, no_spot, disposition,
                  spot_realtime, spot_stale, total},
        'quote_status': {priced, total, last_sweep, connected, uncovered},
        'spot_status': {fetched_at, age_sec, source, partial, circuit,
                        elapsed_sec, realtime, stale, db_fallback_date},
      }
    spot_status.fetched_at 是現價「實際」取得時間（可能落後數分鐘），
    前端須顯示它而非瀏覽器時鐘，否則舊價會被誤呈現為即時。
    rows 預設依 basis_pct 由大到小排序（無現價列墊底）。
    """
    try:
        futures, qstatus = _fetch_capital_futures()
    except Exception as e:
        logger.warning(f"群益報價服務連線失敗: {e}")
        return {
            'ok': False,
            'error': f'無法連線群益報價服務（{CAPITAL_QUOTE_URL}）：{e}',
            'rows': [], 'stats': {}, 'quote_status': {},
        }

    # 問題1：同一 underlying 可能對應多個期貨商品（標準近月 + 冷門重複碼），
    # 只保留成交量(volume)最大的那一個（最有流動性的近月），其餘捨棄。
    def _vol(f):
        try:
            return int(f.get('volume') or 0)
        except (ValueError, TypeError):
            return 0

    best_by_underlying = {}   # underlying -> (product, f)
    no_underlying = []        # 無 underlying 者照原樣保留（不去重）
    for product, f in futures.items():
        underlying = f.get('underlying')
        if not underlying:
            no_underlying.append((product, f))
            continue
        cur = best_by_underlying.get(underlying)
        if cur is None or _vol(f) > _vol(cur[1]):
            best_by_underlying[underlying] = (product, f)

    deduped = list(best_by_underlying.values()) + no_underlying

    # 收集 underlying 代號
    underlyings = sorted(best_by_underlying.keys())

    conn = get_conn()
    try:
        db_info, db_latest_date = _load_db_prices(conn, underlyings)

        # 問題2：現股價改抓即時為主，對所有 underlying 取即時現價。
        # 拿不到某檔時，下方逐列 fallback 回 DB 收盤（標記 spot_stale）。
        spot_fetch_started = datetime.now()
        if underlyings:
            mis_prices, spot_meta = _fetch_spot_prices(conn, underlyings)
        else:
            mis_prices, spot_meta = {}, {}
        # 現價的「實際時間」以報價層回報者為準，不可用本次請求時間冒充：
        # 走快取或熔斷時資料可能是數分鐘前的，用 now() 會讓前端誤顯示為即時。
        spot_fetched_at = spot_meta.get('fetched_at')

        # 目前有效的處置股清單（沿用 /disposition 同一資料源）
        disp_map = _load_active_dispositions(conn)
    finally:
        conn.close()

    rows = []
    contango = backwardation = flat = no_spot = 0
    disposition_count = 0
    spot_realtime_count = 0
    spot_stale_count = 0

    for product, f in deduped:
        underlying = f.get('underlying')
        fut_price = _parse_float(f.get('close'))
        info = db_info.get(underlying, {}) if underlying else {}
        name = info.get('name') or (underlying or product)

        # 現股價：優先用 MIS 即時，拿不到再 fallback DB 收盤（標記資料較舊）
        spot = mis_prices.get(underlying) if underlying else None
        spot_stale = False
        if spot is not None:
            spot_realtime_count += 1
        else:
            spot = info.get('close')
            if spot is not None:
                spot_stale = True
                spot_stale_count += 1

        # 期貨 bid/ask（供前端顯示與新鮮度判斷）
        fut_bid = _parse_float(f.get('bid'))
        fut_ask = _parse_float(f.get('ask'))

        volume = f.get('volume')
        try:
            volume = int(volume) if volume is not None else 0
        except (ValueError, TypeError):
            volume = 0

        # 處置股標註（目前有效者）
        disp = disp_map.get(underlying) if underlying else None
        is_disposition = disp is not None
        disposition_info = disp if is_disposition else None
        if is_disposition:
            disposition_count += 1

        if spot is None or spot == 0 or fut_price is None:
            no_spot += 1
            rows.append({
                'product': product, 'name': name, 'underlying': underlying or '',
                'fut_price': fut_price, 'spot_price': spot,
                'fut_bid': fut_bid, 'fut_ask': fut_ask,
                'basis': None, 'basis_pct': None,
                'volume': volume, 'status': 'no_spot',
                'spot_stale': spot_stale,
                'is_disposition': is_disposition,
                'disposition_info': disposition_info,
            })
            continue

        basis = fut_price - spot
        basis_pct = basis / spot * 100.0
        if basis > 0:
            status = 'contango'
            contango += 1
        elif basis < 0:
            status = 'backwardation'
            backwardation += 1
        else:
            status = 'flat'
            flat += 1

        rows.append({
            'product': product, 'name': name, 'underlying': underlying or '',
            'fut_price': round(fut_price, 2), 'spot_price': round(spot, 2),
            'fut_bid': round(fut_bid, 2) if fut_bid is not None else None,
            'fut_ask': round(fut_ask, 2) if fut_ask is not None else None,
            'basis': round(basis, 2), 'basis_pct': round(basis_pct, 3),
            'volume': volume, 'status': status,
            'spot_stale': spot_stale,
            'is_disposition': is_disposition,
            'disposition_info': disposition_info,
        })

    # 預設依 basis_pct 由大到小，無現價列墊底
    rows.sort(key=lambda r: (r['basis_pct'] is not None, r['basis_pct'] if r['basis_pct'] is not None else 0), reverse=True)

    priced = qstatus.get('priced')
    total_contracts = qstatus.get('total')
    uncovered = None
    if isinstance(priced, int) and isinstance(total_contracts, int):
        uncovered = total_contracts - priced

    return {
        'ok': True,
        'error': None,
        'rows': rows,
        'stats': {
            'contango': contango,
            'backwardation': backwardation,
            'flat': flat,
            'no_spot': no_spot,
            'disposition': disposition_count,
            'spot_realtime': spot_realtime_count,
            'spot_stale': spot_stale_count,
            'total': len(rows),
        },
        'quote_status': {
            'priced': priced,
            'total': total_contracts,
            'uncovered': uncovered,
            'last_sweep': qstatus.get('last_sweep'),
            'connected': qstatus.get('connected'),
        },
        'spot_status': {
            # fetched_at = 現價「實際」抓到的時間（可能是快取或數分鐘前），
            # 不是本次 API 被呼叫的時間。前端必須顯示這個值而非瀏覽器時鐘。
            'fetched_at': spot_fetched_at,
            'age_sec': spot_meta.get('age_sec'),
            'source': spot_meta.get('source'),          # mis / cache / none
            'partial': spot_meta.get('partial', False),  # 熔斷或批次中斷，涵蓋不全
            'circuit': spot_meta.get('circuit'),         # 熔斷器狀態
            'elapsed_sec': round((datetime.now() - spot_fetch_started).total_seconds(), 1),
            'realtime': spot_realtime_count,
            'stale': spot_stale_count,
            'db_fallback_date': db_latest_date,          # stale 列用的是哪一天的 DB 價
        },
    }
