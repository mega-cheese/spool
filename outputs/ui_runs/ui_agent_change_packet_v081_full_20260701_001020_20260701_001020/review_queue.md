# Review Queue

## 1. Decision needs human review

Severity: medium
Reason: approval_missing
Candidate decision: Implement Spool v0.8.1 review action application layer.

Summary:
The system found a possible decision but does not have enough evidence to safely write it to clean memory.

Recommended action:
Confirm whether this should enter clean memory.

Sources:
* Unknown: ```text
Implement v0.8.1: review action application layer.

Context:
Spool v0.8.0 added approved-memory infrastructure:
- candidate_memory.json
- approved_memory.json
- rejected_memory.json
- review_actions.json
- memory_state_debug.json

It also added:
- src/memory_objects.py
- src/review_policy.py
- src/memory_store.py
- src/memory_artifacts.py

Current principle:
Raw extraction is not trusted memory.

Architecture:
raw docs
→ extracted candidate memory
→ review state
→ human/system review action
→ approved/rejected/edited memory
→ trusted company memory later

v0.8.0 created the artifacts, but the user still needs a real way to apply review actions.

Goal:
Implement the infrastructure for applying review actions to candidate memory.

This is still system infrastructure, not a polished UI.

Do not build:
- browser extension
- integrations
- database
- auth
- AI answer verifier
- large UI redesign
- task reminder system
- todo tracker features

Do not change extraction behavior unless absolutely necessary.
Do not remove or break existing artifacts.
Do not turn commitments into productivity tasks.

Keep existing outputs compatible:
- decisions.json
- clean_memory.json
- action_items.json
- open_questions.json
- review_queue.json
- candidate_memory.json
- approved_memory.json
- rejected_memory.json
- review_actions.json
- memory_state_debug.json

Part 1: Add review action model.

Create or update a module:

src/review_actions.py

Define a normalized ReviewAction shape:

{
  "action_id": "review_...",
  "memory_id": "mem_...",
  "action": "approve" | "reject" | "edit" | "mark_needs_review" | "mark_resolved" | "mark_unresolved" | "mark_superseded" | "mark_stale",
  "reviewer": "system" | "human" | "...",
  "reason": null,
  "patch": {},
  "created_at": "..."
}

Add helper functions:
- create_review_action(memory_id, action, reviewer="human", reason=None, patch=None)
- validate_review_action(action)
- apply_review_action(candidate, action) -> MemoryObject
- apply_review_actions(candidates, existing_approved, existing_rejected, actions) -> ReviewResult

ReviewResult should include:
{
  "approved_memory": [...],
  "rejected_memory": [...],
  "updated_candidates": [...],
  "actions": [...],
  "errors": [...],
  "warnings": [...]
}

Important:
- Actions should be append-only in review_actions.json.
- Applying a review action should not delete source evidence.
- Edited memory must preserve original source_object_id/source_event_ids/sources/quote unless patch explicitly changes allowed fields.
- Do not allow edits to remove all source evidence unless action is reject.
- Do not silently approve unsupported memory.

Part 2: Review action semantics.

Approve:
- review_state = "approved"
- status:
  - for decisions: "approved"
  - for commitments: keep "unresolved" unless patch/status says resolved/superseded/stale
  - for open_questions: keep "open" or "unresolved" depending existing schema
- evidence_state must remain supported/weakly_supported/unknown as previously assigned
- If evidence_state is unsupported or contradicted, approval should either fail or mark warning unless force flag is present.
- v0.8.1 does not need force approval unless easy.

Reject:
- review_state = "rejected"
- status = "rejected"
- add rejection_reason from action.reason if available
- move to rejected_memory.json
- do not include in approved_memory.json

Edit:
- apply patch to safe editable fields:
  - title
  - summary
  - owner_names
  - status
  - type
  - metadata
  - due_date if already present in object schema
  - review_state if valid
- after edit:
  - review_state = "edited" unless patch explicitly sets approved/rejected/needs_review
  - updated_at changes
  - metadata.original_memory_id or metadata.edited_from should preserve lineage
- preserve source evidence.

Mark needs review:
- review_state = "needs_review"
- status should remain current unless unsupported
- add reason code or review note.

Mark resolved:
- for commitments only:
  - status = "resolved"
  - review_state can be "approved" if already approved, otherwise "needs_review" or "edited"
- do not treat this as productivity task completion; it is a memory-state update.
- require source/evidence if possible, but for v0.8.1 allow human marking with reason.

Mark unresolved:
- for commitments/open questions:
  - status = "unresolved"

Mark superseded:
- status = "superseded"
- review_state = "approved" or "edited" depending prior state
- require reason if possible.
- metadata.superseded_reason = reason

Mark stale:
- status = "stale"
- metadata.stale_reason = reason

Part 3: Update memory_store.py.

Extend or update src/memory_store.py with functions:

- load_candidate_memory(path)
- save_candidate_memory(path, objects)
- load_review_actions(path)
- save_review_actions(path, actions)
- append_review_action(path, action)
- find_memory_by_id(candidates, memory_id)
- apply_action_to_store(run_dir, action) -> updated artifacts

apply_action_to_store should:
1. Load candidate_memory.json
2. Load approved_memory.json
3. Load rejected_memory.json
4. Load review_actions.json
5. Validate action
6. Find target memory object
7. Apply action
8. Update approved/rejected/candidate states
9. Append action to review_actions.json
10. Rewrite memory_state_debug.json or update it if appropriate

For duplicate handling:
- If approving a memory object that already exists in approved_memory by memory_id, update it instead of duplicating.
- If rejecting an approved memory object, remove it from approved_memory and add/update rejected_memory.
- If approving a rejected memory object, remove it from rejected_memory and add/update approved_memory.
- Keep actions append-only.

Part 4: Add CLI support for review actions.

Add lightweight CLI command support. Prefer either a new script or extending an existing script.

Recommended new script:

review_memory.py

Usage examples:

venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action approve --reviewer Owen

venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action reject --reason "False positive"

venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action edit --patch '{"title":"Owen should reach out to schools","owner_names":["Owen"]}'

venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action mark_resolved --reason "Confirmed in follow-up notes"

The script should:
- load artifacts from run dir
- apply action
- write updated approved/rejected/candidate/review_actions artifacts
- print a compact summary:
  - memory_id
  - action
  - new review_state
  - new status
  - approved_count
  - rejected_count
  - action_count

Do not require Streamlit to use this.
Do not require a database.

Part 5: Optional minimal Streamlit integration.

If simple and low-risk, add minimal buttons or controls in current Streamlit UI:
- Approve
- Reject
- Mark needs review
- Edit can be deferred if complex.

But do not do a major UI redesign.
CLI support is required; Streamlit support is optional.

Part 6: Add review action debug artifact.

Update memory_state_debug.json after actions.

Each item should show:
{
  "memory_id": "...",
  "type": "...",
  "title": "...",
  "review_state": "...",
  "status": "...",
  "evidence_state": "...",
  "memory_quality_score": 0.0,
  "reason_codes": [],
  "latest_review_action": "approve",
  "latest_review_action_at": "...",
  "source_quote": "..."
}

If memory_state_debug currently only reflects initial classification, add enough action-state reflection so the latest state is visible.

Part 7: Tests.

Add tests for review action infrastructure.

Required tests:

1. approve candidate writes to approved_memory.
2. approve does not duplicate already-approved memory.
3. reject candidate writes to rejected_memory.
4. reject removes memory from approved_memory if it was approved.
5. approve removes memory from rejected_memory if it was previously rejected.
6. edit updates safe fields and preserves source evidence.
7. edit cannot remove all source evidence.
8. mark_resolved changes commitment status to resolved.
9. mark_superseded records superseded reason in metadata.
10. mark_stale records stale reason in metadata.
11. mark_needs_review sets review_state to needs_review.
12. actions append to review_actions.json, not overwrite.
13. invalid memory_id returns clear error.
14. invalid action returns clear error.
15. unsupported/contradicted memory cannot be silently approved.
16. CLI approve command works on a temp run directory.
17. CLI reject command works on a temp run directory.
18. CLI edit command works with JSON patch.
19. memory_state_debug reflects latest action state.
20. candidate_memory remains present and source-backed after actions.

Part 8: Scenario validation.

Run:

venv/bin/python -m unittest discover -s tests

venv/bin/python run_scenarios.py --scenario-set meeting_minutes --mode heuristic --pipeline candidates-shadow --run-id v081_review_actions_heuristic

venv/bin/python run_scenarios.py --scenario-set meeting_minutes --mode llm --pipeline candidates-shadow --run-id v081_review_actions_llm

venv/bin/python run_scenarios.py --scenario-set docs --mode llm --pipeline candidates-shadow --run-id v081_docs_regression

venv/bin/python run_scenarios.py --scenario-set review --mode llm --pipeline candidates-shadow --run-id v081_review_regression

venv/bin/python run_scenarios.py --scenario-set conflict --mode llm --pipeline candidates-shadow --run-id v081_conflict_regression

Manual smoke test:
1. Pick a run directory from Meeting #7 or Meeting #12.
2. Open candidate_memory.json.
3. Pick one commitment memory_id.
4. Approve it with review_memory.py.
5. Confirm approved_memory.json contains it.
6. Confirm review_actions.json appended the approve action.
7. Reject another candidate.
8. Confirm rejected_memory.json contains it.
9. Edit one weak item title/owner.
10. Confirm source quote is preserved.
11. Confirm memory_state_debug reflects latest states.

Part 9: README update.

Update README with a short section:

Review actions:

- v0.8.0 creates candidate memory but does not trust it automatically.
- v0.8.1 adds review actions.
- Use review_memory.py to approve/reject/edit/mark memory states.
- approved_memory.json is the trusted memory store for reviewed items.
- rejected_memory.json preserves rejected candidates.
- review_actions.json is append-only audit history.

Mention:
- Action items are represented as commitments in candidate/approved memory.
- Spool is not a todo tracker.
- Commitment statuses are memory states: unresolved, resolved, superseded, stale, unknown.

Part 10: Non-goals reminder.

Do not implement:
- AI answer verification
- browser extension
- user auth
- database
- cloud sync
- Slack/Drive/Gmail integrations
- polished UI
- Kanban/task tracker/reminders
- automatic approval by default

v0.8.1 is done when:
- review actions can be applied from CLI
- approved/rejected/review action artifacts update correctly
- candidate source evidence is preserved
- tests pass
- existing extraction/regression scenarios still pass
```

