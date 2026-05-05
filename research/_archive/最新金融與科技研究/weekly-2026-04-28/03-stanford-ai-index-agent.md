# Stanford AI Index 2026：Agent 能力臨界點與 GiS 內部對策

> 撰寫日期：2026-04-28　|　作者：GiS 量化研究室　|　研究週次：weekly-2026-04-28
> 等級：B+（Stanford HAI 官方報告 + IEEE Spectrum + arXiv + 官方 benchmark 站）

---

## 摘要

Stanford HAI 2026 AI Index Report（4 月發布，全文 423 頁）首度將「AI Agent 在真實電腦環境的任務成功率」列為主軸指標。最關鍵的觀察是：在 OSWorld 基準上，前緣模型自 2025 年的約 12% 在一年內躍升到 **66.3%**，距離人類基準（72.36%）剩不到 6 個百分點。在升級版 OSWorld-Verified 上，**GPT-5.4 Thinking 達 75.0%，首次正式超越人類 72.4% 的基線**；GPT-5.5（4/23 發布）再推升到 78.7%，被 Claude Mythos Preview 79.6% 反超。在更貼近經濟價值的 GDPval（44 種職業 × 9 大 GDP 貢獻產業）上，GPT-5.5 拿到 **84.9%**（OpenAI 官方為 ~83% 級距，AI Index 引用約為「frontier 模型已逼近專家品質」）。對 GiS 而言，這不只是 benchmark 故事——它意味著 **「以 agent 為主體的研究流水線」已從 PoC 階段進入可衡量的工程階段**，但 89% 的企業 agent 仍卡在 production gap，因此「監控、可觀測性、成本控制」是現階段比模型選型更值得投資的方向。

---

## 一、AI Index 2026 關鍵數據

### 1.1 Agent 能力躍升曲線（Technical Performance 章）

| 指標 | 2024–2025 baseline | 2026 最佳分數 | 人類 baseline | 來源 |
|---|---|---|---|---|
| OSWorld（原始版）| ~12% | **66.3%** | 72.36% | AI Index 2026 / OSWorld site |
| OSWorld-Verified | 47.3%（GPT-5.2） | **79.6%**（Claude Mythos Preview）；GPT-5.4 75.0% | 72.4% | benchlm.ai 4 月榜單 |
| Terminal-Bench | 20% | 77.3%（AI Index 統計）；GPT-5.5 在 Terminal-Bench 2.0 為 **82.7%** | — | AI Index / OpenAI |
| SWE-bench Verified | 60% | 接近 100% | — | AI Index Technical Performance |
| Cybersecurity（CTF 類）| 15%（2024）| 93% | — | AI Index |
| Humanity's Last Exam | 8.8%（2025）| >50% | — | AI Index / IEEE Spectrum |

> **Jagged Frontier 警告**：同一批奪 IMO 金牌的模型，在「讀類比指針時鐘」這種任務上仍只有 **50.1%** 正確率。這提醒我們把 agent 部署到生產時，不能假設「一個任務拿高分代表整條流程都行」。

### 1.2 GDPval（OpenAI 9/2025 提出，arXiv:2510.04374）

- 涵蓋 **44 個職業**，對應 **美國 GDP 前 9 大產業**（含金融、法律、醫療、媒體、製造等）。
- 每職業平均 30 個任務（gold subset 5 個），由平均 **14 年資歷**的專業人士命題。
- 評分採 **盲評頭對頭比較**（better / as good as / worse than human）。
- 結果：前緣模型已能 **以 ~100 倍速度與 ~100 倍成本優勢** 完成與專家品質相當的交付物；GPT-5.5 達 **84.9%**（4/23 發布）。
- AI Index 將其引為「AI 已從 chat assistant 進入 economic-value worker」的核心證據。

### 1.3 企業採納（Economy 章）

- 組織級 AI 使用率：78%（2024）→ **88%（2025）**；其中 79% 使用生成式 AI 於至少一個業務功能。
- 但 **agent 部署率在多數部門仍是「個位數百分比」**。
- **89% 的企業 agent 專案無法進入 production**（D[AI]LY BRIEF 引用 AI Index）——「示範會、做不出來」是普遍現象。
- 全球企業 AI 投資：$253B（2024）→ **$581B（2025）**，美國佔 $344B；全球 AI 算力容量自 2022 年起年增 3.3 倍，相對 2021 已成長 30 倍。

---

## 二、對 GiS 內部 agent 系統的意義

User 目前已具備兩條 agent 軸線：
1. **Claude auto-memory**（`.claude/projects/d--claude/memory/`）—— 跨 session 的記憶層。
2. **tw-stock-scanner agents**（CLAUDE.md 定義的 dispatcher / requirements-analyst / function-builder / test-runner / log-writer 七階段流水線）。

對照 AI Index 2026 的數據，可導出三個直接啟示：

