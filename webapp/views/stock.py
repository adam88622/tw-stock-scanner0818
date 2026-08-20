"""個股詳情、自選股、搜尋（自 app.py 拆出）"""
import sys
import os
import json
import time
import logging
import threading
import requests as http_requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import load_dotenv
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask_httpauth import HTTPBasicAuth
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash, generate_password_hash
from models.database import (init_db, get_conn, get_latest_date, get_breakouts_by_date,
                             get_trading_dates, get_broker_trades,
                             get_regime_history, get_latest_regime,
                             add_to_watchlist, remove_from_watchlist,
                             get_watchlist, is_in_watchlist)
from scanners.institutional import get_ranking
from scanners.futures_large_trader import get_stock_large_trader
from scrapers.market import fetch_futures_oi, fetch_retail_ratio, fetch_put_call_ratio, _finmind_get
import sqlite3

import logging
logger = logging.getLogger(__name__)
from webapp.core import app, auth, limiter

def fetch_margin_data(stock_id, days=20):
    """從 FinMind 抓取融資融券資料"""
    end = datetime.now()
    start = end - timedelta(days=days + 15)
    try:
        r = http_requests.get("https://api.finmindtrade.com/api/v4/data",
            params={
                "dataset": "TaiwanStockMarginPurchaseShortSale",
                "data_id": stock_id,
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            },
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        d = r.json()
        if d.get("status") != 200 or not d.get("data"):
            return []
        result = []
        for row in d["data"]:
            bal = float(row.get("MarginPurchaseTodayBalance", 0))
            limit = float(row.get("MarginPurchaseLimit", 0))
            s_bal = float(row.get("ShortSaleTodayBalance", 0))
            result.append({
                "date": row["date"],
                "balance": bal,
                "limit": limit,
                "use_rate": round(bal / limit * 100, 2) if limit > 0 else 0,
                "short_bal": s_bal,
            })
        result.sort(key=lambda x: x["date"])
        return result[-days:]
    except Exception as e:
        logger.error(f"融資融券資料抓取失敗 ({stock_id}): {e}")
        return []


def calc_institutional_cost(conn, stock_id, days=20):
    """Estimate institutional cost by VWAP of buying days"""
    rows = conn.execute("""
        SELECT dp.date, dp.close_price, dp.volume, dp.high_price, dp.low_price,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.sitc_buy, 0) as sitc_buy,
               COALESCE(i.dealer_buy, 0) as dealer_buy
        FROM daily_prices dp
        LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
        WHERE dp.stock_id = ?
        ORDER BY dp.date DESC LIMIT ?
    """, (stock_id, days)).fetchall()

    if not rows:
        return None

    result = {}
    for inst_type, col in [('foreign', 'foreign_buy'), ('sitc', 'sitc_buy'), ('dealer', 'dealer_buy')]:
        total_cost = 0
        total_vol = 0
        days_buying = 0
        for r in rows:
            buy_vol = r[col]
            if buy_vol > 0:
                avg_price = (r['high_price'] + r['low_price']) / 2
                total_cost += avg_price * buy_vol
                total_vol += buy_vol
                days_buying += 1

        if total_vol > 0:
            vwap = round(total_cost / total_vol, 2)
            result[inst_type] = {
                'cost': vwap,
                'total_volume': total_vol,
                'days_buying': days_buying,
            }
        else:
            result[inst_type] = None

    if rows:
        result['current_price'] = rows[0]['close_price']
        result['period'] = days

    return result


def calc_ma(closes, period):
    """計算移動平均線"""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(round(sum(closes[i - period + 1:i + 1]) / period, 2))
    return result


def calc_rsi(closes, period=14):
    """計算 RSI 指標"""
    if len(closes) < period + 1:
        return [None] * len(closes)
    result = [None] * period
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, c) for c in changes[:period]]
    losses = [max(0, -c) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100 - 100 / (1 + rs), 2))
    for i in range(period, len(changes)):
        gain = max(0, changes[i])
        loss = max(0, -changes[i])
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100 - 100 / (1 + rs), 2))
    return result