---
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
* Unknown: - Review actions could corrupt JSON artifacts if writes are not atomic.
- Edits could accidentally allow source evidence to be removed.
- `memory_state_debug.json` could diverge from actual approved/rejected state.
- Duplicate handling by `memory_id` could incorrectly overwrite edited objects.
- CLI patch parsing could accept unsafe or invalid fields.

## 2. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Apply approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Apply approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale
* Unknown: mark_needs_review

## 3. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Append actions to review_actions.json.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Append actions to review_actions.json

## 4. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Preserve source evidence through edits.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Preserve source evidence through edits

## 5. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Prevent silent approval of unsupported/contradicted memory.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Prevent silent approval of unsupported/contradicted memory

## 6. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Avoid duplicate approved/rejected entries by memory_id.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Avoid duplicate approved/rejected entries by memory_id

## 7. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Unit tests passed with 212 tests.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: PASS, 212 tests

## 8. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: venv/bin/python -m unittest discover -s tests: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: venv/bin/python -m unittest discover -s tests: PASS

## 9. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: meeting_minutes heuristic: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: meeting_minutes heuristic: PASS

## 10. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: meeting_minutes LLM: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: meeting_minutes LLM: PASS

## 11. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: docs LLM: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: docs LLM: PASS

## 12. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: review LLM: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: review LLM: PASS

