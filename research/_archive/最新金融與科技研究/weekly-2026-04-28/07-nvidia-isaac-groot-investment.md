# NVIDIA Isaac GR00T 投資研究筆記

**研究日期**：2026-04-28
**研究單位**：GiS Genesis International Capital — 量化研究部
**主題**：NVIDIA Isaac GR00T 開源人形機器人基礎模型與供應鏈投資切入點
**分類**：Physical AI / Humanoid Robotics / NVIDIA 生態
**對照**：本篇與 `02-humanoid-robot` 互補，02 偏中國產業鏈 (Unitree/AgiBot) 與本體製造，本篇偏 NVIDIA 生態系 (運算 + 模型 + 模擬)。

---

## ⚠️ 實證警告（v1.1 補註，2026-04-28）

> **三項假設被實證打臉**：① **NVDA-β 過濾器失效** — 13 檔僅 NVDA + SERV 通過 β>0.6 + R²>0.3，台股 β 多在 -0.24~+0.20；② **真實 portfolio correlation 與 02 籃為 0.693**（非票面 Jaccard 0.21），對沖效果差；③ **GTC 2026「買謠言賣事實」** — 事件當日 +1.95%，±10D CAR -7.5%，策略應前移 10D 進場、事件當日減碼。故事 vs 績效背離：原列 ★★★★★ 的研華 1Y +8.7%、所羅門 -15.3%；真贏家是台達電 +453%、宜鼎 +436%、Advantest +379%。詳見 [results/07_empirical_results.md](results/07_empirical_results.md)。

---

## 摘要

NVIDIA 已將 Isaac GR00T 從 GTC 2024 的 Project GR00T 願景推進到 GTC 2026 的 **N1.7 商用授權** 階段。GR00T N1 是全球首個**開源、可客製化** 的人形機器人 VLA (Vision-Language-Action) 基礎模型，採用「System 1 反射動作 + System 2 慢思考推理」雙系統架構，搭配 Isaac Sim 合成資料管線（780,000 條合成軌跡 / 6,500 小時 / 11 小時生成）將真實資料 + 合成資料訓練績效提升 40%。GTC 2026 黃仁勳 keynote 中以 1X 機器人現場示範 GR00T N1 後訓練政策完成居家整理任務，並宣布 1X、Agility、Apptronik、Boston Dynamics、Figure、Fourier、Sanctuary、Unitree、XPENG、AGIBOT、LG、NEURA 等 12 家以上 OEM 採用。對照硬體面：**Boston Dynamics Atlas 規劃 2028 年達 30,000 台/年**（Hyundai Metaplant），**Unitree 2026 目標 20,000 台**，全球人形機器人出貨進入「萬台級」拐點。NVIDIA Robotics & Auto 部門 FY2026 營收目標 50 億美元，是繼資料中心後最大新成長軸。本篇從 NVIDIA 生態切入，鎖定 **Jetson Thor 模組、邊緣 AI 伺服器、感測整合** 三條台股供應鏈，列 6 檔代號並提供量化籃子建構建議。

---

## GR00T 技術概要

### 模型架構（VLA, Vision-Language-Action）
- **System 2（慢思考）**：基於 VLM 處理多模態輸入（語言指令 + 影像 + 機器人本體狀態），生成任務計畫。
- **System 1（快反射）**：Diffusion-Transformer-based action policy，將 System 2 的計畫轉成關節級連續動作，控制頻率高（毫秒級）。
- **多步驟自然語言任務**：能解讀「把藍色杯子從桌上拿到水槽並轉手」這類含長上下文 + 雙臂協作 + 物件移交的指令。

### Isaac Sim + Cosmos 預訓練資料管線
- **Isaac GR00T Blueprint**：合成資料生成藍圖，將少量真實人類示範 → 大規模 trajectory。
- **Newton physics engine**：與 Google DeepMind、Disney Research 合作的開源物理引擎，專為機器人 sim2real 設計。
- **EgoScale**：N1.7 加入 20,000 小時第一人稱影片預訓練，強化跨形態泛化與語言遵循。
- **資料倍率**：6,500 小時合成示範 = 9 個月人類連續操作，11 小時即可產生。

### 釋出時程
| 版本 | 時點 | 重點 |
|---|---|---|
| Project GR00T 願景 | GTC 2024 (3/2024) | 概念發表 |
| GR00T N1 開源 | 2025-03 (GTC 2025) | 全球首個開源人形 FM |
| GR00T N1.5 / N1.7 | 2026-03 (GTC 2026) | 商用授權 + EgoScale + 手術機器人變體 |

