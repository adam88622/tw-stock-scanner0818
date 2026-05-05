import sqlite3
import os
import logging
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_conn():
    """取得資料庫連線"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")  # 等待鎖最多 10 秒
    return conn


@contextmanager
def db_connection(auto_commit=False):
    """
    資料庫連線 context manager，自動處理 close/rollback。
    用法:
        with db_connection() as conn:
            conn.execute(...)
        # 自動 close

        with db_connection(auto_commit=True) as conn:
            conn.execute(...)
        # 自動 commit + close，失敗時自動 rollback
    """
    conn = get_conn()
    try:
        yield conn
        if auto_commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化資料庫 schema"""
    conn = get_conn()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            market     TEXT NOT NULL CHECK(market IN ('twse', 'tpex'))
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            open_price  REAL,
            high_price  REAL,
            low_price   REAL,
            close_price REAL,
            volume      INTEGER,
            trade_value INTEGER,
            change_pct  REAL,
            PRIMARY KEY (stock_id, date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id)
        );

        CREATE TABLE IF NOT EXISTS breakouts (
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            break_5     INTEGER DEFAULT 0,
            break_10    INTEGER DEFAULT 0,
            break_20    INTEGER DEFAULT 0,
            break_60    INTEGER DEFAULT 0,
            break_120   INTEGER DEFAULT 0,
            break_240   INTEGER DEFAULT 0,
            close_price REAL,
            change_pct  REAL,
            PRIMARY KEY (stock_id, date)
        );

        CREATE TABLE IF NOT EXISTS institutional (
            stock_id      TEXT NOT NULL,
            date          TEXT NOT NULL,
            foreign_buy   INTEGER DEFAULT 0,
            sitc_buy      INTEGER DEFAULT 0,
            dealer_buy    INTEGER DEFAULT 0,
            total_buy     INTEGER DEFAULT 0,
            PRIMARY KEY (stock_id, date)
        );

        CREATE TABLE IF NOT EXISTS broker_trades (
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            broker_name TEXT NOT NULL,
            buy_volume  INTEGER DEFAULT 0,
            sell_volume INTEGER DEFAULT 0,
            net_volume  INTEGER DEFAULT 0,
            pct         REAL DEFAULT 0,
            PRIMARY KEY (stock_id, date, broker_name)
        );

        CREATE TABLE IF NOT EXISTS regime_history (
            date TEXT PRIMARY KEY,
            recon_error REAL NOT NULL,
            tau REAL NOT NULL,
            regime TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS credit_spread_history (
            date TEXT PRIMARY KEY,
            hyg_shy_ratio REAL NOT NULL,
            indicator_value REAL NOT NULL,
            signal TEXT NOT NULL,
            spy_close REAL DEFAULT 0,
            trend5d REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            signal TEXT DEFAULT 'NEUTRAL',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, indicator)
        );

        CREATE TABLE IF NOT EXISTS cta_signal_history (
            date TEXT PRIMARY KEY,
            close REAL NOT NULL,
            signal_raw REAL NOT NULL,
            raw_pos REAL NOT NULL,
            position REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_indicators(date);
        CREATE INDEX IF NOT EXISTS idx_macro_indicator ON macro_indicators(indicator);
        CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date);
        CREATE INDEX IF NOT EXISTS idx_breakouts_date ON breakouts(date);
        CREATE INDEX IF NOT EXISTS idx_institutional_date ON institutional(date);
        CREATE INDEX IF NOT EXISTS idx_broker_trades_date ON broker_trades(date);
        CREATE INDEX IF NOT EXISTS idx_institutional_date_stock ON institutional(date, stock_id);
        CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);
        CREATE INDEX IF NOT EXISTS idx_breakouts_date ON breakouts(date);

        CREATE TABLE IF NOT EXISTS watchlist (
            stock_id TEXT PRIMARY KEY,
            added_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id)
        );

        CREATE TABLE IF NOT EXISTS holder_distribution (
            stock_id   TEXT NOT NULL,
            sca_date   TEXT NOT NULL,
            band       INTEGER NOT NULL,
            band_label TEXT NOT NULL,
            holders    INTEGER,
            shares     INTEGER,
            pct        REAL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_id, sca_date, band)
        );
        CREATE INDEX IF NOT EXISTS idx_holder_dist_date ON holder_distribution(sca_date);
        CREATE INDEX IF NOT EXISTS idx_holder_dist_stock ON holder_distribution(stock_id);

        CREATE TABLE IF NOT EXISTS notice_announcements (
            announce_date TEXT NOT NULL,
            stock_id      TEXT NOT NULL,
            name          TEXT,
            cumulative    INTEGER,
            reason        TEXT,
            close_price   REAL,
            is_real_stock INTEGER DEFAULT 0,
            updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (announce_date, stock_id, reason)
        );
        CREATE INDEX IF NOT EXISTS idx_notice_date  ON notice_announcements(announce_date);
        CREATE INDEX IF NOT EXISTS idx_notice_stock ON notice_announcements(stock_id);

        CREATE TABLE IF NOT EXISTS disposition_announcements (
            announce_date TEXT NOT NULL,
            stock_id      TEXT NOT NULL,
            name          TEXT,
            cumulative    INTEGER,
            condition     TEXT,
            period_start  TEXT,
            period_end    TEXT,
            action        TEXT,
            content       TEXT,
            is_real_stock INTEGER DEFAULT 0,
            updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (announce_date, stock_id, period_start)
        );
        CREATE INDEX IF NOT EXISTS idx_disp_date     ON disposition_announcements(announce_date);
        CREATE INDEX IF NOT EXISTS idx_disp_stock    ON disposition_announcements(stock_id);
        CREATE INDEX IF NOT EXISTS idx_disp_period   ON disposition_announcements(period_start, period_end);
    """)

    conn.commit()

    # Add sector column if not exists (Feature 2: Industry Sector)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    if 'sector' not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN sector TEXT DEFAULT ''")
        conn.commit()

    conn.close()
    logger.info("資料庫初始化完成")


def upsert_stock(conn, stock_id, name, market):
    """新增或更新股票基本資料（保留 sector 欄位）"""
    conn.execute("""
        INSERT INTO stocks (stock_id, name, market, sector) VALUES (?, ?, ?, '')
        ON CONFLICT(stock_id) DO UPDATE SET name=excluded.name, market=excluded.market
    """, (stock_id, name, market))


def upsert_daily_price(conn, stock_id, date, open_price, high_price, low_price,
                        close_price, volume, trade_value, change_pct):
    """新增或更新每日收盤價"""
    conn.execute("""
        INSERT OR REPLACE INTO daily_prices
        (stock_id, date, open_price, high_price, low_price, close_price, volume, trade_value, change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (stock_id, date, open_price, high_price, low_price, close_price, volume, trade_value, change_pct))


