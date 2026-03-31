"""
Flask 主程式 — 台股掃描器網站
"""
import sys
import os
import json
import time
import logging
import threading
import requests as http_requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify
from models.database import (init_db, get_conn, get_latest_date, get_breakouts_by_date,
                             get_trading_dates, get_broker_trades,
                             get_regime_history, get_latest_regime,
                             add_to_watchlist, remove_from_watchlist,
                             get_watchlist, is_in_watchlist)
from scanners.institutional import get_ranking
try:
    from scanners.regime import get_market_temperature, rolling_retrain, get_model_info
except ImportError:
    get_market_temperature = None
    rolling_retrain = None
    get_model_info = None
from scrapers.market import fetch_futures_oi, fetch_retail_ratio, fetch_put_call_ratio, _finmind_get

app = Flask(__name__)

# 啟動時初始化 DB
init_db()


# ===== 全球行情 (Global Quotes) =====

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


# ===== 融資融券 (Margin Trading) =====

def fetch_margin_data(stock_id, days=20):
    """從 FinMind 抓取融資融券資料"""
    end = datetime.now()
    start = end - timedelta(days=days + 15)
    try:
        r = http_requests.get("https://api.finmindtrade.com/api/v4/data",
            params={
                "dataset": "TaiwanStockMarginPurchaseShortSale",
                "data_id": stock_id,
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            },
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        d = r.json()
        if d.get("status") != 200 or not d.get("data"):
            return []
        result = []
        for row in d["data"]:
            bal = float(row.get("MarginPurchaseTodayBalance", 0))
            limit = float(row.get("MarginPurchaseLimit", 0))
            s_bal = float(row.get("ShortSaleTodayBalance", 0))
            result.append({
                "date": row["date"],
                "balance": bal,
                "limit": limit,
                "use_rate": round(bal / limit * 100, 2) if limit > 0 else 0,
                "short_bal": s_bal,
            })
        result.sort(key=lambda x: x["date"])
        return result[-days:]
    except Exception as e:
        logger.error(f"融資融券資料抓取失敗 ({stock_id}): {e}")
        return []


# Cached latest_date for context processor (avoid DB hit on every request)
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
            conn = get_conn()
            try:
                latest = get_latest_date(conn)
                _latest_date_cache['value'] = latest
                _latest_date_cache['ts'] = now
            finally:
                conn.close()
    return {
        'today': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'last_update': latest or '尚未更新',
    }


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404, message='頁面不存在'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, message='伺服器錯誤'), 500


@app.route('/')
def index():
    return redirect_to_breakout()


# ===== 產業研究 =====
RESEARCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research')

@app.route('/research')
def research_list():
    """列出所有研究報告，按分類資料夾分組"""
    import re as _re
    def _extract_title(fpath):
        """從 HTML <title> 抓取報告標題"""
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                head = fh.read(3000)
            m = _re.search(r'<title[^>]*>([^<]+)</title>', head)
            if m:
                t = m.group(1).strip()
                t = _re.sub(r'\s*\|\s*GiS.*$', '', t)
                if t:
                    return t
        except Exception:
            pass
        return None

    def _extract_summary(fpath, max_len=80):
        """從 HTML 抓第一段有意義的文字當摘要"""
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read(10000)
            # 找 <p> 或 <div> 裡第一段有意義的文字
            for tag in ['p', 'h2', 'h3', 'li']:
                matches = _re.findall(rf'<{tag}[^>]*>([^<]+)</{tag}>', content)
                for m in matches:
                    text = m.strip()
                    # 跳過太短、純英文標題、或 boilerplate
                    if len(text) > 15 and text not in ('GiS', 'Report', 'Table of Contents'):
                        if len(text) > max_len:
                            text = text[:max_len] + '...'
                        return text
        except Exception:
            pass
        return ''

    categories = {}
    if os.path.isdir(RESEARCH_DIR):
        for item in sorted(os.listdir(RESEARCH_DIR)):
            item_path = os.path.join(RESEARCH_DIR, item)
            if os.path.isdir(item_path) and not item.startswith('_'):
                reports = []
                for f in sorted(os.listdir(item_path)):
                    if f.endswith('.html'):
                        fpath = os.path.join(item_path, f)
                        mtime = os.path.getmtime(fpath)
                        date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                        title = _extract_title(fpath) or f.replace('.html', '').replace('-', ' ').replace('_', ' ')
                        summary = _extract_summary(fpath)
                        reports.append({'filename': f, 'title': title, 'category': item, 'date': date_str, 'summary': summary})
                if reports:
                    # 按日期排序，最新的在前
                    reports.sort(key=lambda x: x['date'], reverse=True)
                    categories[item] = reports
            elif item.endswith('.html'):
                fpath = os.path.join(RESEARCH_DIR, item)
                mtime = os.path.getmtime(fpath)
                date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                title = _extract_title(fpath) or item.replace('.html', '').replace('-', ' ').replace('_', ' ')
                summary = _extract_summary(fpath)
                categories.setdefault('其他', []).append({'filename': item, 'title': title, 'category': '', 'date': date_str, 'summary': summary})
    total_count = sum(len(v) for v in categories.values())
    return render_template('research.html', categories=categories, total_count=total_count)

