"""
論文 #8 LLM 財報舞弊偵測 — Scaffold + Mock Demo
Paper: arXiv:2604.20652 - LLMs Outperform Humans in Fraud Detection

實作狀態：
- ✅ 完整框架（prompt、輸出 schema、portfolio 整合邏輯）
- ✅ Mock LLM evaluator（rule-based fake LLM 用於 demo）
- ⏳ 真實 LLM 呼叫（需 ANTHROPIC_API_KEY 環境變數）
- ⏳ 真實 MOPS 文本（需另建 scrapers/mops_annual_reports.py）

Usage:
  # Mock 模式（演算法 demo 不花 API 費用）
  python exp08_fraud_detection_scaffold.py --mock

  # 真實 LLM 模式（需 ANTHROPIC_API_KEY）
  python exp08_fraud_detection_scaffold.py --real

驗證設計：
- 用台股已知問題標的做 ground truth：樂陞 (3662, 2016)、康友-KY (6452, 2020)
- 但本範例僅用 mock 資料展示流程
"""

import os
import sys
import json
import argparse
import random
from pathlib import Path
from typing import Optional

OUT_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\2026_0427_papers\experiments")


# ====== Prompt 設計 ======

FRAUD_DETECTION_PROMPT = """你是嚴謹的法務會計師（forensic accountant）。請分析以下台灣上市公司的財報/法說會文本，識別 9 個舞弊前兆：

1. **數字一致性**：敘事與表上數字是否矛盾？
2. **語意模糊度**：是否大量使用模糊措辭逃避具體承諾？
3. **責任轉嫁**：營運不佳是否歸咎外部因素過於頻繁？
4. **時間遠離**：「下半年/明年」承諾出現次數
5. **複雜度噪音**：是否用過度技術術語掩蓋簡單問題？
6. **第一人稱避免**：管理層是否避免「我承擔」、「我決定」？
7. **過度樂觀詞彙**：超出歷史常態的正面詞彙密度
8. **修正性敘述**：對前期承諾的修正是否被淡化？
9. **異常會計處理說明**：是否花過多篇幅解釋會計處理變更？

請輸出 JSON（嚴格遵守格式）：
```json
{{
  "stock_id": "{stock_id}",
  "fraud_score": 0.0,
  "primary_concerns": ["list of concerns"],
  "specific_quotes": ["最可疑的 3 句原文"],
  "audit_recommendation": "low_risk|monitor|high_risk|exclude"
}}
```

文本內容：
{text}
"""


# ====== Output Schema ======

FRAUD_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["stock_id", "fraud_score", "audit_recommendation"],
    "properties": {
        "stock_id": {"type": "string"},
        "fraud_score": {"type": "number", "minimum": 0, "maximum": 1},
        "primary_concerns": {"type": "array", "items": {"type": "string"}},
        "specific_quotes": {"type": "array", "items": {"type": "string"}},
        "audit_recommendation": {
            "type": "string",
            "enum": ["low_risk", "monitor", "high_risk", "exclude"],
        },
    },
}


# ====== Portfolio Integration ======

def fraud_score_to_weight_adjustment(score: float) -> float:
    """
    論文應用：
    - score < 0.3:   不調整（low_risk）
    - 0.3 <= score < 0.5:  降權重 20%（monitor）
    - 0.5 <= score < 0.8:  降權重 50%（high_risk）
    - score >= 0.8:        排除（exclude）
    """
    if score < 0.3:
        return 1.0
    elif score < 0.5:
        return 0.8
    elif score < 0.8:
        return 0.5
    else:
        return 0.0


def apply_to_portfolio(weights: dict, fraud_scores: dict) -> dict:
    """套用 fraud score 到既有 portfolio weights"""
    adjusted = {}
    for sid, w in weights.items():
        adj = fraud_score_to_weight_adjustment(fraud_scores.get(sid, 0.0))
        adjusted[sid] = w * adj
    # 重新標準化
    total = sum(adjusted.values())
    if total > 0:
        return {k: v / total for k, v in adjusted.items()}
    return adjusted