def upsert_breakout(conn, stock_id, date, breaks, close_price, change_pct):
    """新增或更新突破紀錄。breaks = dict like {5: 1, 10: 0, ...}"""
    conn.execute("""
        INSERT OR REPLACE INTO breakouts
        (stock_id, date, break_5, break_10, break_20, break_60, break_120, break_240, close_price, change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        stock_id, date,
        breaks.get(5, 0), breaks.get(10, 0), breaks.get(20, 0),
        breaks.get(60, 0), breaks.get(120, 0), breaks.get(240, 0),
        close_price, change_pct
    ))


def upsert_institutional(conn, stock_id, date, foreign_buy, sitc_buy, dealer_buy):
    """新增或更新法人買賣超"""
    total = foreign_buy + sitc_buy + dealer_buy
    conn.execute("""
        INSERT OR REPLACE INTO institutional
        (stock_id, date, foreign_buy, sitc_buy, dealer_buy, total_buy)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (stock_id, date, foreign_buy, sitc_buy, dealer_buy, total))


def upsert_holder_distribution(conn, stock_id, sca_date, rows):
    """
    寫入單支股票某週的 17 行持股分佈。
    rows: list[dict] from scrapers.tdcc.parse_holder_table
    """
    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO holder_distribution
            (stock_id, sca_date, band, band_label, holders, shares, pct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            stock_id, sca_date, r['band'], r['band_label'],
            r.get('holders'), r.get('shares'), r.get('pct'),
        ))


