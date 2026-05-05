# 01 — DeepSeek V4 替代方案 ollama 本機實證結果

**研究單位**：GiS Genesis International Capital — 量化研究室
**執行日期**：2026-04-28
**對應評估報告**：`../01-deepseek-v4-feasibility.md`
**目的**：在 DeepSeek V4 / Claude API key 尚未取得前，先用本機 ollama 量化驗證
LLM 摘要管線可行性（schema 合規、延遲、情感一致性、成本），作為 PoC 階段資料。

---

## 1. 設定

| 項目 | 值 |
|------|------|
| 推論引擎 | ollama (HTTP API @ `localhost:11434`) |
| 模型 A | `qwen3.5:latest` — 9.7B params, Q4_K_M, 6.6 GB |
| 模型 B | `phi3:3.8b` — 3.8B params, Q4_0, 2.2 GB |
| 解碼參數 | `temperature=0.2, num_predict=400, format=json, think=false` |
| 樣本 | 8 則台股新聞（涵蓋財報／法說／M&A／產品／訴訟／政策核准）|
| 推論次數 | 8 × 2 = 16 |
| Schema 欄位 | `company / ticker / event_type / sentiment / summary_3sent` |

> 註：qwen3.5 預設啟用 thinking 模式，會把答案塞 `thinking` field 而 `response=""`。
> 必須顯式傳 `think:false`，schema 合規率才能拉到 100%。

---

## 2. 結果摘要

### 2.1 兩模型彙總

| 指標 | qwen3.5:latest (9.7B) | phi3:3.8b |
|------|----------------------:|----------:|
| JSON 解析成功率 | **100.0%** (8/8) | 87.5% (7/8) |
| Schema 合法率 | **100.0%** (8/8) | 87.5% (7/8) |
| 平均延遲 | 4,474 ms | **3,686 ms** |
| 中位數延遲 | 3,457 ms | 3,851 ms |
| 平均 input token | 326 | 516 |
| 平均 output token | 127 | 195 |

> phi3 一個失敗案例 (N8 陽明海運訴訟)：`summary_3sent` 在輸出中段「跑題」生成
> endocrine disruptors 段落，並因 `num_predict` 截斷使 JSON 未閉合。
> qwen3.5 在 8 則新聞全部產出 schema 合法的 JSON，且摘要切題、ticker/公司皆正確。

### 2.2 兩模型一致性（情感分數）

| 指標 | 值 |
|------|----:|
| 有效配對數 | 7 / 8（N8 因 phi3 失敗排除）|
| Pearson correlation | **+0.964** |
| 方向同意率（正/負/中性）| **100.0%** |
| 平均絕對差 \|sₐ − s_b\| | 0.10 |

兩模型在情感方向上完全一致；分數在量級上 phi3 偏「飽和」（多家正面新聞給 0.8，
N5 長榮甚至給 1.0），qwen3.5 則更穩定地落在 0.85 附近。

---

## 3. 逐則對照

| ID | 新聞主題 | qwen3.5 公司/代號/event/情感 | phi3 公司/代號/event/情感 | 一致性 |
|----|---------|-----------------------------|---------------------------|--------|
| N1 | 台積電法說上修財測 | 台積電 / 2330 / 財報 / **+0.85** | 台積電 / 2330 / 財測 / **+0.80** | 同向，公司/代號完全一致 |
| N2 | 鴻海印度廠擴產 | 鴻海 / 2317 / M&A / **+0.85** | 鴻海 / **630**(錯) / 新產品 / **+0.80** | 同向，但 phi3 ticker 錯誤（ground truth 2317） |
| N3 | 聯發科天璣 9500 競爭加劇 | 聯發科 / 2454 / 財報 / **−0.40** | 聯發科 / 2454 / 財報 / **−0.30** | 同向且分類完全一致 |
| N4 | 國泰金併安泰銀 | 國泰金 / 2882 / M&A / **+0.85** | 國泰金 / **2849**(錯) / M&A / **+0.60** | 同向，phi3 把被併方 2849 誤填為主併方 |
| N5 | 長榮 Q1 EPS 6.8 | 長榮海運 / 2603 / 財報 / **+0.85** | 長榮海運 / 2603 / 財報 / **+1.00** | 同向；phi3 給滿分 1.0 偏激 |
| N6 | 華碩 AI PC | 華碩 / 2357 / 新產品 / **+0.85** | 華碩 / 2357 / **財報**(錯類) / **+0.80** | 同向；phi3 event_type 分類偏弱 |
| N7 | 中華電 5G 用戶 | 中華電信 / 2412 / 財報 / **+0.85** | 中華電信 / 2412 / 財報 / **+0.80** | 完全一致 |
| N8 | 陽明遭歐盟反壟斷 | 陽明海運 / 2609 / **訴訟** / **−0.85** | （JSON 截斷失敗，跑題到 endocrine disruptors）| qwen3.5 唯一正確判定為訴訟事件 |