# ====== LLM Caller (Real) ======

def call_claude_api(text: str, stock_id: str) -> Optional[dict]:
    """真實 Claude API 呼叫（需 ANTHROPIC_API_KEY）"""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed")
        return None
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return None
    client = anthropic.Anthropic(api_key=key)
    prompt = FRAUD_DETECTION_PROMPT.format(stock_id=stock_id, text=text)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text
    # 提取 JSON 區塊
    import re
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"raw_response": raw, "parse_error": True}
    return {"raw_response": raw, "no_json": True}


# ====== Mock LLM (for demo without API) ======

def mock_llm_evaluator(text: str, stock_id: str, seeded_score: Optional[float] = None) -> dict:
    """
    Rule-based fake LLM for demo purposes.
    用簡單關鍵字計分模擬 LLM 行為。
    """
    if seeded_score is not None:
        score = seeded_score
    else:
        # 基於關鍵字密度的簡單評分
        red_flag_terms = [
            "預期", "可能", "未來", "下半年", "明年", "預計",  # 時間遠離
            "外部因素", "市場狀況", "宏觀", "不可抗力",  # 責任轉嫁
            "進一步說明", "會計處理變更", "重新分類",  # 異常會計
        ]
        green_flag_terms = ["實現", "完成", "已達成", "本季", "本年度"]
        text_lower = text.lower() if text else ""
        red_count = sum(text_lower.count(t.lower()) for t in red_flag_terms)
        green_count = sum(text_lower.count(t.lower()) for t in green_flag_terms)
        # 模擬 LLM 評分
        ratio = red_count / max(red_count + green_count, 1)
        score = min(0.95, max(0.05, ratio * 1.2 + random.uniform(-0.1, 0.1)))

    if score < 0.3:
        rec = "low_risk"
        concerns = ["敘事一致、數字明確"]
    elif score < 0.5:
        rec = "monitor"
        concerns = ["有部分模糊措辭", "若干時間遠離承諾"]
    elif score < 0.8:
        rec = "high_risk"
        concerns = ["大量責任轉嫁", "過度樂觀詞彙密度高", "修正性敘述被淡化"]
    else:
        rec = "exclude"
        concerns = ["異常會計處理說明", "管理層第一人稱避免", "敘事與數字嚴重背離"]

    return {
        "stock_id": stock_id,
        "fraud_score": float(round(score, 3)),
        "primary_concerns": concerns,
        "specific_quotes": [f"[mock] 第{i}段疑似可疑原文..." for i in range(1, 4)],
        "audit_recommendation": rec,
        "_evaluator": "mock",
    }


# ====== Demo Pipeline ======

