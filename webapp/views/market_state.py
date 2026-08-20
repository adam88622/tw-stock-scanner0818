"""市場狀態：體溫、廣度、持股水位、信用利差、去槓桿、融資（自 app.py 拆出）"""
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
from webapp.shared import get_market_temperature, get_model_info, rolling_retrain

def _calc_regime_temperatures(errors):
    """temperature = percentile rank × 100(相對整段歷史窗的位置)。
    比線性 (error/tau)*50 公式更穩,不會卡 100°。
    回傳 list[float],對齊 errors 順序。"""
    if not errors:
        return []
    n = len(errors)
    return [round(sum(1 for x in errors if x <= e) / n * 100, 1) for e in errors]


@app.route('/regime')
def regime():
    model_info = get_model_info() if get_model_info else {}
    source = request.args.get('source', 'auto')  # auto / live / db

    # 優先從 DB 讀（快），DB 沒資料或指定 live 才即時計算
    if source != 'live':
        conn = get_conn()
        try:
            rows = get_regime_history(conn, limit=120)
            if rows:
                latest = rows[0]
                tau = latest['tau']
                current_error = latest['recon_error']
                regime_val = latest['regime']
                # ASC 順序的 errors,給 percentile rank
                errors_asc = [r['recon_error'] for r in reversed(rows)]
                temps_asc = _calc_regime_temperatures(errors_asc)
                temperature = temps_asc[-1] if temps_asc else 0.0
                history = [{'date': r['date'], 'error': r['recon_error'],
                            'regime': r['regime'], 'temperature': t}
                           for r, t in zip(reversed(rows), temps_asc)]
                return render_template('regime.html',
                                       temperature=temperature,
                                       current_error=current_error,
                                       tau=tau,
                                       regime=regime_val,
                                       latest_date=latest['date'],
                                       history=history,
                                       history_json=json.dumps(history),
                                       model_info=model_info,
                                       data_source='db')
        except Exception:
            pass
        finally:
            conn.close()

    # DB 沒資料，走即時計算
    try:
        result = get_market_temperature(lookback_days=120)
        # 統一用 percentile rank 重算 temperature(覆蓋 live 的線性公式)
        hist = result.get('history') or []
        errors_asc = [h.get('error', 0) for h in hist]
        temps_asc = _calc_regime_temperatures(errors_asc)
        for h, t in zip(hist, temps_asc):
            h['temperature'] = t
        latest_temp = temps_asc[-1] if temps_asc else result['temperature']
        return render_template('regime.html',
                               temperature=latest_temp,
                               current_error=result['current_error'],
                               tau=result['tau'],
                               regime=result['regime'],
                               latest_date=result['latest_date'],
                               history=hist,
                               history_json=json.dumps(hist),
                               model_info=model_info,
                               data_source='live')
    except Exception as e:
        logger.error(f"Regime error: {e}")
        return render_template('regime.html',
                               temperature=0, current_error=0, tau=0,
                               regime='unknown', latest_date=None,
                               history=[], history_json='[]',
                               model_info=model_info,
                               data_source='error',
                               error=str(e))


@app.route('/api/regime')
def api_regime():
    try:
        result = get_market_temperature(lookback_days=60)
        if get_model_info:
            result['model_info'] = get_model_info()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/regime/retrain', methods=['POST'])
def api_regime_retrain():
    if rolling_retrain is None:
        return jsonify({'error': 'Regime module not available'}), 500
    try:
        window = int(request.args.get('window_years', 2))
        result = rolling_retrain(window_years=window)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Retrain error: {e}")
        return jsonify({'error': str(e)}), 500


_macro_refresh_lock = threading.Lock()


_macro_last_refresh = 0.0


MACRO_STALE_DAYS = 2          # 指標超過 N 天沒更新視為 stale


MACRO_REFRESH_THROTTLE = 3600  # 同一 process 內至少間隔 N 秒才再抓一次


