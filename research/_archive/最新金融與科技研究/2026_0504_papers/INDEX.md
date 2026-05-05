---
title: 2026-05-04 週報資料夾導覽
date: 2026-05-04
analyst: GiS Quant Research
principle: 能實作的一定要做；先 POC、再與原論文對比
---

# 資料夾導覽（INDEX）

```
2026_0504_papers/
├── INDEX.md                     ← 本檔（先看這個）
├── 00_feasibility_summary.md    ← 9 篇可行性總覽
├── 01_llm_factor_mining.md      ← 9 篇個別實作計畫
├── 02_levered_etf_anomaly.md
├── 03_higher_moment_portfolio.md
├── 04_multivariate_kelly.md
├── 05_gamma_laplace_returns.md
├── 06_motif_risk_spillover.md
├── 07_adversarial_prediction.md
├── 08_news_social_sentiment.md
├── 09_b2broker_news.md
└── experiments/                 ← POC Python 與實證結果
    ├── RESULTS.md               ← 7 篇 POC 結果彙總（先看這個）
    ├── exp01_factor_grammar.py + .json + .csv
    ├── exp02_levered_etf.py + .json + .csv  （另：exp06_levered_etf.py 為平行版）
    ├── exp03_higher_moment.py + .json
    ├── exp04_kelly_sigmoidal.py             （另：exp07_kelly_sigmoidal.py 為平行版）
    ├── exp05_gamma_laplace.py + .json       （另：exp08_gamma_laplace.py 為平行版）
    ├── exp06_motif_spillover.py + _results.csv + .json  （另：exp04_motif_spillover.py 為平行版）
    ├── exp07_context_features.py + .json
    └── （平行 agent 額外）exp02_vpmacd / exp03_hrp_crisp / exp05_tradingagents_adapter
```

## 主要交付：兩份 HTML 週報

兩份報告皆已產出：

| 檔案 | 視角 | 涵蓋論文 | 主要產出時間 |
|------|------|---------|-------------|
| [`2026_0504_research_weekly.html`](../2026_0504_research_weekly.html) | v1（11-paper 平行 agent） | 11 篇 → 7 POC + TradingAgents PoC | 2026-05-04 16:26 |
| [`2026_0504_research_weekly_v2.html`](../2026_0504_research_weekly_v2.html) | v2（9-paper digest 對應） | 9 篇 → 7 POC | 2026-05-04 16:42 |

**閱讀建議**：兩份在重疊主題（Levered ETF / Kelly / Gamma-Laplace / Motif）結論一致；
v2 對應到使用者一開始貼的 9-paper digest，v1 的範圍更廣涵蓋一些上週遺漏項目。
若只看一份，建議讀 **v2**（與本週原始指示對齊），再參考 v1 的 TradingAgents adapter PoC。

## 9 篇實作對應實證狀態

| # | 論文 | 個別計畫 MD | 實證實作 | 狀態 |
|---|------|------------|---------|------|
| 1 | LLM 因子挖掘 | [01_llm_factor_mining.md](01_llm_factor_mining.md) | [experiments/exp01_factor_grammar.py](experiments/exp01_factor_grammar.py) | ✅ Grammar 移植版完成 |
| 2 | 槓桿 ETF 拖曳 | [02_levered_etf_anomaly.md](02_levered_etf_anomaly.md) | [experiments/exp02_levered_etf.py](experiments/exp02_levered_etf.py)（+ exp06 平行版）| ✅ 完成（rolling corr 0.999）|
| 3 | 高階矩組合 | [03_higher_moment_portfolio.md](03_higher_moment_portfolio.md) | [experiments/exp03_higher_moment.py](experiments/exp03_higher_moment.py) | ✅ 完成（DD ↓5.83pp）|
| 4 | Multivariate Kelly | [04_multivariate_kelly.md](04_multivariate_kelly.md) | [experiments/exp04_kelly_sigmoidal.py](experiments/exp04_kelly_sigmoidal.py)（+ exp07 平行版）| ✅ 完成（飽和 N≈100）|
| 5 | Gamma-Laplace VaR | [05_gamma_laplace_returns.md](05_gamma_laplace_returns.md) | [experiments/exp05_gamma_laplace.py](experiments/exp05_gamma_laplace.py)（+ exp08 平行版）| ✅ 完成（OOS LL +5.43%）|
| 6 | Motif 風險溢出 | [06_motif_risk_spillover.md](06_motif_risk_spillover.md) | [experiments/exp06_motif_spillover.py](experiments/exp06_motif_spillover.py)（+ exp04 平行版）| ✅ 完成（density +41.6%）|
| 7 | Context Features 預測 | [07_adversarial_prediction.md](07_adversarial_prediction.md) | [experiments/exp07_context_features.py](experiments/exp07_context_features.py) | ✅ 階段 A 完成（IC +23%）|
| 8 | 新聞 vs 社群情緒 | [08_news_social_sentiment.md](08_news_social_sentiment.md) | — | ⏳ 缺中文文本，延後 |
| 9 | B2BROKER AI 平台 | [09_b2broker_news.md](09_b2broker_news.md) | — | ⚪ 產業情報 |

## 推薦閱讀順序

### 給投資決策者
1. [`../2026_0504_research_weekly_v2.html`](../2026_0504_research_weekly_v2.html) — v2 完整週報（含 7 POC verdict + 5 件可上線）
2. [00_feasibility_summary.md](00_feasibility_summary.md) — 9 篇可行性與資料就緒度
3. [experiments/RESULTS.md](experiments/RESULTS.md) — 兩組實驗對照與彙總

