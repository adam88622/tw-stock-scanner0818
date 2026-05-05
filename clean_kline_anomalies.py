"""
清理 daily_prices 三類資料污染:

1. 「孤兒記錄」 — 首筆與次筆 gap > 60 天 (2015-02-26 那批垃圾)
2. 「未上市偽資料」 — 真實上市日之前的雜訊筆 (3135 那種 8/6-8/11 假交易)
3. 「OHLCV 跨日複製」 — 同股 OHLCV 完全相同出現在多個日期 (6020 那種)

策略:
  - 偵測每檔股票的「真實穩定起點」: 第一個日期 X 使得 [X, X+30] 區間內有 >= 5 筆紀錄
  - 刪除 X 之前的所有筆
  - 對於 X 之後仍有大 gap (>60d) 的筆, 進一步刪除孤兒
  - 對於 OHLCV 完全相同的筆, 保留最晚的(因為早筆通常是 "未上市快照"),刪較早的

用法:
  python clean_kline_anomalies.py            # dry-run (預設,只列印,不動 DB)
  python clean_kline_anomalies.py --apply    # 實際刪除
"""
import argparse
import logging
import sys
from collections import defaultdict
from datetime import date as dt
from models.database import get_conn

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def find_stable_start(records):
    """
    records: list[(date_str, ...)] 升冪
    找第一個日期 X 使得 [X, X+30 days] 區間內有 >= 5 筆紀錄。
    回傳該 date_str (找不到則回傳 records[0])
    """
    if len(records) < 5:
        return records[0][0] if records else None

    dates = [dt.fromisoformat(r[0]) for r in records]
    n = len(dates)
    for i in range(n):
        # 從第 i 個開始,30 天內有幾筆?
        cutoff = dates[i].toordinal() + 30
        cnt = sum(1 for d in dates[i:] if d.toordinal() <= cutoff)
        if cnt >= 5:
            return records[i][0]
    return records[0][0]


def find_orphans_after(records, threshold_days=60):
    """
    records: list[(date_str, ...)] 升冪
    回傳「孤兒」日期清單:該筆與前一筆 gap > threshold AND 與下一筆 gap > threshold
    """
    if len(records) < 3:
        return []
    out = []
    for i in range(1, len(records) - 1):
        d_cur = dt.fromisoformat(records[i][0])
        d_prev = dt.fromisoformat(records[i - 1][0])
        d_next = dt.fromisoformat(records[i + 1][0])
        if (d_cur - d_prev).days > threshold_days and (d_next - d_cur).days > threshold_days:
            out.append(records[i][0])
    # 邊界: 第一筆若與第二筆 gap 太大也算孤兒
    if len(records) >= 2:
        d0, d1 = dt.fromisoformat(records[0][0]), dt.fromisoformat(records[1][0])
        if (d1 - d0).days > threshold_days:
            out.insert(0, records[0][0])
    return out