## 13. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: conflict LLM: PASS.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: conflict LLM: PASS

## 14. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Created a fresh Meeting #12 run.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Created a fresh Meeting #12 run.

## 15. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Approved one commitment into approved_memory.json.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Approved one commitment into approved_memory.json.

## 16. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Rejected one candidate into rejected_memory.json.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Rejected one candidate into rejected_memory.json.

## 17. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Edited one weak item while preserving source quote/evidence.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Edited one weak item while preserving source quote/evidence.

## 18. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Confirmed review_actions.json appended actions and memory_state_debug.json reflects latest action state.

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Confirmed review_actions.json appended actions and memory_state_debug.json reflects latest action state.

## 19. Agent claim needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Meeting minutes extraction warning

Summary:
Agent claim is stated in the packet but lacks independent artifact evidence.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: ---

## 20. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Source evidence must be preserved through edits.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Source evidence must be preserved through edits.

## 21. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Review actions are append-only in review_actions.json.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Review actions are append-only in `review_actions.json`.

## 22. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Duplicate handling by memory_id could incorrectly overwrite edited objects.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Duplicate handling by `memory_id` could incorrectly overwrite edited objects.

## 23. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Should Streamlit expose approve/reject/edit actions?

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Should Streamlit expose approve/reject/edit actions?

## 24. Possible agent scope drift

Severity: high
Reason: extraction_warning
Candidate decision: Possible agent scope drift

Summary:
Possible out-of-scope addition mentions database.

Recommended action:
Inspect the diff for out-of-scope additions.

Sources:
* Evidence unavailable or not yet linked.

## 25. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Review actions could corrupt JSON artifacts if writes are not atomic.

Summary:
Review actions could corrupt JSON artifacts if writes are not atomic.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 26. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Edits could accidentally allow source evidence to be removed.

Summary:
Edits could accidentally allow source evidence to be removed.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 27. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: memory_state_debug.json could diverge from actual approved/rejected state.

Summary:
memory_state_debug.json could diverge from actual approved/rejected state.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 28. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Duplicate handling by memory_id could incorrectly overwrite edited objects.

Summary:
Duplicate handling by memory_id could incorrectly overwrite edited objects.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 29. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: CLI patch parsing could accept unsafe or invalid fields.

Summary:
CLI patch parsing could accept unsafe or invalid fields.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.
