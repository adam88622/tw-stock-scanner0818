"""
08_empirical.py
================
Local-Ollama proxy benchmark for tw-stock-scanner LLM feasibility (task 08).

Three scenarios per the parent MD:
  A) news sentiment classification  (8 cases)
  B) institutional buy/sell narrative summarisation (3 cases)
  C) factor-explanation reasoning (3 cases)

We CANNOT exercise DeepSeek-R1 / Qwen3-235B / Claude (no API keys),
so we use ollama-served local proxies as a *capability lower bound*:
  - qwen3.5:latest   (~9.7B, Q4_K_M)  -> proxy for "Qwen family on TW news"
  - phi3:3.8b        (~3.8B, Q4_0)    -> small-model floor reference

Outputs:
  - prints summary table to stdout
  - writes 08_empirical_raw.json next to this script
  - the calling task will hand-write 08_empirical_results.md from the JSON

Author: GiS quant research / 2026-04-28
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = ["qwen3.5:latest", "phi3:3.8b"]
OUT_DIR = Path(__file__).parent
RAW_OUT = OUT_DIR / "08_empirical_raw.json"

# Cloud pricing (USD per 1M tokens, from MD section 2.1)
PRICING = {
    "claude-sonnet-4-6":  {"input": 3.00, "output": 15.00},
    "deepseek-reasoner":  {"input": 0.55, "output":  2.19},
    "qwen3-235b-a22b":    {"input": 0.26, "output":  1.20},
    "qwq-32b":            {"input": 0.15, "output":  0.60},
}

# ---------------------------------------------------------------------------
# Hardcoded benchmark — 14 items with ground truth
# ---------------------------------------------------------------------------
# sentiment: -1 / 0 / +1
# task: "sentiment" | "narrative" | "factor"
# keywords: list of keywords expected in any reasonable summary/answer

BENCHMARK = [
    # ---------- A. 一般新聞 8 條 ----------
    {
        "id": "A1",
        "task": "sentiment",
        "text": "鴻海 (2317) 公布 Q1 EPS 達 3.12 元，年增 28%，超越市場預期 2.85 元；公司同步調升 2026 全年資本支出至 1,800 億元，主要投入 AI 伺服器產線擴建。",
        "sentiment": +1,
        "keywords": ["EPS", "超預期", "資本支出", "AI"],
    },
    {
        "id": "A2",
        "task": "sentiment",
        "text": "聯電 (2303) 法說會表示，Q2 晶圓出貨量預估較 Q1 下滑 3-5%，產能稼動率由 78% 降至 72%，主因消費型 IC 庫存去化尚未完成。",
        "sentiment": -1,
        "keywords": ["稼動率", "下滑", "庫存"],
    },
    {
        "id": "A3",
        "task": "sentiment",
        "text": "台積電 (2330) 宣布 N2 製程於 2025 Q4 進入量產，初期良率突破 70%，蘋果與輝達已下單包下首年 60% 產能。",
        "sentiment": +1,
        "keywords": ["N2", "量產", "良率", "蘋果", "輝達"],
    },
    {
        "id": "A4",
        "task": "sentiment",
        "text": "長榮海運 (2603) 公告 4 月營收 218 億元，月減 12%、年減 8%，紅海運價回落速度超過預期，公司下修 Q2 毛利率指引至 18-22%（原 25-28%）。",
        "sentiment": -1,
        "keywords": ["營收", "下修", "毛利率"],
    },
    {
        "id": "A5",
        "task": "sentiment",
        "text": "中鋼 (2002) 董事會通過配發現金股利 0.5 元，殖利率約 1.8%，股利政策維持穩定，公司表示鋼價已落底，預期 H2 將溫和回升。",
        "sentiment": 0,
        "keywords": ["股利", "穩定", "落底"],
    },
    {
        "id": "A6",
        "task": "sentiment",
        "text": "金管會公告 2026 年第二季 ETF 發行新規上路，要求基金公司加強流動性風險揭露，業內人士認為對既有 ETF 影響有限，主要為合規調整。",
        "sentiment": 0,
        "keywords": ["金管會", "ETF", "合規", "影響有限"],
    },
    {
        "id": "A7",
        "task": "sentiment",
        "text": "華碩 (2357) 第一季 AI PC 出貨量達 85 萬台，市佔躍居全球第三，毛利率 16.8% 為近八季新高，公司樂觀看待 Copilot+ PC 換機潮延續至 2027。",
        "sentiment": +1,
        "keywords": ["AI PC", "市佔", "毛利率", "新高"],
    },
    {
        "id": "A8",
        "task": "sentiment",
        "text": "南亞科 (2408) 受 DRAM 現貨價單月下跌 7% 影響，4 月營收月減 9%，公司表示伺服器 DRAM 需求仍穩，但消費端能見度不佳。",
        "sentiment": -1,
        "keywords": ["DRAM", "下跌", "營收", "月減"],
    },

    # ---------- B. 法人買賣超敘事 3 條 ----------
    {
        "id": "B1",
        "task": "narrative",
        "text": "外資連 5 個交易日買超台積電 (2330) 累計 2.1 萬張，買超金額逾 230 億元，同期間賣超聯發科 (2454) 8,500 張、鴻海 (2317) 6,200 張，呈現明顯的權值股輪動。",
        "sentiment": +1,
        "keywords": ["外資", "買超", "台積電", "輪動"],
    },
    {
        "id": "B2",
        "task": "narrative",
        "text": "投信 4 月最後一週賣超中小型生技股逾 12,000 張，包括保瑞 (6472)、藥華藥 (6446)、台康生技 (6589)；同期買超金融股龍頭富邦金 (2881)、國泰金 (2882) 合計 9,800 張，顯示防禦性類股輪動。",
        "sentiment": 0,
        "keywords": ["投信", "賣超", "生技", "金融", "防禦"],
    },
    {
        "id": "B3",
        "task": "narrative",
        "text": "三大法人合計賣超航運股單週超過 4 萬張，長榮 (2603)、陽明 (2609)、萬海 (2615) 同步遭外資與投信減碼，運價走弱與 Q2 毛利率下修是主因。",
        "sentiment": -1,
        "keywords": ["法人", "賣超", "航運", "運價"],
    },

    # ---------- C. 因子解釋 3 條（需推理）----------
    {
        "id": "C1",
        "task": "factor",
        "text": "請解釋 Beta-Adjusted Momentum 因子在 2024 Q3 台股失效的可能原因。請至少列出三點，並說明因子在何種市場結構下會回歸。",
        "sentiment": 0,
        "keywords": ["市場結構", "風格輪動", "波動", "反轉", "流動性"],
    },
    {
        "id": "C2",
        "task": "factor",
        "text": "若一個低波動因子 (Low Volatility) 在過去 12 個月夏普值 0.3、最大回撤 -18%，但同期 0050 夏普 0.9，請判斷此因子是否仍具配置價值，並說明檢驗步驟。",
        "sentiment": 0,
        "keywords": ["夏普", "回撤", "檢驗", "回歸", "顯著"],
    },
    {
        "id": "C3",
        "task": "factor",
        "text": "在台股法說會逐字稿之上建構 NLP 情感因子，請說明：(1) 標籤如何定義；(2) 主要 confounder；(3) 與既有 Earnings Surprise 因子如何避免共線性。",
        "sentiment": 0,
        "keywords": ["標籤", "confounder", "共線", "正交", "回歸"],
    },
]

PROMPT_SENTIMENT = """你是台股研究員。請對下列新聞輸出 JSON：
{{"sentiment_score": <-1.0~+1.0>, "summary_zh": [<不超過 3 句繁中摘要>]}}

