"""
volume_alert package — minimal Flask 應用
只提供 /volume-alert 頁面 + /api/volume-alert + /api/volume-alert/trend
"""
import os
import json
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, redirect, url_for

from config import BASE_DIR
from models.database import init_db, get_conn

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates', static_folder='static')


@app.route('/')
def index():
    return redirect(url_for('volume_alert'))


def _load_volume_alert_cache():
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
    return render_template('volume_alert.html', data=payload, updated_at=updated_at)


@app.route('/api/volume-alert')
def api_volume_alert():
    payload, updated_at = _load_volume_alert_cache()
    if payload is None:
        return jsonify({'error': 'no cache yet', 'data': None, 'updated_at': None}), 200
    return jsonify({'data': payload, 'updated_at': updated_at})


@app.route('/api/volume-alert/trend')
def api_volume_alert_trend():
    today_str = datetime.now().strftime('%Y-%m-%d')
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


if __name__ == '__main__':
    init_db()
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, host='127.0.0.1', port=5000)