def _get_stock_detail_data(conn, stock_id):
    """取得個股詳細資料（K線、法人、券商），供頁面和 API 共用"""
    # 股票基本資料
    stock_row = conn.execute("SELECT stock_id, name, market FROM stocks WHERE stock_id = ?",
                             (stock_id,)).fetchone()
    if not stock_row:
        return None

    stock_name = stock_row['name']
    stock_market = stock_row['market']

    # K線資料：全歷史（圖表預設顯示最近 250 根，可拖曳到完整歷史）
    price_rows = conn.execute("""
        SELECT date, open_price, high_price, low_price, close_price, volume, change_pct
        FROM daily_prices WHERE stock_id = ? ORDER BY date ASC
    """, (stock_id,)).fetchall()

    kline_data = []
    closes = []
    for r in price_rows:
        kline_data.append({
            'date': r['date'],
            'open': r['open_price'],
            'high': r['high_price'],
            'low': r['low_price'],
            'close': r['close_price'],
            'volume': r['volume'],
            'change_pct': r['change_pct'],
        })
        closes.append(r['close_price'])

    # 計算技術指標
    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    rsi = calc_rsi(closes, 14)

    # 最新價格資訊
    current_price = closes[-1] if closes else 0
    current_change = kline_data[-1]['change_pct'] if kline_data else 0

    # 法人買賣超：最近 20 個交易日
    inst_rows = conn.execute("""
        SELECT date, foreign_buy, sitc_buy, dealer_buy, total_buy
        FROM institutional
        WHERE stock_id = ? ORDER BY date DESC LIMIT 20
    """, (stock_id,)).fetchall()
    inst_data = []
    for r in inst_rows:
        inst_data.append({
            'date': r['date'],
            'foreign_buy': r['foreign_buy'],
            'sitc_buy': r['sitc_buy'],
            'dealer_buy': r['dealer_buy'],
            'total_buy': r['total_buy'],
        })

    # 法人合計
    foreign_total = sum(r['foreign_buy'] for r in inst_rows)
    sitc_total = sum(r['sitc_buy'] for r in inst_rows)
    dealer_total = sum(r['dealer_buy'] for r in inst_rows)

    # 券商分點
    latest_date = get_latest_date(conn)
    broker_buy, broker_sell = get_broker_trades(conn, stock_id, latest_date) if latest_date else ([], [])

    return {
        'stock_id': stock_id,
        'stock_name': stock_name,
        'stock_market': stock_market,
        'current_price': current_price,
        'current_change': current_change,
        'kline': kline_data,
        'ma5': ma5,
        'ma20': ma20,
        'ma60': ma60,
        'rsi': rsi,
        'institutional': inst_data,
        'foreign_total': foreign_total,
        'sitc_total': sitc_total,
        'dealer_total': dealer_total,
        'broker_buy': [dict(r) for r in broker_buy],
        'broker_sell': [dict(r) for r in broker_sell],
        'broker_date': latest_date,
    }


@app.route('/stock')
def stock_detail():
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return render_template('stock.html', data=None, message='請提供股票代號（例：/stock?id=2330）')

    conn = get_conn()
    try:
        data = _get_stock_detail_data(conn, stock_id)
        if not data:
            return render_template('stock.html', data=None,
                                   message=f'找不到股票 {stock_id}')

        # 融資融券資料（即時從 FinMind 抓取）
        margin_data = fetch_margin_data(stock_id)

        # 主力成本線
        inst_cost = calc_institutional_cost(conn, stock_id, days=20)

        # 自選股狀態
        in_watchlist = is_in_watchlist(conn, stock_id)

        # 期貨大戶淨部位 / 籌碼集中度（近20日；無股期則 has_futures=False）
        try:
            large_trader = get_stock_large_trader(conn, stock_id, days=20)
        except Exception as e:
            logger.warning(f"期貨大戶資料讀取失敗 {stock_id}: {e}")
            large_trader = {'has_futures': False, 'products': [], 'series': [], 'latest': None}

        return render_template('stock.html', data=data, message=None,
                               kline_json=json.dumps(data['kline']),
                               ma5_json=json.dumps(data['ma5']),
                               ma20_json=json.dumps(data['ma20']),
                               ma60_json=json.dumps(data['ma60']),
                               rsi_json=json.dumps(data['rsi']),
                               margin_json=json.dumps(margin_data),
                               inst_cost=inst_cost,
                               in_watchlist=in_watchlist,
                               large_trader=large_trader,
                               large_trader_json=json.dumps(large_trader['series']))
    finally:
        conn.close()


@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if len(q) < 1:
        return jsonify([])
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT stock_id, name, market FROM stocks WHERE stock_id LIKE ? OR name LIKE ? LIMIT 10",
            (f'{q}%', f'%{q}%')
        ).fetchall()
        return jsonify([{'id': r['stock_id'], 'name': r['name'], 'market': r['market']} for r in rows])
    finally:
        conn.close()