新聞：{text}

僅輸出 JSON。"""

PROMPT_NARRATIVE = """你是台股研究員，請對以下法人買賣超敘事輸出 JSON：
{{"sentiment_score": <-1~+1>, "summary_zh": [<2-3 句敘事摘要>]}}

內容：{text}

僅輸出 JSON。"""

PROMPT_FACTOR = """你是台股量化研究員，請對下列問題給出條列式推理回答（不要 JSON），至少 3 個論點，每點 1-2 句：

問題：{text}

請用繁體中文。"""


# ---------------------------------------------------------------------------
@dataclass
class ItemResult:
    item_id: str
    model: str
    task: str
    raw: str = ""
    parsed_sentiment: float | None = None
    parsed_summary: list = field(default_factory=list)
    latency_s: float = 0.0
    prompt_chars: int = 0
    response_chars: int = 0
    keyword_hits: int = 0
    keyword_total: int = 0
    sentiment_correct: bool | None = None
    reasoning_points: int = 0
    error: str = ""


def call_ollama(
    model: str,
    prompt: str,
    num_predict: int = 512,
    think: bool = False,
) -> tuple[str, float, str]:
    """Call ollama generate API; return (text, latency_seconds, thinking).

    qwen3.5 is a hybrid-thinking model — by default it emits chain-of-thought
    into a separate ``thinking`` field, leaving ``response`` empty when the
    num_predict budget is exhausted before reasoning ends. For structured
    sentiment/JSON tasks we disable thinking; for factor reasoning we keep it
    on (and merge thinking + response).
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
        },
    }
    # only pass `think` to models that support it (qwen3.5 hybrid).
    # phi3 returns 400 on unknown flag.
    if model.startswith("qwen"):
        payload["think"] = think
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    dt = time.perf_counter() - t0
    return body.get("response", "") or "", dt, body.get("thinking", "") or ""


