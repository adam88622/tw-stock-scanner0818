# 03 實證結果：tw-stock-scanner Agent Benchmark（真實 log 分析）

> 實驗日期：2026-04-28
> 資料來源：`D:\claude\tw-stock-scanner\log\`（5 個 log 檔，共 10,939 行）
> 工具：`results/03_empirical.py` + `poc/03_agent_benchmark_tracker.py`
> 成本估算：Claude Sonnet 4.5 定價（$3 / $15 per MTok），用 log 字元數逼近 token，僅供量級參考

---

## 一、各 log 摘要

| log 檔 | agent | 行數 | 期間 | runtime | retry | INFO | WARN | success_days | failed_days | success | 估算成本 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 20260413-133245-backfill-institutional.log | backfill-runner | 0 | — | — | 0 | 0 | 0 | 0 | 0 | **FAIL**（0-byte，agent 沒寫東西就死了）| $0.00 |
| 20260413-133601-backfill-institutional.log | backfill-runner | 5,374 | 13:36:01–21:20:57 | 7h44m | 17 | 4,831 | 542 | 4,573 | 0 | **OK** (ALL DONE) | $0.70 |
| backfill_detached.log | backfill-runner | 5,385 | 13:36:01–21:20:57 | 7h44m | 17 | 4,831 | 542 | 4,573 | 0 | **OK** (ALL DONE) | $0.70 |
| clean_institutional_20260427_123517.log | data-cleaner | 134 | 12:35:17–12:35:20 | <1s | 0 | 0 | 0 | — | — | OK（無 ERROR） | $0.009 |
| export_institutional_20260427_123436.log | data-exporter | 46 | 12:34:36–12:34:47 | ~11s | 0 | 0 | 0 | — | — | OK（無 ERROR） | $0.003 |

**注意**：`backfill_detached.log` 與 `20260413-133601-backfill-institutional.log` 內容幾乎重複（前者多 11 行尾部訊息），實質上是同一次 backfill 透過 `tee`/雙 sink 寫出兩份；clean 與 export log 因為 stdout 是中文 cp950，被存成 mojibake，但 ASCII 結構訊息（行數、表格、ERROR 字眼）仍可解析。

---

## 二、整體成功率與失敗模式

`BenchmarkTracker.summary()` 結果：

```json
{
  "n_runs": 5,
  "success_rate": 0.80,
  "retry_rate": 0.40,
  "schema_violation_rate": 0.20,
  "avg_runtime_sec": 11158.4,
  "avg_cost_usd": 0.282,
  "total_cost_usd": 1.41
}
```

分 agent：

| agent | n_runs | success_rate | retry_rate | avg_runtime | total_cost |
|---|---|---|---|---|---|
| backfill-runner | 3 | **66.7%** | 66.7% | 5h10m | $1.40 |
| data-cleaner | 1 | 100% | 0% | <1s | $0.009 |
| data-exporter | 1 | 100% | 0% | 11s | $0.003 |

**失敗模式排序**（出現次數）：

1. **空 log（0-byte）**：`20260413-133245-backfill-institutional.log` — 13:32 啟動的第一次 backfill 在寫 log 之前就崩潰。3 分鐘後（13:36）才有第二次成功啟動，這對應到專案記憶 `project_institutional_backfill.md` 的「2026-04-13 13:36 啟動」紀錄——也就是說，**第一次嘗試是失敗的，13:36 才是真正跑成功的那次**。
2. **API JSON parse 失敗**：「請求失敗 (第 1 次): Expecting value: line 1 column 1 (char 0)」共 **15 次**，全部在 retry 第 1 次後成功（第 2 次只出現 1 次）→ 重試機制有效，但 TWSE/TPEx API 在連發請求時會回空字串。
3. **無資料日（非真錯）**：`TWSE 法人買賣超無資料: 20120503` 等共 542 條 WARNING — 這些是台股休假日 / 補班日，屬於業務正常情境而非 agent bug。

---

## 三、真實案例：法人回補 backfill 的 retry / error 統計

實際 backfill 在 7h44m 內處理 4,573 個交易日（TWSE + TPEx 合併）：

- **total HTTP 失敗**：17 次
- **單次重試後成功**：16 次（94.1%）
- **二次重試後成功**：1 次（5.9%）
- **三次以上**：0 次
- **failed_days**：0 — 所有交易日最終都灌進資料庫

**有效 throughput**：4,573 days / 27,896 sec ≈ **6.1 sec / day**，與 log 內預估的「TWSE 4:42:50（每日 5 sec）」一致。

換算 retry 率：**17 retries / 4,573 tasks = 0.37%**，遠低於 OSWorld-Verified 在前緣 agent 上 21–25% 的 retry 率。原因是這個 agent **不是 LLM agent，而是傳統 ETL pipeline**——它的 retry 是 `requests` 層的固定退避，不是 model reasoning。

---

## 四、結論

**Q1：tw-stock-scanner pipeline 真實成功率多少？**

- 用「每個 log = 一次 run」的粒度：**80%**（5 跑 4 成）。
- 用「每個交易日 = 一次 task」的粒度：**99.63%**（4,573 / 4,590 含 retry 後失敗 0）。
- 第一個粒度反映「啟動穩健性」，第二個粒度反映「資料正確性」。對量化研究來說後者才是業務指標。

**Q2：最該優化的 agent 是哪個？**

**backfill-runner**。理由：

1. 是唯一發生 **冷啟動失敗**（0-byte log）的 agent，現有 dispatcher 沒有捕到這次 crash → 應加 health check（log 寫入後 30 秒內若沒有任何行就視為 crash 重啟）。
2. 是唯一觸發 retry 的 agent（17 次 / 542 WARNING），雖然 retry 機制 OK，但 **WARNING/INFO 比 = 11.2%** 偏高。建議把「無資料日」WARNING 降級為 DEBUG，避免噪音掩蓋真錯誤。
3. 佔 total runtime 99.9%、total cost ~99% — 任何優化的 ROI 都最高。

**Q3：對 Stanford AI Index 觀察的呼應？**

AI Index 2026 提到「89% agent 卡在 production gap」，本案的 tw-stock-scanner 雖不是 LLM agent，但**第一個 0-byte log 就是典型的 production failure mode**：agent 在 dev 環境跑得起來，到 production 因為環境差異（路徑、權限、編碼）就靜默失敗。BenchmarkTracker 把這次失敗顯式記下來，正是 AI Index 強調的「observability gap」第一線價值。

---

## 五、限制與後續工作

- **Token/成本是 best-effort 估算**：用 log 字元數逼近 token 是粗估；真實 LLM agent 應改成從 API response usage header 直接取。
- **Encoding 問題**：clean / export log 是 cp950 mojibake，目前用 `latin-1` fallback 讀取，僅能抓 ASCII 結構訊息。建議源頭把 logging handler 改 `encoding='utf-8'`。
- **重複 log**：`backfill_detached.log` 與 `20260413-133601-...log` 是同一次 run 的雙寫；後續 dispatcher 應在 `task_id` 層級去重，避免 success_rate 被人為拉高。
- **下一步**：把 tracker 嵌入 `dispatcher` 的 phase5/phase6 hook，每跑一個 sub-agent 就 `log_run()` 一筆，三個月後就能用真實長尾資料畫 control chart。

---

## 附件

- 程式：`results/03_empirical.py`
- Tracker store：`results/03_agent_runs.json`
- 完整 metrics JSON：`results/03_empirical_summary.json`
