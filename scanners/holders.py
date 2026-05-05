"""
集保戶股權分散表分析

Bands (TDCC 分級):
  1: 1-999            (極小散戶)
  2: 1,000-5,000      (散戶)
  3: 5,001-10,000     (小資族)
  4-7: 10,001-40,000  (中實戶)
  8-10: 40,001-200,000
  11-14: 200,001-1,000,000  (大戶)
  15: 1,000,001+      (千張大戶 / 主力)
  16: 差異數調整(雜訊)
  17: 合計

定義:
  retail_pct        = sum(band 1-3 pct)        散戶比例
  mid_pct           = sum(band 4-10 pct)
  big_pct           = sum(band 11-14 pct)
  giant_pct (千張)   = band 15 pct              千張大戶
  big_combined_pct  = big_pct + giant_pct       400張+大戶總和
"""
import logging
from models.database import get_conn

logger = logging.getLogger(__name__)

RETAIL_BANDS = (1, 2, 3)
MID_BANDS = (4, 5, 6, 7, 8, 9, 10)
BIG_BANDS = (11, 12, 13, 14)
GIANT_BAND = 15


def _aggregate(rows, bands):
    """rows = list[Row] for ONE (stock_id, sca_date), 加總某些 band 的 pct"""
    total = 0.0
    for r in rows:
        if r["band"] in bands and r["pct"] is not None:
            total += r["pct"]
    return round(total, 4)


def get_concentration_series(stock_id, weeks=52):
    """
    回傳該股票最近 N 週的集中度時序。
    回傳: list[dict(sca_date, retail_pct, big_pct, giant_pct, total_holders)]
    依 sca_date 升冪。
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT sca_date, band, band_label, holders, shares, pct
            FROM holder_distribution
            WHERE stock_id = ?
            ORDER BY sca_date DESC, band ASC
        """, (stock_id,)).fetchall()

    # group by sca_date
    by_date = {}
    for r in rows:
        by_date.setdefault(r["sca_date"], []).append(r)

    series = []
    for d in sorted(by_date.keys()):
        wk = by_date[d]
        # 「合計」行在 band 16 或 17(視是否有「差異數調整」),用 label 判斷
        total_holders = next(
            (r["holders"] for r in wk if "合" in (r["band_label"] or "")),
            None,
        )
        series.append({
            "sca_date": d,
            "retail_pct": _aggregate(wk, RETAIL_BANDS),
            "mid_pct": _aggregate(wk, MID_BANDS),
            "big_pct": _aggregate(wk, BIG_BANDS),
            "giant_pct": _aggregate(wk, (GIANT_BAND,)),
            "total_holders": total_holders,
        })

    if weeks and len(series) > weeks:
        series = series[-weeks:]
    return series


def get_concentration_change_ranking(latest_date=None, weeks_ago=4, limit=50, market=None):
    """
    比較最新 sca_date 與 N 週前的「千張大戶 + 大戶」比例變化,排序變化幅度。
    回傳兩榜:
      increased: list[dict] — 大戶集中度增加最多 (主力進貨)
      decreased: list[dict] — 大戶集中度減少最多 (主力出貨)
    """
    with get_conn() as conn:
        # 找最新 sca_date
        if latest_date is None:
            row = conn.execute("SELECT MAX(sca_date) AS d FROM holder_distribution").fetchone()
            latest_date = row["d"] if row else None
        if not latest_date:
            return {"increased": [], "decreased": [], "latest_date": None, "compare_date": None}

        # 找 N 週前的日期
        prev_row = conn.execute("""
            SELECT DISTINCT sca_date FROM holder_distribution
            WHERE sca_date < ?
            ORDER BY sca_date DESC
            LIMIT 1 OFFSET ?
        """, (latest_date, weeks_ago - 1)).fetchone()
        compare_date = prev_row["sca_date"] if prev_row else None
        if not compare_date:
            return {"increased": [], "decreased": [], "latest_date": latest_date, "compare_date": None}

        # 取兩個日期的所有 stock × big bands (11-15) pct
        sql = """
            SELECT h.stock_id, h.sca_date, h.band, h.pct, s.name, s.market
            FROM holder_distribution h
            JOIN stocks s ON s.stock_id = h.stock_id
            WHERE h.sca_date IN (?, ?)
              AND h.band IN (11, 12, 13, 14, 15)
        """
        params = [latest_date, compare_date]
        if market in ("twse", "tpex"):
            sql += " AND s.market = ?"
            params.append(market)

        all_rows = conn.execute(sql, params).fetchall()

    # build {(stock, date) -> big_combined_pct}
    agg = {}
    name_market = {}
    for r in all_rows:
        key = (r["stock_id"], r["sca_date"])
        agg[key] = agg.get(key, 0.0) + (r["pct"] or 0.0)
        name_market[r["stock_id"]] = (r["name"], r["market"])

    # compute deltas
    deltas = []
    seen = set()
    for (stock_id, sca), pct_now in agg.items():
        if sca != latest_date:
            continue
        pct_prev = agg.get((stock_id, compare_date))
        if pct_prev is None:
            continue
        if stock_id in seen:
            continue
        seen.add(stock_id)
        name, mkt = name_market.get(stock_id, ("", ""))
        deltas.append({
            "stock_id": stock_id,
            "name": name,
            "market": mkt,
            "big_pct_now": round(pct_now, 4),
            "big_pct_prev": round(pct_prev, 4),
            "delta": round(pct_now - pct_prev, 4),
        })

    deltas.sort(key=lambda x: x["delta"], reverse=True)
    increased = deltas[:limit]
    decreased = list(reversed(deltas[-limit:])) if len(deltas) > limit else []
    if len(deltas) <= limit:
        decreased = sorted(deltas, key=lambda x: x["delta"])[:limit]

    return {
        "latest_date": latest_date,
        "compare_date": compare_date,
        "increased": increased,
        "decreased": decreased,
    }


def get_latest_snapshot(stock_id):
    """單支股票最新一週的 17 行 raw + 彙整"""
    with get_conn() as conn:
        latest = conn.execute("""
            SELECT MAX(sca_date) AS d FROM holder_distribution WHERE stock_id = ?
        """, (stock_id,)).fetchone()
        if not latest or not latest["d"]:
            return None
        sca = latest["d"]
        rows = conn.execute("""
            SELECT band, band_label, holders, shares, pct
            FROM holder_distribution
            WHERE stock_id = ? AND sca_date = ?
            ORDER BY band ASC
        """, (stock_id, sca)).fetchall()

    rows_d = [dict(r) for r in rows]
    return {
        "sca_date": sca,
        "rows": rows_d,
        "retail_pct": _aggregate(rows, RETAIL_BANDS),
        "mid_pct": _aggregate(rows, MID_BANDS),
        "big_pct": _aggregate(rows, BIG_BANDS),
        "giant_pct": _aggregate(rows, (GIANT_BAND,)),
    }
