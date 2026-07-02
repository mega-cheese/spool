from __future__ import annotations

from pathlib import Path
from typing import Any

from .decision_memory import ApprovedDecision, load_approved_decisions
from .memory_update_plan import DecisionMemoryUpdatePlan


def write_memory_update_plan(
    plan: DecisionMemoryUpdatePlan,
    output_path: str | Path | None,
    *,
    existing_memory_path: str | Path | None = None,
) -> dict[str, Any]:
    if output_path is None:
        raise ValueError("Writing approved decision memory requires an explicit output_path.")

    destination = Path(output_path)
    existing_decisions = _load_existing_decisions(existing_memory_path)
    new_decisions = [_normalize_decision_entry(item) for item in plan.approved_decisions]
    _raise_for_duplicate_ids(existing_decisions, new_decisions)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_decisions_to_yaml([*_existing_to_entries(existing_decisions), *new_decisions]), encoding="utf-8")

    return {
        "output_path": str(destination),
        "existing_count": len(existing_decisions),
        "written_count": len(new_decisions),
        "total_count": len(existing_decisions) + len(new_decisions),
        "skipped_rejected_count": len(plan.rejected_candidate_decisions),
        "skipped_pending_count": len(plan.pending_candidate_decisions),
    }


def _load_existing_decisions(existing_memory_path: str | Path | None) -> list[ApprovedDecision]:
    if existing_memory_path is None:
        return []
    memory = load_approved_decisions(existing_memory_path)
    if memory.status == "missing":
        return []
    if memory.status != "available":
        raise ValueError(f"Existing approved decision memory could not be loaded: {memory.status}")
    return list(memory.decisions)


def _raise_for_duplicate_ids(existing: list[ApprovedDecision], new: list[dict[str, Any]]) -> None:
    seen = {decision.id for decision in existing}
    for decision in new:
        decision_id = str(decision.get("id") or "")
        if decision_id in seen:
            raise ValueError(f"Duplicate approved decision id: {decision_id}")
        seen.add(decision_id)


def _existing_to_entries(decisions: list[ApprovedDecision]) -> list[dict[str, Any]]:
    return [
        {
            "id": decision.id,
            "status": decision.status,
            "title": decision.title,
            "type": decision.type,
            "scope": decision.scope.to_dict(),
            "must": list(decision.must),
            "must_not": list(decision.must_not),
            "requires_approval_for": list(decision.requires_approval_for),
            "approved_by": list(decision.approved_by),
            "evidence": [item.to_dict() for item in decision.evidence],
            "created_at": decision.created_at,
        }
        for decision in decisions
    ]


def _normalize_decision_entry(entry: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(entry.get("source_candidate_id") or entry.get("source") or entry.get("id") or "")
    normalized = {
        "id": str(entry.get("id") or candidate_id),
        "status": str(entry.get("status") or "approved"),
        "title": str(entry.get("title") or ""),
        "type": str(entry.get("type") or entry.get("decision_type") or "decision"),
        "scope": _scope(entry.get("scope")),
        "must": _as_list(entry.get("must")),
        "must_not": _as_list(entry.get("must_not")),
        "requires_approval_for": _as_list(entry.get("requires_approval_for")),
        "approved_by": _as_list(entry.get("approved_by")),
        "evidence": _evidence(entry.get("evidence")),
        "created_at": entry.get("created_at") or entry.get("approved_at"),
        "source_candidate_id": candidate_id,
    }
    return normalized


def _scope(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {"applies_to_paths": [], "applies_to_concepts": []}
    return {
        "applies_to_paths": _as_list(value.get("applies_to_paths")),
        "applies_to_concepts": _as_list(value.get("applies_to_concepts")),
    }


def _evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            evidence.append(
                {
                    "source_type": str(item.get("source_type") or ""),
                    "quote": str(item.get("quote") or ""),
                }
            )
    return evidence


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    clean = str(value)
    return [clean] if clean else []


def _decisions_to_yaml(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "decisions: []\n"
    lines = ["decisions:"]
    for decision in decisions:
        lines.append(f"  - id: {_yaml_scalar(decision.get('id'))}")
        lines.append(f"    status: {_yaml_scalar(decision.get('status') or 'approved')}")
        lines.append(f"    title: {_yaml_scalar(decision.get('title'))}")
        lines.append(f"    type: {_yaml_scalar(decision.get('type') or 'decision')}")
        lines.append("    scope:")
        _append_list(lines, "applies_to_paths", decision.get("scope", {}).get("applies_to_paths", []), indent=6)
        _append_list(lines, "applies_to_concepts", decision.get("scope", {}).get("applies_to_concepts", []), indent=6)
        _append_list(lines, "must", decision.get("must", []), indent=4)
        _append_list(lines, "must_not", decision.get("must_not", []), indent=4)
        _append_list(lines, "requires_approval_for", decision.get("requires_approval_for", []), indent=4)
        _append_list(lines, "approved_by", decision.get("approved_by", []), indent=4)
        lines.append("    evidence:")
        evidence = decision.get("evidence") or []
        if evidence:
            for item in evidence:
                lines.append(f"      - source_type: {_yaml_scalar(item.get('source_type'))}")
                lines.append(f"        quote: {_yaml_scalar(item.get('quote'))}")
        else:
            lines[-1] = "    evidence: []"
        if decision.get("created_at"):
            lines.append(f"    created_at: {_yaml_scalar(decision.get('created_at'), force_quote=True)}")
        if decision.get("source_candidate_id"):
            lines.append(f"    source_candidate_id: {_yaml_scalar(decision.get('source_candidate_id'))}")
    return "\n".join(lines) + "\n"


def _append_list(lines: list[str], key: str, values: list[str], *, indent: int) -> None:
    prefix = " " * indent
    if not values:
        lines.append(f"{prefix}{key}: []")
        return
    lines.append(f"{prefix}{key}:")
    for value in values:
        lines.append(f"{prefix}  - {_yaml_scalar(value)}")


def _yaml_scalar(value: Any, *, force_quote: bool = False) -> str:
    text = "" if value is None else str(value)
    needs_quote = force_quote or not text or any(char in text for char in [":", "#", "\"", "\n"])
    if not needs_quote:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
