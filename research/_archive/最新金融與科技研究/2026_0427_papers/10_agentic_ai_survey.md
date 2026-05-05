---
paper_id: 10
title: 金融中代理式 AI 全面綜述
title_en: Agentic Artificial Intelligence in Finance - A Comprehensive Survey
arxiv: 2604.21672
date: 2026-04-24
category: q-fin.CP
feasibility: REFERENCE
action: 內部 reference
priority: N/A
---

# 10 · Agentic AI in Finance 綜述（參考文件）

## 論文要旨

完整盤點 LLM agents 在金融服務的部署現況：
- 投研分析（research analyst agents）
- 投組管理（portfolio manager agents）
- 風險管控（risk officer agents）
- 客戶服務（advisor agents）
- 合規審查（compliance agents）

提出統一架構：**Memory + Tools + Role + Coordination**

## 為何僅作 reference 不實作

- 屬綜述文章，**本身不產生新訊號或新方法**
- 但**作為部署 agentic 系統的設計指南極有價值**

## 對我方的設計指南摘要

### 1. 標準化的 Agent 模板

```python
class FinancialAgent:
    """所有金融 agent 應實作的介面"""
    
    role: str  # 明確的角色定義
    tools: List[Tool]  # 可用工具（search、calculator、code、API）
    memory: Memory  # 短期 + 長期記憶
    
    def reason(self, context) -> Action: ...
    def act(self, action) -> Observation: ...
    def reflect(self, trajectory) -> Update: ...
```

### 2. 多 Agent 協調模式

| 模式 | 適用場景 | 我方應用 |
|------|---------|---------|
| Hierarchical | 有明確主從關係 | 投組經理 → 分析師 |
| Debate | 需多視角辯論 | #4 多代理推薦 |
| Collaborative | 共享資訊池 | 風控 + 投研整合 |
| Pipeline | 線性處理 | 文本擷取 → 評分 → 入庫 |

### 3. 關鍵設計原則

1. **明確的角色邊界**：避免 agent 越界做不專業的事
2. **可審計的 reasoning trace**：每步決策必須留下可追溯日誌
3. **工具隔離**：給 agent 的工具僅限其角色所需
4. **退路機制**（fallback）：LLM 失敗時必須有 rule-based 備案
5. **人類在迴路**（HITL）：關鍵決策（>$100K 部位）需人類確認

### 4. 部署陷阱清單（避免）

- ❌ 一個 monolithic agent 做所有事
- ❌ 沒有 logging 的多 agent system
- ❌ Agent 直接執行交易而沒 risk overlay
- ❌ Memory 無上限導致 context 爆炸
- ❌ Tool error 沒有 retry / fallback

## 實作 reference 應用

下列我方項目可參考此論文設計：
- **#4 多代理 LLM 推薦** — 用論文的 Debate 模式
- **#8 LLM 財報舞弊** — 用論文的 Pipeline 模式
- **未來** investment committee agent system — 用 Hierarchical 模式

## 後續行動

- 將此論文摘要存入 internal wiki，作為**所有 LLM/agent 開發的設計依據**
- 引用此論文作為合規審查與風控檢查的學術基礎

## 參考

- Paper: https://arxiv.org/abs/2604.21672
- 相關：Wang et al. (2023) "A Survey on LLM-based Autonomous Agents" Frontiers of Computer Science
- 相關：Park et al. (2023) "Generative Agents: Interactive Simulacra of Human Behavior" UIST
