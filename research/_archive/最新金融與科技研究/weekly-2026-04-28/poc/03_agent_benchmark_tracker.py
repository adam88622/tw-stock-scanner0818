"""GiS Agent 績效監控追蹤器 (Agent Benchmark Tracker)

呼應 Stanford AI Index 2026 對 89% 企業 agent 無法進入 production 的觀察，
本模組為 GiS 內部七階段 dispatcher 流水線提供最小可行的可觀測性層 (observability)。

設計原則:
  * 純標準函式庫實作，無第三方依賴，直接 import 即用
  * JSON 檔作為 store，便於 git diff 與 jq 查詢，必要時可平移到 SQLite
  * 所有時間以 UTC ISO-8601 字串紀錄，避免時區歧義
  * 每筆 record 是 append-only，不允許就地修改 (audit trail)

典型用法 (在 phase5-build / phase6-test 結束的 hook 內呼叫):

    from poc.agent_benchmark_tracker import AgentRunRecord, BenchmarkTracker

    tracker = BenchmarkTracker("D:/claude/var/agent_runs.json")
    tracker.log_run(AgentRunRecord(
        task_id="phase6-test-202604280930",
        agent_name="test-runner",
        success=True,
        runtime_sec=42.7,
        cost_usd=0.083,
        tokens_in=12500,
        tokens_out=2100,
        notes="all 14 unit tests passed",
    ))

    print(tracker.compute_success_rate(agent_name="test-runner"))
    print(tracker.cost_per_task(agent_name="test-runner"))
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _utcnow_iso() -> str:
    """產生 UTC ISO-8601 時間戳，精度到秒。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class AgentRunRecord:
    """單筆 agent 執行紀錄 (對應 OSWorld-style「一個 task 一筆」結構)。"""

    task_id: str                       # 業務側任務識別碼，例：phase5-build-{ts}
    agent_name: str                    # dispatcher / function-builder / test-runner ...
    success: bool                      # 任務是否完成 (走完且輸出符合 schema)
    runtime_sec: float                 # wall-clock 執行秒數
    cost_usd: float = 0.0              # 估算成本，預設 0 表示尚未填入
    tokens_in: int = 0
    tokens_out: int = 0
    retried: bool = False              # 是否為重試後成功，用於 retry_rate
    schema_ok: bool = True             # 輸出是否通過下游 schema 驗證
    notes: str = ""                    # 自由欄位，例外 traceback 或審查訊息
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_utcnow_iso)


class BenchmarkTracker:
    """以 JSON 檔為後端的 append-only 追蹤器。"""

    def __init__(self, store_path: str | os.PathLike) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict]:
        return json.loads(self.store_path.read_text(encoding="utf-8") or "[]")

    def _save(self, rows: list[dict]) -> None:
        self.store_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 寫入 ----------
    def log_run(self, record: AgentRunRecord) -> str:
        """寫入一筆 agent 執行紀錄，回傳 run_id。"""
        rows = self._load()
        rows.append(asdict(record))
        self._save(rows)
        return record.run_id

    # ---------- 查詢 ----------
    def _filter(
        self,
        agent_name: str | None = None,
        since: str | None = None,
    ) -> Iterable[dict]:
        for r in self._load():
            if agent_name and r["agent_name"] != agent_name:
                continue
            if since and r["created_at"] < since:
                continue
            yield r

    def compute_success_rate(
        self, agent_name: str | None = None, since: str | None = None
    ) -> float:
        """成功率 = success=True 的筆數 / 總筆數，無資料回 0.0。"""
        rows = list(self._filter(agent_name, since))
        if not rows:
            return 0.0
        return sum(1 for r in rows if r["success"]) / len(rows)

    def cost_per_task(
        self, agent_name: str | None = None, since: str | None = None
    ) -> float:
        """每任務平均美金成本；無資料回 0.0。"""
        rows = list(self._filter(agent_name, since))
        if not rows:
            return 0.0
        return sum(r["cost_usd"] for r in rows) / len(rows)

    def summary(self, agent_name: str | None = None) -> dict:
        """產出儀表板用的精簡 summary，便於 Streamlit 直接吃。"""
        rows = list(self._filter(agent_name))
        n = len(rows) or 1
        return {
            "agent": agent_name or "ALL",
            "n_runs": len(rows),
            "success_rate": sum(1 for r in rows if r["success"]) / n,
            "retry_rate": sum(1 for r in rows if r["retried"]) / n,
            "schema_violation_rate": sum(1 for r in rows if not r["schema_ok"]) / n,
            "avg_runtime_sec": sum(r["runtime_sec"] for r in rows) / n,
            "avg_cost_usd": sum(r["cost_usd"] for r in rows) / n,
            "total_cost_usd": sum(r["cost_usd"] for r in rows),
        }


if __name__ == "__main__":
    # 最小煙霧測試，可直接 `python 03_agent_benchmark_tracker.py` 跑一次
    demo = BenchmarkTracker(Path(__file__).with_name("agent_runs.demo.json"))
    demo.log_run(AgentRunRecord("demo-001", "test-runner", True, 12.4, 0.05))
    demo.log_run(AgentRunRecord("demo-002", "test-runner", False, 31.0, 0.11,
                                schema_ok=False, notes="schema mismatch"))
    print(json.dumps(demo.summary("test-runner"), ensure_ascii=False, indent=2))