@app.route('/api/research/<path:filepath>')
def api_research(filepath):
    """動態載入研究報告內容"""
    import re
    # 安全檢查：只允許英數中文、底線、連字號、點、斜線
    if re.search(r'\.\.', filepath) or not re.match(r'^[\w\-\./\u4e00-\u9fff]+\.html$', filepath):
        return 'Invalid', 400
    full_path = os.path.join(RESEARCH_DIR, filepath)
    if not os.path.isfile(full_path):
        return 'Not found', 404
    with open(full_path, 'r', encoding='utf-8') as f:
        return f.read()


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
                temperature = round(min(100.0, (current_error / tau) * 50), 1)
                history = [{'date': r['date'], 'error': r['recon_error'], 'regime': r['regime']}
                           for r in reversed(rows)]
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
        return render_template('regime.html',
                               temperature=result['temperature'],
                               current_error=result['current_error'],
                               tau=result['tau'],
                               regime=result['regime'],
                               latest_date=result['latest_date'],
                               history=result['history'],
                               history_json=json.dumps(result['history']),
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


@app.route('/institutional')
def institutional():
    conn = get_conn()
    try:
        inst_type = request.args.get('type', 'foreign')
        days = int(request.args.get('days', '1'))
        market = request.args.get('market', 'all')
        date = request.args.get('date') or get_latest_date(conn)

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


@app.route('/margin-alert')
def margin_alert():
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


# ===== Report Cache =====
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
        # 2. Institutional aggregates for TWSE (latest date)
        twse_inst = conn.execute("""
            SELECT SUM(i.foreign_buy) as foreign_net,
                   SUM(i.sitc_buy) as sitc_net,
                   SUM(i.dealer_buy) as dealer_net
            FROM institutional i
            JOIN stocks s ON s.stock_id = i.stock_id
            WHERE i.date = ? AND s.market = 'twse'
        """, (latest,)).fetchone()

        # 3. Same for TPEx
        tpex_inst = conn.execute("""
            SELECT SUM(i.foreign_buy) as foreign_net,
                   SUM(i.sitc_buy) as sitc_net,
                   SUM(i.dealer_buy) as dealer_net
            FROM institutional i
            JOIN stocks s ON s.stock_id = i.stock_id
            WHERE i.date = ? AND s.market = 'tpex'
        """, (latest,)).fetchone()

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
        """, (latest, latest)).fetchall()

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
        """, (latest, latest)).fetchall()

        # Parallel fetch of all external API data with caching
        external_results = {}
        tasks = {
            'quotes': lambda: get_global_quotes(),
            'futures': lambda: fetch_futures_oi(days=20),
            'pc': lambda: fetch_put_call_ratio(days=20),
            'margin_summary': lambda: fetch_margin_trading_summary(latest),
            'inst_detail': lambda: fetch_institutional_detail(latest),
            'inst_detail_prev': lambda: fetch_institutional_detail_prev(latest),
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


@app.route('/heatmap')
def heatmap():
    return render_template('heatmap.html')


@app.route('/api/heatmap')
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


# ===== API 路由 =====

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
        date = request.args.get('date') or get_latest_date(conn)
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


# ===== 大盤技術指標 (TAIEX Technical Indicators) =====

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


# ===== 主力成本線 (Institutional Cost Estimation) =====

def calc_institutional_cost(conn, stock_id, days=20):
    """Estimate institutional cost by VWAP of buying days"""
    rows = conn.execute("""
        SELECT dp.date, dp.close_price, dp.volume, dp.high_price, dp.low_price,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.sitc_buy, 0) as sitc_buy,
               COALESCE(i.dealer_buy, 0) as dealer_buy
        FROM daily_prices dp
        LEFT JOIN institutional i ON i.stock_id = dp.stock_id AND i.date = dp.date
        WHERE dp.stock_id = ?
        ORDER BY dp.date DESC LIMIT ?
    """, (stock_id, days)).fetchall()

    if not rows:
        return None

    result = {}
    for inst_type, col in [('foreign', 'foreign_buy'), ('sitc', 'sitc_buy'), ('dealer', 'dealer_buy')]:
        total_cost = 0
        total_vol = 0
        days_buying = 0
        for r in rows:
            buy_vol = r[col]
            if buy_vol > 0:
                avg_price = (r['high_price'] + r['low_price']) / 2
                total_cost += avg_price * buy_vol
                total_vol += buy_vol
                days_buying += 1

        if total_vol > 0:
            vwap = round(total_cost / total_vol, 2)
            result[inst_type] = {
                'cost': vwap,
                'total_volume': total_vol,
                'days_buying': days_buying,
            }
        else:
            result[inst_type] = None

    if rows:
        result['current_price'] = rows[0]['close_price']
        result['period'] = days

    return result


# ===== 技術指標計算 =====

def calc_ma(closes, period):
    """計算移動平均線"""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(round(sum(closes[i - period + 1:i + 1]) / period, 2))
    return result


def calc_rsi(closes, period=14):
    """計算 RSI 指標"""
    if len(closes) < period + 1:
        return [None] * len(closes)
    result = [None] * period
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, c) for c in changes[:period]]
    losses = [max(0, -c) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100 - 100 / (1 + rs), 2))
    for i in range(period, len(changes)):
        gain = max(0, changes[i])
        loss = max(0, -changes[i])
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100 - 100 / (1 + rs), 2))
    return result


