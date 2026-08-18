# 需求規格書 — 台指選擇權支撐壓力表（option-sr）

> 本規格為「整合進既有專案 tw-stock-scanner」需求，非新建專案。
> 撰寫時已實際閱讀既有 `app.py`（`/futures-basis`、`/te-tf-strength` route 樣式）、
> `scanners/futures_basis.py`、`scrapers/disposition.py`、`templates/futures_basis.html`、
> `templates/base.html` 導覽、`run_daily.py` 排程分派、`models/database.py` 建表慣例，
> 確保本規格貼合現有風格。

## 專案名稱

option-sr

## 專案根目錄（整合對象）

既有專案 `src/tw-stock-scanner/`（路徑 `/d/claude/tw-stock-scanner`）。
本需求不新建獨立專案，所有程式碼依既有目錄慣例併入：

| 類型 | 落點 | 對應既有範例 |
|------|------|-------------|
| 抓資料 | `scrapers/taifex_option.py`（新增） | `scrapers/disposition.py`、`scrapers/market.py` |
| 計算 | `scanners/option_sr.py`（新增） | `scanners/futures_basis.py` |
| 頁面 route + API route | `app.py`（新增 `/option-sr`、`/api/option-sr`） | `/futures-basis` + `/api/futures-basis` 附近 |
| 前端頁面 | `templates/option_sr.html`（新增） | `templates/futures_basis.html` |
| 導覽連結 | `templates/base.html`（新增一列，接在「電金強弱」後） | 既有 `<li class="nav-item">` |
| 每日排程 | `run_daily.py` 新增分派參數 `option`（新增 `run_option()`） | `run_market()` + `sys.argv[1] == 'market'` |
| 資料儲存 | `models/database.py` 新增建表（`option_daily`） | `init_db()` 內 `CREATE TABLE IF NOT EXISTS ...` |

## 專案概述

複製玩股網（WantGoo）「台指選擇權支撐壓力表」頁面的功能，整合進 tw-stock-scanner。
資料**不爬 WantGoo**，改抓台灣期貨交易所（TAIFEX）公開的選擇權盤後每日行情＋未平倉（OI）資料，
自行計算支撐、壓力與 Max Pain，並提供 OI 長條圖與 T 字報價表兩種檢視。
標的以台指選擇權（TXO）為主。

## 專案類型

- 主要類型：web-fullstack（整合進既有 Flask 站）
- 次要類型：automation（TAIFEX 盤後資料抓取 + 每日排程）

## 技術選型

- 語言：Python 3（沿用既有 `.venv`）
- 後端：Flask（沿用既有 `app.py`，route 回傳 `render_template` / `jsonify`）
- 抓取：`requests`（沿用既有 scraper 風格，含 `HEADERS`、`REQUEST_TIMEOUT`）
- 儲存：SQLite（沿用既有 `models/database.py` 的 `get_conn()` / `init_db()`）
- 前端：既有 base.html 深色主題 + 原生 JS + 輕量圖表（OI 長條可用 CSS/SVG 或既有已引入之圖表庫，不新增外部相依）
- 排程：Windows 工作排程呼叫 `python run_daily.py option`（沿用既有 `run_market` 模式）

## 資料來源（已與使用者確認）

- 台灣期貨交易所（TAIFEX）選擇權盤後每日行情下載（含各履約價的開高低收、漲跌、成交量、**未沖銷契約數（OI）**）。
- 契約：台指選擇權 TXO；到期別涵蓋週選（W1~W5）與月選。
- 每個履約價會有 Call（買權）與 Put（賣權）兩列，欄位含成交價、漲跌、成交量、未平倉。
- 盤後資料約當日下午出，故排程掛在下午盤後。

## 支撐壓力計算方法（納入驗收條件）

- **壓力（Resistance）** = 該契約 **Call 未平倉量最大** 的履約價。
- **支撐（Support）** = 該契約 **Put 未平倉量最大** 的履約價。
- **Max Pain** = 使買賣權「買方總損失最大」（即賣方總獲利最大 / 選擇權總內含價值最小）的履約價。
  - 計算：對每個候選結算價 K，總痛苦值 = Σ_call OI_i × max(K − 履約價_i, 0) + Σ_put OI_j × max(履約價_j − K, 0)，取使總痛苦值最小之 K 為 Max Pain。
