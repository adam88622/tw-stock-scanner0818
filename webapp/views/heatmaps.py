"""台美股熱力圖（自 app.py 拆出）"""
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

@app.route('/heatmap')
def heatmap():
    return render_template('heatmap.html')


@app.route('/api/heatmap')
@limiter.limit("10 per minute")
def api_heatmap():
    """Proxy finviz S&P 500 heatmap data"""
    try:
        r = http_requests.get(
            'https://finviz.com/api/map_perf.ashx?t=sec',
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        if r.status_code == 200:
            return jsonify(r.json())
        return jsonify({'error': f'Finviz returned {r.status_code}'}), 502
    except Exception as e:
        logger.error(f"Finviz heatmap API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/tw-heatmap')
def tw_heatmap():
    return render_template('tw_heatmap.html')


@app.route('/api/tw-heatmap')
def api_tw_heatmap():
    """
    盤中(9:00~13:30)：從即時 API 抓最新報價
    盤後：用 DB 收盤資料
    """
    now = datetime.now()
    is_trading = (now.weekday() < 5 and 900 <= now.hour * 100 + now.minute <= 1330)

    if is_trading:
        return _tw_heatmap_realtime()
    else:
        return _tw_heatmap_db()


def _tw_heatmap_db():
    """盤後：用 DB 收盤資料"""
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        rows = conn.execute("""
            SELECT s.sector, dp.stock_id, s.name, dp.close_price, dp.change_pct, dp.volume
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            WHERE dp.date = ? AND s.sector != '' AND s.sector IS NOT NULL
            ORDER BY s.sector, dp.volume DESC
        """, (date,)).fetchall()
        sector_map = {}
        for r in rows:
            sec = r['sector']
            if sec not in sector_map:
                sector_map[sec] = []
            if len(sector_map[sec]) < 10:
                sector_map[sec].append({
                    'id': r['stock_id'], 'name': r['name'],
                    'price': r['close_price'], 'pct': r['change_pct'],
                    'volume': r['volume']
                })
        return jsonify({'date': date, 'sectors': sector_map, 'realtime': False})
    finally:
        conn.close()


_heatmap_rt_cache = {'data': None, 'ts': 0}


def _tw_heatmap_realtime():
    """盤中：從 mis.twse.com.tw 抓即時報價"""
    import time as _time
    now_ts = _time.time()

    # 5 分鐘快取
    if _heatmap_rt_cache['data'] and (now_ts - _heatmap_rt_cache['ts']) < 300:
        return jsonify(_heatmap_rt_cache['data'])

    conn = get_conn()
    try:
        # 取所有有產業分類的股票
        stocks = conn.execute("""
            SELECT s.stock_id, s.name, s.market, s.sector
            FROM stocks s
            WHERE s.sector != '' AND s.sector IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    if not stocks:
        return _tw_heatmap_db()

    # 按產業取前 10 大（用 DB 的成交量排序）
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        vol_map = {}
        rows = conn.execute("""
            SELECT stock_id, volume FROM daily_prices WHERE date = ?
        """, (date,)).fetchall()
        for r in rows:
            vol_map[r['stock_id']] = r['volume'] or 0
    finally:
        conn.close()

    # 每個產業取前 10 檔
    from collections import defaultdict
    sector_stocks = defaultdict(list)
    for s in stocks:
        sector_stocks[s['sector']].append(s)

    # 排序取 top 10
    fetch_list = []
    for sec, slist in sector_stocks.items():
        slist.sort(key=lambda x: vol_map.get(x['stock_id'], 0), reverse=True)
        for s in slist[:10]:
            fetch_list.append(s)

    # 批次抓即時報價
    from scrapers.realtime import MIS_URL, _parse_float, _parse_int
    import requests as _req
    from config import REQUEST_HEADERS, REQUEST_TIMEOUT

    BATCH = 50
    rt_prices = {}

    for i in range(0, len(fetch_list), BATCH):
        batch = fetch_list[i:i+BATCH]
        parts = []
        for s in batch:
            prefix = 'tse' if s['market'] == 'twse' else 'otc'
            parts.append(f"{prefix}_{s['stock_id']}.tw")
        query = '|'.join(parts)

        try:
            resp = _req.get(MIS_URL, params={'ex_ch': query},
                           headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            for item in data.get('msgArray', []):
                sid = item.get('c', '')
                z = _parse_float(item.get('z'))  # 最新成交價
                y = _parse_float(item.get('y'))  # 昨收
                v = _parse_int(item.get('v'))     # 成交量(張)
                if z and y and y > 0:
                    pct = round((z - y) / y * 100, 2)
                    rt_prices[sid] = {'price': z, 'pct': pct, 'volume': v}
        except Exception:
            pass
        _time.sleep(0.3)

    # 組合結果
    today_str = datetime.now().strftime('%Y-%m-%d')
    sector_map = {}
    for sec, slist in sector_stocks.items():
        sector_map[sec] = []
        slist.sort(key=lambda x: vol_map.get(x['stock_id'], 0), reverse=True)
        for s in slist[:10]:
            sid = s['stock_id']
            if sid in rt_prices:
                sector_map[sec].append({
                    'id': sid, 'name': s['name'],
                    'price': rt_prices[sid]['price'],
                    'pct': rt_prices[sid]['pct'],
                    'volume': rt_prices[sid]['volume'],
                })

    result = {'date': today_str, 'sectors': sector_map, 'realtime': True,
              'update_time': datetime.now().strftime('%H:%M:%S')}
    _heatmap_rt_cache['data'] = result
    _heatmap_rt_cache['ts'] = now_ts
    return jsonify(result)
