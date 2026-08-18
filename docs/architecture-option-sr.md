# 系統架構文件 — 台指選擇權支撐壓力表（option-sr）

> 本文件為「整合進既有專案 tw-stock-scanner」之架構規劃，非新建專案。
> 已實際閱讀並對齊：`app.py`（`/futures-basis`、`/te-tf-strength` route）、
> `scanners/futures_basis.py`、`scrapers/disposition.py`、`scrapers/market.py`、
> `models/database.py`（`init_db()`/upsert 慣例）、`run_daily.py`（`run_market()` 分派）、
> `templates/futures_basis.html`（`fb-*` class + fetch 渲染）、`templates/base.html`（導覽高亮）、
> `config.py`（`REQUEST_HEADERS` / `REQUEST_TIMEOUT` / `DB_PATH`）。
> **TAIFEX 資料端點已實抓驗證**（見下方「資料來源確認」，2026-06-30 實際下載成功）。

## 專案名稱
option-sr（整合進 tw-stock-scanner）

## 專案類型
web-fullstack（整合進既有 Flask 站）＋ automation（TAIFEX 盤後抓取＋每日排程）

## 專案根目錄
`src/tw-stock-scanner/`（`$PRJ = /d/claude/tw-stock-scanner`）。所有檔案路徑以此為根，依既有目錄慣例併入。

---

## 架構總覽

沿用既有「scraper（抓）→ SQLite（存）→ scanner（算）→ Flask route（`/xxx` 頁 + `/api/xxx` 資料）→ template（前端 fetch 渲染）→ run_daily 分派」分層，與 `/futures-basis`、`/te-tf-strength` 完全同構。

```
TAIFEX optDataDown (CSV, Big5)
        │  scrapers/taifex_option.py  (fetch + 解析 + 正規化)
        ▼
   option_daily 表  (models/database.py：建表 + upsert + 查詢 helper)
        │  scanners/option_sr.py  (讀 DB → 支撐/壓力/Max Pain + T字表 rows)
        ▼
   app.py  /option-sr (render)  +  /api/option-sr (jsonify)
        │
        ▼
   templates/option_sr.html  (下拉：日期/契約/檢視 + OI 長條圖 + T字表)
        ▲
   templates/base.html  導覽（接在「電金強弱」後）

   run_daily.py  option 分派 → run_option() → 抓當日入庫（Windows 排程 ~15:30）
```

技術選型：Python 3（既有 `.venv`）、Flask（既有 `app.py`）、`requests`（用 `config.REQUEST_HEADERS` / `REQUEST_TIMEOUT`）、SQLite（`models.database.get_conn`）、前端純原生 JS + CSS/SVG（**不新增外部相依**，OI 長條以 CSS 寬度百分比繪製，比照 `fb-*` 深色風格改前綴為 `os-*`）。

---

## 資料來源確認（TAIFEX，已實抓驗證）

**端點**（每日選擇權行情下載，含 OI）：

```
GET https://www.taifex.com.tw/cht/3/optDataDown
參數：
  down_type       = 1
  commodity_id    = TXO           ← 必須 snake_case（camelCase commodityId 會失敗）
  queryStartDate  = YYYY/MM/DD    ← 單日抓取則起訖同一天
  queryEndDate    = YYYY/MM/DD
Header：沿用 config.REQUEST_HEADERS（含 User-Agent）
逾時：config.REQUEST_TIMEOUT (=30)
編碼：Big5 / cp950（**必須** `resp.encoding='big5'` 或 `resp.content.decode('big5', errors='replace')`；勿用 requests 自動偵測）
```

**回傳為 CSV**，欄位（逗號分隔，含表頭，2026-06-30 單日約 620KB）：

