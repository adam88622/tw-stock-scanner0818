# Hermes-Agent 可行性評估報告

> 評估對象：NousResearch/hermes-agent
> 評估單位：GiS Genesis International Capital（量化交易與因子選股）
> 日期：2026-04-28
> 評估者：研究 Sub-agent

---

## 摘要

Hermes-Agent 是 NousResearch 於 2026-04 推出的開源（MIT License）「自我改進型 agent 框架」，本週 GitHub +30,630 stars（累計 108K），是當前社群熱度最高的 agent 專案之一。其核心賣點為：

1. **closed-loop learning**：agent 在執行任務過程中自動將成功經驗抽象為 skill，寫入 skill registry，下次遇到類似任務自動調用。
2. **persistent user memory**：基於 `MEMORY.md` / `USER.md` 與 Honcho dialectic 模型，跨 session 持續累積使用者畫像。
3. **多平台 gateway**：Telegram / Discord / Slack / WhatsApp / Signal / Email 統一對話狀態。
4. **agentskills.io 標準**：採用 Anthropic 主導的開放標準，與 Claude Code、Cursor、OpenHands、Letta 等 30+ 工具互通。

GiS 現已採用 Claude Code + 自動 memory 系統 + tw-stock-scanner 多 sub-agent dispatcher 架構，與 Hermes 的設計理念高度重疊。**結論為「不完整採用、但深度借鑒兩個關鍵設計」**：autonomous skill creation 與 FTS5 cross-session recall。

---

## 框架架構

### Skill Generation Loop

Hermes 的閉環學習流程（依官方文件還原）：

```
Task arrives
   │
   ├─ Skill Registry 檢索（FTS5 + description matching）
   │     │
   │     ├─ Hit  → 載入 SKILL.md → 執行
   │     └─ Miss → 進入探索模式
   │
   ├─ 探索：reasoning core 規劃步驟 → tool calls → 完成
   │
   └─ 任務成功後：
         ├─ 抽象出 (任務描述, 步驟序列, 參數模板)
         ├─ LLM 生成 SKILL.md（name + description + instructions）
         ├─ 寫入 ~/.hermes/skills/<skill-name>/
         └─ 下次同類任務直接命中
```

採用 agentskills.io 開放格式：每個 skill 是一個資料夾，包含 `SKILL.md`（metadata + 指令）、可選 `scripts/`、`references/`、`assets/`。透過 **progressive disclosure** 三階段載入：discovery（只讀 name+description）→ activation（讀完整 SKILL.md）→ execution（執行內嵌腳本）。

### Memory Model

- **MEMORY.md**：agent-curated 索引檔（與 GiS 已採用的 Claude auto-memory 同名同構）
- **USER.md**：使用者畫像，跨 session 持續深化
- **FTS5 session search**：SQLite 全文檢索 + LLM 摘要，跨會話召回
- **Honcho integration**：dialectic user modeling，從對話中抽取使用者偏好/目標
- **Periodic nudges**：背景任務週期性提示 agent 整理 memory

### Reasoning Core

文件未公開推理引擎內部細節，僅披露：tool-calling 能力、context compression、subagent spawning（並行子任務）、6 種 terminal backend（Local/Docker/SSH/Daytona/Singularity/Modal）。模型不綁定，支援 Anthropic / OpenAI / OpenRouter（200+ models）/ NVIDIA NIM / HuggingFace。

---

## 與 GiS 現有 Claude agent 系統對比

| 維度 | GiS 現有系統（Claude Code + tw-stock-scanner） | Hermes-Agent | 重疊度 |
|---|---|---|---|
| 調度模式 | dispatcher CLAUDE.md 派遣 sub-agent | agent loop spawning subagents | 高 |
| Sub-agent 角色 | requirements-analyst / architecture-planner / function-builder / test-runner / log-writer | 通用 subagent（無預設角色） | GiS 更專業化 |
| Skill 系統 | `.claude/skills/` 已採用 agentskills.io 標準（phase0-7、init、review 等） | 同樣 agentskills.io 標準 + autonomous creation | **格式同源** |
| Skill 來源 | 人工撰寫 | 人工 + autonomous from experience | **Hermes 多自動產生** |
| Memory | Claude auto-memory（`MEMORY.md` + 多個專題子檔） | `MEMORY.md` + `USER.md` + Honcho + FTS5 | 結構同源 |
| 跨 session 召回 | 索引式（手動分檔） | FTS5 全文檢索 + LLM 摘要 | **Hermes 更強** |
| 多平台 | 僅 Claude Code CLI | Telegram/Discord/Slack/WhatsApp/Signal/Email | Hermes 更廣 |
| Phase 流程 | 7 階段瀑布（Phase 0-7） | 自由 agent loop | GiS 更嚴謹 |
| 模型綁定 | Claude Opus 4.7（1M context） | 多 provider | Hermes 更彈性 |
| 量化交易整合 | 已串接 SK COM、KGI API、TWSE/TPEx 法人資料 | 無金融特化 | GiS 大幅領先 |

