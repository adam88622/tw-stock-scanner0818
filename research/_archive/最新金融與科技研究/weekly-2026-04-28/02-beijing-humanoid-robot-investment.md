# 北京人形機器人半馬奪冠 — 投資視角研究報告

- **撰寫日期**：2026-04-28
- **作者**：GiS Genesis International Capital — 量化研究組
- **事件日期**：2026-04-19（賽事），2026-04-22（國際媒體確認週期高峰）
- **主題分類**：人形機器人 / Embodied AI / 台股供應鏈 thematic basket

---

## ⚠️ 實證警告（v1.1 補註，2026-04-28）

> **本報告 v1 假設「籃子 Sharpe 0.8-1.2」已被實證打臉**。yfinance 真實 16 個月（2025-01-02 ~ 2026-04-28）回測：等權籃子 Sharpe **0.55 vs ^TWII 1.79**，累積落後加權指數 **50 個百分點**（籃子 +24% vs TWII +74%）。**Buy & hold 不成立，需改 event-driven**，並刪除 4571 / 1536 / 6121 三檔純度高但無 momentum 的標的。詳見本檔末段「## 實證結果」與 [results/02_empirical_results.md](results/02_empirical_results.md)。

---

## 摘要

2026 年 4 月 19 日，由中國手機品牌 Honor（榮耀）開發的人形機器人「Lightning」於北京亦莊（E-Town）人形機器人半程馬拉松中，以 **50 分 26 秒（自主導航模式）/ 48 分 19 秒（遙控模式）** 完賽 21 公里賽程，**擊敗烏干達跑者 Jacob Kiplimo 在 2026 年 3 月里斯本創下的人類世界紀錄 57 分 20 秒約 7 分鐘**。本屆賽事參賽人形機器人由去年 21 台暴增至 100 台以上，且至少 4 台跑進 1 小時內。此事件並非單純話題，而是 **中國 humanoid 行業從 demo 階段邁向「可長時間連續運作的工程系統」的具體里程碑**——液冷關節、長步幅腿部設計（接近 1 公尺）、自主導航能力同時通過 21K 連續測試，等同於壓力測試級的可靠度驗證。本報告聚焦：(1) 產業意涵；(2) 台股至少 5 檔具體受惠標的；(3) 量化策略可行性與 thematic basket 設計。

---

## 事件詳情與技術突破

| 項目 | 數據 |
|------|------|
| 賽事 | 2026 Beijing E-Town Humanoid Half Marathon |
| 冠軍機器人 | Honor「Lightning」（閃電）|
| 完賽時間（自主） | 50:26 |
| 完賽時間（遙控） | 48:19 |
| 賽程 | 21.0975 km |
| 機器人身高 | 169 cm |
| 腿長 | 約 95 cm（接近 1 公尺長步幅）|
| 散熱 | 液冷關節（技術自手機散熱模組移植）|
| 參賽機器人 | 100+ 台（去年 21 台）|
| Sub-1hr 完賽 | 至少 4 台 |
| 人類世界紀錄 | 57:20（Jacob Kiplimo, 2026/03 Lisbon）|

**技術突破點（quant 視角的工程意義）**：

1. **熱管理**：21K 高負載連續運轉而不熱當機，代表 servo 馬達 + 關節傳動的熱失控風險已可工程化解；這是所有人形機器人量產前最後一道未解之題。
2. **能源密度**：21K 不更換電池意味電池能量密度與 BMS 控制達工業可用門檻——對台廠電池芯與 BMS 廠商是直接訊號。
3. **自主導航**：50:26 的成績是在無遙控干預下完成，含上下坡、轉彎、避障，等同於 SLAM + 步態規劃的完整端到端 stack 已驗收。
4. **生態擴張**：參賽機 100+ 台，意味中國 humanoid OEM 至少有 100 個團隊有「跑得起來」的完整品。Digitimes 報導中國已達 140 家 humanoid OEM、330 個機型——**生態過熱訊號 vs. 規模經濟訊號並存**，需謹慎評估。

---

## 產業意涵

### 中國人形機器人量產時程比較

