# -*- coding: utf-8 -*-
"""
Taiwan Humanoid Robot Supply Chain Basket — PoC
=================================================

GiS Genesis International Capital — Quantitative Research
Author: Quant Team
Date  : 2026-04-28

事件背景
--------
2026-04-19 北京亦莊（E-Town）人形機器人半馬拉松，Honor「Lightning」
以 50:26（自主導航）擊敗人類世界紀錄 57:20。台廠精密機械、伺服、
電池、AI 算力供應鏈進入訂單兌現週期。

本 PoC 目的
-----------
1. 程式內 hardcode 至少 8 檔台股供應鏈成份股
2. 設計權重邏輯：價值鏈核心度 × 營收純度 × 流動性
3. 印出 basket composition 與各檔在價值鏈位置
4. 預留 yfinance / twstock 串接介面，方便日後接實際股價回測

執行方式
--------
    python 02_humanoid_robot_basket.py

未來擴充
--------
- 接 yfinance / twstock 取得日 K
- 建立 daily NAV、Sharpe、max drawdown
- 串接月營收（公開資訊觀測站）做 momentum overlay
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict
import json


# ---------------------------------------------------------------------------
# 1. Basket 成份定義（hardcode 至少 8 檔）
# ---------------------------------------------------------------------------
# 設計邏輯說明：
#   - core_score：價值鏈核心度（0-1）。BOM 成本佔比愈高、技術愈關鍵分數愈高。
#       精密機械（諧波/螺桿/RV）≈ 0.9-1.0
#       伺服 + 電源              ≈ 0.7-0.8
#       AI 算力 / 整機          ≈ 0.6-0.8
#       電池 / 感測             ≈ 0.5-0.7
#   - purity_score：humanoid 訂單在營收占比（0-1）。純度愈高分數愈高。
#       小型 humanoid 純玩家（鈞興、大銀微）≈ 0.7-0.9
#       上銀、和大（自動化機械主業 + humanoid 增量）≈ 0.4-0.6
#       鴻海、廣達（百業 ODM，humanoid 是新戰場但占比小）≈ 0.15-0.30
#   - liquidity_score：流動性（0-1），日均成交額 $30M USD 以上者 = 1.0，
#       低於門檻按比例給分。承載大型機構資金的可行性指標。

@dataclass
class StockComponent:
    """單一供應鏈成份股結構"""
    ticker: str            # 台股代號（4 碼）
    name: str              # 公司名（中文）
    name_en: str           # 公司名（英文）
    value_chain: str       # 價值鏈位置
    sub_segment: str       # 細分（諧波減速 / 滾珠螺桿 / 伺服馬達 ...）
    core_score: float      # 價值鏈核心度 0-1
    purity_score: float    # humanoid 營收純度 0-1
    liquidity_score: float # 流動性 0-1
    notes: str = ""        # 備註：客戶關係、技術 moat
    weight: float = 0.0    # rebalance 後的權重（待計算）


# 至少 8 檔，實際放 10 檔以利分散
BASKET: List[StockComponent] = [
    StockComponent(
        ticker="2049",
        name="上銀科技",
        name_en="HIWIN Technologies",
        value_chain="精密機械",
        sub_segment="諧波減速機 + 滾珠螺桿",
        core_score=1.00,    # 全球前三大、台廠唯一全產品線
        purity_score=0.50,  # 工具機自動化主業 + humanoid 增量
        liquidity_score=1.00,
        notes="鴻海 humanoid 大關節（肩、髖）模組獨家供應；特斯拉 Optimus 合作驗證中。",
    ),
    StockComponent(
        ticker="2308",
        name="台達電",
        name_en="Delta Electronics",
        value_chain="伺服與動力 + 散熱",
        sub_segment="伺服馬達 + 電源管理 + 液冷模組",
        core_score=0.85,
        purity_score=0.20,  # 大集團，humanoid 占比小但絕對額大
        liquidity_score=1.00,
        notes="Honor Lightning 液冷靈感與台達消費電子散熱模組同源；通吃中美 humanoid OEM。",
    ),
    StockComponent(
        ticker="2317",
        name="鴻海",
        name_en="Foxconn (Hon Hai)",
        value_chain="整機組裝 + AI 算力",
        sub_segment="ODM + FoxBrain + NVIDIA GR00T",
        core_score=0.75,
        purity_score=0.15,  # ODM 巨人，humanoid 是次要但戰略性業務
        liquidity_score=1.00,
        notes="自家 humanoid 計畫 2026 年底量產；電動車工廠作驗證場；NVIDIA Project GR00T 台灣首席合作。",
    ),
    StockComponent(
        ticker="1536",
        name="和大工業",
        name_en="Hota Industrial",
        value_chain="精密機械",
        sub_segment="RV 減速機 + 精密齒輪",
        core_score=0.85,
        purity_score=0.45,  # 工業機器人 + 新能源 + humanoid
        liquidity_score=0.85,
        notes="特斯拉電動車齒輪供應商、humanoid 減速齒輪驗證中，營運槓桿大。",
    ),
    StockComponent(
        ticker="2382",
        name="廣達",
        name_en="Quanta Computer",
        value_chain="AI 算力 + 整機",
        sub_segment="QCT × Techman × NVIDIA TM Xplore I",
        core_score=0.75,
        purity_score=0.15,
        liquidity_score=1.00,
        notes="Quanta Cloud Tech + Techman Robot 推 humanoid 服務型機器人，2026 H2 規模化。",
    ),
    StockComponent(
        ticker="4571",
        name="鈞興-KY",
        name_en="Solomon Tech (Kun-Hsin)",
        value_chain="精密機械",
        sub_segment="諧波減速機（純玩家）",
        core_score=0.95,
        purity_score=0.80,  # 最純的 humanoid 標的之一
        liquidity_score=0.50,  # 中小型，流動性受限
        notes="諧波減速機純玩家，多家中國 humanoid OEM 已下單，但日均成交額 < $30M USD。",
    ),
    StockComponent(
        ticker="4576",
        name="大銀微系統",
        name_en="HIWIN Mikrosystem",
        value_chain="精密機械",
        sub_segment="精密微型減速 + 線性致動",
        core_score=0.90,
        purity_score=0.60,  # 上銀子公司，humanoid 占比較高
        liquidity_score=0.55,
        notes="上銀集團子公司，主攻微型化減速機與致動器，humanoid 手腕關節重要供應商。",
    ),
    StockComponent(
        ticker="6121",
        name="新普科技",
        name_en="Simplo Technology",
        value_chain="電池與電源",
        sub_segment="高能量密度電池模組 + BMS",
        core_score=0.65,
        purity_score=0.25,
        liquidity_score=0.85,
        notes="筆電/儲能電池主業，humanoid 高密度電池模組擴充中。",
    ),
    StockComponent(
        ticker="2233",
        name="宇隆科技",
        name_en="Yu Long Tech",
        value_chain="精密機械",
        sub_segment="精密傳動齒輪",
        core_score=0.80,
        purity_score=0.55,
        liquidity_score=0.45,
        notes="月營收已透露 humanoid 訂單，營運槓桿極大；流動性需注意。",
    ),
    StockComponent(
        ticker="2395",
        name="研華",
        name_en="Advantech",
        value_chain="AI 算力（邊緣）",
        sub_segment="工業電腦 + 邊緣 AI 主機板",
        core_score=0.65,
        purity_score=0.20,
        liquidity_score=0.95,
        notes="工業 PC 龍頭，humanoid 控制器與邊緣 AI 主機板候選供應商。",
    ),
]


# ---------------------------------------------------------------------------
# 2. 權重設計邏輯
# ---------------------------------------------------------------------------
# weight = (0.4 * core_score + 0.4 * purity_score + 0.2 * liquidity_score) / 總和
# 三維度設計理由：
#   core_score   權重 40% — 量化「不可取代性」，決定長期 alpha。
#   purity_score 權重 40% — 量化「事件 beta」，決定 thematic 行情中的彈性。
#   liquidity    權重 20% — 量化「機構可承載性」，避免 small cap 滑價毀掉策略。

WEIGHT_CORE = 0.40
WEIGHT_PURITY = 0.40
WEIGHT_LIQUIDITY = 0.20


def compute_weights(basket: List[StockComponent]) -> List[StockComponent]:
    """依三維度分數計算 basket 權重（合計 = 100%）。"""
    raw_scores = []
    for s in basket:
        raw = (
            WEIGHT_CORE * s.core_score
            + WEIGHT_PURITY * s.purity_score
            + WEIGHT_LIQUIDITY * s.liquidity_score
        )
        raw_scores.append(raw)

    total = sum(raw_scores)
    for s, r in zip(basket, raw_scores):
        s.weight = r / total
    return basket


# ---------------------------------------------------------------------------
# 3. 報表輸出
# ---------------------------------------------------------------------------

def print_basket_composition(basket: List[StockComponent]) -> None:
    """印出 basket 詳細組成（中文表格）。"""
    print("=" * 110)
    print("  Taiwan Humanoid Robot Supply Chain Basket — THR-Index PoC")
    print("  GiS Genesis International Capital | 量化研究組 | 2026-04-28")
    print("=" * 110)
    print()
    print(f"{'代號':<6} {'公司':<10} {'價值鏈':<14} {'細分':<26} "
          f"{'Core':>5} {'Purity':>7} {'Liq.':>5} {'權重':>7}")
    print("-" * 110)

    for s in sorted(basket, key=lambda x: -x.weight):
        print(
            f"{s.ticker:<6} "
            f"{s.name:<10} "
            f"{s.value_chain:<14} "
            f"{s.sub_segment:<26} "
            f"{s.core_score:>5.2f} "
            f"{s.purity_score:>7.2f} "
            f"{s.liquidity_score:>5.2f} "
            f"{s.weight*100:>6.2f}%"
        )

    print("-" * 110)
    print(f"{'合計':<6} {'':<10} {'':<14} {'':<26} {'':>5} {'':>7} {'':>5} "
          f"{sum(s.weight for s in basket)*100:>6.2f}%")
    print()


def print_value_chain_breakdown(basket: List[StockComponent]) -> None:
    """依價值鏈位置彙總權重分布。"""
    chain_weights: Dict[str, float] = {}
    for s in basket:
        chain_weights[s.value_chain] = chain_weights.get(s.value_chain, 0.0) + s.weight

    print("價值鏈權重分布")
    print("-" * 50)
    for chain, w in sorted(chain_weights.items(), key=lambda x: -x[1]):
        bar = "#" * int(w * 100)
        print(f"  {chain:<14}  {w*100:>6.2f}%  {bar}")
    print()


def print_notes(basket: List[StockComponent]) -> None:
    """印出每檔投資邏輯備註。"""
    print("選股邏輯與投資要點")
    print("-" * 110)
    for s in sorted(basket, key=lambda x: -x.weight):
        print(f"  [{s.ticker}] {s.name}（權重 {s.weight*100:.2f}%）")
        print(f"      價值鏈：{s.value_chain} / {s.sub_segment}")
        print(f"      切入點：{s.notes}")
        print()


def export_json(basket: List[StockComponent], path: str = "basket_composition.json") -> None:
    """輸出 JSON，供下游策略引擎使用。"""
    payload = [
        {
            "ticker": s.ticker,
            "name": s.name,
            "name_en": s.name_en,
            "value_chain": s.value_chain,
            "sub_segment": s.sub_segment,
            "core_score": s.core_score,
            "purity_score": s.purity_score,
            "liquidity_score": s.liquidity_score,
            "weight": round(s.weight, 6),
            "notes": s.notes,
        }
        for s in sorted(basket, key=lambda x: -x.weight)
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[OK] basket composition exported -> {path}")


# ---------------------------------------------------------------------------
# 4. 預留 yfinance / twstock 介面（不執行，僅註解供日後串接）
# ---------------------------------------------------------------------------
"""
# === 未來擴充：實際抓取股價並計算 NAV ===