### 2.1 GiS 的架構已踩在 2026 主流上，但缺「績效層」
七階段 dispatcher 模型本質就是 **multi-agent orchestration**。2026 報告強調的「agent 能用，但生產化失敗率 89%」，幾乎都是因為缺三件事：**任務級成功率追蹤、成本歸因、回歸測試**。GiS 已有 `$PRJ/log/` 但格式偏自由文字，需升級為結構化 metric。

### 2.2 OSWorld-Verified 的 75% 給了 GiS「研究自動化」的綠燈
法人籌碼回補、EZWin 報告下載、研報 HTML 解析這類 GUI/混合任務，過去因為 agent 失敗率高需要人工監督。**75% 跨人類基線**意味著：可以開始把這些任務從「半自動」推到「監督式全自動」。實務上 GiS 應將 EZWin 冷啟動 tab 切換 bug 這類問題重打包成 OSWorld 風格的 task spec，使其可被 agent 化解決。

### 2.3 Jagged Frontier 對 quant 研究是雙面刃
模型可以推 IMO 但讀錯時鐘——對應到 quant 場景，就是「能跑因子回測但會把日期 parse 錯」。GiS 的回補腳本（2012/2018 起的法人資料）必須加 **schema-level 驗證**而非僅看 agent 自報「成功」。

---

## 三、投資意涵：Agent Infrastructure 受益分層

| 層級 | 受益邏輯 | 美股代表 | 台股相關 |
|---|---|---|---|
| **算力底層** | $581B 投資 + 3.3x 年增算力 | NVDA、AMD、AVGO | TSMC（2330）、聯發科（2454）、創意（3443）、世芯-KY（3661） |
| **資料中心散熱/電源** | 全球 30 倍算力擴張、地方政府開始限制新建 | VRT、ETN、CEG | 台達電（2308）、光寶科（2301）、奇鋐（3017）、雙鴻（3324） |
| **網通/Connectivity** | agent 多步推理 = scale-out 通訊頻寬翻倍 | ANET、CSCO | 智邦（2345）、技嘉（2376）、緯穎（6669） |
| **模型/前緣 lab** | GPT-5.4 → 5.5 一個月迭代 | MSFT（OpenAI）、GOOG、META；私有 Anthropic | — |
| **垂直應用 / GDPval 受益者** | 法律、金融、醫療、客服 | INTU、CRM、NOW；金融類 PYPL、V | — |
| **Agent 工具/IDE** | 89% 生產化失敗 → 監控/工具市場剛起步 | DDOG、SNOW、MDB；私有 LangChain、Anthropic | 緯軟（4953）作為服務整合者觀察 |

**重點觀察**：2026 的 agent 故事 **不只是 NVDA 故事**。受益最直接但市場低估的是 **散熱（vapor chamber、水冷）+ 電源（HVDC、SiC）+ 觀測層（DDOG 級監控）**。台股應重點看 **奇鋐 / 雙鴻 / 台達電 / 緯穎** 的 Q2 訂單能見度。

---

## 四、可行性評估：GiS 內建立「Agent 績效監控儀表板」

### 4.1 為何要做
1. CLAUDE.md 七階段流水線已每天執行；無 metric → 無法回答「上週成功率多少？最貴的 agent 是誰？」
2. AI Index 點名「89% production gap」根因之一就是缺監控。GiS 領先一步等於把研究自動化推進工程級。
3. 凱基/EZWin/法人回補 三條 pipeline 都將仰賴 agent，沒儀表板出事只能事後 grep log。

### 4.2 指標設計（最小可行集）

| 類別 | 指標 | 計算 | 警戒線 |
|---|---|---|---|
| 任務 | task_success_rate | success / total | <80% 兩天即告警 |
| 任務 | mean_runtime_sec | 平均 wall-clock | 超過 baseline 2× 告警 |
| 成本 | cost_per_task_usd | tokens × price | 月 budget 90% 警示 |
| 可靠 | retry_rate | retried / total | >15% 表示流程脆弱 |
| 資料 | schema_violation_rate | 結構不合法的輸出比 | >1% 即停線 |
| 體感 | end_to_end_latency_p95 | 七階段總時間 P95 | 自定 |

### 4.3 落地三步驟（建議）
1. **Week 1**：用本週 PoC（`03_agent_benchmark_tracker.py`）做 JSON store，所有 agent 收尾 hook 寫入。
2. **Week 2**：接 SQLite + 簡易 Streamlit dashboard，按 agent_name 切分。
3. **Week 3+**：對接 Claude API 計費資料、加 Slack/LINE 警報、建立週報自動產出（可被 schedule skill 排程）。

**結論：強烈建議建置**，初期投入 < 3 工作天，預期可降低 30%+ 的 debug 時間。

---

## 五、結論

1. **Agent 已過實用門檻**：OSWorld 12→66%、GPT-5.4 在 OSWorld-Verified 超越人類，是 2026 最重要的單一技術事件。
2. **GiS 的 dispatcher 架構方向正確**，差距在「可觀測性」而非「能力」。
3. **投資面**散熱/電源/監控是被低估的二階受益者；台股聚焦奇鋐、雙鴻、台達、緯穎。
4. **內部行動**：本週啟動 agent 績效監控儀表板 PoC（已附）。
5. **風險**：Jagged Frontier 仍真實存在，所有 agent 輸出必須走 schema 驗證；89% production gap 是警告，不是別人故事。