| 廠商 | 2025 出貨 | 2026 目標 | 量產地位 |
|------|----------|----------|---------|
| Unitree（宇樹）| ~5,500 台 | **20,000 台**（4 倍增）| 全球單機出貨王，已申請上海 IPO（610M USD）|
| UBTech（優必選）| 首批商用交付完成 | 規模化量產 | 工廠端應用領先 |
| Honor（榮耀）| Lightning Demo | 場景化部署 | 本次賽事勝出，技術形象第一 |
| Leju（樂聚）| — | 募資 200M USD 擴產 | 中端市場 |
| Boston Dynamics（Atlas 電動版）| 試產 | 全數交付 Hyundai + Google DeepMind | 不對外銷售 |
| Tesla Optimus | 內測 | Gen 3 量產延後至 2026 中 | 特斯拉自家工廠驗證 |

Morgan Stanley 預測 2026 中國人形機器人銷售 **+133% YoY 至 28,000 台**。**這代表台廠關鍵零組件需求 2026 進入第一個指數爆發點**。

### 價值鏈拆解（含台廠卡位）

```
人形機器人 BOM
├── 精密機械（30-35% BOM）
│   ├── 諧波減速機 ........... 上銀 2049、大銀微 4576、鈞興 4571
│   ├── 滾珠螺桿 ............. 上銀 2049
│   ├── RV 減速機/齒輪 ....... 和大 1536、宇隆 2233
│   └── 軸承 ................. （日系主導，台廠次要）
├── 伺服與動力（15-20%）
│   ├── 伺服馬達 ............. 台達電 2308
│   └── 電源/變頻 ............ 台達電 2308
├── 感測 + 視覺（10-15%）
│   ├── 力感測 ............... 鴻海集團（內部設計）
│   └── 視覺/SLAM 攝影機 ..... 鴻海 2317、廣達 2382
├── 電池與電源（10-12%）
│   ├── 電池芯/模組 .......... 新普 6121、加百裕 3323
│   └── 電源管理 ............. 台達電 2308
├── 算力與 AI（15-20%）
│   ├── 邊緣 AI 主機板 ....... 廣達 2382、緯創 3231、研華 2395
│   └── ASIC/GPU 整合 ........ 鴻海 2317（NVIDIA Project GR00T 合作）
└── 整機組裝（5-10%）
    └── ODM/OEM .............. 鴻海 2317、廣達 2382、和碩 4938
```

---

## 台股供應鏈標的（核心持股池，至少 5 檔）

| # | 代號 | 公司 | 切入點 | 量化評等 |
|---|------|------|-------|---------|
| 1 | **2049** | 上銀科技 | 諧波減速機 + 滾珠螺桿，已入列鴻海 humanoid 大關節模組（肩、髖）；旗下大銀微系統（4576）做精密微型減速 | 核心 Tier-1，beta 高 |
| 2 | **2308** | 台達電 | 伺服馬達 + 電源管理 + 散熱解決方案（Lightning 液冷靈感與此同源），通吃多家中國/美國 humanoid OEM | 防禦性 Tier-1 |
| 3 | **2317** | 鴻海 | 整機組裝 + 自家 humanoid「FoxBrain + 機器人」計畫 2026 年底量產；NVIDIA GR00T 合作；自有電動車工廠驗證場 | 整機受惠龍頭 |
| 4 | **1536** | 和大 | RV 減速機與精密齒輪，工業機器人與 humanoid 雙重受惠 | Tier-2，營運槓桿大 |
| 5 | **2382** | 廣達 | Quanta Cloud Tech × Techman Robot × NVIDIA 合作 humanoid TM Xplore I，2026 H2 規模化 | AI 算力 + 整機合一 |
| 6 | **4571** | 鈞興-KY | 諧波減速機，純度高的小而美標的 | 高 beta thematic |
| 7 | **6121** | 新普 | 電池模組，humanoid 高能量密度電池整合者 | Tier-2 |
| 8 | **2233** | 宇隆 | 精密傳動齒輪，營收已透露 humanoid 訂單 | 高彈性中小型 |

> 補充觀察名單：4576 大銀微（微型減速）、2395 研華（邊緣 AI）、3231 緯創（ODM）、4938 和碩。

---

## 量化交易切入點

### Thematic Basket：Taiwan Humanoid Robot Supply Chain Index（THR-Index）

採 **價值鏈分層 × 純度加權** 設計：

```
權重 = 價值鏈核心度（0.4）+ 營收敞口純度（0.4）+ 流動性（0.2）
```

- **核心度**：依 BOM 成本佔比（精密機械 35% > AI/算力 20% > 電池 12% ...）
- **純度**：humanoid 訂單在公司營收佔比（鈞興、大銀微純度 > 鴻海、廣達）
- **流動性**：日均成交額 $30M USD 以上才入選，避免 small-cap 滑價

### 可建構因子（factor candidates）