| # (0-based) | 欄位 | 說明 / 存哪 |
|---|---|---|
| 0 | 交易日期 | `2026/06/30` → 轉 ISO `2026-06-30`（存 `date`）|
| 1 | 契約 | `TXO`（固定，過濾用）|
| 2 | 到期月份(週別) | 契約代碼：`202607W1`/`202607F1`/`202607`（月選）→ 存 `contract` |
| 3 | 履約價 | `40100.0000` → `strike` (REAL) |
| 4 | 買賣權 | `買權`→`C` / `賣權`→`P` → `cp` |
| 8 | 收盤價 | → `close`（`-` 視為 NULL）|
| 9 | 成交量 | → `volume` |
| 10 | 結算價 | → `settlement` |
| **11** | **未沖銷契約數 (OI)** | → `oi`（支撐/壓力/Max Pain 核心）|
| 17 | 交易時段 | `一般` / `盤後` → **只取 `一般`**（見風險點）|
| 18 | 漲跌價 | → `change` |
| 19 | 漲跌% | `-78.26%` → `change_pct`（去 `%` 轉 float）|
| 20 | 契約到期日 | `20260701` → `expiry`（決定「最近到期」用）|

**契約代碼實測（2026-06-30 當日存在）**，附各自到期日：

| contract | 到期日 | 類別 |
|---|---|---|
| `202607W1` | 2026-07-01 (三) | 週選（週三到期）|
| `202607F1` | 2026-07-03 (五) | **週選（週五到期）** |
| `202607W2` | 2026-07-08 (三) | 週選 |
| `202607F2` | 2026-07-10 (五) | 週選（週五）|
| `202607`   | 2026-07-15 (三) | **月選（代碼 = YYYYMM，無後綴）** |
| `202608` / `202609` / `202612` / `202703` | 各月第三週三 | 月選（次月/季月）|

> **關鍵發現**：TXO 現有 **週三(W)＋週五(F) 兩種週選**，非只有需求書假設的 `W1~W5`。
> 故契約下拉與「最近到期」判斷**一律由當日實際出現的 contract + 其 `expiry` 動態決定，不得寫死 W1~W5**（見 FN-003 / 風險點）。
> 此同時定案需求書「待確認 #1」：**月選代碼 = `YYYYMM`（6 碼無後綴）**，前端標籤顯示「月選」。

**非交易日 / 資料未出**：端點回傳僅表頭或極少列 → 解析後 rows 為空 → 回 0 筆，記 log 不拋例外。

**替代來源（備註）**：TAIFEX 另有 OpenAPI（`openapi.taifex.com.tw`）與 `optDailyMarketExcel`；本案以 `optDataDown` 為主（已驗證、單檔一日全契約全履約價、含 OI 與到期日），不採 FinMind（`scrapers/market.py` 用 FinMind 只取彙總 P/C，無逐履約價 OI，無法算支撐壓力）。

---

## 測試策略（供 Phase 6）

專案為 automation + web-fullstack，測試分四層：

1. **Scraper 解析測試（FN-001）**：以 2026-06-30 實抓 CSV 存為 fixture（`tests/fixtures/txo_20260630.csv`，Big5），離線餵入解析函式，斷言：
   - 每列正確拆欄；`買權/賣權`→`C/P`；`-`→None；日期轉 ISO；`漲跌%` 去 `%`。
   - **只保留 `一般` 時段**（驗證 `盤後` 列被濾除，無重複 strike+cp）。
   - contract 代碼、expiry 原樣保留；回傳筆數 > 0。
   - 空 CSV / 僅表頭 → 回 `[]`（非交易日容錯）。
2. **DB 測試（FN-002）**：對臨時 DB 跑 `init_db()`（表存在、重複執行不報錯）；`upsert_option_daily` 兩次同資料不增列（唯一鍵 `(date,contract,strike,cp)`）；查詢 helper 回傳正確 available_dates / contracts / rows。
3. **計算測試（FN-003，數值可人工核算）**：
   - 壓力 = Call OI 最大履約價；支撐 = Put OI 最大履約價（用 fixture 手算比對）。
   - Max Pain：構造小型 OI 集（如 3 個履約價），手算 Σ 痛苦值，斷言取最小者。
   - 查無資料 → `ok:False` + 空結構，不拋例外。
   - 契約排序：available_contracts 依 expiry 升冪；預設契約 = expiry ≥ 查詢日之最小 expiry（最近到期）。
