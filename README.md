# Spool

Spool is a merge authorization layer for AI-generated pull requests.

It checks whether an AI agent's proposed change can be trusted to merge given the assignment, the PR's own claims, the actual diff, available test evidence, and approved repo decisions. Spool is not trying to be a general AI code reviewer. It focuses on merge authorization: scope, claim support, evidence coverage, decision conflicts, and candidate repo decisions that need human approval.

The PR-native path currently lives under:

```text
spool_trust.py
src/spool/merge_authorization/
examples/pr_trust/
examples/pr_trust_adversarial/
tests/test_pr_trust.py
```

Run a local merge authorization report with:

```bash
venv/bin/python spool_trust.py \
  --diff examples/pr_trust/basic_supported/diff.patch \
  --task examples/pr_trust/basic_supported/task.md \
  --agent-summary examples/pr_trust/basic_supported/agent_summary.md \
  --test-output examples/pr_trust/basic_supported/test_output.txt \
  --repo-memory examples/pr_trust/memory_conflict/approved_repo_memory.json \
  --out outputs/trust_runs/basic_supported \
  --pretty
```

Run the adversarial PR-trust fixture suite with:

```bash
venv/bin/python scripts/run_pr_trust_adversarial.py
```

Run Spool against a local GitHub PR with the GitHub CLI:

```bash
venv/bin/python scripts/spool_check_pr.py 123 \
  --task path/to/task.md \
  --repo-memory .spool/approved_decisions.yml \
  --out outputs/spool/pr-123
```

This is a read-only local design-partner workflow. It uses `gh pr view` to write `pr_metadata.json` and `pr_body.md`, uses `gh pr diff` to write `diff.patch`, treats the PR body as the agent summary, then writes the normal `trust_report.json` and `trust_report.md`. `--task` and `--repo-memory` are optional; omitting `--task` keeps the existing partial-review behavior. The command does not post PR comments, does not mutate approved decision memory, and does not require manually preparing `agent_summary.md`.

Approved repo decisions are the primary merge-time memory primitive for the PR-native path. By default Spool looks for `.spool/approved_decisions.yml`, and `--repo-memory` can point to either that YAML shape or the legacy `approved_repo_memory.json` fixture shape while compatibility remains in place.

Merge authorization reports now include `evidence_coverage` and `candidate_decisions`. Evidence coverage shows which local PR sources were available, such as assignment, agent summary, diff, changed files, test output, and approved decision memory. Candidate decisions surface durable product, security, architecture, process, or implementation rules introduced by a PR so a reviewer can approve or reject them before they become future guardrails.

Evidence coverage uses a small status vocabulary:

- `available`: the source exists and Spool used it.
- `empty`: the source was provided but contained no meaningful content.
- `not_provided`: the source is supported by the current input mode, but the user did not provide it.
- `not_supported`: the source is not supported by the current input mode, such as PR title or CI checks in local file mode.
- `not_configured`: the repo or project has not configured an optional source, such as approved decision memory.
- `missing`: a source was expected or explicitly referenced, but could not be found.
- `malformed`: the source exists, but could not be parsed.

## Legacy prototype

This repository still contains the earlier company-memory prototype. That code ingests fake Slack JSON, local documents, and meeting notes; extracts source-backed decisions and memory candidates; and provides a local Streamlit review UI. It is temporarily kept in place while the PR-native Spool architecture is separated.

The legacy pipeline is:

```text
Fake Slack JSON
  -> Slack parser
  -> canonical events
  -> decision extraction
  -> deterministic source resolution
  -> validation and clean filtering
  -> raw and clean JSON decision records
  -> optional Streamlit review
  -> approved decision memory
```

The source JSON is the source of truth. The LLM, when used, proposes claims and `message_ts` citations only. Python verifies those timestamps and attaches the original quotes.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your environment exposes `python` instead of `python3`, either form is fine.

## Heuristic Mode

```bash
python3 extract_decisions.py data/original_fake.json outputs/decisions.json --mode heuristic --pretty
```

