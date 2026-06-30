# Verification Summary

Overall verdict: FAIL

## Pair Summary

| Scenario Type | Original | New | Pair Verdict |
| --- | --- | --- | --- |
| general_workability | - | - | - |
| no_decision | - | - | - |
| pricing_change | - | - | - |
| vendor_selection | - | - | - |
| decision_reversal | - | - | - |
| messy | - | - | - |
| document_vendor_selection | - | - | - |
| review_queue | - | - | - |

## Scenario Summary

| Scenario | Expected Count | Actual Count | Count Pass | Invariant Pass | Verdict | Failure Count | Review Item Count | Review Reasons |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
| review_proposal_no_approval | 0 | 0 | PASS | FAIL | FAIL | 1 | 0 | - |
| review_missing_owner | 1 | 1 | PASS | PASS | PASS | 0 | 1 | owner_missing |
| review_low_confidence_ambiguous | 0 | 0 | PASS | PASS | PASS | 0 | 1 | ambiguous_decision |

## Failures

### review_proposal_no_approval
Failure files: `outputs/runs/review_false_positive_fix_review/failures/review_proposal_no_approval`
- approval missing review item: expected `approval_missing`, actual `[]`
