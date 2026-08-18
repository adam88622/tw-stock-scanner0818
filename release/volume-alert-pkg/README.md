# 盤中爆量預估 Volume Alert（台股）

每 5 分鐘掃描全市場（上市+上櫃），用 J 型成交量曲線估算「收盤前預估量 vs ADV20」，依倍率分級燈號，幫助你抓到正在爆量的標的。包含一個簡單的網頁看板（`http://127.0.0.1:5000/volume-alert`）。

---

## 系統需求

| 項目 | 需求 |
|---|---|
| 作業系統 | Windows 10 / 11 |
| Python | 3.10 以上（安裝時請勾選 **Add Python to PATH**） |
| 網路 | 可連 `mis.twse.com.tw` / `www.twse.com.tw` / `www.tpex.org.tw` |
| 磁碟 | 500 MB（含 60 個交易日歷史資料） |
| 權限 | 建立工作排程需要一般使用者權限即可 |

> 沒裝 Python？至 https://www.python.org/downloads/ 下載 3.12，**安裝時勾選 “Add python.exe to PATH”**。

---

## 一鍵安裝

1. 把這個資料夾整包解壓到任意位置（路徑請**避免中文與空白**，例如：`C:\tools\volume-alert\`）。
2. **點兩下 `install.bat`**。會自動：
   1. 建立 `.venv` 虛擬環境
   2. 安裝相依套件（Flask、requests）
   3. 初始化 SQLite 資料庫（`db/volume_alert.db`）
   4. 抓最近 60 個交易日歷史 OHLCV（約 **5–10 分鐘**，請耐心等）
   5. 註冊 4 個 Windows 工作排程
3. 跑完後**點兩下 `start.bat`** → 自動開啟瀏覽器到 `http://127.0.0.1:5000/volume-alert`。

如果只是想關掉系統：執行 `stop.bat`。

---

## 工作排程（自動運行）

`install.bat` 會註冊四個排程到 Windows 工作排程器（搜尋 `VolumeAlert`）：

| 排程名稱 | 時間 | 動作 |
|---|---|---|
| `VolumeAlert-Healthcheck` | 平日 08:55 | 盤前健檢（DB / MIS 報價源 / scanner） |
| `VolumeAlert-Start` | 平日 09:00 | 啟動 `volume_alert_worker.py`（開始掃描） |
| `VolumeAlert-Stop` | 平日 13:30 | 安全網結束 worker（worker 本身也會自動退出） |
| `VolumeAlert-AutoStart` | 使用者登入時 | 啟動 watchdog（守 Flask 一直在跑） |

> 想關閉自動化：執行 `uninstall_schedule.ps1`（PowerShell 右鍵 → 以 PowerShell 執行）。

---

## 資料來源說明（給好奇的人）

| 用途 | 來源 | 端點 |
|---|---|---|
| 上市每日 OHLCV | TWSE 公開 API | `https://www.twse.com.tw/exchangeReport/MI_INDEX` |
| 上櫃每日 OHLCV | TPEx 公開 API | `https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php` |
| 盤中即時報價 | TWSE MIS（公開） | `https://mis.twse.com.tw/stock/api/getStockInfo.jsp` |

全部都是免費公開的 endpoint，**無需任何 API key**。腳本內已 mimic 瀏覽器 User-Agent、Referer，並有 3 次重試 + 指數 backoff，避免被擋。

### 為什麼要先抓 60 天歷史？
爆量是「相對 ADV20（前 20 個交易日均量）」的判斷。沒有歷史資料就算不出基準，新裝完一定要跑一次 `seed_data.py`。`install.bat` 已幫你做了，後續每天 worker 在盤中會把當日資料寫進 `intraday_snapshot` / `daily_prices`。

---

## 看板畫面說明

| 燈號 | 意義 |
|---|---|
| 🔴 EXTREME | 預估 EOD 量 / ADV20 ≥ 3.0 |
| 🟠 HIGH | 倍率 2.0–3.0 |
| 🟡 ELEVATED | 倍率 1.5–2.0 |
| 🟢 NORMAL | 倍率 < 1.5（看板不會出現，被過濾） |

頁面右上會顯示加權指數 RVOL 預估（含 90% CI），可用來判斷大盤是縮量還是放量。

---

## 檔案結構

```
volume-alert-pkg/
├── install.bat                     ← 點兩下安裝
├── start.bat                       ← 點兩下啟動
├── stop.bat                        ← 點兩下關閉
├── install_schedule.ps1            ← 註冊工作排程（install.bat 會呼叫）
├── uninstall_schedule.ps1          ← 移除工作排程
├── seed_data.py                    ← 抓歷史 OHLCV（install.bat 會呼叫）
├── app.py                          ← Flask 看板服務
├── volume_alert_worker.py          ← 每 5 分鐘掃描；13:30 自動結束
├── health_check_volume_alert.py    ← 08:55 排程觸發
├── stop_volume_alert.py            ← 13:30 排程觸發（safety net）
├── watchdog.py                     ← 守護 Flask 一直在
├── config.py
├── requirements.txt
├── models/database.py              ← SQLite schema
├── scanners/volume_anomaly.py      ← 爆量演算法核心
├── scrapers/realtime.py            ← TWSE MIS 抓即時報價
├── scrapers/seed.py                ← 歷史 OHLCV 抓取
├── templates/                      ← 網頁模板
├── static/                         ← 靜態資源
├── db/volume_alert.db              ← 安裝後自動建立
└── log/                            ← 各 process 的 log
```

---

## 疑難排解

### `install.bat` 卡在 seed 階段
TWSE/TPEx 偶爾限流。中斷後重跑：
```
.venv\Scripts\python.exe seed_data.py 60
```

### 看板顯示「目前沒有資料」
- 確認當下是平日 09:00–13:30 盤中時段。
- 看 `log/volume_alert_worker.log`，最新一行應該每 5 分鐘有一筆 `cache 更新`。
- 若 worker 沒在跑：執行 `start.bat`。

### 8:55 健檢失敗怎麼辦
看 `log/health_check_volume_alert.log`：
- `MIS 連線失敗` → 通常網路問題或 TWSE 維護中。
- `volume_anomaly_cache 表不存在` → 重跑一次 `install.bat`。

### 想換 port（5000 被佔用）
修改 `app.py` 最後一行 `port=5000` 與 `watchdog.py` 的 `PORT = 5000`。

### 想完全移除
1. `stop.bat`
2. `uninstall_schedule.ps1`
3. 刪除整個資料夾

---

## 授權與聲明

本工具為內部研究用途，不保證數據準確性。**所有投資決策請自行負責**。
資料來自 TWSE / TPEx 公開 API，使用時請遵守對方服務條款（節制請求頻率）。
