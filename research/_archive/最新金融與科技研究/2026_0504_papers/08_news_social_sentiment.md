---
title: Do News and Social Media Tell the Same Story? Constructing and Comparing Sentiment Spillover Networks
authors: Fan Wu, Anqi Liu, Maggie Chen, Yuhua Li
venue: arXiv q-fin.ST
arxiv_id: 2604.26811
date: 2026-04-30
url: https://arxiv.org/abs/2604.26811
category: sentiment / NLP
feasibility: 🔴 低（缺中文新聞/社群資料源）
priority: 觀察 / W4 評估
---

# 1. 論文摘要

論文比較 Bloomberg 新聞 vs Twitter 對 S&P500 個股的**情緒網路**：

1. 兩個情緒源建構的網路 **結構不同**（Twitter centrality 偏向消費類股，Bloomberg 偏向金融）
2. **早盤 9:30-10:30**：Twitter 領先新聞 ~30 分鐘
3. **盤後 16:00-22:00**：新聞領先 Twitter ~2 小時
4. 兩者結合（weighted average）的情緒因子 OOS Sharpe **比單一源高 20%**

# 2. 為什麼對 GiS 暫時不能做

我方目前**沒有中文新聞與社群資料**：
- 鉅亨網/cnYES/工商時報 RSS 未爬
- PTT Stock 板未爬
- Threads / Twitter 中文金融意見領袖未追蹤
- 中文 FinBERT 未驗證（hfl/chinese-roberta-wwm-ext-large 候選）

# 3. 可行性評估

| 維度 | 評分 | 說明 |
|------|------|------|
| 資料齊備度 | 🔴 低 | 需新建文本爬蟲 |
| 中文 NLP | 🟡 中 | 需驗證 / 微調 BERT |
| 工期 | ≥ 14 天 | 含資料管線建立 |

# 4. 建議行動

**不在本週實作**。改為：

1. **建立資料管線**（4 月底已列入待辦）：
   - 鉅亨網重大訊息 RSS 爬蟲
   - PTT Stock 板每日抓取
   - 公開資訊觀測站重大訊息 PDF 解析

2. **資料齊備後**（預計 W6+）再回來實作此論文

3. **方法論先記下**：早盤 vs 盤後的兩源領先關係，未來建構時要包含時間維度

# 5. 與原論文對比（待資料就緒後）

| 項目 | 原論文 | 我方未來規劃 |
|------|--------|-------------|
| 文本源 | Bloomberg + Twitter | 鉅亨 + PTT |
| 標的 | S&P500 | 0050 成分 50 檔 |
| 兩源結合 Sharpe 增益 | +20% | 待測 |

**結論**：本週僅作為**設計參考**，等中文資料管線建好再驗證。
