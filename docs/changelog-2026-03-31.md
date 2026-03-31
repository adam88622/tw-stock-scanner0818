# 台股掃描器 — 變更紀錄 2026-03-31

## 變更摘要

新增「研究週報」模組，將金融科技研究報告從產業研究頁面分離，建立獨立的分類檢索系統。

---

## 變更檔案

| 檔案 | 異動 | 行數 |
|------|------|------|
| `app.py` | 新增 | +275 行 |
| `templates/base.html` | 修改 | +16 行 |
| `templates/weekly.html` | 新增 | 全新檔案 |
| `templates/research.html` | 未修改 | — |

---

## 詳細變更

### 1. `app.py` — 新增 3 個 route

#### `/research` — 產業研究報告列表
- 掃描 `research/` 資料夾下的 HTML 報告
- 自動從 HTML `<title>` 抓取報告標題
- 自動從 HTML 內容抓取摘要（第一段有意義的文字）
- 排除 `_archive/` 資料夾（已封存的金融科技報告）
- 按日期降序排列

#### `/weekly` — 研究週報頁面
- 自動掃描 3 類內容：
  1. **量化研究週報**：`fin-lab/output/weekly-briefing-*.html`
  2. **金融科技分類報告**：`fin-lab/output/category-reports/*.html`，按內容自動分為 6 類：
     - 風險管理（6 篇）
     - 因子與策略（11 篇）
     - 選擇權與波動率（8 篇）
     - 情緒與 NLP（3 篇）
     - 總經與資產配置（6 篇）
     - 特殊主題（7 篇）
  3. **科技研究精選**：`tech-research/research-*/*.html`
- 第二個 Tab 顯示 fin-lab 全部專案總覽（自動掃描 15 個分類、100+ 個專案）

#### `/api/weekly/<path>` — 報告內容 API
- 支援 3 種路徑前綴：`fin/`、`cat/`、`tech/`
- 安全檢查：禁止 `..`、正則驗證檔名格式
- 讓 iframe 彈出式閱讀器讀取報告 HTML

### 2. `templates/base.html` — 側邊欄修改

新增「產業研究」區塊，含兩個連結：
```html
<li class="nav-section">產業研究</li>
<li class="nav-item"><a href="/research">研究報告</a></li>
<li class="nav-item"><a href="/weekly">研究週報</a></li>
```

### 3. `templates/weekly.html` — 全新頁面

功能：
- **Tab 切換**：「週報簡報」/「專案總覽」
- **分類區塊**：每個類別有獨立顏色標籤
  - 風險管理 → 紅
  - 因子與策略 → 紫
  - 選擇權與波動率 → 黃
  - 情緒與 NLP → 綠
  - 總經與資產配置 → 藍
  - 特殊主題 → 淺紫
- **彈出式閱讀器**：點擊報告 → overlay + iframe 顯示完整報告
- **統計卡片**：總專案數、有程式碼數、研究分類數

### 4. 檔案搬移

| 來源 | 目的 | 說明 |
|------|------|------|
| `research/最新金融與科技研究/` (41 份) | `research/_archive/最新金融與科技研究/` | 從產業研究頁面移除 |
| 同上（複製） | `fin-lab/output/category-reports/` | 搬到週報頁面顯示 |

---

## 分類映射邏輯

金融科技報告的分類基於檔名前綴匹配：

```python
_CAT_MAP = {
    'regime-detector': '風險管理',
    'garch-report': '風險管理',
    'te-report': '風險管理',
    'entropy-report': '風險管理',
    'km-report': '風險管理',
    'blind-signal': '因子與策略',
    'jf-ml-returns': '因子與策略',
    'oql-report': '選擇權與波動率',
    'spx-vix': '選擇權與波動率',
    'llm-screener': '情緒與 NLP',
    'wti-report': '情緒與 NLP',
    'rate-cycle': '總經與資產配置',
    'FI-01': '總經與資產配置',
    'CQ-01': '特殊主題',
    'ED-01': '特殊主題',
    # ... 完整映射見 app.py
}
```

---

## 安全措施

- `/api/research/` 和 `/api/weekly/` 均禁止 `..` 路徑穿越
- 檔名用正則嚴格驗證（只允許英數中文、底線、連字號、點）
- 所有檔案讀取前檢查 `os.path.isfile()`

---

## 影響範圍

- **不影響**：現有台股功能（突破掃描、法人買賣超、券商分點、市場體溫等）
- **不影響**：台股產業研究 8 篇報告
- **新增**：`/weekly` 和 `/research` 兩個獨立頁面
- **移除**：產業研究頁面不再顯示金融科技研究（已搬至週報）
