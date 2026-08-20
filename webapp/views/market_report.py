"""大盤籌碼與盤前盤後綜合報告（自 app.py 拆出）"""
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
from webapp.shared import get_global_quotes

def fetch_margin_trading_summary(date_str):
    """從 TWSE 抓取信用交易增減（融資融券彙總）"""
    try:
        yyyymmdd = date_str.replace('-', '')
        url = f'https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={yyyymmdd}&selectType=MS'
        r = http_requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        d = r.json()
        if d.get('stat') != 'OK' or not d.get('tables'):
            return None
        # tables[0] is margin purchase/short sale summary
        table = d['tables'][0]
        rows = table.get('data', [])
        result = {}
        for row in rows:
            item = row[0].strip() if row else ''
            if '融資' in item and '融券' not in item:
                # 融資(張): [項目, 買進, 賣出, 現金償還, 前日餘額, 今日餘額, ...]
                try:
                    prev_bal = int(str(row[4]).replace(',', ''))
                    today_bal = int(str(row[5]).replace(',', ''))
                    result['margin_buy_prev'] = prev_bal
                    result['margin_buy_today'] = today_bal
                    result['margin_buy_change'] = today_bal - prev_bal
                except (ValueError, IndexError):
                    pass
            elif '融券' in item:
                try:
                    prev_bal = int(str(row[4]).replace(',', ''))
                    today_bal = int(str(row[5]).replace(',', ''))
                    result['short_sell_prev'] = prev_bal
                    result['short_sell_today'] = today_bal
                    result['short_sell_change'] = today_bal - prev_bal
                except (ValueError, IndexError):
                    pass

        # Also try tables for 融資金額
        if len(d['tables']) > 1:
            table2 = d['tables'][1]
            rows2 = table2.get('data', [])
            for row in rows2:
                item = row[0].strip() if row else ''
                if '融資' in item and '融券' not in item:
                    try:
                        prev_bal = int(str(row[4]).replace(',', ''))
                        today_bal = int(str(row[5]).replace(',', ''))
                        # 金額單位：仟元 -> 億
                        result['margin_amount_prev'] = prev_bal
                        result['margin_amount_today'] = today_bal
                        result['margin_amount_change'] = today_bal - prev_bal
                    except (ValueError, IndexError):
                        pass
        return result if result else None
    except Exception as e:
        logger.error(f"信用交易資料抓取失敗: {e}")
        return None


def fetch_institutional_detail(date_str):
    """從 TWSE 抓取三大法人買進/賣出/淨額明細"""
    try:
        yyyymmdd = date_str.replace('-', '')
        url = f'https://www.twse.com.tw/fund/BFI82U?response=json&dayDate={yyyymmdd}&type=day'
        r = http_requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        d = r.json()
        if d.get('stat') != 'OK' or not d.get('data'):
            return None
        result = []
        for row in d['data']:
            name = row[0].strip()
            try:
                buy = int(str(row[1]).replace(',', ''))
                sell = int(str(row[2]).replace(',', ''))
                net = int(str(row[3]).replace(',', ''))
            except (ValueError, IndexError):
                buy, sell, net = 0, 0, 0
            result.append({'name': name, 'buy': buy, 'sell': sell, 'net': net})
        return result
    except Exception as e:
        logger.error(f"三大法人買賣超明細抓取失敗: {e}")
        return None


def fetch_institutional_detail_prev(date_str):
    """嘗試抓前一個交易日的三大法人資料（用日期往回推最多7天）"""
    from datetime import datetime as dt
    base = dt.strptime(date_str, '%Y-%m-%d')
    for i in range(1, 8):
        prev = base - timedelta(days=i)
        prev_str = prev.strftime('%Y-%m-%d')
        data = fetch_institutional_detail(prev_str)
        if data:
            return data
    return None


