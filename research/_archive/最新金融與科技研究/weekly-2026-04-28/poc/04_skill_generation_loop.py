"""
PoC: Hermes 風格 Skill Generation Loop 簡化版
============================================
目的：示範 agent 在執行量化交易任務時，如何將成功經驗抽象為可復用 skill，
      下次遇到類似任務先檢索 registry 命中，避免重複規劃。

設計參考：
- NousResearch/hermes-agent 的 closed-loop learning
- agentskills.io 的 SKILL.md 格式（name / description / instructions）
- progressive disclosure：先讀 description，命中再讀完整 instructions

執行：python 04_skill_generation_loop.py（不需 LLM API key，全部 mock）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

# ---------- Skill Registry ----------

SKILL_DIR = Path(__file__).parent / "_skill_registry"
SKILL_DIR.mkdir(exist_ok=True)


@dataclass
class Skill:
    """單一 skill 的記憶體表示，對應 agentskills.io 的 SKILL.md。"""
    name: str                # 唯一識別（kebab-case）
    description: str         # 一句話描述，progressive disclosure 第一階段讀這個
    keywords: list[str]      # 檢索用關鍵字
    steps: list[str]         # 執行步驟（成功經驗）
    params: dict             # 參數模板
    hit_count: int = 0       # 命中次數（用於排序與淘汰）

    def to_md(self) -> str:
        """序列化為 SKILL.md 格式（agentskills.io 標準）。"""
        return (
            f"---\nname: {self.name}\ndescription: {self.description}\n"
            f"keywords: {json.dumps(self.keywords, ensure_ascii=False)}\n---\n\n"
            f"## Steps\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(self.steps)) +
            f"\n\n## Params\n```json\n{json.dumps(self.params, ensure_ascii=False, indent=2)}\n```\n"
        )


class SkillRegistry:
    """以資料夾為單位的 skill 倉庫，每個 skill 一個子目錄與 SKILL.md。"""

    def __init__(self, root: Path):
        self.root = root
        self.skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        for sub in self.root.iterdir():
            if sub.is_dir() and (sub / "skill.json").exists():
                data = json.loads((sub / "skill.json").read_text(encoding="utf-8"))
                self.skills[data["name"]] = Skill(**data)

    def search(self, query: str, top_k: int = 3) -> list[Skill]:
        """簡化版檢索：以關鍵字 substring 命中數 + 命中次數排序（仿 FTS5 + popularity）。
        中文不易斷詞，故採 substring 包含判斷，較貼近 FTS5 在 CJK 上的表現。"""
        q = query.lower()
        scored = []
        for sk in self.skills.values():
            overlap = sum(1 for k in sk.keywords if k.lower() in q)
            if overlap > 0:
                scored.append((overlap * 10 + sk.hit_count, sk))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]

    def add(self, skill: Skill):
        """寫入 skill：建立資料夾 + SKILL.md + skill.json（給 PoC 重載用）。"""
        path = self.root / skill.name
        path.mkdir(exist_ok=True)
        (path / "SKILL.md").write_text(skill.to_md(), encoding="utf-8")
        (path / "skill.json").write_text(json.dumps(asdict(skill), ensure_ascii=False, indent=2), encoding="utf-8")
        self.skills[skill.name] = skill

    def bump_hit(self, name: str):
        """命中後更新計數並 persist。"""
        if name in self.skills:
            self.skills[name].hit_count += 1
            self.add(self.skills[name])  # 重新寫入


# ---------- Mock LLM ----------

def mock_llm_plan(task: str) -> list[str]:
    """假裝 LLM 對未見過的任務做規劃，回傳步驟列表。"""
    if "法人買超" in task or "三大法人" in task:
        return [
            "呼叫 TWSE API: /fund/T86?date=YYYYMMDD",
            "解析 JSON 取得每檔股票之外資/投信/自營商買賣超",
            "計算三大法人合計買超金額",
            "依買超金額降冪排序",
            "取前 N 名輸出",
        ]
    if "均線" in task or "MA" in task:
        return ["抓取個股 K 線", "計算 MA5/MA20", "判斷黃金交叉", "輸出符合清單"]
    return ["分析任務需求", "查詢資料來源", "執行運算", "輸出結果"]


def mock_llm_abstract(task: str, steps: list[str]) -> Skill:
    """假裝 LLM 將成功經驗抽象為 skill（產生 name/description/keywords）。"""
    # 規則化命名（真實場景由 LLM 生成）
    if "法人買超" in task:
        return Skill(
            name="twse-institutional-top-buyers",
            description="抓取 TWSE 三大法人買超排行（外資+投信+自營合計）",
            keywords=["法人", "買超", "三大法人", "外資", "投信", "TWSE", "排行"],
            steps=steps,
            params={"date": "YYYYMMDD", "top_n": 10},
        )
    if "均線" in task:
        return Skill(
            name="ma-golden-cross-scanner",
            description="掃描 MA5/MA20 黃金交叉之個股",
            keywords=["均線", "MA", "黃金交叉", "技術指標"],
            steps=steps,
            params={"short": 5, "long": 20},
        )
    slug = re.sub(r"\s+", "-", task.strip())[:40].lower() or "generic-task"
    return Skill(name=slug, description=task[:60], keywords=task.split()[:5],
                 steps=steps, params={})


# ---------- Agent Loop ----------

def execute(skill_or_steps, params: dict) -> bool:
    """模擬執行：90% 成功率（PoC 永遠回 True）。"""
    return True


def agent_run(task: str, registry: SkillRegistry) -> str:
    """單次任務執行：先檢索 → 命中執行 / 未命中規劃 → 成功則抽象為 skill。"""
    print(f"\n[Agent] 收到任務：{task}")
    hits = registry.search(task)
    if hits:
        sk = hits[0]
        print(f"[Agent] Skill 命中：{sk.name}（{sk.description}）")
        if execute(sk, sk.params):
            registry.bump_hit(sk.name)
            return f"OK（用 skill: {sk.name}, 累計命中 {sk.hit_count}）"
        return "FAIL"
    # 未命中：探索模式
    print("[Agent] 無 skill 命中，進入探索模式（mock LLM 規劃）")
    steps = mock_llm_plan(task)
    if not execute(steps, {}):
        return "FAIL（探索失敗）"
    # 成功 → 抽象為 skill
    new_skill = mock_llm_abstract(task, steps)
    registry.add(new_skill)
    print(f"[Agent] 成功！已將經驗抽象為 skill: {new_skill.name}")
    return f"OK（新增 skill: {new_skill.name}）"


# ---------- Demo ----------

def main():
    reg = SkillRegistry(SKILL_DIR)
    print(f"=== 啟動：registry 既有 skills = {list(reg.skills.keys())} ===")

    tasks = [
        "找出今日法人買超前 10 名股票",       # 第一次：未命中 → 學習
        "列出三大法人買超最多的個股",          # 第二次：命中（不同措辭）
        "今天三大法人買超排行",                # 第三次：命中
        "掃描 MA5 MA20 均線黃金交叉個股",       # 不同任務 → 新 skill
        "找均線黃金交叉的標的",                 # 命中第二個 skill
        "今日法人買超前 20 大",                 # 第四次：命中第一個 skill
    ]

    for t in tasks:
        result = agent_run(t, reg)
        print(f"[Result] {result}")

    print("\n=== 結束：registry 摘要 ===")
    for name, sk in reg.skills.items():
        print(f"  - {name}: hits={sk.hit_count}, desc={sk.description}")


if __name__ == "__main__":
    main()
