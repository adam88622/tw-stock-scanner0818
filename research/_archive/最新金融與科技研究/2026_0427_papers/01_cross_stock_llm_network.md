---
paper_id: 01
title: 跨股票可預測性：LLM 增強語意網路
title_en: Cross-Stock Predictability via LLM-Augmented Semantic Networks
arxiv: 2604.19476
date: 2026-04-22
category: q-fin.PM
feasibility: HIGH
action: 立刻實作
priority: 4
eta_days: 5-7
---

# 01 · 跨股票可預測性：LLM 增強語意網路

## 論文要旨

用 LLM/SBERT 將公司公告、新聞、財報轉成 embedding，建立股票語意相似網路；觀察「語意鄰居」收益對「目標股」的領先性，作為橫斷面可預測性訊號。

## 為何重要（與我方因子契合）

- 現有量價因子已飽和，**另類數據（文本）embedding 是顯著的下一個 alpha 來源**
- 不依賴傳統產業分類（GICS/TSE 行業），**動態反映實際業務關聯**（例：聯發科 ↔ 高通並非同 GICS，但語意網路可揪出）
- 可作為「同業聯動因子」加入既有 multi-factor model

## 可行性評估

| 項目 | 狀態 |
|------|------|
| 中文新聞/公告文本 | ⚠️ 缺 — 需建立爬蟲（公開資訊觀測站、鉅亨、cnYES） |
| 中文 embedding 模型 | ✅ 可用 `BAAI/bge-large-zh-v1.5` 或 `text-embedding-3-large`（中文支援佳） |
| 股票連動性歷史驗證資料 | ✅ tw-stock-scanner 日線 |
| 計算成本 | 1500 檔 × 30 篇文 × 30 天 = 135 萬 embedding，OpenAI API 約 USD 25（一次性 backfill） |

**結論：可行，瓶頸在文本爬蟲建置（約 2 天）**

## 實作步驟

### Phase A：資料層（2 天）
1. 公開資訊觀測站重大訊息抓取（`mops.twse.com.tw`）
2. 鉅亨網/cnYES 個股新聞 RSS
3. 入庫 SQLite `news_embedding.db`：`(stock_id, ts, source, title, body, embedding_vec)`

### Phase B：Embedding 與相似網路（1 天）
```python
# pseudo-code
from openai import OpenAI
client = OpenAI()

def get_embedding(text):
    resp = client.embeddings.create(
        model="text-embedding-3-large",
        input=text,
        dimensions=1024  # 降維節省儲存
    )
    return resp.data[0].embedding

# 對每檔股票：取近 30 天新聞，加權平均成 stock_vec
# stock_vec[i] = Σ w_t * embedding(news_t)，w_t = exp(-λ * Δdays)

# 建立相似度矩陣
sim = stock_vecs @ stock_vecs.T  # (N, N)
# 對每檔股票，取 top-K 相似（K=10）作為語意鄰居
```

### Phase C：可預測性檢驗（2 天）
```python
# 對每檔股票 i：
#   neighbor_ret_t = Σ_{j∈top10} w_ij * ret_j_t
#   檢驗：neighbor_ret_{t-1} → ret_i_t 的 IC
#   分組 long/short 投組：高/低 neighbor_ret 五分位
```

### Phase D：因子整合（1 天）
- 將 `lagged_neighbor_return` 加入既有 alpha pool
- Fama-MacBeth 回歸控制 size/value/momentum，看 LLM 因子的增量解釋力

## 預期產出

- `src/tw-stock-scanner/factors/llm_semantic_network.py`
- 因子 IC（5d、20d）報告
- 與量價因子相關性熱力圖
- 多空組合 backtest（2018–2026）

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| 中文新聞品質參差 | 用「重大訊息」為主、新聞為輔；新聞需經情緒過濾 |
| Embedding 飄移（OpenAI 換版本） | 鎖定 model version；定期 re-embed 校驗 |
| Look-ahead bias | 嚴格用 t-1 之前的新聞建立 embedding |

## 參考

- Paper: https://arxiv.org/abs/2604.19476
- 相關：Cong et al. (2024) "AlphaManager" Review of Financial Studies
- 相關：Ke et al. (2019) "Predicting Returns with Text Data" NBER w26186