def fetch_night_session_spread():
    """從 FinMind 抓取外資夜盤台指期資料，計算夜盤價差"""
    try:
        end = datetime.now()
        start = end - timedelta(days=10)
        rows = _finmind_get('TaiwanFuturesDaily', 'TX',
                            start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
        if not rows:
            return None

        # Find the latest after_market (night session) and day session
        # Only use near-month contracts (no spread contracts like 202604/202605)
        day_sessions = {}
        night_sessions = {}
        for row in rows:
            d = row['date']
            contract = str(row.get('contract_date', ''))
            # Skip spread contracts (contain '/')
            if '/' in contract:
                continue
            session = row.get('trading_session', '')
            close = float(row.get('close', 0) or 0)
            volume = int(row.get('volume', 0) or 0)
            if close <= 0:
                continue
            # Pick the contract with highest volume (near-month)
            if session == 'after_market':
                if d not in night_sessions or volume > night_sessions[d][1]:
                    night_sessions[d] = (close, volume)
            elif session in ('position', ''):
                if d not in day_sessions or volume > day_sessions[d][1]:
                    day_sessions[d] = (close, volume)

        if not night_sessions:
            return None

        # Get latest night session date
        latest_night_date = max(night_sessions.keys())
        night_close = night_sessions[latest_night_date][0]

        # Day session close (same date or most recent before)
        day_entry = day_sessions.get(latest_night_date)
        if not day_entry:
            for d in sorted(day_sessions.keys(), reverse=True):
                if d <= latest_night_date:
                    day_entry = day_sessions[d]
                    break

        if not day_entry:
            return None

        day_close = day_entry[0]
        spread = night_close - day_close
        pct = (spread / day_close * 100) if day_close else 0

        return {
            'date': latest_night_date,
            'day_close': day_close,
            'night_close': night_close,
            'spread': spread,
            'pct': round(pct, 2),
        }
    except Exception as e:
        logger.error(f"外資夜盤資料抓取失敗: {e}")
        return None


_report_cache = {}


_report_cache_lock = threading.Lock()


_REPORT_CACHE_TTL = 300  # 5 minutes


def _get_report_cache(key):
    """Get cached value if not expired."""
    with _report_cache_lock:
        entry = _report_cache.get(key)
        if entry and (time.time() - entry['ts']) < _REPORT_CACHE_TTL:
            return entry['data']
    return None


def _set_report_cache(key, data):
    """Set cache value with current timestamp."""
    with _report_cache_lock:
        _report_cache[key] = {'data': data, 'ts': time.time()}


def _fetch_tsm_adr():
    """Fetch TSM ADR quote from Yahoo Finance."""
    try:
        r = http_requests.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/TSM',
            params={'interval': '1d', 'range': '2d'},
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        d = r.json()
        res = d.get('chart', {}).get('result', [])
        if res:
            meta = res[0].get('meta', {})
            price = meta.get('regularMarketPrice', 0)
            prev = meta.get('chartPreviousClose', 0) or meta.get('previousClose', 0)
            chg = price - prev if prev else 0
            pct = (chg / prev * 100) if prev else 0
            return {'price': round(price, 2), 'change': round(chg, 2), 'pct': round(pct, 2)}
    except Exception as e:
        logger.warning(f"TSM ADR 報價抓取失敗: {e}")
    return None


@app.route('/report')
def report():
    conn = get_conn()
    try:
        latest = get_latest_date(conn)
        if not latest:
            return render_template('report.html', date=None,
                                   quotes_json='[]', twse_inst=None, tpex_inst=None,
                                   foreign_trend_json='[]', sitc_trend_json='[]',
                                   futures_json='[]', pc_json='[]',
                                   inst_detail_json='[]', inst_detail_prev_json='[]',
                                   margin_summary=None, night_session=None,
                                   message='尚無資料，請先執行 python run_daily.py 抓取資料')

        # DB queries (fast, no caching needed)
        # 法人資料可能比收盤價晚一天，用 institutional 自己的最新日期
        inst_latest_row = conn.execute("SELECT MAX(date) as d FROM institutional").fetchone()
        inst_latest = inst_latest_row['d'] if inst_latest_row and inst_latest_row['d'] else latest

        # 2. Institutional aggregates for TWSE (latest institutional date)
        twse_inst = conn.execute("""
            SELECT SUM(i.foreign_buy) as foreign_net,
                   SUM(i.sitc_buy) as sitc_net,
                   SUM(i.dealer_buy) as dealer_net
            FROM institutional i
            JOIN stocks s ON s.stock_id = i.stock_id
            WHERE i.date = ? AND s.market = 'twse'
        """, (inst_latest,)).fetchone()

        # 3. Same for TPEx
        tpex_inst = conn.execute("""
            SELECT SUM(i.foreign_buy) as foreign_net,
                   SUM(i.sitc_buy) as sitc_net,
                   SUM(i.dealer_buy) as dealer_net
            FROM institutional i
            JOIN stocks s ON s.stock_id = i.stock_id
            WHERE i.date = ? AND s.market = 'tpex'
        """, (inst_latest,)).fetchone()

        # 4. Foreign buy daily trend (20 days)
        foreign_trend = conn.execute("""
            SELECT i.date, SUM(i.foreign_buy) as net
            FROM institutional i
            GROUP BY i.date ORDER BY i.date DESC LIMIT 20
        """).fetchall()
        foreign_trend = [{'date': r['date'], 'net': r['net']} for r in reversed(foreign_trend)]

        # 5. SITC buy daily trend (20 days)
        sitc_trend = conn.execute("""
            SELECT i.date, SUM(i.sitc_buy) as net
            FROM institutional i
            GROUP BY i.date ORDER BY i.date DESC LIMIT 20
        """).fetchall()
        sitc_trend = [{'date': r['date'], 'net': r['net']} for r in reversed(sitc_trend)]

        # 8. Limit up/down anomalies
        limit_up_foreign_sell = conn.execute("""
            SELECT dp.stock_id, s.name, dp.close_price, dp.change_pct, dp.volume,
                   i.foreign_buy,
                   (SELECT SUM(i2.foreign_buy) FROM institutional i2
                    WHERE i2.stock_id = dp.stock_id AND i2.date >= date(?, '-5 days')
                   ) as foreign_5d
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
            WHERE dp.date = ? AND dp.change_pct >= 9.5 AND i.foreign_buy < 0
            ORDER BY i.foreign_buy ASC
            LIMIT 15
        """, (inst_latest, inst_latest)).fetchall()

        limit_dn_foreign_buy = conn.execute("""
            SELECT dp.stock_id, s.name, dp.close_price, dp.change_pct, dp.volume,
                   i.foreign_buy,
                   (SELECT SUM(i2.foreign_buy) FROM institutional i2
                    WHERE i2.stock_id = dp.stock_id AND i2.date >= date(?, '-5 days')
                   ) as foreign_5d
            FROM daily_prices dp
            JOIN stocks s ON s.stock_id = dp.stock_id
            LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
            WHERE dp.date = ? AND dp.change_pct <= -9.5 AND i.foreign_buy > 0
            ORDER BY i.foreign_buy DESC
            LIMIT 15
        """, (inst_latest, inst_latest)).fetchall()

        # Parallel fetch of all external API data with caching
        external_results = {}
        tasks = {
            'quotes': lambda: get_global_quotes(),
            'futures': lambda: fetch_futures_oi(days=20),
            'pc': lambda: fetch_put_call_ratio(days=20),
            'margin_summary': lambda: fetch_margin_trading_summary(inst_latest),
            'inst_detail': lambda: fetch_institutional_detail(inst_latest),
            'inst_detail_prev': lambda: fetch_institutional_detail_prev(inst_latest),
            'night_session': lambda: fetch_night_session_spread(),
            'tsm_adr': lambda: _fetch_tsm_adr(),
        }

        # Check cache first, collect tasks that need fetching
        tasks_to_fetch = {}
        for key, fn in tasks.items():
            cached = _get_report_cache(key)
            if cached is not None:
                external_results[key] = cached
            else:
                tasks_to_fetch[key] = fn

        # Fetch missing data in parallel
        if tasks_to_fetch:
            with ThreadPoolExecutor(max_workers=6) as executor:
                future_map = {executor.submit(fn): key for key, fn in tasks_to_fetch.items()}
                for future in as_completed(future_map):
                    key = future_map[future]
                    try:
                        result = future.result(timeout=30)
                        external_results[key] = result
                        _set_report_cache(key, result)
                    except Exception as e:
                        logger.warning(f"Report parallel fetch failed for {key}: {e}")
                        external_results[key] = None

        quotes = external_results.get('quotes', [])
        futures_data = external_results.get('futures', [])
        pc_data = external_results.get('pc', [])
        margin_summary = external_results.get('margin_summary')
        inst_detail = external_results.get('inst_detail')
        inst_detail_prev = external_results.get('inst_detail_prev')
        night_session = external_results.get('night_session')
        tsm_adr = external_results.get('tsm_adr')

        return render_template('report.html',
                               date=latest,
                               quotes_json=json.dumps(quotes or []),
                               twse_inst={
                                   'foreign_net': twse_inst['foreign_net'] or 0,
                                   'sitc_net': twse_inst['sitc_net'] or 0,
                                   'dealer_net': twse_inst['dealer_net'] or 0,
                               } if twse_inst else {'foreign_net': 0, 'sitc_net': 0, 'dealer_net': 0},
                               tpex_inst={
                                   'foreign_net': tpex_inst['foreign_net'] or 0,
                                   'sitc_net': tpex_inst['sitc_net'] or 0,
                                   'dealer_net': tpex_inst['dealer_net'] or 0,
                               } if tpex_inst else {'foreign_net': 0, 'sitc_net': 0, 'dealer_net': 0},
                               foreign_trend_json=json.dumps(foreign_trend),
                               sitc_trend_json=json.dumps(sitc_trend),
                               futures_json=json.dumps(futures_data or []),
                               pc_json=json.dumps(pc_data or []),
                               limit_up_sell=limit_up_foreign_sell,
                               limit_dn_buy=limit_dn_foreign_buy,
                               tsm_adr=tsm_adr,
                               margin_summary=margin_summary,
                               inst_detail_json=json.dumps(inst_detail or []),
                               inst_detail_prev_json=json.dumps(inst_detail_prev or []),
                               night_session=night_session,
                               message=None)
    finally:
        conn.close()


@app.route('/market')
def market():
    futures_data = fetch_futures_oi(days=60)
    retail_data = fetch_retail_ratio(days=60)
    pc_data = fetch_put_call_ratio(days=60)

    # Technical indicators for TAIEX
    indicators = {}
    try:
        ohlc = fetch_taiex_ohlc(120)
        if ohlc:
            indicators = calc_technical_indicators(ohlc)
    except Exception as e:
        logger.warning(f"TAIEX technical indicators failed: {e}")

    return render_template('market.html',
                           futures_json=json.dumps(futures_data),
                           retail_json=json.dumps(retail_data),
                           pc_json=json.dumps(pc_data),
                           indicators_json=json.dumps(indicators))


def fetch_taiex_ohlc(days=120):
    """Fetch TAIEX OHLC from Yahoo Finance"""
    cached = _get_report_cache(f'taiex_ohlc_{days}')
    if cached is not None:
        return cached
    try:
        resp = http_requests.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII',
            params={'range': f'{days}d', 'interval': '1d'},
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        data = resp.json()['chart']['result'][0]
        timestamps = data['timestamp']
        quotes = data['indicators']['quote'][0]
        result = []
        for i, ts in enumerate(timestamps):
            o = quotes['open'][i]
            h = quotes['high'][i]
            l = quotes['low'][i]
            c = quotes['close'][i]
            v = quotes['volume'][i]
            if c is None:
                continue
            d = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            result.append({
                'date': d,
                'open': o or c,
                'high': h or c,
                'low': l or c,
                'close': c,
                'volume': v or 0,
            })
        _set_report_cache(f'taiex_ohlc_{days}', result)
        return result
    except Exception as e:
        logger.error(f"TAIEX OHLC 抓取失敗: {e}")
        return []


def _ema(data, period):
    """Calculate EMA"""
    result = []
    multiplier = 2 / (period + 1)
    ema = None
    for val in data:
        if val is None:
            result.append(None)
            continue
        if ema is None:
            ema = val
        else:
            ema = (val - ema) * multiplier + ema
        result.append(round(ema, 2))
    return result


def calc_technical_indicators(ohlc_data):
    """Calculate KD, MACD, Bollinger Bands from OHLC data"""
    closes = [d['close'] for d in ohlc_data]
    highs = [d['high'] for d in ohlc_data]
    lows = [d['low'] for d in ohlc_data]
    dates = [d['date'] for d in ohlc_data]

    # KD (9-day stochastic)
    k_values = []
    d_values = []
    prev_k = 50
    prev_d = 50
    for i in range(len(closes)):
        if i < 8:
            k_values.append(None)
            d_values.append(None)
            continue
        high_9 = max(highs[i-8:i+1])
        low_9 = min(lows[i-8:i+1])
        if high_9 == low_9:
            rsv = 50
        else:
            rsv = (closes[i] - low_9) / (high_9 - low_9) * 100
        k = prev_k * 2/3 + rsv * 1/3
        d = prev_d * 2/3 + k * 1/3
        k_values.append(round(k, 2))
        d_values.append(round(d, 2))
        prev_k = k
        prev_d = d

    # MACD (12, 26, 9)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [round(a - b, 2) if a and b else None for a, b in zip(ema12, ema26)]
    dif_clean = [v for v in dif if v is not None]
    macd_signal = _ema(dif_clean, 9)
    # Pad macd_signal to match length
    pad = len(dif) - len(macd_signal)
    macd_signal = [None] * pad + macd_signal
    histogram = [round(d - s, 2) if d is not None and s is not None else None
                 for d, s in zip(dif, macd_signal)]

    # Bollinger Bands (20-day, 2 std)
    bb_mid = []
    bb_upper = []
    bb_lower = []
    for i in range(len(closes)):
        if i < 19:
            bb_mid.append(None)
            bb_upper.append(None)
            bb_lower.append(None)
            continue
        window = closes[i-19:i+1]
        mean = sum(window) / 20
        std = (sum((x - mean) ** 2 for x in window) / 20) ** 0.5
        bb_mid.append(round(mean, 2))
        bb_upper.append(round(mean + 2 * std, 2))
        bb_lower.append(round(mean - 2 * std, 2))

    return {
        'dates': dates,
        'closes': closes,
        'k': k_values,
        'd': d_values,
        'dif': dif,
        'macd_signal': macd_signal,
        'histogram': histogram,
        'bb_mid': bb_mid,
        'bb_upper': bb_upper,
        'bb_lower': bb_lower,
    }


@app.route('/api/market')
def api_market():
    futures_data = fetch_futures_oi(days=60)
    retail_data = fetch_retail_ratio(days=60)
    pc_data = fetch_put_call_ratio(days=60)
    return jsonify({
        'futures_oi': futures_data,
        'retail': retail_data,
        'put_call_ratio': pc_data,
    })


@app.route('/api/market-indicators')
def api_market_indicators():
    """Return TAIEX technical indicators as JSON"""
    try:
        ohlc = fetch_taiex_ohlc(120)
        if not ohlc:
            return jsonify({'error': 'No TAIEX data'}), 404
        indicators = calc_technical_indicators(ohlc)
        return jsonify(indicators)
    except Exception as e:
        logger.error(f"Market indicators API error: {e}")
        return jsonify({'error': str(e)}), 500
