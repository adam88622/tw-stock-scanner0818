"""籌碼：三大法人、連續買賣超、券商分點（自 app.py 拆出）"""
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

@app.route('/institutional')
def institutional():
    conn = get_conn()
    try:
        inst_type = request.args.get('type', 'foreign')
        days = int(request.args.get('days', '1'))
        market = request.args.get('market', 'all')
        date = request.args.get('date')
        if not date:
            row = conn.execute("SELECT MAX(date) as d FROM institutional").fetchone()
            date = row['d'] if row else None

        if not date:
            return render_template('institutional.html', buy_rows=[], sell_rows=[],
                                   date=None, inst_type=inst_type, days=days, market=market,
                                   available_dates=[],
                                   message='尚無資料，請先執行 python run_daily.py 抓取資料')

        buy_rows, sell_rows = get_ranking(conn, inst_type, days, date,
                                           market if market != 'all' else None)
        available_dates = get_trading_dates(conn, 30)

        return render_template('institutional.html',
                               buy_rows=buy_rows, sell_rows=sell_rows,
                               date=date, inst_type=inst_type, days=days, market=market,
                               available_dates=available_dates, message=None)
    finally:
        conn.close()


@app.route('/consecutive')
def consecutive():
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        inst_type = request.args.get('type', 'foreign')  # foreign, sitc, dealer
        min_days = int(request.args.get('days', '3'))
        direction = request.args.get('dir', 'buy')  # buy or sell

        col_map = {'foreign': 'foreign_buy', 'sitc': 'sitc_buy', 'dealer': 'dealer_buy'}
        if inst_type not in col_map:
            inst_type = 'foreign'
        col = col_map[inst_type]

        if not date:
            return render_template('consecutive.html', results=[], date=date,
                                   inst_type=inst_type, min_days=min_days, direction=direction)

        # Get the last 20 trading dates
        dates = conn.execute(
            "SELECT DISTINCT date FROM institutional ORDER BY date DESC LIMIT 20"
        ).fetchall()
        date_list = [d['date'] for d in dates]

        if len(date_list) < min_days:
            return render_template('consecutive.html', results=[], date=date,
                                   inst_type=inst_type, min_days=min_days, direction=direction)

        # For each stock, check consecutive days
        # Get all institutional data for recent dates
        placeholders = ','.join(['?'] * len(date_list))
        rows = conn.execute(f"""
            SELECT stock_id, date, {col} as net_buy
            FROM institutional
            WHERE date IN ({placeholders})
            ORDER BY stock_id, date DESC
        """, date_list).fetchall()

        # Group by stock
        from collections import defaultdict
        stock_data = defaultdict(list)
        for r in rows:
            stock_data[r['stock_id']].append({
                'date': r['date'],
                'net_buy': r['net_buy']
            })

        # Count consecutive days
        results = []
        for stock_id, days_data in stock_data.items():
            # days_data is sorted by date DESC
            count = 0
            total = 0
            for d in days_data:
                if direction == 'buy' and d['net_buy'] > 0:
                    count += 1
                    total += d['net_buy']
                elif direction == 'sell' and d['net_buy'] < 0:
                    count += 1
                    total += d['net_buy']
                else:
                    break
            if count >= min_days:
                results.append({
                    'stock_id': stock_id,
                    'consecutive_days': count,
                    'total_volume': total
                })

        # Sort by consecutive days desc, then total volume
        results.sort(key=lambda x: (-x['consecutive_days'], -abs(x['total_volume'])))

        # Enrich with stock info and latest price
        enriched = []
        for r in results[:100]:
            info = conn.execute("""
                SELECT s.name, s.market, dp.close_price, dp.change_pct, dp.volume
                FROM stocks s
                LEFT JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.date = ?
                WHERE s.stock_id = ?
            """, (date, r['stock_id'])).fetchone()
            if info:
                enriched.append({**r, **dict(info)})

        return render_template('consecutive.html', results=enriched, date=date,
                               inst_type=inst_type, min_days=min_days, direction=direction)
    finally:
        conn.close()


