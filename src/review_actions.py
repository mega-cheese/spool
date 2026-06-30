from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .memory_objects import MEMORY_STATUSES, MEMORY_TYPES, REVIEW_STATES, MemoryObject


ALLOWED_ACTIONS = {
    "approve",
    "reject",
    "edit",
    "mark_needs_review",
    "mark_resolved",
    "mark_unresolved",
    "mark_superseded",
    "mark_stale",
}
SAFE_EDIT_FIELDS = {"title", "summary", "owner_names", "status", "type", "metadata", "due_date", "review_state"}


@dataclass
class ReviewResult:
    approved_memory: list[MemoryObject] = field(default_factory=list)
    rejected_memory: list[MemoryObject] = field(default_factory=list)
    updated_candidates: list[MemoryObject] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def create_review_action(
    memory_id: str,
    action: str,
    reviewer: str = "human",
    reason: str | None = None,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = _now()
    digest = hashlib.sha1("|".join([memory_id, action, reviewer, created_at]).encode("utf-8")).hexdigest()
    return {
        "action_id": f"review_{digest[:12]}",
        "memory_id": memory_id,
        "action": action,
        "reviewer": reviewer,
        "reason": reason,
        "patch": patch or {},
        "created_at": created_at,
    }


def validate_review_action(action: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    if not action.get("memory_id"):
        errors.append(_error("missing_memory_id", "Review action is missing memory_id."))
    if action.get("action") not in ALLOWED_ACTIONS:
        errors.append(_error("invalid_action", f"Unsupported review action: {action.get('action')}"))
    if action.get("action") == "edit" and not isinstance(action.get("patch"), dict):
        errors.append(_error("invalid_patch", "Edit action patch must be a JSON object."))
    return errors


def apply_review_action(candidate: MemoryObject | dict[str, Any], action: dict[str, Any]) -> tuple[MemoryObject | None, list[dict[str, Any]], list[dict[str, Any]]]:
    errors = validate_review_action(action)
    warnings: list[dict[str, Any]] = []
    memory = _copy_memory(candidate)
    if errors:
        return None, errors, warnings
    if memory.memory_id != action.get("memory_id"):
        return None, [_error("memory_id_mismatch", "Review action memory_id does not match candidate.")], warnings

    action_name = str(action["action"])
    reason = action.get("reason")
    patch = dict(action.get("patch") or {})

    if action_name == "approve":
        if memory.evidence_state in {"unsupported", "contradicted"} and not patch.get("force"):
            return None, [_error("unsupported_approval", "Unsupported or contradicted memory cannot be approved without force.")], warnings
        memory.review_state = "approved"
        if memory.type == "decision":
            memory.status = "approved"
        elif memory.type in {"commitment", "open_question"}:
            memory.status = _patched_status_or_existing(patch, memory.status, default="unresolved")
        else:
            memory.status = _patched_status_or_existing(patch, memory.status, default="approved")
    elif action_name == "reject":
        memory.review_state = "rejected"
        memory.status = "rejected"
        if reason:
            memory.metadata = {**memory.metadata, "rejection_reason": reason}
    elif action_name == "edit":
        edit_errors = _apply_edit(memory, patch)
        if edit_errors:
            return None, edit_errors, warnings
        if "review_state" not in patch:
            memory.review_state = "edited"
    elif action_name == "mark_needs_review":
        memory.review_state = "needs_review"
        if reason:
            memory.metadata = {**memory.metadata, "review_note": reason}
    elif action_name == "mark_resolved":
        if memory.type != "commitment":
            return None, [_error("invalid_target_type", "Only commitments can be marked resolved.")], warnings
        memory.status = "resolved"
        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
        if reason:
            memory.metadata = {**memory.metadata, "resolved_reason": reason}
    elif action_name == "mark_unresolved":
        if memory.type not in {"commitment", "open_question"}:
            return None, [_error("invalid_target_type", "Only commitments or open questions can be marked unresolved.")], warnings
        memory.status = "unresolved"
        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
    elif action_name == "mark_superseded":
        memory.status = "superseded"
        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
        if reason:
            memory.metadata = {**memory.metadata, "superseded_reason": reason}
        else:
            warnings.append(_warning("missing_reason", "Superseded memory should include a reason."))
    elif action_name == "mark_stale":
        memory.status = "stale"
        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
        if reason:
            memory.metadata = {**memory.metadata, "stale_reason": reason}
    memory.updated_at = action.get("created_at") or _now()
    return memory, errors, warnings


def apply_review_actions(
    candidates: list[MemoryObject],
    existing_approved: list[MemoryObject],
    existing_rejected: list[MemoryObject],
    actions: list[dict[str, Any]],
) -> ReviewResult:
    result = ReviewResult(
        approved_memory=list(existing_approved),
        rejected_memory=list(existing_rejected),
        updated_candidates=list(candidates),
        actions=list(actions),
    )
    for action in actions:
        result = _apply_one_to_result(result, action)
    return result


def latest_actions_by_memory_id(actions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for action in actions:
        memory_id = str(action.get("memory_id") or "")
        if not memory_id:
            continue
        if memory_id not in latest or str(action.get("created_at") or "") >= str(latest[memory_id].get("created_at") or ""):
            latest[memory_id] = action
    return latest


def _apply_one_to_result(result: ReviewResult, action: dict[str, Any]) -> ReviewResult:
    errors = validate_review_action(action)
    if errors:
        result.errors.extend(errors)
        return result
    memory_id = str(action["memory_id"])
    candidate = find_memory_by_id(result.updated_candidates, memory_id)
    target = candidate or find_memory_by_id(result.approved_memory, memory_id) or find_memory_by_id(result.rejected_memory, memory_id)
    if target is None:
        result.errors.append(_error("memory_not_found", f"No memory object found for {memory_id}.", memory_id=memory_id))
        return result
    updated, action_errors, warnings = apply_review_action(target, action)
    result.errors.extend(action_errors)
    result.warnings.extend(warnings)
    if updated is None:
        return result
    result.updated_candidates = _upsert_memory(result.updated_candidates, updated)
    if updated.review_state == "approved":
        result.rejected_memory = _remove_memory(result.rejected_memory, updated.memory_id)
        result.approved_memory = _upsert_memory(result.approved_memory, updated)
    elif updated.review_state == "rejected":
        result.approved_memory = _remove_memory(result.approved_memory, updated.memory_id)
        result.rejected_memory = _upsert_memory(result.rejected_memory, updated)
    elif action.get("action") == "edit" and find_memory_by_id(result.approved_memory, updated.memory_id):
        result.approved_memory = _upsert_memory(result.approved_memory, updated)
    elif action.get("action") == "edit" and find_memory_by_id(result.rejected_memory, updated.memory_id):
        result.rejected_memory = _upsert_memory(result.rejected_memory, updated)
    return result


def find_memory_by_id(objects: list[MemoryObject], memory_id: str) -> MemoryObject | None:
    return next((item for item in objects if item.memory_id == memory_id), None)


def _apply_edit(memory: MemoryObject, patch: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    disallowed = sorted(set(patch) - SAFE_EDIT_FIELDS)
    if disallowed:
        errors.append(_error("unsafe_edit_field", f"Edit patch contains unsafe fields: {', '.join(disallowed)}"))
    if errors:
        return errors
    original_memory_id = memory.metadata.get("original_memory_id") or memory.memory_id
    for key, value in patch.items():
        if key == "metadata":
            if not isinstance(value, dict):
                errors.append(_error("invalid_metadata_patch", "metadata patch must be an object."))
                continue
            memory.metadata = {**memory.metadata, **value}
        elif key == "status":
            if value not in MEMORY_STATUSES:
                errors.append(_error("invalid_status", f"Invalid memory status: {value}"))
                continue
            memory.status = str(value)
        elif key == "review_state":
            if value not in REVIEW_STATES:
                errors.append(_error("invalid_review_state", f"Invalid review state: {value}"))
                continue
            memory.review_state = str(value)
        elif key == "type":
            if value not in MEMORY_TYPES:
                errors.append(_error("invalid_memory_type", f"Invalid memory type: {value}"))
                continue
            memory.type = str(value)
        else:
            setattr(memory, key, value)
    if errors:
        return errors
    if not (memory.sources or memory.source_event_ids or memory.quote):
        return [_error("source_evidence_required", "Edit cannot remove all source evidence.")]
    memory.metadata = {**memory.metadata, "original_memory_id": original_memory_id, "edited_from": original_memory_id}
    return []


def _patched_status_or_existing(patch: dict[str, Any], current: str, default: str) -> str:
    status = patch.get("status") or current or default
    return str(status) if status in MEMORY_STATUSES else default


def _copy_memory(candidate: MemoryObject | dict[str, Any]) -> MemoryObject:
    if isinstance(candidate, MemoryObject):
        return MemoryObject.from_dict(copy.deepcopy(candidate.to_dict()))
    return MemoryObject.from_dict(copy.deepcopy(candidate))


def _upsert_memory(objects: list[MemoryObject], memory: MemoryObject) -> list[MemoryObject]:
    updated = []
    found = False
    for item in objects:
        if item.memory_id == memory.memory_id:
            updated.append(memory)
            found = True
        else:
            updated.append(item)
    if not found:
        updated.append(memory)
    return updated


def _remove_memory(objects: list[MemoryObject], memory_id: str) -> list[MemoryObject]:
    return [item for item in objects if item.memory_id != memory_id]


def _error(code: str, message: str, memory_id: str | None = None) -> dict[str, Any]:
    result = {"code": code, "message": message}
    if memory_id:
        result["memory_id"] = memory_id
    return result


def _warning(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
