---
title: Modeling Stock Returns and Volatility Using Bivariate Gamma Generalized Laplace Law
authors: Tomasz J. Kozubowski, Andrey Sarantsev, James A. Spiker
venue: arXiv q-fin.ST
arxiv_id: 2605.00196
date: 2026-05-04
url: https://arxiv.org/abs/2605.00196
category: statistical finance / heavy-tail
feasibility: 🟢 高（純統計擬合）
priority: W1（立刻實作）
---

# 1. 論文摘要

報酬與波動率的**聯合分配**長期被當成獨立或 normal+lognormal 處理，但實證上：
- 報酬厚尾（kurt ≫ 3）
- 波動率右偏（lognormal 部分捕捉到）
- 報酬與波動率 leverage effect（負相關）

論文提出 **Bivariate Gamma Generalized Laplace (BGGL)**：

```
(R_t, V_t) ~ BGGL(α, β, γ, ρ)
```

其中 R_t 邊際為 Generalized Laplace（厚尾、可不對稱），V_t 邊際為 Gamma（正偏右尾），相依結構由 ρ 控制。

論文宣稱：
1. 對 SPX、Bitcoin、油價的 **OOS log-likelihood 比 Normal-Lognormal baseline 改善 3-8%**
2. **VaR 95% 估計誤差 < 10%**（baseline 為 25-40%）
3. 拒絕「正態 + 獨立」 null hypothesis 在所有測試樣本

# 2. 為什麼對 GiS 有意義

我們的風控與 sizing 模組多數假設高斯，這對台股**長尾事件**（如 2020 三月、2022 烏俄戰爭、2024 八月 carry trade unwinding）嚴重低估尾部風險。

換用 BGGL 邊際擬合可立即改善：
- 個股 VaR / CVaR 的精度
- Stop-loss 的設置（避免在 normal 假設下被假崩盤觸發）
- 槓桿產品的 expected drag 估計（與 #2 互補）

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🟢 極高 | 任何個股 5+ 年日線 |
| 數學複雜度 | 🟡 中 | 需自寫 BGGL log-likelihood + 數值優化 |
| 工期 | 2–3 天 | 主要時間花在數值穩定性 |
| 與既有流程契合 | 🟢 高 | 替換現有 VaR / volatility 模組 |

# 4. 實作步驟

```python
# experiments/exp05_gamma_laplace.py
# 1. 取 0050 / 2330 / 大盤指數 2010-2024 日報酬與滾動 21 日 σ
# 2. 擬合三個模型：
#    (a) Normal-Lognormal 獨立 baseline
#    (b) Generalized Laplace 邊際 + Gaussian copula
#    (c) BGGL 完整聯合模型
# 3. 用 MLE（scipy.optimize）估參數
# 4. AIC / BIC 比較
# 5. OOS（2025-04 ~ 2026-04）：
#    每日預測明日 VaR 95% / CVaR 95%
#    計算經驗覆蓋率（應接近 5%）
```

# 5. 與原論文對比設計

| 項目 | 原論文 (SPX/BTC/Oil) | 我方 POC (0050/2330) |
|------|---------------------|---------------------|
| OOS LogLik 改善 | 3-8% | 預期 2-6% |
| VaR 95% 覆蓋率（理想 5%）| BGGL: 4.8-5.3% | 預期 4-6% |
| Baseline VaR 覆蓋 | Normal: 7-9%（過低估）| 預期 6-9% |

**對比邏輯**：若台股 BGGL 也能讓 VaR 覆蓋率回到 ~5%，即可上線替換現行 normal-VaR 模組。
