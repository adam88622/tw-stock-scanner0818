"""
04_empirical.py — Hermes-style Skill 自動檢索實證
==================================================
目的：用真實 GiS .claude/skills/ 的 7 個 phase skill，驗證 BM25 檢索能否
      在 dispatcher 場景中取代人工選 skill。

方法：
1. 讀取 D:/claude/.claude/skills/{phase0-7}*/SKILL.md 取 (name, description)
2. 用 rank_bm25 建索引（中文以 char-bigram 斷詞，避免分詞器依賴）
3. 設計 10 個真實量化交易 dispatcher user query，標註正確 skill (ground truth)
4. 對每 query 取 top-3，計算 Recall@1 / Recall@3 / MRR
5. 估算節省的人工分派次數（hit_rate × 場景數）

執行：
  C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe \
    D:/claude/tw-stock-scanner/research/_archive/最新金融與科技研究/weekly-2026-04-28/results/04_empirical.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

# ---------- 1. 載入真實 skill ----------

SKILL_ROOT = Path("D:/claude/.claude/skills")
PHASE_DIRS = [
    "phase0-init",
    "phase2-requirements",
    "phase3-architecture",
    "phase4-review",
    "phase5-build",
    "phase6-test",
    "phase7-delivery",
]


def parse_skill_md(path: Path) -> tuple[str, str]:
    """從 SKILL.md frontmatter 取 name 與 description。"""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        raise ValueError(f"no frontmatter in {path}")
    fm = m.group(1)
    name = re.search(r'name:\s*"?([^"\n]+)"?', fm).group(1).strip()
    desc_m = re.search(r'description:\s*"?([^"\n]+)"?', fm)
    desc = desc_m.group(1).strip() if desc_m else ""
    return name, desc


skills: list[dict] = []
for d in PHASE_DIRS:
    skill_md = SKILL_ROOT / d / "SKILL.md"
    name, desc = parse_skill_md(skill_md)
    skills.append({"name": name, "description": desc})

print("=== 已載入 7 個真實 skill ===")
for s in skills:
    print(f"  - {s['name']}: {s['description']}")


# ---------- 2. 建 BM25 索引（char-bigram 斷詞處理中文） ----------

def tokenize(text: str) -> list[str]:
    """中文 char-bigram + 英數 word，避免依賴 jieba。"""
    text = text.lower()
    tokens: list[str] = []
    # 英數詞
    for w in re.findall(r"[a-z0-9]+", text):
        tokens.append(w)
    # 中文 char-bigram
    cjk = re.findall(r"[一-鿿]+", text)
    for seg in cjk:
        if len(seg) == 1:
            tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
            # 加單字提升 recall
            for ch in seg:
                tokens.append(ch)
    return tokens


# index 文本 = name + description（與 progressive disclosure 第一階段一致）
corpus_texts = [f"{s['name']} {s['description']}" for s in skills]
corpus_tokens = [tokenize(t) for t in corpus_texts]
bm25 = BM25Okapi(corpus_tokens)


def search(query: str, top_k: int = 3) -> list[tuple[str, float]]:
    q_tokens = tokenize(query)
    scores = bm25.get_scores(q_tokens)
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
    return [(skills[i]["name"], float(s)) for i, s in ranked]


# ---------- 3. 10 個真實量化 dispatcher 場景 ----------

# 每個 query 對應的「正確 skill」由 dispatcher 角色判定
queries = [
    # phase0-init：專案初始化
    {"q": "幫我準備一個新專案的目錄結構，從零開始", "gold": "phase0-init"},
    # phase2-requirements：需求分析
    {"q": "幫我做新聞情緒因子的需求分析，產出規格書", "gold": "phase2-requirements"},
    # phase2-requirements：另一種措辭
    {"q": "我要建立法人買賣超 PoC，先把功能需求拆解清楚", "gold": "phase2-requirements"},
    # phase3-architecture：系統架構
    {"q": "請規劃這個量化回測系統的整體架構與 function 拆解", "gold": "phase3-architecture"},
    # phase4-review：架構審查
    {"q": "請審查現在的架構設計，看跟需求有沒有對齊", "gold": "phase4-review"},
    # phase5-build：開發
    {"q": "依照架構文件並行開發所有 function 模組", "gold": "phase5-build"},
    # phase5-build：另一種措辭
    {"q": "把因子計算和訊號產生的程式碼都寫出來", "gold": "phase5-build"},
    # phase6-test：測試
    {"q": "跑完整測試，把環境也建好，所有 API 都驗證一次", "gold": "phase6-test"},
    # phase7-delivery：交付
    {"q": "交付這個專案，產出總結報告與執行說明", "gold": "phase7-delivery"},
    # phase3-architecture：另一個措辭
    {"q": "我需要一份系統架構規劃文件，含介面與依賴關係", "gold": "phase3-architecture"},
]

assert len(queries) == 10


# ---------- 4. 評測 ----------

def evaluate():
    rows = []
    recall_at_1 = 0
    recall_at_3 = 0
    mrr_sum = 0.0

    for i, item in enumerate(queries, 1):
        q, gold = item["q"], item["gold"]
        top3 = search(q, top_k=3)
        names = [n for n, _ in top3]

        hit_at_1 = (names[0] == gold) if names else False
        hit_at_3 = (gold in names)

        # MRR
        rr = 0.0
        for rank, n in enumerate(names, 1):
            if n == gold:
                rr = 1.0 / rank
                break

        recall_at_1 += int(hit_at_1)
        recall_at_3 += int(hit_at_3)
        mrr_sum += rr

        rows.append({
            "idx": i,
            "query": q,
            "gold": gold,
            "top3": top3,
            "hit@1": hit_at_1,
            "hit@3": hit_at_3,
            "rr": rr,
        })

    n = len(queries)
    return {
        "rows": rows,
        "recall@1": recall_at_1 / n,
        "recall@3": recall_at_3 / n,
        "mrr": mrr_sum / n,
        "saved_dispatch": recall_at_3,  # top-3 命中視為可省去人工選 skill
    }


if __name__ == "__main__":
    print("\n=== 評測結果 ===")
    result = evaluate()
    for r in result["rows"]:
        flag1 = "OK" if r["hit@1"] else "--"
        flag3 = "OK" if r["hit@3"] else "--"
        print(f"\n[Q{r['idx']}] {r['query']}")
        print(f"  gold = {r['gold']}")
        print(f"  top3 = {[(n, round(s,2)) for n,s in r['top3']]}")
        print(f"  hit@1={flag1}  hit@3={flag3}  RR={r['rr']:.3f}")

    print("\n=== 整體指標 ===")
    print(f"Recall@1 = {result['recall@1']:.2%}")
    print(f"Recall@3 = {result['recall@3']:.2%}")
    print(f"MRR      = {result['mrr']:.3f}")
    print(f"節省人工分派次數 = {result['saved_dispatch']} / {len(queries)}")

    # JSON dump 給 markdown 報告引用
    out = Path(__file__).parent / "04_empirical_results.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果已寫入：{out}")