1. **Humanoid Mention Momentum**：法說會逐字稿、年報、月營收公告中 humanoid / 人形機器人 / 諧波 / 減速 詞頻變化率（NLP 因子，月頻）。
2. **Order Book Lead Indicator**：上銀月營收 YoY × 鴻海 humanoid pipeline 公告事件 → 領先 Tier-2/3 標的 1-2 個月。
3. **China Humanoid Shipment Beta**：以 Unitree、UBTech 月度出貨估計（券商研究）為 driver，回歸台股 basket 的 rolling beta，挑高 beta 標的做 momentum overlay。
4. **Event-Driven Reversal**：每次中國 humanoid 重大 demo 事件（賽事、發表會、IPO、政策）→ basket 短線過熱 → mean reversion 5-10 日。
5. **Cross-Asset Hedging**：basket long 配 NVDA / TSLA / Boston Dynamics 母公司 Hyundai 美股 short，做 China-Taiwan vs. US 相對價值。

### 訊號頻率與週期建議

| 策略類型 | 頻率 | 預期 Sharpe |
|---------|------|------------|
| Thematic basket buy & hold + 季度 rebalance | 月/季 | 0.8-1.2（beta-driven）|
| Event-driven momentum | 日 | 1.0-1.5（事件密度依賴）|
| NLP 詞頻因子 | 月 | 0.6-0.9 |
| Cross-asset RV | 週 | 0.7-1.0 |

---

## 可行性評估

### 能否做成 systematic strategy？

**結論：可以，但屬於 thematic + factor overlay 混合，不適合純 statistical arb。**

理由：
- **正面**：題材長尾（5-10 年產業 S 曲線初段）、台股供應鏈具體可量化、出貨數據有公開 proxy（上銀月營收、Unitree IPO 後將揭露季度數據）。
- **挑戰**：
  1. 樣本長度短——humanoid 是 2024 後才有意義的 dataset，無法做 20 年 backtest。
  2. 事件叢集化——demo / 發表會 / 政策會集中在科技展期間，存在 regime 切換。
  3. 純度標的流動性受限——鈞興、宇隆日均 $10-30M USD，承載資金 < $50M。

### 資料來源

| 類別 | 來源 | 頻率 | 取得方式 |
|------|------|------|---------|
| 台股價量 | TWSE / TPEx / yfinance | 日 | 已建置 |
| 月營收 | 公開資訊觀測站（mops.twse.com.tw）| 月 | 既有 scanner 可擴充 |
| 法說會逐字稿 | 公司 IR 網站 + Bloomberg transcript | 季 | 需 scraper |
| 中國 humanoid 出貨 | Morgan Stanley / Digitimes / 36Kr | 不定期 | 訂閱 |
| 國際對標 | 美股 NVDA、TSLA、SYM、KSCP；港股 UBT | 日 | yfinance |

---

## 結論與監控指標

### 投資主軸

**Lightning 的 50:26 不是話題，是 humanoid 從「能站」走到「能用」的工程拐點。** 中國 OEM 由 21 → 100 家規模化參賽，意味 2026 H2 至 2027 將是台股精密機械 + 算力 + 電池供應鏈的訂單兌現年。建議以 **THR-Index 核心 8 檔 basket** 為底倉，搭配 Honor / Unitree / UBTech 重大事件做動態加碼。

### 五大監控指標（dashboard 化）

1. **上銀（2049）月營收 YoY**：> +20% 連續兩個月 → basket overweight 訊號。
2. **Unitree IPO 後季度出貨數據**：實際 vs. 20,000 台目標達成率。
3. **台達電 法說會 humanoid 提及次數**：NLP 詞頻因子。
4. **NVIDIA Project GR00T 合作名單變動**：每新增一家台廠，basket 加碼。
5. **中國 humanoid 政策補貼公告**：北京、上海、深圳地方政府月度追蹤。

### 風險提示

- **過熱回檔**：basket 已年初至今上漲 35-50%，部分標的 P/E > 50x。
- **量產不及預期**：Tesla Optimus、鴻海 humanoid 多次延期前例。
- **地緣風險**：若中美科技戰升級限制中國 humanoid AI 晶片，台廠雙邊接單存風險。

---

## 引用來源

> 等級：Reuters / Fox News / CGTN / Fortune / NBC News / NPR / 經濟日報 / 鉅亨網 / Digitimes（皆 B+ 以上財經與產業媒體）