**重要觀察**：
- **公司辨識**：qwen3.5 8/8 全對；phi3 7/8 公司對但 ticker 錯 2 次（N2、N4）。
- **事件分類**：qwen3.5 8/8 合理；phi3 N6 把「新產品」誤判為「財報」。
- **情感校準**：兩者方向 100% 同向，但 phi3 分數有飽和現象（給 1.0 / 0.6 等極端值較多）。
- **摘要可讀性**：qwen3.5 繁中正確、語法通順；phi3 偶有語病（「搭輛」、「同歪」、「3.8% 成立為國內第三大銀行」）並會混入英文 hallucination。

---

## 4. 延遲與成本

### 4.1 延遲對比

| 模型 | 觀測中位數 | 雲端 API 預估中位數 (參考) |
|------|----------:|--------------------------:|
| 本機 qwen3.5 (9.7B) | **3,457 ms** | — |
| 本機 phi3 (3.8B) | **3,851 ms** | — |
| DeepSeek V4-Flash (cloud) | — | ~1,200–2,500 ms (官方 chat completions) |
| DeepSeek V4-Pro non-thinking | — | ~2,500–5,000 ms |
| Claude Sonnet 4.6 | — | ~1,500–3,500 ms |

> 本機 ollama 在 RTX-class GPU + Q4 量化下，500 token 級摘要落在 3-5 秒，
> 已具備 batch 場景可用性；但 cloud API 在小 prompt 上仍有 1-2× 速度優勢。

### 4.2 月成本對比 (54M input / 10M output, 來自 01-feasibility § 3.3)

| 方案 | 月成本 (USD) | 相對 Sonnet |
|------|------------:|------------:|
| 本機 ollama (qwen3.5/phi3) | **0** (僅電費 + GPU 折舊) | −100% |
| DeepSeek V4-Flash | 10.36 | −96.7% |
| DeepSeek V4-Pro | 128.76 | −58.7% |
| Claude Sonnet 4.6 | 312.00 | 基準 |
| Claude Opus 4.6 | 1,560.00 | +400% |

> ollama 本機不消耗 token 費用，但在月 30,000+ 則新聞的 throughput 場景下，
> 單機推理 (3-4 sec/req) 大約 700-1,200 req/hour，需要 ~25 hour CPU/GPU
> 才能跑完一日量；若要每分鐘級更新仍需多卡 / 升級至雲端 API。

---

## 5. 結論

### 5.1 本機 ollama 是否足夠取代 API？

**結論：可作為 PoC + 備援，但量產仍應走 cloud API**。

✅ 適用情境：
- PoC 階段、API key 尚未取得時的功能性驗證（本次即為此）
- 敏感資料（不能送境外）的離線處理場景
- 低 throughput / 非即時的研究用途

❌ 限制：
- phi3 在中文金融術語、ticker 對應、JSON 穩定度都有明顯瑕疵 (12.5% 失敗率)，
  雖然 qwen3.5 9.7B 表現接近合格，但 hardware bound（單機 3.4-6.8 sec/req），
  無法支撐分鐘級新聞摘要管線。
- 沒有 1M context window，無法吃整本研報；研報任務必須走 V4-Pro / Claude。

### 5.2 cloud DeepSeek V4 是否仍具品質優勢？

**強烈推測：是**，且優勢主要來自三方面：

1. **規模**：V4-Flash (284B MoE / 13B 活躍) ≫ qwen3.5 9.7B；V4-Pro 1.6T 更不在話下。
   本次 phi3 的 ticker 與分類錯誤、英文 hallucination 在 V4 級模型上極不可能發生。
2. **JSON mode 穩定**：DeepSeek/Claude 的 `response_format=json_object` 是經 RLHF
   優化後的嚴格輸出，phi3 在本次出現的「summary 跑題到 endocrine disruptors」
   類 hallucination 在 V4 級基本不會出現。
3. **指令理解**：本次 qwen3.5 ticker 100% 正確、phi3 兩次填錯，這直接反映 7B-class
   小模型對「multi-entity 新聞中誰是主角」的指代消解仍弱。

### 5.3 行動建議（更新 01-feasibility 報告 § 5）

1. **立即**：以本實證結論支撐「本週跑通 PoC」目標 — qwen3.5 7/8 schema 正確
   + 100% 方向一致，已可作為「無 API 環境下的 fallback worker」原型。
2. **API key 到位後**：立刻把這 8 則新聞同樣 prompt 跑 V4-Flash + Sonnet，
   產生三方對照表（ollama / V4-Flash / Sonnet）作為 §3.4 中文 benchmark
   200 則人工標註的縮小版 sanity check。
3. **雙軌設計**：production code 預留 `provider` 欄位，可以一鍵切換
   `ollama-local / deepseek / anthropic`，cost router 之上再加 fallback router
   (V4 down → ollama qwen3.5)。

---

## 6. 產出檔案

| 檔案 | 說明 |
|------|------|
| `01_empirical.py` | 主實證腳本（依賴 `requests`，直連 ollama HTTP API）|
| `01_empirical_raw.json` | 16 次推理的完整原始輸出（含 raw、parsed、latency、token）|
| `01_empirical_stats.json` | 彙總統計（schema rate / latency / pearson / cost）|
| `01_empirical_results.md` | 本文件 |

執行命令：
```bash
"C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe" 01_empirical.py
```

---

**撰寫者**：dispatcher (Claude Opus 4.7)
**版本**：v1.0
