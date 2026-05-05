---
paper_id: 03
title: 結構化策略回測評估：Peer 基準、制度擇時、實盤表現
title_en: Evaluating Structured Strategy Backtests
arxiv: 2604.18821
date: 2026-04-22
category: q-fin.PM
feasibility: VERY_HIGH
action: 立刻實作
priority: 2
eta_days: 3-4
---

# 03 · 結構化策略回測評估框架

## 論文要旨

提出一個三層回測健診框架：
1. **Peer benchmark**：跟「同類策略」比較，避免 self-defined benchmark
2. **Regime timing**：分制度（牛/熊/震盪/恐慌）拆解績效
3. **Live tracking**：用樣本外實盤資料驗證，計算「樣本內 vs 實盤」績效衰減

## 為何重要

- 量化團隊**最大的職業風險是回測過擬合的策略上線**
- 我們既有的 backtest 報告偏「孤立評估」，沒有 peer 對比、沒有 regime 拆分
- 上線後績效衰退到「正常範圍」還是「策略已死」需要量化判定

## 可行性評估

| 項目 | 狀態 |
|------|------|
| 既有 backtest engine | ✅ tw-stock-scanner |
| 制度標籤資料 | ⏳ 需建立（或用 #7 動態網路指標反推） |
| Peer benchmark 庫 | ⚠️ 需自建 |
| 計算成本 | 中等 |

**結論：極高可行 — 主要是建立分析框架，3-4 天**

## 實作步驟

### Phase A：定義 Peer Benchmark 庫（1 天）
為每類策略建立公開可下載的 peer：

| 策略類型 | Peer Benchmark |
|---------|---------------|
| 多因子選股 | 0050、0056、AOR ETF |
| 動量策略 | MTUM、QQQ momentum tilts |
| 價值策略 | VLUE、IWN |
| 低波動 | USMV、SPLV |
| 量化多空 | HFRX Equity Hedge Index |
| 配對交易 | Bond/Equity 60/40 |

落地：`src/tw-stock-scanner/eval/peer_benchmarks.py`

### Phase B：制度標籤系統（1 天）
參考 Hamilton (1989) Markov-switching：
- 定義 4 個制度：牛市/熊市/震盪/恐慌
- 用 VIX、台灣 VIX、信用利差、廣度指標 4 個因子
- HMM 或 rule-based 標記每個交易日

```python
# src/tw-stock-scanner/eval/regime_classifier.py
def classify_regime(date_range):
    """
    回傳每日的 regime label：
    - bull: VIX<20 且 趨勢正
    - bear: VIX>30 或 max-drawdown>20%
    - choppy: VIX 20-30 且 趨勢不明
    - panic: VIX 一週內飆升 >50%
    """
    ...
```

### Phase C：三層健診報告產生器（1 天）
```python
# src/tw-stock-scanner/eval/strategy_health_check.py

def health_check(strategy_returns, benchmark='auto'):
    report = {}
    # 第一層：Peer 對比
    report['peer'] = {
        'sharpe_diff': strategy_sharpe - peer_sharpe,
        'tracking_error': ...,
        'percentile_in_peer_distribution': ...
    }
    # 第二層：制度拆解
    regimes = classify_regime(strategy_returns.index)
    report['regime'] = {
        regime: {
            'sharpe': ..., 'mdd': ..., 'win_rate': ...
        } for regime in ['bull', 'bear', 'choppy', 'panic']
    }
    # 第三層：實盤衰減
    if has_live_data:
        report['decay'] = {
            'in_sample_sharpe': ...,
            'live_sharpe': ...,
            'decay_pct': (in_sample - live) / in_sample,
            'p_value_decay_significant': ...  # bootstrap test
        }
    return report
```

### Phase D：自動化集成（半天）
- 所有 production 策略每月跑一次 health_check
- 產出 PDF 報告，紅燈/黃燈/綠燈
- **紅燈標準**：實盤 Sharpe < 樣本內 Sharpe × 0.4 持續 3 個月 → 暫停策略

## 預期產出

- `src/tw-stock-scanner/eval/peer_benchmarks.py`
- `src/tw-stock-scanner/eval/regime_classifier.py`
- `src/tw-stock-scanner/eval/strategy_health_check.py`
- 既有 5 個 production 策略的首次健診報告
- 自動化排程（每月 1 號跑一次）

## 預期效果

- **早期偵測策略衰退**：把過擬合策略下架的 lead time 從 6 個月縮到 2 個月
- **更誠實的 sharpe**：peer 對比後，「真實 alpha」與「市場 beta 偽裝成 alpha」分離
- **管理層溝通**：紅黃綠燈讓非量化人員也能理解策略狀態

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| Peer benchmark 與我方策略相關性過低 | 用多個 peer 做 regression-based custom benchmark |
| Regime 標籤主觀 | 同時提供 HMM-based 與 rule-based 兩版本 |
| 衰減判定誤差 | 至少 90 個交易日才判定，bootstrap 信賴區間 |

## 參考

- Paper: https://arxiv.org/abs/2604.18821
- Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary Time Series" Econometrica
- Bailey & López de Prado (2014) "The Deflated Sharpe Ratio" JoPM