The heuristic extractor is deterministic and offline. It groups messages by thread, finds decision-like threads, extracts compact decision titles, separates reasoning from objections, assumptions, risks, tradeoffs, timelines, deadlines, and action items, then attaches exact source quotes.

## LLM Mode

Create a local environment file or export variables:

```bash
cp .env.example .env
export OPENAI_API_KEY="your_key_here"
export OPENAI_MODEL="gpt-5.4-mini"
```

Run:

```bash
python3 extract_decisions.py data/pricing_change.json outputs/decisions.json --mode llm --verify --pretty
```

LLM mode uses the OpenAI Responses API with structured JSON output. The model is instructed not to write quotes. It can only cite existing message timestamps, and invalid or unsupported claims are filtered before clean output.

LLM mode writes latest files by default when the clean output is `outputs/decisions.json`:

```text
outputs/raw_decisions.json
outputs/decisions.json
```

It also archives every LLM run with a shared run ID:

```text
outputs/raw_decisions_run_<scenario>_YYYYMMDD_HHMMSS.json
outputs/decisions_run_<scenario>_YYYYMMDD_HHMMSS.json
```

Archive filenames include the input scenario stem. For `data/vendor_selection.json`, archived files look like:

```text
outputs/raw_decisions_run_vendor_selection_YYYYMMDD_HHMMSS.json
outputs/decisions_run_vendor_selection_YYYYMMDD_HHMMSS.json
```

`raw_decisions.json` contains the model-proposed records after source resolution. It keeps unsupported, indirect, and needs-review claims so they can be inspected, and its metadata includes the full validation issue list.

`decisions.json` is the clean product output. By default it removes claims that are unsupported, indirect, marked `needs_review`, missing a valid source, or contain obvious mixed-language alphabetic artifacts. It also deduplicates near-identical claims across categories, keeps implementation estimates in `timelines`, external milestones in `deadlines`, decision start dates in `effective_date`, and non-date revisit conditions in `revisit_trigger`. Clean post-processing removes duplicate timeline, deadline, risk, or action-item versions of information already captured in structured fields.

Clean metadata is intentionally concise:

```json
{
  "raw_validation_issue_count": 2,
  "clean_validation_issue_count": 0,
  "removed_claim_count": 1,
  "repair_count": 0,
  "postprocessing_change_count": 1
}
```

The full validation issue list stays in raw output for debugging.

To keep review-worthy claims in the clean output, run:

```bash
python3 extract_decisions.py data/pricing_change.json outputs/decisions.json --mode llm --include-review-claims --pretty
```

To choose the raw output path, run:

```bash
python3 extract_decisions.py data/pricing_change.json outputs/decisions.json --mode llm --raw-output outputs/my_raw_decisions.json --pretty
```

To choose an archive ID, run:

```bash
python3 extract_decisions.py data/pricing_change.json outputs/decisions.json --mode llm --run-id 1227
```

This creates:

```text
outputs/decisions_run_pricing_change_1227.json
outputs/raw_decisions_run_pricing_change_1227.json
```

If those archived paths already exist, the extractor uses a suffix such as `_2`. To avoid replacing the latest files, run:

```bash
python3 extract_decisions.py data/pricing_change.json outputs/decisions.json --mode llm --run-id 1227 --no-overwrite
```

## Candidate Shadow Pipeline

The candidate shadow pipeline keeps the legacy clean output behavior, then writes all shadow artifacts into a per-run folder:

```bash
python3 extract_decisions.py data/new_vendor_selection.json outputs/decisions.json --mode heuristic --pipeline candidates-shadow --run-id 1227 --pretty
```

This still updates `outputs/decisions.json` unless `--no-overwrite` is passed. It also creates:

