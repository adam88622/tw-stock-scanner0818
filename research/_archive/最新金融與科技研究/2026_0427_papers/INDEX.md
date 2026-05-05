---
title: 2026-04-27 週報資料夾導覽
date: 2026-04-28
---

# 資料夾導覽（INDEX）

```
2026_0427_papers/
├── INDEX.md ← 本檔（先看這個）
├── 00_feasibility_summary.md ← 12 篇可行性總覽
├── 01-12_*.md ← 12 篇個別實作計畫（含論文摘要、實作步驟、預期效益）
└── experiments/ ← 實證對照（已實作 6 篇）
    ├── RESULTS.md ← 6 篇實證彙總（先看這個）
    ├── exp01_*.py + exp01_results.md + 結果 CSV/JSON
    ├── exp02_*.py + exp02_results.md + 結果 CSV/JSON
    ├── exp03_*.py + exp03_results.md + 結果 CSV/JSON
    ├── exp06_*.py + exp06_results.md + 結果 CSV/JSON
    ├── exp07_*.py + exp07_results.md + 結果 CSV/JSON
    └── exp08_*.py + exp08_results.md + 結果 JSON
```

## 主要交付：HTML 週報

[../2026_0427_research_weekly.html](../2026_0427_research_weekly.html) — 16 頁完整週報，含實證對照章節 + 落地路線

## 12 篇實作計畫對應實證狀態

| # | 論文 | 計畫 MD | 實證實作 | 狀態 |
|---|------|---------|---------|------|
| 1 | LLM 語意網路 | [01_*.md](01_cross_stock_llm_network.md) | [experiments/exp01_*.py](experiments/exp01_cross_stock_predictability.py) | ✅ 已跑（相關代理）|
| 2 | 後篩選組合 | [02_*.md](02_post_screening_portfolio.md) | [experiments/exp02_*.py](experiments/exp02_post_screening.py) | ✅ 已跑、論文成立 |
| 3 | 制度回測 | [03_*.md](03_structured_backtest_eval.md) | [experiments/exp03_*.py](experiments/exp03_regime_backtest.py) | ✅ 已跑、論文成立 |
| 4 | 多代理 LLM | [04_*.md](04_multi_agent_llm_signal.md) | — | ⏳ **延後**（需 LLM API 成本 USD 50+/月）|
| 5 | Machine Spirits 投機 | [05_*.md](05_machine_spirits_speculation.md) | — | 觀察類，不需實作 |
| 6 | LOB 制度偵測 | [06_*.md](06_lob_microstructure_regime.md) | [experiments/exp06_*.py](experiments/exp06_microstructure_regime.py) | ✅ 已跑（日線代理）|
| 7 | 動態網路風險 | [07_*.md](07_dynamic_network_markers.md) | [experiments/exp07_*.py](experiments/exp07_dynamic_network.py) | ✅ 已跑、部分成立 |
| 8 | LLM 舞弊偵測 | [08_*.md](08_llm_fraud_detection.md) | [experiments/exp08_*.py](experiments/exp08_fraud_detection_scaffold.py) | ✅ Scaffold 完成 |
| 9 | AI 交易泡沫 | [09_*.md](09_ai_trading_bubbles.md) | — | 觀察類，不需實作 |
| 10 | Agentic AI 綜述 | [10_*.md](10_agentic_ai_survey.md) | — | 內部 reference |
| 11 | ChatGPT 時光膠囊 | [11_*.md](11_chatgpt_time_capsule.md) | — | 設計準則 |
| 12 | Moomoo API Skills | [12_*.md](12_moomoo_api_skills.md) | — | 產業情報 |

## 為何 #4 延後實作

**多代理 LLM 推薦**（arXiv:2604.17327）需要：
- 多個 LLM API（GPT/Claude/Gemini）並行呼叫
- 50 檔 PoC 規模約 USD 50/月
- 1500 檔擴大版約 USD 800/月

由於：
1. 需編列預算與決定 base model 選擇
2. 工期較長（7-10 天，需先建立 RAG 結構化輸入）
3. 在 #1 LLM 語意網路（更基礎）實作前先做沒有意義

**列為 W4 排程實作**，等 #1 完成 + 預算核可後再做 PoC。

## 推薦閱讀順序

### 給投資決策者
1. [../2026_0427_research_weekly.html](../2026_0427_research_weekly.html) — 完整週報（包含實證 KPI、4 週路線）
2. [00_feasibility_summary.md](00_feasibility_summary.md) — 12 篇可行性總表

### 給量化研究員
1. [experiments/RESULTS.md](experiments/RESULTS.md) — 6 篇實證彙總
2. 個別實證 md：依需求挑 [exp02_results.md](experiments/exp02_results.md) 等
3. Python 原始碼：可獨立重跑

### 給工程實作者
1. [00_feasibility_summary.md](00_feasibility_summary.md) — 工期、優先序
2. 個別實作計畫：[02_*.md](02_post_screening_portfolio.md) 等（含程式碼骨架）
3. 對應實驗 .py：可作為實作起點

## 重跑說明

```bash
# 環境：Python 3.12 + pandas/numpy/scipy/networkx（皆已安裝）
cd D:\claude

# 立即可跑（不需外部資源）
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp02_post_screening.py
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp03_regime_backtest.py
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp06_microstructure_regime.py
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp08_fraud_detection_scaffold.py --mock

# 較久（5-10 分鐘，計算 60 日滑動相關矩陣）
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp01_cross_stock_predictability.py
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp07_dynamic_network.py

# 需 LLM API key
ANTHROPIC_API_KEY=sk-ant-... python ...exp08_fraud_detection_scaffold.py --real
```

## 數字漂移說明

實驗讀取 `db/scanner.db` 即時資料，DB 每日更新會使結果微幅變動：

| 實驗 | 首跑數值 | 2026-04-28 重跑 | 結論方向 |
|------|---------|----------------|---------|
| #2 估計準度 | 2.07x | 1.88x | 不變（corrected 仍比 naive 準）|
| #3 panic sharpe | -0.21 | -0.20 | 不變（顯著差於 bull）|
| 其他 | 大致相同 | 大致相同 | 不變 |

**論文 verdict 全部不受漂移影響**。若需鎖定數值可在實驗檔加 `WHERE date <= 'YYYY-MM-DD'` 條件。

## 已知限制

1. **資料期間僅 13 個月**（2025-04 ~ 2026-04）— 不含 COVID、烏俄戰爭等大事件
2. **OOS 期為台股多頭尾段** — 部分結果（如 #2 sharpe gap）方向偏特殊
3. **無 LOB tick 資料** — #6 量級需 SK COM 五檔開發後才能精確
4. **無中文新聞/財報文本** — #1 與 #8 的真實版本待 MOPS 爬蟲建立後才能跑

## 建議的下一步

| 優先序 | 行動 | 工期 |
|-------|------|------|
| 1 | 把 #2、#3 結論搬至 production（修因子 mu 估計、做策略健診）| 1-2 週 |
| 2 | tw-stock-scanner 補回 2018-2024 歷史日線 | 5 天爬取 |
| 3 | 建立 MOPS 重大訊息爬蟲（給 #1、#8 用）| 2-3 天 |
| 4 | SK COM 五檔開發（與 #6 整合）| 7 天 |
| 5 | 編列 LLM API 預算啟動 #4 多代理 PoC | 後續 |
