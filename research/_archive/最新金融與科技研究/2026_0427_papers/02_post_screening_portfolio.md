---
paper_id: 02
title: 後篩選組合選擇
title_en: Post-Screening Portfolio Selection
arxiv: 2604.17593
date: 2026-04-21
category: q-fin.PM
feasibility: VERY_HIGH
action: 立刻實作
priority: 1
eta_days: 2-3
---

# 02 · 後篩選組合選擇（修正既有因子流程的選擇偏誤）

## 論文要旨

當你先用篩選器（價值因子排序、流動性門檻、ESG 過濾⋯）縮小股票池後，再做組合最佳化，**標準的 mean-variance 估計會帶有顯著的「選擇偏誤」**，導致樣本外績效高估。論文提出修正方法（post-selection inference），把篩選步驟對統計分布的扭曲明確補回去。

## 為何重要（與我方因子契合）

- 我們**所有**因子流程都是「先篩、再排、再選」三段式 → **這正是論文打到的痛點**
- 過去回測夏普可能高估 0.2–0.4，**修正後 alpha 還是不是 alpha 是關鍵**
- 改善很小（程式碼層面），但對 production 上線把關意義巨大

## 可行性評估

| 項目 | 狀態 |
|------|------|
| 既有 backtest 引擎 | ✅ tw-stock-scanner |
| 因子 panel 資料 | ✅ 已有 |
| 統計套件需求 | ✅ `scipy`, `statsmodels` 即可 |
| 計算成本 | 微不足道 |

**結論：極高可行性 — 純程式碼修正，2-3 天可上線**

## 實作步驟

### Phase A：理論套用（半天）
論文核心是 Lee, Sun, Sun, Taylor (2016) 的 polyhedral lemma 應用到投組：
- 篩選 = 線性不等式 `A·X ≤ b`（例：「PE < 20」、「市值 > 50e」）
- 條件分布服從 truncated normal
- 修正點估計與信賴區間

### Phase B：實作（1 天）
```python
# src/tw-stock-scanner/portfolio/post_screening_inference.py

import numpy as np
from scipy.stats import truncnorm

def post_selection_mean(returns, screen_mask, screen_threshold):
    """
    篩選後的條件期望值修正
    returns: (T, N) panel
    screen_mask: bool (N,) 通過篩選
    screen_threshold: 篩選門檻值（如 PE<20 對應的 PE 排序）
    """
    selected = returns[:, screen_mask]
    # 標準均值
    mu_naive = selected.mean(axis=0)
    sigma = selected.std(axis=0)
    
    # 條件均值（truncated）：因為 selection 是「分數 > 門檻」
    # 用 truncnorm 反推未截斷分布的 mu
    a = (screen_threshold - mu_naive) / sigma
    b = np.inf
    correction = sigma * truncnorm.mean(a, b)
    
    mu_corrected = mu_naive - correction
    return mu_corrected

def post_screening_optimize(returns, screen_func, lambda_=1.0):
    """
    Markowitz with post-selection corrected expectations
    """
    mask = screen_func(returns)
    mu_corr = post_selection_mean(returns, mask, ...)
    cov = np.cov(returns[:, mask].T)
    # 標準 mean-variance with corrected mu
    w = np.linalg.solve(lambda_ * cov, mu_corr)
    return w / w.sum()
```

### Phase C：歷史驗證（1 天）
- 取 2015–2026 月度資料
- 三種比較：
  - (a) 不篩選 → MV 最佳化
  - (b) 篩選 → MV 最佳化（既有作法）
  - (c) 篩選 → post-screening MV 最佳化
- 看 (b) 與 (c) 在樣本外的 Sharpe / IR / drawdown 差異

### Phase D：上線（半天）
- 把現有所有 factor strategies 的 portfolio construction 改用 (c)
- 監控指標：上線後 3 個月、6 個月實際 vs 預期績效落差是否縮小

## 預期產出

- `src/tw-stock-scanner/portfolio/post_screening_inference.py`
- 對比報告：純 MV vs post-screening MV，2015–2026 月度
- 既有 5 個 production 策略的回測重跑結果

## 預期效果

- 樣本外 Sharpe 預期下修 0.1–0.3（這是「被誤算的 alpha」消失，不是壞事）
- Drawdown 預期估計更貼近實際
- **長期：避免誇大策略而誤上線**

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| 修正過頭使估計過於保守 | 用 cross-validation 校準 shrinkage 參數 |
| 篩選函數非線性 | Linear approximation，或 bootstrap-based 修正 |

## 參考

- Paper: https://arxiv.org/abs/2604.17593
- 相關：Lee et al. (2016) "Exact post-selection inference, with application to the lasso" Annals of Statistics
- 相關：Tibshirani et al. (2018) Selective Inference for Statistical Learning
