"""
01_empirical.py — DeepSeek V4 PoC 的本機 ollama 替代實證

由於 DeepSeek V4 / Claude API 暫無 key，先用本機 ollama 跑相同 prompt schema，
量測：(a) 兩本機模型 (qwen3.5:9.7B / phi3:3.8B) 對台股新聞摘要的品質
      (b) JSON 結構化輸出穩定度
      (c) 延遲與輸出 token 數
      (d) 對映雲端 V4-Flash / Claude Sonnet 的成本估算
      (e) 兩模型情感分數一致性 (Pearson + 方向同意率)

ollama HTTP API: http://localhost:11434/api/generate
模型已備：qwen3.5:latest (9.7B Q4_K_M, 6.6 GB) / phi3:3.8b (Q4_0, 2.2 GB)

執行：
    "C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe" 01_empirical.py
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = ["qwen3.5:latest", "phi3:3.8b"]

# 雲端對標定價 (USD per 1M tokens)
PRICING = {
    "deepseek-v4-flash": {"in": 0.14,  "out": 0.28},
    "deepseek-v4-pro":   {"in": 1.74,  "out": 3.48},
    "claude-sonnet-4.6": {"in": 3.00,  "out": 15.00},
    "claude-opus-4.6":   {"in": 15.00, "out": 75.00},
}

# 台股新聞語料 (2026-04-28 當日台股相關新聞，類比 cnyes / 經濟日報常見頭條型態)
NEWS_SAMPLES: list[dict[str, str]] = [
    {
        "id": "N1",
        "title": "台積電法說財測上修 Q2 營收估季增 8-10%",
        "text": (
            "台積電 (2330) 今日召開法人說明會，CEO 魏哲家表示，AI 加速器與高階手機晶片"
            "需求強勁，第二季合併營收將達 290-295 億美元，季增 8-10%，毛利率區間 58-60%。"
            "公司同時上修 2026 全年資本支出至 460 億美元，較先前指引提高約 5%。"
            "受財測利多激勵，台積電 ADR 隔夜大漲 4.2%，台股盤前期貨同步走高。"
        ),
    },
    {
        "id": "N2",
        "title": "鴻海印度廠擴產 蘋果 iPhone 17 在地化加速",
        "text": (
            "鴻海 (2317) 證實將投資 12 億美元擴建印度坦米爾納都廠區，新增 6 條 iPhone 17"
            "組裝線，預計 2026 年第三季量產。法人指出此舉有助分散地緣風險、降低關稅成本，"
            "預估 2027 年印度廠占鴻海 iPhone 總出貨比重將從目前 18% 提升至 30%。"
            "鴻海早盤股價跳空高開 2.5%，攻上 215 元。"
        ),
    },
    {
        "id": "N3",
        "title": "聯發科天璣 9500 出貨不如預期 高通競爭加劇",
        "text": (
            "聯發科 (2454) 第一季財報雖優於市場預期，但管理層在電話會議坦言旗艦級天璣 9500"
            "在中國 OEM 客戶端面臨高通 Snapdragon 8 Gen 5 的強烈競爭，第二季智慧手機 SoC"
            "出貨將季減低個位數。摩根大通隨後將聯發科目標價自 1,450 元下修至 1,280 元，"
            "維持「中性」評等。聯發科今日股價開低走低，盤中跌 3.8%。"
        ),
    },
    {
        "id": "N4",
        "title": "金管會核准國泰金併購安泰銀 創純民營金控併購最大案",
        "text": (
            "金管會今日正式核准國泰金 (2882) 以 580 億元現金加換股方式併購安泰銀行 (2849)，"
            "創下台灣純民營金控併購規模新高。合併後國泰世華銀分行數將達 215 家、"
            "資產規模突破 4.8 兆元，市占率躍升至國內第三。國泰金董座李長庚表示，"
            "併購綜效預計 2027 年顯現，目標 ROE 提升至 12% 以上。"
        ),
    },
    {
        "id": "N5",
        "title": "長榮海運 Q1 EPS 6.8 元優於預期 紅海航線運價回升",
        "text": (
            "長榮 (2603) 公布第一季財報，稅後純益 184.6 億元，每股盈餘 6.8 元，優於市場"
            "預期的 5.9 元。紅海地緣局勢未解、葉門胡塞武裝持續攻擊商船，迫使主要航商繞行"
            "好望角，推升歐美主力航線運價約 28%。長榮宣布配發每股 5.5 元現金股利，"
            "殖利率約 4.6%，今日股價漲幅達 5.2% 收 121 元。"
        ),
    },
    {
        "id": "N6",
        "title": "華碩 AI PC 出貨量達標 Copilot+ 機種市占衝第三",
        "text": (
            "華碩 (2357) 今日於投資人說明會表示，2026 年首季 AI PC 出貨量達 95 萬台，"
            "達成原訂目標，其中搭載 Copilot+ 認證的機種市占率排名全球第三，僅次於聯想與惠普。"
            "公司同時宣布與 NVIDIA 合作推出搭載 RTX 5070 行動版 GPU 的高階創作者筆電，"
            "預計第三季在歐美、亞太同步上市。華碩股價今日小漲 1.3% 收 530 元。"
        ),
    },
    {
        "id": "N7",
        "title": "中華電信 5G 用戶突破 800 萬 ARPU 連 6 季成長",
        "text": (
            "中華電 (2412) 公布 3 月營運數據，5G 用戶數正式突破 800 萬戶滲透率達 64%，"
            "ARPU 連續 6 季維持成長並達到 698 元。Q1 EBITDA 利潤率 38.4%，年增 0.6 個百分點。"
            "公司亦宣布跟進台日海纜計畫，將投資 21 億元拉設新海纜，提升國際頻寬韌性。"
            "中華電股價持平於 138 元，配息政策可望維持高殖利率。"
        ),
    },
    {
        "id": "N8",
        "title": "陽明海運遭歐盟反壟斷調查 股價重挫 7%",
        "text": (
            "陽明 (2609) 確認收到歐盟競爭總署 (DG-COMP) 的正式調查通知，涉嫌與其他亞洲"
            "航商在歐線聯營協議中協同訂價。若違規屬實，最高可處全球年營收 10% 罰款，"
            "粗估上限約 60 億元台幣。陽明回應將全力配合調查並否認違規。"
            "今日股價開盤即重挫 7.1%，盤中一度跌停，三大法人合計賣超逾 2.4 萬張。"
        ),
    },
]

SYSTEM_PROMPT = """你是台股新聞分析師。對輸入新聞輸出單一 JSON 物件，不要任何 markdown 包覆、不要解釋、不要前後文字。
schema:
{
  "company": "主要公司名稱",
  "ticker": "證券代號(數字字串)",
  "event_type": "財報|法說|M&A|新產品|政策|訴訟|人事|其他",
  "sentiment": -1.0~1.0 之間數字,
  "summary_3sent": "三句以內繁中摘要"
}
注意：sentiment 必須是介於 -1 與 1 的小數，正面為正、負面為負。"""


# ----------------------------- 推理呼叫 -----------------------------

@dataclass
class Inference:
    news_id: str
    model: str
    raw: str
    parsed: dict[str, Any] | None
    parse_ok: bool
    schema_ok: bool
    latency_ms: float
    eval_count: int           # 輸出 token 數 (ollama 回傳)
    prompt_eval_count: int    # 輸入 token 數
    err: str = ""


def call_ollama(model: str, prompt: str, timeout: int = 240) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "format": "json",
        "think": False,  # qwen3.5 預設 thinking 模式會把答案塞 thinking field 導致 response=""
        "options": {"temperature": 0.2, "num_predict": 400},
    }
    t0 = time.time()
    r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    latency_ms = (time.time() - t0) * 1000
    r.raise_for_status()
    body = r.json()
    body["_latency_ms"] = latency_ms
    return body


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = JSON_RE.search(text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


REQUIRED_KEYS = {"company", "ticker", "event_type", "sentiment", "summary_3sent"}


def validate(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if not REQUIRED_KEYS.issubset(payload.keys()):
        return False
    s = payload.get("sentiment")
    if not isinstance(s, (int, float)):
        return False
    if not (-1.0 <= float(s) <= 1.0):
        return False
    return True


def run_one(news: dict[str, str], model: str) -> Inference:
    prompt = f"新聞標題：{news['title']}\n新聞內容：{news['text']}\n請依 schema 輸出 JSON。"
    try:
        body = call_ollama(model, prompt)
    except Exception as e:                              # noqa: BLE001
        return Inference(
            news_id=news["id"], model=model, raw="", parsed=None,
            parse_ok=False, schema_ok=False, latency_ms=0,
            eval_count=0, prompt_eval_count=0, err=f"{type(e).__name__}: {e}",
        )
    # 萬一 think:false 仍然把內容放在 thinking (老版 ollama)，做 fallback
    raw = body.get("response") or body.get("thinking") or ""
    parsed = parse_json(raw)
    return Inference(
        news_id=news["id"], model=model, raw=raw, parsed=parsed,
        parse_ok=parsed is not None,
        schema_ok=validate(parsed),
        latency_ms=body["_latency_ms"],
        eval_count=body.get("eval_count", 0),
        prompt_eval_count=body.get("prompt_eval_count", 0),
    )


# ----------------------------- 統計 -----------------------------

def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def sign(x: float) -> int:
    if x > 0.05:
        return 1
    if x < -0.05:
        return -1
    return 0


def cost_estimate(in_tok: int, out_tok: int, model_key: str) -> float:
    p = PRICING[model_key]
    return (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000


# ----------------------------- 主流程 -----------------------------

def main() -> None:
    out_dir = Path(__file__).parent
    inferences: list[Inference] = []

    print("=" * 70)
    print(f"開始：對 {len(NEWS_SAMPLES)} 則新聞 × {len(MODELS)} 模型 = "
          f"{len(NEWS_SAMPLES) * len(MODELS)} 次推理")
    print("=" * 70)

    for news in NEWS_SAMPLES:
        for model in MODELS:
            print(f"  [{news['id']}] {model} ... ", end="", flush=True)
            inf = run_one(news, model)
            inferences.append(inf)
            status = "OK" if inf.schema_ok else ("JSON-OK" if inf.parse_ok else "FAIL")
            print(f"{status}  {inf.latency_ms:7.0f} ms  out={inf.eval_count:4d} tok"
                  + (f"  ERR={inf.err}" if inf.err else ""))

    # 把原始輸出存檔
    raw_path = out_dir / "01_empirical_raw.json"
    raw_path.write_text(
        json.dumps(
            [{
                "news_id": i.news_id, "model": i.model, "latency_ms": i.latency_ms,
                "prompt_eval_count": i.prompt_eval_count, "eval_count": i.eval_count,
                "parse_ok": i.parse_ok, "schema_ok": i.schema_ok,
                "raw": i.raw, "parsed": i.parsed, "err": i.err,
            } for i in inferences],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n原始結果寫入：{raw_path}")

    # ---------- 摘要統計 ----------
    by_model: dict[str, list[Inference]] = {m: [] for m in MODELS}
    for i in inferences:
        by_model[i.model].append(i)

    print("\n" + "=" * 70)
    print("各模型彙總")
    print("=" * 70)

    summary_rows = []
    for m, items in by_model.items():
        n = len(items)
        parse_rate  = sum(1 for x in items if x.parse_ok) / n
        schema_rate = sum(1 for x in items if x.schema_ok) / n
        latencies = [x.latency_ms for x in items if x.latency_ms > 0]
        in_toks  = [x.prompt_eval_count for x in items]
        out_toks = [x.eval_count for x in items]
        avg_lat = statistics.fmean(latencies) if latencies else 0
        p50_lat = statistics.median(latencies) if latencies else 0
        avg_in  = statistics.fmean(in_toks)  if in_toks  else 0
        avg_out = statistics.fmean(out_toks) if out_toks else 0

        # 模擬月成本 (54M input / 10M output)
        cost_flash = cost_estimate(54_000_000, 10_000_000, "deepseek-v4-flash")
        cost_pro   = cost_estimate(54_000_000, 10_000_000, "deepseek-v4-pro")
        cost_son   = cost_estimate(54_000_000, 10_000_000, "claude-sonnet-4.6")
        cost_opu   = cost_estimate(54_000_000, 10_000_000, "claude-opus-4.6")

        print(
            f"\n[{m}]\n"
            f"  JSON 解析成功率   : {parse_rate*100:5.1f}%\n"
            f"  Schema 合法率     : {schema_rate*100:5.1f}%\n"
            f"  平均延遲 (ms)     : {avg_lat:7.0f}   中位數: {p50_lat:7.0f}\n"
            f"  平均 input  token : {avg_in:6.0f}\n"
            f"  平均 output token : {avg_out:6.0f}\n"
        )
        summary_rows.append({
            "model": m, "n": n,
            "parse_rate": parse_rate, "schema_rate": schema_rate,
            "avg_lat_ms": avg_lat, "p50_lat_ms": p50_lat,
            "avg_in_tok": avg_in, "avg_out_tok": avg_out,
        })

    print("=" * 70)
    print("模擬雲端月成本 (54M input / 10M output)")
    print("=" * 70)
    print(f"  DeepSeek V4-Flash : USD ${cost_flash:8.2f}")
    print(f"  DeepSeek V4-Pro   : USD ${cost_pro:8.2f}")
    print(f"  Claude Sonnet 4.6 : USD ${cost_son:8.2f}")
    print(f"  Claude Opus 4.6   : USD ${cost_opu:8.2f}")
    print(f"  本機 ollama       : USD $    0.00 (僅電費 / GPU 折舊)")

    # ---------- 兩模型一致性 ----------
    print("\n" + "=" * 70)
    print("兩模型一致性 (qwen3.5 vs phi3)")
    print("=" * 70)
    pairs: list[tuple[float, float, str, str]] = []
    for news in NEWS_SAMPLES:
        a = next((x for x in inferences
                  if x.news_id == news["id"] and x.model == "qwen3.5:latest"), None)
        b = next((x for x in inferences
                  if x.news_id == news["id"] and x.model == "phi3:3.8b"), None)
        if a and b and a.schema_ok and b.schema_ok:
            sa = float(a.parsed["sentiment"])
            sb = float(b.parsed["sentiment"])
            pairs.append((sa, sb, news["id"], news["title"]))

    if len(pairs) >= 2:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        r = pearson(xs, ys)
        same_dir = sum(1 for x, y, *_ in pairs if sign(x) == sign(y)) / len(pairs)
        avg_diff = statistics.fmean(abs(x - y) for x, y, *_ in pairs)
        print(f"  有效配對數         : {len(pairs)} / {len(NEWS_SAMPLES)}")
        print(f"  Pearson correlation: {r:+.3f}" if r is not None else "  Pearson: N/A")
        print(f"  方向同意率         : {same_dir*100:.1f}%")
        print(f"  情感分數平均絕對差 : {avg_diff:.3f}")
    else:
        print("  有效配對 < 2，無法計算統計")

    # 寫一份 JSON 供 markdown 報告使用
    stats_path = out_dir / "01_empirical_stats.json"
    stats_path.write_text(
        json.dumps({
            "n_news": len(NEWS_SAMPLES),
            "models": MODELS,
            "summary": summary_rows,
            "pairs": [
                {"news_id": n_id, "title": title, "qwen35": sa, "phi3": sb}
                for sa, sb, n_id, title in pairs
            ],
            "consistency": {
                "n_pairs": len(pairs),
                "pearson": pearson([p[0] for p in pairs], [p[1] for p in pairs])
                           if len(pairs) >= 2 else None,
                "direction_agree_rate": (
                    sum(1 for x, y, *_ in pairs if sign(x) == sign(y)) / len(pairs)
                    if pairs else None
                ),
                "avg_abs_diff": (
                    statistics.fmean(abs(x - y) for x, y, *_ in pairs)
                    if pairs else None
                ),
            },
            "monthly_cost_usd_54M_in_10M_out": {
                "deepseek-v4-flash": cost_flash,
                "deepseek-v4-pro":   cost_pro,
                "claude-sonnet-4.6": cost_son,
                "claude-opus-4.6":   cost_opu,
                "ollama-local":      0.0,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n統計結果寫入：{stats_path}")
    print("\n完成")


if __name__ == "__main__":
    sys.exit(main())