4. **API / 整合測試（FN-004）**：Flask test client 打 `/api/option-sr`（無參數走預設；帶 `date`/`contract`；帶無資料日期）→ 驗證 HTTP 200、JSON 結構鍵齊全、`ok` 正確；`/option-sr` 回 200 且含 `os-` 版面。route 例外 → 500 + `ok:False`（比照 `/api/futures-basis`）。
5. **排程測試（FN-007）**：`python run_daily.py option` 可獨立執行、入庫當日資料、log 記筆數；失敗只記 log 不中斷（try/except）。
6. **前端（FN-005/006）**：template 只做「Flask 渲染回 200 + 關鍵 DOM/`os-` class 存在」驗證（比照既有前端不做瀏覽器自動化）。

---

## Function 清單

### FN-001：TAIFEX TXO 盤後抓取與解析
- 對應需求：F-001
- 職責：抓指定日期 TXO 選擇權每日行情 CSV（Big5），解析為逐列 dict，**只保留「一般」時段**，正規化欄位。純抓取＋解析，不碰 DB（便於離線測試）。
- 檔案路徑：`$PRJ/scrapers/taifex_option.py`
- 主要函式與介面：
  - `fetch_txo_daily(date_str)`
    - 輸入：`date_str` (str, `YYYYMMDD`，比照既有 scraper)
    - 回傳：`list[dict]` — 每筆 `{date, contract, strike, cp, close, settlement, change, change_pct, volume, oi, expiry}`（`date` 為 ISO；`cp` ∈ `'C'/'P'`；數值欄位 float/int 或 None；非交易日回 `[]`）
  - `_parse_csv(text)`（內部）：輸入 Big5 已解碼字串，回傳同上 `list[dict]`；濾 `交易時段=='一般'`。
  - `_to_float(v)` / `_to_int(v)`（內部）：`'-'`/`''`/`'--'`→None（比照 `futures_basis._parse_float`）。
- 依賴：`config.REQUEST_HEADERS`、`config.REQUEST_TIMEOUT`。無其他 FN 依賴。
- 測試方式：見測試策略 (1)。

### FN-002：資料表 option_daily + DB helper
- 對應需求：F-008（＋供 F-001 寫入、F-002/F-003 讀取）
- 職責：於 `init_db()` 新增 `option_daily` 表與索引；提供 upsert 與查詢 helper。
- 檔案路徑：`$PRJ/models/database.py`
- 介面（新增函式）：
  - `upsert_option_daily(conn, rows)`：`rows` = FN-001 輸出的 `list[dict]`；`INSERT OR REPLACE` 逐列寫入（僅 `conn.execute`，commit 由呼叫端負責，比照既有 upsert）。回傳 `int`（寫入列數）。
  - `get_option_dates(conn, limit=250)` → `list[str]`（`option_daily` DISTINCT date DESC）。
  - `get_option_contracts(conn, date)` → `list[dict]` `{contract, expiry}`（該日 DISTINCT，依 expiry 升冪；供下拉）。
  - `get_option_rows(conn, date, contract)` → `list[sqlite3.Row]`（該日該契約全 strike/cp，含 close/change/change_pct/volume/oi）。
  - `get_latest_option_date(conn)` → `str|None`。
- 資料表 schema：見下方「資料表 schema」節。
- 依賴：無（`get_conn` 既有）。
- 測試方式：見測試策略 (2)。

### FN-003：支撐/壓力/Max Pain 計算（scanner）
- 對應需求：F-002、F-003（可選日期後端）、F-004（可選契約後端）
- 職責：讀 DB，組出前端所需完整資料結構（含可選日期/契約清單、支撐壓力 Max Pain、T字表 rows）。為 `/api/option-sr` 的唯一資料來源。
- 檔案路徑：`$PRJ/scanners/option_sr.py`
- 介面：
  - `compute_option_sr(date=None, contract=None)`
    - 輸入：`date` (str|None, ISO `YYYY-MM-DD`；None→最新有資料日)、`contract` (str|None；None→該日最近到期契約)
    - 回傳：`dict`（見「API JSON 契約」）。查無資料回 `{'ok': False, 'error': ..., 'rows': [], ...}`，不拋例外。
  - `_max_pain(strikes_oi)`（內部）：輸入 `{strike: {'call_oi', 'put_oi'}}`，回傳 `(max_pain_strike, pain_value)`。
    - 公式：對每個候選 K（=實際出現的履約價集合，定案需求書「待確認 #2」），
      `痛苦 = Σ_call OI_i·max(K−strike_i,0) + Σ_put OI_j·max(strike_j−K,0)`，取最小者。