def _macro_is_stale(conn):
    row = conn.execute(
        "SELECT MAX(date) as d FROM macro_indicators "
        "WHERE indicator IN ('T10Y3M','CP_SPREAD','DOLLAR','COR3M','MOVE')"
    ).fetchone()
    if not row or not row['d']:
        return True
    try:
        latest = datetime.strptime(row['d'], '%Y-%m-%d').date()
    except Exception:
        return True
    return (datetime.now().date() - latest).days > MACRO_STALE_DAYS


def _credit_is_stale(conn):
    row = conn.execute(
        "SELECT MAX(date) as d FROM credit_spread_history"
    ).fetchone()
    if not row or not row['d']:
        return True
    try:
        latest = datetime.strptime(row['d'], '%Y-%m-%d').date()
    except Exception:
        return True
    return (datetime.now().date() - latest).days > MACRO_STALE_DAYS


def _rolling_refresh_macro(conn):
    """若 DB 過期且未被節流，即時抓 FRED + Yahoo 補齊。失敗不擋頁面。"""
    global _macro_last_refresh
    now = time.time()
    if now - _macro_last_refresh < MACRO_REFRESH_THROTTLE:
        return
    if not _macro_is_stale(conn) and not _credit_is_stale(conn):
        return
    if not _macro_refresh_lock.acquire(blocking=False):
        return  # 已有另一個請求在抓，直接放行
    try:
        _macro_last_refresh = now
        if _macro_is_stale(conn):
            logger.info("[rolling] macro indicators 過期，即時補抓...")
            try:
                from scanners.macro_indicators import update_macro_indicators
                update_macro_indicators(conn)
            except Exception as e:
                logger.warning(f"[rolling] macro 補抓失敗: {e}")
        if _credit_is_stale(conn):
            logger.info("[rolling] credit spread 過期，即時補抓...")
            try:
                from scanners.credit_spread import update_credit_spread_db
                update_credit_spread_db(conn)
            except Exception as e:
                logger.warning(f"[rolling] credit 補抓失敗: {e}")
    finally:
        _macro_refresh_lock.release()


@app.route('/position-vote')
def position_vote():
    from scanners.position_vote import compute_position_vote
    from scanners.indicator_correlation import run_correlation_analysis
    conn = get_conn()
    try:
        _rolling_refresh_macro(conn)
        data = compute_position_vote(conn)
        corr = run_correlation_analysis(conn)
        return render_template('position_vote.html', data=data, corr=corr)
    except Exception as e:
        logger.error(f"Position vote error: {e}")
        return render_template('position_vote.html', data=None, corr=None, error=str(e))
    finally:
        conn.close()


@app.route('/api/position-vote')
def api_position_vote():
    from scanners.position_vote import compute_position_vote
    conn = get_conn()
    try:
        _rolling_refresh_macro(conn)
        return jsonify(compute_position_vote(conn))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/breadth')
def breadth():
    from scanners.breadth import compute_breadth, compute_breadth_history
    conn = get_conn()
    try:
        current = compute_breadth(conn)
        if not current:
            return render_template('breadth.html', current=None, history_json='[]', error='無資料')
        history = compute_breadth_history(conn, limit=60)
        return render_template('breadth.html',
                               current=current,
                               history=history,
                               history_json=json.dumps(history))
    except Exception as e:
        logger.error(f"Breadth error: {e}")
        return render_template('breadth.html', current=None, history_json='[]', error=str(e))
    finally:
        conn.close()


