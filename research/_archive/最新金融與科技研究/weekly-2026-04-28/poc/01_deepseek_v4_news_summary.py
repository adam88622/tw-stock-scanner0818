"""
DeepSeek V4 台股新聞摘要 PoC — GiS Genesis International Capital

API key 取得：platform.deepseek.com 註冊 → 儲值 ≥ USD $2 → Create API key (sk-xxxx)
環境變數：  Bash: export DEEPSEEK_API_KEY="sk-xxxx"   PS: $env:DEEPSEEK_API_KEY="sk-xxxx"
執行：      python 01_deepseek_v4_news_summary.py --text "..."
            python 01_deepseek_v4_news_summary.py --file news.txt --model deepseek-v4-pro
依賴：openai>=1.0.0（DeepSeek 與 OpenAI SDK 完全相容）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

from openai import OpenAI, APIError, APITimeoutError, RateLimitError

# DeepSeek 官方 OpenAI-compatible endpoint (V4 預覽版自動路由)
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# V4 定價（USD per 1M tokens；2026-04-24 公告值）
PRICING = {
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro":   {"input": 1.74, "output": 3.48},  # 促銷期 input 可低至 0.036
}

SYSTEM_PROMPT = """你是 GiS 量化研究室的台股新聞分析師。對輸入新聞做結構化摘要，僅輸出單一 JSON 物件（無 markdown fence）。
schema:
{
  "companies":[{"name":"台積電","ticker":"2330"}],   // 最多 5 家
  "event_type":"財報|法說|M&A|新產品|政策|訴訟|人事|其他",
  "summary_zh":"三句以內繁中摘要",
  "impact":{"scope":"個股|產業|大盤","direction":"正面|負面|中性","industries":["半導體"]},
  "sentiment_score": -1.0~1.0,
  "confidence": 0.0~1.0,
  "key_terms":["關鍵詞"]
}"""


@dataclass
class SummaryResult:
    payload: dict
    input_tokens: int
    output_tokens: int
    model: str
    latency_s: float

    def cost_usd(self) -> float:
        p = PRICING.get(self.model, PRICING["deepseek-v4-flash"])
        return (self.input_tokens * p["input"] + self.output_tokens * p["output"]) / 1_000_000


def build_client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        sys.exit("ERROR: 環境變數 DEEPSEEK_API_KEY 未設定。請見檔案頂端註解。")
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def summarize_news(
    client: OpenAI,
    text: str,
    model: str = "deepseek-v4-flash",
    max_retries: int = 3,
    base_backoff: float = 2.0,
) -> SummaryResult:
    """呼叫 DeepSeek V4，內建指數退避 retry。"""
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"以下是台股新聞，請依 schema 輸出 JSON：\n\n{text}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=800,
                timeout=60,
            )
            latency = time.time() - t0
            content = resp.choices[0].message.content or "{}"
            payload = json.loads(content)
            usage = resp.usage
            return SummaryResult(
                payload=payload,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                model=model,
                latency_s=latency,
            )
        except (APITimeoutError, RateLimitError, APIError) as e:
            last_err = e
            if attempt == max_retries:
                break
            sleep_s = base_backoff ** attempt
            print(f"[retry {attempt}/{max_retries}] {type(e).__name__}: {e}; sleep {sleep_s}s",
                  file=sys.stderr)
            time.sleep(sleep_s)
        except json.JSONDecodeError as e:
            last_err = e
            print(f"[retry {attempt}/{max_retries}] JSON parse failed: {e}", file=sys.stderr)
            if attempt == max_retries:
                break
            time.sleep(base_backoff)
    raise RuntimeError(f"summarize_news failed after {max_retries} attempts: {last_err}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DeepSeek V4 台股新聞結構化摘要 PoC")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="直接傳入新聞文本")
    src.add_argument("--file", help="新聞文本檔案路徑（UTF-8）")
    p.add_argument("--model", default="deepseek-v4-flash",
                   choices=list(PRICING.keys()), help="預設 deepseek-v4-flash")
    p.add_argument("--retries", type=int, default=3)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    news_text = args.text if args.text else open(args.file, "r", encoding="utf-8").read()
    if not news_text.strip():
        sys.exit("ERROR: 新聞文本為空。")

    client = build_client()
    result = summarize_news(client, news_text, model=args.model, max_retries=args.retries)

    print("=" * 60)
    print(f"模型：{result.model}   延遲：{result.latency_s:.2f}s")
    print(f"Tokens — input: {result.input_tokens}  output: {result.output_tokens}")
    print(f"成本：USD ${result.cost_usd():.6f}")
    print("=" * 60)
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
