from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    task_path: str
    agent_summary_path: str
    test_output_path: str | None
    repo_memory_path: str | None
    diff_text: str
    task_text: str
    agent_summary_text: str
    test_output_text: str = ""
    repo_memory_exists: bool = False
    repo_memory_items: list[RepoMemoryItem] = field(default_factory=list)

    @property
    def has_test_output(self) -> bool:
        return bool(self.test_output_text.strip())


def load_pr_evidence(
    *,
    diff_path: str | Path,
    task_path: str | Path,
    agent_summary_path: str | Path,
    test_output_path: str | Path | None = None,
    repo_memory_path: str | Path | None = None,
) -> PREvidence:
    diff = Path(diff_path)
    task = Path(task_path)
    summary = Path(agent_summary_path)
    test_output = Path(test_output_path) if test_output_path else None
    memory_path = Path(repo_memory_path) if repo_memory_path else DEFAULT_REPO_MEMORY_PATH

    test_text = ""
    if test_output is not None and test_output.exists():
        test_text = test_output.read_text(encoding="utf-8")

    memory_exists = memory_path.exists()
    memory_items = _load_repo_memory(memory_path) if memory_exists else []

    return PREvidence(
        diff_path=str(diff),
        task_path=str(task),
        agent_summary_path=str(summary),
        test_output_path=str(test_output) if test_output else None,
        repo_memory_path=str(memory_path),
        diff_text=diff.read_text(encoding="utf-8"),
        task_text=task.read_text(encoding="utf-8"),
        agent_summary_text=summary.read_text(encoding="utf-8"),
        test_output_text=test_text,
        repo_memory_exists=memory_exists,
        repo_memory_items=memory_items,
    )


def _load_repo_memory(path: Path) -> list[RepoMemoryItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items = []
    if isinstance(payload, dict):
        raw_items = list(payload.get("approved_memory") or payload.get("memories") or [])
    elif isinstance(payload, list):
        raw_items = list(payload)

    items: list[RepoMemoryItem] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("decision") or "").strip()
        summary = str(item.get("summary") or title).strip()
        if not title and not summary:
            continue
        items.append(
            RepoMemoryItem(
                memory_id=str(item.get("memory_id") or f"repo_memory_{index}"),
                type=str(item.get("type") or "decision"),
                title=title or summary,
                summary=summary or title,
                status=str(item.get("status") or "approved"),
                raw=dict(item),
            )
        )
    return items
