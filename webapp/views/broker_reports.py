"""每日券商 PDF 報告（自 app.py 拆出）"""
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

BROKER_REPORTS_DIR = os.environ.get('BROKER_REPORTS_DIR', r'P:\2026年報告')


BROKER_RATING_CACHE = os.path.join(BASE_DIR, 'broker_ratings.json')


MAX_EXTRACT_PER_REQ = 80


def _broker_normalize_rating(raw):
    """把一段文字正規化成標準評等字串，抓不到回 None。先比對到先回傳。"""
    if not raw:
        return None
    t = raw.strip()
    tl = t.lower()
    # 買進
    if '買進' in t or 'buy' in tl or 'outperform' in tl or '優於大盤' in t:
        return '買進'
    # 增持
    if '增持' in t or '增加持股' in t or '加碼' in t or 'overweight' in tl or 'accumulate' in tl:
        return '增持'
    # 偏多
    if '偏多' in t:
        return '偏多'
    # 中立
    if '中立' in t or 'neutral' in tl or 'hold' in tl or '持有' in t or '同大盤' in t:
        return '中立'
    # 區間操作
    if '區間' in t:
        return '區間操作'
    # 減碼
    if '減碼' in t or '減持' in t or 'underweight' in tl or 'reduce' in tl or '劣於大盤' in t:
        return '減碼'
    # 賣出
    if '賣出' in t or 'sell' in tl:
        return '賣出'
    return None


def _extract_pdf_rating(fpath):
    """從 PDF 第 1 頁抽取投資評等，回傳標準評等字串或 ''。"""
    import re as _re
    try:
        import fitz
    except Exception:
        return ''
    try:
        doc = fitz.open(fpath)
    except Exception:
        return ''
    try:
        if doc.page_count < 1:
            return ''
        try:
            text = doc.load_page(0).get_text() or ''
        except Exception:
            return ''
    finally:
        try:
            doc.close()
        except Exception:
            pass
    text = text[:6000]
    # ① 先找有標籤的評等：標籤後方 0–14 字內的評等字樣（最可靠）
    m = _re.search(r'(投資評等|投資建議|評等|Rating|Recommendation)[\s：:．.\-]{0,4}(.{0,14})', text, _re.IGNORECASE)
    if m:
        rating = _broker_normalize_rating(m.group(2))
        if rating:
            return rating
    # ② 退而找內文前 1500 字是否直接出現評等字樣
    rating = _broker_normalize_rating(text[:1500])
    if rating:
        return rating
    return ''