- 依賴：FN-002 查詢 helper。
- 測試方式：見測試策略 (3)。

### FN-004：Flask route（頁面 + API）
- 對應需求：F-006（＋承載 F-003/F-004/F-005 參數）
- 職責：新增 `/option-sr`（render template）與 `/api/option-sr`（jsonify），比照 `/futures-basis` 樣式（try/except、500 + `ok:False`）。
- 檔案路徑：`$PRJ/app.py`（接在 `/te-tf-strength` 區塊後新增）
- 介面：
  - `GET /option-sr` → `render_template('option_sr.html')`
  - `GET /api/option-sr?date=YYYY-MM-DD&contract=202607W1`
    - 讀 `request.args.get('date')` / `get('contract')`（皆可省略，走預設）；`view` 由前端自行切換，不進後端。
    - `from scanners.option_sr import compute_option_sr` → `jsonify(result)`
    - 例外：`logger.error(...)` + `jsonify({'ok': False, 'error': str(e), 'rows': [], ...}), 500`
- 依賴：FN-003。
- 測試方式：見測試策略 (4)。

### FN-005：前端頁面 option_sr.html
- 對應需求：F-003、F-004、F-005（前端）
- 職責：下拉（日期 / 契約 / 檢視）+ 支撐/壓力/Max Pain 摘要卡 + **OI 長條圖（預設）** ↔ **T字報價表** 切換。`extends base.html`，class 前綴 `os-*`（比照 `fb-*`），深色主題，無外部相依。
- 檔案路徑：`$PRJ/templates/option_sr.html`
- 行為：
  - 載入 → `fetch('/api/option-sr' + query)`；依 `available_dates`/`available_contracts` 填兩個 `<select>`；`view` 按鈕 `os-btn.active` 切 `bars`/`table`（預設 `bars`）。
  - 切日期/契約 → 帶新 query 重新 fetch，全頁（摘要＋圖＋表）同步更新。
  - `!data.ok` 或空 rows → 顯示「該日無資料」提示（非白頁報錯）。
  - **OI 長條圖**：以履約價為列，Call OI（一側）與 Put OI（另一側）雙向長條（寬度 = OI/最大OI×100%，純 CSS）；標示壓力（Call OI 最大）、支撐（Put OI 最大）、Max Pain 列。
  - **T字表**：欄位 `Call(成交價/漲跌/成交量/未平倉) | 履約價 | Put(成交價/漲跌/成交量/未平倉)`，依履約價排序；壓力/支撐/Max Pain 列加標記 class。
- 依賴：FN-004 的 JSON 契約（介面已定，可平行開發）。
- 測試方式：見測試策略 (6)。

### FN-006：導覽連結（base.html）
- 對應需求：F-006
- 職責：於「電金強弱」`<li>`（`request.endpoint == 'te_tf_strength_page'`）後新增一列，連 `/option-sr`，高亮 `request.endpoint == 'option_sr'`（route function 命名為 `option_sr`）。
- 檔案路徑：`$PRJ/templates/base.html`（約第 130~136 行後）
- 介面：新增
  ```html
  <li class="nav-item">
      <a class="nav-link {% if request.endpoint == 'option_sr' %}active{% endif %}"
         href="/option-sr">選擇權支撐壓力</a>
  </li>
  ```
- 依賴：無（route function 名須與 FN-004 一致 → 命名 `option_sr`）。
- 測試方式：頁面渲染回 200 且導覽含該連結。

