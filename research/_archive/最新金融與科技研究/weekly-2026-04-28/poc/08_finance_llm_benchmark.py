"""
08_finance_llm_benchmark.py
============================
台股新聞情感分析 LLM 基準測試 PoC

目的：對比 Claude / DeepSeek-R1 / Qwen3-235B 在同一條台股新聞上的：
  1. 情感分數（-1 到 +1）
  2. 三句摘要
  3. 回應時間
  4. 估算成本（USD）

執行：
  $ export ANTHROPIC_API_KEY=sk-ant-...
  $ export DEEPSEEK_API_KEY=sk-...
  $ export DASHSCOPE_API_KEY=sk-...
  $ python 08_finance_llm_benchmark.py

缺失任何 KEY 將自動以 mock 模式跳過該模型。
依賴：openai>=1.30, anthropic>=0.40 （皆為 OpenAI-compat 或原生 SDK）

作者：GiS 量化研究 / 2026-04-28
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 1. 測試樣本（單條台股新聞）
# ---------------------------------------------------------------------------
SAMPLE_NEWS = {
    "headline": "台積電法說會釋出 2026 全年資本支出上修至 480 億美元，AI 需求強勁",
    "body": (
        "台積電 (2330) 今日召開第一季法說會，董事長魏哲家表示，受惠於 AI "
        "晶片訂單能見度延伸至 2027 年，公司將 2026 年全年資本支出由原估 "
        "420 億美元上修至 480 億美元，主要投入 N2 及 A16 製程擴產。第一季 "
        "毛利率達 59.8%，優於市場預期 58.5%，每股盈餘 16.42 元創同期新高。"
        "公司同時上調全年營收年增率指引至 28%（原為 22%）。"
    ),
}

# 統一的 prompt 模板，要求結構化 JSON 輸出
PROMPT_TEMPLATE = """你是台股研究員，請對以下新聞進行分析，僅回傳 JSON：

{{
  "sentiment_score": <-1.0 到 1.0 的浮點數，正數=利多，負數=利空>,
  "summary_zh": [<三句繁中摘要，每句 30 字內>]
}}

新聞標題：{headline}
新聞內文：{body}