def get_latest_holder_date(conn):
    """取得 holder_distribution 最新一筆 sca_date"""
    row = conn.execute("SELECT MAX(sca_date) AS d FROM holder_distribution").fetchone()
    return row['d'] if row and row['d'] else None


def upsert_notice(conn, rec):
    """寫入單筆注意股紀錄 (rec from scrapers.disposition.fetch_notice)"""
    if not rec.get('announce_date') or not rec.get('stock_id'):
        return
    conn.execute("""
        INSERT OR REPLACE INTO notice_announcements
        (announce_date, stock_id, name, cumulative, reason, close_price, is_real_stock, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        rec['announce_date'], rec['stock_id'], rec.get('name'),
        rec.get('cumulative'), rec.get('reason'), rec.get('close_price'),
        1 if rec.get('is_real_stock') else 0,
    ))


def upsert_disposition(conn, rec):
    """寫入單筆處置股紀錄 (rec from scrapers.disposition.fetch_punish)"""
    if not rec.get('announce_date') or not rec.get('stock_id'):
        return
    conn.execute("""
        INSERT OR REPLACE INTO disposition_announcements
        (announce_date, stock_id, name, cumulative, condition,
         period_start, period_end, action, content, is_real_stock, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        rec['announce_date'], rec['stock_id'], rec.get('name'),
        rec.get('cumulative'), rec.get('condition'),
        rec.get('period_start'), rec.get('period_end'),
        rec.get('action'), rec.get('content'),
        1 if rec.get('is_real_stock') else 0,
    ))


