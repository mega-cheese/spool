# Agent Change Packet: Spool v0.8.1 Review Actions

Generated: 2026-06-30T23:37:39Z

## Purpose

This packet is a pseudo-PR / agent-change audit input for Spool. It should support an agent-change audit focused on implementation claims, scope adherence, and unsupported or contradicted claims.

## Original Request

```text
Implement v0.8.1: review action application layer.

Do not build:
- browser extension
- integrations
- database
- auth
- AI answer verifier
- task reminder system
- todo tracker features

Add review actions for approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale.
Actions should be append-only in review_actions.json.
Applying a review action should not delete source evidence.
Do not silently approve unsupported memory.
```

## Agent Implementation Summary

- Added `src/review_actions.py`.
- Added `review_memory.py`.
- Extended `src/memory_store.py`.
- Updated `src/review_policy.py`.
- Added `tests/test_review_actions.py`.
- Updated README review-action docs.
- Preserves source evidence through edits.
- Avoids duplicate approved/rejected entries by memory_id.

## Claimed Capabilities

- Supports approve/reject/edit/mark_needs_review/mark_resolved/mark_unresolved/mark_superseded/mark_stale.
- Updates candidate_memory.json, approved_memory.json, rejected_memory.json, and memory_state_debug.json.
- Appends to review_actions.json.
- Prevents silent approval of unsupported or contradicted memory.

## Validation Claimed by Agent

```text
venv/bin/python -m unittest discover -s tests
Ran 212 tests in 2.41s
OK

Scenario regressions passed: meeting_minutes, docs, review, conflict.
Manual smoke passed in Streamlit.
```

## Existing Product Rules / Approved Context

- Raw extraction is not trusted memory.
- Keep review actions append-only.
- Do not auto-approve extracted memory.
- Store review actions in JSON artifacts rather than a database.
- Represent action items as commitments rather than todo tasks.

## Candidate Technical Decisions / Risks / Open Questions

Technical decisions:
- Add a review action application layer for candidate memory.
- Store review actions in JSON artifacts rather than a database.
- Keep review actions append-only.
- Do not auto-approve extracted memory.
- Represent action items as commitments rather than todo tasks.

Risks:
- JSON artifact writes may not be atomic.
- Edit patches could accidentally remove or corrupt source evidence.
- memory_state_debug could diverge from actual memory state.
- Duplicate handling by memory_id could overwrite edited objects.
- CLI patch parsing could accept invalid fields.

Open questions:
- Should weakly_supported memory be approvable?
- Should approved_memory become canonical in v0.8.2?
- Should review actions support stronger reviewer identity?
- Should actions become hash-chained or tamper-evident later?
- Should Streamlit expose approve/reject/edit?

## Git Stat

```text
 README.md                  |  31 ++++++
 review_memory.py           |  70 +++++++++++++
 src/memory_store.py        | 130 ++++++++++++++++++++++++
 src/review_actions.py      | 210 ++++++++++++++++++++++++++++++++++++++++
 src/review_policy.py       |  18 ++++
 tests/test_review_actions.py | 180 ++++++++++++++++++++++++++++++++++
 6 files changed, 639 insertions(+)
```

## Git Diff

```diff
diff --git a/src/review_actions.py b/src/review_actions.py
new file mode 100644
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
+def append_review_action(path, action):
+    actions.append(action)
+def apply_review_action(candidate, action):
+    if candidate.evidence_state in {"unsupported", "contradicted"} and not action.get("patch", {}).get("force"):
+        return _error("unsupported_approval")
+    edited = _copy_memory(candidate)
+    edited.source_event_ids = list(candidate.source_event_ids)
+    edited.sources = list(candidate.sources)
+    edited.quote = candidate.quote
+    return edited
diff --git a/review_memory.py b/review_memory.py
new file mode 100755
+parser.add_argument("--action", choices=sorted(ALLOWED_ACTIONS))
diff --git a/src/memory_store.py b/src/memory_store.py
+def apply_action_to_store(run_dir, action):
+    append_review_action(review_actions_path, action)
+    approved = _upsert_memory(approved, updated)
+    rejected = _remove_memory(rejected, updated.memory_id)
diff --git a/tests/test_review_actions.py b/tests/test_review_actions.py
new file mode 100644
+def test_approval_rejects_unsupported_memory_without_force(self):
+    self.assertIn("unsupported_approval", errors[0]["code"])
+def test_approve_updates_existing_memory_without_duplicate(self):
+    self.assertEqual(len(result.approved_memory), 1)
diff --git a/README.md b/README.md
+Review actions update candidate_memory.json, approved_memory.json, rejected_memory.json, and memory_state_debug.json.
```

## Questions for Spool Audit

- Which claims are supported by git stat or git diff?
- Are validation claims directly supported or only weakly supported?
- Did the agent add anything explicitly out of scope?

## Expected Useful Spool Output

- Extract implementation claims.
- Mark file and capability claims as supported when git diff evidence exists.
- Mark test pass and manual smoke claims as weakly_supported when only validation text states them.
- Surface technical decisions, risks, and open questions as candidate memory.
