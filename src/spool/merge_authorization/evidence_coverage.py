from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .pr_context import PREvidence


@dataclass
class EvidenceCoverage:
    pr_title: str = "not_supported"
    pr_body: str = "missing"
    diff: str = "missing"
    changed_files: str = "missing"
    assignment: str = "missing"
    test_output: str = "not_provided"
    ci_checks: str = "not_supported"
    approved_decisions: str = "not_configured"
    legacy_repo_memory: str = "not_configured"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "pr_title": self.pr_title,
            "pr_body": self.pr_body,
            "diff": self.diff,
            "changed_files": self.changed_files,
            "assignment": self.assignment,
            "test_output": self.test_output,
            "ci_checks": self.ci_checks,
            "approved_decisions": self.approved_decisions,
            "legacy_repo_memory": self.legacy_repo_memory,
            "notes": list(self.notes),
        }


def build_evidence_coverage(evidence: PREvidence, changed_files: list[object]) -> EvidenceCoverage:
    coverage = EvidenceCoverage(
        pr_body=_text_status(evidence.agent_summary_text),
        diff=_text_status(evidence.diff_text),
        changed_files="available" if changed_files else "missing",
        assignment=_text_status(evidence.task_text),
        test_output=_test_output_status(evidence),
        approved_decisions=_decision_memory_status(evidence),
        legacy_repo_memory=_legacy_memory_status(evidence),
    )
    coverage.notes.append("PR title is not available in local file mode.")
    coverage.notes.append("CI checks are not available in local file mode.")
    if coverage.test_output in {"not_provided", "empty"}:
        coverage.notes.append("No test output was provided, so test-pass claims cannot be verified from external evidence.")
    if coverage.assignment == "empty":
        coverage.notes.append("Assignment file was provided but empty, so assignment review is partial.")
    elif coverage.assignment == "missing":
        coverage.notes.append("Assignment was expected but could not be found, so assignment review is partial.")
    if coverage.approved_decisions == "not_configured":
        coverage.notes.append("No approved decision memory was configured for this run.")
    if coverage.approved_decisions == "malformed":
        coverage.notes.append("Approved decision memory is malformed.")
    return coverage


def _text_status(text: str) -> str:
    return "available" if text.strip() else "empty"


def _test_output_status(evidence: PREvidence) -> str:
    if not evidence.test_output_path:
        return "not_provided"
    return "available" if evidence.has_test_output else "empty"


def _decision_memory_status(evidence: PREvidence) -> str:
    if evidence.decision_memory.status == "available":
        return "available"
    if evidence.decision_memory.status == "malformed":
        return "malformed"
    return "not_configured"


def _legacy_memory_status(evidence: PREvidence) -> str:
    if not evidence.decision_memory.source:
        return "not_configured"
    return "available" if Path(evidence.decision_memory.source).suffix.lower() == ".json" else "not_configured"
