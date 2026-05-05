---
title: Context-Integrated Adversarial Learning for Predictive Modelling of Stock Price Dynamics
authors: Alexis Lazanas, Spyros Christodoulou, Spyridon Karpouzis
venue: arXiv q-fin.ST
arxiv_id: 2604.22801
date: 2026-04-28
url: https://arxiv.org/abs/2604.22801
category: deep learning / prediction
feasibility: 🟡 中（完整版需深度學習；簡化版用 sklearn 即可驗證主要 idea）
priority: W2-W3
---

# 1. 論文摘要

純價格序列模型（LSTM/Transformer）易過擬合。論文加入**情境特徵（context features）**：

- 總體經濟（殖利率曲線、VIX、原油）
- 事件記號（FOMC、財報、stock split）
- 類股輪動（GICS sector momentum）

並用 **adversarial training**：在訓練時加入小擾動，迫使模型學到真正穩健的 pattern。

實證宣稱：
1. **OOS R² 比純價格 LSTM 高 15-25%**
2. **黑天鵝事件期間**（COVID/SVB）報酬率衰退從 -40% 改善至 -15%
3. Adversarial 部分提供 ~8% 的 R² 增益（其餘來自 context features）

# 2. 為什麼對 GiS 有意義

我們現有的 regime_history 是 reconstruction-error 一維。可借鑒此方法擴增：
- 加入 macro_indicators 表的殖利率曲線（已有）
- 加入 broker_trades 的主力買進濃度
- 加入 institutional 法人持股變化

簡化版可驗證：**單純加入這些 context 特徵，能否讓既有 baseline 模型 OOS R² 改善 5%+**

# 3. 可行性評估（兩階段）

**階段 A（簡化版，1 天可完成）**：
- 用 sklearn GradientBoostingRegressor + context features 預測未來 5 日報酬
- 與「純動量+均值回歸」baseline 對比
- **不做** adversarial training（只驗證 context 部分價值）

**階段 B（完整版，5–7 天）**：
- LSTM + adversarial training
- 需 PyTorch / TF
- 留待 W3-W4 評估

| 維度 | 階段 A | 階段 B |
|------|--------|--------|
| 資料 | 🟢 已有 | 🟢 已有 |
| 工期 | 1 天 | 5–7 天 |
| 算力 | CPU | GPU 較佳 |

# 4. 實作步驟（階段 A）

```python
# experiments/exp07_adversarial_proxy.py（簡化版）
# 1. target: 個股未來 5 日 fwd return
# 2. baseline features: ret_5, ret_20, vol_20, rsi_14
# 3. + context features:
#    - regime_history.regime (one-hot)
#    - macro_indicators T10Y3M
#    - institutional foreign_buy zscore
# 4. 跑 5-fold time series CV
# 5. 比較 baseline vs +context 的 R²、IC、Sharpe
```

# 5. 與原論文對比設計

| 項目 | 原論文（adversarial+context, S&P500）| 我方階段 A（context only, 台股）|
|------|----------------------------------|------------------------------|
| OOS R² 增益 | +15-25% | 預期 +5-10%（沒有 adversarial）|
| 黑天鵝表現改善 | +25 ppt | 預期 +5-10 ppt |
| 工期 | 完整 | 簡化 |

**對比邏輯**：階段 A 若能拿到 +5% R²，已足以證明 context features 對台股預測有顯著價值，再評估是否值得做階段 B。
