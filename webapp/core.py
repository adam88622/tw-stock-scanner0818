"""Flask app 物件、驗證、限流、錯誤頁與全域 context（自 app.py 拆出）"""
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

"""
Flask 主程式 — 台股掃描器網站
"""


logger = logging.getLogger(__name__)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


load_dotenv()


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__,
            template_folder=os.path.join(_ROOT, 'templates'),
            static_folder=os.path.join(_ROOT, 'static'))


auth = HTTPBasicAuth()


_SCANNER_USER = os.environ.get('SCANNER_USER', '')


_SCANNER_PASS = os.environ.get('SCANNER_PASS', '')


@auth.verify_password
def _verify_password(username, password):
    # 未設定帳密時視為未啟用,直接放行(避免本機開發誤鎖死)
    if not _SCANNER_USER or not _SCANNER_PASS:
        return 'guest'
    if username == _SCANNER_USER and password == _SCANNER_PASS:
        return username
    return None


limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["120 per minute"],
)


_AUTH_EXEMPT_PREFIXES = ('/static/',)


_AUTH_EXEMPT_PATHS = {'/api/health'}


@app.before_request
def _global_auth_guard():
    # 未設定帳密時不啟用 (本機開發友善)
    if not _SCANNER_USER or not _SCANNER_PASS:
        return None
    path = request.path or ''
    if path in _AUTH_EXEMPT_PATHS:
        return None
    for prefix in _AUTH_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return None
    # 利用 flask_httpauth 的 login_required 機制驗證 (回傳 None 表示通過)
    return auth.login_required(lambda: None)()


init_db()


_latest_date_cache = {'value': None, 'ts': 0}


_latest_date_lock = threading.Lock()


@app.context_processor
def inject_global():
    """每個頁面都注入今天日期和最後更新時間"""
    now = time.time()
    with _latest_date_lock:
        if now - _latest_date_cache['ts'] < 60 and _latest_date_cache['value'] is not None:
            latest = _latest_date_cache['value']
        else:
            try:
                conn = get_conn()
                try:
                    latest = get_latest_date(conn)
                    _latest_date_cache['value'] = latest
                    _latest_date_cache['ts'] = now
                finally:
                    conn.close()
            except Exception:
                latest = _latest_date_cache['value'] or '更新中'
    return {
        'today': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'last_update': latest or '尚未更新',
    }


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404, message='頁面不存在'), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 錯誤: {e}")
    return render_template('error.html', code=500,
                           message='伺服器暫時無法處理請求，資料庫可能忙碌中，請稍後重試'), 500


@app.errorhandler(sqlite3.OperationalError)
def db_error(e):
    logger.error(f"DB 錯誤: {e}")
    return render_template('error.html', code=503,
                           message='資料庫忙碌中（背景正在更新資料），請稍後重試'), 503
