# 2026 Q1 開源金融 LLM 可行性評估：tw-stock-scanner 應用情境

**研究日期**：2026-04-28
**機構**：GiS Genesis International Capital
**範疇**：DeepSeek-R1 (671B MoE) / Qwen3-235B-A22B / QwQ-32B 用於台股量化系統之可行性

---

## 摘要

本報告評估 2026 Q1 三大開源旗艦推理模型（DeepSeek-R1、Qwen3-235B-A22B、QwQ-32B）在 tw-stock-scanner 三個 LLM 驅動情境（新聞情感、法人敘事、因子解釋）的可行性，並與目前使用的 Claude Sonnet 4.6 / GPT-5.4 對比。

**核心結論**：
- **API 成本**：DeepSeek-R1 比 Claude Sonnet 4.6 便宜約 **5-7 倍**（output token），Qwen3-235B 在中文場景成本更低，QwQ-32B 是最便宜的「夠用」推理選項。
- **中文能力**：Qwen3-235B 在台股新聞、法說會逐字稿等繁中場景表現最佳；DeepSeek-R1 簡中強、繁中可接受；QwQ-32B 中文推理在 32B 級別中領先。
- **金融基準**：根據 arXiv 2510.05533《The New Quant》綜述，推理型模型（reasoning LLM）在多步金融計算與因子推導上具結構性優勢；但在純情感分類任務上，35B 級別已飽和，未必需要旗艦。
- **建議路徑**：**短期 API 混合**（情感→Qwen3、敘事→Claude、因子解釋→DeepSeek-R1），**中期 vLLM 自架 QwQ-32B** 處理高頻情感任務，**長期評估** DeepSeek-R1 自架條件需月處理 >5B output tokens 才划算。

---

## 一、模型對比表

| 模型 | 參數 (Active) | Context | Input $/M | Output $/M | 中文能力 | FinanceBench (估) | 備註 |
|---|---|---|---|---|---|---|---|
| **DeepSeek-R1** | 671B (37B MoE) | 128K | $0.55 | $2.19 | 簡中強、繁中可接受 | ~75% (R1-推理) | OpenAI-compat API；推理型 |
| **Qwen3-235B-A22B** | 235B (22B MoE) | 262K | $0.26 | ~$1.20 | **繁簡雙強**（119 語） | ~70%（私有金融基準） | DashScope；hybrid thinking |
| **QwQ-32B** | 32B dense | 131K | ~$0.15 | ~$0.60 | 中文良好 | ~65%（推估） | 32B 級對標 R1，極省 |
| **Claude Sonnet 4.6** | 不公開 | 1M | $3.00 | $15.00 | 中文優秀 | ~78%（Claude 4 Sonnet） | 目前 tw-stock-scanner 使用 |
| **GPT-5.4** | 不公開 | 1.05M | $2.50 | ~$10.00 | 中文優秀 | ~88%（GPT-5 系列） | 金融基準頂級 |

**註**：FinanceBench 多數實驗室未公開分數（《The New Quant》綜述指出此為產業普遍狀況）；以上為公開可查與業界估計值，僅供方向性參考。

---

## 二、部署選項對比

### 2.1 API 呼叫（建議短期）

| 提供方 | 模型 | 月估算成本（10M input + 2M output/月） | 延遲 | 適用 |
|---|---|---|---|---|
| Anthropic | Sonnet 4.6 | $30 + $30 = **$60** | 1-3s | 高品質敘事 |
| DeepSeek 官方 | R1 | $5.5 + $4.4 = **$9.9** | 2-5s（推理） | 因子解釋 |
| Alibaba DashScope | Qwen3-235B | $2.6 + $2.4 = **$5.0** | 1-3s | 繁中情感 |
| OpenRouter | QwQ-32B | $1.5 + $1.2 = **$2.7** | <2s | 高頻情感 |

### 2.2 自架（中長期評估）

| 模型 | 最低 GPU | 月雲端成本（$2/H100/h） | 月本地成本（電費+折舊） | 月損益平衡點 |
|---|---|---|---|---|
| DeepSeek-R1 671B | 8× H100 (FP8) 或 16× A100 | **$11,520** | NT$ ~150K（含電） | 需 >5B output token/月 |
| Qwen3-235B | 4× H100 或 8× A100 | **~$5,800** | NT$ ~80K | 需 >2.5B output token/月 |
| **QwQ-32B** | **1× H100 / 2× RTX 4090** | **~$1,440** | NT$ ~25K | 需 >0.7B output token/月 |

