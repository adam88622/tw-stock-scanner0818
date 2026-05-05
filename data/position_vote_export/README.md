# Position-Vote 八指標資料集（匯出日 2026-04-30）

資料來源：tw-stock-scanner / db/scanner.db
編碼：UTF-8 with BOM（Excel 直開不亂碼）

## 檔案清單

| 檔名 | 指標 | 維度 | 來源 | 筆數 | 起 ~ 迄 |
|------|------|------|------|------|---------|
| `01_regime_AE.csv` | AE 體制 (regime) | 股票異常 | tw-stock-scanner Autoencoder（用現役模型對 SPY+VIX 歷史特徵回推；非 point-in-time） | 6,301 | 2001-04-06 ~ 2026-04-29 |
| `02_credit_spread.csv` | 信用利差 (credit) | 信用風險 | Yahoo Finance: HYG / SHY / SPY | 4,605 | 2008-01-09 ~ 2026-04-29 |
| `03_breadth_full_market.csv` | 市場廣度 (breadth, Layer 3) | 台股內部 | TWSE + TPEx 全市場日漲跌家數（自 daily_prices 聚合） | 5,259 | 2004-03-01 ~ 2026-04-30 |
| `04_T10Y3M.csv` | 殖利率利差 10Y-3M | 景氣政策 | FRED 系列 T10Y3M | 6,247 | 2001-05-07 ~ 2026-04-29 |
| `05_CP_SPREAD.csv` | CP-Treasury 資金壓力 | 資金流動 | FRED DCPF3M − DTB3 | 5,593 | 2001-05-07 ~ 2026-04-28 |
| `06_DOLLAR.csv` | 美元指數 (DXY) | 資金流動 | Yahoo DX-Y.NYB（FRED DTWEXBGS fallback） | 6,309 | 2001-05-07 ~ 2026-04-29 |
| `07_COR3M_VIX.csv` | VIX 系統性風險 | 尾部波動 | Yahoo ^VIX（FRED VIXCLS fallback） | 6,304 | 2001-05-07 ~ 2026-04-29 |
| `08_MOVE.csv` | MOVE 國債波動 | 尾部波動 | Yahoo ^MOVE | 5,800 | 2002-11-12 ~ 2026-04-29 |

## 欄位說明

### 01_regime_AE.csv
- `recon_error` Autoencoder 重構誤差
- `tau` 動態門檻（rolling quantile）
- `regime` `normal` 或 `abnormal`（recon_error > tau 視為 abnormal）

### 02_credit_spread.csv
- `hyg_shy_ratio` HYG / SHY 收盤比
- `indicator_value` 189 日滾動百分位（反向，0~1，越高代表信用風險越高）
- `signal` `GREEN` / `YELLOW` / `RED`
- `spy_close` SPY 當日收盤；`trend5d` 5 日趨勢百分比

### 03_breadth_full_market.csv
- `advancers` / `decliners` / `unchanged` 上漲/下跌/平盤檔數（門檻 ±0.1%）
- `limit_up` / `limit_down` 漲停/跌停（±9.5%）家數
- `adr` advance/decline ratio = adv / dec
- `score` adr / (adr + 1)
- `regime` `STRONG_BULL` / `BULL` / `NEUTRAL` / `BEAR` / `CRASH`
- 註：本檔僅含 Layer 3（全市場），完整 3 層投票需另跑 `scanners/breadth.py`

### 04 ~ 08 macro_indicators.csv
共同欄位 `date / value / signal`
- `signal` `GREEN` / `YELLOW` / `RED`，分類規則見 `scanners/macro_indicators.py` THRESHOLDS

## 資料完整度與限制

| 指標 | 完整度 | 備註 |
|------|--------|------|
| credit | ✅ 完整 | Yahoo 提供 2007 起，已最完整 |
| breadth | ✅ 完整 | TWSE/TPEx 自 2012-05-02 起回補完成 |
| T10Y3M / CP_SPREAD / DOLLAR / COR3M | ✅ 已延長 | 2026-04-29 補抓至 2001-05 起 |
| MOVE | ⚠ 部分 | Yahoo ^MOVE 僅提供 2002-11-12 起 |
| regime | ⚠ Back-inference | 已用現役 AE 模型回推至 2001-04-06。**模型 τ 是用近 2 年訓練的**，套到歷史是「現在的模型回頭看歷史」的結果。可用於相關性分析、特徵跨期比較，**不可拿來嚴格回測**（look-ahead bias）。要 point-in-time 需要對每個歷史日期重新滾動訓練模型。 |

## 加權與門檻（六指標投票，position_vote.py）

- credit 15% / breadth 25% / T10Y3M 20% / CP_SPREAD 15% / DOLLAR 10% / COR3M 15%
- 強制上限：credit RED → 50%；breadth CRASH → 30%；T10Y3M RED → 50%；CP_SPREAD RED → 40%
- AE regime 與 MOVE 因共線性已排除於投票，僅保留於相關性分析。