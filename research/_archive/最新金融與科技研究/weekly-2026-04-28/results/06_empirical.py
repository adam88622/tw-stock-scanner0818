"""
06_empirical.py — TurboQuant Token Planner 實證驗證

對 3 份真實長文金融文件（NVIDIA 10-K、TSMC 20-F、Apple 10-K）：
  1. 用 tiktoken 實際計算 token 數
  2. 對 6 家 1M context 模型跑成本對比
  3. RAG chunking vs 1M context one-shot 經濟學交叉點分析

執行：
  python 06_empirical.py
依賴：tiktoken, requests（已裝）

文件來源：SEC EDGAR（已預先下載至 ./docs/）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from html.parser import HTMLParser

import tiktoken

# --------------------------------------------------------------------------- #
#  模型定價（2026-04，USD per 1M tokens）— 含 GPT-5.4、DeepSeek V4-Pro/Flash、Qwen3 4 家替換 PoC 的 6 家清單
# --------------------------------------------------------------------------- #
MODELS = {
    "claude-opus-4.7-1m":   {"input": 15.0, "output": 75.0, "ctx": 1_000_000, "tier": "premium"},
    "claude-sonnet-4.7":    {"input":  3.0, "output": 15.0, "ctx":   200_000, "tier": "balanced"},
    "gpt-5.4":              {"input": 12.0, "output": 36.0, "ctx":   400_000, "tier": "premium"},
    "deepseek-v4-pro-1m":   {"input":  0.7, "output":  2.5, "ctx": 1_000_000, "tier": "cheap"},
    "deepseek-v4-flash-1m": {"input":  0.15,"output":  0.5, "ctx": 1_000_000, "tier": "ultra-cheap"},
    "qwen3-1m":             {"input":  0.4, "output":  1.5, "ctx": 1_000_000, "tier": "cheap"},
}

DOCS_DIR = Path(__file__).parent / "docs"
OUTPUT_TOKENS_DEFAULT = 4_000          # 一次推理預設輸出長度
RAG_CHUNK_TOKENS = 5_000               # RAG 每個 chunk 大小
RAG_TOPK = 8                           # 一次查詢拉幾個 chunk 進 prompt
RAG_QUERIES_PER_DOC = 5                # 一份文件一輪分析平均查詢次數
RAG_OVERLAP_RATIO = 0.10               # chunk 之間 10% overlap


# --------------------------------------------------------------------------- #
#  HTML → 純文字（簡化版，避免裝額外依賴）
# --------------------------------------------------------------------------- #
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0  # 跳過 script/style

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    txt = " ".join(p.parts)
    # 清理 nbsp、連續空白
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


# --------------------------------------------------------------------------- #
#  Token 計算
# --------------------------------------------------------------------------- #
_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    # tiktoken 對 100K+ 字元一次 encode 沒問題，但分塊更安全
    n = 0
    step = 200_000
    for i in range(0, len(text), step):
        n += len(_ENC.encode(text[i : i + step], disallowed_special=()))
    return n


# --------------------------------------------------------------------------- #
#  成本估算
# --------------------------------------------------------------------------- #
def cost_oneshot(input_tokens: int, output_tokens: int = OUTPUT_TOKENS_DEFAULT) -> list[dict]:
    rows = []
    for name, m in MODELS.items():
        if input_tokens > m["ctx"]:
            rows.append(
                {
                    "model": name,
                    "tier": m["tier"],
                    "fits": False,
                    "cost_usd": None,
                    "ctx_k": m["ctx"] // 1_000,
                }
            )
            continue
        c = input_tokens / 1e6 * m["input"] + output_tokens / 1e6 * m["output"]
        rows.append(
            {
                "model": name,
                "tier": m["tier"],
                "fits": True,
                "cost_usd": round(c, 4),
                "ctx_k": m["ctx"] // 1_000,
            }
        )
    rows.sort(key=lambda r: (not r["fits"], r["cost_usd"] or 9e9))
    return rows


def cost_rag(
    doc_tokens: int,
    chunk_tokens: int = RAG_CHUNK_TOKENS,
    topk: int = RAG_TOPK,
    queries: int = RAG_QUERIES_PER_DOC,
    overlap: float = RAG_OVERLAP_RATIO,
    output_tokens: int = OUTPUT_TOKENS_DEFAULT,
) -> dict:
    """
    RAG 成本模型（不含 embedding，因相對便宜，這裡聚焦 LLM 端）：

    n_chunks       = ceil(doc_tokens / chunk_tokens / (1 - overlap))
    per_query_in   = topk * chunk_tokens + 500 (system+question)
    per_query_out  = output_tokens
    total_cost     = queries * (per_query_in * input + per_query_out * output)
    """
    import math

    n_chunks = math.ceil(doc_tokens / (chunk_tokens * (1 - overlap)))
    per_query_in = topk * chunk_tokens + 500
    per_query_out = output_tokens
    rows = []
    for name, m in MODELS.items():
        if per_query_in > m["ctx"]:
            rows.append({"model": name, "fits": False, "cost_usd": None})
            continue
        c = queries * (per_query_in / 1e6 * m["input"] + per_query_out / 1e6 * m["output"])
        rows.append(
            {
                "model": name,
                "tier": m["tier"],
                "fits": True,
                "cost_usd": round(c, 4),
                "n_chunks": n_chunks,
                "queries": queries,
            }
        )
    rows.sort(key=lambda r: (not r["fits"], r["cost_usd"] or 9e9))
    return {
        "n_chunks": n_chunks,
        "per_query_in_tokens": per_query_in,
        "queries": queries,
        "rows": rows,
    }


def recommend(tokens: int) -> str:
    if tokens < 50_000:
        return "短文。任一模型皆可，選 Sonnet/Flash 級即可。"
    if tokens < 200_000:
        return "中等長度。建議 Claude Sonnet 4.7 或 DeepSeek V4-Flash（200K 內 cost-effective）。"
    if tokens < 500_000:
        return "進入 1M context 區。**DeepSeek V4-Flash 1M** 為成本最佳解；對精度敏感選 Claude Opus 4.7-1M。"
    if tokens <= 1_000_000:
        return "接近 1M 上限。僅 Claude Opus/DeepSeek/Qwen3 1M 版可用；GPT-5.4 與 Sonnet 容不下。"
    return "超過 1M tokens。必須切片 + RAG，無一模型可一次餵入。"


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def analyze(doc_path: Path) -> dict:
    raw = doc_path.read_bytes()
    if doc_path.suffix.lower() in (".html", ".htm"):
        # 嘗試多種 encoding（SEC 文件有時混合）
        for enc in ("utf-8", "latin-1"):
            try:
                html = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        text = html_to_text(html)
    else:
        text = raw.decode("utf-8", errors="ignore")

    tokens = count_tokens(text)
    return {
        "name": doc_path.name,
        "size_kb": round(doc_path.stat().st_size / 1024, 1),
        "chars": len(text),
        "tokens": tokens,
        "oneshot": cost_oneshot(tokens),
        "rag": cost_rag(tokens),
    }


def fmt_cost(c):
    return "  -" if c is None else f"${c:.4f}"


def print_report(reports: list[dict]):
    print("\n" + "=" * 78)
    print(" TurboQuant Token Planner — 實證驗證 (GiS, 2026-04-28)")
    print("=" * 78)

    for r in reports:
        print(f"\n### {r['name']}  ({r['size_kb']:.1f} KB → {r['chars']:,} chars)")
        print(f"  實測 token 數：{r['tokens']:,}")
        print(f"  推薦：{recommend(r['tokens'])}\n")

        print(f"  --- One-shot 1M context 成本（含 {OUTPUT_TOKENS_DEFAULT:,} output）---")
        print(f"    {'模型':24s} {'tier':12s} {'ctx':>6s} {'容得下':>7s} {'單次成本':>12s}")
        for row in r["oneshot"]:
            fits = "OK" if row["fits"] else "X"
            print(
                f"    {row['model']:24s} {row['tier']:12s} {row['ctx_k']:>5d}K "
                f"{fits:>7s} {fmt_cost(row['cost_usd']):>12s}"
            )

        rag = r["rag"]
        print(
            f"\n  --- RAG ({rag['n_chunks']} chunks × {RAG_CHUNK_TOKENS//1000}K, "
            f"top-{RAG_TOPK}, {rag['queries']} queries) ---"
        )
        print(f"    每 query 餵入 token：{rag['per_query_in_tokens']:,}")
        for row in rag["rows"]:
            if not row["fits"]:
                continue
            print(f"    {row['model']:24s} {fmt_cost(row['cost_usd']):>12s}")

        # 一份文件對「最便宜可用 1M one-shot」 vs 「最便宜可用 RAG」做交叉
        cheapest_oneshot = next((x for x in r["oneshot"] if x["fits"]), None)
        cheapest_rag = next((x for x in rag["rows"] if x["fits"]), None)
        if cheapest_oneshot and cheapest_rag:
            ratio = cheapest_oneshot["cost_usd"] / cheapest_rag["cost_usd"]
            print(
                f"\n  one-shot/RAG 成本比 (cheapest)："
                f"{cheapest_oneshot['cost_usd']:.4f} / {cheapest_rag['cost_usd']:.4f} "
                f"= {ratio:.2f}x  ({'one-shot 較便宜' if ratio < 1 else 'RAG 較便宜'})"
            )


def main():
    files = sorted(DOCS_DIR.glob("*.html"))
    if not files:
        print(f"[!] 找不到 docs/*.html，請確認 {DOCS_DIR}", file=sys.stderr)
        sys.exit(1)

    reports = []
    for f in files:
        print(f"[•] 解析 {f.name} ...")
        reports.append(analyze(f))

    print_report(reports)

    # 額外輸出 markdown 表格供報告引用
    md_path = Path(__file__).parent / "_empirical_tables.md"
    with md_path.open("w", encoding="utf-8") as fp:
        fp.write("# 自動產出表格（供 06_empirical_results.md 引用）\n\n")
        fp.write("## Token 統計\n\n")
        fp.write("| 文件 | 大小(KB) | 字元數 | tokens |\n|---|---:|---:|---:|\n")
        for r in reports:
            fp.write(f"| {r['name']} | {r['size_kb']:.1f} | {r['chars']:,} | {r['tokens']:,} |\n")

        fp.write("\n## One-shot 成本矩陣（USD）\n\n")
        header = ["模型/文件"] + [r["name"].replace(".html", "") for r in reports]
        fp.write("| " + " | ".join(header) + " |\n")
        fp.write("|" + "---|" * len(header) + "\n")
        for model_name in MODELS:
            row = [model_name]
            for r in reports:
                cell = next((x for x in r["oneshot"] if x["model"] == model_name), None)
                if cell is None or not cell["fits"]:
                    row.append("X (容不下)")
                else:
                    row.append(f"${cell['cost_usd']:.4f}")
            fp.write("| " + " | ".join(row) + " |\n")

        fp.write("\n## RAG 成本（同模型，top-8 × 5K × 5 queries）\n\n")
        fp.write("| 模型/文件 | " + " | ".join(r["name"].replace(".html", "") for r in reports) + " |\n")
        fp.write("|" + "---|" * (len(reports) + 1) + "\n")
        for model_name in MODELS:
            row = [model_name]
            for r in reports:
                cell = next((x for x in r["rag"]["rows"] if x["model"] == model_name), None)
                if cell is None or not cell["fits"]:
                    row.append("X")
                else:
                    row.append(f"${cell['cost_usd']:.4f}")
            fp.write("| " + " | ".join(row) + " |\n")

    print(f"\n[OK] markdown 表格寫入 {md_path}")


if __name__ == "__main__":
    main()