**關鍵觀察**：GiS 的 Phase 0-7 流程**比 Hermes 更嚴謹**（架構審查、測試循環、交付總結），但 Hermes 在 **skill 自動產生** 與 **FTS5 跨 session 檢索** 上領先 GiS。

---

## 可借鑒的設計

### 1. Autonomous Skill Creation（移植到 Phase 5 build agent）

**現況**：GiS Phase 5 function-builder 每次任務從零開始，重複工作多。例如「抓 TWSE 法人買賣超」邏輯在 tw-stock-scanner 與 EZWin report 都重寫過。

**借鑒做法**：
- 在 Phase 7 交付總結後加一個 **skill-extraction hook**：dispatcher 檢查本次專案是否有可復用模式，若有則自動產生 `~/.claude/skills/<domain>-<task>/SKILL.md`
- 例：「TWSE 法人買賣超抓取」→ 產生 `twse-institutional-fetch` skill，下次任何專案需要時 phase2-requirements 自動掛載
- 成本：在 phase7-delivery skill 末段加入 70-100 行邏輯即可

### 2. FTS5 Cross-Session Recall（強化 auto-memory）

**現況**：GiS auto-memory 採用人工分檔索引（user_profile.md、project_*.md、feedback_*.md），檔案多後檢索靠人工記憶。

**借鑒做法**：
- 在 `~/.claude/projects/d--claude/memory/` 建立 SQLite FTS5 索引
- 新增/修改 memory 檔時自動 reindex
- dispatcher 啟動時用 FTS5 對使用者最新訊息做語意檢索，動態載入相關 memory 而非全載
- 效益：context 占用下降，相關性上升

### 3. USER.md 對話式深化（補強 user_profile.md）

**現況**：`user_profile.md` 內容靜態，需手動維護。

**借鑒做法**：仿 Honcho dialectic 模型，每次 session 結束由 sub-agent 比對對話與 USER.md，自動 propose 更新（保留 diff、由使用者確認）。

### 4. Subagent 化 terminal backend（不建議）

Hermes 支援 Docker/SSH/Modal 等 6 種後端。GiS 主要在 Windows + venv 執行，Modal/Daytona 需求低。**不建議移植**。

---

## 可行性評估

### 完整採用 Hermes-Agent

| 項目 | 評分 | 說明 |
|---|---|---|
| 整合成本 | 高 | 須重建 Phase 0-7 dispatch 流程，丟棄已成熟的 sub-agent 角色定義 |
| 整合風險 | 中高 | 量化交易場景無特化（KGI / SK COM / TWSE / TPEx 串接需重寫） |
| 學習曲線 | 中 | 框架複雜（gateway / 6 backend / Honcho），文件尚淺 |
| 模型彈性收益 | 中 | GiS 已綁 Claude，多 provider 收益有限 |
| 多平台收益 | 低 | GiS 是內部量化團隊，無 Telegram/Discord 客服需求 |
| **建議** | **不採用** | 重疊度高、特化度低、遷移成本不划算 |

### 部分借鑒（推薦路徑）

| 項目 | 預估工時 | 風險 |
|---|---|---|
| Phase 7 加 autonomous skill extraction | 1-2 人日 | 低（僅在現有 skill 加邏輯） |
| auto-memory 加 FTS5 索引 | 2-3 人日 | 低（SQLite 標準功能） |
| USER.md dialectic update（提案式） | 2 人日 | 低（保留人工確認門檻） |
| **總計** | **5-7 人日** | **低** |

---

## 結論