```text
outputs/runs/1227/decisions.json
outputs/runs/1227/normalized_events_debug.json
outputs/runs/1227/source_bundles_debug.json
outputs/runs/1227/candidates_debug.json
outputs/runs/1227/state_resolution_debug.json
outputs/runs/1227/conflicts_debug.json
outputs/runs/1227/meeting_metadata.json
outputs/runs/1227/action_items.json
outputs/runs/1227/open_questions.json
outputs/runs/1227/document_memory_debug.json
outputs/runs/1227/review_queue.json
outputs/runs/1227/review_queue.md
outputs/runs/1227/candidate_memory.json
outputs/runs/1227/approved_memory.json
outputs/runs/1227/rejected_memory.json
outputs/runs/1227/review_actions.json
outputs/runs/1227/memory_state_debug.json
outputs/runs/1227/run_metadata.json
```

In LLM mode, the run folder also includes `raw_decisions.json`.

## Document Ingestion

Spool can now ingest local document sources into the same normalized-event pipeline as Slack fixtures. This is not a real Google Docs or Drive integration yet; it is the local architecture for future integrations and uploaded files.

Supported local document inputs:

- Structured Google Doc fixture JSON with `source_type: "google_doc"` and `sections`.
- Mixed fixture JSON with a `sources` array containing Slack and document sources.
- Uploaded local `.txt`, `.md`, `.docx`, or `.pdf` files.

Examples:

```bash
python3 extract_decisions.py data/google_doc_vendor_selection.json outputs/decisions.json --mode heuristic --pipeline candidates-shadow --pretty
python3 extract_decisions.py data/uploaded_doc_vendor_selection.md outputs/decisions.json --mode heuristic --pipeline candidates-shadow --pretty
python3 extract_decisions.py path/to/file.pdf outputs/decisions.json --mode llm --pipeline candidates-shadow --source-type uploaded_document --pretty
```

PDF text extraction uses `pypdf`; image-only PDFs raise a clear OCR-not-supported error. DOCX parsing uses `python-docx`.

## Agent Change Packets

An agent change packet is a Markdown artifact containing the original implementation request, the agent implementation summary, claimed validation, git stat, and git diff. Spool classifies these documents as `agent_change_packet` when the headings and packet language are present.

Spool audits the agent's story against deterministic packet evidence: what files or capabilities were claimed, whether the git stat/diff supports those claims, which validation claims are only weakly supported, and whether obvious out-of-scope additions appear. This is not a GitHub integration and it is not AI code review; it verifies the agent's claims, scope, and technical decisions.

Agent packet runs still write the standard run-folder artifacts. They also write:

```text
agent_change_packet_debug.json
agent_claims.json
agent_audit_report.json
agent_audit_report.md
```

Candidate technical decisions, risks, open questions, and important implementation claims are projected into `candidate_memory.json` for review. `approved_memory.json` remains empty by default unless an explicit review action approves memory.

Example:

```bash
python3 extract_decisions.py data/agent_change_packet_v081.md outputs/decisions.json --mode heuristic --pipeline candidates-shadow --pretty
```

## Meeting Minutes

Uploaded `.md`, `.txt`, `.docx`, and `.pdf` documents are classified before extraction. Documents that look like meeting minutes, for example notes with `Present`, `Absent`, `Action Items`, agenda numbering, or `To be Discussed Next Time`, get a meeting-memory pass in addition to the existing decision pipeline.

Meeting-minutes runs write:

```text
meeting_metadata.json
action_items.json
open_questions.json
document_memory_debug.json
```

Confirmed operational memories are also projected into `decisions.json`. Unresolved discussion stays out of confirmed decisions and appears in `open_questions.json` or the review queue.

Example:

```bash
python3 extract_decisions.py data/meeting_minutes_vymun_april15.md outputs/decisions.json --mode heuristic --pipeline candidates-shadow --pretty
```

## Approved Memory Infrastructure

Spool now writes a review-state layer beside the legacy extractor outputs. `clean_memory.json`, `action_items.json`, and `open_questions.json` remain backward compatible. The new canonical extracted-memory artifact is `candidate_memory.json`.