@app.route('/broker')
def broker():
    conn = get_conn()
    try:
        stock_id = request.args.get('stock', '').strip()
        # 只顯示有分點資料的日期
        available_dates = [r['date'] for r in conn.execute(
            "SELECT DISTINCT date FROM broker_trades ORDER BY date DESC LIMIT 30"
        ).fetchall()]
        date = request.args.get('date') or (available_dates[0] if available_dates else None)

        if not date:
            return render_template('broker.html', buy_rows=[], sell_rows=[],
                                   stock_id=stock_id, date=None,
                                   available_dates=[],
                                   message='尚無資料，請先執行 python run_daily.py 抓取資料')

        buy_rows = []
        sell_rows = []
        stock_name = ''
        if stock_id:
            buy_rows, sell_rows = get_broker_trades(conn, stock_id, date)
            # 取得股票名稱
            row = conn.execute("SELECT name FROM stocks WHERE stock_id = ?", (stock_id,)).fetchone()
            stock_name = row['name'] if row else ''

        return render_template('broker.html',
                               buy_rows=buy_rows, sell_rows=sell_rows,
                               stock_id=stock_id, stock_name=stock_name,
                               date=date, available_dates=available_dates,
                               message=None)
    finally:
        conn.close()


@app.route('/api/broker')
def api_broker():
    conn = get_conn()
    try:
        stock_id = request.args.get('stock', '').strip()
        date = request.args.get('date') or get_latest_date(conn)
        if not date or not stock_id:
            return jsonify({'error': '需提供 stock 參數'}), 400
        buy_rows, sell_rows = get_broker_trades(conn, stock_id, date)
        return jsonify({
            'buy': [dict(r) for r in buy_rows],
            'sell': [dict(r) for r in sell_rows],
        })
    finally:
        conn.close()


@app.route('/api/institutional')
def api_institutional():
    conn = get_conn()
    try:
        inst_type = request.args.get('type', 'foreign')
        days = int(request.args.get('days', '1'))
        market = request.args.get('market', 'all')
        date = request.args.get('date')
        if not date:
            # 用 institutional 表自己的最新日期，避免 daily_prices 超前
            row = conn.execute("SELECT MAX(date) as d FROM institutional").fetchone()
            date = row['d'] if row else None
        if not date:
            return jsonify({'error': '無資料'}), 404
        buy_rows, sell_rows = get_ranking(conn, inst_type, days, date,
                                           market if market != 'all' else None)
        return jsonify({
            'buy': [dict(r) for r in buy_rows],
            'sell': [dict(r) for r in sell_rows],
        })
    finally:
        conn.close()


@app.route('/api/export/institutional')
def export_institutional():
    import csv, io
    conn = get_conn()
    try:
        inst_type = request.args.get('type', 'foreign')
        days = int(request.args.get('days', '1'))
        market = request.args.get('market', 'all')
        date = request.args.get('date') or get_latest_date(conn)
        if not date:
            return jsonify({'error': 'no data'}), 404
        buy_rows, sell_rows = get_ranking(conn, inst_type, days, date,
                                           market if market != 'all' else None)
        type_names = {'foreign': '外資', 'sitc': '投信', 'dealer': '自營商', 'total': '三大法人'}
        type_label = type_names.get(inst_type, inst_type)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['方向', '代號', '名稱', '市場', f'{type_label}買賣超(張)', '收盤價', '漲跌%'])
        for r in buy_rows:
            writer.writerow(['買超', r['stock_id'], r['name'], r['market'], r['total_amount'], r['close_price'] or 0, r['change_pct'] or 0])
        for r in sell_rows:
            writer.writerow(['賣超', r['stock_id'], r['name'], r['market'], r['total_amount'], r['close_price'] or 0, r['change_pct'] or 0])
        resp = app.response_class(output.getvalue(), mimetype='text/csv',
                                   headers={'Content-Disposition': f'attachment;filename=institutional_{inst_type}_{days}d_{date}.csv'})
        return resp
    finally:
        conn.close()


@app.route('/api/export/broker')
def export_broker():
    import csv, io
    conn = get_conn()
    try:
        stock_id = request.args.get('stock', '').strip()
        date = request.args.get('date') or get_latest_date(conn)
        if not stock_id or not date:
            return jsonify({'error': 'missing params'}), 400
        buy_rows, sell_rows = get_broker_trades(conn, stock_id, date)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['方向', '券商', '買進', '賣出', '淨買賣超', '佔成交%'])
        for r in buy_rows:
            writer.writerow(['買超', r['broker_name'], r['buy_volume'], r['sell_volume'], r['net_volume'], r['pct']])
        for r in sell_rows:
            writer.writerow(['賣超', r['broker_name'], r['buy_volume'], r['sell_volume'], r['net_volume'], r['pct']])
        resp = app.response_class(output.getvalue(), mimetype='text/csv',
                                   headers={'Content-Disposition': f'attachment;filename=broker_{stock_id}_{date}.csv'})
        return resp
    finally:
        conn.close()
