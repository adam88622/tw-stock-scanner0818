"""
Backfill TDCC 集保戶股權分散表

用法:
  python backfill_tdcc.py                    # 抓最新一週,所有有 daily_prices 的股票
  python backfill_tdcc.py --weeks 12         # 抓最近 12 週(從最新往回)
  python backfill_tdcc.py --stock 2330       # 只抓單支股票(全部歷史)
  python backfill_tdcc.py --workers 4        # 平行 worker 數(預設 4)
  python backfill_tdcc.py --resume           # 跳過已存在的 (stock_id, sca_date)

注意:
- TDCC 對 IP 沒明顯 rate limit,但建議 throttle 0.2s/req,4 平行 ≈ 20 req/s
- 全市場 (~2000 股) × 1 週 ≈ 8 分鐘 (4 workers)
- 每股一次 form_state 重用 token,避免每次重新 GET
"""
import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from models.database import get_conn, upsert_holder_distribution
from scrapers.tdcc import (
    make_session, fetch_form_state, fetch_holder_distribution
)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def get_target_stocks(stock_id=None):
    """取要抓的股票清單"""
    with get_conn() as conn:
        if stock_id:
            row = conn.execute(
                "SELECT stock_id, name FROM stocks WHERE stock_id = ?",
                (stock_id,)
            ).fetchone()
            return [dict(row)] if row else []
        # 全部:有 daily_prices 紀錄的(代表還活著)
        rows = conn.execute("""
            SELECT s.stock_id, s.name FROM stocks s
            WHERE EXISTS (
              SELECT 1 FROM daily_prices d WHERE d.stock_id = s.stock_id
              AND d.date >= date('now', '-30 days')
            )
            ORDER BY s.stock_id
        """).fetchall()
        return [dict(r) for r in rows]


def already_done(stock_id, sca_date):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM holder_distribution WHERE stock_id=? AND sca_date=? LIMIT 1",
            (stock_id, sca_date)
        ).fetchone()
        return row is not None


def fetch_one(stock_id, sca_date, throttle):
    """單一 (stock × week) 抓取 + 寫入。每呼叫一次取新 form_state(避免 CSRF token 失效)"""
    session = make_session()
    try:
        # 每個請求都重抓 form_state — TDCC 的 SYNCHRONIZER_TOKEN 是 single-use
        form_state = fetch_form_state(session)
        rows = fetch_holder_distribution(session, stock_id, sca_date, form_state)
        if not rows:
            return (stock_id, sca_date, 0, 'no_data')
        with get_conn() as conn:
            upsert_holder_distribution(conn, stock_id, sca_date, rows)
            conn.commit()
        time.sleep(throttle)
        return (stock_id, sca_date, len(rows), 'ok')
    except Exception as e:
        return (stock_id, sca_date, 0, f'err:{e}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weeks', type=int, default=1,
                        help='抓最近 N 週 (從最新往回,預設 1)')
    parser.add_argument('--stock', type=str, default=None,
                        help='只抓單支股票')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--throttle', type=float, default=0.2,
                        help='每次請求後等待秒數')
    parser.add_argument('--resume', action='store_true',
                        help='跳過已存在的 (stock × week)')
    args = parser.parse_args()

    # 取得 sca_dates
    init_session = make_session()
    form_state = fetch_form_state(init_session)
    sca_dates = form_state.get('sca_dates', [])
    if not sca_dates:
        logger.error('TDCC: 取不到 sca_dates 列表')
        sys.exit(1)

    targets_dates = sca_dates[:args.weeks] if args.weeks else sca_dates
    logger.info(f'抓取週期: {len(targets_dates)} 週,從 {targets_dates[0]} 到 {targets_dates[-1]}')

    stocks = get_target_stocks(args.stock)
    if not stocks:
        logger.error('找不到目標股票')
        sys.exit(1)
    logger.info(f'目標股票數: {len(stocks)}')

    # 建任務清單
    tasks = []
    for s in stocks:
        for d in targets_dates:
            if args.resume and already_done(s['stock_id'], d):
                continue
            tasks.append((s['stock_id'], d))

    logger.info(f'總任務數: {len(tasks)}')
    if not tasks:
        logger.info('全部已完成,結束。')
        return

    ok = no_data = err = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(fetch_one, sid, d, args.throttle): (sid, d)
            for sid, d in tasks
        }
        done = 0
        for fut in as_completed(futures):
            sid, d, n, status = fut.result()
            done += 1
            if status == 'ok':
                ok += 1
            elif status == 'no_data':
                no_data += 1
            else:
                err += 1
                logger.warning(f'{sid}@{d}: {status}')

            if done % 50 == 0:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(tasks) - done) / rate if rate > 0 else 0
                logger.info(
                    f'進度 {done}/{len(tasks)} '
                    f'(ok={ok}, no_data={no_data}, err={err}) '
                    f'rate={rate:.1f} req/s ETA={eta/60:.1f}min'
                )

    elapsed = time.time() - start
    logger.info(
        f'完成: {len(tasks)} 任務 / {elapsed:.0f}s '
        f'(ok={ok}, no_data={no_data}, err={err})'
    )


if __name__ == '__main__':
    main()
