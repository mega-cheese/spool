# Verification Summary

Overall verdict: FAIL

## Pair Summary

| Scenario Type | Original | New | Pair Verdict |
| --- | --- | --- | --- |
| general_workability | PASS | PASS | PASS |
| no_decision | PASS | PASS | PASS |
| pricing_change | PASS | PASS | PASS |
| vendor_selection | FAIL | FAIL | FAIL |
| decision_reversal | PASS | PASS | PASS |
| messy | PASS | PASS | PASS |
| document_vendor_selection | - | - | - |

## Scenario Summary

| Scenario | Expected Count | Actual Count | Count Pass | Invariant Pass | Verdict | Failure Count |
| --- | ---: | ---: | --- | --- | --- | ---: |
| original_fake | 2 | 2 | PASS | PASS | PASS | 0 |
| mobile_app | 0 | 0 | PASS | PASS | PASS | 0 |
| pricing_change | 1 | 1 | PASS | PASS | PASS | 0 |
| vendor_selection | 1 | 1 | PASS | FAIL | FAIL | 1 |
| decision_reversal | 1 | 1 | PASS | PASS | PASS | 0 |
| messy | 1 | 1 | PASS | PASS | PASS | 0 |
| new_general_workability | 2 | 2 | PASS | PASS | PASS | 0 |
| new_no_decision | 0 | 0 | PASS | PASS | PASS | 0 |
| new_pricing_change | 1 | 1 | PASS | PASS | PASS | 0 |
| new_vendor_selection | 1 | 1 | PASS | FAIL | FAIL | 1 |
| new_decision_reversal | 1 | 1 | PASS | PASS | PASS | 0 |
| new_messy_implicit | 1 | 1 | PASS | PASS | PASS | 0 |

## Failures

### vendor_selection
Failure files: `outputs/runs/slack_baseline_after_docs_fix/failures/vendor_selection`
- canonical vendor tradeoff: expected `['Higher monthly spend is the accepted tradeoff.']`, actual `['The higher monthly spend is a cost of choosing Datadog is the accepted tradeoff.']`

### new_vendor_selection
Failure files: `outputs/runs/slack_baseline_after_docs_fix/failures/new_vendor_selection`
- canonical vendor tradeoff: expected `['Higher monthly spend is the accepted tradeoff.']`, actual `[]`