1. **Fortune**, "Humanoid robot runs faster than any person in a half marathon during all-bot race in China", 2026-04-19. URL: https://fortune.com/2026/04/19/humanoid-robot-world-record-half-marathon-race-china-honor/ — Web 取用 2026-04-28
2. **CGTN**, "48:19?! Humanoid 'Lightning' beats human world record", 2026-04-19. URL: https://news.cgtn.com/news/2026-04-19/48-19-Humanoid-Lightning-beats-human-world-record-1MsQrzvBe7K/p.html — Web
3. **Fox News**, "Chinese humanoid robot beats half-marathon world record in Beijing", 2026-04. URL: https://www.foxnews.com/tech/chinese-robot-breaks-human-world-record-beijing-half-marathon — Web
4. **NBC News**, "Robot breaks human half-marathon world record in China race", 2026-04. URL: https://www.nbcnews.com/world/china/humanoid-robots-race-humans-beijing-half-marathon-showing-rapid-advanc-rcna340842 — Web
5. **NPR**, "A humanoid robot sprints past the human half-marathon world record in Beijing race", 2026-04-20. URL: https://www.npr.org/2026/04/20/g-s1-118086/humanoid-robot-half-marathon — Web
6. **CBS News**, "Humanoid robot beats human half-marathon world record in Beijing", 2026-04. URL: https://www.cbsnews.com/news/humanoid-robot-half-marathon-beijing-human-world-record/ — Web
7. **Global Times**, "Humanoid robot breaks human half-marathon record; Drastic improvement reflects systemic advances in China's robot technologies", 2026-04. URL: https://www.globaltimes.cn/page/202604/1359229.shtml — Web
8. **Humanoid Press Database**, "Lightning — High-Speed Humanoid Robot by Honor", 2026-04. URL: https://humanoid.press/database/humanoid-press-database-honor-lightning/ — Web
9. **The Robot Report**, "Leju raises $200M for humanoid production as Unitree unveils H2", 2026-Q1. URL: https://www.therobotreport.com/leju-raises-200m-humanoid-production-unitree-unveils-h2-robot/ — Web
10. **Rest of World**, "China robot maker Unitree files for $610 million Shanghai IPO", 2026. URL: https://restofworld.org/2026/unitree-china-humanoid-robot-shanghai-ipo/ — Web
11. **eWeek**, "China's Unitree Aims to Ship 20,000 Humanoid Robots in 2026", 2026-02. URL: https://www.eweek.com/news/unitree-20000-humanoid-robots-2026-china/ — Web
12. **Digitimes**, "China hits 140 humanoid robot OEMs, 330 models; supply chain ramps", 2026-03-02. URL: https://www.digitimes.com/news/a20260302PD235/robot-2025-supply-chain-production-expansion.html — Web（付費訂閱摘要）
13. **Digitimes**, "China leads the humanoid robot supply chain, with 2026 sales forecast to jump", 2026-02-03. URL: https://www.digitimes.com/news/a20260203PD235/robot-2026-sales-supply-chain-market.html — Web
14. **Digitimes**, "Taiwan's humanoid robot race heats up beyond Foxconn", 2025-11-17. URL: https://www.digitimes.com/news/a20251117PD220/robot-hiwin-technologies-component-taiwan-supply-chain.html — Web
15. **Taiwan News**, "Taiwan's Techman unveils new humanoid robot at Nvidia GTC", 2026-03-18. URL: https://www.taiwannews.com.tw/news/6323007 — Web
16. **經濟日報（UDN Money）**, "人形機器人要動了 上銀、和大、宇隆等準備出貨", 2026. URL: https://money.udn.com/money/story/11162/9265985 — Web
17. **鉅亨網（cnyes）**, "AI機器人從題材變現實：上銀（2049）、大銀微（4576）、鈞興-KY（4571）搶先布局人形機器人核心供應鏈", 2026. URL: https://news.cnyes.com/news/id/6039509 — Web
18. **Morgan Stanley Research**（透過 Digitimes 引用）, "China humanoid robot 2026 sales forecast +133% YoY to 28,000 units" — 二手引用
19. **iRunFar**, "Human Half-Marathon World Record Zapped by Humanoid Robot at the 2026 Beijing E-Town Half Marathon", 2026-04. URL: https://www.irunfar.com/2026-beijing-e-town-half-marathon-humanoid-robot-beats-human-world-record — Web
20. **Scientific American**, "A humanoid robot beat the human half-marathon record at a Beijing race. But what did it actually prove?", 2026-04. URL: https://www.scientificamerican.com/article/a-humanoid-robot-beat-the-human-half-marathon-record-at-a-beijing-race-but-what-did-it-actually-prove/ — Web

