---
paper_id: 04
title: 多代理 LLM 股票推薦：訊號還是雜訊
title_en: Signal or Noise in Multi-Agent LLM-based Stock Recommendations
arxiv: 2604.17327
date: 2026-04-21
category: q-fin.PM
feasibility: MEDIUM
action: 排程實作
priority: 7
eta_days: 7-10
---

# 04 · 多代理 LLM 股票推薦：訊號還是雜訊

## 論文要旨

讓多個 LLM agents（不同 prompt、不同 role：分析師/技術派/價值派/風險官）對同一檔股票做推薦，問：**「集合多 agents 的推薦能否比單一 LLM 強？」** 答案：**多代理一致性高的推薦才有 alpha，分歧的推薦純雜訊**。

## 為何重要

- 我們在考慮把 LLM 加入因子流程，但「LLM 分數本身有多少信號」未驗證
- 多代理 ensemble 是「LLM agent 共識」概念，**比單一 LLM 在過擬合與穩健性上有優勢**
- 適合作為「軟性訊號」加入 alpha pool，而不是 standalone 策略

## 可行性評估

| 項目 | 狀態 |
|------|------|
| LLM API（Claude/GPT） | ✅ 已有 |
| 中文財報/新聞輸入 | ⚠️ 需先做（與 #1 共用資料層） |
| 評估資料 | ✅ tw-stock-scanner |
| 計算成本 | ⚠️ 高 — 1500 檔 × 每月 4 次 × 5 agents ≈ 30000 次/月 |

**結論：中等可行，先做 50 檔 PoC（成本 USD 50/月）再決定是否擴大**

## 實作步驟

### Phase A：定義 5 個 Agent 角色（1 天）
```python
AGENTS = {
    "fundamentalist": "你是價值投資分析師。看財報三表、ROE、估值合理性...",
    "technician": "你是技術分析師。只看價量、KD、MACD、籌碼...",
    "macro_strategist": "你是總經策略師。看產業景氣、利率、匯率...",
    "risk_officer": "你是風險官。優先看下檔風險、財務體質...",
    "contrarian": "你是逆向投資者。質疑市場共識，找錯定價..."
}
```

### Phase B：Prompt 與輸出標準化（2 天）
每個 agent 輸出 JSON：
```json
{
  "stock_id": "2330",
  "agent_role": "fundamentalist",
  "recommendation": "BUY|HOLD|SELL",
  "conviction": 0.0-1.0,
  "horizon_days": 30,
  "key_reasons": ["..."],
  "risk_flags": ["..."]
}
```

### Phase C：共識度計算（1 天）
```python
def consensus_score(agent_outputs):
    """
    輸入 5 個 agent 的推薦
    輸出 (-1 ~ +1) 共識度
    全 BUY = +1, 全 SELL = -1, 分歧 = 0
    """
    rec_map = {"BUY": 1, "HOLD": 0, "SELL": -1}
    weighted = [rec_map[a['recommendation']] * a['conviction'] for a in agent_outputs]
    return np.mean(weighted)

def disagreement_score(agent_outputs):
    """高分歧 = 不可信"""
    rec_map = {"BUY": 1, "HOLD": 0, "SELL": -1}
    return np.std([rec_map[a['recommendation']] for a in agent_outputs])
```

### Phase D：訊號驗證（3 天）
- PoC 取台股 50 檔（市值最大 50）
- 每月 1 號跑一次（2025-01 ~ 2026-04）
- 驗證：
  - 「全體共識 BUY」組合 vs「全體共識 SELL」組合的下個月收益差
  - 「低分歧度 BUY」(disagreement<0.5) vs「高分歧度 BUY」(disagreement>0.5)
- 預期論文結論成立：低分歧度 BUY 才有 alpha

### Phase E：是否上線決策點（1 天）
- 通過驗證 → 擴大到 1500 檔
- 失敗 → 改僅做極端共識（全 5 agent BUY 才買）的篩選層

## 預期產出

- `src/tw-stock-scanner/llm_agents/agent_roles.py`
- `src/tw-stock-scanner/llm_agents/consensus_engine.py`
- PoC 報告：50 檔 × 16 個月驗證結果
- 上線/不上線決策報告

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| LLM 幻覺（fundamentals 數字錯） | 強制 RAG，輸入結構化財務資料 |
| 多 agent 結論其實高度相似（同一 base model） | 用 GPT + Claude + Gemini 不同 model 增加多樣性 |
| 成本失控 | 嚴格控制在 50 檔 PoC，先驗證 alpha 再擴大 |

## 參考

- Paper: https://arxiv.org/abs/2604.17327
- 相關：Hong & Ng (2024) "Can Large Language Models Trade?" arXiv:2504.10789
- 相關：TradingGPT 多 agent 架構