Action items are mapped to memory objects with `type: "commitment"`; this keeps them as source-backed company memory rather than turning Spool into a todo tracker. By default, `--auto-approve false` keeps extracted memory as unreviewed candidates. Conservative review policy marks weak parses, missing evidence, suspicious owners, or question-like commitments as `needs_review` or `rejected` in `memory_state_debug.json`.

New per-run artifacts:

```text
candidate_memory.json
approved_memory.json
rejected_memory.json
review_actions.json
memory_state_debug.json
```

`approved_memory.json` and `rejected_memory.json` are local JSON stores. They may be empty until a human or explicit review action approves/rejects candidates.

## Review Actions

Use `review_memory.py` to apply review actions to a run folder without Streamlit or a database:

```bash
venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action approve --reviewer Owen
venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action reject --reason "False positive"
venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action edit --patch '{"title":"Owen should reach out to schools","owner_names":["Owen"]}'
venv/bin/python review_memory.py --run-dir outputs/runs/<run_id> --memory-id mem_123 --action mark_resolved --reason "Confirmed in follow-up notes"
```

Review actions update `candidate_memory.json`, `approved_memory.json`, `rejected_memory.json`, and `memory_state_debug.json`. They append to `review_actions.json` as an audit history rather than overwriting it.

Action items are represented as commitments in candidate/approved memory. Spool is not a todo tracker: commitment statuses are company-memory states such as `unresolved`, `resolved`, `superseded`, `stale`, and `unknown`.

## Batch Scenario Verification

To verify every original and new fixture in one organized run, use:

```bash
python3 run_scenarios.py --scenario-set all --mode llm --pipeline candidates-shadow --run-id claim_ownership_check
```

The batch runner writes one folder under `outputs/runs/<run_id>/` with aggregate files keyed by scenario:

```text
outputs/runs/claim_ownership_check/
  batch_manifest.json
  verification_summary.md
  verification_summary.csv
  decisions_by_scenario.json
  raw_decisions_by_scenario.json
  normalized_events_by_scenario.json
  source_bundles_by_scenario.json
  candidates_by_scenario.json
  state_resolution_by_scenario.json
  conflicts_by_scenario.json
  meeting_metadata_by_scenario.json
  action_items_by_scenario.json
  open_questions_by_scenario.json
  document_memory_debug_by_scenario.json
  review_queue_by_scenario.json
```

Passing scenarios stay in the aggregate files. Only failed scenarios get expanded under `failures/<scenario_name>/` with individual debug artifacts. If a requested run folder already exists, the runner creates a safe suffix such as `_2` unless `--overwrite` is explicitly passed.

Document-ingestion fixtures can be verified separately:

```bash
python3 run_scenarios.py --scenario-set docs --mode llm --pipeline candidates-shadow --run-id docs_check
```

Review-queue fixtures can be verified separately:

```bash
python3 run_scenarios.py --scenario-set review --mode llm --pipeline candidates-shadow --run-id review_queue_check
```

Review items do not fail otherwise passing scenario checks. They are a separate triage surface for candidates that should not enter clean memory without human attention.

Conflict-detection fixtures can be verified separately:

```bash
python3 run_scenarios.py --scenario-set conflict --mode llm --pipeline candidates-shadow --run-id conflict_check
```

Meeting-minutes fixtures can be verified separately:

```bash
python3 run_scenarios.py --scenario-set meeting_minutes --mode llm --pipeline candidates-shadow --run-id meeting_minutes_check
```

## Conflict Detection

Spool v0.5 adds conservative conflict detection for source-backed decisions. If two strong decision sources select different mutually exclusive options for the same decision area, clean memory does not silently pick one. Instead, Spool removes the conflicted confirmed decision from clean output and writes a high-severity review item.

Examples that can be flagged:

- Slack approval selects LaunchDarkly, while a Google Doc decision section selects ConfigCat for feature flags.
- An older document selects ConfigCat, while a newer Slack approval selects LaunchDarkly. No automatic recency override is applied yet.

Examples intentionally not flagged:

