# Review Queue

## 1. Agent claim needs review

Severity: high
Reason: unsupported_agent_claim
Candidate decision: The CLI supports approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Applying review actions

## 2. Agent claim needs review

Severity: high
Reason: unsupported_agent_claim
Candidate decision: review_actions.json is append-only.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Appending to review_actions.json

## 3. Agent claim needs review

Severity: high
Reason: unsupported_agent_claim
Candidate decision: Source evidence must be preserved through edits.

Summary:
Agent claim lacks deterministic support.

Recommended action:
Inspect the cited packet section and supporting git/test evidence.

Sources:
* Unknown: Source evidence must be preserved through edits.

## 4. Validation claims need evidence

Severity: medium
Reason: weak_validation_evidence
Candidate decision: Validation claims are self-reported

Summary:
Validation claims are self-reported and need log evidence.

Recommended action:
Attach or inspect the actual test/scenario logs before relying on these validation claims.

Sources:
* Unknown: PASS, 212 tests

## 5. Agent packet is missing expected sections

Severity: medium
Reason: missing_source_evidence
Candidate decision: Agent packet is missing expected sections

Summary:
Missing sections: Questions for Spool Audit, Expected Useful Spool Output.

Recommended action:
Review whether the packet has enough evidence for audit.

Sources:
* Evidence unavailable or not yet linked.

## 6. Possible agent scope drift

Severity: high
Reason: possible_scope_drift
Candidate decision: Possible agent scope drift

Summary:
Possible out-of-scope addition mentions database.

Recommended action:
Inspect the diff for out-of-scope additions.

Sources:
* Evidence unavailable or not yet linked.
