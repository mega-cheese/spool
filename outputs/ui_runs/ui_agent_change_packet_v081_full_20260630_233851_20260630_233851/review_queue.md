# Review Queue

## 1. Task table may have been under-extracted

Severity: high
Reason: extraction_warning
Candidate decision: Task table may have been under-extracted

Summary:
Document appears to contain a task table, but many likely task rows were not extracted.

Recommended action:
Review block classification and task extraction output.

Sources:
* Unknown: ```text
Implemented v0.8.1 review action application layer.
Changed:
Added src/review_actions.py
Added review_memory.py
Extended src/memory_store.py
Updated src/review_policy.py
Added tests/test_review_actions.py
Updated README review-action docs

What now works:
- Apply approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale
- Update candidate_memory.json, approved_memory.json, rejected_memory.json, and memory_state_debug.json
- Append actions to review_actions.json
- Preserve source evidence through edits
- Prevent silent approval of unsupported/contradicted memory
- Avoid duplicate approved/rejected entries by memory_id

CLI example:
venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action approve --reviewer Owen

Validation:
venv/bin/python -m unittest discover -s tests: PASS, 212 tests
meeting_minutes heuristic: PASS
meeting_minutes LLM: PASS
docs LLM: PASS
review LLM: PASS
conflict LLM: PASS

Manual smoke:
Created a fresh Meeting #12 run.
Approved one commitment into approved_memory.json.
Rejected one candidate into rejected_memory.json.
Edited one weak item while preserving source quote/evidence.
Confirmed review_actions.json appended actions and memory_state_debug.json reflects latest action state.

```

---
* Unknown: ```text
venv/bin/python -m unittest discover -s tests: PASS, 212 tests

meeting_minutes heuristic: PASS
meeting_minutes LLM: PASS
docs LLM: PASS
review LLM: PASS
conflict LLM: PASS
```

Manual smoke test claimed:

```text
Created a fresh Meeting #12 run.
Approved one commitment into approved_memory.json.
Rejected one candidate into rejected_memory.json.
Edited one weak item while preserving source quote/evidence.
Confirmed review_actions.json appended actions and memory_state_debug.json reflects latest action state.
```

---
* Unknown: diff --git a/review_memory.py b/review_memory.py
new file mode 100644
index 0000000..00469ff
--- /dev/null
+++ b/review_memory.py
@@ -0,0 +1,74 @@
+#!/usr/bin/env python3
+from __future__ import annotations
+
+import argparse
+import json
+import sys
+from pathlib import Path
+from typing import Any
+
+from src.memory_store import apply_action_to_store
+from src.review_actions import create_review_action
+
+
+def main() -> int:
+    parser = argparse.ArgumentParser(description="Apply a review action to a Spool run folder.")
+    parser.add_argument("--run-dir", required=True, help="Path to a run folder containing candidate_memory.json.")
+    parser.add_argument("--memory-id", required=True)
+    parser.add_argument(
+        "--action",
+        required=True,
+        choices=[
+            "approve",
+            "reject",
+            "edit",
+            "mark_needs_review",
+            "mark_resolved",
+            "mark_unresolved",
+            "mark_superseded",
+            "mark_stale",
+        ],
+    )
+    parser.add_argument("--reviewer", default="human")
+    parser.add_argument("--reason")
+    parser.add_argument("--patch", default="{}")
+    parser.add_argument("--pretty", action="store_true")
+    args = parser.parse_args()
+
+    try:
+        patch = json.loads(args.patch)
+    except json.JSONDecodeError as exc:
+        print(f"Invalid --patch JSON: {exc}", file=sys.stderr)
+        return 2
+    if not isinstance(patch, dict):
+        print("--patch must be a JSON object.", file=sys.stderr)
+        return 2
+
+    run_dir = Path(args.run_dir)
+    action = create_review_action(
+        args.memory_id,
+        args.action,
+        reviewer=args.reviewer,
+        reason=args.reason,
+        patch=patch,
+    )
+    result = apply_action_to_store(run_dir, action, pretty=args.pretty)
+    if result["errors"]:
+        for error in result["errors"]:
+            print(f"Error: {error.get('code')}: {error.get('message')}", file=sys.stderr)
+        return 1
+    updated = result.get("updated_memory") or {}
+    print(f"memory_id: {args.memory_id}")
+    print(f"action: {args.action}")
+    print(f"review_state: {updated.get('review_state')}")
+    print(f"status: {updated.get('status')}")
+    print(f"approved_count: {len(result['approved_memory'])}")
+    print(f"rejected_count: {len(result['rejected_memory'])}")
+    print(f"action_count: {len(result['actions'])}")
+    if result["warnings"]:
+        print("warnings: " + ", ".join(str(warning.get("code")) for warning in result["warnings"]))
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/src/memory_store.py b/src/memory_store.py
index 8e3a796..d5b79b4 100644
--- a/src/memory_store.py
+++ b/src/memory_store.py
@@ -2,12 +2,13 @@ from __future__ import annotations

 import copy
 import json
-import hashlib
 from datetime import datetime, timezone
 from pathlib import Path
 from typing import Any

 from .memory_objects import MemoryObject