@app.route('/api/stock')
def api_stock():
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return jsonify({'error': '需提供 id 參數'}), 400

    conn = get_conn()
    try:
        data = _get_stock_detail_data(conn, stock_id)
        if not data:
            return jsonify({'error': f'找不到股票 {stock_id}'}), 404
        return jsonify(data)
    finally:
        conn.close()


@app.route('/api/stock-large-trader')
def api_stock_large_trader():
    """
    個股期「期貨大戶淨部位 + 籌碼集中度」。
    參數：id=股票代號（必填）、days=取幾個交易日（預設 20，上限 250）。
    無股期標的 → has_futures=false、series=[]（不視為錯誤）。
    """
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return jsonify({'error': '需提供 id 參數'}), 400
    try:
        days = max(1, min(250, int(request.args.get('days', 20))))
    except (TypeError, ValueError):
        days = 20

    conn = get_conn()
    try:
        return jsonify(get_stock_large_trader(conn, stock_id, days=days))
    except Exception as e:
        logger.error(f"api_stock_large_trader({stock_id}) 失敗: {e}", exc_info=True)
        return jsonify({'error': '期貨大戶資料讀取失敗'}), 500
    finally:
        conn.close()


@app.route('/watchlist')
def watchlist():
    conn = get_conn()
    try:
        items = get_watchlist(conn)
        return render_template('watchlist.html', items=items)
    finally:
        conn.close()


@app.route('/api/watchlist/add', methods=['POST'])
def api_watchlist_add():
    stock_id = request.json.get('stock_id', '').strip()
    if not stock_id:
        return jsonify({'error': 'missing stock_id'}), 400
    conn = get_conn()
    try:
        add_to_watchlist(conn, stock_id)
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/api/watchlist/remove', methods=['POST'])
def api_watchlist_remove():
    stock_id = request.json.get('stock_id', '').strip()
    conn = get_conn()
    try:
        remove_from_watchlist(conn, stock_id)
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/api/stock-realtime')
@limiter.limit("10 per minute")
def api_stock_realtime():
    """盤中即時報價（單一個股），用 mis.twse.com.tw"""
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return jsonify({'error': 'missing id'}), 400

    conn = get_conn()
    try:
        stock = conn.execute("SELECT stock_id, name, market FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if not stock:
            return jsonify({'error': 'not found'}), 404
    finally:
        conn.close()

    from scrapers.realtime import MIS_URL, _parse_float, _parse_int
    prefix = 'tse' if stock['market'] == 'twse' else 'otc'
    query = f'{prefix}_{stock_id}.tw'

    try:
        resp = http_requests.get(MIS_URL, params={'ex_ch': query},
                                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        data = resp.json()
        items = data.get('msgArray', [])
        if not items:
            return jsonify({'error': 'no data'}), 404

        item = items[0]
        z = _parse_float(item.get('z'))       # 最新成交價
        y = _parse_float(item.get('y'))       # 昨收
        o = _parse_float(item.get('o'))       # 開盤
        h = _parse_float(item.get('h'))       # 最高
        l = _parse_float(item.get('l'))       # 最低
        v = _parse_int(item.get('v'))         # 成交量(張)
        t = item.get('t', '')                 # 時間

        pct = round((z - y) / y * 100, 2) if z and y and y > 0 else 0
        change = round(z - y, 2) if z and y else 0

        return jsonify({
            'stock_id': stock_id,
            'price': z,
            'change': change,
            'change_pct': pct,
            'open': o,
            'high': h,
            'low': l,
            'volume': v,
            'time': t,
            'yesterday': y,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-preview')
def api_stock_preview():
    stock_id = request.args.get('id', '').strip()
    conn = get_conn()
    try:
        stock = conn.execute("SELECT * FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if not stock:
            return jsonify({'error': 'not found'}), 404
        prices = conn.execute("""
            SELECT date, close_price, open_price, high_price, low_price, change_pct, volume
            FROM daily_prices WHERE stock_id=? ORDER BY date DESC LIMIT 20
        """, (stock_id,)).fetchall()
        inst = conn.execute("""
            SELECT date, foreign_buy, sitc_buy, dealer_buy
            FROM institutional WHERE stock_id=? ORDER BY date DESC LIMIT 5
        """, (stock_id,)).fetchall()
        in_watchlist = is_in_watchlist(conn, stock_id)
        return jsonify({
            'stock_id': stock['stock_id'],
            'name': stock['name'],
            'market': stock['market'],
            'prices': [dict(r) for r in prices],
            'institutional': [dict(r) for r in inst],
            'in_watchlist': in_watchlist,
        })
    finally:
        conn.close()
