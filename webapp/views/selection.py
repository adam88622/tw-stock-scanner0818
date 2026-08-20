"""選股：突破、篩選器、回測、產業族群（自 app.py 拆出）"""
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
from webapp.shared import get_market_temperature

@app.route('/breakout')
def breakout():
    conn = get_conn()
    try:
        date = request.args.get('date') or get_latest_date(conn)
        market = request.args.get('market', 'all')
        filter_days = request.args.get('days', '')  # e.g. '5,10,20'

        if not date:
            return render_template('breakout.html', rows=[], date=None, market=market,
                                   filter_days=[], available_dates=[],
                                   message='尚無資料，請先執行 python run_daily.py 抓取資料')

        rows = get_breakouts_by_date(conn, date, market if market != 'all' else None)
        available_dates = get_trading_dates(conn, 30)

        # 篩選特定突破天數
        active_filters = [int(d) for d in filter_days.split(',') if d.isdigit()]
        if active_filters:
            filtered = []
            for r in rows:
                match = False
                for d in active_filters:
                    if r[f'break_{d}'] == 1:
                        match = True
                        break
                if match:
                    filtered.append(r)
            rows = filtered

        # 取得市場體溫（失敗不影響頁面）
        regime_info = None
        if get_market_temperature is not None:
            try:
                regime_info = get_market_temperature(lookback_days=5)
            except Exception:
                pass

        return render_template('breakout.html', rows=rows, date=date, market=market,
                               filter_days=active_filters, available_dates=available_dates,
                               message=None, regime_info=regime_info)
    finally:
        conn.close()


@app.route('/api/breakout')
def api_breakout():
    conn = get_conn()
    try:
        date = request.args.get('date') or get_latest_date(conn)
        market = request.args.get('market', 'all')
        if not date:
            return jsonify({'error': '無資料'}), 404
        rows = get_breakouts_by_date(conn, date, market if market != 'all' else None)
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route('/api/export/breakout')
def export_breakout():
    import csv, io
    conn = get_conn()
    try:
        date = request.args.get('date') or get_latest_date(conn)
        market = request.args.get('market', 'all')
        rows = get_breakouts_by_date(conn, date, market if market != 'all' else None)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['代號', '名稱', '市場', '收盤價', '漲跌%', '成交量', '5日', '10日', '20日', '60日', '120日', '240日'])
        for r in rows:
            writer.writerow([r['stock_id'], r['name'], r['market'], r['close_price'], r['change_pct'], r['volume'],
                           r['break_5'], r['break_10'], r['break_20'], r['break_60'], r['break_120'], r['break_240']])
        resp = app.response_class(output.getvalue(), mimetype='text/csv',
                                   headers={'Content-Disposition': f'attachment;filename=breakout_{date}.csv'})
        return resp
    finally:
        conn.close()


VALID_BREAK_DAYS = {5, 10, 20, 60, 120, 240}


@app.route('/screener')
def screener():
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        results = []
        # Get filter params
        min_volume = request.args.get('min_vol', type=int)
        min_price = request.args.get('min_price', type=float)
        max_price = request.args.get('max_price', type=float)
        min_change = request.args.get('min_change', type=float)
        max_change = request.args.get('max_change', type=float)
        foreign_dir = request.args.get('foreign')  # 'buy' or 'sell'
        sitc_dir = request.args.get('sitc')
        break_days = request.args.get('break_days', type=int)
        market = request.args.get('market', 'all')
        consecutive_foreign = request.args.get('consec_foreign', type=int)

        # Validate break_days against whitelist to prevent SQL injection
        if break_days and break_days not in VALID_BREAK_DAYS:
            break_days = None

        # Build dynamic query
        conditions = ["dp.date = ?"]
        params = [date]

        joins = """
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
            LEFT JOIN breakouts b ON b.stock_id = dp.stock_id AND b.date = dp.date
        """

        if market and market != 'all':
            conditions.append("s.market = ?")
            params.append(market)
        if min_volume:
            conditions.append("dp.volume >= ?")
            params.append(min_volume)
        if min_price:
            conditions.append("dp.close_price >= ?")
            params.append(min_price)
        if max_price:
            conditions.append("dp.close_price <= ?")
            params.append(max_price)
        if min_change is not None:
            conditions.append("dp.change_pct >= ?")
            params.append(min_change)
        if max_change is not None:
            conditions.append("dp.change_pct <= ?")
            params.append(max_change)
        if foreign_dir == 'buy':
            conditions.append("i.foreign_buy > 0")
        elif foreign_dir == 'sell':
            conditions.append("i.foreign_buy < 0")
        if sitc_dir == 'buy':
            conditions.append("i.sitc_buy > 0")
        elif sitc_dir == 'sell':
            conditions.append("i.sitc_buy < 0")
        if break_days:
            conditions.append(f"b.break_{break_days} = 1")

        where = " AND ".join(conditions)

        # Only query if at least one filter is active (besides date)
        has_filter = any([min_volume, min_price, max_price, min_change is not None, max_change is not None,
                         foreign_dir, sitc_dir, break_days, (market and market != 'all'), consecutive_foreign])

        if has_filter:
            sql = f"""
                SELECT dp.stock_id, s.name, s.market, dp.close_price, dp.change_pct, dp.volume,
                       COALESCE(i.foreign_buy, 0) as foreign_buy, COALESCE(i.sitc_buy, 0) as sitc_buy,
                       b.break_5, b.break_10, b.break_20, b.break_60, b.break_120, b.break_240
                {joins}
                WHERE {where}
                ORDER BY dp.volume DESC
                LIMIT 200
            """
            results = conn.execute(sql, params).fetchall()

        return render_template('screener.html', results=results, date=date,
                             has_filter=has_filter,
                             f_min_vol=min_volume, f_min_price=min_price, f_max_price=max_price,
                             f_min_change=min_change, f_max_change=max_change,
                             f_foreign=foreign_dir, f_sitc=sitc_dir,
                             f_break=break_days, f_market=market,
                             f_consec=consecutive_foreign)
    finally:
        conn.close()


