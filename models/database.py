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

        CREATE TABLE IF NOT EXISTS intraday_snapshot (
            stock_id    TEXT NOT NULL,
            snapshot_ts TIMESTAMP NOT NULL,
            cum_volume  INTEGER NOT NULL,
            last_price  REAL,
            PRIMARY KEY (stock_id, snapshot_ts)
        );
        CREATE INDEX IF NOT EXISTS idx_intraday_snapshot_ts ON intraday_snapshot(snapshot_ts);

        CREATE TABLE IF NOT EXISTS volume_anomaly_cache (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            payload    TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS taiex_trend (
            snapshot_ts        TEXT PRIMARY KEY,
            minute_idx         INTEGER,
            rvol_forecast      REAL,
            forecast_eod_value REAL,
            level              TEXT,
            ci_low             REAL,
            ci_high            REAL
        );

        CREATE TABLE IF NOT EXISTS te_tf_strength_intraday (
            date        TEXT NOT NULL,
            ts          TEXT NOT NULL,
            strength    REAL NOT NULL,
            te_chg_pct  REAL NOT NULL,
            tf_chg_pct  REAL NOT NULL,
            te_close    REAL,
            tf_close    REAL,
            base_source TEXT,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_te_tf_intraday_date ON te_tf_strength_intraday(date);

        CREATE TABLE IF NOT EXISTS te_tf_strength_history (
            date           TEXT PRIMARY KEY,
            strength_close REAL NOT NULL,
            te_chg_pct     REAL NOT NULL,
            tf_chg_pct     REAL NOT NULL,
            strength_high  REAL,
            strength_low   REAL,
            updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS option_daily (
            date        TEXT    NOT NULL,               -- ISO 'YYYY-MM-DD'
            contract    TEXT    NOT NULL,               -- '202607W1' / '202607F1' / '202607'(月選=YYYYMM)
            strike      REAL    NOT NULL,               -- 履約價
            cp          TEXT    NOT NULL CHECK(cp IN ('C','P')),
            close       REAL,                           -- 收盤價（'-'→NULL）
            settlement  REAL,                           -- 結算價
            change      REAL,                           -- 漲跌價
            change_pct  REAL,                           -- 漲跌%（去 % 後 float）
            volume      INTEGER DEFAULT 0,              -- 成交量
            oi          INTEGER DEFAULT 0,              -- 未沖銷契約數（OI）
            expiry      TEXT,                           -- 契約到期日 'YYYYMMDD'
            updated_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, contract, strike, cp)    -- 唯一鍵，避免重複列
        );
        CREATE INDEX IF NOT EXISTS idx_option_daily_date          ON option_daily(date);
        CREATE INDEX IF NOT EXISTS idx_option_daily_date_contract ON option_daily(date, contract);

        -- 期貨大額交易人未沖銷部位（僅「到期月份 999999 = 所有月份合計」層級）
        CREATE TABLE IF NOT EXISTS futures_large_trader (
            date         TEXT    NOT NULL,           -- ISO 'YYYY-MM-DD'
            product_code TEXT    NOT NULL,           -- 期貨商品代碼 'CD'(台積電) / 'QF'(小型台積電)
            trader_type  INTEGER NOT NULL,           -- 0=整體十大(含造市者) 1=特定法人
            top5_buy     INTEGER DEFAULT 0,          -- 前五大交易人買方部位(口)
            top5_sell    INTEGER DEFAULT 0,          -- 前五大交易人賣方部位(口)
            top10_buy    INTEGER DEFAULT 0,          -- 前十大交易人買方部位(口)
            top10_sell   INTEGER DEFAULT 0,          -- 前十大交易人賣方部位(口)
            market_oi    INTEGER DEFAULT 0,          -- 全市場未沖銷部位數(口)
            updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, product_code, trader_type)
        );
        CREATE INDEX IF NOT EXISTS idx_flt_date    ON futures_large_trader(date);
        CREATE INDEX IF NOT EXISTS idx_flt_product ON futures_large_trader(product_code, date);

        -- 股票期貨商品代碼 ↔ 標的股票對照（含大小型與「張/口」換算）
        CREATE TABLE IF NOT EXISTS stock_futures_map (
            product_code      TEXT PRIMARY KEY,      -- 'CD' / 'QF'
            stock_id          TEXT NOT NULL,         -- '2330'
            stock_name        TEXT,                  -- '台積電'
            product_name      TEXT,                  -- '台積電期貨' / '小型台積電期貨'
            is_mini           INTEGER DEFAULT 0,     -- 1=小型契約
            is_etf            INTEGER DEFAULT 0,     -- 1=標的為 ETF
            lots_per_contract REAL    DEFAULT 2,     -- 張/口：股票 2 / 小型股票 0.1 / ETF 10 / 小型ETF 1
            updated_at        TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sfm_stock ON stock_futures_map(stock_id);
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


def upsert_intraday_snapshot(conn, stock_id, snapshot_ts, cum_volume, last_price):
    """寫入盤中快照（保留歷史，供爆量預估使用）"""
    conn.execute("""
        INSERT OR REPLACE INTO intraday_snapshot
        (stock_id, snapshot_ts, cum_volume, last_price)
        VALUES (?, ?, ?, ?)
    """, (stock_id, snapshot_ts, cum_volume, last_price))


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


# ===== TE/TF 強弱指標 =====

def upsert_te_tf_intraday(conn, date, ts, strength, te_chg_pct, tf_chg_pct,
                          te_close, tf_close, base_source):
    """寫入 TE/TF 強弱盤中快照（僅 conn.execute，不自 commit）。"""
    conn.execute("""
        INSERT OR REPLACE INTO te_tf_strength_intraday
        (date, ts, strength, te_chg_pct, tf_chg_pct, te_close, tf_close, base_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, ts, strength, te_chg_pct, tf_chg_pct, te_close, tf_close, base_source))


def get_te_tf_intraday_series(conn, date):
    """取得某日 TE/TF 強弱盤中序列（ts 升冪）。"""
    rows = conn.execute("""
        SELECT ts, strength, te_chg_pct, tf_chg_pct
        FROM te_tf_strength_intraday
        WHERE date = ?
        ORDER BY ts ASC
    """, (date,)).fetchall()
    return rows


def upsert_te_tf_history(conn, date, strength_close, te_chg_pct, tf_chg_pct,
                         strength_high, strength_low):
    """寫入或更新 TE/TF 強弱日線收盤紀錄（僅 conn.execute，不自 commit）。"""
    conn.execute("""
        INSERT INTO te_tf_strength_history
        (date, strength_close, te_chg_pct, tf_chg_pct, strength_high, strength_low)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            strength_close=excluded.strength_close,
            te_chg_pct=excluded.te_chg_pct,
            tf_chg_pct=excluded.tf_chg_pct,
            strength_high=excluded.strength_high,
            strength_low=excluded.strength_low,
            updated_at=CURRENT_TIMESTAMP
    """, (date, strength_close, te_chg_pct, tf_chg_pct, strength_high, strength_low))


# ===== 選擇權支撐壓力（option_daily） =====

def upsert_option_daily(conn, rows):
    """
    寫入選擇權每日行情（僅 conn.execute，commit 由呼叫端負責）。
    rows: list[dict]（FN-001 fetch_txo_daily 輸出），每筆含
          {date, contract, strike, cp, close, settlement, change,
           change_pct, volume, oi, expiry}
    以 INSERT OR REPLACE 逐列寫入，唯一鍵 (date, contract, strike, cp) 防重複。
    回傳: int（寫入列數）
    """
    n = 0
    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO option_daily
            (date, contract, strike, cp, close, settlement, change,
             change_pct, volume, oi, expiry, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            r['date'], r['contract'], r['strike'], r['cp'],
            r.get('close'), r.get('settlement'), r.get('change'),
            r.get('change_pct'), r.get('volume', 0), r.get('oi', 0),
            r.get('expiry'),
        ))
        n += 1
    return n


def upsert_large_trader(conn, rows):
    """
    寫入期貨大額交易人未沖銷部位（僅 conn.execute，commit 由呼叫端負責）。
    rows: list[dict]（scrapers.taifex_large_trader.fetch_large_trader 輸出），每筆含
          {date, product_code, trader_type, top5_buy, top5_sell,
           top10_buy, top10_sell, market_oi}
    以 INSERT OR REPLACE 逐列寫入，唯一鍵 (date, product_code, trader_type)。
    回傳: int（寫入列數）
    """
    n = 0
    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO futures_large_trader
            (date, product_code, trader_type, top5_buy, top5_sell,
             top10_buy, top10_sell, market_oi, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            r['date'], r['product_code'], r['trader_type'],
            r.get('top5_buy', 0), r.get('top5_sell', 0),
            r.get('top10_buy', 0), r.get('top10_sell', 0),
            r.get('market_oi', 0),
        ))
        n += 1
    return n


def upsert_stock_futures_map(conn, rows):
    """
    寫入股票期貨商品代碼對照表（僅 conn.execute，commit 由呼叫端負責）。
    rows: list[dict]（scrapers.taifex_stock_futures.fetch_stock_futures_list 輸出）。
    回傳: int（寫入列數）
    """
    n = 0
    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO stock_futures_map
            (product_code, stock_id, stock_name, product_name,
             is_mini, is_etf, lots_per_contract, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            r['product_code'], r['stock_id'], r.get('stock_name'),
            r.get('product_name'), r.get('is_mini', 0), r.get('is_etf', 0),
            r.get('lots_per_contract', 2.0),
        ))
        n += 1
    return n


def get_option_dates(conn, limit=250):
    """取得 option_daily 有資料的日期列表（DISTINCT date，DESC）。"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM option_daily ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [r['date'] for r in rows]


def get_option_contracts(conn, date):
    """
    取得某日出現的所有契約（DISTINCT），依 expiry 升冪。
    回傳: list[dict] {contract, expiry}（供前端下拉）。
    """
    rows = conn.execute("""
        SELECT contract, MAX(expiry) AS expiry
        FROM option_daily
        WHERE date = ?
        GROUP BY contract
        ORDER BY expiry ASC
    """, (date,)).fetchall()
    return [{'contract': r['contract'], 'expiry': r['expiry']} for r in rows]


def get_option_rows(conn, date, contract):
    """
    取得某日某契約的全部 strike/cp 列（含 close/change/change_pct/volume/oi）。
    回傳: list[sqlite3.Row]，依 strike、cp 升冪。
    """
    rows = conn.execute("""
        SELECT date, contract, strike, cp, close, settlement, change,
               change_pct, volume, oi, expiry
        FROM option_daily
        WHERE date = ? AND contract = ?
        ORDER BY strike ASC, cp ASC
    """, (date, contract)).fetchall()
    return rows


def get_latest_option_date(conn):
    """取得 option_daily 最新一筆有資料的日期。"""
    row = conn.execute("SELECT MAX(date) AS d FROM option_daily").fetchone()
    return row['d'] if row and row['d'] else None
