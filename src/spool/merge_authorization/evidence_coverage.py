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
        pr_title=_pr_title_status(evidence),
        pr_body=_text_status(evidence.agent_summary_text),
        diff=_text_status(evidence.diff_text),
        changed_files="available" if changed_files else "missing",
        assignment=_assignment_status(evidence),
        test_output=_test_output_status(evidence),
        ci_checks=evidence.ci_checks.status,
        approved_decisions=_decision_memory_status(evidence),
        legacy_repo_memory=_legacy_memory_status(evidence),
    )
    if coverage.pr_title == "not_supported":
        coverage.notes.append("PR title is not available in local file mode.")
    elif coverage.pr_title == "empty":
        coverage.notes.append("GitHub PR metadata was fetched, but the PR title was empty.")
    if coverage.ci_checks == "not_supported":
        coverage.notes.append("CI checks are not available in local file mode.")
    elif coverage.ci_checks == "empty":
        coverage.notes.append("GitHub checks were fetched, but no checks were returned for this PR.")
    elif coverage.ci_checks == "unavailable":
        coverage.notes.append("GitHub checks could not be fetched for this PR.")
    elif coverage.ci_checks == "malformed":
        coverage.notes.append("GitHub checks were fetched, but the checks payload could not be parsed.")
    if coverage.test_output in {"not_provided", "empty"}:
        coverage.notes.append("No test output was provided, so test-pass claims cannot be verified from external evidence.")
    if coverage.assignment == "not_provided":
        coverage.notes.append(
            "Assignment evidence was not provided, so Spool could not verify full task authorization. "
            "This partial report checks the PR body against the diff and flags available evidence gaps."
        )
    elif coverage.assignment == "empty":
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


def _pr_title_status(evidence: PREvidence) -> str:
    if evidence.pr_metadata_path is None:
        return "not_supported"
    return _text_status(evidence.pr_title_text)


def _assignment_status(evidence: PREvidence) -> str:
    if evidence.task_path is None:
        return "not_provided"
    if not Path(evidence.task_path).exists():
        return "missing"
    return _text_status(evidence.task_text)


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
