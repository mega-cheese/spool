from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .merge_authorization import PRTrustReport


def write_trust_report(report: PRTrustReport, out_dir: str | Path, *, pretty: bool = False) -> dict[str, str]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "trust_report.json"
    md_path = output_dir / "trust_report.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2 if pretty else None, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(trust_report_to_markdown(report), encoding="utf-8")
    return {"trust_report_json": str(json_path), "trust_report_md": str(md_path)}


def trust_report_to_markdown(report: PRTrustReport) -> str:
    payload = report.to_dict()
    scope = payload["scope"]
    summary = payload.get("summary") or {}
    lines = [
        "# Spool Merge Authorization Report",
        "",
        "## Overall Verdict",
        "",
        str(payload["overall_verdict"]),
        "",
        "## Checked",
        "",
        f"- Changed files checked: {summary.get('changed_files_checked', 0)}",
        f"- Allowed scope status: {summary.get('allowed_scope_status', 'unknown')}",
        f"- Supported claims: {summary.get('supported_claims_count', 0)}",
        f"- Test evidence: {summary.get('test_evidence_status', 'unknown')}",
        f"- Decision memory: {summary.get('decision_memory_status', summary.get('repo_memory_status', 'unknown'))}",
        "",
        "## Scope / Authorization",
        "",
        f"- Task source: {scope.get('task_source') or 'task'}",
        f"- Approved task: {scope.get('approved_task') or 'Not specified'}",
        f"- Allowed files: {_join_or_none(scope.get('allowed_files') or [])}",
        f"- Changed files: {_join_or_none([item.get('path') for item in scope.get('changed_files') or []])}",
        f"- Scope violations: {_join_or_none(scope.get('scope_violations') or [])}",
        f"- Forbidden path violations: {_join_or_none(scope.get('forbidden_violations') or [])}",
        "",
        "## Evidence Coverage",
        "",
    ]
    lines.extend(_evidence_coverage_lines(payload.get("evidence_coverage") or {}))
    lines.extend([
        "",
        "## Agent Claims",
        "",
    ])
    lines.extend(_claim_lines(payload.get("agent_claims") or []))
    lines.extend(["", "## Weak or Missing Evidence", ""])
    lines.extend(_simple_lines(payload.get("weak_or_missing_evidence") or []))
    lines.extend(["", "## Candidate Decisions Requiring Approval", ""])
    lines.extend(_candidate_decision_lines(payload.get("candidate_decisions") or []))
    lines.extend(["", "## Decision Memory", ""])
    lines.extend(_decision_memory_lines(payload.get("decision_memory") or {}))
    lines.extend(["", "## Repo Memory Check", ""])
    lines.extend(_repo_memory_lines(payload.get("repo_memory_check") or []))
    lines.extend(["", "## Risks", ""])
    lines.extend(_simple_lines(payload.get("risks") or []))
    lines.extend(["", "## Reviewer Actions", ""])
    lines.extend(_simple_lines(payload.get("reviewer_actions") or []))
    return "\n".join(lines).rstrip() + "\n"


def _claim_lines(claims: list[dict[str, Any]]) -> list[str]:
    if not claims:
        return ["- None"]
    lines: list[str] = []
    for claim in claims:
        lines.append(f"- Claim: {claim.get('claim')}")
        lines.append(f"  - Type: {claim.get('claim_type')}")
        lines.append(f"  - Verdict: {claim.get('verdict')}")
        lines.append(f"  - Evidence target: {claim.get('evidence_target')}")
        if claim.get("evidence_quote"):
            lines.append(f"  - Evidence: {claim.get('evidence_quote')}")
        if claim.get("reason"):
            lines.append(f"  - Reason: {claim.get('reason')}")
    return lines


def _evidence_coverage_lines(coverage: dict[str, Any]) -> list[str]:
    if not coverage:
        return ["- None"]
    lines = [
        f"- PR title: {coverage.get('pr_title')}",
        f"- PR body / agent summary: {coverage.get('pr_body')}",
        f"- Diff: {coverage.get('diff')}",
        f"- Changed files: {coverage.get('changed_files')}",
        f"- Assignment: {coverage.get('assignment')}",
        f"- Test output: {coverage.get('test_output')}",
        f"- CI checks: {coverage.get('ci_checks')}",
        f"- Approved decisions: {coverage.get('approved_decisions')}",
        f"- Legacy repo memory: {coverage.get('legacy_repo_memory')}",
    ]
    notes = coverage.get("notes") or []
    if notes:
        lines.append(f"- Notes: {_join_or_none(notes)}")
    return lines


def _candidate_decision_lines(decisions: list[dict[str, Any]]) -> list[str]:
    if not decisions:
        return ["- None"]
    lines: list[str] = []
    for decision in decisions:
        lines.append(f"- {decision.get('id')}: {decision.get('title')} ({decision.get('decision_type')})")
        lines.append(f"  - Source: {decision.get('source')}")
        lines.append(f"  - Recommended action: {decision.get('recommended_action')}")
        lines.append(f"  - Quote: {decision.get('quote')}")
    return lines


def _decision_lines(decisions: list[dict[str, Any]]) -> list[str]:
    if not decisions:
        return ["- None"]
    return [f"- {item.get('decision')} ({item.get('verdict')})" for item in decisions]


def _repo_memory_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- no relevant memory"]
    return [f"- {item.get('memory')}: {item.get('verdict')} - {item.get('reason')}" for item in items]


def _decision_memory_lines(memory: dict[str, Any]) -> list[str]:
    if not memory:
        return ["- None"]
    return [
        f"- Source: {memory.get('source') or 'None'}",
        f"- Status: {memory.get('status') or 'unknown'}",
        f"- Approved decisions: {memory.get('approved_decisions_count', 0)}",
        f"- Matched decisions: {_join_or_none(memory.get('matched_decisions') or [])}",
        f"- Conflicts: {_join_or_none(memory.get('conflicts') or [])}",
        f"- Notes: {_join_or_none(memory.get('notes') or [])}",
    ]


def _simple_lines(items: list[Any]) -> list[str]:
    if not items:
        return ["- None"]
    return [f"- {item}" for item in items]


def _join_or_none(items: list[Any]) -> str:
    cleaned = [str(item) for item in items if item]
    return ", ".join(cleaned) if cleaned else "None"