---

## 產業生態（誰在用？）

| OEM | 國家 | 採用狀態 | 2026 出貨指引 |
|---|---|---|---|
| **1X Technologies** | 挪威/美國 | GTC 2026 keynote demo（GR00T N1 後訓練） | NEO Beta 商用化中 |
| **Boston Dynamics Atlas** | 美國（Hyundai 持有） | 已採 NVIDIA AI 平台；Hyundai RMAC + Google DeepMind 部署 | 2026 全部產能售罄；2028 年 30,000 台/年 |
| **Agility Robotics Digit** | 美國 | NVIDIA 平台合作 | 倉儲部署擴張 |
| **Figure AI** | 美國 | NVIDIA 平台合作 | Figure 03 量產化 |
| **Apptronik Apollo** | 美國 | NVIDIA 平台合作 | Mercedes-Benz 工廠試點 |
| **Unitree** | 中國 | NVIDIA 平台合作 | **2026 目標 20,000 台**（2025 約 5,500 台） |
| **AgiBot / XPENG / Fourier / Sanctuary / LG / NEURA** | 多國 | 採用 GR00T N1.7 | 中國 OEM 合計約佔全球 80% 出貨 |
| **Techman + QCT (台達/廣達)** | 台灣 | TM Xplore I 於 GTC 2026 發表，Jetson Thor 驅動 | 工業場景 |

關鍵觀察：**Atlas 2026 全部產能已售罄**（已配給 Hyundai + DeepMind），代表 B2B 端需求遠超供給，瓶頸在製造端而非需求端 → 供應鏈訂單能見度高。

---

## 台股供應鏈（NVIDIA 生態切入點）

**與 02 篇區別**：02 篇 (humanoid_robot) 偏中國本體鏈（諧波減速器、無框馬達、絲槓）。本篇鎖定 **NVIDIA 平台依賴** 的零組件 — Jetson Thor 模組整合商、邊緣 AI 伺服器代工、感測 + 視覺方案商。

| 代號 | 名稱 | 在 GR00T 生態中的角色 | 邏輯強度 |
|---|---|---|---|
| **2395** | **研華 Advantech** | NVIDIA 點名 Jetson Thor 合作夥伴，MIC-743 整合系統、MIC-742 開發套件已上市 | ★★★★★ 直接受惠 |
| **5289** | **宜鼎 Innodisk** | NVIDIA 點名嵌入式儲存合作夥伴（工業級 SSD/DRAM 模組於 Jetson 平台） | ★★★★ |
| **2382** | **廣達 Quanta (QCT)** | 與 Techman 共同開發 TM Xplore I 人形平台；DGX/HGX 伺服器主代工 | ★★★★★ 伺服器側 |
| **2359** | **所羅門 Solomon** | 3D 視覺 + AI 物件辨識方案，供 NVIDIA Isaac 生態整合 | ★★★★ |
| **6245** | **立端 Lanner** | 邊緣 AI 運算主板與機器人 controller | ★★★ |
| **8234** | **新漢 NEXCOM** | Taiwan Robotics Show 已展出 Jetson Thor 整合產品 | ★★★ |
| **2308** | **台達電 Delta** | 機器人控制器、伺服馬達、電源；Newton 物理引擎合作含 Disney 但 Delta 為馬達側鏈 | ★★★ 跨界 |
| **2347** | **聯強 Synnex** | Jetson Thor 兩岸代理通路 | ★★（通路型，beta 較低） |

**美股錨點**：NVDA（核心受惠）、 Symbotic、Serve Robotics（B2B 部署）。
**日股錨點**：6857 Advantest（測試）、6981 村田（感測元件）、6594 Nidec（馬達）。

---

## 量化切入點

### 1. GR00T-thematic basket（NVIDIA 生態人形籃）
- **權重設計**：以「對 NVIDIA Jetson 生態營收暴露度」為主排序：
  - 直接組件商（研華、宜鼎、所羅門）權重 60%
  - 伺服器代工（廣達）權重 20%
  - 邊緣 / 通路（立端、新漢、聯強）權重 20%
- **再平衡頻率**：月。每月以 NVDA 機器人事業營收 YoY 為 momentum factor 微調。