def _get_stock_detail_data(conn, stock_id):
    """取得個股詳細資料（K線、法人、券商），供頁面和 API 共用"""
    # 股票基本資料
    stock_row = conn.execute("SELECT stock_id, name, market FROM stocks WHERE stock_id = ?",
                             (stock_id,)).fetchone()
    if not stock_row:
        return None

    stock_name = stock_row['name']
    stock_market = stock_row['market']

    # K線資料：最近 60 個交易日
    price_rows = conn.execute("""
        SELECT date, open_price, high_price, low_price, close_price, volume, change_pct
        FROM daily_prices WHERE stock_id = ? ORDER BY date DESC LIMIT 60
    """, (stock_id,)).fetchall()
    price_rows = list(reversed(price_rows))  # 時間正序

    kline_data = []
    closes = []
    for r in price_rows:
        kline_data.append({
            'date': r['date'],
            'open': r['open_price'],
            'high': r['high_price'],
            'low': r['low_price'],
            'close': r['close_price'],
            'volume': r['volume'],
            'change_pct': r['change_pct'],
        })
        closes.append(r['close_price'])

    # 計算技術指標
    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    rsi = calc_rsi(closes, 14)

    # 最新價格資訊
    current_price = closes[-1] if closes else 0
    current_change = kline_data[-1]['change_pct'] if kline_data else 0

    # 法人買賣超：最近 20 個交易日
    inst_rows = conn.execute("""
        SELECT date, foreign_buy, sitc_buy, dealer_buy, total_buy
        FROM institutional
        WHERE stock_id = ? ORDER BY date DESC LIMIT 20
    """, (stock_id,)).fetchall()
    inst_data = []
    for r in inst_rows:
        inst_data.append({
            'date': r['date'],
            'foreign_buy': r['foreign_buy'],
            'sitc_buy': r['sitc_buy'],
            'dealer_buy': r['dealer_buy'],
            'total_buy': r['total_buy'],
        })

    # 法人合計
    foreign_total = sum(r['foreign_buy'] for r in inst_rows)
    sitc_total = sum(r['sitc_buy'] for r in inst_rows)
    dealer_total = sum(r['dealer_buy'] for r in inst_rows)

    # 券商分點
    latest_date = get_latest_date(conn)
    broker_buy, broker_sell = get_broker_trades(conn, stock_id, latest_date) if latest_date else ([], [])

    return {
        'stock_id': stock_id,
        'stock_name': stock_name,
        'stock_market': stock_market,
        'current_price': current_price,
        'current_change': current_change,
        'kline': kline_data,
        'ma5': ma5,
        'ma20': ma20,
        'ma60': ma60,
        'rsi': rsi,
        'institutional': inst_data,
        'foreign_total': foreign_total,
        'sitc_total': sitc_total,
        'dealer_total': dealer_total,
        'broker_buy': [dict(r) for r in broker_buy],
        'broker_sell': [dict(r) for r in broker_sell],
        'broker_date': latest_date,
    }


