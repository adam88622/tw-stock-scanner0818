# 去槓桿壓力儀表板 — 即時管線規格 (Phase B)

## 目標
建立 `scanners/deleveraging.py`，函式 `build_indicators() -> dict`，產出與原 `compute_indicators.py`
相同結構的 IND dict，供 `/deleveraging` route 注入 `templates/deleveraging.html`。
原始靜態快照在 `data/deleveraging_snapshot.json`（asof 2026-07-14，score=78.14）。

## 核心架構：向前追加（forward-append），不回補
快照**已含每條 series 的完整 1518 日歷史**（`dates` 2020→2026-07-14）。
即時管線 **不需回補 6 年**，只需：
1. 載入快照為歷史基底。
2. 對 asof（2026-07-14）之後的每個新交易日，從即時來源抓「當日單日值」追加到各 series。
3. 重算 rolling 百分位、unwind、momentum、foreign_sell、composite。
4. 快取結果（例如 `data/deleveraging_live.json` + 當日日期戳），同日重複呼叫直接回傳。

## 鐵則驗收（correctness gate）
`build_indicators()` 若**沒有任何新交易日**（即只餵快照），**必須逐位元重現快照**：
- `composite.score == 78.14`
- 每個 `composite.parts` 值、每條 `series`、`unwind`、`latest`、`pctl` 全部一致。
先讓「零新增日→等於快照」通過，才接即時來源。這保證百分位/composite 數學正確。

## Composite 公式（已解碼並驗證，見 docs/deleveraging_golden_spec.json）
`score = Σ weight_i * subscore_i`，weights 總和=100：

| 元件 | weight | subscore 公式 |
|---|---|---|
| margin_total_pctl | 12 | pctl(margin_total)/100 |
| turn_heat_pctl | 8 | pctl(turn_heat)/100 |
| margin_util_pctl | 5 | pctl(margin_util)/100 |
| unwind_remaining | 22 | excess_now/excess_peak |
| margin_momentum | 8 | **待反解**（融資5日變化映射，snapshot subscore=0.25）|
| maint_inverse_pctl | 12 | pctl(maint_inverse)/100 |
| low140_pctl | 8 | pctl(maint_low140_share)/100 |
| rv20_pctl | 8 | pctl(rv20)/100 |
| turn_val_pctl | 7 | pctl(turn_val)/100 |
| foreign_sell_pressure | 10 | **待反解**（foreign_net_20d 映射，snapshot subscore=0.9178）|

驗證錨點（快照最新日）：pctl_margin_total=98.72→part 11.8464；unwind excess_now/peak=0.7061→15.5345。

## 待反解（用快照同時含 raw series + 衍生值來反解，逐日驗證）
1. **百分位函式**：rolling window=1250 交易日、point-in-time。用 `series[k]` 重算，須完全吻合
   `series['pctl_'+k]`（快照有 pctl_margin_total/turn_heat/margin_util/maint_inverse/maint_low140_share/rv20/turn_val）。
   常見定義：window 內 <= 當前值 的比例 ×100。逐日比對選對定義。
2. **margin_momentum**：由 `series['margin_total']` 的 5 日變化推。method 原文：
   「融資5日跌幅大於−1%」為訊號條件。subscore 快照=0.25。
3. **foreign_sell_pressure**：由 `series['foreign_net_20d']`（或 5d）推，subscore=0.9178。
4. **unwind**：maint_wavg 序列的 peak（episode 最高）、baseline（=config.baseline_date 2026-04-30 當日值）、
   current（最新）。excess=maint-baseline；excess_peak=peak-baseline；excess_now=current-baseline；
   U(已出清比例)=1-excess_now/excess_peak；unwind_remaining subscore=excess_now/excess_peak。

## 即時來源對應（向前追加時每日抓單日值）
| series | 即時來源 | 狀態 |
|---|---|---|
| foreign_net_5d/20d | scanner DB `institutional`（sum foreign_buy 全市場，滾動 5/20 日）| DB 有 2012-2026 |
| turn_val / turn_heat | scanner DB `daily_prices`（sum trade_value 全市場）| 確認 trade_value 是否填值 |
| taiex_close / taiex_dd / rv20 | TWSE 加權指數日資料（DB 無指數列，需抓）| 需 fetcher |
| margin_total / margin_util / short_balance / short_margin_ratio | TWSE MI_MARGN 大盤合計 | 需 fetcher |
| maint_wavg / maint_low130_share / maint_low140_share | 公開代理值（見 research 產出）| 需 fetcher（代理）|
| pcr_oi / pcr_vol | scanner DB `option_daily` | DB 僅近 10 日，代理/略過亦可 |
| daytrade_ratio | TWSE 當沖統計 | 可降級（原本就允許 pending）|

即時來源抓不到某日時：該 series 該日追加 None（保留最後有效值邏輯同原管線），
並在輸出設 `partial=true` / 對應 asof 欄位，不可用 0 混充。

## 交付
- `scanners/deleveraging.py`（含 `build_indicators()` + 各 fetcher；fetcher 抓不到回 None 不可 crash）
- `tests/test_deleveraging.py`：驗收「零新增日→重現 78.14 與快照」+ 百分位函式逐日比對。
- route 已就緒（app.py `/deleveraging` 會呼叫 `build_indicators()`，失敗自動回退快照）。