---

## 實證結果（2026-04-28 補入）

把 PoC `BenchmarkTracker` 套用在 tw-stock-scanner 既有 5 個 log（共 10,939 行）上，做了一次「以 log 反推 agent 績效」的真實驗證。完整報告與程式：[`results/03_empirical_results.md`](./results/03_empirical_results.md)、[`results/03_empirical.py`](./results/03_empirical.py)。

**關鍵指標**：

| 維度 | 值 | 備註 |
|---|---|---|
| 整體 success_rate | **80%**（5 跑 4 成）| 啟動粒度 |
| 交易日級別 success_rate | **99.63%**（4,573 / 4,590）| 業務粒度 |
| backfill-runner success_rate | 66.7%（3 跑 2 成）| 含 1 次 0-byte 冷啟動失敗 |
| retry_rate | 40%（2/5）| 全部來自 backfill-runner |
| total estimated cost (Sonnet 4.5 定價) | $1.41 | 字元逼近 token 估算 |
| 最常見錯誤 | TWSE/TPEx API JSON parse 失敗 (15 次) | retry 後 100% 成功 |

**三個直接結論（呼應正文）**：

1. **Production gap 是真的**：第一個 backfill 嘗試（13:32:45 啟動）寫出 0-byte log 就靜默崩潰，3 分鐘後（13:36:01）才有第二次成功跑完。如果沒有 BenchmarkTracker 把這次失敗顯式記錄，事後只看「13:36 那次跑完了」會錯估 success_rate 從 66.7% → 100%。**這就是 AI Index 講的 89% production gap 在自家專案的縮影**。
2. **應優先優化 backfill-runner**：佔 99.9% runtime、99% cost，是唯一發生冷啟動失敗的 agent，且 WARNING/INFO 比達 11.2%（噪音偏高）。建議：(a) 加 30 秒 health check 偵測 0-byte log；(b) 把「無資料日」WARNING 降級為 DEBUG。
3. **PoC 設計可用**：`AgentRunRecord` schema 直接吃下 5 種異質 log（含 0-byte、cp950 mojibake），驗證 dataclass + JSON store 的 KISS 路線足夠在內部開跑。下一步是嵌入 dispatcher phase5/phase6 hook。

---

## 六、引用來源（B+ 等級）

1. Stanford HAI. *The 2026 AI Index Report* (full PDF, 423 pp.). 2026-04. https://hai.stanford.edu/assets/files/ai_index_report_2026.pdf
2. Stanford HAI. *Inside the AI Index: 12 Takeaways from the 2026 Report*. 2026-04. https://hai.stanford.edu/news/inside-the-ai-index-12-takeaways-from-the-2026-report
3. Stanford HAI. *Technical Performance — 2026 AI Index*. https://hai.stanford.edu/ai-index/2026-ai-index-report/technical-performance
4. Stanford HAI. *Economy — 2026 AI Index*. https://hai.stanford.edu/ai-index/2026-ai-index-report/economy
5. IEEE Spectrum. *Stanford's AI Index for 2026 Shows the State of AI*. 2026-04. https://spectrum.ieee.org/state-of-ai-index-2026
6. OSWorld project site. *OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments*. https://os-world.github.io/
7. BenchLM. *OSWorld-Verified Benchmark 2026 Leaderboard* (accessed 2026-04-24). https://benchlm.ai/benchmarks/osWorldVerified
8. OpenAI. *Measuring the performance of our models on real-world tasks (GDPval)*. 2025-09. https://openai.com/index/gdpval/
9. arXiv:2510.04374. *GDPval: Evaluating AI Model Performance on Real-World Economically Valuable Tasks*. https://arxiv.org/abs/2510.04374
10. OpenAI. *Introducing GPT-5.5*. 2026-04-23. https://openai.com/index/introducing-gpt-5-5/
11. OpenAI. *Introducing GPT-5.4*. 2026 (Q1). https://openai.com/index/introducing-gpt-5-4/
12. MarkTechPost. *OpenAI Releases GPT-5.5 — 82.7% Terminal-Bench 2.0 / 84.9% GDPval*. 2026-04-23. https://www.marktechpost.com/2026/04/23/openai-releases-gpt-5-5-...
13. THE D[AI]LY BRIEF (Beri.net). *Stanford AI Index 2026: AI Agents Hit 66% — But 89% Never Reach Production*. 2026-04.  https://www.beri.net/article/stanford-ai-index-2026-agents-66-percent-success
14. Arahi AI. *Stanford AI Index 2026: AI Agents Jump from 12% to 66.3%*. 2026-04. https://arahi.ai/ai-agent-news/stanford-ai-index-2026-ai-agents-task-success