@app.route('/stock')
def stock_detail():
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return render_template('stock.html', data=None, message='請提供股票代號（例：/stock?id=2330）')

    conn = get_conn()
    try:
        data = _get_stock_detail_data(conn, stock_id)
        if not data:
            return render_template('stock.html', data=None,
                                   message=f'找不到股票 {stock_id}')

        # 融資融券資料（即時從 FinMind 抓取）
        margin_data = fetch_margin_data(stock_id)

        # 主力成本線
        inst_cost = calc_institutional_cost(conn, stock_id, days=20)

        # 自選股狀態
        in_watchlist = is_in_watchlist(conn, stock_id)

        return render_template('stock.html', data=data, message=None,
                               kline_json=json.dumps(data['kline']),
                               ma5_json=json.dumps(data['ma5']),
                               ma20_json=json.dumps(data['ma20']),
                               ma60_json=json.dumps(data['ma60']),
                               rsi_json=json.dumps(data['rsi']),
                               margin_json=json.dumps(margin_data),
                               inst_cost=inst_cost,
                               in_watchlist=in_watchlist)
    finally:
        conn.close()


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


@app.route('/api/quotes')
def api_quotes():
    data = get_global_quotes()
    return jsonify(data)


@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if len(q) < 1:
        return jsonify([])
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT stock_id, name, market FROM stocks WHERE stock_id LIKE ? OR name LIKE ? LIMIT 10",
            (f'{q}%', f'%{q}%')
        ).fetchall()
        return jsonify([{'id': r['stock_id'], 'name': r['name'], 'market': r['market']} for r in rows])
    finally:
        conn.close()


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
            HEALTH_CHECK_TABLES = {
                'daily_prices': 'date',
                'breakouts': 'date',
                'institutional': 'date',
                'broker_trades': 'date',
            }
            for table, date_col in HEALTH_CHECK_TABLES.items():
                if table not in HEALTH_CHECK_TABLES or date_col != 'date':
                    continue  # whitelist guard
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


@app.route('/api/stock')
def api_stock():
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return jsonify({'error': '需提供 id 參數'}), 400

    conn = get_conn()
    try:
        data = _get_stock_detail_data(conn, stock_id)
        if not data:
            return jsonify({'error': f'找不到股票 {stock_id}'}), 404
        return jsonify(data)
    finally:
        conn.close()


@app.route('/watchlist')
def watchlist():
    conn = get_conn()
    try:
        items = get_watchlist(conn)
        return render_template('watchlist.html', items=items)
    finally:
        conn.close()


