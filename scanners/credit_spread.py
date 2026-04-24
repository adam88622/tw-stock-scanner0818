"""
Credit Spread Traffic Light scanner.

Downloads HYG/SHY/SPY from Yahoo Finance, computes the credit spread
indicator (189-day rolling percentile rank of HYG/SHY ratio, inverted),
and stores daily signal history in the DB.
"""

import warnings
import logging
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

LOOKBACK = 189
THRESHOLD = 0.3
YELLOW_LOW = 0.28
YELLOW_HIGH = 0.32


def compute_credit_spread(start_date='2007-01-01'):
    """Fetch data and compute the full credit spread indicator series.

    Returns dict with keys: prices, ratio, indicator, signal_shifted, backtest.
    """
    warnings.filterwarnings('ignore')

    tickers = ['SPY', 'HYG', 'SHY']
    raw = yf.download(tickers, start=start_date, auto_adjust=True, progress=False)

    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw['Close']
    else:
        prices = raw[['Close']].rename(columns={'Close': tickers[0]})

    if prices.index.tz is not None:
        prices.index = prices.index.tz_localize(None)

    prices = prices.ffill().dropna()

    if len(prices) < LOOKBACK + 10:
        raise ValueError(f"Not enough data: {len(prices)} rows (need {LOOKBACK + 10})")

    # HYG/SHY ratio
    ratio = prices['HYG'] / prices['SHY']

    # Rolling percentile rank, inverted (0=safe, 1=danger)
    ranked = ratio.rolling(LOOKBACK).rank(pct=True)
    indicator = 1.0 - ranked

    # Signal with directional yellow (5D trend in buffer zone)
    delta5 = indicator.diff(5)
    signal_raw = pd.Series('', index=indicator.index, dtype=object)
    prev_pos = 0.0
    for i in range(len(indicator)):
        v = indicator.iloc[i]
        d = delta5.iloc[i] if pd.notna(delta5.iloc[i]) else 0
        if pd.isna(v):
            signal_raw.iloc[i] = None
            continue
        if v < YELLOW_LOW:
            # Clear GREEN zone, but check if trending toward RED
            if d > 0 and v > YELLOW_LOW - YELLOW_LOW * 0.1:
                signal_raw.iloc[i] = 'YELLOW'
                # hold previous position
            else:
                signal_raw.iloc[i] = 'GREEN'
                prev_pos = 1.0
        elif v >= YELLOW_HIGH:
            # Clear RED zone, but check if trending toward GREEN
            if d < 0 and v < YELLOW_HIGH + YELLOW_HIGH * 0.1:
                signal_raw.iloc[i] = 'YELLOW'
            else:
                signal_raw.iloc[i] = 'RED'
                prev_pos = 0.0
        else:
            # Buffer zone: direction decides
            if d < 0:  # improving
                signal_raw.iloc[i] = 'GREEN'
                prev_pos = 1.0
            elif d > 0:  # worsening
                signal_raw.iloc[i] = 'RED'
                prev_pos = 0.0
            else:
                signal_raw.iloc[i] = 'YELLOW'

    signal_shifted = signal_raw.shift(1)

    # 5D trend for display
    trend5 = delta5

    return {
        'prices': prices,
        'trend5': trend5,
        'ratio': ratio,
        'indicator': indicator,
        'signal_shifted': signal_shifted,
    }


def update_credit_spread_db(conn):
    """Compute latest credit spread data and upsert into DB.

    Similar to update_regime_db: computes the last 30 days and upserts.
    On first run, backfills the full history.
    """
    from models.database import upsert_credit_spread, get_credit_spread_history

    # Check if DB has data to decide backfill vs incremental
    existing = get_credit_spread_history(conn, limit=1)
    if not existing:
        logger.info("Credit spread: first run, backfilling full history...")
        start = '2007-01-01'
    else:
        start = '2007-01-01'  # always compute full for correct rolling rank

    data = compute_credit_spread(start_date=start)
    indicator = data['indicator']
    ratio = data['ratio']
    signal = data['signal_shifted']
    spy_prices = data['prices']['SPY']
    trend5 = data['trend5']

    # Upsert last 30 days (or all if first run)
    n_upsert = len(indicator) if not existing else 30
    valid_idx = indicator.dropna().index[-n_upsert:]

    count = 0
    for dt in valid_idx:
        sig = signal.loc[dt] if dt in signal.index and pd.notna(signal.loc[dt]) else ''
        spy_close = float(spy_prices.loc[dt]) if dt in spy_prices.index else 0
        t5 = float(trend5.loc[dt]) if dt in trend5.index and pd.notna(trend5.loc[dt]) else 0
        if sig:
            upsert_credit_spread(
                conn,
                dt.strftime('%Y-%m-%d'),
                float(ratio.loc[dt]),
                float(indicator.loc[dt]),
                sig,
                spy_close,
                t5,
            )
            count += 1

    conn.commit()

    # Return current status for logging
    latest_dt = indicator.dropna().index[-1]
    latest_sig = signal.loc[latest_dt] if pd.notna(signal.loc[latest_dt]) else 'N/A'
    latest_val = float(indicator.loc[latest_dt])

    logger.info(f"Credit spread: upserted {count} rows, latest={latest_sig} ({latest_val:.4f})")

    return {
        'signal': latest_sig,
        'indicator_value': latest_val,
        'rows_upserted': count,
    }