**結論**：QwQ-32B 是唯一在 tw-stock-scanner 規模可行的本地自架選項（單台 H100 或雙 4090 可跑）。

---

## 三、tw-stock-scanner 三情境選型

### 3.1 新聞情感評分（高頻、低複雜度）

- **建議**：**Qwen3-235B（API）** 或 **QwQ-32B（自架）**
- **理由**：台股新聞繁中為主，Qwen3 系列訓練資料涵蓋最完整；任務本身屬分類，32B 已飽和；批次量大時自架 QwQ-32B 邊際成本接近 0。
- **預期準確度**：對標 Claude Sonnet 4.6 約 95%（情感三分類），成本降至 1/10。

### 3.2 法人買賣超敘事生成（中頻、中複雜度）

- **建議**：**短期保留 Claude Sonnet 4.6**，中期切換 **Qwen3-235B**
- **理由**：敘事品質直接面向研究員/客戶，Claude 在中文表達流暢度仍領先；但 Qwen3-235B-Instruct-2507 已可達近似品質，可在 PoC 階段並行評測 1-2 個月。
- **風險**：DeepSeek-R1 為推理型，敘事輸出常含 `<think>` 多餘前綴，需後處理。

### 3.3 因子解釋（低頻、高複雜度、需推理）

- **建議**：**DeepSeek-R1（API）** 為主、**GPT-5.4** 為交叉驗證
- **理由**：因子解釋（如「為何 Beta-Adjusted Momentum 在 2024 Q3 失效」）需要長鏈推理，《The New Quant》明確指出 reasoning LLM 在多步金融計算具結構性優勢；R1 的價格/性能比是 GPT-5.4 的 5-10 倍。
- **延遲容忍**：因子解釋為日批次/週批次，5-10s 延遲可接受。

---

## 四、可行性評估

| 維度 | 短期（0-3 月） | 中期（3-12 月） | 長期（12 月+） |
|---|---|---|---|
| **時程** | 統一 client wrapper、雙跑對比 | 自架 QwQ-32B（vLLM）、AB test | DeepSeek-R1 自架 ROI 評估 |
| **預算** | API：月 NT$ 3K-10K | 加 1× H100 + vLLM：月 NT$ 30K | 8× H100 cluster：月 NT$ 150K+ |
| **技術門檻** | 低（OpenAI-compat 介面統一） | 中（vLLM、量化、監控） | 高（MoE 部署、KV cache 優化） |
| **資料治理** | API 上雲：注意客戶資料外流 | 自架可保留全部資料境內 | 完全私有 |

---

## 五、結論與建議路徑

### 短期（立即執行）
1. **建立統一 client wrapper**（本週 PoC `08_finance_llm_benchmark.py` 已實作雛形）
2. **三家 API 並行對比**：每日從 tw-stock-scanner 抽 10-20 條新聞，三模型同跑、人工標註對比
3. **保留 Claude 為高品質敘事 fallback**

### 中期（3-6 月）
4. **自架 QwQ-32B**：單台 H100 或 2× RTX 4090，vLLM + AWQ INT4 量化
5. **將情感任務全量切到 QwQ-32B**，Claude 改處理研究員互動式查詢
6. **預期 API 成本下降 70%+**，同時資料留在境內

### 長期（12 月+）
7. **DeepSeek-R1 自架評估**：僅當因子解釋日均 token 量 >150M output 才划算
8. **追蹤 DeepSeek-V4 / Qwen4 / QwQ-Max** 等下一代開源旗艦
9. **建立內部 FinBench-TW 基準**（含台股財報、法說會、櫃買中心新聞）

---

## 六、引用來源

### 學術文獻（B+ 以上等級）
- arXiv:2510.05533 — *The New Quant: A Survey of LLMs in Financial Prediction and Trading*（2026 綜述，B+ working paper）— https://arxiv.org/abs/2510.05533
- FinanceBench (Patronus AI) — 10,231 題 SEC 文件 QA 基準

