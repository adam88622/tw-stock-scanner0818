---
title: Yau's Affine-Normal Descent for Large-Scale Higher-Moment Portfolio Optimization
authors: Yongheng Wang, Yujia Niu, Artan Sheshmani, Shing-Tung Yau
venue: arXiv q-fin
arxiv_id: 2604.25378
date: 2026-04-29
url: https://arxiv.org/abs/2604.25378
category: portfolio optimization / higher moments
feasibility: 🟢 高（純數學，scipy/cvxpy 即可）
priority: W1（立刻實作）
---

# 1. 論文摘要

傳統 Markowitz 只用一階（mean）與二階（variance）矩。但實證上，台股、加密、商品有**顯著偏度（skew）與峰度（kurt）**。

論文提出 **Affine-Normal Descent (AND)** 求解高維高階矩組合最佳化：

```
max_w  μᵀw - (λ₂/2)·wᵀΣw + (λ₃/6)·M3(w,w,w) - (λ₄/24)·M4(w,w,w,w)
s.t.   1ᵀw=1, w≥0
```

其中 M3 為共偏度張量、M4 為共峰度張量。傳統凸優化在 N>50 時數值不穩。AND 的貢獻：

1. **將高階張量收縮到 affine-normal 子空間**，數值複雜度從 O(N⁴) 降至 O(N²)
2. **保證收斂到 KKT 點**（雖非全域）
3. 在 S&P500 樣本中，相對 mean-variance 組合**OOS Sharpe +0.18**、**Max Drawdown 改善 3-5%**

# 2. 為什麼對 GiS 有意義

我們有 22 年 × 2705 檔日線。傳統 mean-variance 在台股有兩個痛點：

- 中小型股**極端正偏度**（少數飆股、大量平庸） → mean-variance 過度分散
- 部分熱門股**負偏度**（緩漲急跌） → mean-variance 低估風險

加入 skew 偏好（投資人偏好正偏 → 願意付溢價 → 對沖時可短負偏）、kurt 厭惡，可能改善現有組合。

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🟢 極高 | 任意 5–10 年回看期 |
| 數學複雜度 | 🟡 中 | 需自寫共偏度/共峰度張量計算 |
| 工期 | 3–4 天 | 含對比 mean-variance baseline |
| 與既有流程契合 | 🟢 高 | 直接增強現有 portfolio module |

# 4. 實作步驟

```python
# experiments/exp03_higher_moment.py
# 1. 選 50 檔流動性最高個股（ETF/0050 成分），2018-2024 為 IS
# 2. 估計 μ, Σ, M3, M4（co-skew / co-kurt 用 standardized 版本）
# 3. 用 scipy.optimize（projected gradient）解
#    （a）Mean-Variance baseline
#    （b）+ skew preference (λ₃=1)
#    （c）+ skew + kurt aversion (λ₃=1, λ₄=0.5)
# 4. 2025-04 ~ 2026-04 OOS 測試三組合
# 5. 比較 Sharpe / MaxDD / Calmar
```

# 5. 與原論文對比設計

| 項目 | 原論文 (S&P500, 500檔) | 我方 POC (台股 50 檔) |
|------|----------------------|---------------------|
| 樣本期間 | 2010-2024 | 2018-2024 IS, 2025-2026 OOS |
| 維度 | N=500 | N=50（先做小規模驗證） |
| Baseline Sharpe | ~0.85 | TBD |
| Higher-moment Sharpe gain | +0.18 | 預期 +0.05 ~ +0.15 |
| MaxDD 改善 | 3-5% | 預期 1-3% |

**對比邏輯**：若小規模能複製方向（即使量級小於原論文），代表方法可推到 N=200 全市場。
