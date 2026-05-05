"""
07_groot_supply_chain_basket.py
NVIDIA Isaac GR00T 生態 — 投資籃子定義與重疊度分析 PoC

研究背景：
  GTC 2026 後 NVIDIA Isaac GR00T N1.7 商用授權釋出，台股 + 美股 + 日股
  形成跨市場 NVIDIA 機器人生態鏈。本 PoC 定義基準 basket 並提供與
  humanoid_robot basket(02) 的交集 / 差集函數，以利量化研究團隊分析
  「純 NVIDIA 生態 alpha」 vs 「純本體鏈 alpha」。

Author: GiS Quant Research
Date  : 2026-04-28
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Holding:
    ticker: str           # 交易代號
    market: str           # TW / US / JP
    name: str             # 公司名
    role: str             # 在 GR00T 生態中的角色
    weight: float         # basket 內權重 (sum=1.0)


# ---------------------------------------------------------------------------
# 1. NVIDIA Robotics ecosystem basket（本篇 07 主籃）
# ---------------------------------------------------------------------------
NVIDIA_ROBOTICS_BASKET: list[Holding] = [
    # 台股 — Jetson Thor 直接合作 / IPC 整合
    Holding("2395.TW", "TW", "研華 Advantech",   "NVIDIA 點名 Jetson Thor 夥伴；MIC-743/742", 0.18),
    Holding("5289.TW", "TW", "宜鼎 Innodisk",    "Jetson 平台嵌入式儲存 / DRAM 模組",         0.10),
    Holding("2382.TW", "TW", "廣達 Quanta(QCT)", "Techman TM Xplore I 共開發 + DGX 主代工",   0.15),
    Holding("2359.TW", "TW", "所羅門 Solomon",   "3D 視覺 + AI 物件辨識整合 Isaac Sim",       0.08),
    Holding("6245.TW", "TW", "立端 Lanner",      "邊緣 AI 運算主板 / 機器人 controller",      0.05),
    Holding("8234.TW", "TW", "新漢 NEXCOM",      "Jetson Thor 整合產品（Robotics Show）",     0.04),
    Holding("2308.TW", "TW", "台達電 Delta",     "機器人控制器 / 伺服馬達 / 電源",            0.06),
    # 美股 — 核心錨
    Holding("NVDA",    "US", "NVIDIA",            "GR00T 平台 + Jetson Thor + Isaac Sim 主軸", 0.20),
    Holding("SYM",     "US", "Symbotic",          "倉儲自動化 + 機器人系統部署",               0.04),
    # 日股 — 感測 / 馬達
    Holding("6857.T",  "JP", "Advantest",         "AI 晶片測試（NVIDIA 上游）",                0.04),
    Holding("6981.T",  "JP", "Murata",            "MEMS 感測元件 / 通訊模組",                  0.03),
    Holding("6594.T",  "JP", "Nidec",             "高精度伺服馬達",                            0.03),
]


# ---------------------------------------------------------------------------
# 2. 02 篇 humanoid_robot basket（中國本體鏈為主，本檔僅引用代表性集合）
#    註：實際 02 PoC 若已建立，應由該檔 import；此處先以代表性樣本陣列模擬。
# ---------------------------------------------------------------------------
HUMANOID_ROBOT_BASKET_02: list[Holding] = [
    Holding("2308.TW", "TW", "台達電 Delta",     "伺服馬達 / 控制器（02 與 07 共有核心）",    0.10),
    Holding("1597.TW", "TW", "直得 Chieftek",    "線性滑軌 / 直線傳動",                       0.08),
    Holding("4533.TW", "TW", "全鋒 Quanfeng",    "減速器 / 機構件",                           0.06),
    Holding("6230.TW", "TW", "尼得科超眾",       "散熱模組（Nidec 集團）",                    0.05),
    Holding("002472.SZ", "CN", "雙環傳動",       "RV 減速器 — Unitree/AgiBot 鏈",             0.15),
    Holding("300124.SZ", "CN", "匯川技術",       "伺服馬達 / 變頻控制 — 中國頭部",            0.15),
    Holding("688041.SH", "CN", "海光信息",       "AI 運算（國產替代）",                       0.10),
    Holding("UBTECH",   "HK", "優必選",          "中國人形機器人 OEM",                        0.10),
    Holding("NVDA",     "US", "NVIDIA",          "上游 AI 平台（02 與 07 共有錨）",           0.10),
    Holding("6594.T",   "JP", "Nidec",           "馬達（02 與 07 共有）",                     0.06),
    Holding("6981.T",   "JP", "Murata",          "感測（02 與 07 共有）",                     0.05),
]


# ---------------------------------------------------------------------------
# 3. 集合運算工具
# ---------------------------------------------------------------------------
def _ticker_set(basket: list[Holding]) -> set[str]:
    return {h.ticker for h in basket}


def intersection(a: list[Holding], b: list[Holding]) -> list[Holding]:
    """共有持股 — 跨主題的核心受惠標的。"""
    common = _ticker_set(a) & _ticker_set(b)
    return [h for h in a if h.ticker in common]


def difference(a: list[Holding], b: list[Holding]) -> list[Holding]:
    """A 獨有 — 純 a 主題 alpha。"""
    only_a = _ticker_set(a) - _ticker_set(b)
    return [h for h in a if h.ticker in only_a]


def overlap_ratio(a: list[Holding], b: list[Holding]) -> float:
    """Jaccard overlap = |A∩B| / |A∪B|。"""
    sa, sb = _ticker_set(a), _ticker_set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# 4. 報表輸出
# ---------------------------------------------------------------------------
def print_basket(name: str, basket: list[Holding]) -> None:
    print(f"\n=== {name}  (n={len(basket)}, sum_w={sum(h.weight for h in basket):.2f}) ===")
    print(f"{'Ticker':<11}{'Mkt':<5}{'Name':<22}{'Weight':>8}  Role")
    print("-" * 100)
    for h in sorted(basket, key=lambda x: -x.weight):
        print(f"{h.ticker:<11}{h.market:<5}{h.name:<22}{h.weight:>8.2%}  {h.role}")


def main() -> None:
    print_basket("07 NVIDIA Robotics Ecosystem Basket", NVIDIA_ROBOTICS_BASKET)
    print_basket("02 Humanoid Robot Basket (sample)",   HUMANOID_ROBOT_BASKET_02)

    print("\n=== Cross-basket analysis ===")
    inter = intersection(NVIDIA_ROBOTICS_BASKET, HUMANOID_ROBOT_BASKET_02)
    only_07 = difference(NVIDIA_ROBOTICS_BASKET, HUMANOID_ROBOT_BASKET_02)
    only_02 = difference(HUMANOID_ROBOT_BASKET_02, NVIDIA_ROBOTICS_BASKET)
    ratio = overlap_ratio(NVIDIA_ROBOTICS_BASKET, HUMANOID_ROBOT_BASKET_02)

    print(f"Jaccard overlap : {ratio:.1%}")
    print(f"Common holdings ({len(inter)}): " + ", ".join(h.ticker for h in inter))
    print(f"Only-in-07 ({len(only_07)})    : " + ", ".join(h.ticker for h in only_07))
    print(f"Only-in-02 ({len(only_02)})    : " + ", ".join(h.ticker for h in only_02))

    print("\nInterpretation:")
    print(" - Common  → 雙主題核心（NVDA + 馬達/感測 通用基礎建設）")
    print(" - Only-07 → 純 NVIDIA 生態 alpha（IPC / 伺服器代工 / Jetson 整合）")
    print(" - Only-02 → 純本體鏈 alpha（中國 OEM、減速器、滑軌）")
    print(" - 建議：以 only_07 - only_02 作 long/short pair 觀察生態 vs 本體相對強度。")


if __name__ == "__main__":
    main()