def find_ohlcv_duplicates(records, min_dup_count=3, min_span_days=30):
    """
    records: list[(date, open, high, low, close, volume)] 升冪
    保守規則: 同 OHLCV + 同 vol 必須 ≥ min_dup_count 筆,且跨 ≥ min_span_days 才算複製。
    刪「除最晚日期外」其他筆。
    """
    groups = defaultdict(list)
    for r in records:
        key = (r[1], r[2], r[3], r[4], r[5])
        if key[4] is None or key[4] == 0:
            continue
        groups[key].append(r[0])

    to_delete = []
    for key, dates in groups.items():
        if len(dates) < min_dup_count:
            continue
        dates_sorted = sorted(dates)
        d_first = dt.fromisoformat(dates_sorted[0])
        d_last = dt.fromisoformat(dates_sorted[-1])
        if (d_last - d_first).days < min_span_days:
            continue  # 連續幾天偶爾巧合,跳過
        to_delete.extend(dates_sorted[:-1])
    return to_delete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='實際刪除(預設只 dry-run)')
    parser.add_argument('--limit-stocks', type=int, default=None,
                        help='只處理前 N 檔(測試用)')
    args = parser.parse_args()

    mode = 'APPLY' if args.apply else 'DRY-RUN'
    logger.info(f"=== K 線清理 ({mode}) ===")

    with get_conn() as conn:
        # 全部股票清單
        sql = "SELECT DISTINCT stock_id FROM daily_prices ORDER BY stock_id"
        if args.limit_stocks:
            sql += f" LIMIT {args.limit_stocks}"
        stocks = [r['stock_id'] for r in conn.execute(sql).fetchall()]
        logger.info(f"共 {len(stocks)} 檔股票")

    deletes = []  # list of (stock_id, date, reason)
    affected_stocks = set()

    with get_conn() as conn:
        for sid in stocks:
            rows = conn.execute("""
                SELECT date, open_price, high_price, low_price, close_price, volume
                FROM daily_prices WHERE stock_id = ? ORDER BY date ASC
            """, (sid,)).fetchall()
            if len(rows) < 2:
                continue

            simple = [(r['date'], r['open_price'], r['high_price'], r['low_price'],
                       r['close_price'], r['volume']) for r in rows]

            # Step 1: 找穩定起點,刪之前所有筆
            stable_start = find_stable_start(simple)
            if stable_start and stable_start != simple[0][0]:
                # 真實起點不是最早一筆 → 刪 stable_start 之前的
                for r in simple:
                    if r[0] < stable_start:
                        deletes.append((sid, r[0], 'pre_ipo_orphan'))
                        affected_stocks.add(sid)
                # 重組 records 為穩定起點之後的
                simple = [r for r in simple if r[0] >= stable_start]

            if len(simple) < 2:
                continue

            # Step 2: 找穩定起點之後的「中段孤兒」(極少見,但保險)
            orphans = find_orphans_after(simple, threshold_days=60)
            for d in orphans:
                # 確保未在 step 1 已標記
                if not any(t for t in deletes if t[0] == sid and t[1] == d):
                    deletes.append((sid, d, 'mid_orphan'))
                    affected_stocks.add(sid)
            simple = [r for r in simple if r[0] not in set(orphans)]

            # Step 3: OHLCV 跨日複製
            dup_dates = find_ohlcv_duplicates(simple)
            for d in dup_dates:
                if not any(t for t in deletes if t[0] == sid and t[1] == d):
                    deletes.append((sid, d, 'ohlcv_dup'))
                    affected_stocks.add(sid)

    # 統計
    logger.info(f"\n=== 預計刪除 {len(deletes)} 筆,影響 {len(affected_stocks)} 檔股票 ===")
    by_reason = defaultdict(int)
    for _, _, reason in deletes:
        by_reason[reason] += 1
    for reason, n in by_reason.items():
        logger.info(f"  {reason}: {n} 筆")

    # 範例(每類顯示前 10)
    print()
    samples = defaultdict(list)
    for sid, d, reason in deletes:
        if len(samples[reason]) < 10:
            samples[reason].append((sid, d))
    for reason, items in samples.items():
        print(f"  [{reason}] 範例:")
        for sid, d in items:
            print(f"    {sid} {d}")

    if not args.apply:
        print()
        logger.info("DRY-RUN 結束。確認後加 --apply 實際刪除。")
        return

    # 實際刪除
    logger.info("\n=== 開始刪除 ===")
    deleted = 0
    with get_conn() as conn:
        try:
            for sid, d, _ in deletes:
                conn.execute(
                    "DELETE FROM daily_prices WHERE stock_id = ? AND date = ?",
                    (sid, d),
                )
                deleted += 1
                if deleted % 100 == 0:
                    logger.info(f"  已刪 {deleted}/{len(deletes)}")
            conn.commit()
            logger.info(f"=== 完成: 已刪 {deleted} 筆 ===")
        except Exception as e:
            conn.rollback()
            logger.error(f"刪除失敗,已 rollback: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