def strip_thinking(txt: str) -> str:
    """remove <think>...</think> blocks (qwen3.5 reasoning prefix)."""
    txt = re.sub(r"<think>[\s\S]*?</think>", "", txt, flags=re.IGNORECASE).strip()
    # also handle dangling open <think> with no close
    if "<think>" in txt.lower() and "</think>" not in txt.lower():
        # everything after the last newline of the prefix
        parts = txt.split("\n")
        # find first line that looks like JSON or numbered list
        for i, line in enumerate(parts):
            s = line.strip()
            if s.startswith("{") or re.match(r"^[1-9一二三四五六七八九十].", s):
                txt = "\n".join(parts[i:])
                break
    return txt.strip()


def extract_json(txt: str) -> dict | None:
    txt = strip_thinking(txt)
    # remove ``` fences
    txt = re.sub(r"^```(json)?", "", txt.strip(), flags=re.IGNORECASE).strip()
    txt = re.sub(r"```$", "", txt.strip()).strip()
    # find first { ... last }
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        # last-resort: try to pull sentiment_score with regex
        sm = re.search(r"sentiment_score['\"]?\s*[:=]\s*(-?\d+(?:\.\d+)?)", txt)
        if sm:
            return {"sentiment_score": float(sm.group(1)), "summary_zh": []}
        return None


def score_sentiment(pred: float | None, gt: int) -> bool | None:
    if pred is None:
        return None
    # bucket prediction
    if pred >= 0.25:
        bucket = +1
    elif pred <= -0.25:
        bucket = -1
    else:
        bucket = 0
    return bucket == gt


def count_keyword_hits(text: str, keywords: list[str]) -> tuple[int, int]:
    if not text:
        return 0, len(keywords)
    hits = sum(1 for k in keywords if k.lower() in text.lower())
    return hits, len(keywords)


def count_reasoning_points(text: str) -> int:
    """rough heuristic: count numbered / bulleted points."""
    if not text:
        return 0
    text = strip_thinking(text)
    patterns = [
        r"^\s*\d+[\.\)、]",                # 1. 1) 1、
        r"^\s*[一二三四五六七八九十][\.、)]",   # 一. 一、
        r"^\s*[\-•·\*]",                     # -, •, *
        r"^\s*\([1-9一二三]\)",              # (1) (一)
    ]
    count = 0
    for line in text.split("\n"):
        for p in patterns:
            if re.match(p, line):
                count += 1
                break
    return count


def run() -> list[dict]:
    all_results: list[ItemResult] = []
    for item in BENCHMARK:
        if item["task"] == "sentiment":
            prompt = PROMPT_SENTIMENT.format(text=item["text"])
        elif item["task"] == "narrative":
            prompt = PROMPT_NARRATIVE.format(text=item["text"])
        else:
            prompt = PROMPT_FACTOR.format(text=item["text"])

        for model in MODELS:
            r = ItemResult(item_id=item["id"], model=model, task=item["task"])
            r.prompt_chars = len(prompt)
            print(f"[{item['id']}] {model} ... ", end="", flush=True)
            # think only for factor task; structured JSON tasks: think=False
            think_flag = (item["task"] == "factor")
            np_budget = 1500 if think_flag else 600
            try:
                txt, dt, thinking = call_ollama(
                    model, prompt, num_predict=np_budget, think=think_flag
                )
                # for factor tasks, prepend thinking content if response is empty
                if item["task"] == "factor" and not txt.strip() and thinking.strip():
                    txt = thinking
                r.raw = txt
                r.latency_s = dt
                r.response_chars = len(txt)
            except Exception as e:
                r.error = f"call failed: {e}"
                print(f"ERR {e}")
                all_results.append(r)
                continue

            if item["task"] in ("sentiment", "narrative"):
                obj = extract_json(txt)
                if obj is None:
                    r.error = "json parse failed"
                else:
                    try:
                        r.parsed_sentiment = float(obj.get("sentiment_score", 0))
                    except Exception:
                        r.parsed_sentiment = None
                    summary = obj.get("summary_zh", [])
                    if isinstance(summary, str):
                        summary = [summary]
                    r.parsed_summary = list(summary)[:3]
                r.sentiment_correct = score_sentiment(r.parsed_sentiment, item["sentiment"])
                summary_text = " ".join(r.parsed_summary) if r.parsed_summary else strip_thinking(txt)
                hits, total = count_keyword_hits(summary_text, item["keywords"])
                r.keyword_hits = hits
                r.keyword_total = total
            else:  # factor
                clean = strip_thinking(txt)
                hits, total = count_keyword_hits(clean, item["keywords"])
                r.keyword_hits = hits
                r.keyword_total = total
                r.reasoning_points = count_reasoning_points(clean)

            print(f"{dt:.1f}s ok")
            all_results.append(r)

    return [asdict(x) for x in all_results]


