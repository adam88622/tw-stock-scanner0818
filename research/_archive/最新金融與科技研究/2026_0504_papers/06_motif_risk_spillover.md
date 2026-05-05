---
title: A Motif-Based Framework for Decomposing Risk Spillovers
authors: Y. Shao, Y. Yang, Y. Zhang
venue: arXiv q-fin
arxiv_id: 2604.25406
date: 2026-04-29
url: https://arxiv.org/abs/2604.25406
category: network risk / systemic risk
feasibility: 🟢 高（networkx + 相關矩陣）
priority: W1（立刻實作）
---

# 1. 論文摘要

傳統系統性風險度量（CoVaR, SRISK）只看 pairwise 連動。論文用**圖論 motif（小圖樣式）**分解風險溢出：

```
  3-node motifs:
    triad / star / chain
  4-node motifs:
    clique-4 / cycle-4 / fork / tail-triangle
```

每個 motif 對總體風險溢出的貢獻可被分離計算。實證發現：
1. **Triad（三角形）motif** 解釋了 ~40% 的系統風險，遠高於 pairwise
2. **危機前 6-8 週**，triad motif 密度上升 30-50%（可作為 leading indicator）
3. **產業 motif clustering** 比個股 pairwise 更能識別風險傳染路徑

# 2. 為什麼對 GiS 有意義

我們在 weekly-2026-04-28/exp07_dynamic_network.py 已經用 60 日滑動相關矩陣建構網路，但**只看 edge density / clustering coefficient**。

加入 motif 分解後：
- 可區分「市場全面恐慌」vs「特定產業壟」（前者 cycle motif 多，後者 star motif 多）
- 可作為 regime classifier 的 input feature
- 與現有 regime_history 表整合，提供風險升溫的預警

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🟢 極高 | 已有 daily_prices + 既有 corr 計算 |
| 工具 | 🟢 已裝 | networkx 3.6 已安裝 |
| 工期 | 2–3 天 | |
| 與既有流程契合 | 🟢 高 | 直接接到 regime_history |

# 4. 實作步驟

```python
# experiments/exp06_motif_spillover.py
# 1. 取 100 檔流動性最高股 2020-2024 日報酬
# 2. 60 日滑動：建相關矩陣 → threshold (|ρ|>0.5) → adjacency
# 3. 用 networkx 計算每個視窗的 motif 數：
#    - 三角形 (triangles)
#    - star-3 (一中心三邊)
#    - chain-4 (path of 4)
#    - clique-4
# 4. 對 weekly 標記 panic / normal regime（用 macro_indicators 或 max drawdown）
# 5. 比較不同 regime 下 motif 分布
# 6. 危機前 8 週 vs 平靜期，跑 t-test
```

# 5. 與原論文對比設計

| 項目 | 原論文（全球股市/債券）| 我方 POC（台股 top100）|
|------|----------------------|----------------------|
| 樣本網路 | 50 國 × 多資產 | 100 檔個股 |
| Triad 解釋風險 % | ~40% | 預期 25-45% |
| 危機前 motif 上升 | 30-50% | 預期 15-40% |
| Leading lag | 6-8 週 | 預期 4-8 週 |

**對比邏輯**：若 triad 在台股危機前也顯著上升，則 motif 密度可作為 regime classifier 的新特徵。
