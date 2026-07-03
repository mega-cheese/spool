from __future__ import annotations

import json
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
class PRCheckRun:
    name: str
    bucket: str = ""
    state: str = ""
    conclusion: str = ""
    started_at: str = ""
    completed_at: str = ""
    link: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_failed(self) -> bool:
        status = f"{self.bucket} {self.state} {self.conclusion}".lower()
        return any(token in status for token in ("failure", "failed", "fail", "error", "timed_out", "cancelled", "action_required"))

    @property
    def is_pending(self) -> bool:
        status = f"{self.bucket} {self.state} {self.conclusion}".lower()
        if self.is_failed or self.is_passed:
            return False
        return not self.conclusion.strip() or any(token in status for token in ("pending", "in_progress", "queued", "waiting", "requested"))

    @property
    def is_passed(self) -> bool:
        status = f"{self.bucket} {self.state} {self.conclusion}".lower()
        return any(token in status for token in ("success", "passed", "pass"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bucket": self.bucket,
            "state": self.state,
            "conclusion": self.conclusion,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "link": self.link,
        }


@dataclass
class PRChecksEvidence:
    status: str = "not_supported"
    checks: list[PRCheckRun] = field(default_factory=list)
    error: str = ""
    source: str | None = None

    @property
    def total_count(self) -> int:
        return len(self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.is_passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for check in self.checks if check.is_failed)

    @property
    def pending_count(self) -> int:
        return sum(1 for check in self.checks if check.is_pending)

    @property
    def failed_checks(self) -> list[str]:
        return [check.name for check in self.checks if check.is_failed]

    @property
    def pending_checks(self) -> list[str]:
        return [check.name for check in self.checks if check.is_pending]

    @property
    def all_passing(self) -> bool:
        return self.status == "available" and self.total_count > 0 and self.failed_count == 0 and self.pending_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_count": self.total_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pending_count": self.pending_count,
            "failed_checks": self.failed_checks,
            "pending_checks": self.pending_checks,
            "error": self.error,
            "source": self.source,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass
class PREvidence:
    diff_path: str
    task_path: str | None
    agent_summary_path: str
    test_output_path: str | None
    pr_metadata_path: str | None
    repo_memory_path: str | None
    diff_text: str
    task_text: str
    agent_summary_text: str
    pr_title_text: str = ""
    test_output_text: str = ""
    repo_memory_exists: bool = False
    repo_memory_items: list[RepoMemoryItem] = field(default_factory=list)
    ci_checks: PRChecksEvidence = field(default_factory=PRChecksEvidence)
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
    pr_metadata_path: str | Path | None = None,
    pr_title_text: str | None = None,
    ci_checks_path: str | Path | None = None,
    repo_memory_path: str | Path | None = None,
) -> PREvidence:
    diff = Path(diff_path)
    task = Path(task_path) if task_path else None
    summary = Path(agent_summary_path)
    test_output = Path(test_output_path) if test_output_path else None
    metadata = Path(pr_metadata_path) if pr_metadata_path else None
    ci_checks_file = Path(ci_checks_path) if ci_checks_path else None
    memory_path = resolve_decision_memory_path(repo_memory_path)

    task_text = ""
    if task is not None and task.exists():
        task_text = task.read_text(encoding="utf-8")
    test_text = ""
    if test_output is not None and test_output.exists():
        test_text = test_output.read_text(encoding="utf-8")
    ci_checks = _load_ci_checks(ci_checks_file)

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
        pr_metadata_path=str(metadata) if metadata else None,
        repo_memory_path=str(memory_path),
        diff_text=diff.read_text(encoding="utf-8"),
        task_text=task_text,
        agent_summary_text=summary.read_text(encoding="utf-8"),
        pr_title_text=str(pr_title_text or ""),
        test_output_text=test_text,
        repo_memory_exists=memory_exists,
        repo_memory_items=memory_items,
        ci_checks=ci_checks,
        decision_memory=decision_memory,
    )


def _load_ci_checks(path: Path | None) -> PRChecksEvidence:
    if path is None:
        return PRChecksEvidence()
    if not path.exists():
        return PRChecksEvidence(status="missing", source=str(path), error="Checks evidence file was expected but not found.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return PRChecksEvidence(status="malformed", source=str(path), error=str(exc))

    if isinstance(payload, list):
        checks = _checks_from_payload(payload)
        return PRChecksEvidence(status="available" if checks else "empty", checks=checks, source=str(path))
    if not isinstance(payload, dict):
        return PRChecksEvidence(status="malformed", source=str(path), error="Checks evidence must be a JSON object or array.")

    checks = _checks_from_payload(payload.get("checks") or [])
    status = str(payload.get("status") or ("available" if checks else "empty"))
    return PRChecksEvidence(
        status=status,
        checks=checks,
        error=str(payload.get("error") or ""),
        source=str(path),
    )


def _checks_from_payload(items: object) -> list[PRCheckRun]:
    if not isinstance(items, list):
        return []
    checks: list[PRCheckRun] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        checks.append(
            PRCheckRun(
                name=str(item.get("name") or item.get("workflowName") or "unnamed check"),
                bucket=str(item.get("bucket") or ""),
                state=str(item.get("state") or ""),
                conclusion=str(item.get("conclusion") or ""),
                started_at=str(item.get("startedAt") or ""),
                completed_at=str(item.get("completedAt") or ""),
                link=str(item.get("link") or item.get("url") or ""),
                raw=dict(item),
            )
        )
    return checks
