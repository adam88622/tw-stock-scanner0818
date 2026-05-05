"""03_empirical.py — 用 tw-stock-scanner 實際 log 做 agent benchmark 分析。

設計：
  * 把每個 log 當成「一次 agent run」灌進 PoC 的 BenchmarkTracker
  * regex 解析 [YYYY-MM-DD HH:MM:SS] LEVEL 格式
  * 額外從 backfill log 抓 retry / 「失敗」 / 「完成 — 成功 X 失敗 Y」 等業務訊號
  * 成本以 Claude Sonnet 4.5 估算 ($3 / MTok input, $15 / MTok output)，
    用「log 行數」當 token 代理量；這是 best-effort，會在報告中標明限制
  * 輸出：results/03_agent_runs.json（tracker store）+ stdout 摘要 JSON

執行：
  python 03_empirical.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# ---- 載入 PoC tracker（poc 資料夾不是合法 package 名，所以用路徑插入）----
HERE = Path(__file__).resolve().parent
POC_DIR = HERE.parent / "poc"
sys.path.insert(0, str(POC_DIR))

# poc 檔名以數字開頭，用 importlib 動態載入
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "agent_benchmark_tracker",
    POC_DIR / "03_agent_benchmark_tracker.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["agent_benchmark_tracker"] = _mod  # dataclass 需要在 sys.modules 找得到
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

AgentRunRecord = _mod.AgentRunRecord
BenchmarkTracker = _mod.BenchmarkTracker

# ---- 來源 log ----
LOG_DIR = Path(r"D:\claude\tw-stock-scanner\log")

LOG_FILES = [
    # (filename, agent_name, task_id)
    ("20260413-133245-backfill-institutional.log", "backfill-runner", "backfill-2026-04-13-1332"),
    ("20260413-133601-backfill-institutional.log", "backfill-runner", "backfill-2026-04-13-1336"),
    ("backfill_detached.log", "backfill-runner", "backfill-2026-04-13-detached"),
    ("clean_institutional_20260427_123517.log", "data-cleaner", "clean-2026-04-27"),
    ("export_institutional_20260427_123436.log", "data-exporter", "export-2026-04-27"),
]

# ---- regex ----
TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
LEVEL_RE = re.compile(r"\]\s+(INFO|WARNING|ERROR|CRITICAL|DEBUG)\b")
RETRY_RE = re.compile(r"(請求失敗|retry|��\s*\d+\s*��|第\s*\d+\s*次)")
DONE_RE = re.compile(
    r"回補完成\s*[-—]+\s*成功\s*(\d+).*?失敗\s*(\d+)"
)
ALL_DONE_RE = re.compile(r"ALL DONE")

# Claude Sonnet 4.5 pricing (USD per MTok)
PRICE_IN = 3.0 / 1_000_000
PRICE_OUT = 15.0 / 1_000_000


def _read_text(path: Path) -> str:
    """容錯讀檔：utf-8 → big5 → latin-1。"""
    for enc in ("utf-8", "big5", "cp950", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_bytes().decode("latin-1", errors="replace")


def parse_log(path: Path) -> dict:
    """解析單一 log，回傳結構化指標。"""
    text = _read_text(path)
    lines = text.splitlines()

    timestamps: list[datetime] = []
    levels: Counter[str] = Counter()
    error_msgs: Counter[str] = Counter()
    retry_count = 0
    success_days = 0
    failed_days = 0
    explicit_all_done = False

    for line in lines:
        m_ts = TS_RE.search(line)
        if m_ts:
            try:
                timestamps.append(datetime.strptime(m_ts.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                pass

        m_lv = LEVEL_RE.search(line)
        if m_lv:
            levels[m_lv.group(1)] += 1

        if RETRY_RE.search(line):
            retry_count += 1

        if "WARNING" in line or "ERROR" in line:
            # 取 LEVEL 之後的訊息頭 60 字當錯誤指紋
            after = re.split(r"\b(?:WARNING|ERROR)\b", line, maxsplit=1)
            if len(after) == 2:
                fp = after[1].strip()[:60]
                if fp:
                    error_msgs[fp] += 1

        m_done = DONE_RE.search(line)
        if m_done:
            success_days = int(m_done.group(1))
            failed_days = int(m_done.group(2))

        if ALL_DONE_RE.search(line):
            explicit_all_done = True

    if timestamps:
        runtime_sec = (max(timestamps) - min(timestamps)).total_seconds()
        first_ts = min(timestamps).isoformat()
        last_ts = max(timestamps).isoformat()
    else:
        runtime_sec = 0.0
        first_ts = last_ts = ""

    n_lines = len(lines)
    n_chars = len(text)

    # 成本估算：用 log 字元數逼近 token（中英混雜，~2 chars/token），
    # 假設 90% 是 input、10% 是 output（agent 多半在收 stdin / API response）。
    approx_tokens = n_chars / 2
    tokens_in = int(approx_tokens * 0.9)
    tokens_out = int(approx_tokens * 0.1)
    cost_usd = tokens_in * PRICE_IN + tokens_out * PRICE_OUT

    # 成功判定：
    # 1) backfill 類有 ALL DONE 或 DONE_RE 且 failed=0 → success
    # 2) 0-byte log → fail（agent 沒寫東西就死了）
    # 3) export/clean：log 完整且無 ERROR → success
    if path.stat().st_size == 0:
        success = False
        schema_ok = False
        notes = "empty log file (agent crashed before writing)"
    elif explicit_all_done and failed_days == 0:
        success = True
        schema_ok = True
        notes = f"ALL DONE; success_days={success_days}, failed_days={failed_days}"
    elif levels.get("ERROR", 0) > 0:
        success = False
        schema_ok = True
        notes = f"errors={levels['ERROR']}"
    else:
        # 沒有 ALL DONE 但也沒 ERROR：視為「過程紀錄」，當 partial success
        success = n_lines > 0 and levels.get("ERROR", 0) == 0
        schema_ok = True
        notes = f"no explicit ALL DONE marker; lines={n_lines}"

    return {
        "n_lines": n_lines,
        "n_chars": n_chars,
        "levels": dict(levels),
        "retry_count": retry_count,
        "success_days": success_days,
        "failed_days": failed_days,
        "explicit_all_done": explicit_all_done,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "runtime_sec": runtime_sec,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "success": success,
        "schema_ok": schema_ok,
        "top_errors": error_msgs.most_common(5),
        "notes": notes,
    }


def main() -> None:
    store_path = HERE / "03_agent_runs.json"
    # 每次重跑前清空，避免重複累積
    if store_path.exists():
        store_path.unlink()

    tracker = BenchmarkTracker(store_path)

    per_log: list[dict] = []
    for fname, agent_name, task_id in LOG_FILES:
        path = LOG_DIR / fname
        if not path.exists():
            print(f"[skip] not found: {path}")
            continue
        info = parse_log(path)
        record = AgentRunRecord(
            task_id=task_id,
            agent_name=agent_name,
            success=info["success"],
            runtime_sec=info["runtime_sec"],
            cost_usd=round(info["cost_usd"], 4),
            tokens_in=info["tokens_in"],
            tokens_out=info["tokens_out"],
            retried=info["retry_count"] > 0,
            schema_ok=info["schema_ok"],
            notes=info["notes"],
        )
        tracker.log_run(record)

        info["filename"] = fname
        info["agent_name"] = agent_name
        info["task_id"] = task_id
        per_log.append(info)

    overall = tracker.summary()
    by_agent = {
        name: tracker.summary(agent_name=name)
        for name in {a for _, a, _ in LOG_FILES}
    }

    report = {
        "per_log": per_log,
        "overall": overall,
        "by_agent": by_agent,
    }
    out_json = HERE / "03_empirical_summary.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # 印出主要指標
    print("=" * 70)
    print("tw-stock-scanner agent benchmark — empirical run")
    print("=" * 70)
    for r in per_log:
        print(
            f"\n[{r['agent_name']}] {r['filename']}\n"
            f"  lines={r['n_lines']}, runtime={r['runtime_sec']:.0f}s, "
            f"retries={r['retry_count']}, levels={r['levels']}, "
            f"success_days={r['success_days']}, failed_days={r['failed_days']}, "
            f"success={r['success']}\n"
            f"  cost~${r['cost_usd']:.4f}  notes={r['notes']}"
        )

    print("\n" + "-" * 70)
    print("OVERALL:", json.dumps(overall, ensure_ascii=False, indent=2))
    print("\nBY AGENT:", json.dumps(by_agent, ensure_ascii=False, indent=2))
    print(f"\nstore -> {store_path}")
    print(f"summary -> {out_json}")


if __name__ == "__main__":
    main()