import yfinance as yf
import pandas as pd

def fetch_prices(basket, start="2024-01-01", end=None):
    \"\"\"
    yfinance 介面 — 台股代號需加 .TW 後綴
        2049  -> 2049.TW
        4571  -> 4571.TWO（上櫃用 .TWO）
    \"\"\"
    tickers_yf = []
    for s in basket:
        # 上市 .TW，上櫃 .TWO；4571 鈞興-KY 為上櫃
        suffix = ".TWO" if s.ticker in {"4571"} else ".TW"
        tickers_yf.append(s.ticker + suffix)

    df = yf.download(tickers_yf, start=start, end=end, auto_adjust=True)["Close"]
    return df


def fetch_prices_twstock(basket):
    \"\"\"
    twstock 介面（台廠官方資料源，無需翻牆）
        import twstock
        stock = twstock.Stock('2049')
        stock.fetch_from(2024, 1)
    \"\"\"
    pass


def compute_basket_nav(prices: pd.DataFrame, basket) -> pd.Series:
    \"\"\"按 weight 計算 daily NAV。每季度 rebalance。\"\"\"
    weights = pd.Series({s.ticker: s.weight for s in basket})
    returns = prices.pct_change().fillna(0.0)
    daily_ret = (returns * weights.values).sum(axis=1)
    nav = (1 + daily_ret).cumprod()
    return nav