def aggregate(results: list[dict]) -> dict:
    by_model: dict[str, dict] = {m: {
        "sentiment_total": 0,
        "sentiment_correct": 0,
        "keyword_hits": 0,
        "keyword_total": 0,
        "reasoning_points_sum": 0,
        "reasoning_items": 0,
        "latency_sum": 0.0,
        "latency_n": 0,
        "errors": 0,
        "response_chars_sum": 0,
    } for m in MODELS}

    for r in results:
        m = r["model"]
        b = by_model[m]
        if r["error"]:
            b["errors"] += 1
        b["latency_sum"] += r["latency_s"]
        b["latency_n"] += 1
        b["response_chars_sum"] += r["response_chars"]
        if r["task"] in ("sentiment", "narrative") and r["sentiment_correct"] is not None:
            b["sentiment_total"] += 1
            if r["sentiment_correct"]:
                b["sentiment_correct"] += 1
        b["keyword_hits"] += r["keyword_hits"]
        b["keyword_total"] += r["keyword_total"]
        if r["task"] == "factor":
            b["reasoning_items"] += 1
            b["reasoning_points_sum"] += r["reasoning_points"]

    for m, b in by_model.items():
        b["sentiment_acc"] = (b["sentiment_correct"] / b["sentiment_total"]) if b["sentiment_total"] else 0.0
        b["keyword_coverage"] = (b["keyword_hits"] / b["keyword_total"]) if b["keyword_total"] else 0.0
        b["avg_latency_s"] = (b["latency_sum"] / b["latency_n"]) if b["latency_n"] else 0.0
        b["avg_reasoning_points"] = (b["reasoning_points_sum"] / b["reasoning_items"]) if b["reasoning_items"] else 0.0
    return by_model


def cloud_cost_estimate() -> dict:
    """Monthly cost assuming 100 news/day, in=220 tok, out=110 tok per call."""
    daily_in = 100 * 220
    daily_out = 100 * 110
    monthly_in = daily_in * 30
    monthly_out = daily_out * 30
    out = {"monthly_in_tokens": monthly_in, "monthly_out_tokens": monthly_out, "by_model": {}}
    for k, p in PRICING.items():
        cost = (monthly_in * p["input"] + monthly_out * p["output"]) / 1_000_000
        out["by_model"][k] = round(cost, 4)
    return out


def main() -> None:
    print(f"Running {len(BENCHMARK)} items x {len(MODELS)} models = {len(BENCHMARK) * len(MODELS)} calls\n")
    results = run()
    agg = aggregate(results)
    cost = cloud_cost_estimate()

    payload = {
        "results": results,
        "aggregate": agg,
        "cloud_cost_estimate_monthly_usd": cost,
        "benchmark_items": BENCHMARK,
        "models": MODELS,
    }
    RAW_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Aggregate ===")
    for m, b in agg.items():
        print(f"\n[{m}]")
        print(f"  sentiment accuracy : {b['sentiment_correct']}/{b['sentiment_total']} = {b['sentiment_acc']:.1%}")
        print(f"  keyword coverage   : {b['keyword_hits']}/{b['keyword_total']} = {b['keyword_coverage']:.1%}")
        print(f"  avg latency        : {b['avg_latency_s']:.2f}s")
        print(f"  avg reasoning pts  : {b['avg_reasoning_points']:.2f}")
        print(f"  errors             : {b['errors']}")
        print(f"  avg resp chars     : {b['response_chars_sum'] / max(b['latency_n'], 1):.0f}")

    print("\n=== Cloud cost (USD/month, 100 news/day) ===")
    for k, v in cost["by_model"].items():
        print(f"  {k:<22} {v:>8.2f}")

    print(f"\nRaw JSON saved: {RAW_OUT}")


if __name__ == "__main__":
    main()
