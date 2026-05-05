# claude-mem 可行性評估報告

**評估對象**：thedotmack/claude-mem (GitHub, +12,472 stars / 週)
**評估日期**：2026-04-28
**評估單位**：GiS Genesis International Capital — 量化交易與因子選股
**現有系統**：`C:\Users\User\.claude\projects\d--claude\memory\`（純 Markdown auto-memory，MEMORY.md 索引 + per-topic .md，無向量檢索）

---

## ⚠️ 實證警告（v1.1 補註，2026-04-28）

> **本報告 v1 推薦的「方案 B 用向量檢索」已被實證部分打臉**。真實 GiS 9 個 memory + 8 個 query 跑出：BM25 **Recall@1 0.75 vs Vector 0.625**，BM25 延遲 **0.12 ms vs Vector 12.55 ms（100× 差距）**。**修正：BM25 為主路徑，Vector 為 fallback**（當 BM25 top-1 score &lt; 2.0 時觸發）。Vector 唯一勝場為純語意 query（如「SK COM callback 失敗」）。詳見 [results/05_empirical_results.md](results/05_empirical_results.md)。

---

## 摘要

claude-mem 是一個針對 Claude Code 的 TypeScript 記憶外掛，透過 Chroma 向量庫 + SQLite/FTS5 + MCP 工具（`search` / `timeline` / `get_observations`）實作會話跨次保留與相關脈絡自動注入，宣稱以「漸進披露（progressive disclosure）」達 ~10x token 節省。對 GiS 而言，**不建議全套採用**，主因是 Node/Bun/uv 多重執行期依賴與 Chroma 落地會新增 ~300 MB 與額外維運面，且現有 Markdown 系統在 ~10 個檔案規模下尚無檢索瓶頸。**推薦方案 B**：在現有 Markdown 系統上加裝一支 ~200 行的 Python 向量檢索器（sentence-transformers + numpy），以 ~100 MB 增量取得語意檢索能力，零外部服務、零 API 成本、可離線、相容現有工作流。

---

## 技術架構

### 1. Embedding 模型
README 未明示具體模型；Chroma 預設使用 `all-MiniLM-L6-v2`（sentence-transformers，384 維，22 MB），可推測 claude-mem 沿用該預設。**注意：MiniLM-L6-v2 為英文模型**，對中英混合的 GiS 記憶內容（如「凱基證券API」「量化研究嚴謹度」）召回率受限。

### 2. 向量庫
- **Chroma**：嵌入式向量庫，HNSW 索引，混合（semantic + keyword）檢索
- **SQLite + FTS5**：主存儲，session/observation/summary 三類記錄
- 雙索引設計：向量負責語意，FTS5 負責關鍵字，由 MCP `search` 工具融合排序

### 3. 注入流程（3 層漸進披露）
1. **SessionStart hook**：MCP `search(query)` 回傳「壓縮索引」（IDs + 短摘要，~50-100 tokens）
2. **timeline(id)**：取上下文時序（中等成本）
3. **get_observations(ids)**：僅對篩選後的 ID 拉完整內容（高成本但低呼叫次數）

### 4. 會話擷取
透過 6 個 hook 腳本捕獲：`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`, 以及一個 pre-hook 依賴檢查。每次工具使用會生成 observation，會話結束時由 LLM 生成 summary 寫入 SQLite + Chroma。

### 5. 安裝與相依
- Node.js ≥ 18、Bun（自動安裝）、uv（自動安裝）、SQLite3（bundled）
- 配置：`~/.claude-mem/settings.json`
- 一鍵安裝：`npx claude-mem install`

---

## 與 GiS 現有 Markdown auto-memory 對比

| 面向 | 現有 MD 系統 | claude-mem |
|---|---|---|
| 儲存形式 | 9 個 .md + 1 個 MEMORY.md 索引 | SQLite (FTS5) + Chroma (HNSW) |
| 檢索 | 字串/關鍵字（人工讀索引） | 向量 + FTS5 混合 |
| 中文支援 | 原生（純文字） | 受 embedding 模型影響（預設 MiniLM 英文偏向） |
| Token 成本 | 整份 MEMORY.md 注入（~1.4 KB） | 漸進披露 ~10x 節省 |
| 維運成本 | 零（純檔案） | Node + Bun + uv + Chroma + SQLite |
| 透明度／可審計 | 高（git diff 即可） | 低（向量盲區、SQLite blob） |
| 跨次會話自動寫入 | 手動（透過手動更新） | 全自動（hook） |
| 隱私 | 完全本機，明文 | 本機，但 summary 由 LLM 生成 |
| 安裝/維護工 | 0 | 中（多 runtime、外掛升級） |
| 規模上限 | ~50 檔案後檢索變慢 | 萬級 observation 仍順暢 |

**關鍵觀察**：GiS 目前記憶條目數 ~10 筆，遠未到向量檢索的甜蜜點；但隨著 trading_terminal、kgi_api、ezwin、institutional_backfill 等專案推進，預期 6 個月內成長至 50-100 筆，屆時關鍵字檢索會吃力。

---

## 整合方案 A：完整採用 claude-mem

**步驟**：
1. `npm i -g claude-mem`，執行 `npx claude-mem install`
2. 將 `C:\Users\User\.claude\projects\d--claude\memory\` 的 9 個 .md 透過 importer 批量寫入 SQLite + Chroma
3. 啟用 SessionStart hook，停用現行 `MEMORY.md` 自動讀取邏輯
4. 監控 ~/.claude-mem/ 容量與 hook 執行延遲

**風險**：
- 多 runtime（Node/Bun/uv）增加 Windows 環境失敗面
- MiniLM 預設模型對中文召回弱，需替換為 `paraphrase-multilingual-MiniLM-L12-v2` 或 `BAAI/bge-m3`，但 claude-mem 是否支援自訂 embedding 未明
- 既有 Markdown 流程被 SQLite 取代後失去 git 可審計性
- 受外掛升級節奏綁定（單一作者專案）

---

## 整合方案 B：自建輕量向量索引（推薦）

**設計**：
- 保留現有 Markdown 系統不動（MEMORY.md + per-topic .md）
- 加一支 Python 模組 `memory_vector_search.py`：
  - `index_memory_dir()`：用 `paraphrase-multilingual-MiniLM-L12-v2`（118 MB，50+ 語言含繁中）為每個 .md 產生 384 維向量，存成 `memory_index.npz`
  - `search(query, top_k=5)`：cosine similarity，回傳 (檔名, 分數, 摘要)
  - Fallback：若 sentence-transformers 不可用，退回 `rank_bm25` BM25Okapi
- 透過 SessionStart hook 呼叫 `search(user_first_prompt)` 並把 top-3 .md 路徑塞入系統提示

**優勢**：
- 零外部服務、零 API 成本
- 完全相容現有 Markdown 流程，git 可審計
- 中英雙語支援優於 claude-mem 預設
- ~200 行 Python，可由內部完全掌握、可審計、可改寫
- 索引重建成本低（10 檔案 < 5 秒）

**劣勢**：
- 需自寫 hook 整合（一次性 ~50 行）
- 沒有 claude-mem 的「自動會話摘要寫回」（但 GiS 既有人工策劃的 .md 反而品質更高）

---

## 可行性評估

| 維度 | 方案 A | 方案 B |
|---|---|---|
| 一次性建置 | 1-2 天（含偵錯多 runtime） | 0.5 天 |
| 月維運 | ~2 小時（升級、hook debug） | ~0 |
| 磁碟 | +300 MB (Bun + uv + Chroma) | +120 MB (model + numpy) |
| RAM | 低（懶載入） | +200 MB（首次載模型） |
| API 成本 | 0（本地） | 0（本地） |
| 隱私 | 高（全本機） | 高（全本機） |
| 中文召回 | 中（看模型替換成功與否） | 高（multilingual 模型） |
| 量化交易合規 | 需審查外掛 supply chain | 自建可審計 |

**對量化交易團隊的特別考量**：
- 記憶內容含「策略偏好、API 金鑰申請進度、券商整合狀態」，敏感度高，本地化必要 → 兩方案皆滿足
- 任何注入到 LLM 的脈絡都可能影響策略生成的可重現性，**漸進披露機制需嚴格紀錄**注入了什麼 → 方案 B 自建可記 log，方案 A 黑盒風險高

---

## 結論

**推薦方案 B**。

claude-mem 在概念上正確（漸進披露、語意+關鍵字混合），但對 GiS 的規模（10 筆記憶）、語言（中英混合）、合規（量化策略可重現性需可審計）來說，導入成本不對等。建議：

1. **本週**：部署 PoC `05_memory_vector_search.py`，索引現有 9 個 .md
2. **下週**：寫 SessionStart hook，將 top-3 結果注入系統提示
3. **3 個月後重評**：若記憶條目超過 50 筆且 BM25 與向量檢索 NDCG@5 < 0.7，再評估方案 A 或升級 BAAI/bge-m3

長期觀察 claude-mem 是否：(a) 開放自訂 embedding、(b) 支援匯出 Markdown、(c) 走多作者治理。任一條件達成可重評。

---

## 引用來源

1. thedotmack/claude-mem README（GitHub, retrieved 2026-04-28），https://github.com/thedotmack/claude-mem
2. Chroma 官方文件 — Embedding Functions（預設 all-MiniLM-L6-v2），https://docs.trychroma.com/embeddings
3. sentence-transformers Model Hub — `paraphrase-multilingual-MiniLM-L12-v2`，https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
4. Anthropic Claude Code Hooks 文件（SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd），https://docs.anthropic.com/claude/docs/claude-code/hooks
5. Robertson & Zaragoza (2009), "The Probabilistic Relevance Framework: BM25 and Beyond", Foundations and Trends in IR（B+ 期刊基準，rank_bm25 實作依據）
6. Reimers & Gurevych (2019), "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks", EMNLP 2019（A 級會議，sentence-transformers 原始論文）
7. GiS 內部現有 memory 目錄結構（`C:\Users\User\.claude\projects\d--claude\memory\MEMORY.md`，索引 9 個 topic 檔案，2026-04-28 盤點）

---

## 實證結果

於 2026-04-28 在真實 GiS auto-memory 目錄（10 個 .md，~20 KB）執行 PoC 對照測試，8 個中英混合 query 結果如下（完整數據：`results/05_empirical_results.md` 與 `results/05_empirical_raw.json`，腳本：`results/05_empirical.py`）：

| 方法 | NDCG@5 | Recall@1 | Recall@3 | MRR | 平均延遲 |
|---|---|---|---|---|---|
| BM25 (rank_bm25) | 0.8289 | **0.7500** | 0.8750 | **0.8125** | **0.12 ms** |
| Vector (paraphrase-multilingual-MiniLM-L12-v2, 384d) | **0.8452** | 0.6250 | **1.0000** | 0.7917 | 12.55 ms |

**核心發現**：
1. **BM25 已經夠好**：8 個 query 中 7 個於 top-3 命中 GT，平均延遲 0.12 ms，零模型依賴；唯一失手是純語意 query「SK COM 事件處理 callback 失敗」（內文用 `inner_class` / `silently fail`，字面不重疊）。
2. **Vector 補的是語意 gap**：Recall@3 = 100%、模型 470 MB、冷啟動 26 秒、查詢 12 ms。對「使用者用同義詞或英文描述中文記憶」的 query 確實能救回。
3. **規模假設未成立**：當前 memory 僅 10 檔，距「向量檢索勝出規模（~50+ 檔且語意分散）」尚遠。
4. **NDCG@5 統計差異不顯著**：+0.016 / 8 query，樣本太小無法宣稱 Vector 顯著優於 BM25。

**修正建議（取代原結論第 1-2 步）**：

1. **本週優先**：部署純 BM25 版本（`rank_bm25`，無模型依賴）寫 SessionStart hook，注入 top-3。
2. **設語意 fallback**：當 BM25 top-1 < 2.0 時觸發 Vector 重排，避開 SK COM 那類純語意 query 的失手。
3. **3 個月後重評**（不變）：若 memory 超過 50 筆，重跑此實證；屆時若 Vector NDCG 領先 > 0.05 再切主路徑。

結論升級：**方案 B 仍然推薦，但內部以 BM25 為主路徑、Vector 為 fallback**，而非把向量檢索當成預設方案。原因是當前語料規模下，Vector 的 470 MB 與 26 秒冷啟動成本與其邊際 NDCG 收益（+0.016）不對等。