def compute_sharpe(nav: pd.Series, rf: float = 0.015) -> float:
    daily_ret = nav.pct_change().dropna()
    excess = daily_ret.mean() * 252 - rf
    vol = daily_ret.std() * (252 ** 0.5)
    return excess / vol
"""


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------
def main() -> None:
    # 1. 計算權重
    basket = compute_weights(BASKET)

    # 2. 印出組成
    print_basket_composition(basket)

    # 3. 價值鏈分布
    print_value_chain_breakdown(basket)

    # 4. 投資邏輯
    print_notes(basket)

    # 5. JSON 輸出（提供下游策略使用）
    export_json(basket)

    # 6. 簡要統計
    print("Basket 摘要統計")
    print("-" * 50)
    print(f"  成份股數          : {len(basket)} 檔")
    print(f"  最大單檔權重      : {max(s.weight for s in basket)*100:.2f}%  "
          f"（{max(basket, key=lambda x: x.weight).name}）")
    print(f"  最小單檔權重      : {min(s.weight for s in basket)*100:.2f}%  "
          f"（{min(basket, key=lambda x: x.weight).name}）")
    print(f"  精密機械權重佔比  : "
          f"{sum(s.weight for s in basket if s.value_chain=='精密機械')*100:.2f}%")
    print(f"  純度加權平均      : "
          f"{sum(s.weight*s.purity_score for s in basket)*100:.1f}（百分制）")
    print()
    print("[Done] Lightning 50:26 — Taiwan supply chain basket ready for backtest hookup.")


if __name__ == "__main__":
    main()
