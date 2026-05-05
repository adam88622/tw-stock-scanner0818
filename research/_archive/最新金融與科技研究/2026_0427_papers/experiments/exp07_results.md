---
paper_id: 07
title: 動態網路風險標記 — 台股實證對照
arxiv: 2604.21297
date_run: 2026-04-28
verdict: PARTIAL ⚠️ 方向對、命中率低、lead time 偏長
---

# #7 動態網路風險標記 — 台股實證對照

## 論文宣稱
把市場視為股票相關性網路，計算 6 個網路指標。spectral_radius 上升（同步化）顯著早於 VIX，**前置 5-10 個交易日**警示市場壓力。

## 我方實作

### 設定
- 100 檔流動性最高股票
- 60 日滑動視窗、abs 相關矩陣
- 警示規則：rolling-60d 90 分位突破 + mean_corr > 0.7 + modularity 7d 跌 30%

### 6 個網路指標
- spectral_radius（最大特徵值，同步化）
- spectral_gap（最大 - 第二大特徵值）
- mean_correlation（橫斷面平均相關）
- modularity（社群結構）
- avg_clustering
- avg_degree

## 結果

### 警示頻率（200 個交易日）

| 警示類型 | 觸發天數 | 占比 |
|---------|---------|------|
| spectral_radius 突破 q90 | 28 | 14.0% |
| mean_correlation > 0.7 | 0 | 0.0% |
| modularity 7d 跌 > 30% | 17 | 8.5% |
| **任一警示** | **39** | **19.5%** |

### Ground Truth #1：tw-stock-scanner 既有 regime_history（autoencoder reconstruction error）

| 項目 | 值 |
|------|-----|
| abnormal 區段數 | 1（2026-02-24 起始） |
| SR 警示命中 | 0% |
| 事件前 30 日 SR 變化 | **-12.8%（下降）** |

> **解讀**：tw-stock-scanner regime 用 autoencoder 偵測「個股價格行為偏離」，論文 SR 偵測「市場同步化」。兩者測量不同維度，不該對齊。換 ground truth。

### Ground Truth #2：市場大跌日（11 個事件）

| 事件日 | 中位收益 | SR 警示 | 任一警示 | Lead 日數 |
|--------|----------|---------|---------|-----------|
| 2025-08-07 | -2.40% | ❌ | ✅ (07-18) | 20 |
| 2025-08-20 | -3.26% | ❌ | ✅ (08-04) | 16 |
| 2025-09-01 | -2.36% | ❌ | ❌ | — |
| 2025-09-26 | -3.21% | ❌ | ❌ | — |
| 2025-10-13 | -2.23% | ❌ | ❌ | — |
| 2025-11-04 | -2.07% | ❌ | ❌ | — |
| 2025-12-16 | -2.42% | ❌ | ❌ | — |
| 2026-01-21 | -1.99% | ❌ | ❌ | — |
| 2026-03-03 | -2.63% | ❌ | ❌ | — |
| **2026-03-23** | **-4.19%** | ✅ (03-10) | ✅ | **13** |
| **2026-04-23** | **-3.85%** | ✅ (04-01) | ✅ | **22** |

### 命中率與 Lead Time

| 指標 | 論文宣稱 | 台股實證 |
|------|---------|---------|
| Lead time | 5-10 日 | **17 日**（範圍 13-22）|
| SR 命中率 | （高，未明示） | 18%（11 中 2）|
| 任一警示命中率 | — | 36%（11 中 4）|

## Verdict：⚠️ 部分成立

### ✅ 方向對
- 警示確實在事件**之前**出現
- 對 -3.5% 以上大跌（2026-03-23、04-23）成功預警
- spectral_radius 與市場壓力有關係

### ❌ 量級偏離
- Lead time 17 日 vs 論文 5-10 日 — **長 2-3 倍**
- 命中率 18% — 對中等跌幅（-2~3%）無效
- mean_correlation 警示完全沒觸發（0 天）

### 可能原因
1. **13 月樣本太短** — 不足以校準動態門檻
2. **台股結構特殊** — 外資主導使同步化更早起來
3. **警示門檻 q90 太寬鬆** — 應降至 q95 或 q99

## 可行動洞察

### ✅ 可立即用為「降槓桿訊號」
- 不適合做出清訊號（命中率太低）
- **適合做加碼/減碼訊號**：
  - 任一警示 → 降槓桿 20%
  - SR 警示 → 降槓桿 50%
  - 持續多日警示 → 降槓桿 80%

### 與其他訊號結合
- 單獨 SR 警示力不足
- 應與 #3 制度分類器、信用利差、市場廣度組合
- **多訊號 ensemble** 才能達到實用命中率

## 下一步

| 項目 | 工期 |
|------|------|
| 1. tw-stock-scanner 補回 2018-2024 歷史日線 | 5 天（爬取） |
| 2. 重做實驗涵蓋 COVID、烏俄等大事件 | 2 天 |
| 3. 校準警示門檻（q95/q99） | 1 天 |
| 4. 與其他 risk signal ensemble | 2 天 |
| 5. 上 dashboard | 1 天 |
| **總計** | **11 天** |

## 檔案

- 程式：[exp07_dynamic_network.py](exp07_dynamic_network.py)
- 200 日網路指標序列：[exp07_markers.csv](exp07_markers.csv)
- 含 regime + 警示：[exp07_markers_with_regime.csv](exp07_markers_with_regime.csv)
- 11 個壓力事件對照：[exp07_stress_lead_time.csv](exp07_stress_lead_time.csv)
- Summary JSON：[exp07_summary.json](exp07_summary.json)

## 重跑

```bash
python tw-stock-scanner/research/_archive/最新金融與科技研究/2026_0427_papers/experiments/exp07_dynamic_network.py
```