### 2. NVIDIA proxy 因子
- **Beta-to-NVDA**：用 60 日滾動回歸個股 vs NVDA 收益 beta，篩 beta > 0.6 且 R² > 0.3 的台股。
- **GR00T event-driven alpha**：GTC、N 系列模型 release、OEM 量產里程碑前後 ±5 日，籃子超額報酬統計（建議 backtest 2024-03 後三次 GTC）。

### 3. 跨籃子 overlap 分析
- 與 02 humanoid_robot basket 取交集 → 共同核心（多偏馬達/減速器）；取差集 → 分離出「純 NVIDIA 生態 alpha」 vs 「純本體鏈 alpha」，可分別配對沖。

### 4. 風險因子
- **Geopolitical**：Jetson Thor 對中國 OEM 出貨可能受出口管制干擾（Unitree、AgiBot）。
- **Concentration**：研華 + 廣達單兩檔常佔籃子權重 > 40%，需設個股上限 25%。
- **Valuation**：研華本益比已偏高（金管會新聞引「被嚴重低估 → 上看千元」屬媒體價，須以絕對估值反推）。

---

## 可行性評估

| 面向 | 評分 | 說明 |
|---|---|---|
| 主題能見度 | A | GTC 2026 後媒體覆蓋度極高，散戶熱度（昆盈、所羅門兩根漲停）已反映。 |
| 機構參與度 | B+ | TrendForce、Morgan Stanley 已上修出貨預測；外資對研華 18 連賣後出現分歧。 |
| 量化可投資性 | A- | 8-10 檔流動性充足；台股 + 美股 + 日股可組多市場籃子。 |
| 持續性 | B | 短期催化密集（GTC、N1.7 GA），中期需檢驗 OEM 真實量產達標率（Unitree 5,500→20,000 的執行風險）。 |
| 與 02 籃子互補性 | A | NVIDIA 生態 vs 本體鏈差異化清楚，可雙籃並行做相對價值。 |

**結論**：可建構，但需透過個股權重上限 + 與 NVDA 高 beta 過濾減少集中度與估值風險。

---

## 結論與監控訊號

### 結論
GR00T 已從「demo 階段」邁入「商用授權 + OEM 規模採用」拐點。市場已部分定價，但**製造端 throughput**（Atlas 30K、Unitree 20K、Figure/Apollo 工廠試點達成率）才是 2026-2027 真實 alpha 來源。建議建立 GR00T-thematic basket（核心 6-8 檔），並與 02 的中國本體鏈 basket 配對。

### 下一個催化劑（監控訊號）
1. **2026 Q2-Q3**：GR00T N1.7 商用授權客戶名單擴張、N2 路線圖（若有）。
2. **2026-08 NVIDIA FY26 Q2 財報**：Robotics & Auto 是否達 50 億 USD 階段目標。
3. **2026 Computex（6 月）**：Jetson Thor 衍生模組與台廠新合作。
4. **2026-2027 CES 1 月**：Hyundai Atlas 量產進度更新（去年 1/5 揭露 30K 計畫）。
5. **GTC 2027（3 月）**：可能 N2/GR00T 多形態（雙足 + 輪式）統一模型。
6. **Unitree H1 2026 出貨數字**：是否走在 20K 軌道上。
7. **Figure / Apptronik** 大客戶簽約（BMW、Mercedes、Amazon FC 部署規模）。

---

## 引用來源

