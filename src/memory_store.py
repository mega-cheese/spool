from __future__ import annotations

import copy
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_objects import MemoryObject


def load_approved_memory(path: str | Path) -> list[MemoryObject]:
    return _load_memory_list(path, "approved_memory")


def load_rejected_memory(path: str | Path) -> list[MemoryObject]:
    return _load_memory_list(path, "rejected_memory")


def save_approved_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = _memory_payload("approved_memory", "approved_count", objects, metadata)
    _write_json(path, payload, pretty=pretty)


def save_rejected_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = _memory_payload("rejected_memory", "rejected_count", objects, metadata)
    _write_json(path, payload, pretty=pretty)


def approve_memory(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
    memory = _copy_memory(candidate)
    memory.review_state = "approved"
    memory.status = "approved"
    memory.updated_at = _now()
    _record_action(action_log, memory, "approve", reviewer)
    return memory


def reject_memory(
    candidate: MemoryObject | dict[str, Any],
    reviewer: str = "system",
    reason: str | None = None,
    action_log: list[dict[str, Any]] | None = None,
) -> MemoryObject:
    memory = _copy_memory(candidate)
    memory.review_state = "rejected"
    memory.status = "rejected"
    memory.updated_at = _now()
    if reason:
        memory.metadata = {**memory.metadata, "rejection_reason": reason}
    _record_action(action_log, memory, "reject", reviewer, reason=reason)
    return memory


def edit_memory(
    candidate: MemoryObject | dict[str, Any],
    patch: dict[str, Any],
    reviewer: str = "system",
    action_log: list[dict[str, Any]] | None = None,
) -> MemoryObject:
    memory = _copy_memory(candidate)
    for key, value in patch.items():
        if hasattr(memory, key):
            setattr(memory, key, value)
        else:
            memory.metadata[key] = value
    memory.review_state = "edited"
    memory.updated_at = _now()
    _record_action(action_log, memory, "edit", reviewer, patch=patch)
    return memory


def mark_needs_review(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", reason: str | None = None, action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
    memory = _copy_memory(candidate)
    memory.review_state = "needs_review"
    memory.status = "needs_review"
    memory.updated_at = _now()
    _record_action(action_log, memory, "mark_needs_review", reviewer, reason=reason)
    return memory


def review_actions_payload(input_file: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "metadata": {
            "input_file": input_file,
            "generated_at": _now(),
            "action_count": len(actions),
        },
        "actions": actions,
    }


def approved_memory_payload(input_file: str, objects: list[MemoryObject]) -> dict[str, Any]:
    return _memory_payload("approved_memory", "approved_count", objects, {"input_file": input_file})


def rejected_memory_payload(input_file: str, objects: list[MemoryObject]) -> dict[str, Any]:
    return _memory_payload("rejected_memory", "rejected_count", objects, {"input_file": input_file})


def _load_memory_list(path: str | Path, key: str) -> list[MemoryObject]:
    memory_path = Path(path)
    if not memory_path.exists():
        return []
    with memory_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        items = payload
    else:
        items = payload.get(key, [])
    return [MemoryObject.from_dict(item) for item in items]


def _memory_payload(key: str, count_key: str, objects: list[MemoryObject], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "metadata": {
            **(metadata or {}),
            "generated_at": _now(),
            count_key: len(objects),
        },
        key: [obj.to_dict() for obj in objects],
    }


def _copy_memory(candidate: MemoryObject | dict[str, Any]) -> MemoryObject:
    if isinstance(candidate, MemoryObject):
        return MemoryObject.from_dict(copy.deepcopy(candidate.to_dict()))
    return MemoryObject.from_dict(copy.deepcopy(candidate))


def _record_action(
    action_log: list[dict[str, Any]] | None,
    memory: MemoryObject,
    action: str,
    reviewer: str,
    reason: str | None = None,
    patch: dict[str, Any] | None = None,
) -> None:
    if action_log is None:
        return
    created_at = _now()
    digest = hashlib.sha1("|".join([memory.memory_id, action, reviewer, created_at]).encode("utf-8")).hexdigest()
    action_log.append(
        {
            "action_id": f"review_{digest[:12]}",
            "memory_id": memory.memory_id,
            "action": action,
            "reviewer": reviewer,
            "reason": reason,
            "patch": patch or {},
            "created_at": created_at,
        }
    )


def _write_json(path: str | Path, payload: Any, pretty: bool = False) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
