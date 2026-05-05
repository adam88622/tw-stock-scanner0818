"""
06_long_context_use_case_planner.py

PoC: 長文金融文件 1M context 推理規劃工具

用途：
    給定一份金融文件（PDF / TXT），估算 token 數，計算各家 1M context API
    推理成本，並建議最適模型。

背景：
    Google TurboQuant @ ICLR 2026 將降低雲端推理 1M context 成本，但 GiS
    自部署不划算。本工具協助評估「現況下哪些任務值得用 1M context API」。

執行：
    python 06_long_context_use_case_planner.py <檔案路徑>

依賴：
    pip install tiktoken pypdf
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---- 模型定價（2026-04 公開牌價，USD per 1M tokens）----
# 註：output 通常 ~5x input，這裡僅用 input 估算下限
MODELS = {
    "claude-opus-4.7-1m":   {"input": 15.0,  "output": 75.0,  "ctx": 1_000_000, "tier": "premium"},
    "claude-sonnet-4.7":    {"input": 3.0,   "output": 15.0,  "ctx": 200_000,   "tier": "balanced"},
    "gemini-1.5-pro-1m":    {"input": 7.0,   "output": 21.0,  "ctx": 1_000_000, "tier": "premium"},
    "gemini-1.5-flash":     {"input": 0.35,  "output": 1.05,  "ctx": 1_000_000, "tier": "cheap"},
    "deepseek-v3-1m":       {"input": 0.5,   "output": 2.0,   "ctx": 1_000_000, "tier": "cheap"},
    "gpt-4.5":              {"input": 10.0,  "output": 30.0,  "ctx": 200_000,   "tier": "premium"},
}


def read_text(path: Path) -> str:
    """支援 .txt / .md / .pdf。"""
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            print("[!] 需要 pypdf：pip install pypdf", file=sys.stderr)
            sys.exit(1)
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def count_tokens(text: str) -> int:
    """優先用 tiktoken；不可用則以字數 × 1.3 估算（中文偏保守）。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # 中文 1 字約 1.5 token，英文 1 詞約 1.3 token，混合取 1.3
        return int(len(text) * 1.3)


def estimate_cost(tokens: int, output_tokens: int = 4000) -> list[dict]:
    """估算單次推理成本（input + 預設 4K output）。"""
    rows = []
    for name, info in MODELS.items():
        if tokens > info["ctx"]:
            rows.append({"model": name, "fits": False, "cost_usd": None, "tier": info["tier"]})
            continue
        cost = tokens / 1_000_000 * info["input"] + output_tokens / 1_000_000 * info["output"]
        rows.append({"model": name, "fits": True, "cost_usd": round(cost, 4), "tier": info["tier"]})
    return sorted(rows, key=lambda r: (not r["fits"], r["cost_usd"] or 9e9))


def recommend(tokens: int) -> str:
    if tokens < 50_000:
        return "短文，任何模型皆可，選 Sonnet/Flash 級即可。"
    if tokens < 200_000:
        return "中等長度，建議 Claude Sonnet 4.7 或 Gemini 1.5 Pro（200K 內最划算）。"
    if tokens < 500_000:
        return "進入 1M context 區，建議 Gemini 1.5 Pro 1M 或 DeepSeek V3 1M（成本敏感選 DeepSeek）。"
    if tokens <= 1_000_000:
        return "接近 1M 上限，僅 Gemini/Claude/DeepSeek 1M 版可用；TurboQuant 普及後成本可望腰斬。"
    return "超過 1M tokens，需切片 + RAG，無法一次餵入。"


def main():
    if len(sys.argv) < 2:
        print("用法：python 06_long_context_use_case_planner.py <文件路徑>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[!] 檔案不存在：{path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== 長文金融文件規劃工具 (GiS 2026-04-28) ===")
    print(f"檔案：{path.name}  ({path.stat().st_size/1024:.1f} KB)")

    text = read_text(path)
    tokens = count_tokens(text)
    print(f"字元數：{len(text):,}")
    print(f"預估 token：{tokens:,}")

    print(f"\n--- 各模型成本估算（含 4K output）---")
    print(f"{'模型':28s} {'tier':10s} {'容得下':6s} {'單次成本(USD)':>15s}")
    for r in estimate_cost(tokens):
        fits = "OK" if r["fits"] else "X"
        cost = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "  -"
        print(f"{r['model']:28s} {r['tier']:10s} {fits:6s} {cost:>15s}")

    print(f"\n--- 建議 ---")
    print(recommend(tokens))
    print()


if __name__ == "__main__":
    main()