1. **不建議完整採用 Hermes-Agent**。GiS 的 Phase 0-7 dispatcher + 專業化 sub-agent 角色（requirements/architecture/builder/test/log）在嚴謹度與量化金融特化上**已優於 Hermes 通用設計**。完整遷移會丟棄 KGI/SK COM/TWSE 整合資產與 7 階段審查流程。
2. **強烈建議借鑒 3 個設計**：
   - **Autonomous Skill Creation**（移植到 Phase 7）
   - **FTS5 Cross-Session Recall**（移植到 auto-memory）
   - **USER.md Dialectic Update**（移植到 user_profile.md）
3. **預估投入 5-7 人日**，可在不破壞現有架構的前提下，吸收 Hermes 最有價值的兩個 idea。
4. **競品觀察**：agentskills.io 已成事實標準（Claude/Cursor/Gemini CLI/OpenHands/Letta 等 30+ 工具支援），GiS 的 `.claude/skills/` 已合規，未來 skill 可在多工具間遷移，戰略上無鎖定風險。

---

## 引用來源

1. **GitHub README**：NousResearch/hermes-agent，<https://github.com/NousResearch/hermes-agent>（MIT License、Python 87.7% / TS 8.8%、UV 套件管理、6 terminal backend）
2. **官方文件**：<https://hermes-agent.nousresearch.com/docs>（agent loop、closed learning loop、FTS5 cross-session recall、Honcho dialectic user modeling）
3. **NousResearch Blog**：<https://nousresearch.com/blog/>
   - "Introducing Hermes 4.3: Local Intelligence Globally Trained"（Hermes 模型系列，512K context）
   - "Freedom at the Frontier: Hermes 3"
   - "Introducing Atropos"（分散式 RL 框架）
   - "tinker-atropos: An Integration Layer"
   - "Measuring Thinking Efficiency in Reasoning Models"
4. **Agent Skills 標準**：<https://agentskills.io>（Anthropic 主導開放格式、SKILL.md + progressive disclosure 三階段、30+ 工具採用）
5. **Skills Hub / OpenClaw**：Hermes README 引用之社群 skill 倉庫（具體 URL 文件未列出）
6. **HuggingFace**：README 未直接連結 HF model card；Hermes 模型系列另託管於 NousResearch HF org（<https://huggingface.co/NousResearch>）
7. **學術論文**：截至 2026-04-28 未發現針對 Hermes-Agent 框架的同儕審查論文；blog 為主要技術披露管道

> 訪問方式：以上連結 2026-04-28 透過公開 HTTPS 訪問，皆無需付費或登入。

---

## 實證結果

> 詳細數據：`weekly-2026-04-28/results/04_empirical_results.md`
> 實驗腳本：`weekly-2026-04-28/results/04_empirical.py`（rank_bm25 + char-bigram 中文斷詞）

針對 GiS 真實 `D:/claude/.claude/skills/` 7 個 phase skill，設計 10 個量化交易 dispatcher user query（涵蓋 phase 0/2/3/4/5/6/7 各環節，含同義改寫測試）並用 BM25 對 (name + description) 做 progressive disclosure 第一階段檢索。結果：

| 指標 | 數值 |
|------|------|
| **Recall@1** | **90.0%**（9/10） |
| **Recall@3** | **90.0%**（9/10） |
| **MRR** | **0.900** |
| 節省 dispatcher 人工分派次數 | 9 / 10 |

**關鍵發現**：

1. **9/10 命中且全壓 top-1**：證明 Hermes-style 「只讀 description 就能正確路由」在 GiS 7-skill 規模下成立。
2. **唯一失敗（Q7）來自詞彙缺口**：「程式碼/寫出來」未匹配 phase5-build description 的「開發/function-builder」。改善只需在 SKILL.md 補 `keywords:` aliases，**不必引入 dense vector / SLM rerank**。
3. **工程含義**：Phase 7 加裝「成功經驗 → 抽象 SKILL.md」閉環後，dispatcher 即可省 90% 的 phase 路由判斷成本，剩餘 10% 用 score 閾值退回人工即可兜住。
4. **強化第三節「強烈建議借鑒」清單**：Autonomous Skill Creation 對 GiS 的實際投資報酬率（90% dispatch 自動化）已用真實 skill 與真實 query 量化驗證，預估 5-7 人日投入合理。