def get_trading_dates(conn, limit=240):
    """取得最近 N 個交易日日期列表"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_prices ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [r['date'] for r in rows]


def get_breakouts_by_date(conn, date, market=None):
    """取得某日所有突破紀錄"""
    sql = """
        SELECT b.*, s.name, s.market, dp.volume
        FROM breakouts b
        JOIN stocks s ON s.stock_id = b.stock_id
        LEFT JOIN daily_prices dp ON dp.stock_id = b.stock_id AND dp.date = b.date
        WHERE b.date = ?
          AND (b.break_5=1 OR b.break_10=1 OR b.break_20=1
               OR b.break_60=1 OR b.break_120=1 OR b.break_240=1)
    """
    params = [date]
    if market and market != 'all':
        sql += " AND s.market = ?"
        params.append(market)
    sql += " ORDER BY b.change_pct DESC"
    return conn.execute(sql, params).fetchall()


def get_institutional_ranking(conn, inst_type, days, date, market=None, limit=50):
    """
    取得法人買賣超排行。
    inst_type: 'foreign' | 'sitc' | 'dealer' | 'total'
    days: 累積天數 (1, 5, 10, 30)
    回傳買超 Top N 和賣超 Top N。
    """
    ALLOWED_COLUMNS = {'foreign_buy', 'sitc_buy', 'dealer_buy', 'total_buy'}
    col_map = {
        'foreign': 'foreign_buy',
        'sitc': 'sitc_buy',
        'dealer': 'dealer_buy',
        'total': 'total_buy',
    }
    col = col_map.get(inst_type, 'foreign_buy')
    if col not in ALLOWED_COLUMNS:
        raise ValueError(f"Invalid institutional column: {col}")

    # 從 institutional 表取基準日往回 N 個交易日
    dates = [r['date'] for r in conn.execute(
        "SELECT DISTINCT date FROM institutional WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (date, days)
    ).fetchall()]
    if not dates:
        return [], []

    placeholders = ','.join(['?'] * len(dates))

    market_filter = ""
    params_base = list(dates)
    if market and market != 'all':
        market_filter = "AND s.market = ?"
        params_base.append(market)

    # 買超排行
    buy_sql = f"""
        SELECT s.stock_id, s.name, s.market,
               SUM(i.{col}) as total_amount,
               dp.close_price, dp.change_pct
        FROM institutional i
        JOIN stocks s ON s.stock_id = i.stock_id
        LEFT JOIN daily_prices dp ON dp.stock_id = i.stock_id AND dp.date = ?
        WHERE i.date IN ({placeholders})
        {market_filter}
        GROUP BY s.stock_id
        HAVING total_amount > 0
        ORDER BY total_amount DESC
        LIMIT ?
    """
    buy_params = [date] + params_base + [limit]
    buy_rows = conn.execute(buy_sql, buy_params).fetchall()

    # 賣超排行
    sell_sql = f"""
        SELECT s.stock_id, s.name, s.market,
               SUM(i.{col}) as total_amount,
               dp.close_price, dp.change_pct
        FROM institutional i
        JOIN stocks s ON s.stock_id = i.stock_id
        LEFT JOIN daily_prices dp ON dp.stock_id = i.stock_id AND dp.date = ?
        WHERE i.date IN ({placeholders})
        {market_filter}
        GROUP BY s.stock_id
        HAVING total_amount < 0
        ORDER BY total_amount ASC
        LIMIT ?
    """
    sell_params = [date] + params_base + [limit]
    sell_rows = conn.execute(sell_sql, sell_params).fetchall()

    return buy_rows, sell_rows


def get_broker_trades(conn, stock_id, date):
    """
    取得個股券商分點進出資料。
    回傳: (buy_rows, sell_rows)
    buy_rows: net_volume > 0, 依 net_volume DESC, limit 15
    sell_rows: net_volume < 0, 依 net_volume ASC, limit 15
    """
    buy_rows = conn.execute("""
        SELECT broker_name, buy_volume, sell_volume, net_volume, pct
        FROM broker_trades
        WHERE stock_id = ? AND date = ? AND net_volume > 0
        ORDER BY net_volume DESC
        LIMIT 15
    """, (stock_id, date)).fetchall()

    sell_rows = conn.execute("""
        SELECT broker_name, buy_volume, sell_volume, net_volume, pct
        FROM broker_trades
        WHERE stock_id = ? AND date = ? AND net_volume < 0
        ORDER BY net_volume ASC
        LIMIT 15
    """, (stock_id, date)).fetchall()

    return buy_rows, sell_rows


def get_latest_date(conn):
    """取得資料庫中最新的交易日"""
    row = conn.execute("SELECT MAX(date) as d FROM daily_prices").fetchone()
    return row['d'] if row else None


def upsert_regime(conn, date, recon_error, tau, regime):
    """插入或更新 regime 記錄。"""
    conn.execute("""
        INSERT INTO regime_history (date, recon_error, tau, regime)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            recon_error=excluded.recon_error,
            tau=excluded.tau,
            regime=excluded.regime,
            updated_at=CURRENT_TIMESTAMP
    """, (date, recon_error, tau, regime))
    conn.commit()


def get_regime_history(conn, limit=120):
    """取得最近 N 筆 regime 歷史。"""
    rows = conn.execute("""
        SELECT date, recon_error, tau, regime
        FROM regime_history
        ORDER BY date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return rows


def add_to_watchlist(conn, stock_id):
    conn.execute("INSERT OR IGNORE INTO watchlist (stock_id) VALUES (?)", (stock_id,))
    conn.commit()


def remove_from_watchlist(conn, stock_id):
    conn.execute("DELETE FROM watchlist WHERE stock_id = ?", (stock_id,))
    conn.commit()