@app.route('/api/export/screener')
def export_screener():
    """Export screener results as CSV"""
    import csv, io
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        min_volume = request.args.get('min_vol', type=int)
        min_price = request.args.get('min_price', type=float)
        max_price = request.args.get('max_price', type=float)
        min_change = request.args.get('min_change', type=float)
        max_change = request.args.get('max_change', type=float)
        foreign_dir = request.args.get('foreign')
        sitc_dir = request.args.get('sitc')
        break_days = request.args.get('break_days', type=int)
        market = request.args.get('market', 'all')

        if break_days and break_days not in VALID_BREAK_DAYS:
            break_days = None

        conditions = ["dp.date = ?"]
        params = [date]
        joins = """
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
            LEFT JOIN breakouts b ON b.stock_id = dp.stock_id AND b.date = dp.date
        """
        if market and market != 'all':
            conditions.append("s.market = ?")
            params.append(market)
        if min_volume:
            conditions.append("dp.volume >= ?")
            params.append(min_volume)
        if min_price:
            conditions.append("dp.close_price >= ?")
            params.append(min_price)
        if max_price:
            conditions.append("dp.close_price <= ?")
            params.append(max_price)
        if min_change is not None:
            conditions.append("dp.change_pct >= ?")
            params.append(min_change)
        if max_change is not None:
            conditions.append("dp.change_pct <= ?")
            params.append(max_change)
        if foreign_dir == 'buy':
            conditions.append("i.foreign_buy > 0")
        elif foreign_dir == 'sell':
            conditions.append("i.foreign_buy < 0")
        if sitc_dir == 'buy':
            conditions.append("i.sitc_buy > 0")
        elif sitc_dir == 'sell':
            conditions.append("i.sitc_buy < 0")
        if break_days:
            conditions.append(f"b.break_{break_days} = 1")

        where = " AND ".join(conditions)
        sql = f"""
            SELECT dp.stock_id, s.name, s.market, dp.close_price, dp.change_pct, dp.volume,
                   COALESCE(i.foreign_buy, 0) as foreign_buy, COALESCE(i.sitc_buy, 0) as sitc_buy,
                   b.break_5, b.break_10, b.break_20, b.break_60, b.break_120, b.break_240
            {joins}
            WHERE {where}
            ORDER BY dp.volume DESC
            LIMIT 200
        """
        results = conn.execute(sql, params).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['代號', '名稱', '市場', '收盤價', '漲跌%', '成交量(張)', '外資買賣超', '投信買賣超',
                         '5日突破', '10日突破', '20日突破', '60日突破', '120日突破', '240日突破'])
        for r in results:
            writer.writerow([r['stock_id'], r['name'], r['market'], r['close_price'], r['change_pct'],
                           r['volume'], r['foreign_buy'], r['sitc_buy'],
                           r['break_5'] or 0, r['break_10'] or 0, r['break_20'] or 0,
                           r['break_60'] or 0, r['break_120'] or 0, r['break_240'] or 0])
        resp = app.response_class(output.getvalue(), mimetype='text/csv',
                                   headers={'Content-Disposition': f'attachment;filename=screener_{date}.csv'})
        return resp
    finally:
        conn.close()