- A rejected alternative section says ConfigCat was cheaper.
- A tradeoff says LaunchDarkly has higher monthly spend.
- Slack and a document repeat the same chosen option.

Candidates-shadow, UI, and batch runs write conflict debug artifacts:

```text
conflicts_debug.json
review_queue.json
review_queue.md
```

Batch runs also write:

```text
conflicts_by_scenario.json
```

## Review Queue

The review queue is the first human-review layer for Spool v0.4. Clean memory remains the reliable presentation layer; incomplete, ambiguous, unsupported, low-confidence, or ownerless candidates are written into a queue beside the normal debug artifacts.

Candidates-shadow runs write:

```text
review_queue.json
review_queue.md
```

An empty queue is still written:

```json
{
  "metadata": {
    "review_item_count": 0
  },
  "items": []
}
```

Initial review reasons include:

- `approval_missing`
- `owner_missing`
- `conflicting_sources`
- `stale_source`
- `unsupported_claim`
- `low_confidence`
- `ambiguous_decision`
- `extraction_warning`

Confirmed decisions with a clear decision source and clean validation stay out of the queue. The queue is intentionally conservative: it gives a reviewer something to inspect without changing decision detection or blocking already reliable clean output.

## Local Demo UI

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The UI is local-only and visualizes existing pipeline outputs. It does not add Slack OAuth, Google OAuth, a database, vector search, auth, billing, or deployment behavior.

The sidebar lets you choose a fixture or upload a local `.json`, `.md`, `.txt`, `.docx`, or supported `.pdf` file. The UI defaults to the candidates-shadow pipeline for demos only; CLI defaults are unchanged.

Example scenarios to run:

```text
data/new_vendor_selection.json
data/mixed_slack_doc_vendor_selection.json
data/uploaded_doc_vendor_selection.md
```

Each UI run writes artifacts under:

```text
outputs/ui_runs/<run_id>/
  decisions.json
  raw_decisions.json
  normalized_events_debug.json
  source_bundles_debug.json
  candidates_debug.json
  state_resolution_debug.json
  conflicts_debug.json
  meeting_metadata.json
  action_items.json
  open_questions.json
  document_memory_debug.json
  review_queue.json
  review_queue.md
  candidate_memory.json
  approved_memory.json
  rejected_memory.json
  review_actions.json
  memory_state_debug.json
  run_metadata.json
```

The confirmed-decisions panel shows decision cards with reasoning, objections, assumptions, risks, tradeoffs, timelines, deadlines, action items, and source quotes. Meeting-minutes panels show meeting metadata, extracted action items, and unresolved open questions. The review-queue panel shows open review items, severity, reason, recommended action, related candidate IDs, source event IDs, source quotes, and conflict options when present. The conflicts panel shows detected conflicting source decisions from `conflicts_debug.json`. The excluded-memory and claim-ownership panels show rejected-alternative claims, such as ConfigCat or New Relic comparison claims, so the demo can show that Spool does not blindly summarize.

## Query Approved Memory

The query layer only reads approved records.

```bash
python3 query_decisions.py outputs/approved_decisions.json "Why did we delay enterprise launch?"
```

If no approved record supports the question, it says:

```text
I do not have reliable approved memory for that.
```

## Anti-Hallucination Design

The system does not promise that an LLM never hallucinates. It promises that no unsupported claim is saved without a source.

Validation checks include:

- Every decision has a source.
- Every claim has a source.
- Every cited `message_ts` exists in the original Slack JSON.
- Quotes are attached by code from the source JSON.
- Claim text is checked for obvious mixed-language alphabetic artifacts.
- Unsupported, indirect, and needs-review claims are excluded from clean output by default.
- Participants must appear in the source thread.
- Confirmed status is not allowed unless the thread contains explicit confirmation language.
- If a later message explicitly reverses or replaces an earlier decision in the same thread, clean output keeps the later active decision.
- Empty categories are allowed and preferred over invented claims.
- `effective_date` contains when a decision starts applying, such as `2026-08-01`.
- `deadlines` contain external milestones or hard due dates, such as an SLA start date.
- `action_items` contain assigned next steps and are not treated as decisions.
- `revisit_date` only contains ISO-like dates explicitly present in source review/revisit language.
- `revisit_trigger` contains non-date revisit conditions such as `After launch if Redis becomes a bottleneck.`

