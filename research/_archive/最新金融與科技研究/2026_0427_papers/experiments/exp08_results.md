---
paper_id: 08
title: LLM 財報舞弊偵測 — Scaffold + Mock Demo
arxiv: 2604.20652
date_run: 2026-04-28
verdict: SCAFFOLD_READY 🔧 框架可跑，待文本+API 驗證
---

# #8 LLM 財報舞弊偵測 — Scaffold + Mock Demo

## 論文宣稱
LLM（GPT-4o / Claude）在識別財務舞弊文本（含蓄式語意操弄、敘事偏離數字、抗壓性低的揭露）上**顯著優於人類分析師**，且對「老闆要求說好話」式的壓力誘導抵抗力強。

## 我方實作狀態

### ✅ 已完成
- 9 維度 fraud detection prompt
- JSON output schema
- Portfolio weight 整合邏輯
- Real Claude API caller（待 ANTHROPIC_API_KEY）
- Mock LLM evaluator（rule-based for demo）

### ⏳ 待外部資源
- ❌ MOPS 年報/法說稿爬蟲（無真實文本）
- ❌ ANTHROPIC_API_KEY 設定（無 API 可呼叫）

## 框架核心

### 9 維度檢測 prompt

| 維度 | 檢測重點 |
|------|---------|
| 數字一致性 | 敘事與表上數字是否矛盾 |
| 語意模糊度 | 大量模糊措辭逃避具體承諾 |
| 責任轉嫁 | 歸咎外部因素過於頻繁 |
| 時間遠離 | 「下半年/明年」承諾頻率 |
| 複雜度噪音 | 過度技術術語掩蓋簡單問題 |
| 第一人稱避免 | 管理層避免「我承擔/我決定」 |
| 過度樂觀詞彙 | 超出歷史常態的正面密度 |
| 修正性敘述 | 對前期承諾的修正被淡化 |
| 異常會計處理說明 | 過多篇幅解釋會計變更 |

### Output Schema (JSON)

```json
{
  "stock_id": "2330",
  "fraud_score": 0.0-1.0,
  "primary_concerns": ["..."],
  "specific_quotes": ["最可疑的 3 句原文"],
  "audit_recommendation": "low_risk|monitor|high_risk|exclude"
}
```

### Portfolio 整合規則

| Score 區間 | 行動 | 權重調整 |
|-----------|------|---------|
| < 0.3 | low_risk | × 1.0 |
| 0.3 - 0.5 | monitor | × 0.8 |
| 0.5 - 0.8 | high_risk | × 0.5 |
| ≥ 0.8 | exclude | × 0.0 |

## Mock Demo 結果（5 檔樣本）

| stock_id | 文本特徵 | fraud_score | recommendation | weight before | weight after |
|----------|---------|-------------|----------------|---------------|--------------|
| 2330 | 數字明確、實現承諾 | 0.10 | low_risk | 0.200 | 0.286 |
| **XXXX1** | **大量模糊措辭、會計重新分類** | **0.85** | **exclude** | 0.200 | **0.000** |
| 2454 | 達成率超預期 | 0.18 | low_risk | 0.200 | 0.286 |
| YYYY1 | 責任轉嫁、時間遠離 | 0.72 | high_risk | 0.200 | 0.143 |
| 2317 | 已驗收完畢、明確 | 0.22 | low_risk | 0.200 | 0.286 |

> **效果**：5 檔組合中，1 檔被排除、1 檔降權重至 50%。可立即看到組合風險降低。

## 預期效益（待真實驗證）

假設：
- LLM 識別出的 high_risk 股票，未來 1 年績效平均落後市場 15 pp（基於論文歷史 fraud 案）
- 1500 檔台股中約 1-3% 為 high_risk

| 指標 | 預期值 |
|------|-------|
| 每季 high_risk 排除 | 5-15 檔 |
| 既有因子組合 Sharpe 提升 | +0.05 ~ +0.15 |
| Max drawdown 改善 | -10% ~ -20% |
| LLM API 年成本（Claude Opus） | USD 200 |

## Verdict：🔧 框架可跑，待外部資源驗證

## Production 化所需

| 項目 | 狀態 | 工期 |
|------|------|------|
| MOPS 年報爬蟲 | ❌ | 2 天 |
| 法說會逐字稿爬蟲（cnYES/MoneyDJ）| ❌ | 1 天 |
| ANTHROPIC_API_KEY 設定 | ⏳ | 0.1 天 |
| 1500 檔批次處理 pipeline | ❌ | 1 天 |
| 已知舞弊案回溯驗證（樂陞、康友、ABT）| ❌ | 2 天 |
| 整合至 production portfolio | ❌ | 1 天 |
| **總計** | | **6 天** |

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| LLM 對中文台股財報語境陌生 | Few-shot prompting，提供 3 個歷史舞弊案例 |
| False positive 誤殺好公司 | high_risk 需人工複核才剔除 |
| 訴訟風險（給人「指控」）| 內部使用，使用模糊話術「需進一步審視」|
| LLM cutoff 偏誤 | 嚴格 walk-forward（依 #11 ChatGPT 時光膠囊原則）|

## 道德與合規

- **僅供內部投資決策參考**
- **不對外發布、不向第三方提供**
- **任何 high_risk 標記不等同「公司有舞弊」**，僅是統計風險指標

## 檔案

- 程式：[exp08_fraud_detection_scaffold.py](exp08_fraud_detection_scaffold.py)（含 mock + real API）
- Summary JSON：[exp08_summary.json](exp08_summary.json)

## 重跑

```bash
# Mock 模式（不花 API 費用）
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp08_fraud_detection_scaffold.py --mock

# 真實 LLM 模式（需設 ANTHROPIC_API_KEY 環境變數）
ANTHROPIC_API_KEY=sk-ant-... python ...exp08_fraud_detection_scaffold.py --real
```