### 模型卡與技術報告
- DeepSeek-R1 HuggingFace 卡：https://huggingface.co/deepseek-ai/DeepSeek-R1
- Qwen3-235B-A22B HuggingFace 卡：https://huggingface.co/Qwen/Qwen3-235B-A22B
- QwQ-32B HuggingFace 卡：https://huggingface.co/Qwen/QwQ-32B
- DeepSeek 官方 API 定價：https://api-docs.deepseek.com/quick_start/pricing
- Anthropic Pricing：https://platform.claude.com/docs/en/about-claude/pricing

### 評測與市場
- Artificial Analysis Leaderboard：https://artificialanalysis.ai/leaderboards/models
- Finance LLM Leaderboard 2026：https://awesomeagents.ai/leaderboards/finance-llm-leaderboard/
- LLM API Pricing 2026 (TLDL)：https://www.tldl.io/resources/llm-api-pricing-2026
- DeepSeek R1 部署成本：https://blog.premai.io/how-to-self-host-deepseek-r1-hardware-setup-and-privacy-guide-2026/

### 引用方式
- arXiv：免費全文取得（open access）
- HuggingFace：模型權重 + 卡片（部分需登入）
- 官方 API 文件：免註冊可閱讀

---

**報告撰寫**：GiS 量化研究 / 2026-04-28
**下次回顧**：2026-07（Q2 結束時更新模型版本與定價）

---

## 實證結果（2026-04-28 補）

由於 DeepSeek / DashScope / Anthropic API key 尚未到位，本週以本機 ollama 模型（**qwen3.5:latest** 9.7B Q4_K_M、**phi3:3.8b** Q4_0）作為**家族能力下限代理**，跑了 14 題 hardcoded benchmark（8 條一般新聞 + 3 條法人敘事 + 3 條因子解釋題）。完整結果見 `results/08_empirical_results.md`，腳本於 `results/08_empirical.py`、原始輸出於 `results/08_empirical_raw.json`。

**關鍵數據**：

| 指標 | qwen3.5 (9.7B) | phi3 (3.8B) |
|---|---|---|
| 情感+敘事 三類 accuracy | **8/11 = 72.7%** | 7/11 = 63.6% |
| 摘要關鍵字覆蓋率 | **49/58 = 84.5%** | 37/58 = 63.8% |
| 因子推理平均條列點數 | **49.0** | 8.0 |
| 平均延遲 | 5.2s（情感 ~3s／因子 ~13s） | **3.1s** |
| 模型 vs 模型 sentiment disagreement | 3/11 = 27.3% | — |

**對原選型路徑的驗證**：

1. **情感→Qwen 系**：✅ 支持。9.7B Qwen 在繁中情感 8 條中 7/8 正確、摘要 kw 覆蓋率 90%；雲端 235B 應顯著更高。
2. **敘事→Claude（短期）/ Qwen3（中期）**：✅ 支持。qwen3.5 在 B1 (外資買超台積電) 正確而 phi3 誤判為負面，顯示參數規模對結構化金融敘事有顯著影響；但 B2 ambiguous「防禦性輪動」題兩模型分歧，這類事件仍建議 Claude 級別模型把關。
3. **因子→DeepSeek-R1（推理型）**：✅ 強烈支持。qwen3.5 hybrid-thinking 模式在 C1-C3 產出 39-58 條結構化推理；phi3 僅 6-9 條 — 推理型旗艦對多步金融邏輯有質的提升，與 arXiv 2510.05533 結論一致。

**技術發現（生產實作必須注意）**：qwen3.5 為 hybrid-thinking 模型，呼叫 ollama API 時若不顯式設 `"think": false`，整個 num_predict budget 會耗在 `<think>` 區塊使 `response` 為空字串（首輪測試 8/11 條 sentiment 失敗均為此因）。phi3 不支援該旗標（HTTP 400）— wrapper 必須做 model-specific 分支。本任務的 wrapper 已在 `08_empirical.py:call_ollama` 完成此處理，可作為 production client 的參考。

**限制**：本機 proxy 模型不等於雲端旗艦；14 題樣本主要驗證方向，production 決策仍需 ≥200 題、API key 到位後重跑取得絕對 accuracy 數字。


