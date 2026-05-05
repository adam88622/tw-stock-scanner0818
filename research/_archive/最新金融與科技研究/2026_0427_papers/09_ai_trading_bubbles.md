---
paper_id: 09
title: 解構 AI 交易：行為金融與市場泡沫
title_en: Dissecting AI Trading - Behavioral Finance and Market Bubbles
arxiv: 2604.18373
date: 2026-04-21
category: q-fin.GN
feasibility: LOW
action: 觀察
priority: N/A
eta_days: N/A
---

# 09 · 解構 AI 交易：行為金融與市場泡沫（觀察類）

## 論文要旨

理論性研究，分析演算法交易（特別是 ML/AI 驅動）如何**放大市場行為偏誤**並催生泡沫。論文用模擬與實證結合，指出當演算法交易市占率 > 60% 時，動量類策略會自我強化，形成「演算法泡沫」（algo-driven bubble）。

## 為何僅觀察

- **無直接交易訊號**：屬於市場結構觀察，不產出 alpha
- **政策意涵 > 交易應用**：對監管機構、市場結構研究員意義較大
- **實證範圍偏美股**：台股演算法交易占比結構不同（外資 + 機構 主導）

## 對我方的啟示

1. **動量策略警戒**
   - 當市場演算法成交占比快速攀升時，動量策略下檔風險放大
   - 加入「演算法成交占比」作為動量策略的權重調節因子

2. **泡沫指標檢測**
   - 台股的 ETF（特別是 0050、00878 等大型成分股 ETF）持股集中化效應類似
   - 監控大型 ETF 對前 10 大成份股的占比

3. **與 #7 動態網路風險呼應**
   - 演算法泡沫期 = 高同步化 = spectral_radius 飆升
   - 兩者可互相驗證

## 監測指標（加入既有 dashboard）

| 指標 | 計算方式 | 警戒值 |
|------|---------|--------|
| ETF 集中持股度 | 0050 + 00878 持股佔台股市值比 | > 35% 警戒 |
| 動量集中度 | 過去一個月漲幅前 20 檔的成交量占比 | > 30% 警戒 |
| 量價背離 | 大盤指數新高但廣度（上漲家數）下降 | 連續 5 日 → 黃燈 |

## 後續觀察計畫

- 持續追蹤台股演算法交易占比公開數據（證交所季報）
- 若出現「演算法成交占比 > 50% 持續 6 個月」+「市場上漲 > 30%」 → 進入泡沫警戒模式

## 參考

- Paper: https://arxiv.org/abs/2604.18373
- 相關：Brunnermeier & Nagel (2004) "Hedge Funds and the Technology Bubble" Journal of Finance
- 相關：Khandani & Lo (2007) "What happened to the quants in August 2007?" Journal of Investment Management