+from .review_actions import apply_review_action, create_review_action, find_memory_by_id
+from .review_policy import build_memory_state_debug


 def load_approved_memory(path: str | Path) -> list[MemoryObject]:
@@ -18,6 +19,21 @@ def load_rejected_memory(path: str | Path) -> list[MemoryObject]:
     return _load_memory_list(path, "rejected_memory")


+def load_candidate_memory(path: str | Path) -> list[MemoryObject]:
+    return _load_memory_list(path, "candidates")
+
+
+def load_review_actions(path: str | Path) -> list[dict[str, Any]]:
+    review_path = Path(path)
+    if not review_path.exists():
+        return []
+    with review_path.open("r", encoding="utf-8") as handle:
+        payload = json.load(handle)
+    if isinstance(payload, list):
+        return list(payload)
+    return list(payload.get("actions") or [])
+
+
 def save_approved_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
     payload = _memory_payload("approved_memory", "approved_count", objects, metadata)
     _write_json(path, payload, pretty=pretty)
@@ -28,12 +44,44 @@ def save_rejected_memory(path: str | Path, objects: list[MemoryObject], metadata
     _write_json(path, payload, pretty=pretty)


+def save_candidate_memory(path: str | Path, objects: list[MemoryObject], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
+    payload = {
+        "metadata": {
+            **(metadata or {}),
+            "generated_at": _now(),
+            "candidate_count": len(objects),
+        },
+        "candidates": [obj.to_dict() for obj in objects],
+    }
+    _write_json(path, payload, pretty=pretty)
+
+
+def save_review_actions(path: str | Path, actions: list[dict[str, Any]], metadata: dict[str, Any] | None = None, pretty: bool = False) -> None:
+    payload = {
+        "metadata": {
+            **(metadata or {}),
+            "generated_at": _now(),
+            "action_count": len(actions),
+        },
+        "actions": actions,
+    }
+    _write_json(path, payload, pretty=pretty)
+
+
+def append_review_action(path: str | Path, action: dict[str, Any], metadata: dict[str, Any] | None = None, pretty: bool = False) -> list[dict[str, Any]]:
+    actions = load_review_actions(path)
+    actions.append(action)
+    save_review_actions(path, actions, metadata=metadata, pretty=pretty)
+    return actions
+
+
 def approve_memory(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
-    memory = _copy_memory(candidate)
-    memory.review_state = "approved"
-    memory.status = "approved"
-    memory.updated_at = _now()
-    _record_action(action_log, memory, "approve", reviewer)
+    action = create_review_action(_copy_memory(candidate).memory_id, "approve", reviewer=reviewer)
+    memory, errors, _warnings = apply_review_action(candidate, action)
+    if errors or memory is None:
+        raise ValueError(errors[0]["message"] if errors else "Unable to approve memory.")
+    if action_log is not None:
+        action_log.append(action)
     return memory


@@ -43,13 +91,12 @@ def reject_memory(
     reason: str | None = None,
     action_log: list[dict[str, Any]] | None = None,
 ) -> MemoryObject:
-    memory = _copy_memory(candidate)
-    memory.review_state = "rejected"
-    memory.status = "rejected"
-    memory.updated_at = _now()
-    if reason:
-        memory.metadata = {**memory.metadata, "rejection_reason": reason}
-    _record_action(action_log, memory, "reject", reviewer, reason=reason)
+    action = create_review_action(_copy_memory(candidate).memory_id, "reject", reviewer=reviewer, reason=reason)
+    memory, errors, _warnings = apply_review_action(candidate, action)
+    if errors or memory is None:
+        raise ValueError(errors[0]["message"] if errors else "Unable to reject memory.")
+    if action_log is not None:
+        action_log.append(action)
     return memory


@@ -59,24 +106,22 @@ def edit_memory(
     reviewer: str = "system",
     action_log: list[dict[str, Any]] | None = None,
 ) -> MemoryObject:
-    memory = _copy_memory(candidate)
-    for key, value in patch.items():
-        if hasattr(memory, key):
-            setattr(memory, key, value)
-        else:
-            memory.metadata[key] = value
-    memory.review_state = "edited"
-    memory.updated_at = _now()
-    _record_action(action_log, memory, "edit", reviewer, patch=patch)
+    action = create_review_action(_copy_memory(candidate).memory_id, "edit", reviewer=reviewer, patch=patch)
+    memory, errors, _warnings = apply_review_action(candidate, action)
+    if errors or memory is None:
+        raise ValueError(errors[0]["message"] if errors else "Unable to edit memory.")
+    if action_log is not None:
+        action_log.append(action)
     return memory


 def mark_needs_review(candidate: MemoryObject | dict[str, Any], reviewer: str = "system", reason: str | None = None, action_log: list[dict[str, Any]] | None = None) -> MemoryObject:
-    memory = _copy_memory(candidate)
-    memory.review_state = "needs_review"
-    memory.status = "needs_review"
-    memory.updated_at = _now()
-    _record_action(action_log, memory, "mark_needs_review", reviewer, reason=reason)
+    action = create_review_action(_copy_memory(candidate).memory_id, "mark_needs_review", reviewer=reviewer, reason=reason)
+    memory, errors, _warnings = apply_review_action(candidate, action)
+    if errors or memory is None:
+        raise ValueError(errors[0]["message"] if errors else "Unable to mark memory needs-review.")
+    if action_log is not None:
+        action_log.append(action)
     return memory


@@ -99,6 +144,65 @@ def rejected_memory_payload(input_file: str, objects: list[MemoryObject]) -> dic
     return _memory_payload("rejected_memory", "rejected_count", objects, {"input_file": input_file})


+def find_memory_by_id_in_list(candidates: list[MemoryObject], memory_id: str) -> MemoryObject | None:
+    return find_memory_by_id(candidates, memory_id)
+
+
+def apply_action_to_store(run_dir: str | Path, action: dict[str, Any], pretty: bool = False) -> dict[str, Any]:
+    root = Path(run_dir)
+    candidate_path = root / "candidate_memory.json"
+    approved_path = root / "approved_memory.json"
+    rejected_path = root / "rejected_memory.json"
+    actions_path = root / "review_actions.json"
+    debug_path = root / "memory_state_debug.json"
+
+    candidate_payload = _load_payload(candidate_path, default={"metadata": {}, "candidates": []})
+    approved_payload = _load_payload(approved_path, default={"metadata": {}, "approved_memory": []})
+    rejected_payload = _load_payload(rejected_path, default={"metadata": {}, "rejected_memory": []})
+    actions_payload = _load_payload(actions_path, default={"metadata": {}, "actions": []})
+
+    candidates = [MemoryObject.from_dict(item) for item in candidate_payload.get("candidates", [])]
+    approved = [MemoryObject.from_dict(item) for item in approved_payload.get("approved_memory", [])]
+    rejected = [MemoryObject.from_dict(item) for item in rejected_payload.get("rejected_memory", [])]
+    actions = list(actions_payload.get("actions") or [])
+
+    target = (
+        find_memory_by_id(candidates, str(action.get("memory_id") or ""))
+        or find_memory_by_id(approved, str(action.get("memory_id") or ""))
+        or find_memory_by_id(rejected, str(action.get("memory_id") or ""))
+    )
+    if target is None:
+        error = {"code": "memory_not_found", "message": f"No memory object found for {action.get('memory_id')}.", "memory_id": action.get("memory_id")}
+        return _store_result(candidates, approved, rejected, actions, [error], [], action=None)
+
+    updated, errors, warnings = apply_review_action(target, action)
+    if errors or updated is None:
+        return _store_result(candidates, approved, rejected, actions, errors, warnings, action=None)
+
+    candidates = _upsert_memory(candidates, updated)
+    if updated.review_state == "approved":
+        approved = _upsert_memory(approved, updated)
+        rejected = _remove_memory(rejected, updated.memory_id)
+    elif updated.review_state == "rejected":
+        rejected = _upsert_memory(rejected, updated)
+        approved = _remove_memory(approved, updated.memory_id)
+    elif find_memory_by_id(approved, updated.memory_id):
+        approved = _upsert_memory(approved, updated)
+    elif find_memory_by_id(rejected, updated.memory_id):
+        rejected = _upsert_memory(rejected, updated)
+    actions.append(action)
+
+    base_metadata = dict(candidate_payload.get("metadata") or {})
+    input_file = str(base_metadata.get("input_file") or approved_payload.get("metadata", {}).get("input_file") or rejected_payload.get("metadata", {}).get("input_file") or "")
+    save_candidate_memory(candidate_path, candidates, metadata=base_metadata, pretty=pretty)
+    save_approved_memory(approved_path, approved, metadata={"input_file": input_file}, pretty=pretty)
+    save_rejected_memory(rejected_path, rejected, metadata={"input_file": input_file}, pretty=pretty)
+    save_review_actions(actions_path, actions, metadata={"input_file": input_file}, pretty=pretty)
+    debug = build_memory_state_debug(candidates, input_file=input_file, review_actions=actions)
+    _write_json(debug_path, debug, pretty=pretty)
+    return _store_result(candidates, approved, rejected, actions, [], warnings, action=action)
+
+
 def _load_memory_list(path: str | Path, key: str) -> list[MemoryObject]:
     memory_path = Path(path)
     if not memory_path.exists():
@@ -129,29 +233,51 @@ def _copy_memory(candidate: MemoryObject | dict[str, Any]) -> MemoryObject:
     return MemoryObject.from_dict(copy.deepcopy(candidate))


-def _record_action(
-    action_log: list[dict[str, Any]] | None,
-    memory: MemoryObject,
-    action: str,
-    reviewer: str,
-    reason: str | None = None,
-    patch: dict[str, Any] | None = None,
-) -> None:
-    if action_log is None:
-        return
-    created_at = _now()
-    digest = hashlib.sha1("|".join([memory.memory_id, action, reviewer, created_at]).encode("utf-8")).hexdigest()
-    action_log.append(
-        {
-            "action_id": f"review_{digest[:12]}",
-            "memory_id": memory.memory_id,
-            "action": action,
-            "reviewer": reviewer,
-            "reason": reason,
-            "patch": patch or {},
-            "created_at": created_at,
-        }
-    )
+def _load_payload(path: Path, default: dict[str, Any]) -> dict[str, Any]:
+    if not path.exists():
+        return copy.deepcopy(default)
+    with path.open("r", encoding="utf-8") as handle:
+        return json.load(handle)
+
+
+def _upsert_memory(objects: list[MemoryObject], memory: MemoryObject) -> list[MemoryObject]:
+    result = []
+    found = False
+    for item in objects:
+        if item.memory_id == memory.memory_id:
+            result.append(memory)
+            found = True
+        else:
+            result.append(item)
+    if not found:
+        result.append(memory)
+    return result
+
+
+def _remove_memory(objects: list[MemoryObject], memory_id: str) -> list[MemoryObject]:
+    return [item for item in objects if item.memory_id != memory_id]
+
+
+def _store_result(
+    candidates: list[MemoryObject],
+    approved: list[MemoryObject],
+    rejected: list[MemoryObject],
+    actions: list[dict[str, Any]],
+    errors: list[dict[str, Any]],
+    warnings: list[dict[str, Any]],
+    action: dict[str, Any] | None,
+) -> dict[str, Any]:
+    memory_id = action.get("memory_id") if action else None
+    updated = find_memory_by_id(candidates, str(memory_id)) if memory_id else None
+    return {
+        "approved_memory": [item.to_dict() for item in approved],
+        "rejected_memory": [item.to_dict() for item in rejected],
+        "updated_candidates": [item.to_dict() for item in candidates],
+        "actions": actions,
+        "errors": errors,
+        "warnings": warnings,
+        "updated_memory": updated.to_dict() if updated else None,
+    }


 def _write_json(path: str | Path, payload: Any, pretty: bool = False) -> None:
diff --git a/src/review_actions.py b/src/review_actions.py
new file mode 100644
index 0000000..282ff2c
--- /dev/null
+++ b/src/review_actions.py
@@ -0,0 +1,274 @@
+from __future__ import annotations
+
+import copy
+import hashlib
+from dataclasses import dataclass, field
+from datetime import datetime, timezone
+from typing import Any
+
+from .memory_objects import MEMORY_STATUSES, MEMORY_TYPES, REVIEW_STATES, MemoryObject
+
+
+ALLOWED_ACTIONS = {
+    "approve",
+    "reject",
+    "edit",
+    "mark_needs_review",
+    "mark_resolved",
+    "mark_unresolved",
+    "mark_superseded",
+    "mark_stale",
+}
+SAFE_EDIT_FIELDS = {"title", "summary", "owner_names", "status", "type", "metadata", "due_date", "review_state"}
+
+
+@dataclass
+class ReviewResult:
+    approved_memory: list[MemoryObject] = field(default_factory=list)
+    rejected_memory: list[MemoryObject] = field(default_factory=list)
+    updated_candidates: list[MemoryObject] = field(default_factory=list)
+    actions: list[dict[str, Any]] = field(default_factory=list)
+    errors: list[dict[str, Any]] = field(default_factory=list)
+    warnings: list[dict[str, Any]] = field(default_factory=list)
+
+
+def create_review_action(
+    memory_id: str,
+    action: str,
+    reviewer: str = "human",
+    reason: str | None = None,
+    patch: dict[str, Any] | None = None,
+) -> dict[str, Any]:
+    created_at = _now()
+    digest = hashlib.sha1("|".join([memory_id, action, reviewer, created_at]).encode("utf-8")).hexdigest()
+    return {
+        "action_id": f"review_{digest[:12]}",
+        "memory_id": memory_id,
+        "action": action,
+        "reviewer": reviewer,
+        "reason": reason,
+        "patch": patch or {},
+        "created_at": created_at,
+    }
+
+
+def validate_review_action(action: dict[str, Any]) -> list[dict[str, Any]]:
+    errors = []
+    if not action.get("memory_id"):
+        errors.append(_error("missing_memory_id", "Review action is missing memory_id."))
+    if action.get("action") not in ALLOWED_ACTIONS:
+        errors.append(_error("invalid_action", f"Unsupported review action: {action.get('action')}"))
+    if action.get("action") == "edit" and not isinstance(action.get("patch"), dict):
+        errors.append(_error("invalid_patch", "Edit action patch must be a JSON object."))
+    return errors
+
+
+def apply_review_action(candidate: MemoryObject | dict[str, Any], action: dict[str, Any]) -> tuple[MemoryObject | None, list[dict[str, Any]], list[dict[str, Any]]]:
+    errors = validate_review_action(action)
+    warnings: list[dict[str, Any]] = []
+    memory = _copy_memory(candidate)
+    if errors:
+        return None, errors, warnings
+    if memory.memory_id != action.get("memory_id"):
+        return None, [_error("memory_id_mismatch", "Review action memory_id does not match candidate.")], warnings
+
+    action_name = str(action["action"])
+    reason = action.get("reason")
+    patch = dict(action.get("patch") or {})
+
+    if action_name == "approve":
+        if memory.evidence_state in {"unsupported", "contradicted"} and not patch.get("force"):
+            return None, [_error("unsupported_approval", "Unsupported or contradicted memory cannot be approved without force.")], warnings
+        memory.review_state = "approved"
+        if memory.type == "decision":
+            memory.status = "approved"
+        elif memory.type in {"commitment", "open_question"}:
+            memory.status = _patched_status_or_existing(patch, memory.status, default="unresolved")
+        else:
+            memory.status = _patched_status_or_existing(patch, memory.status, default="approved")
+    elif action_name == "reject":
+        memory.review_state = "rejected"
+        memory.status = "rejected"
+        if reason:
+            memory.metadata = {**memory.metadata, "rejection_reason": reason}
+    elif action_name == "edit":
+        edit_errors = _apply_edit(memory, patch)
+        if edit_errors:
+            return None, edit_errors, warnings
+        if "review_state" not in patch:
+            memory.review_state = "edited"
+    elif action_name == "mark_needs_review":
+        memory.review_state = "needs_review"
+        if reason:
+            memory.metadata = {**memory.metadata, "review_note": reason}
+    elif action_name == "mark_resolved":
+        if memory.type != "commitment":
+            return None, [_error("invalid_target_type", "Only commitments can be marked resolved.")], warnings
+        memory.status = "resolved"
+        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
+        if reason:
+            memory.metadata = {**memory.metadata, "resolved_reason": reason}
+    elif action_name == "mark_unresolved":
+        if memory.type not in {"commitment", "open_question"}:
+            return None, [_error("invalid_target_type", "Only commitments or open questions can be marked unresolved.")], warnings
+        memory.status = "unresolved"
+        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
+    elif action_name == "mark_superseded":
+        memory.status = "superseded"
+        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
+        if reason:
+            memory.metadata = {**memory.metadata, "superseded_reason": reason}
+        else:
+            warnings.append(_warning("missing_reason", "Superseded memory should include a reason."))
+    elif action_name == "mark_stale":
+        memory.status = "stale"
+        memory.review_state = "approved" if memory.review_state == "approved" else "edited"
+        if reason:
+            memory.metadata = {**memory.metadata, "stale_reason": reason}
+    memory.updated_at = action.get("created_at") or _now()
+    return memory, errors, warnings
+
+
+def apply_review_actions(
+    candidates: list[MemoryObject],
+    existing_approved: list[MemoryObject],
+    existing_rejected: list[MemoryObject],
+    actions: list[dict[str, Any]],
+) -> ReviewResult:
+    result = ReviewResult(
+        approved_memory=list(existing_approved),
+        rejected_memory=list(existing_rejected),
+        updated_candidates=list(candidates),
+        actions=list(actions),
+    )
+    for action in actions:
+        result = _apply_one_to_result(result, action)
+    return result
+
+
+def latest_actions_by_memory_id(actions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
+    latest: dict[str, dict[str, Any]] = {}
+    for action in actions:
+        memory_id = str(action.get("memory_id") or "")
+        if not memory_id:
+            continue
+        if memory_id not in latest or str(action.get("created_at") or "") >= str(latest[memory_id].get("created_at") or ""):
+            latest[memory_id] = action
+    return latest
+
+
+def _apply_one_to_result(result: ReviewResult, action: dict[str, Any]) -> ReviewResult:
+    errors = validate_review_action(action)
+    if errors:
+        result.errors.extend(errors)
+        return result
+    memory_id = str(action["memory_id"])
+    candidate = find_memory_by_id(result.updated_candidates, memory_id)
+    target = candidate or find_memory_by_id(result.approved_memory, memory_id) or find_memory_by_id(result.rejected_memory, memory_id)
+    if target is None:
+        result.errors.append(_error("memory_not_found", f"No memory object found for {memory_id}.", memory_id=memory_id))
+        return result
+    updated, action_errors, warnings = apply_review_action(target, action)
+    result.errors.extend(action_errors)
+    result.warnings.extend(warnings)
+    if updated is None:
+        return result
+    result.updated_candidates = _upsert_memory(result.updated_candidates, updated)
+    if updated.review_state == "approved":
+        result.rejected_memory = _remove_memory(result.rejected_memory, updated.memory_id)
+        result.approved_memory = _upsert_memory(result.approved_memory, updated)
+    elif updated.review_state == "rejected":
+        result.approved_memory = _remove_memory(result.approved_memory, updated.memory_id)
+        result.rejected_memory = _upsert_memory(result.rejected_memory, updated)
+    elif action.get("action") == "edit" and find_memory_by_id(result.approved_memory, updated.memory_id):
+        result.approved_memory = _upsert_memory(result.approved_memory, updated)
+    elif action.get("action") == "edit" and find_memory_by_id(result.rejected_memory, updated.memory_id):
+        result.rejected_memory = _upsert_memory(result.rejected_memory, updated)
+    return result
+
+
+def find_memory_by_id(objects: list[MemoryObject], memory_id: str) -> MemoryObject | None:
+    return next((item for item in objects if item.memory_id == memory_id), None)
+
+
+def _apply_edit(memory: MemoryObject, patch: dict[str, Any]) -> list[dict[str, Any]]:
+    errors = []
+    disallowed = sorted(set(patch) - SAFE_EDIT_FIELDS)
+    if disallowed:
+        errors.append(_error("unsafe_edit_field", f"Edit patch contains unsafe fields: {', '.join(disallowed)}"))
+    if errors:
+        return errors
+    original_memory_id = memory.metadata.get("original_memory_id") or memory.memory_id
+    for key, value in patch.items():
+        if key == "metadata":
+            if not isinstance(value, dict):
+                errors.append(_error("invalid_metadata_patch", "metadata patch must be an object."))
+                continue
+            memory.metadata = {**memory.metadata, **value}
+        elif key == "status":
+            if value not in MEMORY_STATUSES:
+                errors.append(_error("invalid_status", f"Invalid memory status: {value}"))
+                continue
+            memory.status = str(value)
+        elif key == "review_state":
+            if value not in REVIEW_STATES:
+                errors.append(_error("invalid_review_state", f"Invalid review state: {value}"))
+                continue
+            memory.review_state = str(value)
+        elif key == "type":
+            if value not in MEMORY_TYPES:
+                errors.append(_error("invalid_memory_type", f"Invalid memory type: {value}"))
+                continue
+            memory.type = str(value)
+        else:
+            setattr(memory, key, value)
+    if errors:
+        return errors
+    if not (memory.sources or memory.source_event_ids or memory.quote):
+        return [_error("source_evidence_required", "Edit cannot remove all source evidence.")]
+    memory.metadata = {**memory.metadata, "original_memory_id": original_memory_id, "edited_from": original_memory_id}
+    return []
+
+
+def _patched_status_or_existing(patch: dict[str, Any], current: str, default: str) -> str:
+    status = patch.get("status") or current or default
+    return str(status) if status in MEMORY_STATUSES else default
+
+
+def _copy_memory(candidate: MemoryObject | dict[str, Any]) -> MemoryObject:
+    if isinstance(candidate, MemoryObject):
+        return MemoryObject.from_dict(copy.deepcopy(candidate.to_dict()))
+    return MemoryObject.from_dict(copy.deepcopy(candidate))
+
+
+def _upsert_memory(objects: list[MemoryObject], memory: MemoryObject) -> list[MemoryObject]:
+    updated = []
+    found = False
+    for item in objects:
+        if item.memory_id == memory.memory_id:
+            updated.append(memory)
+            found = True
+        else:
+            updated.append(item)
+    if not found:
+        updated.append(memory)
+    return updated
+
+
+def _remove_memory(objects: list[MemoryObject], memory_id: str) -> list[MemoryObject]:
+    return [item for item in objects if item.memory_id != memory_id]
+
+
+def _error(code: str, message: str, memory_id: str | None = None) -> dict[str, Any]:
+    result = {"code": code, "message": message}
+    if memory_id:
+        result["memory_id"] = memory_id
+    return result
+
+
+def _warning(code: str, message: str) -> dict[str, Any]:
+    return {"code": code, "message": message}
+
+
+def _now() -> str:
+    return datetime.now(timezone.utc).isoformat()
diff --git a/src/review_policy.py b/src/review_policy.py
index 8f4ee31..72c981d 100644
--- a/src/review_policy.py
+++ b/src/review_policy.py
@@ -83,7 +83,19 @@ def classify_memory(
     return candidate


-def build_memory_state_debug(candidates: list[MemoryObject], *, input_file: str | None = None) -> dict[str, Any]:
+def build_memory_state_debug(
+    candidates: list[MemoryObject],
+    *,
+    input_file: str | None = None,
+    review_actions: list[dict[str, Any]] | None = None,
+) -> dict[str, Any]:
+    latest_actions = {}
+    for action in review_actions or []:
+        memory_id = str(action.get("memory_id") or "")
+        if not memory_id:
+            continue
+        if memory_id not in latest_actions or str(action.get("created_at") or "") >= str(latest_actions[memory_id].get("created_at") or ""):
+            latest_actions[memory_id] = action
     return {
         "metadata": {
             "input_file": input_file,
@@ -98,9 +110,12 @@ def build_memory_state_debug(candidates: list[MemoryObject], *, input_file: str
                 "type": item.type,
                 "title": item.title,
                 "review_state": item.review_state,
+                "status": item.status,
                 "evidence_state": item.evidence_state,
                 "memory_quality_score": round(float(item.memory_quality_score), 3),
                 "reason_codes": list(item.review_reason_codes),
+                "latest_review_action": (latest_actions.get(item.memory_id) or {}).get("action"),
+                "latest_review_action_at": (latest_actions.get(item.memory_id) or {}).get("created_at"),
                 "source_quote": item.quote,
             }
             for item in candidates
diff --git a/tests/test_review_actions.py b/tests/test_review_actions.py
new file mode 100644
index 0000000..653efe4
--- /dev/null
+++ b/tests/test_review_actions.py
@@ -0,0 +1,274 @@
+from __future__ import annotations
+
+import json
+import subprocess
+import sys
+import tempfile
+import unittest
+from pathlib import Path
+
+from src.memory_objects import MemoryObject
+from src.memory_store import apply_action_to_store
+from src.review_actions import apply_review_action, create_review_action, validate_review_action
+from src.review_policy import build_memory_state_debug
+
+
+class ReviewActionsTest(unittest.TestCase):
+    def test_approve_candidate_writes_to_approved_memory(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            result = apply_action_to_store(run_dir, create_review_action("mem_1", "approve", reviewer="Owen"), pretty=True)
+
+            approved = self._load(run_dir / "approved_memory.json")["approved_memory"]
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertFalse(result["errors"])
+            self.assertEqual(len(approved), 1)
+            self.assertEqual(approved[0]["memory_id"], "mem_1")
+            self.assertEqual(candidate["review_state"], "approved")
+            self.assertEqual(candidate["status"], "unresolved")
+
+    def test_approve_does_not_duplicate_already_approved_memory(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+
+            approved = self._load(run_dir / "approved_memory.json")["approved_memory"]
+            self.assertEqual(len(approved), 1)
+
+    def test_reject_candidate_writes_to_rejected_memory(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="False positive"))
+
+            rejected = self._load(run_dir / "rejected_memory.json")["rejected_memory"]
+            self.assertEqual(len(rejected), 1)
+            self.assertEqual(rejected[0]["metadata"]["rejection_reason"], "False positive")
+
+    def test_reject_removes_from_approved_memory(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="Changed mind"))
+
+            self.assertEqual(self._load(run_dir / "approved_memory.json")["approved_memory"], [])
+            self.assertEqual(len(self._load(run_dir / "rejected_memory.json")["rejected_memory"]), 1)
+
+    def test_approve_removes_from_rejected_memory(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="Initial reject"))
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+
+            self.assertEqual(self._load(run_dir / "rejected_memory.json")["rejected_memory"], [])
+            self.assertEqual(len(self._load(run_dir / "approved_memory.json")["approved_memory"]), 1)
+
+    def test_edit_updates_safe_fields_and_preserves_source_evidence(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(
+                run_dir,
+                create_review_action("mem_1", "edit", patch={"title": "Owen should reach out to schools.", "owner_names": ["Owen"]}),
+            )
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertEqual(candidate["title"], "Owen should reach out to schools.")
+            self.assertEqual(candidate["owner_names"], ["Owen"])
+            self.assertEqual(candidate["quote"], "Owen to reach out to schools.")
+            self.assertTrue(candidate["sources"])
+            self.assertEqual(candidate["review_state"], "edited")
+
+    def test_edit_cannot_remove_all_source_evidence(self) -> None:
+        candidate = self._memory("mem_1")
+        action = create_review_action("mem_1", "edit", patch={"sources": [], "source_event_ids": [], "quote": ""})
+
+        updated, errors, _warnings = apply_review_action(candidate, action)
+
+        self.assertIsNone(updated)
+        self.assertTrue(any(error["code"] == "unsafe_edit_field" for error in errors))
+
+    def test_mark_resolved_changes_commitment_status(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_resolved", reason="Confirmed in notes"))
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertEqual(candidate["status"], "resolved")
+            self.assertEqual(candidate["metadata"]["resolved_reason"], "Confirmed in notes")
+
+    def test_mark_superseded_records_reason(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_superseded", reason="Replaced by later decision"))
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertEqual(candidate["status"], "superseded")
+            self.assertEqual(candidate["metadata"]["superseded_reason"], "Replaced by later decision")
+
+    def test_mark_stale_records_reason(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_stale", reason="No longer current"))
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertEqual(candidate["status"], "stale")
+            self.assertEqual(candidate["metadata"]["stale_reason"], "No longer current")
+
+    def test_mark_needs_review_sets_review_state(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_needs_review", reason="Ambiguous owner"))
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertEqual(candidate["review_state"], "needs_review")
+            self.assertEqual(candidate["metadata"]["review_note"], "Ambiguous owner")
+
+    def test_actions_append_to_review_actions(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_needs_review"))
+            apply_action_to_store(run_dir, create_review_action("mem_1", "edit", patch={"summary": "Updated summary."}))
+
+            actions = self._load(run_dir / "review_actions.json")["actions"]
+            self.assertEqual([action["action"] for action in actions], ["mark_needs_review", "edit"])
+
+    def test_invalid_memory_id_returns_clear_error(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            result = apply_action_to_store(run_dir, create_review_action("missing", "approve"))
+
+            self.assertTrue(result["errors"])
+            self.assertEqual(result["errors"][0]["code"], "memory_not_found")
+
+    def test_invalid_action_returns_clear_error(self) -> None:
+        errors = validate_review_action({"memory_id": "mem_1", "action": "dance"})
+
+        self.assertEqual(errors[0]["code"], "invalid_action")
+
+    def test_unsupported_memory_cannot_be_silently_approved(self) -> None:
+        with self._run_dir([self._memory("mem_1", evidence_state="unsupported")]) as run_dir:
+            result = apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+
+            self.assertTrue(any(error["code"] == "unsupported_approval" for error in result["errors"]))
+            self.assertEqual(self._load(run_dir / "approved_memory.json")["approved_memory"], [])
+
+    def test_cli_approve_command(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            result = subprocess.run(
+                [sys.executable, "review_memory.py", "--run-dir", str(run_dir), "--memory-id", "mem_1", "--action", "approve", "--reviewer", "Owen"],
+                cwd=Path(__file__).resolve().parents[1],
+                text=True,
+                capture_output=True,
+                check=True,
+            )
+
+            self.assertIn("approved_count: 1", result.stdout)
+            self.assertEqual(len(self._load(run_dir / "approved_memory.json")["approved_memory"]), 1)
+
+    def test_cli_reject_command(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            result = subprocess.run(
+                [sys.executable, "review_memory.py", "--run-dir", str(run_dir), "--memory-id", "mem_1", "--action", "reject", "--reason", "False positive"],
+                cwd=Path(__file__).resolve().parents[1],
+                text=True,
+                capture_output=True,
+                check=True,
+            )
+
+            self.assertIn("rejected_count: 1", result.stdout)
+            self.assertEqual(len(self._load(run_dir / "rejected_memory.json")["rejected_memory"]), 1)
+
+    def test_cli_edit_command_with_json_patch(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            result = subprocess.run(
+                [
+                    sys.executable,
+                    "review_memory.py",
+                    "--run-dir",
+                    str(run_dir),
+                    "--memory-id",
+                    "mem_1",
+                    "--action",
+                    "edit",
+                    "--patch",
+                    '{"title":"Owen should reach out to schools.","owner_names":["Owen"]}',
+                ],
+                cwd=Path(__file__).resolve().parents[1],
+                text=True,
+                capture_output=True,
+                check=True,
+            )
+
+            self.assertIn("review_state: edited", result.stdout)
+            self.assertEqual(self._candidate(run_dir, "mem_1")["owner_names"], ["Owen"])
+
+    def test_memory_state_debug_reflects_latest_action(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+
+            debug_item = self._load(run_dir / "memory_state_debug.json")["items"][0]
+            self.assertEqual(debug_item["latest_review_action"], "approve")
+            self.assertEqual(debug_item["review_state"], "approved")
+            self.assertEqual(debug_item["status"], "unresolved")
+
+    def test_candidate_memory_remains_source_backed_after_actions(self) -> None:
+        with self._run_dir([self._memory("mem_1")]) as run_dir:
+            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
+
+            candidate = self._candidate(run_dir, "mem_1")
+            self.assertTrue(candidate["sources"])
+            self.assertTrue(candidate["source_event_ids"])
+            self.assertEqual(candidate["quote"], "Owen to reach out to schools.")
+
+    def test_build_memory_state_debug_includes_status_and_latest_action_fields(self) -> None:
+        action = create_review_action("mem_1", "approve")
+        debug = build_memory_state_debug([self._memory("mem_1")], review_actions=[action])
+
+        self.assertEqual(debug["items"][0]["status"], "unresolved")
+        self.assertEqual(debug["items"][0]["latest_review_action"], "approve")
+        self.assertEqual(debug["items"][0]["latest_review_action_at"], action["created_at"])
+
+    def _run_dir(self, candidates: list[MemoryObject]):
+        class RunDirContext:
+            def __enter__(inner_self):
+                inner_self.temp_dir = tempfile.TemporaryDirectory()
+                run_dir = Path(inner_self.temp_dir.name)
+                self._write_initial_run(run_dir, candidates)
+                return run_dir
+
+            def __exit__(inner_self, exc_type, exc, traceback):
+                inner_self.temp_dir.cleanup()
+
+        return RunDirContext()
+
+    def _write_initial_run(self, run_dir: Path, candidates: list[MemoryObject]) -> None:
+        run_dir.mkdir(parents=True, exist_ok=True)
+        payloads = {
+            "candidate_memory.json": {
+                "metadata": {"input_file": "data/example.md", "candidate_count": len(candidates)},
+                "candidates": [candidate.to_dict() for candidate in candidates],
+            },
+            "approved_memory.json": {"metadata": {"input_file": "data/example.md", "approved_count": 0}, "approved_memory": []},
+            "rejected_memory.json": {"metadata": {"input_file": "data/example.md", "rejected_count": 0}, "rejected_memory": []},
+            "review_actions.json": {"metadata": {"input_file": "data/example.md", "action_count": 0}, "actions": []},
+            "memory_state_debug.json": build_memory_state_debug(candidates, input_file="data/example.md"),
+        }
+        for filename, payload in payloads.items():
+            (run_dir / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
+
+    def _memory(self, memory_id: str, evidence_state: str = "supported") -> MemoryObject:
+        return MemoryObject(
+            memory_id=memory_id,
+            source_object_id=f"task_{memory_id}",
+            type="commitment",
+            title="Reach out to schools.",
+            summary="Owen should reach out to schools.",
+            owner_names=["Owen"],
+            status="unresolved",
+            confidence=0.9,
+            source_event_ids=["event_1"],
+            sources=[{"source_event_id": "event_1", "source_type": "uploaded_document", "quote": "Owen to reach out to schools."}],
+            quote="Owen to reach out to schools.",
+            evidence_state=evidence_state,
+            review_state="unreviewed",
+            memory_quality_score=0.95,
+        )
+
+    def _load(self, path: Path):
+        return json.loads(path.read_text(encoding="utf-8"))
+
+    def _candidate(self, run_dir: Path, memory_id: str):
+        candidates = self._load(run_dir / "candidate_memory.json")["candidates"]
+        return next(candidate for candidate in candidates if candidate["memory_id"] == memory_id)
+
+
+if __name__ == "__main__":
+    unittest.main()

```

---

## 2. Owner-task lines may have been missed

Severity: medium
Reason: extraction_warning
Candidate decision: Owner-task lines may have been missed

Summary:
Document contains multiple owner-task patterns but few action items were extracted.

Recommended action:
Review block classification and task extraction output.

Sources:
* Unknown: Generated: 2026-06-30T23:37:39.330788+00:00
