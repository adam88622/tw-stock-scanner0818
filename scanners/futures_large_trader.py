"""
個股期「期貨大戶淨部位」與「期貨籌碼集中度」計算模組

資料源：futures_large_trader（期交所大額交易人未沖銷部位，僅到期月份 999999
       ＝所有月份合計，避免換月時部位在新舊月之間搬家造成假性跳動）
       × stock_futures_map（商品代碼 ↔ 標的股票、大小型、張/口換算）。

大戶淨部位（v3 公式）——先把特定法人從十大裡扣掉，再算多空淨額：

    大戶(口) = (含造市前10買 − 特法前10買) − (含造市前10賣 − 特法前10賣)

    其中「含造市」= 交易人類別 0（整體十大交易人，含造市者），
        「特法」  = 交易人類別 1（特定法人）。
    得到的是「非特法的十大交易人（造市商＋自然人大戶）淨部位」。

同一檔股票可能同時有大型與小型契約（例：2330 → CD 大型、QF 小型），
兩者各自套 v3 公式後，用契約乘數換算成「張」相加：
    股票 2 張/口、小型股票 0.1 張/口、ETF 10 張/口、小型 ETF 1 張/口。

期貨籌碼集中度：取當日「未沖銷部位最大的那個契約」（幾乎都是大型），
以期交所原始欄位對全市場未沖銷部位取百分比：
    前五/前十大買方(賣方)集中度 = 該欄位 ÷ 全市場未沖銷部位 × 100
    十大淨集中度               = (前十大買 − 前十大賣) ÷ 全市場未沖銷部位 × 100
    大戶淨集中度               = v3 大戶淨部位(口) ÷ 全市場未沖銷部位 × 100
※ 集中度用「含造市者」的原始欄位，是市場慣用定義；大戶淨集中度才是扣掉特法的版本，
  前端兩者分開標示，不混用。
"""
import logging

logger = logging.getLogger(__name__)

TRADER_TYPE_ALL = 0   # 整體十大交易人（含造市者）
TRADER_TYPE_INST = 1  # 特定法人


def _pct(num, den):
    """安全百分比；分母 <= 0 → None（前端顯示 '—'，不畫成 0%）。"""
    if not den or den <= 0:
        return None
    return round(num / den * 100, 2)


def get_stock_futures_products(conn, stock_id):
    """
    取某檔股票的所有股票期貨商品（大型 + 小型）。
    回傳 list[dict] {product_code, product_name, is_mini, is_etf, lots_per_contract}，
    大型在前。查表失敗（表未建立 / 未回補）→ []。
    """
    try:
        rows = conn.execute("""
            SELECT product_code, product_name, stock_name, is_mini, is_etf, lots_per_contract
            FROM stock_futures_map
            WHERE stock_id = ?
            ORDER BY is_mini ASC, product_code ASC
        """, (str(stock_id).strip(),)).fetchall()
    except Exception as e:
        logger.warning('讀取 stock_futures_map 失敗（視為無股期）: %s', e)
        return []
    return [dict(r) for r in rows]


def has_stock_futures(conn, stock_id):
    """該股是否有股票期貨標的（供頁面標註「有股期」徽章）。"""
    return bool(get_stock_futures_products(conn, stock_id))


