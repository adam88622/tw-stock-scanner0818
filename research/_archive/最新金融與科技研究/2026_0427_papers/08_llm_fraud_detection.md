---
paper_id: 08
title: LLM 在識破財務舞弊上勝過人類，且抗壓力誘導
title_en: Large Language Models Outperform Humans in Fraud Detection and Resistance to Motivated Pressure
arxiv: 2604.20652
date: 2026-04-23
category: q-fin.GN
feasibility: HIGH
action: 立刻實作
priority: 6
eta_days: 5-6
---

# 08 · LLM 財報舞弊偵測（下檔風險篩選）

## 論文要旨

LLM（GPT-4o / Claude）在識別財務舞弊文本（含蓄式語意操弄、敘事偏離數字、抗壓性低的揭露）上**顯著優於人類分析師**，且**對「老闆要求說好話」式的壓力誘導抵抗力強**。

## 為何重要

- 我方因子流程目前**僅看數字**（ROE、cash flow），會漏接「會計操作前兆」
- 加入 LLM 文本層作為**下檔風險篩選器**，剔除高風險樣本
- 台股財務舞弊歷史頻發（如：2017 樂陞、2020 康友、2024 ABT）
- 下檔風險篩選對因子組合 Sharpe 提升幅度顯著（避免炸雷）

## 可行性評估

| 項目 | 狀態 |
|------|------|
| 年報/法說稿文本 | ⚠️ 缺 — 需建立公開資訊觀測站爬蟲 |
| LLM API | ✅ Claude（user 已有） |
| 標註資料（已知舞弊案） | ✅ 證交所裁罰公告可追溯 |
| 計算成本 | 中等 — 1500 檔 × 一年 4 篇 × 50K tokens = USD 200/年 |

**結論：高可行 — 5-6 天可建立 PoC**

## 實作步驟

### Phase A：建立資料層（2 天）
```python
# src/tw-stock-scanner/text/financial_text_scraper.py

# 來源：
# 1. 公開資訊觀測站 - 年報 PDF
# 2. 法說會逐字稿（cnYES、moneyDJ）
# 3. 重大訊息

# PDF → text：用 pdfplumber 或 unstructured.io
# 入庫 SQLite financial_text.db：
#   (stock_id, year, doc_type, raw_text, fraud_score, fraud_flags, llm_audit_date)
```

### Phase B：建立 fraud detection prompt（1 天）
基於論文的 9 個檢測維度：
```python
FRAUD_DETECTION_PROMPT = """
你是嚴謹的法務會計師（forensic accountant）。請分析以下台灣上市公司的財報/法說會文本，識別以下 9 個舞弊前兆：

1. **數字一致性**：敘事與表上數字是否矛盾？
2. **語意模糊度**：是否大量使用模糊措辭逃避具體承諾？
3. **責任轉嫁**：營運不佳是否歸咎外部因素過於頻繁？
4. **時間遠離**：「今年下半年/明年」承諾出現次數
5. **複雜度噪音**：是否用過度技術術語掩蓋簡單問題？
6. **第一人稱避免**：管理層是否避免「我承擔」、「我決定」？
7. **過度樂觀詞彙**：超出歷史常態的正面詞彙密度
8. **修正性敘述**：對前期承諾的修正是否被淡化？
9. **異常的會計處理說明**：是否花過多篇幅解釋會計處理變更？

請輸出 JSON：
{
  "fraud_score": 0.0-1.0,
  "primary_concerns": ["..."],
  "specific_quotes": ["..."],  // 引用原文最可疑的 3 句
  "audit_recommendation": "low_risk|monitor|high_risk|exclude"
}

文本：
{text}
"""
```

### Phase C：歷史驗證（1 天）
- 已知舞弊案（樂陞、康友、ABT 等）回溯：模型能否在事件爆發前 6-12 個月給高分？
- 隨機對照：1500 檔 × 5 年正常公司，看 false positive rate

### Phase D：因子化（1 天）
```python
# src/tw-stock-scanner/factors/fraud_risk_factor.py

def fraud_risk_factor(date):
    """
    回傳每檔股票的舞弊風險分數（0-1，1 最高風險）
    用最近一份財報/法說的 LLM 評分
    """
    return scores  # pd.Series indexed by stock_id

# 整合到 portfolio：
# - hard exclude: fraud_score > 0.8
# - down-weight: 0.5 < score < 0.8 → 降權重 50%
```

### Phase E：監控與迭代（1 天）
- 每季新財報/法說自動觸發 LLM audit
- Email 警示：score 變動 > 0.3 或落入 high_risk

## 預期產出

- `src/tw-stock-scanner/text/financial_text_scraper.py`
- `src/tw-stock-scanner/text/fraud_detector_llm.py`
- `src/tw-stock-scanner/factors/fraud_risk_factor.py`
- 1500 檔的初次評分 baseline
- 歷史舞弊案回溯驗證報告

## 預期效果

- 每季排除 5-15 檔高風險樣本
- 既有因子組合 Sharpe 預期提升 0.05-0.15（避免黑天鵝拖累）
- Max drawdown 改善 10-20%（從個股黑天鵝免疫）

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| LLM 對中文台股財報語境陌生 | 用 few-shot prompting，提供 3 個歷史舞弊案例做範本 |
| False positive 誤殺好公司 | high_risk 需人工複核才剔除 |
| 訴訟風險（給人「指控」） | 內部使用，不對外發佈，使用模糊話術「需進一步審視」 |

## 道德與合規

- **僅供內部投資決策參考**
- **不對外發布、不向第三方提供**
- **任何 high_risk 標記不等同「公司有舞弊」**，僅是統計風險

## 參考

- Paper: https://arxiv.org/abs/2604.20652
- 相關：Beneish M-Score (1999) — 經典財務舞弊偵測模型
- 相關：Cecchini et al. (2010) "Detecting Management Fraud in Public Companies" Management Science
