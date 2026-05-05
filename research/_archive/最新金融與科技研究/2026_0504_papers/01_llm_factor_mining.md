---
title: 從假設到因子：受約束 LLM 代理在加密貨幣市場
authors: Yikuan Huang, Zheqi Fan, Kaiqi Hu, Yifan Ye
venue: arXiv q-fin.PM
arxiv_id: 2604.26747
date: 2026-04-30
url: https://arxiv.org/abs/2604.26747
category: factor mining / LLM agent
feasibility: 🟡 中（須移植到台股、且去除 LLM 依賴的版本可立即跑）
priority: W2
---

# 1. 論文摘要

研究團隊用 LLM 從**自然語言假設**（如「上市量大時隔夜跳空更可能延續」）出發，自動生成可被回測的因子表達式（formula），再用統計篩選保留有效因子。
LLM 被「受約束」在一個合法因子文法（grammar of factors）裡操作，避免無意義輸出。

**核心三步驟**：
1. **Hypothesis → Expression**：LLM 把假設轉為合法數學算子組合（rolling/diff/zscore/quantile 等）
2. **In-sample IC 篩選**：每個因子計算 Spearman IC，過 0.03 進入候選
3. **Combination 與正交化**：用 OLS 對既有因子組正交化、剔除 redundancy

**論文 OOS 結論**：在 BTC/ETH 高頻資料（1m / 5m）上，LLM 生成因子組合 OOS Sharpe 1.6（vs. 經典 alpha101 baseline 0.9）。

# 2. 對 GiS 量化系統的啟示

我們現有的因子流程是**硬編碼**的（突破、量價、法人）。本論文提供一條「**自動探索新因子**」的路徑——但 LLM API 成本高（每次嘗試 ≈ USD 0.02）。

關鍵 insight：**真正關鍵的不是 LLM，而是「合法因子文法 + 自動 IC 篩選」這個架構**。可以用：
- 隨機因子表達式生成器（grammar 內隨機抽樣，不靠 LLM）→ 工程實作快、零 API 成本
- 之後若預算允許再用 LLM 提案

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🟢 高 | 22 年日線 × 2705 檔即可 |
| LLM 依賴 | 🔴 強 | 但**可解耦**：先做 grammar-based 隨機因子探索 |
| 工期（簡化版）| 5–7 天 | 含 IC 篩選與正交化 |
| 與既有流程契合 | 🟢 高 | 直接接到現有 scanners |

# 4. 實作策略（先做 LLM-free 版）

```python
# experiments/exp01_llm_factor_mining.py 落地內容：
# 1. 定義因子文法：Op = {ts_mean, ts_std, ts_rank, delta, log, sign, abs}
#                  Atom = {open, high, low, close, volume, amount}
# 2. 隨機抽樣 1000 個 expression（深度 ≤ 3）
# 3. 對每個 expression 在 2020-2024 計算每日截面 IC
# 4. 過 IC > 0.03 且 |t-stat| > 2 的留下
# 5. 對保留因子做正交化（PCA），最終得到 K 個獨立因子
# 6. OOS（2025-04 ~ 2026-04）測試組合 IC 與 long-short 累積報酬
```

# 5. 與原論文對比設計

| 項目 | 原論文 | 我方 POC |
|------|--------|----------|
| 標的 | BTC/ETH 高頻 | 台股 2705 檔日線 |
| 因子來源 | LLM proposal | 隨機 grammar sampling |
| 篩選 | IC > 0.03 | 同 |
| OOS Sharpe 目標 | 1.6 | 預期 0.6-1.0（日線資料） |

**對比結論將驗證**：去除 LLM 後，純粹 grammar + IC 篩選能否回收論文 50%+ 的價值。