### 給量化研究員
1. [experiments/RESULTS.md](experiments/RESULTS.md) — 兩組實驗對照
2. 個別計畫 MD：依研究主題挑（如 [02_levered_etf_anomaly.md](02_levered_etf_anomaly.md)）
3. 對應 Python 原始碼：可獨立重跑

### 給工程實作者
1. [00_feasibility_summary.md](00_feasibility_summary.md) — 工期、優先序
2. [experiments/RESULTS.md](experiments/RESULTS.md) §「立刻可上線的 5 件 production action」
3. 對應實驗 .py 檔（含完整 SQL/數值流程）

## 重跑指令

```bash
# 環境（Windows / git bash）
cd D:\claude
# Python 3.12 + numpy 2.4 + pandas 3 + scipy 1.17 + sklearn 1.8 + networkx 3.6 (本週新裝)

cd "tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0504_papers/experiments"

# 立即可跑（每個 1-3 分鐘）
python -X utf8 exp02_levered_etf.py        # 槓桿 ETF 拖曳
python -X utf8 exp04_kelly_sigmoidal.py    # Kelly Sigmoidal
python -X utf8 exp05_gamma_laplace.py      # Asym-Laplace VaR

# 較久（5-8 分鐘）
python -X utf8 exp03_higher_moment.py      # 高階矩組合
python -X utf8 exp06_motif_spillover.py    # Motif risk
python -X utf8 exp07_context_features.py   # Context features

# 較久（10 分鐘+，含 800 隨機 expression IC 計算）
python -X utf8 exp01_factor_grammar.py     # Grammar factor mining

# 平行 agent 版本（亦可跑，比較結果）
python -X utf8 exp06_levered_etf.py
python -X utf8 exp07_kelly_sigmoidal.py
python -X utf8 exp08_gamma_laplace.py
```

## 7 篇 POC 結果速覽

| # | 主題 | 我方實證 vs 原論文 | Verdict |
|---|------|-----------------|---------|
| 1 | LLM 因子挖掘（grammar 移植）| OOS Sharpe 0.44 vs 論文 1.6；4/4 sign 一致 | 🟡 部分（LLM 在 proposal 智能上有真實價值）|
| 2 | 槓桿 ETF 波動拖曳 | rolling corr 0.999；2× drag 9.75%/年 vs 論文公式 9.27% | 🟢 完全成立 |
| 3 | 高階矩組合（Yau）| MaxDD ↓5.83pp 吻合論文 3-5pp；Sharpe +0.016 | 🟢 DD 完全 / Sharpe 部分 |
| 4 | Kelly Sigmoidal | sigmoidal 成立但飽和 N≈100（vs 論文 N≈30）| 🟡 公式對／點不同 |
| 5 | Gamma-Laplace VaR | OOS LL +5.43% 落在論文 3-8% 區間 | 🟢 完全成立 |
| 6 | Motif 風險溢出 | density +41.6% (p=0.019)；clustering +27% / transitivity +14% | 🟢 完全成立 |
| 7 | Context Features 預測 | IC 0.122→0.150 (+23%)；法人+殖利率合計 42% 重要性 | 🟢 完全成立 |

**統計**：🟢 完全 4 篇、🟡 部分 2 篇、🔴 未做 2 篇（缺中文資料/僅新聞）

## 立刻可上線（5 件）

1. **替換 Normal VaR 為 Asym-Laplace 邊際**（exp05）— 2 天，OOS LL +5.43%
2. **法人 zscore + 殖利率曲線進入預測模型**（exp07）— 2 天，IC +23%
3. **Motif 網路密度寫入 macro_indicators 作為預警**（exp06）— 3 天，危機前 +41.6%
4. **槓桿 ETF 拖曳客戶教材化**（exp02）— 2 天，rolling corr 0.999
5. **高階矩偏好加入組合 module**（exp03）— 3 天，MaxDD ↓5.83pp

## 已知限制

1. **OOS 期間僅 13-16 個月**（2025-04 ~ 2026-04 多頭尾段）
2. **scanner DB 沒有 leveraged ETF 個股資料**（00631L/00632R）→ #2 用 0050 合成
3. **broker_trades 僅 2025-06 起** → 部分 broker-based 因子無法做長 IS
4. **無中文新聞/社群文本** → #8 必須延後到 W3-W4 爬蟲建立後
5. **macro_indicators 部分缺值** → #7 regime feature importance 為 0

## 下週優先級

| 優先 | 行動 | 工期 | 對應 POC |
|------|------|------|---------|
| 1 | scanners/risk.py 改用 Laplace VaR | 2 天 | #5 |
| 2 | feature_engineering 新增法人/殖利率 features | 2 天 | #7 |
| 3 | macro_indicators 新增 motif_density 欄位 + weekly 排程 | 3 天 | #6 |
| 4 | Levered ETF fact sheet PDF | 2 天 | #2 |
| 5 | portfolio_module 新增 skew/kurt option | 3 天 | #3 |
| 6 | 中文新聞爬蟲（鉅亨/PTT/MOPS）| 5 天 | #8（資料準備）|
| 7 | LLM proposal PoC（50 檔）| 7 天 | #1 |

## 與 04-27 週報的銜接

上週 7 篇實作中：
- ✅ #2 後篩選組合 + #3 制度回測 已上線（與本週新增無衝突）
- ⏳ #4 多代理 LLM 推薦 仍排程中
- 本週新增的 5 件可上線項目，與上週 unfinished 互補

## 與兩組平行實驗的關係

詳見 [experiments/RESULTS.md](experiments/RESULTS.md) §「附錄：與平行實驗結果的對照」。
重點：兩組在 Levered ETF / Kelly / Gamma-Laplace / Motif 4 個重疊主題上**結論一致**。
本 INDEX 與 v2 HTML 以本週 9-paper digest 對應的版本為主軸。
