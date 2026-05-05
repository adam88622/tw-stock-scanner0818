# -*- coding: utf-8 -*-
"""
05_memory_vector_search.py
=============================================================================
為 GiS 既有 markdown auto-memory 目錄建立輕量向量索引（方案 B 的 PoC）。

設計：
    - 主路徑：sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
              中英雙語、384 維、118 MB；對「我之前對量化研究嚴謹度的偏好是什麼」
              這類中文 query 召回優於預設 MiniLM-L6-v2 (英文)。
    - Fallback：rank_bm25.BM25Okapi（純 Python，無模型權重）
    - 索引格式：.npz（embeddings）+ .pkl（檔名/原文/中介資料）
    - 索引目錄預設：C:\\Users\\User\\.claude\\projects\\d--claude\\memory\\

公開 API：
    - index_memory_dir(memory_dir, index_path) -> None
    - search(query, top_k=5, index_path=None) -> List[Tuple[str, float, str]]

注意：
    程式可直接 import，不會在 import 時下載模型；首次呼叫 index_memory_dir()
    或 search() 才會載模型。`__main__` 區塊提供範例但僅在直接執行時觸發。
"""

from __future__ import annotations

import os
import re
import pickle
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np

# -----------------------------------------------------------------------------
# 模組層級常數
# -----------------------------------------------------------------------------
DEFAULT_MEMORY_DIR = Path(r"C:\Users\User\.claude\projects\d--claude\memory")
DEFAULT_INDEX_PATH = Path(__file__).parent / "memory_index.npz"
DEFAULT_META_PATH = Path(__file__).parent / "memory_index.pkl"
EMBED_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# 模型懶載入（避免 import 即下載 118 MB）
_model = None
_backend: Optional[str] = None  # "st" 或 "bm25"


# -----------------------------------------------------------------------------
# Backend 偵測：優先 sentence-transformers，否則退回 BM25
# -----------------------------------------------------------------------------
def _load_backend() -> str:
    """偵測可用後端，回傳 'st' 或 'bm25'。"""
    global _model, _backend
    if _backend is not None:
        return _backend
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        _backend = "st"
    except ImportError:
        try:
            import rank_bm25  # noqa: F401
            _backend = "bm25"
            print("[memory_vector_search] sentence-transformers 不可用，退回 BM25")
        except ImportError:
            raise RuntimeError(
                "需要安裝 sentence-transformers 或 rank_bm25 其中之一。\n"
                "推薦：pip install sentence-transformers\n"
                "輕量替代：pip install rank_bm25"
            )
    return _backend