Support levels mean:

- `direct`: the claim is close enough to the cited message to show in clean output.
- `indirect`: the claim may be inferred from the source, but needs review.
- `unsupported`: the claim is not backed by the cited message and is removed from clean output by default.

Claim categories mean:

- `reasoning`: why the decision was made.
- `objections`: explicit concern, worry, disagreement, or pushback.
- `assumptions`: explicitly stated assumptions.
- `risks`: possible negative outcomes or failure modes.
- `tradeoffs`: accepted costs of the decision.
- `timelines`: implementation estimates, durations, or delivery windows.

Structured timing fields mean:

- `effective_date`: when the decision starts applying.
- `deadlines`: external milestones or hard due dates with sources.
- `revisit_date`: calendar date to revisit the decision.
- `revisit_trigger`: non-date condition for revisiting the decision.

Decision records split source lists into:

- `decision_sources`: explicit final, approval, or decision messages.
- `supporting_sources`: messages used by reasoning, objections, assumptions, risks, tradeoffs, timelines, deadlines, or action items.
- `all_sources`: the deduplicated union of decision and supporting sources.

## Scenario Fixtures

The current local fixtures are:

- `data/original_fake.json`: SOC 2 launch delay and Redis session-state decisions.
- `data/pricing_change.json`: pricing change with `effective_date`, grandfathering, objection, tradeoff, and revisit trigger.
- `data/mobile_app.json`: unresolved no-decision thread that should produce zero clean decisions.
- `data/vendor_selection.json`: Datadog vendor selection with deadline, timeline, assumption, tradeoff, and revisit trigger.
- `data/decision_reversal.json`: SendGrid-to-Postmark reversal where clean output keeps only the latest active decision.
- `data/messy.json`: implicit Organization Settings decision inferred from convergence, ownership, scope, timeline, deadline, and tradeoff.

Implicit decisions require concrete implementation commitment after convergence. Brainstorming, vague preferences, unresolved alternatives, and threads with no owner or implementation step remain no-decision threads.

## Tests

```bash
python3 -m unittest discover -s tests
```

The test suite covers Slack parsing, canonical event conversion, heuristic extraction, source resolution, validation, query behavior, and a Streamlit import smoke path that does not require Streamlit to be installed.

## Troubleshooting

```bash
python3 check_env.py
python3 -m pip install -r requirements.txt
python3 -c "import openai; print(openai.__version__)"
```

If LLM mode fails, check that `OPENAI_API_KEY` is set. Heuristic mode does not need network access or API credentials.

## Limitations

- Input is fake Slack-style JSON only.
- The heuristic extractor is intentionally conservative and phrase based.
- The optional support verifier is a lightweight local check, not semantic entailment.
- Approved memory is stored in local JSON files.
- Keyword query is simple and does not use embeddings yet.

## Roadmap

- v0.1: fake Slack JSON to heuristic decision records.
- v0.2: cleaner deterministic classification.
- v0.3: LLM extraction with timestamp citations.
- v0.4: stronger validation and optional entailment verification.
- v0.5: human review UI.
- v0.6: keyword query over approved records.
- v1.0 later: real integrations such as Slack, GitHub, Notion, Google Drive, Jira, Linear, and meeting transcripts.
## PR trust adversarial runner

Run all local PR trust adversarial fixtures with:

```bash
venv/bin/python scripts/run_pr_trust_adversarial.py
```

The runner uses each fixture directory under `examples/pr_trust_adversarial/`, passes fixture-local `approved_decisions.yml` when present, falls back to `approved_repo_memory.json` for legacy fixtures, and writes reports to `outputs/trust_runs_adversarial/<fixture_name>/`.