@app.route('/api/breadth')
def api_breadth():
    from scanners.breadth import compute_breadth
    conn = get_conn()
    try:
        result = compute_breadth(conn)
        return jsonify(result) if result else jsonify({'error': '無資料'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


CREDIT_SPREAD_THRESHOLD = 0.3


CREDIT_SPREAD_YELLOW_LOW = 0.28


CREDIT_SPREAD_YELLOW_HIGH = 0.32


def _live_credit_signal(v):
    if v is None:
        return ''
    if v < CREDIT_SPREAD_YELLOW_LOW:
        return 'GREEN'
    if v >= CREDIT_SPREAD_YELLOW_HIGH:
        return 'RED'
    return 'YELLOW'


@app.route('/credit-spread')
def credit_spread():
    from models.database import get_credit_spread_history
    conn = get_conn()
    try:
        raw_rows = get_credit_spread_history(conn, limit=500)
        rows = [
            {
                'date': r['date'],
                'hyg_shy_ratio': r['hyg_shy_ratio'],
                'indicator_value': r['indicator_value'],
                'signal': _live_credit_signal(r['indicator_value']),
                'spy_close': r['spy_close'],
                'trend5d': r['trend5d'],
            }
            for r in raw_rows
        ]

        if not rows:
            # DB empty - try live compute and seed
            try:
                from scanners.credit_spread import update_credit_spread_db
                update_credit_spread_db(conn)
                raw_rows = get_credit_spread_history(conn, limit=500)
                rows = [
                    {
                        'date': r['date'],
                        'hyg_shy_ratio': r['hyg_shy_ratio'],
                        'indicator_value': r['indicator_value'],
                        'signal': _live_credit_signal(r['indicator_value']),
                        'spy_close': r['spy_close'],
                        'trend5d': r['trend5d'],
                    }
                    for r in raw_rows
                ]
            except Exception as e:
                logger.warning(f"Credit spread live seed failed: {e}")
                return render_template('credit_spread.html',
                                       signal='N/A', indicator_value=0, percentile=0,
                                       days_in_signal=0, last_switch='', latest_date='',
                                       history=[], history_json='[]', backtest=None,
                                       threshold=CREDIT_SPREAD_THRESHOLD,
                                       error=f"DB empty. Run daily_check.py first. ({e})")

        # rows are DESC order, reverse for chart
        rows_asc = list(reversed(rows))

        latest = rows[0]
        signal = latest['signal']
        value = latest['indicator_value']
        latest_date = latest['date']

        # Days in current signal
        days = 0
        for r in rows:
            if r['signal'] == signal:
                days += 1
            else:
                break
        last_switch = rows[days - 1]['date'] if days < len(rows) else rows[-1]['date']

        # History for chart (include spy_close + trend)
        history = [{'date': r['date'], 'ratio': r['hyg_shy_ratio'],
                     'value': r['indicator_value'], 'signal': r['signal'],
                     'spy': r['spy_close'] if r['spy_close'] else 0,
                     'trend': r['trend5d'] if r['trend5d'] else 0}
                    for r in rows_asc]

        # Current trend direction
        latest_trend = rows[0]['trend5d'] if rows[0]['trend5d'] else 0

        # 5-day average of indicator value + its signal
        avg5d = sum(r['indicator_value'] for r in rows[:5]) / min(5, len(rows)) if rows else 0
        if avg5d < CREDIT_SPREAD_YELLOW_LOW:
            avg5d_signal = 'GREEN'
        elif avg5d >= CREDIT_SPREAD_YELLOW_HIGH:
            avg5d_signal = 'RED'
        else:
            avg5d_signal = 'YELLOW'

        # Backtest: compute from DB data (simple version)
        backtest = _compute_backtest_from_db(conn)

        # SPY CTA 訊號(由 cta_signal scanner 寫入 DB)
        cta_data = _build_cta_payload(conn)

        return render_template('credit_spread.html',
                               signal=signal,
                               indicator_value=value,
                               percentile=value,
                               days_in_signal=days,
                               last_switch=last_switch,
                               latest_date=latest_date,
                               history=history,
                               history_json=json.dumps(history),
                               backtest=backtest,
                               threshold=CREDIT_SPREAD_THRESHOLD,
                               yellow_low=CREDIT_SPREAD_YELLOW_LOW,
                               yellow_high=CREDIT_SPREAD_YELLOW_HIGH,
                               trend5d=latest_trend,
                               avg5d=avg5d,
                               avg5d_signal=avg5d_signal,
                               cta=cta_data)
    except Exception as e:
        logger.error(f"Credit spread error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('credit_spread.html',
                               signal='N/A', indicator_value=0, percentile=0,
                               days_in_signal=0, last_switch='', latest_date='',
                               history=[], history_json='[]', backtest=None,
                               threshold=CREDIT_SPREAD_THRESHOLD,
                               error=str(e))
    finally:
        conn.close()


def _build_cta_payload(conn):
    """從 cta_signal_history 讀整段歷史,算回測 + 勝率,塞給 template。
    DB 沒資料就回 None,template 會略過 CTA 區塊。"""
    from models.database import get_cta_signal_all
    try:
        rows = get_cta_signal_all(conn)
    except Exception:
        return None
    if not rows or len(rows) < 200:
        return None

    import pandas as pd
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")

    # 用 scanners.cta_signal 的回測邏輯(避免重複)
    from scanners.cta_signal import compute_backtest, compute_trades
    bt = compute_backtest(df)
    tr = compute_trades(df)

    # 整段時間序列(供 chart 疊圖)
    history = []
    for dt, row in df.iterrows():
        history.append({
            "date": dt.strftime("%Y-%m-%d"),
            "spy": float(row["close"]),
            "signal": float(row["signal_raw"]),
            "pos": int(row["raw_pos"]) if pd.notna(row["raw_pos"]) else 0,
        })

    # 最新狀態
    last = df.iloc[-1]
    pos = int(last["raw_pos"]) if pd.notna(last["raw_pos"]) else 0
    action = "BUY" if pos > 0 else ("SELL" if pos < 0 else "HOLD")

    return {
        "action": action,
        "signal": float(last["signal_raw"]),
        "close": float(last["close"]),
        "date": str(df.index[-1].date()),
        "history": history,
        "history_json": json.dumps(history),
        "backtest": bt,
        "trades": tr,
    }


def _compute_backtest_from_db(conn):
    """Quick backtest from DB data."""
    import numpy as np
    import pandas as pd
    rows = conn.execute("""
        SELECT cs.date, cs.signal, cs.indicator_value
        FROM credit_spread_history cs
        ORDER BY cs.date ASC
    """).fetchall()
    if len(rows) < 252:
        return None

    try:
        import yfinance as yf
        spy = yf.download('SPY', start=rows[0]['date'], auto_adjust=True, progress=False)
        if isinstance(spy.columns, pd.MultiIndex):
            spy_close = spy['Close']['SPY']
        else:
            spy_close = spy['Close']
        spy_close.index = spy_close.index.tz_localize(None) if spy_close.index.tz else spy_close.index

        # Build signal series
        sig_df = pd.DataFrame(rows)
        sig_df['date'] = pd.to_datetime(sig_df['date'])
        sig_df = sig_df.set_index('date')

        # Align
        common = spy_close.index.intersection(sig_df.index)
        if len(common) < 100:
            return None

        spy_ret = spy_close.pct_change()
        position = (sig_df.loc[common, 'signal'] == 'GREEN').astype(float)
        tc = 0.5 * 0.01 * 0.01 * position.diff().abs().fillna(0)
        strat_ret = (spy_ret.loc[common] * position - tc).fillna(0)
        strat_eq = (1 + strat_ret).cumprod()
        bh_ret = spy_ret.loc[common].fillna(0)
        bh_eq = (1 + bh_ret).cumprod()

        n_yr = len(strat_ret) / 252
        s_tot = strat_eq.iloc[-1] / strat_eq.iloc[0] - 1
        b_tot = bh_eq.iloc[-1] / bh_eq.iloc[0] - 1

        class BT:
            pass
        bt = BT()
        bt.cagr = (1 + s_tot) ** (1 / n_yr) - 1
        bt.bh_cagr = (1 + b_tot) ** (1 / n_yr) - 1
        bt.vol = strat_ret.std() * np.sqrt(252)
        bt.bh_vol = bh_ret.std() * np.sqrt(252)
        bt.sharpe = strat_ret.mean() * np.sqrt(252) / strat_ret.std() if strat_ret.std() > 0 else 0
        bt.bh_sharpe = bh_ret.mean() * np.sqrt(252) / bh_ret.std() if bh_ret.std() > 0 else 0
        bt.maxdd = float((strat_eq / strat_eq.cummax() - 1).min())
        bt.bh_maxdd = float((bh_eq / bh_eq.cummax() - 1).min())
        bt.calmar = bt.cagr / abs(bt.maxdd) if bt.maxdd != 0 else 0
        bt.bh_calmar = bt.bh_cagr / abs(bt.bh_maxdd) if bt.bh_maxdd != 0 else 0
        bt.tim = float(position.mean())
        return bt
    except Exception as e:
        logger.warning(f"Backtest compute failed: {e}")
        return None


@app.route('/api/credit-spread')
def api_credit_spread():
    from models.database import get_credit_spread_history
    conn = get_conn()
    try:
        rows = get_credit_spread_history(conn, limit=1)
        if not rows:
            return jsonify({'error': 'No data. Run daily_check.py first.'}), 404
        latest = rows[0]
        return jsonify({
            'signal': _live_credit_signal(latest['indicator_value']),
            'indicator_value': latest['indicator_value'],
            'percentile': latest['indicator_value'],
            'hyg_shy_ratio': latest['hyg_shy_ratio'],
            'date': latest['date'],
            'threshold': CREDIT_SPREAD_THRESHOLD,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/deleveraging')
def deleveraging():
    """台股去槓桿壓力儀表板 — 優先用即時管線,失敗則回退 2026-07-14 靜態快照"""
    ind = None
    # Phase B: 即時管線 (scanners/deleveraging.py),尚未建置時自動回退快照
    try:
        from scanners.deleveraging import build_indicators
        ind = build_indicators()
    except Exception as e:
        logger.warning(f'deleveraging 即時管線失敗,回退快照: {e}')
    if not ind:
        snap = os.path.join(BASE_DIR,
                            'data', 'deleveraging_snapshot.json')
        try:
            with open(snap, encoding='utf-8') as f:
                ind = json.load(f)
        except Exception as e:
            logger.error(f'deleveraging 快照載入失敗: {e}')
            ind = None
    return render_template('deleveraging.html',
                           ind_json=json.dumps(ind, ensure_ascii=False) if ind else 'null')


@app.route('/margin-warning')
def margin_warning():
    """融資預警訊號白話報告 — 靜態教育型內容"""
    return render_template('margin_warning.html')


@app.route('/margin-alert')
def margin_alert():
    # 已由「融資維持率查詢」取代；保留舊 URL 可用，直接導向新頁。
    return redirect(url_for('margin_maintenance'))


@app.route('/margin-maintenance')
def margin_maintenance():
    return render_template('margin_maintenance.html')


@app.route('/api/margin-maintenance')
def api_margin_maintenance():
    from scanners.margin_maintenance import (get_stock_maintenance, CodeError,
                                             MarketNotFoundError, DEFAULT_N)
    code = request.args.get('code', '').strip()
    try:
        n = int(request.args.get('n', DEFAULT_N))
    except (TypeError, ValueError):
        n = DEFAULT_N
    try:
        return jsonify(get_stock_maintenance(code, n))
    except CodeError as e:
        return jsonify({'error': 'invalid_code', 'message': str(e)}), 422
    except MarketNotFoundError as e:
        return jsonify({'error': 'not_found', 'message': str(e)}), 422
    except Exception as e:
        logger.error(f"margin-maintenance error: {e}")
        return jsonify({'error': 'internal', 'message': str(e)}), 500


@app.route('/api/margin-maintenance/scan')
def api_margin_maintenance_scan():
    from scanners.margin_maintenance import scan_market, DEFAULT_N
    try:
        n = int(request.args.get('n', DEFAULT_N))
    except (TypeError, ValueError):
        n = DEFAULT_N
    try:
        return jsonify(scan_market(n))
    except Exception as e:
        logger.error(f"margin-maintenance scan error: {e}")
        return jsonify({'error': 'internal', 'message': str(e), 'rows': []}), 500
