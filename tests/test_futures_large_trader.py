"""
個股期「期貨大戶淨部位 / 籌碼集中度」單元測試
跑：python -m pytest tests/test_futures_large_trader.py -v

黃金樣本來自實機比對：精材（3374，商品代碼 QL，大型契約）2026-05-19 ~ 2026-06-11
共 18 個交易日的淨部位，與參考網站逐日完全一致。
"""
import os
import sys
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers.taifex_large_trader import _parse_csv, _to_int, _iso_date, _fmt_query  # noqa: E402
from scrapers.taifex_stock_futures import lots_per_contract  # noqa: E402
from scanners.futures_large_trader import (  # noqa: E402
    _pct,
    get_stock_large_trader,
    get_stock_futures_products,
    has_stock_futures,
)


# -------- CSV 解析 --------

HEADER = '日期,商品(契約),商品名稱(契約名稱),到期月份(週別),交易人類別,前五大交易人買方,前五大交易人賣方,前十大交易人買方,前十大交易人賣方,全市場未沖銷部位數\r\n'


def test_parse_keeps_only_all_months():
    """只保留到期月份 999999（所有月份合計），逐月列必須丟掉。"""
    csv_text = HEADER + (
        '2026/06/11,QL     ,精材期貨,202606  ,0,100,200,300,400,1000\r\n'
        '2026/06/11,QL     ,精材期貨,999999  ,0,1000,1200,1500,1400,1931\r\n'
        '2026/06/11,QL     ,精材期貨,999999  ,1,50,60,70,80,1931\r\n'
    )
    rows = _parse_csv(csv_text)
    assert len(rows) == 2
    assert {r['trader_type'] for r in rows} == {0, 1}
    assert all(r['product_code'] == 'QL' for r in rows)   # 尾隨空白已 strip
    assert rows[0]['date'] == '2026-06-11'                # 日期轉 ISO


def test_parse_skips_bad_rows():
    """錯誤頁 / 殘缺列 / 非 0,1 交易人類別一律跳過，不拋例外。"""
    csv_text = HEADER + (
        '<html>錯誤頁\r\n'
        '2026/06/11,QL     ,精材期貨,999999  ,9,1,2,3,4,5\r\n'   # 交易人類別非 0/1
        '2026/06/11,QL     ,精材期貨,999999  ,0\r\n'              # 欄位不足
    )
    assert _parse_csv(csv_text) == []


def test_to_int_handles_dash_and_commas():
    assert _to_int('1,234') == 1234
    assert _to_int('-') == 0
    assert _to_int('') == 0
    assert _to_int(None) == 0
    assert _to_int('abc') == 0


def test_iso_date_and_query_format():
    assert _iso_date('2026/08/13') == '2026-08-13'
    assert _fmt_query('20260813') == '2026/08/13'
    assert _fmt_query('2026-08-13') == '2026/08/13'
    with pytest.raises(ValueError):
        _fmt_query('2026-8-1')


# -------- 契約乘數（張/口） --------

def test_lots_per_contract():
    assert lots_per_contract('2330', False) == 2.0     # 股票期貨 2,000 股
    assert lots_per_contract('2330', True) == 0.1      # 小型股票期貨 100 股
    assert lots_per_contract('0050', False) == 10.0    # ETF 期貨 10,000 受益權單位
    assert lots_per_contract('0050', True) == 1.0      # 小型 ETF 期貨 1,000 單位


# -------- 百分比防呆 --------

def test_pct_guards_zero_denominator():
    assert _pct(100, 0) is None      # 全市場未沖銷為 0 → None（前端顯示 '—'）
    assert _pct(100, None) is None
    assert _pct(50, 200) == 25.0


# -------- v3 大戶公式（記憶體 DB） --------

