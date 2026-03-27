"""
券商分點進出資料抓取模組
資料來源：富邦 e 券商
"""
import time
import logging
import requests
from bs4 import BeautifulSoup
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_RETRY, REQUEST_RETRY_DELAY

logger = logging.getLogger(__name__)

BROKER_URL = 'https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm'


def _safe_int(val):
    if val is None or val == '' or val == '--':
        return 0
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


def _safe_float(val):
    if val is None or val == '' or val == '--':
        return 0.0
    try:
        return float(str(val).replace(',', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _format_date_for_url(iso_date):
    """將 'YYYY-MM-DD' 轉為 'YYYY-M-D'"""
    parts = iso_date.split('-')
    return f"{parts[0]}-{int(parts[1])}-{int(parts[2])}"


def _parse_broker_html(html):
    """
    解析富邦券商分點 HTML（使用 BeautifulSoup）。
    每個 TR 有 10 個 TD：左 5 個是買超券商，右 5 個是賣超券商。
    欄位: 券商名(含<a>), 買進, 賣出, 買超/賣超, 佔成交比重
    回傳: list of dict
    """
    results = []

    try:
        soup = BeautifulSoup(html, 'html.parser')
    except Exception as e:
        logger.error(f"HTML 解析失敗: {e}")
        return results

    for tr in soup.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 10:
            continue

        # 檢查是否為資料行（含券商連結 class t4t1 或 zco0.djhtm 連結）
        tr_text = str(tr)
        if 't4t1' not in tr_text and 'zco0.djhtm' not in tr_text:
            continue

        left = tds[0:5]
        right = tds[5:10]

        for is_sell, side_tds in [(False, left), (True, right)]:
            # 從 <a> 提取券商名，fallback 到純文字
            a_tag = side_tds[0].find('a')
            if a_tag:
                broker_name = a_tag.get_text(strip=True)
            else:
                broker_name = side_tds[0].get_text(strip=True)

            if not broker_name or broker_name in ('買超券商', '賣超券商', '合計', ''):
                continue

            # Skip corrupted parse: broker name should not be all numeric or empty
            if broker_name.replace(',', '').replace('.', '').isdigit():
                logger.warning(f"Skipping corrupted broker name (all numeric): '{broker_name}'")
                continue

            buy_vol = _safe_int(side_tds[1].get_text(strip=True))
            sell_vol = _safe_int(side_tds[2].get_text(strip=True))
            net_vol = _safe_int(side_tds[3].get_text(strip=True))
            pct = _safe_float(side_tds[4].get_text(strip=True))

            # 右邊是賣超，net_volume 存為負數
            if is_sell and net_vol > 0:
                net_vol = -net_vol

            if buy_vol == 0 and sell_vol == 0:
                continue

            results.append({
                'broker_name': broker_name,
                'buy_volume': buy_vol,
                'sell_volume': sell_vol,
                'net_volume': net_vol,
                'pct': pct,
            })

    return results


def fetch_broker_data(conn, stock_id, date_str):
    """
    抓取單一股票的券商分點資料並存入資料庫。
    date_str: ISO 格式 'YYYY-MM-DD'
    回傳: 成功筆數
    """
    url_date = _format_date_for_url(date_str)

    for attempt in range(REQUEST_RETRY):
        try:
            resp = requests.get(
                BROKER_URL,
                params={'a': stock_id, 'e': url_date, 'f': url_date},
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            resp.encoding = 'big5'
            html = resp.text
            break
        except Exception as e:
            logger.warning(f"券商分點請求失敗 {stock_id} (第 {attempt+1} 次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(REQUEST_RETRY_DELAY)
            else:
                return 0

    all_data = _parse_broker_html(html)

    if not all_data:
        logger.warning(f"券商分點 {stock_id}: 解析後 0 筆資料（預期有資料）")
        return 0

    count = 0
    for row in all_data:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO broker_trades
                (stock_id, date, broker_name, buy_volume, sell_volume, net_volume, pct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                stock_id, date_str, row['broker_name'],
                row['buy_volume'], row['sell_volume'],
                row['net_volume'], row['pct'],
            ))
            count += 1
        except Exception as e:
            logger.error(f"寫入券商分點資料錯誤 {stock_id} {row['broker_name']}: {e}")
            continue

    return count


def fetch_all_brokers(conn, date_str):
    """
    抓取所有股票的券商分點資料。
    date_str: ISO 格式 'YYYY-MM-DD'
    """
    rows = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
    if not rows:
        logger.warning("資料庫中無股票資料")
        return

    stock_ids = [r['stock_id'] for r in rows]
    total = len(stock_ids)
    success = 0

    logger.info(f"開始抓取券商分點: {date_str}，共 {total} 檔")

    for i, stock_id in enumerate(stock_ids, 1):
        try:
            count = fetch_broker_data(conn, stock_id, date_str)
            if count > 0:
                success += 1
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"券商分點 {stock_id} 錯誤: {e}")

        if i % 100 == 0:
            logger.info(f"券商分點進度: {i}/{total} (成功={success})")

        time.sleep(0.5)

    logger.info(f"券商分點完成: {date_str}，成功={success}/{total}")
