---
paper_id: 05
title: Machine Spirits：LLM agents 的投機與適應
title_en: Machine Spirits — Speculation and Adaptation of LLM Agents in Asset Markets
arxiv: 2604.18602
date: 2026-04-22
category: q-fin.TR
feasibility: LOW
action: 觀察
priority: N/A
eta_days: N/A
---

# 05 · Machine Spirits：LLM agents 投機行為（觀察類）

## 論文要旨

在模擬市場中投放 LLM agents 進行交易，觀察其是否會自發產生**投機性、羊群、泡沫、踩踏**等行為。結論：**會的**，且 LLM agent 有顯著的「敘事跟隨」傾向，與人類非理性行為類似。

## 為何僅觀察不實作

- **方法論驗證屬學術性**：需要市場模擬器（如 ABIDES），我方無此基礎建設
- **不直接產生交易訊號**：結論是「警示」性質，告訴你 LLM agent 不能無監督地交易
- **重複實驗成本高**：每次模擬 1000+ agents × 數萬步，GPU/API 成本不符 ROI

## 對我方的啟示（這才是重點）

1. **LLM agent 不能沒有 risk overlay**
   - 任何 LLM-driven 交易訊號（如 #4 多代理推薦）必須有規則層覆蓋
   - 規則層：position limit、daily loss limit、單一 sector concentration limit

2. **避免「敘事過熱」訊號**
   - 監控 LLM agent 在新聞密集期（如政策面、地緣事件）的推薦變動率
   - 若推薦變動率 > 歷史 95 分位 → 標記為「敘事驅動期」，降權重

3. **A/B 測試必要性**
   - LLM 訊號上線前必須與 baseline（純量價）做平行測試
   - 至少 6 個月 paper trading

## 監測指標（加入既有 dashboard）

| 指標 | 計算 | 警戒值 |
|------|------|--------|
| LLM 推薦變動率 | 過去 5 日推薦改變比例 | >40% → 黃燈 |
| 多 agent 一致性 | std of conviction | <0.1 過於一致也警戒（羊群） |
| 推薦與隔天大盤相關性 | corr(rec, market_ret) | >0.5 → 純跟漲跌 |

## 後續觀察計畫

- 持續追蹤 q-fin.TR 類別關於 LLM agent stability 的研究
- 若 6 個月內出現 3+ 篇相關實證 → 考慮重啟此題

## 參考

- Paper: https://arxiv.org/abs/2604.18602
- 相關：Lussange et al. (2021) "Modelling Stock Markets by Multi-agent RL" Computational Economics