@app.route('/broker-reports')
def broker_reports():
    """每日券商報告：一次只讀選定那一天的 PDF 清單"""
    import re as _re
    # pCloud 未掛載
    if not os.path.isdir(BROKER_REPORTS_DIR):
        return render_template('broker_reports.html', dates=[], reports=[],
                               selected_date=None,
                               message='pCloud 磁碟 (P:) 未掛載，無法讀取券商報告')

    # 掃描符合 ^\d{4}$ 的日期資料夾，計算各自 PDF 數
    dates = []
    for item in os.listdir(BROKER_REPORTS_DIR):
        item_path = os.path.join(BROKER_REPORTS_DIR, item)
        if _re.match(r'^\d{4}$', item) and os.path.isdir(item_path):
            count = 0
            for f in os.listdir(item_path):
                if f.lower().endswith('.pdf'):
                    count += 1
            dates.append({
                'code': item,
                'label': f'{item[:2]}/{item[2:]}',
                'count': count,
            })
    # 依 code 由大到小排序（最新在前）
    dates.sort(key=lambda d: d['code'], reverse=True)

    # 選定日期
    date_codes = [d['code'] for d in dates]
    selected = request.args.get('date')
    if selected not in date_codes:
        selected = dates[0]['code'] if dates else None

    # 讀取評等快取（不存在或壞掉 → 空 dict）
    rating_cache = {}
    try:
        with open(BROKER_RATING_CACHE, 'r', encoding='utf-8') as _cf:
            rating_cache = json.load(_cf)
        if not isinstance(rating_cache, dict):
            rating_cache = {}
    except Exception:
        rating_cache = {}
    cache_dirty = False
    extracted_this_req = 0
    deferred_count = 0

    reports = []
    count_all = count_stock = count_industry = 0
    if selected:
        day_dir = os.path.join(BROKER_REPORTS_DIR, selected)
        for f in os.listdir(day_dir):
            if not f.lower().endswith('.pdf'):
                continue
            base = f[:-4]  # 去掉 .pdf
            code = ''
            m = _re.match(r'^reports_(stock|industry)_reports_\d{4}_\d{4}_(.+)$', base)
            if m:
                kind, rest = m.group(1), m.group(2)
                if kind == 'stock':
                    rtype = '個股'
                    sm = _re.match(r'^(\d{2,6})(.+)$', rest)
                    if sm:
                        code, name = sm.group(1), sm.group(2)
                        title = f'{code} {name}'
                    else:
                        title = rest
                else:
                    rtype = '產業'
                    title = rest
            else:
                rtype = '其他'
                title = base

            # 評等：先查快取；未快取則抽取（每請求上限 MAX_EXTRACT_PER_REQ）
            cache_key = f'{selected}/{f}'
            if cache_key in rating_cache:
                rating = rating_cache[cache_key]
            elif extracted_this_req < MAX_EXTRACT_PER_REQ:
                rating = _extract_pdf_rating(os.path.join(day_dir, f))
                rating_cache[cache_key] = rating
                cache_dirty = True
                extracted_this_req += 1
            else:
                # 超過本請求抽取上限：先給空、不寫入快取，下次載入再補抽
                rating = ''
                deferred_count += 1

            reports.append({'filename': f, 'title': title, 'rtype': rtype, 'code': code, 'rating': rating})

        if deferred_count:
            logger.info(f"broker rating: {selected} 有 {deferred_count} 篇評等延後抽取")

        # 若本次有新增評等 → 寫回快取檔（寫失敗只 log 不讓頁面掛掉）
        if cache_dirty:
            try:
                with open(BROKER_RATING_CACHE, 'w', encoding='utf-8') as _cf:
                    json.dump(rating_cache, _cf, ensure_ascii=False)
            except Exception as _e:
                logger.warning(f"broker rating: 寫入快取失敗 {_e}")

        count_all = len(reports)
        count_stock = sum(1 for r in reports if r['rtype'] == '個股')
        count_industry = sum(1 for r in reports if r['rtype'] == '產業')
        # 產業在前、個股在後，同類型再依標題排序
        _order = {'產業': 0, '個股': 1, '其他': 2}
        reports.sort(key=lambda r: (_order.get(r['rtype'], 3), r['title']))

    return render_template('broker_reports.html', dates=dates, reports=reports,
                           selected_date=selected, count_all=count_all,
                           count_stock=count_stock, count_industry=count_industry,
                           message=None)


@app.route('/api/broker-report/<path:filepath>')
def api_broker_report(filepath):
    """動態載入券商報告 PDF（inline 內嵌預覽）"""
    # 安全檢查：拒絕 .. ；必須以 .pdf 結尾（不分大小寫）；
    # 字元不再限制，真正防護交給下方 realpath 包含檢查
    if '..' in filepath or not filepath.lower().endswith('.pdf'):
        return 'Invalid', 400
    full_path = os.path.join(BROKER_REPORTS_DIR, filepath)
    # 路徑遍歷防護：realpath 後必須位於 BROKER_REPORTS_DIR 之內
    resolved = os.path.realpath(full_path)
    broker_root = os.path.realpath(BROKER_REPORTS_DIR)
    if not resolved.startswith(broker_root + os.sep):
        return 'Forbidden', 403
    if not os.path.isfile(resolved):
        return 'Not found', 404
    return send_file(resolved, mimetype='application/pdf')
