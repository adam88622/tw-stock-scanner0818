---
title: 2026-05-04 週報可行性評估總表
date: 2026-05-04
analyst: GiS Quant Research
scope: 9 篇本週金融研究與產業新聞
principle: 能實作的一定要做；先實作、再對比原論文
---

# 可行性評估總表（2026-05-04）

## 評估維度

| 維度 | 說明 |
|------|------|
| **資料齊備度** | 我方現有資料能否支撐（TWSE/TPEx 行情、法人、broker、tick、財報）|
| **算力需求** | CPU / GPU / LLM API call 量級 |
| **實作工期** | 從零到可上線的人天數 |
| **與量化系統契合** | 直接強化現有因子/組合/風控流程的程度 |
| **建議行動** | 立刻實作 / 排程實作 / 觀察 / 僅備檔 |

## 9 篇分級結果

| # | 標題 | 類別 | 可行性 | 工期 | 行動 |
|---|------|------|--------|------|------|
| 1 | LLM 從假設到因子 | q-fin.PM | 🟡 中 | 5–7 天 | 簡化版立刻實作（grammar-based）|
| 2 | 槓桿 ETF 異常解釋 | q-fin.PM | 🟢 極高 | 1 天 | **立刻實作** |
| 3 | 高階矩組合最佳化 | q-fin | 🟢 高 | 3–4 天 | **立刻實作** |
| 4 | 多變量 Kelly Sigmoidal | q-fin | 🟢 極高 | 1–2 天 | **立刻實作** |
| 5 | Gamma 廣義 Laplace 報酬 | q-fin.ST | 🟢 高 | 2–3 天 | **立刻實作** |
| 6 | Motif 風險溢出分解 | q-fin | 🟢 高 | 2–3 天 | **立刻實作** |
| 7 | 情境整合對抗預測 | q-fin.ST | 🟡 中 | A:1天 / B:5–7天 | 階段A立刻實作 |
| 8 | 新聞 vs 社群媒體情緒 | q-fin.ST | 🔴 低 | ≥ 14 天 | 觀察（缺中文資料）|
| 9 | B2BROKER AI 平台 | News | ⚪ — | — | 產業情報 |

**統計**：立刻實作 7 篇（含 1 簡化、1 階段A）、觀察 1 篇、產業情報 1 篇

## 資料/環境就緒度

| 資料源 | 狀態 | 期間 | 備註 |
|--------|------|------|------|
| TWSE/TPEx 日線 | ✅ | 2004-03 ~ 2026-05 | 6.16M 筆 / 2705 檔 |
| 法人買賣超 | ✅ | 2012-05 ~ 2026-04 | 3.97M 筆 / 2110 檔 |
| 主力券商分點 | ✅ | 2025-06 ~ 2026-04 | 11.2M 筆 |
| Macro indicators | ✅ | 2023-04 ~ | 殖利率曲線等 |
| Regime history | ✅ | 14 年 | 用於 motif vs regime 對比 |
| 中文新聞文本 | ❌ | — | #8 阻礙 |
| 中文社群文本 | ❌ | — | #8 阻礙 |
| Python: scipy/sklearn/networkx | ✅ | 已安裝 | 本次新增 |
| LLM API | ✅ | claude-api skill | 暫不消耗 |

## 立刻實作的 7 篇優先順序（依 ROI）

1. **#2 Levered ETF**（1 天）— 純數學 + 客戶教材價值極高
2. **#4 Multivariate Kelly**（1–2 天）— 立即解答「該選幾檔」這個問題
3. **#3 高階矩組合**（3 天）— 直接強化現有 portfolio module
4. **#6 Motif spillover**（2 天）— 補強既有 dynamic network 研究
5. **#5 Gamma Laplace**（2 天）— 替換 normal-VaR
6. **#7 Context-feature 預測（階段A）**（1 天）— 驗證 macro/法人特徵價值
7. **#1 Grammar-based factor mining**（3–5 天）— 自動探索新因子

## 風險與限制

- **leveraged ETF 缺實際資料**：scanner DB 沒有 00631L/00632R，#2 將用 0050 合成 2x/-1x 序列
- **OOS 期間短**：2025-04 ~ 2026-04 為台股多頭尾段，部分結論方向偏特殊
- **無中文文本**：#8 必須延後
- **adversarial training 算力**：#7 階段 B 需 PyTorch + GPU，本週只做階段 A

## 落地時程

```
本週（2026-05-04 ~ 05-10）— 7 篇全跑：
  Day 1: #2 Levered ETF + #4 Kelly（半天各）
  Day 2: #6 Motif + #5 Gamma-Laplace 啟動
  Day 3-4: #3 高階矩組合 + #5 完成
  Day 5: #1 Grammar factor + #7 Context

下週（2026-05-11 ~ 05-17）：
  - #7 階段 B（adversarial）若值得做
  - 上線：把 #2 / #4 / #5 結論搬到 production
  
W3-W4：
  - 中文新聞爬蟲建立（給 #8 與下個週期使用）
```

## 流程原則（沿用 04-27 週報）

> **能實作的一定要做。先 POC、再與原論文對比、再決定是否上線。**

每個 POC 產出：
1. `experiments/exp{N}_*.py` — 可重跑的程式碼
2. `experiments/exp{N}_results.md` — 結果與原論文對比表
3. `experiments/exp{N}_*.csv|json` — 中間數據

最終彙總在 `experiments/RESULTS.md` + HTML 週報。
