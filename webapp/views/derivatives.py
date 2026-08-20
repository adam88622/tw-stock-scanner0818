"""衍生品與盤中：選擇權支撐壓力、期現價差、電金強弱、爆量預估（自 app.py 拆出）"""
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

@app.route('/futures-basis')
def futures_basis():
    return render_template('futures_basis.html')


@app.route('/api/futures-basis')
def api_futures_basis():
    from scanners.futures_basis import compute_futures_basis
    try:
        result = compute_futures_basis()
        return jsonify(result)
    except Exception as e:
        logger.error(f"futures-basis error: {e}")
        return jsonify({'ok': False, 'error': str(e), 'rows': [],
                        'stats': {}, 'quote_status': {}}), 500


@app.route('/te-tf-strength')
def te_tf_strength_page():
    return render_template('te_tf_strength.html')


@app.route('/api/te-tf-strength')
def api_te_tf_strength():
    from scanners.te_tf_strength import build_response
    try:
        smooth = request.args.get('smooth', default=0, type=int)
        return jsonify(build_response(smooth=smooth))
    except Exception as e:
        logger.error(f"te-tf-strength error: {e}")
        return jsonify({'ok': False, 'error': str(e), 'now': None, 'series': [], 'quote_status': {}}), 500


@app.route('/option-sr')
def option_sr():
    return render_template('option_sr.html')


@app.route('/api/option-sr')
def api_option_sr():
    from scanners.option_sr import compute_option_sr
    try:
        date = request.args.get('date')
        contract = request.args.get('contract')
        result = compute_option_sr(date, contract)
        return jsonify(result)
    except Exception as e:
        logger.error(f"option-sr error: {e}")
        return jsonify({'ok': False, 'error': str(e), 'rows': [],
                        'available_dates': [], 'available_contracts': []}), 500


def _load_volume_alert_cache():
    """讀 volume_anomaly_cache 單 row，回傳 (payload_dict, updated_at) 或 (None, None)"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT payload, updated_at FROM volume_anomaly_cache WHERE id = 1"
        ).fetchone()
        if not row:
            return None, None
        return json.loads(row['payload']), row['updated_at']
    except Exception as e:
        logger.error(f"volume_anomaly_cache 讀取失敗: {e}")
        return None, None
    finally:
        conn.close()


@app.route('/volume-alert')
def volume_alert():
    payload, updated_at = _load_volume_alert_cache()
    return render_template(
        'volume_alert.html',
        data=payload,
        updated_at=updated_at,
    )


@app.route('/api/volume-alert')
def api_volume_alert():
    payload, updated_at = _load_volume_alert_cache()
    if payload is None:
        return jsonify({'error': 'no cache yet', 'data': None, 'updated_at': None}), 200
    return jsonify({'data': payload, 'updated_at': updated_at})


@app.route('/api/volume-alert/trend')
def api_volume_alert_trend():
    """回傳今日 taiex_trend 全部 rows（依時間排序）"""
    from datetime import datetime as _dt
    today_str = _dt.now().strftime('%Y-%m-%d')
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT snapshot_ts, minute_idx, rvol_forecast, forecast_eod_value,
                   level, ci_low, ci_high
            FROM taiex_trend
            WHERE snapshot_ts >= ?
            ORDER BY snapshot_ts ASC
        """, (today_str + ' 00:00:00',)).fetchall()
        data = [{
            'minute_idx': r['minute_idx'],
            'rvol': r['rvol_forecast'],
            'level': r['level'],
            'eod': r['forecast_eod_value'],
            'ci_low': r['ci_low'],
            'ci_high': r['ci_high'],
        } for r in rows]
        return jsonify({'data': data})
    except Exception as e:
        logger.error(f"taiex_trend 讀取失敗: {e}")
        return jsonify({'data': [], 'error': str(e)}), 200
    finally:
        conn.close()