def get_watchlist(conn):
    """Get watchlist with latest prices"""
    return conn.execute("""
        SELECT w.stock_id, s.name, s.market, dp.close_price, dp.change_pct, dp.volume,
               w.added_at
        FROM watchlist w
        JOIN stocks s ON s.stock_id = w.stock_id
        LEFT JOIN daily_prices dp ON dp.stock_id = w.stock_id
            AND dp.date = (SELECT MAX(date) FROM daily_prices)
        ORDER BY w.added_at DESC
    """).fetchall()


def is_in_watchlist(conn, stock_id):
    row = conn.execute("SELECT 1 FROM watchlist WHERE stock_id = ?", (stock_id,)).fetchone()
    return row is not None


def get_latest_regime(conn):
    """取得最新一筆 regime。"""
    row = conn.execute("""
        SELECT date, recon_error, tau, regime
        FROM regime_history
        ORDER BY date DESC
        LIMIT 1
    """).fetchone()
    return row


# ===== Credit Spread =====

def upsert_credit_spread(conn, date, hyg_shy_ratio, indicator_value, signal, spy_close=0, trend5d=0):
    conn.execute("""
        INSERT INTO credit_spread_history (date, hyg_shy_ratio, indicator_value, signal, spy_close, trend5d)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            hyg_shy_ratio=excluded.hyg_shy_ratio,
            indicator_value=excluded.indicator_value,
            signal=excluded.signal,
            spy_close=excluded.spy_close,
            trend5d=excluded.trend5d,
            updated_at=CURRENT_TIMESTAMP
    """, (date, hyg_shy_ratio, indicator_value, signal, spy_close, trend5d))


def get_credit_spread_history(conn, limit=500):
    rows = conn.execute("""
        SELECT date, hyg_shy_ratio, indicator_value, signal, spy_close, trend5d
        FROM credit_spread_history
        ORDER BY date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return rows


# ===== CTA Signal (SPY Lasso 中頻) =====

def upsert_cta_signal(conn, date, close, signal_raw, raw_pos, position):
    conn.execute("""
        INSERT INTO cta_signal_history (date, close, signal_raw, raw_pos, position)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            close=excluded.close,
            signal_raw=excluded.signal_raw,
            raw_pos=excluded.raw_pos,
            position=excluded.position,
            updated_at=CURRENT_TIMESTAMP
    """, (date, close, signal_raw, raw_pos, position))


def get_cta_signal_history(conn, limit=500):
    """取最近 N 筆,DESC 排序。"""
    rows = conn.execute("""
        SELECT date, close, signal_raw, raw_pos, position
        FROM cta_signal_history
        ORDER BY date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return rows


def get_cta_signal_all(conn):
    """取全部,ASC 排序(供回測/勝率用)。"""
    rows = conn.execute("""
        SELECT date, close, signal_raw, raw_pos, position
        FROM cta_signal_history
        ORDER BY date ASC
    """).fetchall()
    return rows


# ===== Macro Indicators =====

def upsert_macro(conn, date, indicator, value, signal='NEUTRAL'):
    conn.execute("""
        INSERT INTO macro_indicators (date, indicator, value, signal)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, indicator) DO UPDATE SET
            value=excluded.value,
            signal=excluded.signal,
            updated_at=CURRENT_TIMESTAMP
    """, (date, indicator, value, signal))


def get_macro_latest(conn, indicator):
    row = conn.execute("""
        SELECT date, value, signal FROM macro_indicators
        WHERE indicator = ?
        ORDER BY date DESC LIMIT 1
    """, (indicator,)).fetchone()
    return row


def get_macro_history(conn, indicator, limit=500):
    rows = conn.execute("""
        SELECT date, value, signal FROM macro_indicators
        WHERE indicator = ?
        ORDER BY date DESC LIMIT ?
    """, (indicator, limit)).fetchall()
    return rows
