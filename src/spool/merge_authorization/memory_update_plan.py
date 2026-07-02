from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .merge_authorization_result import MergeAuthorizationResult
from .reviewer_actions import ReviewerAction


@dataclass
class DecisionMemoryUpdatePlan:
    approved_decisions: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidate_decisions: list[str] = field(default_factory=list)
    pending_candidate_decisions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved_decisions": list(self.approved_decisions),
            "rejected_candidate_decisions": list(self.rejected_candidate_decisions),
            "pending_candidate_decisions": list(self.pending_candidate_decisions),
            "notes": list(self.notes),
        }


def build_memory_update_plan(
    result: MergeAuthorizationResult,
    reviewer_actions: list[ReviewerAction],
    *,
    approved_at: str | None = None,
) -> DecisionMemoryUpdatePlan:
    candidates = result.candidate_by_id()
    approved_ids: list[str] = []
    rejected_ids: list[str] = []
    approved_decisions: list[dict[str, Any]] = []

    for action in reviewer_actions:
        if action.candidate_decision_id and action.candidate_decision_id not in candidates:
            raise ValueError(f"Unknown candidate decision id: {action.candidate_decision_id}")
        if action.action_type == "approve_candidate_decision":
            candidate_id = str(action.candidate_decision_id)
            if candidate_id not in approved_ids:
                approved_ids.append(candidate_id)
                approved_decisions.append(_approved_decision_entry(candidates[candidate_id], action, approved_at))
        elif action.action_type == "reject_candidate_decision":
            candidate_id = str(action.candidate_decision_id)
            if candidate_id not in rejected_ids:
                rejected_ids.append(candidate_id)

    pending = [
        candidate_id
        for candidate_id in candidates
        if candidate_id not in approved_ids and candidate_id not in rejected_ids
    ]
    notes = [
        "Merge approval and decision-memory approval are separate reviewer decisions.",
    ]
    if pending:
        notes.append("Candidate decisions remain unapproved unless explicitly approved by a reviewer.")

    return DecisionMemoryUpdatePlan(
        approved_decisions=approved_decisions,
        rejected_candidate_decisions=rejected_ids,
        pending_candidate_decisions=pending,
        notes=notes,
    )


def _approved_decision_entry(
    candidate: dict[str, Any],
    action: ReviewerAction,
    approved_at: str | None,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("id") or "")
    entry = {
        "id": candidate_id,
        "status": "approved",
        "title": str(candidate.get("title") or ""),
        "type": str(candidate.get("decision_type") or candidate.get("type") or "decision"),
        "scope": _candidate_scope(candidate),
        "must": [str(candidate.get("quote") or candidate.get("title") or "")],
        "must_not": [],
        "requires_approval_for": [],
        "approved_by": [action.reviewer],
        "evidence": [
            {
                "source_type": "candidate_decision",
                "quote": str(candidate.get("quote") or ""),
            }
        ],
        "source": candidate_id,
    }
    if approved_at:
        entry["approved_at"] = approved_at
    return entry


def _candidate_scope(candidate: dict[str, Any]) -> dict[str, list[str]]:
    scope = candidate.get("scope")
    if not isinstance(scope, dict):
        return {"applies_to_paths": [], "applies_to_concepts": []}
    return {
        "applies_to_paths": [str(item) for item in scope.get("applies_to_paths") or []],
        "applies_to_concepts": [str(item) for item in scope.get("applies_to_concepts") or []],
    }
