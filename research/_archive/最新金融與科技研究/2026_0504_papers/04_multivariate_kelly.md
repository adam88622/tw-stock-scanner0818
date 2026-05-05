---
title: Efficient Multivariate Kelly Optimization Reveals Sigmoidal Scaling Laws
authors: Ruben Tepelyan, Donny Lam
venue: arXiv q-fin
arxiv_id: 2604.24723
date: 2026-04-28
url: https://arxiv.org/abs/2604.24723
category: portfolio sizing / Kelly criterion
feasibility: 🟢 極高（μ, Σ 即可）
priority: W1（立刻實作）
---

# 1. 論文摘要

經典 Kelly criterion 對單資產解：`f* = μ/σ²`。多資產時最佳部位 `f* = Σ⁻¹μ`。

實務問題：直接套 `Σ⁻¹μ` 在 N>20 時 leverage 爆炸（因為 μ 估計噪聲被反矩陣放大）。

論文貢獻：
1. 提出 **regularized Kelly**：`f*(λ) = (Σ + λI)⁻¹μ` 並推導最佳 λ 的解析式
2. **發現 sigmoidal scaling**：當 N 增加時，最佳全部位 `Σ|f_i*|` 不是線性增長，而是 **sigmoid 飽和**
3. 在 100 檔股票測試：N=10→30 時 Kelly 部位線性增加，但 N=30→100 時飽和（log saturation）

**實務含義**：選股池超過 ~30 檔後，加更多股票邊際 Kelly 部位收益遞減 → 回應「過度分散」批判。

# 2. 為什麼對 GiS 有意義

我們現在的 scanner 一次給 50–100 檔買進名單，但**沒有 sizing 規則**。
若 sigmoidal 假說在台股成立，可訂出原則：

> 「Kelly 推荐的最佳活躍部位數約 25–40 檔，超過此數應降低個股權重 floor」

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🟢 極高 | 任何 N=10–200 的子集 |
| 數學複雜度 | 🟢 低 | 純 numpy linalg |
| 工期 | 1–2 天 | |
| 與既有流程契合 | 🟢 高 | 直接做為 portfolio sizing 模組 |

# 4. 實作步驟

```python
# experiments/exp04_kelly_sigmoidal.py
# 1. 取流動性 top 200 個股 2020-2024 日報酬
# 2. 對 N = 5, 10, 20, 30, 50, 75, 100, 150, 200 各跑：
#    隨機抽 N 檔 × 30 次，計算 regularized Kelly 部位
#    記錄 sum(|f*|), max(|f*|), N_active
# 3. 繪製 N vs sum(|f*|) — 驗證是否呈 sigmoid
# 4. fitting：Levenberg–Marquardt 擬合 logistic
```

# 5. 與原論文對比設計

| 項目 | 原論文（美股 100 檔）| 我方 POC（台股 200 檔） |
|------|----------------------|------------------------|
| sigmoidal 飽和點 | N≈30 | 預期 N≈25–35 |
| 最大 Kelly leverage | 4-5x | 預期 3-5x |
| 最佳 λ | log(N)/N | 同公式 |

**對比邏輯**：sigmoidal 是普世現象（來自 Σ 特徵值衰減模式），台股應同樣成立。若飽和點顯著不同 (例如 N=10)，則表示台股相關性結構不同 → 對組合決策有實務啟示。