1. NVIDIA Newsroom — *NVIDIA Announces Isaac GR00T N1, the World's First Open Humanoid Robot Foundation Model*. https://nvidianews.nvidia.com/news/nvidia-isaac-gr00t-n1-open-humanoid-robot-foundation-model-simulation-frameworks
2. NVIDIA Developer — *Isaac GR00T - Generalist Robot 00 Technology*. https://developer.nvidia.com/isaac/gr00t
3. NVIDIA/Isaac-GR00T GitHub (N1.7). https://github.com/NVIDIA/Isaac-GR00T
4. arXiv 2503.14734 — *GR00T N1: An Open Foundation Model for Generalist Humanoid Robots*. https://arxiv.org/abs/2503.14734
5. NVIDIA Newsroom — *NVIDIA and Global Robotics Leaders Take Physical AI to the Real World* (GTC 2026). https://nvidianews.nvidia.com/news/nvidia-and-global-robotics-leaders-take-physical-ai-to-the-real-world
6. The Robot Report — *Advantech shows robotics, medical AI, industrial edge using NVIDIA Jetson Thor*. https://www.therobotreport.com/advantech-shows-robotics-medical-ai-industrial-edge-using-nvidia-jetson-thor/
7. Taiwan News — *Taiwan's Techman unveils new humanoid robot at NVIDIA GTC* (2026-03-18). https://www.taiwannews.com.tw/news/6323007
8. TrendForce — *NVIDIA Jetson Thor Targets Advanced Humanoid Applications, Driving Humanoid Robot Chip Market to Exceed US$48M by 2028*. https://www.trendforce.com/presscenter/news/20250826-12685.html
9. Axios — *Hyundai plans 30,000 humanoid robots a year by 2028* (CES 2026, 2026-01-05). https://www.axios.com/2026/01/05/hyundai-humanoid-robots-boston-dynamics
10. eWeek — *China's Unitree Aims to Ship 20,000 Humanoid Robots in 2026*. https://www.eweek.com/news/unitree-20000-humanoid-robots-2026-china/
11. SCMP — *Unitree eyes 20,000-robot output in 2026 after gala*. https://www.scmp.com/tech/big-tech/article/3343825/kung-fu-somersaults-and-scale-unitree-eyes-20000-robot-output-2026-after-gala
12. TrendForce — *China's Humanoid Robot Output to Surge 94% in 2026; Unitree and AgiBot Capture ~80% Share* (2026-04-09). https://www.trendforce.com/presscenter/news/20260409-13007.html
13. 經濟日報 — *機器人新大腦 標的股 IPC 電源崛起 研華被嚴重低估*. https://money.udn.com/money/story/12040/8969816
14. 永豐金證券豐雲學堂 — *NVIDIA Jetson Thor 平台 3 間台廠合作夥伴*. https://www.sinotrade.com.tw/richclub/hotstock/...

---

**免責聲明**：本筆記僅供內部研究，非投資建議。所有公司、代號、股價討論僅為量化研究基礎資料整理，實際投資需配合風控與法遵程序。

---

## 實證結果（empirical, 2026-04-28）

> Data: yfinance daily Close, 2025-01-02 ~ 2026-04-27（rf=2%）
> 完整資料: `results/07_empirical_results.md` / `07_empirical_stats.csv` / `07_empirical_results.json`
> 腳本: `results/07_empirical.py`

### 績效（1Y return Top / Bottom）
- **Top**：2308 台達電 +452.7% (Sharpe 5.5)、5289 宜鼎 +435.9% (Sharpe 5.6)、6857.T Advantest +379.2% (Sharpe 3.1)、SYM +148.6%、6981.T Murata +149.8%。
- **Bottom**：8234 新漢 -16.6%、2359 所羅門 -15.3%、6594.T Nidec -5.1%、6245 立端 +1.7%、2395 研華 +8.7%。
- 籃內最強者是「上游週期 (台達電/Advantest) + Jetson 儲存 (宜鼎)」，**MD 列為 ★★★★★ 的研華 / 所羅門卻是吊車尾**。

### NVDA-β 假設驗證 — **失敗**
- 原 MD 篩選條件「β > 0.6 & R² > 0.3」：13 檔中**僅 NVDA(自身) 與 SERV** 通過。
- 台股 β-to-NVDA 普遍在 -0.24 ~ +0.20，R² < 0.07 — 短期價格與 NVDA 動能脫鉤。
- **建議改用 β-to-SOX 或 β-to-2330(台積電)** 作 NVIDIA 生態 proxy，或拉長至 120D rolling。

### 兩籃 portfolio 相關係數
- **07 vs 02 portfolio return correlation = 0.69**（高於 PoC Jaccard ≈ 0.20）。
- 共同錨 (NVDA、Nidec、Murata、台達電) 拉高真實共動；要做純 long/short pair 需先剝除共有錨點。

### GTC 2026 (3/17) 事件研究
- 07 籃：事件當日 AR **+1.95%**，但 ±10D **CAR -7.5%**（02 籃 CAR -13.2%）。
- 「買謠言、賣事實」型態：GTC 前已被市場 price in，事件後獲利了結。
- **策略**：事件前 -10D 進場，事件當日減碼，而非追高。

### 權重再平衡建議（資料驅動）
- **升權重**：2308 (6→12%)、5289 (10→12%)、6857.T (4→7%)、SYM (4→6%)。
- **降權重**：2395 (18→10%)、2359 (8→4%)、8234 (4→2%)、6245 (5→3%)。
- 結構性發現：**「直接合作夥伴」≠ 股價跑贏**；真正貢獻 alpha 的是同時吃到 AI server 與機器人題材的上游週期股。
