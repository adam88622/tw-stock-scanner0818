"""volume_alert package — slim config"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'db', 'volume_alert.db')

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://mis.twse.com.tw/stock/index.jsp',
    'X-Requested-With': 'XMLHttpRequest',
}
REQUEST_TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_RETRY_DELAY = 30

# 抓取多少天的歷史 daily_prices 作為 ADV20 / 異常比較基準
SEED_DAYS = 60

TWSE_DAILY_URL = 'https://www.twse.com.tw/exchangeReport/MI_INDEX'
TPEX_DAILY_URL = 'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php'
