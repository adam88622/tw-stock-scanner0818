"""
volume_alert package — slim DB module
只保留 volume_alert 需要的表：stocks / daily_prices / intraday_snapshot / volume_anomaly_cache / taiex_trend
"""
import sqlite3
import os
import logging
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def db_connection(auto_commit=False):
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
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            market     TEXT NOT NULL CHECK(market IN ('twse', 'tpex')),
            sector     TEXT DEFAULT ''
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
            PRIMARY KEY (stock_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date);

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
    """)
    conn.commit()
    conn.close()
    logger.info("資料庫初始化完成")


def upsert_stock(conn, stock_id, name, market):
    conn.execute("""
        INSERT INTO stocks (stock_id, name, market, sector) VALUES (?, ?, ?, '')
        ON CONFLICT(stock_id) DO UPDATE SET name=excluded.name, market=excluded.market
    """, (stock_id, name, market))


def upsert_daily_price(conn, stock_id, date, open_price, high_price, low_price,
                       close_price, volume, trade_value, change_pct):
    conn.execute("""
        INSERT OR REPLACE INTO daily_prices
        (stock_id, date, open_price, high_price, low_price, close_price, volume, trade_value, change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (stock_id, date, open_price, high_price, low_price, close_price, volume, trade_value, change_pct))


def upsert_intraday_snapshot(conn, stock_id, snapshot_ts, cum_volume, last_price):
    conn.execute("""
        INSERT OR REPLACE INTO intraday_snapshot
        (stock_id, snapshot_ts, cum_volume, last_price)
        VALUES (?, ?, ?, ?)
    """, (stock_id, snapshot_ts, cum_volume, last_price))
