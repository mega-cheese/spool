from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_objects import MemoryObject
from .review_actions import apply_review_action, create_review_action, find_memory_by_id
from .review_policy import build_memory_state_debug


def load_approved_memory(path: str | Path) -> list[MemoryObject]:
    return _load_memory_list(path, "approved_memory")


def load_rejected_memory(path: str | Path) -> list[MemoryObject]:
    return _load_memory_list(path, "rejected_memory")


def load_candidate_memory(path: str | Path) -> list[MemoryObject]:
    return _load_memory_list(path, "candidates")


def load_review_actions(path: str | Path) -> list[dict[str, Any]]:
    review_path = Path(path)
    if not review_path.exists():
        return []
    with review_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return list(payload)
    return list(payload.get("actions") or [])


def save_approved_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = _memory_payload("approved_memory", "approved_count", objects, metadata)
    _write_json(path, payload, pretty=pretty)


def save_rejected_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = _memory_payload("rejected_memory", "rejected_count", objects, metadata)
    _write_json(path, payload, pretty=pretty)


def save_candidate_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = {
        "metadata": {
            **(metadata or {}),
            "generated_at": _now(),
            "candidate_count": len(objects),
        },
        "candidates": [obj.to_dict() for obj in objects],
    }
    _write_json(path, payload, pretty=pretty)


