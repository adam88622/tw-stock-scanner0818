---
paper_id: 11
title: ChatGPT 作為時光膠囊：價格發現的極限
title_en: ChatGPT as a Time Capsule - The Limits of Price Discovery
arxiv: 2604.21433
date: 2026-04-24
category: q-fin.GN
feasibility: REFERENCE
action: 設計準則
priority: N/A
---

# 11 · ChatGPT 作為時光膠囊（設計準則參考）

## 論文要旨

LLM 的訓練資料有截止日（cutoff），這在金融應用上產生兩個嚴重問題：
1. **時光膠囊效應**：LLM 對 cutoff 後事件無感知，導致它的「市場觀點」實際是過時的
2. **混淆效應**：LLM 可能用 cutoff 後的資料推論出 cutoff 前已知的「未來」（資訊洩漏）

兩者都會破壞回測的真實性，並在實盤產生不可預測偏誤。

## 對我方 LLM 系統設計的鐵律

### 1. **絕對禁止：用 LLM 做歷史回測訊號**

❌ 錯誤做法：
```python
# 用 GPT-4 對 2020 年的新聞做情緒分析
# 但 GPT-4 訓練資料含 2020 年 → 它「知道」之後發生什麼 → 偏誤
```

✅ 正確做法：
```python
# 用「截止於分析日之前」的模型
# 或用本地 model 嚴格控制訓練資料
# 或 walk-forward 方式：每年用該年訓練的 model
```

### 2. **明確標註 cutoff date**

所有 LLM 訊號的儲存必須包含：
```json
{
  "signal": ...,
  "model": "claude-opus-4-7",
  "cutoff_date": "2025-12-31",  // 模型訓練截止日
  "as_of_date": "2026-04-27",  // 訊號生成時間
  "input_data_cutoff": "2026-04-26"  // 輸入資料截止
}
```

### 3. **回測必須遵守 walk-forward**

對任何 LLM 訊號做歷史回測：
- **不可** 用 2026 年的 LLM 對 2018 年資料生成訊號 → 必偏誤
- **可** 模擬「假設 2018 年我們有今天 LLM 的能力」（但需在報告明確標註）
- **理想** 每年用該年的 model（如 GPT-3.5 for 2022, GPT-4 for 2023, Claude 3 for 2024…）

### 4. **訊號鮮度監控**

建立指標：
```python
def signal_freshness(signal_date, model_cutoff):
    """
    返回訊號的「鮮度」：
    - >= 0：cutoff 之後產生，正常
    - < 0：cutoff 之前產生，被「時光膠囊」污染
    """
    return (signal_date - model_cutoff).days
```

## 與其他項目整合

- **#1 LLM 語意網路** — 必須註記 model cutoff，回測需 walk-forward
- **#4 多代理 LLM 推薦** — 同上，且需確保所有 agent 用相同 cutoff
- **#8 LLM 財報舞弊** — 已知舞弊案歷史驗證需特別小心（model 已知結局）

## 內部設計檢查清單

新建立任何 LLM 訊號前，回答以下：
- [ ] 訊號是否會用於歷史回測？
- [ ] 若是，model 訓練 cutoff 是否早於回測期間？
- [ ] 若否，是否使用 walk-forward 替代？
- [ ] 訊號是否儲存了 model version、cutoff、as-of date？
- [ ] 訊號的 audit log 是否可查？

## 參考

- Paper: https://arxiv.org/abs/2604.21433
- 相關：Lopez-Lira & Tang (2023) "Can ChatGPT Forecast Stock Price Movements?" arXiv
- 相關：Sarkar & Vafa (2023) "Lookahead Bias in Pretrained Language Models for Trading"
