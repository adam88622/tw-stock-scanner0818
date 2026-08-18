# option-sr — 選擇權支撐壓力（交付總結）

## 專案描述

在既有的 `tw-stock-scanner`（Flask 選股/分析站）中，新增一頁「台指選擇權支撐壓力」，功能比照玩股網(WantGoo)的同名頁面，但**不爬 WantGoo**——直接抓台灣期貨交易所(TAIFEX)公開的 TXO 選擇權盤後每日行情＋未平倉(OI)資料，自己計算：

- **壓力** = Call 未平倉量最大的履約價
- **支撐** = Put 未平倉量最大的履約價
- **Max Pain（最大痛苦點）** = 使買賣權買方總回收價值最小的履約價
- **Put/Call OI 比**

頁面提供三組可選項（比照 WantGoo）：可選**日期**、可選**契約**（週選 W/F 系列＋月選，動態由當日資料產生）、**檢視切換**（OI 長條圖 ↔ T字報價表）。資料每日盤後 15:30 自動更新。

## 整合落點（沿用既有慣例，非新專案）

| 檔案 | 異動 | 用途 |
|------|------|------|
| `scrapers/taifex_option.py` | 新增 | 抓 TAIFEX TXO 盤後行情+OI，Big5 解碼、只取「一般」時段 |
| `scanners/option_sr.py` | 新增 | 計算支撐/壓力/Max Pain，組 API 資料結構 |
| `models/database.py` | 新增 | `option_daily` 表 + 5 個查詢/寫入 helper（未動既有） |
| `app.py` | 新增 | `/option-sr` 頁 + `/api/option-sr`（比照 `/futures-basis`） |
| `templates/option_sr.html` | 新增 | 下拉選單 + OI 長條圖 + T字報價表（`os-*` class） |
| `templates/base.html` | 新增 | 導覽連結「選擇權支撐壓力」（接在「電金強弱」後） |
| `run_daily.py` | 新增 | `run_option(date_str)` + `option` 分派（支援指定日回補） |

## 資料來源

- 端點：`GET https://www.taifex.com.tw/cht/3/optDataDown`
- 參數：`down_type=1 & commodity_id=TXO & queryStartDate=queryEndDate=YYYY/MM/DD`
- 回應：Big5 CSV，只取「一般」交易時段列（盤後列 OI 為空，必須濾除，否則覆蓋真實 OI）
- 免費、合法、穩定；不依賴 WantGoo

## 如何執行 / 操作

**正式站已上線**（無需手動啟動）：
- 開啟 `http://127.0.0.1:5000/option-sr`（或對外 ngrok 網址），從導覽列「選擇權支撐壓力」進入
- 上方切換日期、契約；按鈕切換「OI 長條圖 / T字報價表」

**手動抓資料/回補**（Git Bash）：
```bash
cd /d/claude/tw-stock-scanner
# 抓今日
"C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe" run_daily.py option
# 指定日回補
"C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe" run_daily.py option 20260630
```

## 每日自動更新

- Windows 工作排程器新增工作 **`TW-Stock-Option-1530`**：每日 15:30 執行 `run_daily.py option`（與既有 Daily-14 / Market-17 / Institutional-18 / Broker-20 同機制）
- TAIFEX 選擇權盤後資料約當日下午出，15:30 抓取

## 測試結果（Phase 6）

- 環境：uv 建 `.venv`（CPython 3.11.15），依賴裝妥；全程用臨時 DB，**正式庫未被測試污染**
- 靜態：5 個異動檔 py_compile 全 PASS
- Function 單元測試：**34/34 PASS**
  - 抓取：`fetch_txo_daily('20260630')` 回 3352 筆（Call/Put 各 1676），非零 OI 1775，唯一鍵零重複（證明盤後已濾），Big5 無亂碼
  - 計算：resistance/support 與 DB 原始查詢交叉核對一致；Max Pain fixture 手算精確吻合；tie-break 取較低履約價；缺單側不 KeyError；查無資料回 ok=False 不崩
  - 排程：`run_daily.py option 20260630` 入庫成功、exit 0
- API 端到端：**5/5 PASS**（`/option-sr` 200、`/api/option-sr` 預設/指定日/邊界皆正確，邊界日期回 ok=False 不 500）
- 前後端契約對照：樣板用到的每個 JSON 欄位皆由 API 供給且結構一致

## 上線動作

- 正式庫 `db/scanner.db` 已建立 `option_daily` 表 + 2 索引
- 已灌入近交易日真實資料（6/30、7/1、7/2）
- Flask 伺服器已重啟（watchdog 以新程式拉回 PID），`/option-sr` 與 `/api/option-sr` 正式站 HTTP 200 上線
- 未影響其他服務（broker 排程、realtime worker、ngrok、其他 bot 皆保留）

## 注意事項

- 標的僅台指選擇權(TXO)；電子/金融選擇權為未來可選，本次未納入
- 「指數/期貨現價」為樣板選配欄位，TAIFEX 選擇權檔不含，缺值時前端優雅降級為 `--`，非缺陷
- 週選契約不只 W1~W5，另有週五到期的 F 系列；契約下拉一律由當日實際資料動態產生，未寫死
- 非交易日抓取回空清單、不報錯