def save_review_actions(path: str | Path, actions: list[dict[str, Any]], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
    payload = {
        "metadata": {
            **(metadata or {}),
            "generated_at": _now(),
            "action_count": len(actions),
        },
        "actions": actions,
    }
    _write_json(path, payload, pretty=pretty)


def append_review_action(path: str | Path, action: dict[str, Any], metadata: dict[str, Any] | None = None, pretty: bool = False) -> list[dict[str, Any]]:
    actions = load_review_actions(path)
    actions.append(action)
    save_review_actions(path, actions, metadata=metadata, pretty=pretty)
    return actions


def approve_memory(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
    action = create_review_action(_copy_memory(candidate).memory_id, "approve", reviewer=reviewer)
    memory, errors, _warnings = apply_review_action(candidate, action)
    if errors or memory is None:
        raise ValueError(errors[0]["message"] if errors else "Unable to approve memory.")
    if action_log is not None:
        action_log.append(action)
    return memory


def reject_memory(
    candidate: MemoryObject | dict[str, Any],
    reviewer: str = "system",
    reason: str | None = None,
    action_log: list[dict[str, Any]] | None = None,
) -> MemoryObject:
    action = create_review_action(_copy_memory(candidate).memory_id, "reject", reviewer=reviewer, reason=reason)
    memory, errors, _warnings = apply_review_action(candidate, action)
    if errors or memory is None:
        raise ValueError(errors[0]["message"] if errors else "Unable to reject memory.")
    if action_log is not None:
        action_log.append(action)
    return memory


def edit_memory(
    candidate: MemoryObject | dict[str, Any],
    patch: dict[str, Any],
    reviewer: str = "system",
    action_log: list[dict[str, Any]] | None = None,
) -> MemoryObject:
    action = create_review_action(_copy_memory(candidate).memory_id, "edit", reviewer=reviewer, patch=patch)
    memory, errors, _warnings = apply_review_action(candidate, action)
    if errors or memory is None:
        raise ValueError(errors[0]["message"] if errors else "Unable to edit memory.")
    if action_log is not None:
        action_log.append(action)
    return memory


def mark_needs_review(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", reason: str | None = None, action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
    action = create_review_action(_copy_memory(candidate).memory_id, "mark_needs_review", reviewer=reviewer, reason=reason)
    memory, errors, _warnings = apply_review_action(candidate, action)
    if errors or memory is None:
        raise ValueError(errors[0]["message"] if errors else "Unable to mark memory needs-review.")
    if action_log is not None:
        action_log.append(action)
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


def find_memory_by_id_in_list(candidates: list[MemoryObject], memory_id: str) -> MemoryObject | None:
    return find_memory_by_id(candidates, memory_id)


def apply_action_to_store(run_dir: str | Path, action: dict[str, Any], pretty: bool = False) -> dict[str, Any]:
    root = Path(run_dir)
    candidate_path = root / "candidate_memory.json"
    approved_path = root / "approved_memory.json"
    rejected_path = root / "rejected_memory.json"
    actions_path = root / "review_actions.json"
    debug_path = root / "memory_state_debug.json"

    candidate_payload = _load_payload(candidate_path, default={"metadata": {}, "candidates": []})
    approved_payload = _load_payload(approved_path, default={"metadata": {}, "approved_memory": []})
    rejected_payload = _load_payload(rejected_path, default={"metadata": {}, "rejected_memory": []})
    actions_payload = _load_payload(actions_path, default={"metadata": {}, "actions": []})

    candidates = [MemoryObject.from_dict(item) for item in candidate_payload.get("candidates", [])]
    approved = [MemoryObject.from_dict(item) for item in approved_payload.get("approved_memory", [])]
    rejected = [MemoryObject.from_dict(item) for item in rejected_payload.get("rejected_memory", [])]
    actions = list(actions_payload.get("actions") or [])

    target = (
        find_memory_by_id(candidates, str(action.get("memory_id") or ""))
        or find_memory_by_id(approved, str(action.get("memory_id") or ""))
        or find_memory_by_id(rejected, str(action.get("memory_id") or ""))
    )
    if target is None:
        error = {"code": "memory_not_found", "message": f"No memory object found for {action.get('memory_id')}.", "memory_id": action.get("memory_id")}
        return _store_result(candidates, approved, rejected, actions, [error], [], action=None)

    updated, errors, warnings = apply_review_action(target, action)
    if errors or updated is None:
        return _store_result(candidates, approved, rejected, actions, errors, warnings, action=None)

    candidates = _upsert_memory(candidates, updated)
    if updated.review_state == "approved":
        approved = _upsert_memory(approved, updated)
        rejected = _remove_memory(rejected, updated.memory_id)
    elif updated.review_state == "rejected":
        rejected = _upsert_memory(rejected, updated)
        approved = _remove_memory(approved, updated.memory_id)
    elif find_memory_by_id(approved, updated.memory_id):
        approved = _upsert_memory(approved, updated)
    elif find_memory_by_id(rejected, updated.memory_id):
        rejected = _upsert_memory(rejected, updated)
    actions.append(action)

    base_metadata = dict(candidate_payload.get("metadata") or {})
    input_file = str(base_metadata.get("input_file") or approved_payload.get("metadata", {}).get("input_file") or rejected_payload.get("metadata", {}).get("input_file") or "")
    save_candidate_memory(candidate_path, candidates, metadata=base_metadata, pretty=pretty)
    save_approved_memory(approved_path, approved, metadata={"input_file": input_file}, pretty=pretty)
    save_rejected_memory(rejected_path, rejected, metadata={"input_file": input_file}, pretty=pretty)
    save_review_actions(actions_path, actions, metadata={"input_file": input_file}, pretty=pretty)
    debug = build_memory_state_debug(candidates, input_file=input_file, review_actions=actions)
    _write_json(debug_path, debug, pretty=pretty)
    return _store_result(candidates, approved, rejected, actions, [], warnings, action=action)


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


def _load_payload(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _upsert_memory(objects: list[MemoryObject], memory: MemoryObject) -> list[MemoryObject]:
    result = []
    found = False
    for item in objects:
        if item.memory_id == memory.memory_id:
            result.append(memory)
            found = True
        else:
            result.append(item)
    if not found:
        result.append(memory)
    return result


def _remove_memory(objects: list[MemoryObject], memory_id: str) -> list[MemoryObject]:
    return [item for item in objects if item.memory_id != memory_id]


def _store_result(
    candidates: list[MemoryObject],
    approved: list[MemoryObject],
    rejected: list[MemoryObject],
    actions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    action: dict[str, Any] | None,
) -> dict[str, Any]:
    memory_id = action.get("memory_id") if action else None
    updated = find_memory_by_id(candidates, str(memory_id)) if memory_id else None
    return {
        "approved_memory": [item.to_dict() for item in approved],
        "rejected_memory": [item.to_dict() for item in rejected],
        "updated_candidates": [item.to_dict() for item in candidates],
        "actions": actions,
        "errors": errors,
        "warnings": warnings,
        "updated_memory": updated.to_dict() if updated else None,
    }


def _write_json(path: str | Path, payload: Any, pretty: bool = False) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
