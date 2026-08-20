"""首頁導向、健康檢查、全球報價（自 app.py 拆出）"""
import sys
import os
import json
import time
import logging
import threading
import requests as http_requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import load_dotenv, BASE_DIR
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
from webapp.shared import _quotes_cache, _quotes_lock, get_global_quotes

@app.route('/')
def index():
    return redirect_to_breakout()


@app.route('/api/quotes')
def api_quotes():
    data = get_global_quotes()
    return jsonify(data)


@app.route('/api/health')
def api_health():
    """系統健康檢查端點 — 回報 DB 狀態、各資料表最新日期、資料筆數"""
    status = {'status': 'ok', 'checks': {}}
    try:
        conn = get_conn()
        try:
            # DB 連線測試
            conn.execute("SELECT 1").fetchone()
            status['checks']['db'] = 'ok'

            # 各表最新日期與筆數
            # 注意: SQLite 不支援 table/column 名以參數綁定,只能透過白名單 + 嚴格 assert
            HEALTH_CHECK_TABLES = {
                'daily_prices': 'date',
                'breakouts': 'date',
                'institutional': 'date',
                'broker_trades': 'date',
            }
            _ALLOWED_TABLES = set(HEALTH_CHECK_TABLES.keys())
            _ALLOWED_DATE_COLS = {'date'}
            for table, date_col in HEALTH_CHECK_TABLES.items():
                # 白名單嚴格 assert,確保表名/欄名來源可信
                assert table in _ALLOWED_TABLES, f"unsafe table: {table}"
                assert date_col in _ALLOWED_DATE_COLS, f"unsafe column: {date_col}"
                row = conn.execute(f"SELECT MAX({date_col}) as latest, COUNT(*) as cnt FROM {table}").fetchone()
                status['checks'][table] = {
                    'latest_date': row['latest'],
                    'total_rows': row['cnt'],
                }

            # 股票總數
            stock_count = conn.execute("SELECT COUNT(*) as c FROM stocks").fetchone()
            status['checks']['stocks'] = stock_count['c']

        finally:
            conn.close()
    except Exception as e:
        status['status'] = 'error'
        status['checks']['db'] = f'error: {e}'

    # 快取狀態
    with _quotes_lock:
        quotes_age = int(time.time() - _quotes_cache['ts']) if _quotes_cache['ts'] > 0 else -1
    status['checks']['quotes_cache_age_sec'] = quotes_age

    http_code = 200 if status['status'] == 'ok' else 503
    return jsonify(status), http_code


_data_health_cache = {"data": None, "ts": 0}


_data_health_lock = threading.Lock()


_DATA_HEALTH_TTL = 60  # 60 秒快取，避免重壓 DB