### FN-007：每日盤後排程 run_option()
- 對應需求：F-007
- 職責：`run_daily.py` 新增 `run_option(date_str=None)` 與 `sys.argv[1]=='option'` 分派；抓當日 TXO 入庫，log 記筆數，失敗只記 log（比照 `run_market()` / `run_broker()`）。
- 檔案路徑：`$PRJ/run_daily.py`
- 介面：
  - `run_option(date_str=None)`：`date_str` None→今天 `YYYYMMDD`；`from scrapers.taifex_option import fetch_txo_daily` → `upsert_option_daily(conn, rows)` → `conn.commit()`；log 筆數；try/except + rollback。回 `bool`。
  - `main()` 新增 `elif sys.argv[1] == 'option': run_option()`；並於用法字串補一行（建議排程 ~15:30，資料下午出）。
  - 亦支援 `python run_daily.py option 20260630`（指定日回補，從簡）。
- 依賴：FN-001、FN-002。
- 測試方式：見測試策略 (5)。

---

## API JSON 契約（`/api/option-sr`，FN-003/FN-004/FN-005 共同介面）

```jsonc
{
  "ok": true,
  "error": null,
  "date": "2026-06-30",
  "contract": "202607W1",
  "contract_label": "週選 W1（07/01 到期）",
  "available_dates": ["2026-06-30", "2026-06-27", "..."],          // DESC
  "available_contracts": [                                          // 依 expiry 升冪
    {"code": "202607W1", "expiry": "20260701", "label": "週選 W1（07/01 到期）"},
    {"code": "202607F1", "expiry": "20260703", "label": "週選 F1（07/03 到期）"},
    {"code": "202607",   "expiry": "20260715", "label": "月選（07/15 到期）"}
  ],
  "resistance": {"strike": 42000, "call_oi": 15832},               // Call OI 最大
  "support":    {"strike": 40000, "put_oi": 18211},                // Put OI 最大
  "max_pain":   {"strike": 41000, "pain": 1234567.0},
  "stats": {"total_call_oi": 0, "total_put_oi": 0, "pc_ratio": 0.0},
  "rows": [                                                         // 依 strike 升冪
    {
      "strike": 40000,
      "call": {"close": 210.0, "change": -30.0, "change_pct": -12.5, "volume": 3200, "oi": 8100},
      "put":  {"close": 55.0,  "change": 12.0,  "change_pct": 27.9,  "volume": 5400, "oi": 18211},
      "is_resistance": false, "is_support": true, "is_max_pain": false
    }
    // ...
  ]
}
```
查無資料：`{"ok": false, "error": "該日/契約無資料", "date": ..., "contract": null, "available_dates": [...], "available_contracts": [], "rows": []}`（HTTP 200；僅程式例外才 500）。

---

## 資料表 schema（option_daily）

```sql
CREATE TABLE IF NOT EXISTS option_daily (
    date        TEXT    NOT NULL,               -- ISO 'YYYY-MM-DD'
    contract    TEXT    NOT NULL,               -- '202607W1' / '202607F1' / '202607'(月選=YYYYMM)
    strike      REAL    NOT NULL,               -- 履約價
    cp          TEXT    NOT NULL CHECK(cp IN ('C','P')),
    close       REAL,                           -- 收盤價（'-'→NULL）
    settlement  REAL,                           -- 結算價
    change      REAL,                           -- 漲跌價
    change_pct  REAL,                           -- 漲跌%（去 % 後 float）
    volume      INTEGER DEFAULT 0,              -- 成交量
    oi          INTEGER DEFAULT 0,              -- 未沖銷契約數（OI）
    expiry      TEXT,                           -- 契約到期日 'YYYYMMDD'
    updated_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, contract, strike, cp)    -- 唯一鍵，避免重複列（F-008 驗收）
);
CREATE INDEX IF NOT EXISTS idx_option_daily_date          ON option_daily(date);
CREATE INDEX IF NOT EXISTS idx_option_daily_date_contract ON option_daily(date, contract);
```
- 只存「一般」時段列（FN-001 已濾），故 `(date,contract,strike,cp)` 為天然唯一鍵。
- 寫入用 `INSERT OR REPLACE`（比照 `upsert_daily_price`），重覆抓同日不產生重複（F-001 驗收）。
- 建表放進既有 `init_db()` 的 `executescript`（`CREATE TABLE IF NOT EXISTS`，重覆執行不報錯）。