@app.route('/sectors')
def sectors():
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        rows = conn.execute("""
            SELECT s.sector, COUNT(*) as stock_count,
                   ROUND(AVG(dp.change_pct), 2) as avg_change,
                   SUM(CASE WHEN dp.change_pct > 0 THEN 1 ELSE 0 END) as up_count,
                   SUM(CASE WHEN dp.change_pct < 0 THEN 1 ELSE 0 END) as dn_count
            FROM stocks s
            JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.date = ?
            WHERE s.sector != '' AND s.sector IS NOT NULL
            GROUP BY s.sector
            ORDER BY avg_change DESC
        """, (date,)).fetchall()
        return render_template('sectors.html', sectors=rows, date=date)
    finally:
        conn.close()


@app.route('/sector/<name>')
def sector_detail(name):
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        rows = conn.execute("""
            SELECT dp.stock_id, s.name, dp.close_price, dp.change_pct, dp.volume,
                   COALESCE(i.foreign_buy, 0) as foreign_buy
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
            WHERE dp.date = ? AND s.sector = ?
            ORDER BY dp.change_pct DESC
        """, (date, name)).fetchall()
        return render_template('sector_detail.html', stocks=rows, sector=name, date=date)
    finally:
        conn.close()


@app.route('/backtest')
def backtest():
    conn = get_conn()
    try:
        break_days = int(request.args.get('break', '20'))
        hold_days = int(request.args.get('hold', '5'))
        start_date = request.args.get('start', '')
        end_date = request.args.get('end', '')

        # Validate break_days against whitelist
        if break_days not in VALID_BREAK_DAYS:
            break_days = 20

        # Default: last 3 months
        if not start_date or not end_date:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=90)
            start_date = start_dt.strftime('%Y-%m-%d')
            end_date = end_dt.strftime('%Y-%m-%d')

        # Get all breakout signals in date range (break_days validated above)
        signals = conn.execute(f"""
            SELECT b.stock_id, b.date as signal_date, b.close_price as entry_price,
                   s.name
            FROM breakouts b
            JOIN stocks s ON s.stock_id = b.stock_id
            WHERE b.date >= ? AND b.date <= ? AND b.break_{break_days} = 1
            ORDER BY b.date
        """, (start_date, end_date)).fetchall()

        results = []
        total_return = 0
        win_count = 0
        total_count = 0

        for sig in signals:
            future = conn.execute("""
                SELECT date, close_price FROM daily_prices
                WHERE stock_id = ? AND date > ?
                ORDER BY date LIMIT 1 OFFSET ?
            """, (sig['stock_id'], sig['signal_date'], hold_days - 1)).fetchone()

            if future:
                exit_price = future['close_price']
                ret = round((exit_price - sig['entry_price']) / sig['entry_price'] * 100, 2)
                total_return += ret
                total_count += 1
                if ret > 0:
                    win_count += 1
                results.append({
                    'stock_id': sig['stock_id'],
                    'name': sig['name'],
                    'signal_date': sig['signal_date'],
                    'entry_price': sig['entry_price'],
                    'exit_date': future['date'],
                    'exit_price': exit_price,
                    'return_pct': ret,
                })

        avg_return = round(total_return / total_count, 2) if total_count else 0
        win_rate = round(win_count / total_count * 100, 1) if total_count else 0

        # Group by month for chart
        monthly = {}
        for r in results:
            month = r['signal_date'][:7]
            if month not in monthly:
                monthly[month] = {'count': 0, 'total_ret': 0, 'wins': 0}
            monthly[month]['count'] += 1
            monthly[month]['total_ret'] += r['return_pct']
            if r['return_pct'] > 0:
                monthly[month]['wins'] += 1

        monthly_data = []
        for m in sorted(monthly.keys()):
            d = monthly[m]
            monthly_data.append({
                'month': m,
                'avg_return': round(d['total_ret'] / d['count'], 2),
                'win_rate': round(d['wins'] / d['count'] * 100, 1),
                'count': d['count'],
            })

        return render_template('backtest.html',
            results=results[-100:],
            total_count=total_count,
            avg_return=avg_return,
            win_rate=win_rate,
            win_count=win_count,
            monthly_data=monthly_data,
            monthly_json=json.dumps(monthly_data),
            break_days=break_days,
            hold_days=hold_days,
            start_date=start_date,
            end_date=end_date)
    finally:
        conn.close()
