from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pr_trust import PRTrustReport


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
    lines = [
        "# Spool Trust Report",
        "",
        "## Overall Verdict",
        "",
        str(payload["overall_verdict"]),
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
        "## Agent Claims",
        "",
    ]
    lines.extend(_claim_lines(payload.get("agent_claims") or []))
    lines.extend(["", "## Weak or Missing Evidence", ""])
    lines.extend(_simple_lines(payload.get("weak_or_missing_evidence") or []))
    lines.extend(["", "## Buried Decisions", ""])
    lines.extend(_decision_lines(payload.get("buried_decisions") or []))
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


def _decision_lines(decisions: list[dict[str, Any]]) -> list[str]:
    if not decisions:
        return ["- None"]
    return [f"- {item.get('decision')} ({item.get('verdict')})" for item in decisions]


def _repo_memory_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- no relevant memory"]
    return [f"- {item.get('memory')}: {item.get('verdict')} - {item.get('reason')}" for item in items]


def _simple_lines(items: list[Any]) -> list[str]:
    if not items:
        return ["- None"]
    return [f"- {item}" for item in items]


def _join_or_none(items: list[Any]) -> str:
    cleaned = [str(item) for item in items if item]
    return ", ".join(cleaned) if cleaned else "None"