---

## 依賴關係

```
FN-001 (scraper)        獨立（僅依 config）
FN-002 (DB 表+helper)   獨立
FN-005 (template)       獨立（僅依已定義之 API JSON 契約）
FN-006 (nav)            獨立（僅需約定 route function 名 = option_sr）
FN-003 (scanner)   → 依賴 FN-002
FN-007 (run_option)→ 依賴 FN-001, FN-002
FN-004 (routes)    → 依賴 FN-003（＋ template 檔存在）
```

## 並行分批計畫

- **第 1 批（可並行）**：FN-001、FN-002、FN-005、FN-006
  （四者介面已完全定義：scraper 輸出 dict 結構、DB helper 簽章、API JSON 契約、route function 名 `option_sr`，彼此不阻塞。）
- **第 2 批（等第 1 批）**：FN-003（依 FN-002）、FN-007（依 FN-001+FN-002）
- **第 3 批（等第 2 批）**：FN-004（依 FN-003）
- **整合驗證**：`run_daily.py option 20260630` 實抓入庫 → 開 `/option-sr` 端到端核對數字（Phase 6）。

---

## 檔案結構（本需求新增／異動，皆在 `$PRJ` 下）

```
src/tw-stock-scanner/
├── app.py                         # [改] 新增 /option-sr、/api/option-sr（FN-004）
├── run_daily.py                   # [改] 新增 run_option() + 'option' 分派（FN-007）
├── models/
│   └── database.py                # [改] init_db 建 option_daily + 4 個 helper（FN-002）
├── scrapers/
│   └── taifex_option.py           # [新] fetch_txo_daily / _parse_csv（FN-001）
├── scanners/
│   └── option_sr.py               # [新] compute_option_sr / _max_pain（FN-003）
├── templates/
│   ├── base.html                  # [改] 導覽新增「選擇權支撐壓力」（FN-006）
│   └── option_sr.html             # [新] 下拉 + OI 長條圖 + T字表（FN-005）
└── tests/
    ├── fixtures/txo_20260630.csv  # [新] Big5 實抓樣本（測試用）
    └── test_option_sr.py          # [新] 解析/DB/計算/API 測試
```

---

## 風險點

1. **Big5 編碼**：`optDataDown` 回傳 cp950/Big5，requests 自動偵測會亂碼。務必 `resp.encoding='big5'`（或 `resp.content.decode('big5', errors='replace')`）。已在 FN-001 明列。
2. **一般 vs 盤後雙時段**：每個 `(strike,cp)` 有「一般」與「盤後」兩列，盤後列 OI/結算多為 `-`。若不濾除，唯一鍵 `(date,contract,strike,cp)` 會被盤後空值列覆蓋掉真實 OI → 支撐壓力全錯。**FN-001 必須先濾 `交易時段=='一般'`**（測試策略已含此斷言）。
3. **週選代碼不只 W1~W5**：實測含週五到期的 `F1/F2` 系列。契約下拉與「最近到期」判斷**一律由當日資料 + `expiry` 動態產生**，禁止寫死 W1~W5（否則漏列 F 系列 / 月選）。需求書假設已據此修正。
4. **月選代碼表示法**：定案為 `YYYYMM`（6 碼無後綴），前端標籤顯示「月選」。解析時以「長度/有無 W/F」區分週選與月選（FN-003 產 label 用）。
5. **排程時點**：盤後資料下午出爐，且「一般」時段 OI 於午後即有；建議 Windows 排程掛 ~15:30（正式時點交付時以實抓再確認，需求書「待確認 #3」）。
6. **單日檔案量**：一日全契約全履約價含雙時段約 620KB / 數千列；單次 GET 可負荷，每日一次，對站台無壓力。仍加 `REQUEST_TIMEOUT` 與 UA。
7. **禁爬 WantGoo**：全程僅取 TAIFEX 公開盤後資料，符合非功能需求。
```
