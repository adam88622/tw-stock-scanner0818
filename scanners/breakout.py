"""
Phase 1：N 日高點突破掃描邏輯
判定：今日收盤價 > 過去 N 個交易日最高價（不含今日）
"""
import logging
from config import BREAKOUT_DAYS
from models.database import upsert_breakout, get_trading_dates

logger = logging.getLogger(__name__)


def scan_breakouts(conn, date):
    """
    對所有股票執行突破掃描。
    date: ISO 格式 'YYYY-MM-DD'
    回傳: 突破股票數
    """
    # 取得所有交易日（含今日）
    all_dates = get_trading_dates(conn, 300)
    if date not in all_dates:
        logger.warning(f"日期 {date} 不在資料庫中")
        return 0

    # 取出今日有收盤價的所有股票
    today_rows = conn.execute("""
        SELECT dp.stock_id, dp.close_price, dp.change_pct
        FROM daily_prices dp
        WHERE dp.date = ?
        AND dp.close_price IS NOT NULL
    """, (date,)).fetchall()

    if not today_rows:
        logger.warning(f"日期 {date} 無收盤資料")
        return 0

    # 計算每個窗口需要的交易日
    # all_dates 是降序排列的
    date_idx = all_dates.index(date)
    max_window = max(BREAKOUT_DAYS)

    breakout_count = 0

    for row in today_rows:
        stock_id = row['stock_id']
        close_price = row['close_price']
        change_pct = row['change_pct']

        breaks = {}
        has_any = False

        for n in BREAKOUT_DAYS:
            # 取過去 N 個交易日（不含今日）
            past_dates = all_dates[date_idx + 1: date_idx + 1 + n]
            if len(past_dates) < n:
                breaks[n] = 0
                continue

            placeholders = ','.join(['?'] * len(past_dates))
            result = conn.execute(f"""
                SELECT MAX(high_price) as max_high
                FROM daily_prices
                WHERE stock_id = ?
                AND date IN ({placeholders})
                AND high_price IS NOT NULL
            """, [stock_id] + past_dates).fetchone()

            max_high = result['max_high'] if result else None

            if max_high is not None and close_price > max_high:
                breaks[n] = 1
                has_any = True
            else:
                breaks[n] = 0

        if has_any:
            upsert_breakout(conn, stock_id, date, breaks, close_price, change_pct)
            breakout_count += 1

    conn.commit()
    logger.info(f"突破掃描完成: {date}，共 {breakout_count} 檔突破")
    return breakout_count
