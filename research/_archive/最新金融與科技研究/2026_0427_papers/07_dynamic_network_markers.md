---
paper_id: 07
title: 金融市場不穩定性的動態網路標記
title_en: Identifying Dynamical Network Markers of Financial Market Instability
arxiv: 2604.21297
date: 2026-04-24
category: q-fin.RM
feasibility: HIGH
action: 立刻實作
priority: 3
eta_days: 4-5
---

# 07 · 動態網路風險標記

## 論文要旨

把市場視為股票相關性網路，定義網路指標（degree、betweenness、modularity、spectral radius），觀察網路**結構變化**作為市場壓力前兆。結論：網路同步化（spectral radius 上升）顯著早於波動率指數，**前置 5–10 個交易日**即出現警示。

## 為何重要

- 既有風控指標（VIX、波動率）為「同步反應」，**已沒有 lead time**
- 網路指標屬「結構性」訊號，可作為 **portfolio 降槓桿、降權重的早期警示**
- 計算成本低（每日 1500x1500 矩陣），可作為日常 dashboard

## 可行性評估

| 項目 | 狀態 |
|------|------|
| 日線資料 | ✅ tw-stock-scanner |
| `networkx` / `igraph` | ✅ 標準庫 |
| 計算成本 | 微小（每日 30 秒） |
| 歷史驗證資料 | ✅ 含 2018、2020、2022 多次壓力期 |

**結論：高可行 — 4-5 天內可上 dashboard**

## 實作步驟

### Phase A：建立動態相關性網路（1 天）
```python
# src/tw-stock-scanner/risk/dynamic_network.py

import numpy as np
import networkx as nx

def build_correlation_network(returns, window=60, threshold=0.5):
    """
    returns: (T, N) panel
    回傳每日的網路（過去 60 日相關係數，門檻 0.5 以上連邊）
    """
    networks = {}
    for t in range(window, len(returns)):
        window_ret = returns.iloc[t-window:t]
        corr = window_ret.corr().abs()
        # 用 PMFG 或 MST 抽稀網路（避免完全圖）
        G = build_PMFG(corr)  # Planar Maximally Filtered Graph
        networks[returns.index[t]] = G
    return networks
```

### Phase B：網路指標計算（1 天）
```python
def network_markers(G, returns_window):
    """
    計算 6 個關鍵指標
    """
    corr = returns_window.corr().abs().values
    eigenvalues = np.linalg.eigvalsh(corr)
    
    return {
        'spectral_radius': eigenvalues.max(),  # 最重要：同步化指標
        'spectral_gap': eigenvalues[-1] - eigenvalues[-2],
        'avg_degree': np.mean([d for n, d in G.degree()]),
        'modularity': nx.community.modularity(G, ...),  # 社群結構
        'avg_clustering': nx.average_clustering(G),
        'mean_correlation': corr[np.triu_indices_from(corr, 1)].mean(),
    }
```

### Phase C：警示規則建立（1 天）
```python
# src/tw-stock-scanner/risk/instability_alerts.py

def detect_instability(markers_history):
    """
    基於 markers 過去 252 日分布，檢測異常
    """
    current = markers_history.iloc[-1]
    historical = markers_history.iloc[-252:-1]
    
    alerts = {}
    # 規則 1：spectral_radius 突破 95 分位 → 黃燈
    if current['spectral_radius'] > historical['spectral_radius'].quantile(0.95):
        alerts['spectral_warning'] = True
    
    # 規則 2：mean_correlation > 0.7 → 紅燈（過度同步化）
    if current['mean_correlation'] > 0.7:
        alerts['synchronization_red'] = True
    
    # 規則 3：modularity 7 日跌幅 > 30% → 結構性瓦解
    if (current['modularity'] / markers_history.iloc[-7]['modularity'] - 1) < -0.3:
        alerts['structural_breakdown'] = True
    
    return alerts
```

### Phase D：歷史回測驗證（1 天）
- 取 2018-2026 台股資料
- 標記已知的壓力期：
  - 2018/02 VIX spike
  - 2018/12 美股閃崩
  - 2020/03 COVID
  - 2022/02 烏俄戰爭
  - 2022/10 通膨高峰
  - 2024/08 日圓套利平倉（Yen carry unwind）
- 驗證每次警示有多少 lead time

### Phase E：Dashboard 整合（1 天）
- 即時顯示 6 個 network markers
- 警示燈（綠/黃/紅）
- 歷史百分位圖
- **觸發紅燈 → 自動 email alert + 建議降槓桿幅度**

## 預期產出

- `src/tw-stock-scanner/risk/dynamic_network.py`
- `src/tw-stock-scanner/risk/instability_alerts.py`
- 歷史驗證報告（lead time 統計）
- Dashboard 頁面

## 預期效果

- 重大壓力事件提前 5-10 天警示
- Drawdown 預期降低 15-25%（基於 lead time × 降槓桿執行）
- 比 VIX 多 5-7 天的反應時間

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| 網路指標假警示（市場最終沒事） | 紅燈僅降槓桿不出清，黃燈僅監控 |
| PMFG 抽稀使部分結構訊號流失 | 同時保留稠密網路指標（mean_correlation） |
| 600+ 檔股票數仍嫌少 | 加入 ETF、外資指標、產業 ETF 提高信號密度 |

## 參考

- Paper: https://arxiv.org/abs/2604.21297
- 相關：Tumminello et al. (2005) "A tool for filtering information in complex systems" PNAS
- 相關：Onnela et al. (2003) "Dynamics of market correlations: Taxonomy and portfolio analysis" PRE
- 相關：Diebold & Yilmaz (2014) "On the network topology of variance decompositions"
