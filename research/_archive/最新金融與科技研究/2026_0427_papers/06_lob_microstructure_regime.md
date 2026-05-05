---
paper_id: 06
title: 訂單簿微結構制度的早期偵測
title_en: Early Detection of Latent Microstructure Regimes in Limit Order Books
arxiv: 2604.20949
date: 2026-04-24
category: q-fin.TR
feasibility: HIGH
action: 立刻實作
priority: 5
eta_days: 5-7
---

# 06 · 訂單簿微結構制度的早期偵測

## 論文要旨

從 LOB（限價委託簿）原始資料萃取微結構特徵（spread、depth、order flow imbalance、price impact），用無監督方法（HMM / change-point detection）偵測**制度切換**（流動性枯竭、單向訂單流、高頻干擾期）。提早 30-60 秒識別制度，可顯著降低執行成本。

## 為何重要（與我方契合）

- 我方 SK COM 已有 tick 資料，但目前**只用收盤價，浪費了 LOB 結構資訊**
- 與既有「五檔開發」需求高度重疊（記憶體中提到尚未開發）
- 不僅做日內擇時，**對既有日級因子流程提供「進場時機」加值層**
- 直接降低 slippage（按 5–10bps 估算，組合年化額外收益 30–60bps）

## 可行性評估

| 項目 | 狀態 |
|------|------|
| Tick / LOB 資料 | ✅ SK COM（部分），目前只取得 best bid/ask |
| 五檔深度 | ⏳ 與既有 roadmap 一致，需開發 |
| 計算需求 | CPU 即可，但即時處理需多執行緒 |
| 即時部署環境 | ✅ 既有交易終端機 |

**結論：高可行 — 與既有五檔開發整合，5-7 天**

## 實作步驟

### Phase A：LOB 特徵抽取（2 天）
與既有五檔開發整合，每筆 tick 計算：
```python
# src/tw-stock-scanner/microstructure/lob_features.py

def lob_features(snapshot):
    """
    snapshot: {bid_px[5], bid_qty[5], ask_px[5], ask_qty[5]}
    """
    mid = (snapshot.bid_px[0] + snapshot.ask_px[0]) / 2
    return {
        'spread_bps': (snapshot.ask_px[0] - snapshot.bid_px[0]) / mid * 1e4,
        'depth_imbalance': (sum(snapshot.bid_qty) - sum(snapshot.ask_qty)) 
                            / (sum(snapshot.bid_qty) + sum(snapshot.ask_qty)),
        'top_of_book_imbalance': (snapshot.bid_qty[0] - snapshot.ask_qty[0]) 
                                   / (snapshot.bid_qty[0] + snapshot.ask_qty[0]),
        'depth_decay': np.std([sum(snapshot.bid_qty[:k]) for k in range(1,6)]),
        'price_slope_bid': np.polyfit(range(5), snapshot.bid_px, 1)[0],
        'micro_price': (snapshot.bid_px[0] * snapshot.ask_qty[0] 
                       + snapshot.ask_px[0] * snapshot.bid_qty[0]) 
                       / (snapshot.bid_qty[0] + snapshot.ask_qty[0]),
    }
```

### Phase B：制度偵測模型（2 天）
3 個制度：normal / stressed / illiquid

```python
# src/tw-stock-scanner/microstructure/regime_detector.py

from hmmlearn.hmm import GaussianHMM
from ruptures import Pelt  # change-point

class LOBRegimeDetector:
    def __init__(self, n_regimes=3):
        self.hmm = GaussianHMM(n_components=n_regimes, covariance_type="full")
    
    def fit(self, feature_history):
        """每天收盤後用前 5 日資料 refit"""
        self.hmm.fit(feature_history)
        # 把 hidden state 排序並命名：
        # state with lowest spread, lowest depth_imbalance abs → normal
        # state with high spread → stressed
        # state with low depth → illiquid
    
    def detect(self, current_features, lookback=60):
        """即時偵測：滑動視窗 60 秒"""
        states = self.hmm.predict(current_features[-lookback:])
        return states[-1], self.hmm.predict_proba(current_features[-lookback:])[-1]
```

### Phase C：執行邏輯整合（2 天）
- normal regime：可正常下單，包含市價
- stressed：限價單為主，避免市價
- illiquid：暫停下單 30 秒，或拆單放更小

```python
# src/tw-stock-scanner/execution/regime_aware_executor.py

def smart_execute(order, regime, regime_proba):
    if regime == 'illiquid' and regime_proba > 0.7:
        return delay(order, 30)
    elif regime == 'stressed':
        return convert_to_limit(order, max_slippage_bps=5)
    else:
        return order  # normal
```

### Phase D：歷史回測驗證（1 天）
- 用 2025 年完整 tick 資料回放
- 計算「啟用 regime detector」vs「不啟用」的執行成本（VWAP slippage）
- 期望結果：年化節省 30-60 bps slippage

## 預期產出

- `src/tw-stock-scanner/microstructure/lob_features.py`
- `src/tw-stock-scanner/microstructure/regime_detector.py`
- `src/tw-stock-scanner/execution/regime_aware_executor.py`
- 回測報告：規則對照下的 slippage 對比
- 即時 dashboard：當前 LOB regime 狀態

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| HMM 標籤主觀（哪個 state 是 stressed） | 用 spread 排序 hidden states，rule-based 對應 |
| 制度頻繁切換造成過度反應 | hysteresis：連續 N 個 tick 同制度才切換 |
| 五檔資料延遲 | SK COM 既有架構應 < 100ms 可接受 |

## 與既有 Roadmap 整合

依記憶體：
- ✅ project_terminal_status：「待開發五檔/指標/限價單」
- 此論文實作可**順帶完成五檔開發**，並把五檔資料用於微結構而不只是顯示
- 限價單功能也是此論文的執行邏輯需要

## 參考

- Paper: https://arxiv.org/abs/2604.20949
- 相關：Cont, Stoikov, Talreja (2010) "A Stochastic Model for Order Book Dynamics" Operations Research
- 相關：Cartea, Jaimungal, Penalva (2015) Algorithmic and High-Frequency Trading