---

## 實證結果（empirical, 2026-04-28）

> 本節以 yfinance 真實股價（2025-01-02 ~ 2026-04-28，315 交易日）回測本報告 8 檔 basket。完整程式與資料：`results/02_empirical.py`、`results/02_empirical_results.md`、`results/02_empirical_results.json`。

### 籃子 vs ^TWII

| 籃子 | 16 個月累積報酬 | 超額 vs TWII | Sharpe (rf=2%) | Beta | Max DD |
|---|---:|---:|---:|---:|---:|
| ^TWII（基準） | **+74.25%** | — | **1.79** | 1.00 | -26.7% |
| Equal-weight 8 檔 | +23.76% | **-50.49pp** | 0.63 | 1.08 | -40.1% |
| Designed-weight 8 檔 | +20.08% | **-54.17pp** | 0.55 | 1.08 | -41.3% |

### 個股 1Y 報酬（亮點與拖油瓶）

- **領頭**：2308 台達電 +535%（AI 電源/液冷再評等）、2317 鴻海 +67%、2233 宇隆 +56%、2049 上銀 +47%。
- **拖累**：1536 和大 -15%、4571 鈞興 +13%（年化 vol 47%，Sharpe -0.54）、6121 新普 +10%。
- **離散度極大**：1Y 報酬從 -15% 至 +535%，等權 / 純度加權都被低端標的拖累。

### 事件研究：2026-04-19 北京半馬（T0=2026-04-17）±5 日累積異常報酬（CAR vs ^TWII）

| | 2049 | 2308 | 2317 | 1536 | 4571 | 2382 | 6121 | 2233 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 | +10.5% | +1.5% | -1.6% | -0.2% | -1.3% | -5.8% | -6.0% | +3.9% |
| **T0** | **+16.3%** | +2.1% | -1.2% | +1.8% | -0.0% | -3.5% | -5.1% | +4.8% |
| T+3 | **+22.2%** | +9.2% | +3.3% | -0.3% | -1.8% | -2.8% | -1.0% | **+15.4%** |
| T+5 | +11.4% | +9.7% | +0.6% | -5.5% | +1.4% | -9.5% | -4.8% | +3.5% |

**事件 alpha 集中於 2049、2233、2308**（核心諧波減速 + 純度標的 + 動力散熱龍頭），但 2382、6121、1536 在 ±5 日呈 **負異常報酬**。事件驅動策略應限於 2049 / 2233 / 2308 三檔。

### Correlation 對角化

平均對角外相關 = **0.484**。AI 算力雙塔 2317-2382 相關 **0.75**（重複性高，建議擇一）；精密機械三檔 2049-1536-4571 相關 0.50-0.60；2308 與其他 7 檔相關 0.38-0.62 為**最佳分散貢獻者**。有效成份股數 ≈ 1.85，遠低於名目 8 檔。

### 對原 MD「Sharpe 0.8-1.2 預期」的修正

| 原 MD 假設 | 實測 | 判定 |
|---|---|---|
| Thematic basket Sharpe 0.8-1.2 | 0.55-0.63 | **不成立**（僅 ^TWII 一半） |
| 純度加權應優於等權 | 純度 -54pp 比等權 -50pp 還差 | **反向** |
| Max DD 可控 | -41% (vs TWII -27%) | **不成立** |
| 事件 alpha 顯著 | 集中於 2049/2233/2308 | 部分成立 |

### 修正建議（後續策略迭代）

1. **加 momentum filter**：6M ROC < 0 強制剃除（本期會剔除 1536 / 4571 / 6121）；
2. **集中至 5 檔**：2308 / 2049 / 2317 / 2382 / 2233；
3. **降純度權重至 0.20**，提高核心度權重；
4. **事件驅動 overlay**：humanoid 重大事件 ±3 日 long 2049 / 2233；
5. **風險管理**：個股 -15% stop loss 或 vol-targeting，避免 -41% drawdown 重演。

### 資料品質

- 8 檔全數成功取得（4571、2233 fallback 至 .TW，6121 用 .TWO）；無缺失。
- 2308 +535% 已交叉驗證 yfinance OHLC 原始資料（2025-01-02 收盤 410 → 2026-04-28 收盤 2125）為真實價格。

---

*本報告為 GiS 內部研究文件，僅供量化研究組策略開發使用，不構成任何投資建議。*