def _mem_db():
    """建一個只含本功能兩張表的記憶體 DB，欄位與 models/database.py 一致。"""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE futures_large_trader (
            date TEXT, product_code TEXT, trader_type INTEGER,
            top5_buy INTEGER, top5_sell INTEGER,
            top10_buy INTEGER, top10_sell INTEGER, market_oi INTEGER,
            PRIMARY KEY (date, product_code, trader_type));
        CREATE TABLE stock_futures_map (
            product_code TEXT PRIMARY KEY, stock_id TEXT, stock_name TEXT,
            product_name TEXT, is_mini INTEGER, is_etf INTEGER, lots_per_contract REAL);
    """)
    return conn


def _add_map(conn, code, sid, is_mini, lots):
    conn.execute("INSERT INTO stock_futures_map VALUES (?,?,?,?,?,?,?)",
                 (code, sid, 'X', 'X期貨', is_mini, 0, lots))


def _add_pos(conn, date, code, ttype, b5, s5, b10, s10, oi):
    conn.execute("INSERT INTO futures_large_trader VALUES (?,?,?,?,?,?,?,?)",
                 (date, code, ttype, b5, s5, b10, s10, oi))


def test_v3_formula_golden_sample():
    """
    精材 2026-06-11 期交所實際資料（999999 層級）：
      含造市十大 買 737 / 賣 1,909，特法十大 買 430 / 賣 1,902，全市場未沖銷 1,931。
      大戶 = (737-430) - (1909-1902) = 307 - 7 = 300 口 → 大型 2 張/口 → 600 張。
    參考網站當日顯示「600 張、大型 300 口」，逐日 18 天比對全數一致。
    """
    conn = _mem_db()
    _add_map(conn, 'QL', '3374', 0, 2.0)
    _add_pos(conn, '2026-06-11', 'QL', 0, 493, 1828, 737, 1909, 1931)
    _add_pos(conn, '2026-06-11', 'QL', 1, 329, 1828, 430, 1902, 1931)

    r = get_stock_large_trader(conn, '3374', days=20)
    assert r['has_futures'] is True
    assert r['latest']['main_contracts'] == 300
    assert r['latest']['net_lots'] == 600.0
    assert r['latest']['mini_contracts'] == 0


def test_v3_subtracts_institutional_leg_by_leg():
    """特法買賣兩腳都要各自扣掉，不能只扣淨額。"""
    conn = _mem_db()
    _add_map(conn, 'CD', '2330', 0, 2.0)
    # 含造市：買 1000 賣 400；特法：買 300 賣 100
    # 大戶 = (1000-300) - (400-100) = 700 - 300 = 400 口 → 800 張
    _add_pos(conn, '2026-08-13', 'CD', 0, 800, 300, 1000, 400, 10000)
    _add_pos(conn, '2026-08-13', 'CD', 1, 250, 80, 300, 100, 10000)

    r = get_stock_large_trader(conn, '2330', days=20)
    assert r['latest']['main_contracts'] == 400
    assert r['latest']['net_lots'] == 800.0


def test_missing_institutional_row_treated_as_zero():
    """特法列缺漏時視為 0，不可讓整檔契約消失。"""
    conn = _mem_db()
    _add_map(conn, 'CD', '2330', 0, 2.0)
    _add_pos(conn, '2026-08-13', 'CD', 0, 800, 300, 1000, 400, 10000)

    r = get_stock_large_trader(conn, '2330', days=20)
    assert r['latest']['main_contracts'] == 600     # 1000 - 400
    assert r['latest']['net_lots'] == 1200.0


def test_mini_contract_converted_with_own_multiplier():
    """大型 + 小型各自套乘數再相加（小型 0.1 張/口）。"""
    conn = _mem_db()
    _add_map(conn, 'CD', '2330', 0, 2.0)
    _add_map(conn, 'QF', '2330', 1, 0.1)
    _add_pos(conn, '2026-08-13', 'CD', 0, 0, 0, 1000, 0, 30000)     # 大型 +1000 口 → +2000 張
    _add_pos(conn, '2026-08-13', 'CD', 1, 0, 0, 0, 0, 30000)
    _add_pos(conn, '2026-08-13', 'QF', 0, 0, 0, 0, 5000, 90000)     # 小型 -5000 口 → -500 張
    _add_pos(conn, '2026-08-13', 'QF', 1, 0, 0, 0, 0, 90000)

    s = get_stock_large_trader(conn, '2330', days=20)['latest']
    assert s['main_contracts'] == 1000
    assert s['mini_contracts'] == -5000
    assert s['net_lots'] == 1500.0                  # 2000 - 500


def test_concentration_uses_main_contract_not_largest_lot_count():
    """
    集中度必須挑大型契約：小型乘數只有 1/20，未沖銷「口數」天生較大，
    若用口數比大小會選到小型契約，集中度全錯。
    """
    conn = _mem_db()
    _add_map(conn, 'CD', '2330', 0, 2.0)
    _add_map(conn, 'QF', '2330', 1, 0.1)
    _add_pos(conn, '2026-08-13', 'CD', 0, 0, 0, 1000, 400, 30000)   # 口數較少但為大型
    _add_pos(conn, '2026-08-13', 'CD', 1, 0, 0, 0, 0, 30000)
    _add_pos(conn, '2026-08-13', 'QF', 0, 0, 0, 100, 90, 90000)     # 口數較多的小型
    _add_pos(conn, '2026-08-13', 'QF', 1, 0, 0, 0, 0, 90000)

    s = get_stock_large_trader(conn, '2330', days=20)['latest']
    assert s['conc_code'] == 'CD'
    assert s['market_oi'] == 30000
    assert s['conc_net_pct'] == 2.0                 # (1000-400)/30000
    assert s['top10_buy_pct'] == pytest.approx(3.33, abs=0.01)


def test_series_ordered_old_to_new_and_limited_by_days():
    conn = _mem_db()
    _add_map(conn, 'QL', '3374', 0, 2.0)
    for i, d in enumerate(['2026-08-11', '2026-08-12', '2026-08-13']):
        _add_pos(conn, d, 'QL', 0, 0, 0, 100 + i, 0, 1000)
        _add_pos(conn, d, 'QL', 1, 0, 0, 0, 0, 1000)

    r = get_stock_large_trader(conn, '3374', days=2)
    assert [s['date'] for s in r['series']] == ['2026-08-12', '2026-08-13']
    assert r['latest']['date'] == '2026-08-13'


def test_no_futures_stock_returns_empty_payload():
    conn = _mem_db()
    _add_map(conn, 'CD', '2330', 0, 2.0)

    r = get_stock_large_trader(conn, '1234', days=20)
    assert r['has_futures'] is False
    assert r['series'] == []
    assert r['latest'] is None
    assert has_stock_futures(conn, '1234') is False
    assert has_stock_futures(conn, '2330') is True
    assert get_stock_futures_products(conn, '2330')[0]['product_code'] == 'CD'


def test_missing_tables_do_not_raise():
    """舊 DB（尚未建表）時視為無股期，不可讓個股頁 500。"""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    r = get_stock_large_trader(conn, '2330', days=20)
    assert r['has_futures'] is False
    assert r['series'] == []