- **T 字報價表欄位**：Call（成交價／漲跌／成交量／未平倉）｜履約價｜Put（成交價／漲跌／成交量／未平倉），依履約價排序。

---

## 功能清單

### F-001：TAIFEX 選擇權盤後資料抓取（scraper）
- 描述：抓取指定日期的 TXO 選擇權每日行情（各到期別、各履約價、Call/Put 的價量與 OI），寫入 SQLite。
- 輸入：日期（yyyymmdd，預設當日）。
- 輸出：解析後的逐列資料（契約=TXO、到期別代碼如 `202607W1`/月選、履約價、買賣權別、成交價、漲跌、成交量、未平倉），寫入 `option_daily` 表；回傳寫入筆數。
- 驗收條件：
  - 對一個已收盤交易日呼叫，能抓到當日 TXO 全到期別、全履約價的 Call 與 Put 資料且成功入庫。
  - 非交易日或資料尚未出爐時回傳 0 筆並記錄 log，不拋例外中斷排程（比照既有 scraper 容錯風格）。
  - 重複抓同一日不產生重複資料（以 日期+到期別+履約價+買賣權 為唯一鍵，upsert 或先刪後插）。

### F-002：支撐壓力與 Max Pain 計算（scanner）
- 描述：讀取指定日期＋指定契約（到期別）的 OI 資料，計算壓力、支撐、Max Pain。
- 輸入：日期、契約到期別（如 `202607W1`）。
- 輸出：`{壓力履約價, 支撐履約價, max_pain履約價, 各項對應OI/明細}`。
- 驗收條件：
  - 壓力 = Call OI 最大履約價；支撐 = Put OI 最大履約價；計算結果與該日 OI 明細一致。
  - Max Pain 依上方公式在所有出現的履約價中取總痛苦值最小者，可人工核算比對。
  - 查無資料時回傳明確的空結果結構（`ok: False` + 空欄位），不拋例外（比照 `/api/futures-basis` 錯誤回傳風格）。

### F-003：可選日期（date picker）
- 描述：頁面可選擇查詢日期，預設為資料庫最新有資料日期。
- 輸入：使用者選的日期（URL query `date=yyyy-mm-dd`）。
- 輸出：該日對應的支撐壓力與報價資料。
- 驗收條件：
  - 切換日期後，OI 長條圖與 T 字表都更新為該日資料。
  - 選到無資料日期時，頁面顯示「該日無資料」提示而非報錯白頁。
  - 可選日期範圍限於資料庫實際有資料的交易日（後端提供可選日期清單）。

### F-004：可選契約（週選 W1~W5／月選）
- 描述：頁面可切換到期契約（週選 W1~W5、月選），格式比照 `202607W1`。
- 輸入：契約代碼（URL query `contract=202607W1`）。
- 輸出：該契約的支撐壓力與報價資料。
- 驗收條件：
  - 下拉可列出該查詢日「實際存在資料」的所有到期別（週選＋月選），不列出無資料契約。
  - 切換契約後全頁（支撐/壓力/Max Pain/圖表/T 字表）同步更新。
  - 預設選中最近到期的週選（比照 WantGoo 預設行為）。

### F-005：檢視方式切換（OI 長條圖 ↔ T 字報價表）
- 描述：以按鈕切換兩種檢視，比照 WantGoo。沿用既有 `.fb-btn` / `.fb-btn.active` 樣式風格。
- 輸入：檢視模式（`bars` / `table`）。
- 輸出：
  - **支撐壓力 OI 長條圖**：以履約價為軸，Call OI 與 Put OI 雙向長條；標示壓力（Call OI 最大）、支撐（Put OI 最大）、Max Pain 位置。
  - **T 字報價表**：欄位為 Call（成交價/漲跌/成交量/未平倉）｜履約價｜Put（成交價/漲跌/成交量/未平倉），依履約價排序，並標示壓力/支撐/Max Pain 所在列。
- 驗收條件：
  - 兩種檢視皆呈現同一份（日期＋契約）資料，數字一致。
  - 長條圖能明確看出壓力、支撐履約價；T 字表能對齊同一履約價的 Call 與 Put。
  - 預設檢視為 OI 長條圖（比照 WantGoo）。

