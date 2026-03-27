"""
Phase 2：法人買賣超統計邏輯
資料已在 scraper 階段寫入 institutional 表，
此模組提供查詢/統計用的輔助函式。
"""
import logging
from models.database import get_institutional_ranking, get_trading_dates

logger = logging.getLogger(__name__)


def get_ranking(conn, inst_type='foreign', days=1, date=None, market=None, limit=50):
    """
    取得法人買賣超排行。
    inst_type: 'foreign' | 'sitc' | 'dealer' | 'total'
    days: 累積天數
    date: 基準日（預設最新交易日）
    market: 'twse' | 'tpex' | 'all'
    limit: Top N
    回傳: (buy_rows, sell_rows)
    """
    if date is None:
        dates = get_trading_dates(conn, 1)
        if not dates:
            return [], []
        date = dates[0]

    return get_institutional_ranking(conn, inst_type, days, date, market, limit)
