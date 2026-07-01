# Review Queue

## 1. Validation claims need evidence

Severity: medium
Reason: weak_validation_evidence
Candidate decision: Validation claims are self-reported

Summary:
Validation claims are self-reported and need log evidence.

Recommended action:
Attach or inspect the actual test/scenario logs before relying on these validation claims.

Sources:
* Unknown: PASS, 212 tests

## 2. Possible agent scope drift

Severity: high
Reason: possible_scope_drift
Candidate decision: Possible agent scope drift

Summary:
Possible out-of-scope addition mentions database.

Recommended action:
Inspect the diff for out-of-scope additions.

Sources:
* Evidence unavailable or not yet linked.
