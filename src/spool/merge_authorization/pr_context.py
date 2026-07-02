from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .decision_memory import DecisionMemory, load_approved_decisions, resolve_decision_memory_path


DEFAULT_REPO_MEMORY_PATH = Path(".spool/approved_repo_memory.json")


@dataclass
class RepoMemoryItem:
    memory_id: str
    type: str
    title: str
    summary: str
    status: str = "approved"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "type": self.type,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
        }


@dataclass
class PREvidence:
    diff_path: str
    task_path: str | None
    agent_summary_path: str
    test_output_path: str | None
    repo_memory_path: str | None
    diff_text: str
    task_text: str
    agent_summary_text: str
    test_output_text: str = ""
    repo_memory_exists: bool = False
    repo_memory_items: list[RepoMemoryItem] = field(default_factory=list)
    decision_memory: DecisionMemory = field(
        default_factory=lambda: DecisionMemory(
            source=None,
            status="not_configured",
            notes=["No approved decision memory was configured for this run."],
        )
    )

    @property
    def has_test_output(self) -> bool:
        return bool(self.test_output_text.strip())


def load_pr_evidence(
    *,
    diff_path: str | Path,
    task_path: str | Path | None = None,
    agent_summary_path: str | Path,
    test_output_path: str | Path | None = None,
    repo_memory_path: str | Path | None = None,
) -> PREvidence:
    diff = Path(diff_path)
    task = Path(task_path) if task_path else None
    summary = Path(agent_summary_path)
    test_output = Path(test_output_path) if test_output_path else None
    memory_path = resolve_decision_memory_path(repo_memory_path)

    task_text = ""
    if task is not None and task.exists():
        task_text = task.read_text(encoding="utf-8")
    test_text = ""
    if test_output is not None and test_output.exists():
        test_text = test_output.read_text(encoding="utf-8")

    if repo_memory_path is None and not memory_path.exists():
        decision_memory = DecisionMemory(
            source=None,
            status="not_configured",
            notes=["No approved decision memory was configured for this run."],
        )
    else:
        decision_memory = load_approved_decisions(memory_path)
    memory_exists = decision_memory.status == "available"
    memory_items = [
        RepoMemoryItem(
            memory_id=decision.id,
            type=decision.type,
            title=decision.title,
            summary=decision.summary_text,
            status=decision.status,
            raw=decision.to_dict(),
        )
        for decision in decision_memory.decisions
    ]

    return PREvidence(
        diff_path=str(diff),
        task_path=str(task) if task else None,
        agent_summary_path=str(summary),
        test_output_path=str(test_output) if test_output else None,
        repo_memory_path=str(memory_path),
        diff_text=diff.read_text(encoding="utf-8"),
        task_text=task_text,
        agent_summary_text=summary.read_text(encoding="utf-8"),
        test_output_text=test_text,
        repo_memory_exists=memory_exists,
        repo_memory_items=memory_items,
        decision_memory=decision_memory,
    )
