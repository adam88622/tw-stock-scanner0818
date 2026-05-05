"""
exp05_tradingagents_adapter.py — TradingAgents TW data adapter (PoC)
Reference: TauricResearch/TradingAgents (arXiv 2412.20138, Apache-2.0)
News context: GitHub trending around 2026-05-03 (TauricResearch's repo).

Goal:
  Provide a TW-data-shaped adapter so that TradingAgents' multi-agent loop
  can consume tw-stock-scanner data instead of Alpha Vantage / yfinance.

Adapter responsibilities:
  - market_data(stock_id, date_range)  -> OHLCV dataframe
  - technical_signals(stock_id, date)  -> MACD/RSI/MA snapshot
  - institutional(stock_id, date)      -> 三大法人買賣超
  - broker_top(stock_id, date)         -> 主力分點
  - regime(date)                       -> 大盤狀態 (regime_history)

This file does NOT depend on the actual TradingAgents framework yet — it
provides the data-layer functions that an adapter would call. To test, run
this module directly to print sample outputs for stock 2330 on a recent date.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
import pandas as pd

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"


@dataclass
class TWStockSnapshot:
    stock_id: str
    asof: str
    ohlcv_60d: pd.DataFrame
    macd_hist: float
    rsi_14: float
    ma_ratio: dict
    inst_5d: dict
    broker_concentration: float | None
    regime: str | None


def market_data(stock_id, start, end):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """SELECT date, open_price o, high_price h, low_price l,
                  close_price c, volume v, trade_value
           FROM daily_prices
           WHERE stock_id = ? AND date BETWEEN ? AND ?
           ORDER BY date""",
        con, params=(stock_id, start, end), parse_dates=['date'])
    con.close()
    return df


def technical_signals(df):
    c = df.c
    ema_f = c.ewm(span=12, adjust=False).mean()
    ema_s = c.ewm(span=26, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=9, adjust=False).mean()
    hist = (macd - sig).iloc[-1]
    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    ma = {n: float(c.iloc[-1] / c.rolling(n).mean().iloc[-1] - 1)
          for n in (5, 20, 60) if len(c) >= n}
    return float(hist), float(rsi), ma


def institutional(stock_id, asof, lookback=5):
    con = sqlite3.connect(DB)
    end = asof
    start = (pd.to_datetime(asof) - timedelta(days=15)).date().isoformat()
    df = pd.read_sql_query(
        """SELECT date, foreign_buy, sitc_buy, dealer_buy, total_buy
           FROM institutional WHERE stock_id = ? AND date BETWEEN ? AND ?
           ORDER BY date DESC LIMIT ?""",
        con, params=(stock_id, start, end, lookback))
    con.close()
    if df.empty:
        return {}
    return dict(
        foreign_5d=int(df.foreign_buy.sum()),
        sitc_5d=int(df.sitc_buy.sum()),
        dealer_5d=int(df.dealer_buy.sum()),
        total_5d=int(df.total_buy.sum()),
        days=len(df),
    )


def broker_concentration(stock_id, asof):
    """Top-5 broker net volume share of total broker volume (last available date)."""
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """SELECT broker_name, net_volume, buy_volume, sell_volume
           FROM broker_trades WHERE stock_id = ? AND date = ?
           ORDER BY ABS(net_volume) DESC LIMIT 5""",
        con, params=(stock_id, asof))
    tot = pd.read_sql_query(
        """SELECT SUM(buy_volume + sell_volume) t
           FROM broker_trades WHERE stock_id = ? AND date = ?""",
        con, params=(stock_id, asof))
    con.close()
    if df.empty or tot.empty or pd.isna(tot.t.iloc[0]) or tot.t.iloc[0] == 0:
        return None
    top5_vol = (df.buy_volume + df.sell_volume).sum()
    return float(top5_vol / tot.t.iloc[0])


def regime_state(asof):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """SELECT regime FROM regime_history WHERE date <= ?
           ORDER BY date DESC LIMIT 1""",
        con, params=(asof,))
    con.close()
    if df.empty:
        return None
    return df.regime.iloc[0]


def build_snapshot(stock_id, asof):
    end = asof
    start = (pd.to_datetime(asof) - timedelta(days=120)).date().isoformat()
    df = market_data(stock_id, start, end)
    if df.empty or len(df) < 30:
        return None
    df = df.tail(60).reset_index(drop=True)
    hist, rsi, ma = technical_signals(df)
    inst = institutional(stock_id, asof)
    bc = broker_concentration(stock_id, asof)
    rg = regime_state(asof)
    return TWStockSnapshot(
        stock_id=stock_id,
        asof=asof,
        ohlcv_60d=df[['date', 'o', 'h', 'l', 'c', 'v']],
        macd_hist=hist,
        rsi_14=rsi,
        ma_ratio=ma,
        inst_5d=inst,
        broker_concentration=bc,
        regime=rg,
    )


def render_for_llm(s: TWStockSnapshot) -> str:
    """Serialize snapshot into the kind of context block a TradingAgents
    analyst agent would consume.
    """
    last = s.ohlcv_60d.iloc[-1]
    out = [
        f"# Stock {s.stock_id} as of {s.asof}",
        f"Last close: {last.c:.2f}  Volume: {last.v:,}  60d high/low: {s.ohlcv_60d.h.max():.2f}/{s.ohlcv_60d.l.min():.2f}",
        f"Technical: MACD hist={s.macd_hist:+.3f}  RSI14={s.rsi_14:.1f}",
        f"  MA deviation: 5d={s.ma_ratio.get(5, 0):+.2%}  20d={s.ma_ratio.get(20, 0):+.2%}  60d={s.ma_ratio.get(60, 0):+.2%}",
        f"Institutional 5d net: foreign={s.inst_5d.get('foreign_5d', 'NA')}  sitc={s.inst_5d.get('sitc_5d', 'NA')}  dealer={s.inst_5d.get('dealer_5d', 'NA')}",
        f"Broker concentration: {'%.2f' % s.broker_concentration if s.broker_concentration else 'NA'}",
        f"Market regime: {s.regime or 'NA'}",
    ]
    return "\n".join(out)


if __name__ == '__main__':
    import json
    samples = [('2330', '2026-04-30'), ('2317', '2026-04-30'),
               ('2454', '2026-04-30'), ('0050', '2026-04-30')]
    print("=== TradingAgents TW data adapter PoC ===\n")
    for sid, dt in samples:
        s = build_snapshot(sid, dt)
        if s is None:
            print(f"[{sid}] no data\n")
            continue
        print(render_for_llm(s))
        print()
    # Verify adapter coverage stats
    print("=== Adapter coverage ===")
    con = sqlite3.connect(DB)
    cov = pd.read_sql_query(
        """SELECT
              (SELECT COUNT(*) FROM daily_prices WHERE date='2026-04-30') as price,
              (SELECT COUNT(*) FROM institutional WHERE date='2026-04-30') as inst,
              (SELECT COUNT(*) FROM broker_trades WHERE date='2026-04-30') as broker,
              (SELECT COUNT(*) FROM regime_history WHERE date<='2026-04-30') as regime""",
        con)
    con.close()
    print(cov.to_string(index=False))