### F-006：頁面與 API route（整合進 app.py）
- 描述：新增頁面 route `/option-sr`（`render_template('option_sr.html')`）與資料 route `/api/option-sr`。
- 輸入：`/api/option-sr?date=yyyy-mm-dd&contract=202607W1`。
- 輸出：JSON `{ok, date, contract, available_dates, available_contracts, resistance, support, max_pain, rows:[...]}`，`rows` 為 T 字表逐履約價資料（含 call/put 價量 OI）。
- 驗收條件：
  - route 樣式與既有 `/futures-basis` + `/api/futures-basis` 一致（try/except、錯誤回 500 + `ok:False` 結構）。
  - 未帶參數時採預設（最新日期＋最近週選）。
  - `templates/base.html` 於「電金強弱」後新增一列導覽連結，`request.endpoint` 高亮邏輯比照既有。

### F-007：每日盤後自動更新排程
- 描述：新增 `run_daily.py` 分派參數 `option`，呼叫 `run_option()` 抓當日 TXO 盤後資料入庫；掛 Windows 工作排程於下午盤後執行。
- 輸入：`python run_daily.py option`（可另支援指定日期回補，比照既有 backfill 精神，非必要則從簡）。
- 輸出：當日 TXO 選擇權資料入 `option_daily` 表；log 記錄筆數。
- 驗收條件：
  - `run_daily.py option` 可獨立執行並成功入庫當日資料。
  - 排程時間設定在 TAIFEX 選擇權盤後資料出爐後（下午，建議約 15:00 之後，實際時點於交付時確認）。
  - 執行失敗只記 log 不影響其他既有排程（沿用既有 try/except + logger 風格）。

### F-008：資料表建置（models/database.py）
- 描述：於 `init_db()` 新增 `option_daily` 表與索引，比照既有 `CREATE TABLE IF NOT EXISTS` 慣例。
- 輸入：無（初始化時建立）。
- 輸出：`option_daily(date, contract, strike, cp, close, change, volume, oi, ...)` 表 + 依 `(date, contract)` 的索引。
- 驗收條件：
  - `init_db()` 執行後表存在；重複執行不報錯（`IF NOT EXISTS`）。
  - 唯一鍵 `(date, contract, strike, cp)` 避免重複列。

---

## 非功能需求
- 沿用既有深色主題與版面（extends base.html、class 前綴命名如 `os-*`，比照 `fb-*`）。
- 不新增外部前端相依（CDN / 圖表庫），OI 長條以 CSS/SVG 或既有已引入資源實作。
- 抓取加上 `REQUEST_TIMEOUT` 與 User-Agent，避免拖垮既有站台。
- 遵守 TAIFEX 使用規範，僅取公開盤後資料，禁爬 WantGoo。

## 邊界條件與限制
- 標的僅台指選擇權（TXO）。電子／金融選擇權本次不做（列未來可選）。
- 資料為「盤後每日」等級，非盤中即時。
- 到期別解析需正確對應 TAIFEX 週別代碼與本站契約字串（`202607W1` 等）之映射；月選無 W 後綴，需明確表示法。
- 支撐壓力／Max Pain 皆以「單一契約（到期別）」為計算範圍，非全到期別合計（比照 WantGoo 該頁行為）。

## 待確認事項
1. **契約字串表示法**：週選以 `202607W1` 表示，**月選**在下拉／URL 用什麼字串（例如 `202607`、`202607M`、或直接顯示「月選」）？建議交付時定案，先以「月選＝該月份到期，代碼 `YYYYMM`」為預設。
2. **Max Pain 候選價範圍**：以「當日該契約實際出現的履約價集合」為候選即可，或需在最小～最大履約價間補齊固定間距（50/100 點）？預設用「實際出現的履約價集合」。
3. **排程精確時點**：TAIFEX 選擇權盤後資料實際穩定出爐時間，建議掛約 15:00 後；正式時點於交付時以實抓驗證後定案。
4. **Max Pain 是否需顯示曲線**：WantGoo 主要標示 Max Pain 點位；是否需要在長條圖疊加「各結算價總痛苦值」曲線？預設僅標示點位，不畫痛苦值曲線（避免超出「比照該頁」範圍）。

（以上 4 項皆已給預設值，若使用者無異議可依預設進行，不阻塞開發。）