def get_stock_large_trader(conn, stock_id, days=20):
    """
    取某檔股票近 N 個交易日的期貨大戶淨部位與籌碼集中度。

    參數：
        stock_id: 股票代號，如 '2330'。
        days: 取最近幾個「有大額交易人資料」的交易日（預設 20，比照頁面「近20日」）。

    回傳 dict：
        {
          'has_futures': bool,          # 是否為股票期貨標的
          'products': [...],            # 該股所有期貨商品（大型/小型）
          'main_code' / 'mini_code',    # 大型 / 小型契約代碼（可能 None）
          'series': [...],              # 由舊到新，每個交易日一筆（見下）
          'latest': {...} | None,       # series 最後一筆
        }
    series 每筆：
        date, net_lots(淨部位 張), main_contracts(大型 口), mini_contracts(小型 口),
        market_oi(集中度所用契約之全市場未沖銷 口), conc_code(該契約代碼),
        top5_buy_pct/top5_sell_pct/top10_buy_pct/top10_sell_pct(集中度 %),
        conc_net_pct(十大淨集中度 %), big_net_pct(大戶淨集中度 %)
    無股期 / 無資料 → has_futures 依對照表判定，series 為 []。
    """
    products = get_stock_futures_products(conn, stock_id)
    result = {
        'has_futures': bool(products),
        'products': products,
        'main_code': next((p['product_code'] for p in products if not p['is_mini']), None),
        'mini_code': next((p['product_code'] for p in products if p['is_mini']), None),
        'series': [],
        'latest': None,
    }
    if not products:
        return result

    codes = [p['product_code'] for p in products]
    lots_by_code = {p['product_code']: (p['lots_per_contract'] or 2.0) for p in products}
    mini_by_code = {p['product_code']: bool(p['is_mini']) for p in products}
    placeholders = ','.join('?' * len(codes))

    try:
        # 先取最近 N 個有資料的交易日，再撈那些日子的明細（避免整段歷史都讀進來）
        date_rows = conn.execute(f"""
            SELECT DISTINCT date FROM futures_large_trader
            WHERE product_code IN ({placeholders})
            ORDER BY date DESC LIMIT ?
        """, (*codes, int(days))).fetchall()
        dates = [r['date'] for r in date_rows]
        if not dates:
            return result

        d_ph = ','.join('?' * len(dates))
        rows = conn.execute(f"""
            SELECT date, product_code, trader_type, top5_buy, top5_sell,
                   top10_buy, top10_sell, market_oi
            FROM futures_large_trader
            WHERE product_code IN ({placeholders}) AND date IN ({d_ph})
        """, (*codes, *dates)).fetchall()
    except Exception as e:
        logger.warning('讀取 futures_large_trader 失敗（%s）: %s', stock_id, e)
        return result

    # by_date[date][product_code][trader_type] = row
    by_date = {}
    for r in rows:
        by_date.setdefault(r['date'], {}).setdefault(r['product_code'], {})[r['trader_type']] = r

    series = []
    for d in sorted(by_date):  # 由舊到新
        per_product = by_date[d]
        net_lots = 0.0
        main_contracts = 0
        mini_contracts = 0
        # 集中度取「經濟規模最大」的契約：先大型後小型，同型再比未沖銷張數。
        # 不能直接比未沖銷「口數」——小型契約乘數只有大型的 1/20，口數天生就多
        # （例：2330 小型 9.6 萬口 ≈ 9,669 張，遠小於大型），比口數會選錯契約。
        conc_code, conc_all, conc_net_contracts, conc_rank = None, None, 0, None

        for code, tmap in per_product.items():
            a = tmap.get(TRADER_TYPE_ALL)
            if a is None:
                continue  # 沒有「含造市者」那列就算不出 v3，整檔契約跳過
            b = tmap.get(TRADER_TYPE_INST)
            b_buy = b['top10_buy'] if b else 0
            b_sell = b['top10_sell'] if b else 0
            net_contracts = (a['top10_buy'] - b_buy) - (a['top10_sell'] - b_sell)

            lots = lots_by_code.get(code, 2.0)
            net_lots += net_contracts * lots
            if mini_by_code.get(code):
                mini_contracts += net_contracts
            else:
                main_contracts += net_contracts

            rank = (0 if not mini_by_code.get(code) else 1, -(a['market_oi'] or 0) * lots)
            if conc_rank is None or rank < conc_rank:
                conc_code, conc_all, conc_net_contracts, conc_rank = code, a, net_contracts, rank

        if conc_all is None:
            continue

        oi = conc_all['market_oi'] or 0
        series.append({
            'date': d,
            'net_lots': round(net_lots, 1),
            'main_contracts': main_contracts,
            'mini_contracts': mini_contracts,
            'conc_code': conc_code,
            'market_oi': oi,
            'top5_buy_pct': _pct(conc_all['top5_buy'], oi),
            'top5_sell_pct': _pct(conc_all['top5_sell'], oi),
            'top10_buy_pct': _pct(conc_all['top10_buy'], oi),
            'top10_sell_pct': _pct(conc_all['top10_sell'], oi),
            'conc_net_pct': _pct(conc_all['top10_buy'] - conc_all['top10_sell'], oi),
            'big_net_pct': _pct(conc_net_contracts, oi),
        })

    result['series'] = series
    result['latest'] = series[-1] if series else None
    return result


def get_latest_data_date(conn):
    """futures_large_trader 最新資料日期（供頁面顯示資料新鮮度）；無資料 → None。"""
    try:
        r = conn.execute("SELECT MAX(date) AS d FROM futures_large_trader").fetchone()
        return r['d'] if r else None
    except Exception:
        return None