def demo_pipeline(use_mock: bool = True):
    """
    示範：
    1. 取一組樣本股票（這裡用 hardcoded mock 樣本）
    2. 假設我們有他們的年報/法說稿（mock）
    3. 對每檔評分
    4. 套用至假設的 portfolio
    5. 輸出修正前後的 weights 對比
    """
    print("=" * 60)
    print(f"實驗 #8：LLM 舞弊偵測 Scaffold (mode={'MOCK' if use_mock else 'REAL_API'})")
    print("=" * 60)

    # Mock 樣本：模擬 5 檔台股 + 假設文本內容
    # 真實情境會從 MOPS 取每年 3-4 月的年報 + 每季法說稿
    mock_samples = [
        ("2330", "本年度毛利率達 53%，已超越預期目標。我們在先進製程繼續領先。", 0.10),
        ("XXXX1", "外部市場波動劇烈，下半年可能會有改善，預期明年將回到正軌。會計處理上做了重新分類以符合新準則。", 0.85),
        ("2454", "聯發科本季完成天璣 9400 出貨，營收年增 35%，符合先前指引。", 0.18),
        ("YYYY1", "市場狀況不佳，未來可能進一步調整。本季度我們對若干會計科目做了進一步說明，預計下半年情況改善。", 0.72),
        ("2317", "鴻海伺服器組裝 AI 訂單動能強，本年度達成率 102%，已驗收完畢。", 0.22),
    ]

    print(f"\n處理 {len(mock_samples)} 檔樣本…\n")
    results = []
    for sid, text, seeded in mock_samples:
        if use_mock:
            r = mock_llm_evaluator(text, sid, seeded_score=seeded)
        else:
            r = call_claude_api(text, sid)
            if r is None:
                print(f"[!] {sid} 跳過：API 失敗")
                continue
        print(f"  [{sid}] score={r['fraud_score']:.2f} → {r['audit_recommendation']}")
        results.append(r)

    # 假設 portfolio：5 檔等權
    weights_before = {s[0]: 0.20 for s in mock_samples if any(r['stock_id'] == s[0] for r in results)}
    fraud_scores = {r['stock_id']: r['fraud_score'] for r in results}
    weights_after = apply_to_portfolio(weights_before, fraud_scores)

    print("\n=== Portfolio Weight 修正對比 ===")
    print(f"{'stock_id':<10}{'fraud_score':<14}{'recommend':<14}{'before':<10}{'after':<10}")
    for sid in weights_before:
        score = fraud_scores.get(sid, 0)
        rec = next((r['audit_recommendation'] for r in results if r['stock_id'] == sid), 'unknown')
        print(f"  {sid:<8}{score:<14.2f}{rec:<14}{weights_before[sid]:<10.3f}{weights_after.get(sid,0):<10.3f}")

    excluded = [sid for sid in weights_before if weights_after.get(sid, 0) == 0]
    downweighted = [sid for sid in weights_before if 0 < weights_after.get(sid, 0) < weights_before[sid] / sum(weights_before.values())]
    print(f"\n排除：{len(excluded)} 檔 ({excluded})")
    print(f"降權：{len(downweighted)} 檔")

    # 套用節省效果估算
    # 假設「被排除的股票」未來 1 年平均報酬比 panel 平均低 -15%（基於論文 fraud 案的歷史）
    n_total = len(weights_before)
    n_excluded = len(excluded)
    expected_savings_pp = (n_excluded / n_total) * 15  # 簡化估算
    print(f"\n=== 預期效益 ===")
    print(f"  排除比例：{n_excluded/n_total*100:.0f}%")
    print(f"  假設 fraud 股 1 年績效落後市場 15 pp")
    print(f"  → 預期年化績效改善：{expected_savings_pp:.2f} pp")

    summary = {
        'experiment': '#8 llm_fraud_detection (scaffold + mock)',
        'mode': 'mock' if use_mock else 'real_api',
        'n_samples': len(mock_samples),
        'n_evaluated': len(results),
        'evaluations': results,
        'portfolio_changes': {
            'excluded': excluded,
            'downweighted': downweighted,
            'expected_savings_pp_annual': expected_savings_pp,
        },
        'production_requirements': {
            'data': 'MOPS annual reports + 法說會逐字稿（需新增 scraper）',
            'api': 'ANTHROPIC_API_KEY (Claude Opus 4.7) or OpenAI key',
            'cost_estimate_usd_per_year': 200,
            'eta_days_to_full_impl': 6,
        },
        'paper_claim': 'LLM outperforms humans in fraud detection; resistant to motivated pressure',
        'verdict': 'SCAFFOLD_READY_AWAITING_DATA_AND_API',
    }
    with open(OUT_DIR / "exp08_summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n結果已存至 {OUT_DIR}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="Use real Claude API (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM (default)")
    args = parser.parse_args()
    use_mock = not args.real
    if not use_mock and not os.getenv("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set. Falling back to mock mode.")
        use_mock = True
    demo_pipeline(use_mock=use_mock)