def _build_data_health():
    """蒐集資料健康指標：每張表的覆蓋、落後、缺漏、品質問題、檔案狀態。"""
    import pandas as pd

    today_str = datetime.now().strftime('%Y-%m-%d')
    project_dir = BASE_DIR

    # 交易日曆（檔案可能落後實際資料，僅用於缺漏比對）
    cal_path = os.path.join(project_dir, 'data', 'trading_calendar.parquet')
    trading_days = []
    cal_latest = None
    try:
        cal = pd.read_parquet(cal_path)
        cal['date'] = pd.to_datetime(cal['date']).dt.strftime('%Y-%m-%d')
        trading_days = cal['date'].tolist()
        if trading_days:
            cal_latest = trading_days[-1]
    except Exception as e:
        logger.warning(f"data-health: 無法讀取 trading_calendar.parquet: {e}")

    trading_set = set(trading_days)
    # latest_trading_day 與 expected_recent 改在拿到 daily_prices 實際日期後決定
    latest_trading_day = None
    expected_recent = []

    def _trading_lag(table_max_date):
        """從表內最大日期到 latest_trading_day 之間相差幾個交易日。"""
        if not table_max_date or not latest_trading_day:
            return None
        if table_max_date >= latest_trading_day:
            return 0
        # 計算 (table_max_date, latest_trading_day] 之間的交易日數
        lag = 0
        for d in trading_days:
            if d > table_max_date and d <= latest_trading_day:
                lag += 1
        return lag

    def _status_from_lag(lag):
        if lag is None:
            return 'unknown'
        if lag <= 1:
            return 'ok'
        if lag <= 3:
            return 'warn'
        return 'error'

    result = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'today': today_str,
        'latest_trading_day': None,
        'calendar_latest': cal_latest,
        'tables': [],
        'recent_coverage': {'expected_dates': [], 'by_table': {}},
        'gaps': {},
        'quality': [],
        'macro': [],
        'files': [],
        'stock_universe': {},
        'watchlist_count': 0,
    }

    conn = get_conn()
    try:
        # 先以 daily_prices 實際存在的最近 10 個交易日當基準
        recent_dp_dates = [
            r['date'] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_prices ORDER BY date DESC LIMIT 10"
            ).fetchall()
        ]
        if recent_dp_dates:
            latest_trading_day = recent_dp_dates[0]
            expected_recent = recent_dp_dates  # 已是新→舊
        elif trading_days:
            past = [d for d in trading_days if d <= today_str]
            latest_trading_day = past[-1] if past else None
            expected_recent = past[-10:][::-1]
        result['latest_trading_day'] = latest_trading_day
        result['recent_coverage']['expected_dates'] = expected_recent

        # 每張表概覽
        TABLE_DEFS = [
            ('daily_prices', 'date', '日線行情'),
            ('breakouts', 'date', 'N日高點突破'),
            ('institutional', 'date', '法人買賣超'),
            ('broker_trades', 'date', '券商分點進出'),
            ('credit_spread_history', 'date', '信用利差'),
            ('macro_indicators', 'date', '總經指標'),
            ('regime_history', 'date', 'AE 體制偵測'),
        ]

        for tbl, col, label in TABLE_DEFS:
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS cnt, MIN({col}) AS dmin, MAX({col}) AS dmax, "
                    f"COUNT(DISTINCT {col}) AS distinct_dates FROM {tbl}"
                ).fetchone()
                rows = row['cnt'] or 0
                dmin = row['dmin']
                dmax = row['dmax']
                distinct_dates = row['distinct_dates'] or 0
                # 最新一天的筆數
                latest_count = 0
                if dmax:
                    r2 = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {tbl} WHERE {col} = ?", (dmax,)
                    ).fetchone()
                    latest_count = r2['c'] or 0
                lag = _trading_lag(dmax)
                result['tables'].append({
                    'name': tbl,
                    'label': label,
                    'rows': rows,
                    'date_min': dmin,
                    'date_max': dmax,
                    'distinct_dates': distinct_dates,
                    'latest_row_count': latest_count,
                    'lag_trading_days': lag,
                    'status': _status_from_lag(lag),
                })
            except Exception as e:
                result['tables'].append({
                    'name': tbl, 'label': label, 'error': str(e), 'status': 'error',
                })

        # 最近 10 個交易日，每張主表的覆蓋筆數
        # breakouts 不放入熱圖（本來就只記錄突破的個股，列數天然稀疏，不適合用同一閾值）
        COVERAGE_TABLES = ['daily_prices', 'institutional', 'broker_trades']
        GAP_TABLES = ['daily_prices', 'institutional', 'broker_trades', 'breakouts']
        if expected_recent:
            placeholders = ','.join(['?'] * len(expected_recent))
            for tbl in COVERAGE_TABLES:
                try:
                    rows = conn.execute(
                        f"SELECT date, COUNT(DISTINCT stock_id) AS n FROM {tbl} "
                        f"WHERE date IN ({placeholders}) GROUP BY date",
                        expected_recent,
                    ).fetchall()
                    by_date = {r['date']: r['n'] for r in rows}
                    result['recent_coverage']['by_table'][tbl] = [
                        {'date': d, 'count': by_date.get(d, 0)} for d in expected_recent
                    ]
                except Exception as e:
                    result['recent_coverage']['by_table'][tbl] = {'error': str(e)}

        # 缺漏的交易日（與 trading_calendar 比對，限定在表內 [min,max] 範圍內）
        for tbl in GAP_TABLES:
            try:
                rmm = conn.execute(
                    f"SELECT MIN(date) AS dmin, MAX(date) AS dmax FROM {tbl}"
                ).fetchone()
                dmin, dmax = rmm['dmin'], rmm['dmax']
                if not dmin or not dmax or not trading_set:
                    result['gaps'][tbl] = []
                    continue
                expected_in_range = {d for d in trading_days if dmin <= d <= dmax}
                actual = {
                    r['date'] for r in conn.execute(
                        f"SELECT DISTINCT date FROM {tbl} WHERE date BETWEEN ? AND ?",
                        (dmin, dmax),
                    ).fetchall()
                }
                missing = sorted(expected_in_range - actual, reverse=True)
                result['gaps'][tbl] = {
                    'missing_count': len(missing),
                    'missing_recent': missing[:20],
                }
            except Exception as e:
                result['gaps'][tbl] = {'error': str(e)}

        # 品質檢查
        QUALITY_CHECKS = [
            ('daily_prices.null_close',
             "SELECT COUNT(*) FROM daily_prices WHERE close_price IS NULL", 'OK 應為 0'),
            ('daily_prices.zero_or_negative_close',
             "SELECT COUNT(*) FROM daily_prices WHERE close_price <= 0", 'OK 應為 0'),
            ('daily_prices.zero_volume',
             "SELECT COUNT(*) FROM daily_prices WHERE volume = 0 OR volume IS NULL", '停牌或缺資料'),
            ('daily_prices.extreme_change_pct',
             "SELECT COUNT(*) FROM daily_prices dp "
             "WHERE ABS(dp.change_pct) > 11 "
             "AND LENGTH(dp.stock_id) = 4 AND dp.stock_id GLOB '[1-9]*' "
             "AND (SELECT COUNT(*) FROM daily_prices WHERE stock_id=dp.stock_id AND date<=dp.date) > 5",
             '真股票上市第 6 日後仍漲跌超過 ±11%（豁免 ETF/權證/IPO 前 5 日）'),
            ('daily_prices.unadjusted_split_dividend',
             "WITH paired AS ( "
             "  SELECT d1.stock_id, d1.date, d1.adj_close AS c, d1.change_pct, "
             "    (SELECT adj_close FROM daily_prices d2 WHERE d2.stock_id=d1.stock_id "
             "     AND d2.date<d1.date ORDER BY d2.date DESC LIMIT 1) AS pc "
             "  FROM daily_prices d1 "
             "  WHERE LENGTH(d1.stock_id)=4 AND d1.stock_id GLOB '[1-9]*' "
             "    AND d1.adj_close IS NOT NULL "
             ") "
             "SELECT COUNT(*) FROM paired "
             "WHERE pc > 0 AND c > 0 "
             "AND ABS((c/pc - 1)*100 - change_pct) > 5",
             '已用 adj_close 比對；非 0 = 還原失敗或 raw close 仍含未還原跳空'),
            ('daily_prices.adj_close_missing',
             "SELECT COUNT(*) FROM daily_prices WHERE adj_close IS NULL",
             'adj_close 未計算（請執行 backfill_adj_prices.py）'),
            ('daily_prices.high_lt_low',
             "SELECT COUNT(*) FROM daily_prices WHERE high_price < low_price", 'OK 應為 0'),
            ('institutional.zero_total',
             "SELECT COUNT(*) FROM institutional WHERE total_buy = 0 AND foreign_buy = 0 AND sitc_buy = 0 AND dealer_buy = 0",
             '冷門股當日無法人交易（已驗證為真實資料,非異常）'),
            ('daily_prices.zero_volume_real_stock',
             "SELECT COUNT(*) FROM daily_prices WHERE (volume = 0 OR volume IS NULL) "
             "AND LENGTH(stock_id)=4 AND stock_id GLOB '[1-9]*'",
             '真股票零成交量（停牌或缺資料,豁免 ETF/權證）'),
            ('stocks.duplicates',
             "SELECT COUNT(*) - COUNT(DISTINCT stock_id) FROM stocks", 'OK 應為 0'),
            ('orphan.daily_prices_not_in_stocks',
             "SELECT COUNT(DISTINCT dp.stock_id) FROM daily_prices dp "
             "LEFT JOIN stocks s ON dp.stock_id = s.stock_id WHERE s.stock_id IS NULL",
             '行情中存在、但 stocks 名冊查不到的代號'),
            ('orphan.institutional_recent_not_in_stocks',
             "SELECT COUNT(*) FROM (SELECT i.stock_id FROM institutional i "
             "LEFT JOIN stocks s ON i.stock_id = s.stock_id WHERE s.stock_id IS NULL "
             "GROUP BY i.stock_id HAVING MAX(i.date) >= date('now','-6 months'))",
             '近 6 個月仍有法人資料、但 stocks 名冊缺收（疑似新上市未收錄）'),
            ('orphan.institutional_historical_delisted',
             "SELECT COUNT(*) FROM (SELECT i.stock_id FROM institutional i "
             "LEFT JOIN stocks s ON i.stock_id = s.stock_id WHERE s.stock_id IS NULL "
             "GROUP BY i.stock_id HAVING MAX(i.date) < date('now','-6 months'))",
             '已下市超過 6 個月的歷史法人資料（保留正常）'),
        ]
        for name, sql, hint in QUALITY_CHECKS:
            try:
                cnt = conn.execute(sql).fetchone()[0]
                severity = 'ok' if cnt == 0 else ('warn' if cnt < 100 else 'error')
                # 預期非 0 的檢查（zero_volume / extreme_change_pct / zero_total）降一級
                if name in ('daily_prices.zero_volume', 'daily_prices.extreme_change_pct',
                            'institutional.zero_total'):
                    severity = 'ok' if cnt == 0 else ('info' if cnt < 1000 else 'warn')
                # zero_volume 全是 ETF/權證、zero_total 已驗證為冷門股 → 直接降 info
                if name in ('daily_prices.zero_volume', 'institutional.zero_total'):
                    severity = 'info' if cnt > 0 else 'ok'
                # 歷史下市股的 orphan 永遠標 info（保留是正常的）
                if name == 'orphan.institutional_historical_delisted':
                    severity = 'info' if cnt > 0 else 'ok'
                result['quality'].append({
                    'check': name, 'count': int(cnt), 'hint': hint, 'severity': severity,
                })
            except Exception as e:
                result['quality'].append({
                    'check': name, 'error': str(e), 'severity': 'error',
                })

        # macro_indicators 各 series
        try:
            for r in conn.execute(
                "SELECT indicator, MAX(date) AS dmax, COUNT(*) AS cnt "
                "FROM macro_indicators GROUP BY indicator ORDER BY indicator"
            ).fetchall():
                lag = _trading_lag(r['dmax']) if r['dmax'] else None
                result['macro'].append({
                    'indicator': r['indicator'],
                    'latest': r['dmax'],
                    'rows': r['cnt'],
                    'lag_trading_days': lag,
                    'status': _status_from_lag(lag),
                })
        except Exception as e:
            result['macro'] = [{'error': str(e)}]

        # 股票名冊
        try:
            for r in conn.execute(
                "SELECT market, COUNT(*) AS c FROM stocks GROUP BY market"
            ).fetchall():
                result['stock_universe'][r['market']] = r['c']
        except Exception:
            pass

        # 自選股
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()
            result['watchlist_count'] = row['c'] or 0
        except Exception:
            pass

    finally:
        conn.close()

    # 重要檔案的大小與更新時間
    FILES_TO_CHECK = [
        'db/scanner.db',
        'data/institutional_clean.parquet',
        'data/institutional_full.parquet',
        'data/trading_calendar.parquet',
        'data/stocks_index.parquet',
        'data/data_quality_report.txt',
        'data/institutional_summary.txt',
        'backfill_institutional.log',
        'backfill_broker.log',
        'watchdog.log',
    ]
    for rel in FILES_TO_CHECK:
        full = os.path.join(project_dir, rel)
        try:
            if os.path.exists(full):
                st = os.stat(full)
                result['files'].append({
                    'path': rel,
                    'size_kb': round(st.st_size / 1024, 1),
                    'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'exists': True,
                })
            else:
                result['files'].append({'path': rel, 'exists': False})
        except Exception as e:
            result['files'].append({'path': rel, 'error': str(e)})

    return result


@app.route('/api/data-health')
def api_data_health():
    """回傳資料健康儀表板的 JSON。預設 60 秒快取，?force=1 可強制重新計算。"""
    force = request.args.get('force') == '1'
    now = time.time()
    with _data_health_lock:
        if (not force and _data_health_cache['data'] is not None
                and now - _data_health_cache['ts'] < _DATA_HEALTH_TTL):
            data = _data_health_cache['data']
        else:
            data = _build_data_health()
            _data_health_cache['data'] = data
            _data_health_cache['ts'] = now
    return jsonify(data)


@app.route('/data-health')
def data_health_page():
    """資料健康儀表板頁面。刻意不放在側邊欄，僅供直接以 URL 訪問。"""
    return render_template('data_health.html')


def redirect_to_breakout():
    from flask import redirect, url_for
    return redirect(url_for('breakout'))