@app.route('/api/watchlist/add', methods=['POST'])
def api_watchlist_add():
    stock_id = request.json.get('stock_id', '').strip()
    if not stock_id:
        return jsonify({'error': 'missing stock_id'}), 400
    conn = get_conn()
    try:
        add_to_watchlist(conn, stock_id)
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/api/watchlist/remove', methods=['POST'])
def api_watchlist_remove():
    stock_id = request.json.get('stock_id', '').strip()
    conn = get_conn()
    try:
        remove_from_watchlist(conn, stock_id)
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/api/stock-realtime')
def api_stock_realtime():
    """盤中即時報價（單一個股），用 mis.twse.com.tw"""
    stock_id = request.args.get('id', '').strip()
    if not stock_id:
        return jsonify({'error': 'missing id'}), 400

    conn = get_conn()
    try:
        stock = conn.execute("SELECT stock_id, name, market FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if not stock:
            return jsonify({'error': 'not found'}), 404
    finally:
        conn.close()

    from scrapers.realtime import MIS_URL, _parse_float, _parse_int
    prefix = 'tse' if stock['market'] == 'twse' else 'otc'
    query = f'{prefix}_{stock_id}.tw'

    try:
        resp = http_requests.get(MIS_URL, params={'ex_ch': query},
                                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        data = resp.json()
        items = data.get('msgArray', [])
        if not items:
            return jsonify({'error': 'no data'}), 404

        item = items[0]
        z = _parse_float(item.get('z'))       # 最新成交價
        y = _parse_float(item.get('y'))       # 昨收
        o = _parse_float(item.get('o'))       # 開盤
        h = _parse_float(item.get('h'))       # 最高
        l = _parse_float(item.get('l'))       # 最低
        v = _parse_int(item.get('v'))         # 成交量(張)
        t = item.get('t', '')                 # 時間

        pct = round((z - y) / y * 100, 2) if z and y and y > 0 else 0
        change = round(z - y, 2) if z and y else 0

        return jsonify({
            'stock_id': stock_id,
            'price': z,
            'change': change,
            'change_pct': pct,
            'open': o,
            'high': h,
            'low': l,
            'volume': v,
            'time': t,
            'yesterday': y,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-preview')
def api_stock_preview():
    stock_id = request.args.get('id', '').strip()
    conn = get_conn()
    try:
        stock = conn.execute("SELECT * FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if not stock:
            return jsonify({'error': 'not found'}), 404
        prices = conn.execute("""
            SELECT date, close_price, open_price, high_price, low_price, change_pct, volume
            FROM daily_prices WHERE stock_id=? ORDER BY date DESC LIMIT 20
        """, (stock_id,)).fetchall()
        inst = conn.execute("""
            SELECT date, foreign_buy, sitc_buy, dealer_buy
            FROM institutional WHERE stock_id=? ORDER BY date DESC LIMIT 5
        """, (stock_id,)).fetchall()
        in_watchlist = is_in_watchlist(conn, stock_id)
        return jsonify({
            'stock_id': stock['stock_id'],
            'name': stock['name'],
            'market': stock['market'],
            'prices': [dict(r) for r in prices],
            'institutional': [dict(r) for r in inst],
            'in_watchlist': in_watchlist,
        })
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


def redirect_to_breakout():
    from flask import redirect, url_for
    return redirect(url_for('breakout'))


# ===== Feature 1: 條件篩選器 (Custom Stock Screener) =====

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


# ===== Feature 2: 產業族群分類 (Industry Sector Classification) =====

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


# ===== Feature 3: 類股漲跌幅熱力圖 (Taiwan Sector Heatmap) =====

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


# 即時熱力圖快取（5 分鐘）
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


# ===== Feature: 歷史回測 (Backtesting) =====

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


@app.route('/weekly')
def weekly():
    """研究週報頁面 — 自動彙整量化研究與科技研究週報。"""
    import glob, re

    base_src = os.path.join(os.path.dirname(__file__), '..', 'src')
    if not os.path.isdir(base_src):
        base_src = r'D:\claude\src'

    fin_lab = os.path.join(base_src, 'fin-lab')
    tech_research = os.path.join(base_src, 'tech-research')

    # ── 1. 量化研究週報（fin-lab/output/weekly-briefing-*.html）
    fin_briefings = []
    output_dir = os.path.join(fin_lab, 'output')
    if os.path.isdir(output_dir):
        for f in sorted(glob.glob(os.path.join(output_dir, 'weekly-briefing-*.html')), reverse=True):
            fname = os.path.basename(f)
            m = re.search(r'(\d{4}-\d{2}-\d{2})', fname)
            date_str = m.group(1) if m else ''
            fin_briefings.append({
                'filename': fname,
                'date': date_str,
                'type': 'fin',
                'title': f'量化研究週報 {date_str}',
            })

    # ── 1b. 金融科技分類報告（fin-lab/output/category-reports/*.html）
    cat_reports_dir = os.path.join(fin_lab, 'output', 'category-reports')
    cat_reports = {}  # {category: [reports]}

    # 檔名→分類 映射
    _CAT_MAP = {
        # 風險管理
        'regime-detector': '風險管理', 'garch-report': '風險管理', 'te-report': '風險管理',
        'entropy-report': '風險管理', 'km-report': '風險管理', 'risk-management_report': '風險管理',
        # 因子研究
        'blind-signal': '因子與策略', 'disagreement': '因子與策略', 'jf-ml-returns': '因子與策略',
        'raps-alpha-global': '因子與策略', 'nber-ai-pricing': '因子與策略',
        'nber-ml-markowitz': '因子與策略', 'report-factor': '因子與策略',
        'stat-arb_report': '因子與策略', 'fin-lab-reallife': '因子與策略',
        'finance-lab-briefing': '因子與策略',
        # 選擇權與波動率
        'oql-report': '選擇權與波動率', 'spx-vix': '選擇權與波動率', 'pinn-report': '選擇權與波動率',
        'options-volatility_report': '選擇權與波動率', 'tda-report': '選擇權與波動率',
        'quantum-report': '選擇權與波動率', 'diffusion-report': '選擇權與波動率',
        'report-regime-rl': '選擇權與波動率',
        # 情緒與 NLP
        'llm-screener': '情緒與 NLP', 'wti-report': '情緒與 NLP',
        'sentiment-nlp_report': '情緒與 NLP',
        # 總經與資產配置
        'rate-cycle': '總經與資產配置', 'mideast-war': '總經與資產配置',
        'FI-01': '總經與資產配置', 'FX-01': '總經與資產配置',
        'cross-market': '總經與資產配置', 'portfolio-optimization_report': '總經與資產配置',
        # 特殊主題
        'CQ-01': '特殊主題', 'ED-01': '特殊主題', 'ES-01': '特殊主題',
        'HF-01': '特殊主題', 'XA-01': '特殊主題', 'alternative-data_report': '特殊主題',
        'checkpoint': '特殊主題',
    }

    if os.path.isdir(cat_reports_dir):
        for f in sorted(glob.glob(os.path.join(cat_reports_dir, '*.html'))):
            fname = os.path.basename(f)
            title = fname.replace('.html', '').replace('-', ' ').replace('_', ' ')
            try:
                with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                    head = fh.read(3000)
                import re as _re
                m = _re.search(r'<title[^>]*>([^<]+)</title>', head)
                if m:
                    t = m.group(1).strip()
                    t = _re.sub(r'\s*\|\s*GiS.*$', '', t)
                    if t:
                        title = t
            except Exception:
                pass
            mtime = os.path.getmtime(f)
            date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

            # 分類
            cat = '其他'
            for prefix, c in _CAT_MAP.items():
                if fname.startswith(prefix) or prefix in fname:
                    cat = c
                    break

            cat_reports.setdefault(cat, []).append({
                'filename': fname,
                'date': date_str,
                'title': title,
            })

    # ── 2. 科技研究報告（tech-research/research-*/research-*.html）
    tech_reports = []
    if os.path.isdir(tech_research):
        for batch_dir in sorted(glob.glob(os.path.join(tech_research, 'research-*')), reverse=True):
            batch_name = os.path.basename(batch_dir)
            m = re.search(r'(\d{4}-\d{2}-\d{2})', batch_name)
            date_str = m.group(1) if m else ''
            for html_file in glob.glob(os.path.join(batch_dir, '*.html')):
                fname = os.path.basename(html_file)
                tech_reports.append({
                    'filename': f'{batch_name}/{fname}',
                    'date': date_str,
                    'type': 'tech',
                    'title': f'科技研究精選 {date_str}',
                })

    # ── 3. fin-lab 專案總覽
    projects = []
    categories = {}
    if os.path.isdir(fin_lab):
        for cat_dir in sorted(glob.glob(os.path.join(fin_lab, '*'))):
            cat_name = os.path.basename(cat_dir)
            if cat_name.startswith(('_', '.')) or cat_name in ('scripts', 'factor_data', 'qlib_data', '_meta', 'output'):
                continue
            if not os.path.isdir(cat_dir):
                continue
            for proj_dir in sorted(glob.glob(os.path.join(cat_dir, '[A-Z]*'))):
                proj_name = os.path.basename(proj_dir)
                has_py = len(glob.glob(os.path.join(proj_dir, '**', '*.py'), recursive=True)) > 0
                has_pdf = len(glob.glob(os.path.join(proj_dir, '**', '*.pdf'), recursive=True)) > 0
                has_json = len(glob.glob(os.path.join(proj_dir, '**', '*.json'), recursive=True)) > 0
                has_csv = len(glob.glob(os.path.join(proj_dir, '**', '*.csv'), recursive=True)) > 0
                code = proj_name.split('-')[0] if '-' in proj_name else proj_name[:5]
                display_name = '-'.join(proj_name.split('-')[1:]) if '-' in proj_name else proj_name
                proj = {
                    'code': code,
                    'name': display_name,
                    'category': cat_name,
                    'has_code': has_py,
                    'has_report': has_pdf,
                    'has_data': has_json or has_csv,
                }
                projects.append(proj)
                categories.setdefault(cat_name, []).append(proj)

    stats = {
        'total': len(projects),
        'with_code': sum(1 for p in projects if p['has_code']),
        'with_report': sum(1 for p in projects if p['has_report']),
        'categories': len(categories),
    }

    return render_template('weekly.html',
        fin_briefings=fin_briefings,
        cat_reports=cat_reports,
        tech_reports=tech_reports,
        projects=projects,
        categories=categories,
        stats=stats)


@app.route('/api/weekly/<path:filepath>')
def api_weekly_report(filepath):
    """動態載入週報 HTML 內容。"""
    import re
    base_src = os.path.join(os.path.dirname(__file__), '..', 'src')
    if not os.path.isdir(base_src):
        base_src = r'D:\claude\src'

    # 安全檢查
    if '..' in filepath:
        return 'Invalid', 400

    # fin-lab 週報
    if filepath.startswith('fin/'):
        fname = filepath[4:]
        if not re.match(r'^weekly-briefing-\d{4}-\d{2}-\d{2}\.html$', fname):
            return 'Invalid', 400
        full_path = os.path.join(base_src, 'fin-lab', 'output', fname)
    # 金融科技分類報告
    elif filepath.startswith('cat/'):
        fname = filepath[4:]
        if not re.match(r'^[\w\-\.]+\.html$', fname):
            return 'Invalid', 400
        full_path = os.path.join(base_src, 'fin-lab', 'output', 'category-reports', fname)
    # 科技研究報告
    elif filepath.startswith('tech/'):
        relpath = filepath[5:]
        if not re.match(r'^research-\d{4}-\d{2}-\d{2}/[\w\-\.]+\.html$', relpath):
            return 'Invalid', 400
        full_path = os.path.join(base_src, 'tech-research', relpath)
    else:
        return 'Invalid', 400

    if not os.path.isfile(full_path):
        return 'Not found', 404
    with open(full_path, 'r', encoding='utf-8') as f:
        return f.read()


# ===== 盤中即時報價背景更新 =====
_realtime_thread_started = False

def _realtime_background_loop():
    """背景每 5 分鐘抓全部股票即時報價（僅盤中 9:00~13:35）"""
    import time as _t
    from scrapers.realtime import fetch_realtime_prices, is_trading_hours
    from scanners.breakout import scan_breakouts

    logger.info("[即時報價] 背景執行緒啟動")
    while True:
        try:
            if is_trading_hours():
                conn = get_conn()
                try:
                    count = fetch_realtime_prices(conn)
                    if count > 0:
                        today = datetime.now().strftime('%Y-%m-%d')
                        scan_breakouts(conn, today)
                        conn.commit()
                        logger.info(f"[即時報價] 更新 {count} 筆，已重算突破")
                except Exception as e:
                    logger.error(f"[即時報價] 錯誤: {e}")
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[即時報價] 外層錯誤: {e}")

        _t.sleep(300)  # 5 分鐘


def start_realtime_thread():
    global _realtime_thread_started
    if _realtime_thread_started:
        return
    _realtime_thread_started = True
    t = threading.Thread(target=_realtime_background_loop, daemon=True)
    t.start()
    logger.info("[即時報價] 背景執行緒已啟動（每 5 分鐘更新）")


# Flask 啟動時自動開始（避免 debug reloader 重複啟動）
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    start_realtime_thread()


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
