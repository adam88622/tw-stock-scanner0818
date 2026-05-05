# GiS 科技研究週報 2026-04-28 — 交付物說明

**研究單位**：GiS Genesis International Capital — 量化研究室
**週次**：Week 17 · 2026 (2026-04-22 ~ 2026-04-28)
**主檔報告**：[../weekly-tech-research-20260428.html](../weekly-tech-research-20260428.html)

---

## 目錄結構

```
weekly-2026-04-28/
├── README.md (本檔)
├── 01-deepseek-v4-feasibility.md            # 主 MD：DeepSeek V4 可行性
├── 02-beijing-humanoid-robot-investment.md  # 主 MD：北京人形機器人籃
├── 03-stanford-ai-index-agent.md            # 主 MD：Stanford AI Index Agent
├── 04-hermes-agent-feasibility.md           # 主 MD：Hermes-Agent
├── 05-claude-mem-feasibility.md             # 主 MD：claude-mem
├── 06-google-turboquant.md                  # 主 MD：Google TurboQuant
├── 07-nvidia-isaac-groot-investment.md      # 主 MD：NVIDIA Isaac GR00T
├── 08-quant-finance-llm-feasibility.md      # 主 MD：量化金融 LLM
├── poc/                                      # 原始 PoC 程式（設計階段）
│   ├── 01_deepseek_v4_news_summary.py
│   ├── 02_humanoid_robot_basket.py
│   ├── 03_agent_benchmark_tracker.py
│   ├── 04_skill_generation_loop.py
│   ├── 05_memory_vector_search.py
│   ├── 06_long_context_use_case_planner.py
│   ├── 07_groot_supply_chain_basket.py
│   └── 08_finance_llm_benchmark.py
└── results/                                  # 實證結果（真實資料跑出）
    ├── 0X_empirical.py                       # 實證腳本
    ├── 0X_empirical_results.md               # 實證結果報告
    ├── 0X_empirical_*.json/csv               # 原始實證數據
    └── docs/                                 # SEC EDGAR 真實年報 HTML
```

---

## 環境需求

```bash
# Python 3.12+ (本研究使用 C:/Users/User/AppData/Local/Programs/Python/Python312/)
# 套件：以下皆為實證所需，缺一不可
pip install yfinance pandas numpy requests beautifulsoup4 \
            anthropic openai sentence-transformers rank_bm25 tiktoken
```

**外部依賴**：
- **ollama** (本機 LLM)：需先 `ollama serve` 並下載 `qwen3.5:latest` (6.6GB) + `phi3:3.8b` (2.2GB)
- **yfinance**：需網路連線抓股價
- **SEC EDGAR**：06 抓三份真實年報（已存於 `results/docs/`）

---

## 重跑實證指南

每支實證腳本可獨立重跑，輸出至 `results/` 同名 `_results.md` / `.json` / `.csv`：

| # | 主題 | 重跑指令 | 預估時間 | 依賴 |
|---|------|---------|---------|------|
| 01 | DeepSeek V4 vs ollama | `python results/01_empirical.py` | ~3 分鐘 | ollama serve |
| 02 | 人形機器人籃 yfinance | `python results/02_empirical.py` | ~30 秒 | yfinance + 網路 |
| 03 | Agent log 解析 | `python results/03_empirical.py` | ~5 秒 | tw-stock-scanner/log/ |
| 04 | Skill BM25 retrieval | `python results/04_empirical.py` | ~5 秒 | rank_bm25 |
| 05 | Memory vector vs BM25 | `python results/05_empirical.py` | ~30 秒 (首次下載模型 ~60s) | sentence-transformers |
| 06 | Token planner (年報) | `python results/06_empirical.py` | ~10 秒 | tiktoken + 已存 docs/ |
| 07 | GR00T basket yfinance | `python results/07_empirical.py` | ~30 秒 | yfinance + 網路 |
| 08 | Quant LLM benchmark | `python results/08_empirical.py` | ~5 分鐘 | ollama serve |

---

## 主檔 HTML 報告轉 PDF

```bash
"C:/Program Files/Google/Chrome/Application/chrome.exe" \
  --headless=new --disable-gpu --no-sandbox \
  --print-to-pdf="weekly-tech-research-20260428.pdf" \
  --print-to-pdf-no-header --no-pdf-header-footer \
  --virtual-time-budget=20000 \
  "file:///D:/claude/tw-stock-scanner/research/_archive/最新金融與科技研究/weekly-tech-research-20260428.html"
```

---

## 實證打臉一覽（與原 MD 假設不符）

| # | 原假設 | 實證結果 | 主 MD 已標註？ |
|---|--------|----------|--------------|
| 02 | Sharpe 0.8-1.2 | **Sharpe 0.55**，落後 TWII 50pp | ✅ 末段 |
| 05 | Vector 勝 BM25 | **BM25 反勝**，延遲 100× 快 | ✅ 末段 |
| 07 | Jaccard 0.21 差集大 | **真實 portfolio corr 0.693** | ✅ 末段 |

→ HTML 第「伍 · 實證結果驗證」章節有完整對照與修正方案

---

## 風險免責

- 所有股價數據截至 **2026-04-28**，回測不代表未來收益
- Sharpe 計算採 risk-free = 2% 年化
- 籃子實證僅 16 個月樣本，事件研究 ±10D 視窗，**不可外推為長期 Sharpe**
- LLM 實證使用本機 ollama qwen3.5/phi3，**不等於 cloud DeepSeek V4 / Qwen3-235B 真實表現**，視為 proxy
- 本報告僅供 GiS 內部研究使用，不構成任何投資建議

---

## 下次重評時點

- **2026-05-12**：DeepSeek V4 PoC 結果出爐後重評 01
- **2026-07**：Q2 結束，更新 LLM 模型版本與定價（08）
- **2026-Q3**：claude-mem 條目超過 50 筆時重評 05
- **2026-08**：NVIDIA FY26 Q2 財報後重評 07
- **持續**：每週監控 Unitree 出貨、上銀月營收、SK Hynix/Micron 跌幅