僅輸出 JSON，不要其他文字、不要 markdown 區塊。"""

# ---------------------------------------------------------------------------
# 2. 模型定價表（USD per 1M tokens, 2026-04 資料）
# ---------------------------------------------------------------------------
PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},   # DeepSeek-R1
    "qwen3-235b-a22b":   {"input": 0.26, "output": 1.20},   # DashScope 約估
}


# ---------------------------------------------------------------------------
# 3. 統一回應結構
# ---------------------------------------------------------------------------
@dataclass
class LLMResult:
    model: str
    sentiment: Optional[float] = None
    summary: list = field(default_factory=list)
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: str = ""
    error: Optional[str] = None
    mocked: bool = False

    def calc_cost(self) -> None:
        p = PRICING.get(self.model)
        if p:
            self.cost_usd = (
                self.input_tokens * p["input"] / 1_000_000
                + self.output_tokens * p["output"] / 1_000_000
            )


# ---------------------------------------------------------------------------
# 4. 通用 client wrapper（OpenAI-compatible 介面 + Anthropic 原生）
# ---------------------------------------------------------------------------
def call_claude(prompt: str) -> LLMResult:
    """呼叫 Anthropic Claude Sonnet 4.6（原生 SDK）"""
    res = LLMResult(model="claude-sonnet-4-6")
    if not os.getenv("ANTHROPIC_API_KEY"):
        res.mocked = True
        res.error = "ANTHROPIC_API_KEY 未設定，跳過"
        return res
    try:
        from anthropic import Anthropic
        client = Anthropic()
        t0 = time.perf_counter()
        msg = client.messages.create(
            model="claude-sonnet-4-6-20260217",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        res.latency_s = time.perf_counter() - t0
        res.raw = msg.content[0].text
        res.input_tokens = msg.usage.input_tokens
        res.output_tokens = msg.usage.output_tokens
    except Exception as e:
        res.error = f"Claude 呼叫失敗：{e}"
    return res


def call_openai_compat(
    model_key: str,
    api_model_id: str,
    base_url: str,
    api_key_env: str,
    prompt: str,
) -> LLMResult:
    """通用 OpenAI-compatible 介面（DeepSeek、DashScope 皆支援）"""
    res = LLMResult(model=model_key)
    api_key = os.getenv(api_key_env)
    if not api_key:
        res.mocked = True
        res.error = f"{api_key_env} 未設定，跳過"
        return res
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=api_model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.1,
        )
        res.latency_s = time.perf_counter() - t0
        res.raw = resp.choices[0].message.content or ""
        res.input_tokens = resp.usage.prompt_tokens
        res.output_tokens = resp.usage.completion_tokens
    except Exception as e:
        res.error = f"{model_key} 呼叫失敗：{e}"
    return res


# ---------------------------------------------------------------------------
# 5. 解析 JSON 回應
# ---------------------------------------------------------------------------
def parse_response(res: LLMResult) -> None:
    """從 raw 字串解析 sentiment_score 與 summary_zh"""
    if not res.raw:
        return
    txt = res.raw.strip()
    # 推理型模型可能含 <think> 區塊，移除之
    if "</think>" in txt:
        txt = txt.split("</think>", 1)[1].strip()
    # 移除 markdown code fence
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:].strip()
    try:
        data = json.loads(txt)
        res.sentiment = float(data.get("sentiment_score", 0))
        res.summary = list(data.get("summary_zh", []))[:3]
    except Exception as e:
        res.error = f"JSON 解析失敗：{e}; raw[:120]={txt[:120]!r}"


# ---------------------------------------------------------------------------
# 6. Mock 模式（無 API key 時用，提供合理的展示資料）
# ---------------------------------------------------------------------------
def fill_mock(res: LLMResult) -> None:
    """無 API key 時填入示意資料"""
    res.sentiment = {
        "claude-sonnet-4-6": 0.85,
        "deepseek-reasoner": 0.82,
        "qwen3-235b-a22b":   0.88,
    }.get(res.model, 0.0)
    res.summary = [
        "台積電上修 2026 資本支出至 480 億美元。",
        "第一季毛利率 59.8% 優於市場預期。",
        "AI 需求帶動全年營收年增率指引上修至 28%。",
    ]
    res.latency_s = 0.0
    res.input_tokens = 220
    res.output_tokens = 110


# ---------------------------------------------------------------------------
# 7. 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    prompt = PROMPT_TEMPLATE.format(**SAMPLE_NEWS)

    print("=" * 78)
    print("台股新聞 LLM 基準測試 — 樣本：" + SAMPLE_NEWS["headline"][:40] + "...")
    print("=" * 78)

    results: list[LLMResult] = []

    # Claude Sonnet 4.6
    results.append(call_claude(prompt))

    # DeepSeek-R1（OpenAI-compatible）
    results.append(call_openai_compat(
        model_key="deepseek-reasoner",
        api_model_id="deepseek-reasoner",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        prompt=prompt,
    ))

    # Qwen3-235B（DashScope OpenAI-compatible 端點）
    results.append(call_openai_compat(
        model_key="qwen3-235b-a22b",
        api_model_id="qwen3-235b-a22b-instruct",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        prompt=prompt,
    ))

    # 解析 + 計價（或 mock）
    for r in results:
        if r.mocked:
            fill_mock(r)
        else:
            parse_response(r)
            r.calc_cost()

    # ----------------- 印出對比表 -----------------
    print(f"\n{'模型':<22}{'情感':>8}{'延遲(s)':>10}{'in_tok':>10}"
          f"{'out_tok':>10}{'cost(USD)':>12}{'狀態':>8}")
    print("-" * 78)
    for r in results:
        status = "MOCK" if r.mocked else ("ERR" if r.error else "OK")
        sent = f"{r.sentiment:+.2f}" if r.sentiment is not None else "  N/A"
        print(f"{r.model:<22}{sent:>8}{r.latency_s:>10.2f}"
              f"{r.input_tokens:>10}{r.output_tokens:>10}"
              f"{r.cost_usd:>12.6f}{status:>8}")

    print("\n----- 各模型摘要 -----")
    for r in results:
        print(f"\n[{r.model}]")
        if r.error and not r.mocked:
            print(f"  錯誤：{r.error}")
        for i, s in enumerate(r.summary, 1):
            print(f"  {i}. {s}")

    # ----------------- 月成本估算 -----------------
    print("\n----- 月成本估算（假設每日 100 條新聞，每條約 in=220 / out=110 tokens）-----")
    daily_in, daily_out = 100 * 220, 100 * 110
    monthly_in, monthly_out = daily_in * 30, daily_out * 30
    print(f"月 input tokens={monthly_in:,}, 月 output tokens={monthly_out:,}")
    for model_key, p in PRICING.items():
        m_cost = (monthly_in * p["input"] + monthly_out * p["output"]) / 1_000_000
        print(f"  {model_key:<22} ~= USD {m_cost:>7.2f} / 月")

    print("\n完成。")


if __name__ == "__main__":
    main()
