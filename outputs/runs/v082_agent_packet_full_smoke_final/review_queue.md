# Review Queue

## 1. Agent claim needs review

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

## 2. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Append actions to review_actions.json.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Append actions to review_actions.json

## 3. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Preserve source evidence through edits.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Preserve source evidence through edits

## 4. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Prevent silent approval of unsupported/contradicted memory.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Prevent silent approval of unsupported/contradicted memory

## 5. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Avoid duplicate approved/rejected entries by memory_id.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Avoid duplicate approved/rejected entries by memory_id

## 6. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Source evidence must be preserved through edits.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Source evidence must be preserved through edits.

## 7. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Review actions are append-only in review_actions.json.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Review actions are append-only in `review_actions.json`.

## 8. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Duplicate handling by memory_id could incorrectly overwrite edited objects.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Duplicate handling by `memory_id` could incorrectly overwrite edited objects.

## 9. Agent claim needs review

Severity: high
Reason: unsupported_claim
Candidate decision: Should Streamlit expose approve/reject/edit actions?

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Should Streamlit expose approve/reject/edit actions?

## 10. Possible agent scope drift

Severity: high
Reason: extraction_warning
Candidate decision: Possible agent scope drift

Summary:
Possible out-of-scope addition mentions database.

Recommended action:
Inspect the diff for out-of-scope additions.

Sources:
* Evidence unavailable or not yet linked.

## 11. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Review actions could corrupt JSON artifacts if writes are not atomic.

Summary:
Review actions could corrupt JSON artifacts if writes are not atomic.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 12. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Edits could accidentally allow source evidence to be removed.

Summary:
Edits could accidentally allow source evidence to be removed.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 13. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: memory_state_debug.json could diverge from actual approved/rejected state.

Summary:
memory_state_debug.json could diverge from actual approved/rejected state.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 14. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: Duplicate handling by memory_id could incorrectly overwrite edited objects.

Summary:
Duplicate handling by memory_id could incorrectly overwrite edited objects.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.

## 15. Agent audit risk needs review

Severity: medium
Reason: extraction_warning
Candidate decision: CLI patch parsing could accept unsafe or invalid fields.

Summary:
CLI patch parsing could accept unsafe or invalid fields.

Recommended action:
Review whether this risk should be tracked or mitigated.

Sources:
* Evidence unavailable or not yet linked.
