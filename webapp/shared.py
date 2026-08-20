"""跨頁面共用的 helper 與快取（自 app.py 拆出）"""
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

try:
    from scanners.regime import get_market_temperature, rolling_retrain, get_model_info
except ImportError:
    get_market_temperature = None
    rolling_retrain = None
    get_model_info = None


GLOBAL_QUOTES_SYMBOLS = [
    ("^TWII",   "台股加權"),
    ("^DJI",    "道瓊"),
    ("^GSPC",   "S&P 500"),
    ("^N225",   "日經225"),
    ("^KS11",   "韓國KOSPI"),
    ("^GDAXI",  "德國DAX"),
    ("BTC-USD", "比特幣"),
    ("ETH-USD", "以太幣"),
    ("GC=F",    "黃金"),
    ("SI=F",    "白銀"),
]


_quotes_cache = {"data": [], "ts": 0}


_quotes_lock = threading.Lock()


def _fetch_single_quote(sym, label):
    """從 Yahoo Finance v8 chart API 抓取單一商品行情"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2d&interval=1d"
        r = http_requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            result = r.json()["chart"]["result"][0]
            meta = result["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
            pct = ((price - prev) / prev * 100) if prev else 0
            return {
                "symbol": sym,
                "label": label,
                "price": round(price, 2),
                "pct": round(pct, 2),
            }
    except Exception:
        pass
    return None


def _fetch_global_quotes():
    """從 Yahoo Finance v8 chart API 並行抓取全球行情"""
    try:
        data = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(_fetch_single_quote, sym, label): sym
                for sym, label in GLOBAL_QUOTES_SYMBOLS
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    data.append(result)
        # Sort by original order
        order = {s[0]: i for i, s in enumerate(GLOBAL_QUOTES_SYMBOLS)}
        data.sort(key=lambda x: order.get(x["symbol"], 999))
        return data
    except Exception as e:
        logger.error(f"Yahoo Finance 全球行情抓取失敗: {e}")
        return []


def get_global_quotes():
    """取得全球行情（60 秒快取，double-check locking 防止競態）"""
    with _quotes_lock:
        now = time.time()
        if now - _quotes_cache["ts"] < 60 and _quotes_cache["data"]:
            return _quotes_cache["data"]
        # fetch inside lock to prevent concurrent duplicate requests
        data = _fetch_global_quotes()
        if data:
            _quotes_cache["data"] = data
            _quotes_cache["ts"] = time.time()
            return data
        # return stale cache on failure
        return _quotes_cache["data"]


def _margin_alert_legacy():
    """（保留備用）舊融資使用率警示頁邏輯，現已不由 route 直接呈現。"""
    conn = get_conn()
    try:
        date = get_latest_date(conn)
        if not date:
            return render_template('margin_alert.html', results=[], date=None,
                                   message='尚無資料，請先執行 python run_daily.py 抓取資料')

        # Fetch per-stock margin data from TWSE
        results = _fetch_margin_stocks(date)
        sort_by = request.args.get('sort', 'use_rate')

        if sort_by == 'margin_change':
            results.sort(key=lambda x: -x.get('margin_change', 0))
        elif sort_by == 'short_balance':
            results.sort(key=lambda x: -x.get('short_balance', 0))
        else:
            results.sort(key=lambda x: -x.get('use_rate', 0))

        return render_template('margin_alert.html', results=results[:100], date=date,
                               sort_by=sort_by, message=None)
    finally:
        conn.close()


def _fetch_margin_stocks(date_str):
    """從 TWSE 抓取個股融資融券資料"""
    cached = _get_report_cache('margin_stocks_' + date_str)
    if cached is not None:
        return cached

    try:
        yyyymmdd = date_str.replace('-', '')
        url = f'https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={yyyymmdd}&selectType=ALL'
        r = http_requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        d = r.json()
        if d.get('stat') != 'OK' or not d.get('data'):
            return []

        conn = get_conn()
        try:
            results = []
            for row in d['data']:
                try:
                    stock_id = str(row[0]).strip()
                    if not stock_id or not stock_id[0].isdigit():
                        continue
                    name_raw = str(row[1]).strip()

                    margin_buy = int(str(row[2]).replace(',', '') or '0')
                    margin_sell = int(str(row[3]).replace(',', '') or '0')
                    margin_cash = int(str(row[4]).replace(',', '') or '0')
                    margin_balance_prev = int(str(row[5]).replace(',', '') or '0')
                    margin_balance = int(str(row[6]).replace(',', '') or '0')
                    margin_limit = int(str(row[7]).replace(',', '') or '0')

                    short_sell = int(str(row[8]).replace(',', '') or '0')
                    short_return = int(str(row[9]).replace(',', '') or '0')
                    short_balance_prev = int(str(row[10]).replace(',', '') or '0')
                    short_balance = int(str(row[11]).replace(',', '') or '0')

                    use_rate = round(margin_balance / margin_limit * 100, 2) if margin_limit > 0 else 0
                    margin_change = margin_balance - margin_balance_prev

                    # Lookup stock name/price from DB
                    info = conn.execute("""
                        SELECT s.name, dp.close_price, dp.change_pct
                        FROM stocks s
                        LEFT JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.date = ?
                        WHERE s.stock_id = ?
                    """, (date_str, stock_id)).fetchone()

                    stock_name = info['name'] if info else name_raw
                    close_price = info['close_price'] if info else None
                    change_pct = info['change_pct'] if info else None

                    results.append({
                        'stock_id': stock_id,
                        'name': stock_name,
                        'close_price': close_price,
                        'change_pct': change_pct,
                        'margin_balance': margin_balance,
                        'margin_change': margin_change,
                        'use_rate': use_rate,
                        'margin_limit': margin_limit,
                        'short_balance': short_balance,
                        'short_change': short_balance - short_balance_prev,
                    })
                except (ValueError, IndexError):
                    continue

            _set_report_cache('margin_stocks_' + date_str, results)
            return results
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"個股融資融券資料抓取失敗: {e}")
        return []


def populate_sectors():
    """Fetch and update sector info for all stocks (idempotent)"""
    try:
        resp = http_requests.get('https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockInfo'},
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        data = resp.json().get('data', [])
        conn = get_conn()
        try:
            updated = 0
            for row in data:
                stock_id = row.get('stock_id', '')
                sector = row.get('industry_category', '')
                if stock_id and sector:
                    conn.execute("UPDATE stocks SET sector = ? WHERE stock_id = ?", (sector, stock_id))
                    updated += 1
            conn.commit()
            logger.info(f"產業分類更新完成: {updated} 筆")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"產業分類更新失敗: {e}")


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    # 綁定 0.0.0.0 = 監聽所有網路介面，
    # 可同時由 localhost / 內網 IP / Tailscale IP 存取。
    # 可用環境變數 HOST / PORT 覆寫。
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
