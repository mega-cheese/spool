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
| document_vendor_selection | PASS | - | PASS |

## Scenario Summary

| Scenario | Expected Count | Actual Count | Count Pass | Invariant Pass | Verdict | Failure Count |
| --- | ---: | ---: | --- | --- | --- | ---: |
| google_doc_vendor_selection | 1 | 1 | PASS | PASS | PASS | 0 |
| mixed_slack_doc_vendor_selection | 1 | 1 | PASS | FAIL | FAIL | 1 |
| uploaded_doc_vendor_selection | 1 | 1 | PASS | PASS | PASS | 0 |

## Failures

### mixed_slack_doc_vendor_selection
Failure files: `outputs/runs/docs_check/failures/mixed_slack_doc_vendor_selection`
- document rejected alternative timeline excluded: expected `no ConfigCat migration timeline`, actual `['The migration would take longer with ConfigCat.', 'Implementation can finish in about eight business days.']`