def _get_model():
    """懶載入 sentence-transformers 模型。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


# -----------------------------------------------------------------------------
# 文件讀取與簡易分塊
# -----------------------------------------------------------------------------
def _read_md_files(memory_dir: Path) -> List[Tuple[str, str]]:
    """讀取目錄下所有 .md，回傳 [(檔名, 全文)]。"""
    if not memory_dir.exists():
        raise FileNotFoundError(f"memory 目錄不存在：{memory_dir}")
    docs: List[Tuple[str, str]] = []
    for fp in sorted(memory_dir.glob("*.md")):
        try:
            text = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = fp.read_text(encoding="utf-8-sig", errors="ignore")
        docs.append((fp.name, text))
    return docs


def _tokenize_zh_en(text: str) -> List[str]:
    """中英混合粗略分詞：中文按字元，英文/數字按詞。"""
    # 抓出英文詞、數字、中文字元
    tokens = re.findall(r"[A-Za-z]+|\d+|[一-鿿]", text)
    return [t.lower() for t in tokens if t.strip()]


# -----------------------------------------------------------------------------
# 索引建立
# -----------------------------------------------------------------------------
def index_memory_dir(
    memory_dir: Path = DEFAULT_MEMORY_DIR,
    index_path: Path = DEFAULT_INDEX_PATH,
    meta_path: Path = DEFAULT_META_PATH,
) -> None:
    """
    為 memory_dir 下所有 .md 建立向量索引（或 BM25 索引）。

    產出：
        - {index_path}      ：向量矩陣（.npz）或佔位（BM25 模式）
        - {meta_path}       ：檔名、原文、後端類型、模型雜湊（.pkl）
    """
    memory_dir = Path(memory_dir)
    index_path = Path(index_path)
    meta_path = Path(meta_path)

    docs = _read_md_files(memory_dir)
    if not docs:
        raise ValueError(f"{memory_dir} 內沒有 .md 檔案")

    filenames = [name for name, _ in docs]
    contents = [text for _, text in docs]
    backend = _load_backend()

    meta = {
        "backend": backend,
        "filenames": filenames,
        "contents": contents,
        "memory_dir": str(memory_dir),
        "model_name": EMBED_MODEL_NAME if backend == "st" else "BM25Okapi",
    }

    if backend == "st":
        model = _get_model()
        embeddings = model.encode(
            contents, normalize_embeddings=True, show_progress_bar=False
        )
        np.savez_compressed(index_path, embeddings=embeddings)
        meta["index_hash"] = hashlib.sha1(embeddings.tobytes()).hexdigest()[:12]
    else:
        # BM25：把每篇 tokenize 後存到 meta，查詢時即時計分
        tokenized = [_tokenize_zh_en(t) for t in contents]
        meta["tokenized"] = tokenized
        # index_path 留空檔以維持介面一致
        np.savez_compressed(index_path, embeddings=np.array([0.0]))

    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)
    print(f"[index] backend={backend} 已索引 {len(filenames)} 檔 → {index_path}")


# -----------------------------------------------------------------------------
# 查詢
# -----------------------------------------------------------------------------
def search(
    query: str,
    top_k: int = 5,
    index_path: Path = DEFAULT_INDEX_PATH,
    meta_path: Path = DEFAULT_META_PATH,
) -> List[Tuple[str, float, str]]:
    """
    對索引執行查詢，回傳前 top_k 結果。

    Returns:
        List[(filename, score, snippet)]，分數越高越相關。
    """
    index_path = Path(index_path)
    meta_path = Path(meta_path)
    if not meta_path.exists():
        raise FileNotFoundError(
            f"找不到索引 {meta_path}，請先呼叫 index_memory_dir()"
        )
    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
    backend = meta["backend"]
    filenames = meta["filenames"]
    contents = meta["contents"]

    if backend == "st":
        model = _get_model()
        q_emb = model.encode([query], normalize_embeddings=True)[0]
        embeddings = np.load(index_path)["embeddings"]
        scores = embeddings @ q_emb  # cosine（皆已 L2 normalize）
    else:
        from rank_bm25 import BM25Okapi
        bm25 = BM25Okapi(meta["tokenized"])
        scores = bm25.get_scores(_tokenize_zh_en(query))

    # 取 top_k
    order = np.argsort(-np.asarray(scores))[:top_k]
    results: List[Tuple[str, float, str]] = []
    for i in order:
        snippet = contents[i].strip().replace("\n", " ")[:120]
        results.append((filenames[i], float(scores[i]), snippet))
    return results


# -----------------------------------------------------------------------------
# CLI 範例（僅直接執行時觸發；import 時不執行）
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("GiS Memory Vector Search PoC")
    print("=" * 60)

    # 1) 建索引
    print("\n[1] 索引 memory 目錄 ...")
    try:
        index_memory_dir()
    except Exception as e:
        print(f"建索引失敗：{e}")
        sys.exit(1)

    # 2) 範例查詢
    sample_queries = [
        "我之前對量化研究嚴謹度的偏好是什麼",
        "凱基證券 API 進度",
        "skcom COM event handler 注意事項",
    ]
    for q in sample_queries:
        print(f"\n[Query] {q}")
        for fname, score, snippet in search(q, top_k=3):
            print(f"  {score:+.4f}  {fname}")
            print(f"          {snippet}")
