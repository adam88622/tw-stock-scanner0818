# -*- coding: utf-8 -*-
"""
05_empirical.py
==============================================================================
向量檢索 vs BM25 在 GiS 真實 auto-memory 目錄上的實證對比。

對 9 個 .md 檔（MEMORY.md + 8 個專題檔）建立兩種索引，
針對 8 個中英混合 query 計算 NDCG@5 / Recall@1 / Recall@3 / MRR / 平均延遲。

執行：
    C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe 05_empirical.py
"""
from __future__ import annotations

import sys
import time
import math
import json
from pathlib import Path
from typing import List, Tuple, Dict

import numpy as np

# 引入 PoC ----------------------------------------------------------
POC_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\weekly-2026-04-28\poc")
RESULTS_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\weekly-2026-04-28\results")
sys.path.insert(0, str(POC_DIR))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "memvec", POC_DIR / "05_memory_vector_search.py"
)
memvec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memvec)

MEMORY_DIR = Path(r"C:\Users\User\.claude\projects\d--claude\memory")

# Query 與 ground truth ---------------------------------------------
QUERIES: List[Tuple[str, str]] = [
    ("凱基證券 API 串接進度", "project_kgi_api_setup.md"),
    ("我之前對量化研究嚴謹度的偏好", "feedback_research_rigor.md"),
    ("SK COM 事件處理 callback 失敗", "feedback_skcom_inner_class.md"),
    ("EZWin 報告下載排程", "project_ezwin_report.md"),
    ("trading terminal K 棒不顯示", "project_terminal_status.md"),
    ("auto restart bot", "feedback_auto_restart.md"),
    ("user works in quant trading", "user_profile.md"),
    ("法人買賣超 backfill", "project_institutional_backfill.md"),
]


# 指標 -------------------------------------------------------------
def ndcg_at_k(ranked: List[str], gt: str, k: int = 5) -> float:
    dcg = 0.0
    for i, name in enumerate(ranked[:k]):
        rel = 1.0 if name == gt else 0.0
        dcg += rel / math.log2(i + 2)
    idcg = 1.0  # 只有一個正確答案，理想 DCG = 1/log2(2) = 1
    return dcg / idcg


def recall_at_k(ranked: List[str], gt: str, k: int) -> float:
    return 1.0 if gt in ranked[:k] else 0.0


def mrr(ranked: List[str], gt: str) -> float:
    for i, name in enumerate(ranked):
        if name == gt:
            return 1.0 / (i + 1)
    return 0.0


# 兩種後端 ----------------------------------------------------------
def build_indices():
    """同時建立 ST 與 BM25 索引，回傳兩個 search_fn(query, top_k)."""
    docs = memvec._read_md_files(MEMORY_DIR)
    filenames = [n for n, _ in docs]
    contents = [c for _, c in docs]

    # --- BM25 ---
    from rank_bm25 import BM25Okapi
    tokenized = [memvec._tokenize_zh_en(t) for t in contents]
    bm25 = BM25Okapi(tokenized)

    def bm25_search(query: str, top_k: int = 5):
        scores = bm25.get_scores(memvec._tokenize_zh_en(query))
        order = np.argsort(-np.asarray(scores))[:top_k]
        return [(filenames[i], float(scores[i])) for i in order]

    # --- Sentence-Transformers (try) ---
    st_search = None
    st_load_seconds = None
    try:
        t0 = time.perf_counter()
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(memvec.EMBED_MODEL_NAME)
        embeddings = model.encode(
            contents, normalize_embeddings=True, show_progress_bar=False
        )
        st_load_seconds = time.perf_counter() - t0

        def st_search_fn(query: str, top_k: int = 5):
            q = model.encode([query], normalize_embeddings=True)[0]
            scores = embeddings @ q
            order = np.argsort(-np.asarray(scores))[:top_k]
            return [(filenames[i], float(scores[i])) for i in order]

        st_search = st_search_fn
    except Exception as e:
        print(f"[warn] sentence-transformers 不可用：{e}")

    return {
        "filenames": filenames,
        "contents": contents,
        "bm25": bm25_search,
        "st": st_search,
        "st_load_seconds": st_load_seconds,
    }


def evaluate(name: str, search_fn, queries) -> Dict:
    per_query = []
    ndcgs, r1s, r3s, mrrs, lats = [], [], [], [], []
    for q, gt in queries:
        t0 = time.perf_counter()
        topk = search_fn(q, top_k=5)
        dt = (time.perf_counter() - t0) * 1000
        ranked = [n for n, _ in topk]
        ndcg = ndcg_at_k(ranked, gt, 5)
        r1 = recall_at_k(ranked, gt, 1)
        r3 = recall_at_k(ranked, gt, 3)
        mr = mrr(ranked, gt)
        per_query.append(
            {
                "query": q,
                "gt": gt,
                "top5": topk,
                "ndcg5": ndcg,
                "recall1": r1,
                "recall3": r3,
                "mrr": mr,
                "latency_ms": dt,
            }
        )
        ndcgs.append(ndcg)
        r1s.append(r1)
        r3s.append(r3)
        mrrs.append(mr)
        lats.append(dt)
    return {
        "method": name,
        "per_query": per_query,
        "avg_ndcg5": float(np.mean(ndcgs)),
        "avg_recall1": float(np.mean(r1s)),
        "avg_recall3": float(np.mean(r3s)),
        "avg_mrr": float(np.mean(mrrs)),
        "avg_latency_ms": float(np.mean(lats)),
    }


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[info] memory dir = {MEMORY_DIR}")
    print(f"[info] queries = {len(QUERIES)}")

    backends = build_indices()
    print(f"[info] indexed {len(backends['filenames'])} files: {backends['filenames']}")

    bm25_metrics = evaluate("BM25", backends["bm25"], QUERIES)
    st_metrics = None
    if backends["st"] is not None:
        st_metrics = evaluate("Vector(MiniLM-L12-v2)", backends["st"], QUERIES)

    out = {
        "memory_dir": str(MEMORY_DIR),
        "n_files": len(backends["filenames"]),
        "files": backends["filenames"],
        "bm25": bm25_metrics,
        "vector": st_metrics,
        "st_load_seconds": backends["st_load_seconds"],
    }
    json_path = RESULTS_DIR / "05_empirical_raw.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[info] wrote {json_path}")

    def fmt(m):
        if not m:
            return "(N/A)"
        return (
            f"NDCG@5={m['avg_ndcg5']:.4f}  R@1={m['avg_recall1']:.4f}  "
            f"R@3={m['avg_recall3']:.4f}  MRR={m['avg_mrr']:.4f}  "
            f"avgLat={m['avg_latency_ms']:.2f}ms"
        )

    print("\n=== Summary ===")
    print(f"BM25   : {fmt(bm25_metrics)}")
    print(f"Vector : {fmt(st_metrics)}")


if __name__ == "__main__":
    main()
