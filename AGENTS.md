# AGENTS.md

## Project Identity

Spool is a PR-native merge authorization layer for AI-generated pull requests.

It checks whether an AI agent's PR:

1. Matches the assignment or task.
2. Tells the truth about what changed.
3. Provides evidence for claims such as "tests pass."
4. Respects approved repo decisions.
5. Introduces any candidate decisions that require human approval before becoming future repo behavior.

Spool is **not** a generic AI code reviewer. Do not turn it into CodeRabbit, Greptile, Copilot review, or a generic "is this code good?" checker.

Spool is also **not** a broad company-memory product. The older Slack/docs/meeting decision-memory prototype is legacy and should not be expanded unless explicitly requested.

The core product primitive is:

> Approved repo decisions -> PR merge authorization.

---

## Current Product Direction

The active product is the PR-native merge authorization path under:

```text
src/spool/merge_authorization/
```

The compatibility CLI is:

```text
spool_trust.py
```

The active concepts are:

* PR evidence
* assignment/task checking
* agent claim verification
* evidence coverage
* approved decision memory
* candidate decisions
* merge authorization verdicts
* Markdown/JSON merge authorization reports

The legacy company-memory prototype may still exist in the repo, but it is not the active product direction.

---

## Do Not Build Unless Explicitly Asked

Do not add or expand:

* GitHub Action
* GitHub App
* dashboard
* Streamlit UI
* Slack ingestion
* meeting transcript ingestion
* document ingestion
* broad LLM extraction mode
* compliance exports
* enterprise auth
* web server
* generic company-memory search
* generic AI code review comments

Stay focused on local PR-native merge authorization unless the user explicitly asks otherwise.

---

## Product Boundaries

When making changes, preserve this distinction:

### Code checker

Asks:

```text
Is the code good?
```

Spool should not primarily compete here.

### Scope checker

Asks:

```text
Did the agent touch something outside its bounds?
```

Spool can include this, but it is not enough by itself.

### Spool

Asks:

```text
Can this AI-generated change be trusted to merge, given the assignment, the PR's claims, the actual diff, available evidence, and approved repo decisions?
```

Spool should be differentiated by:

* approved decision memory
* candidate decisions requiring approval
* claim-vs-evidence verification
* merge authorization verdicts
* evidence coverage transparency
* same diff + different assignment = different verdict

---

## Approved Decision Memory

Approved decision memory is not generic memory. It is enforceable merge-time context.

Preferred file shape:

```text
.spool/approved_decisions.yml
```

Approved decisions represent human-approved rules, constraints, product behaviors, technical decisions, or security policies that future AI-generated PRs must respect.

Legacy memory files such as `approved_repo_memory.json` may remain supported for backward compatibility, but new work should prefer the approved decisions model.

Important rule:

> PR changes to approved decision memory are candidate changes, not already-approved memory.

An agent must not approve its own future authority by modifying memory in the same PR.

---

## Candidate Decisions

A candidate durable decision is a product, architecture, security, API, policy, or process choice that future PRs should continue to respect.

A candidate decision is proposed project memory, not a change summary. Do not list ordinary implementation details, tests, documentation updates, refactors, bug fixes, or file additions as candidate decisions.

Examples:

* "Going forward, all unsupported models should hide upload by default."
* "Billing retry policy should now default to three attempts."
* "Auth config changes should be allowed from UI tasks if tests pass."
* "Report JSON schema changes must remain backward compatible."
* "Merge enforcement policy should require explicit approval for dependency changes."

Non-examples:

* "Added tests."
* "Updated README."
* "Added a helper function."
* "Refactored a module."
* "Fixed a parser bug."
* "Added a normal implementation file."
* "Tests should pass."
* "This function should return null."
* "Should update README."

Candidate decisions should be surfaced as:

```text
candidate_decisions
```

`buried_decisions` may remain for backward compatibility, but product-facing output should emphasize candidate decisions.

---

## Evidence Coverage

Spool must be honest about what evidence was available.

Use the current evidence status vocabulary consistently:

* `available`: the source exists and Spool used it.
* `empty`: the source was provided but contained no meaningful content.
* `not_provided`: the source is supported by the current input mode, but the user did not provide it.
* `not_supported`: the source is not supported by the current input mode.
* `not_configured`: the repo/project has not configured this optional source.
* `missing`: a source was expected or explicitly referenced, but could not be found.
* `malformed`: the source exists, but could not be parsed.

Do not pretend missing evidence exists.

Examples:

* Local CLI has no PR title input -> `pr_title: not_supported`
* Local CLI has no CI integration -> `ci_checks: not_supported`
* No approved decision memory provided -> `approved_decisions: not_configured`
* Agent says tests passed but no test output exists -> test claim should be `missing_evidence`

---

## Verdict Vocabulary

Use the existing verdict ladder unless explicitly changed:

* `continue`
* `human_review`
* `repair`
* `stop`

Suggested severity:

* Approved decision contradiction -> `stop`
* Sensitive unauthorized change -> `stop` or `human_review`
* Contradicted test-pass claim -> `stop` or `human_review`
* Ordinary assignment/scope violation -> `repair`
* Missing test evidence -> `human_review`
* Candidate decision requiring approval -> `human_review`
* Missing memory -> not a failure by itself

Missing memory should produce `not_configured`, `no_relevant_memory`, or equivalent. It should not become `unsupported`.

---

## Sensitive Areas

Treat changes involving these areas as higher risk:

* auth
* permissions
* billing
* payment
* migration
* schema
* security
* env
* secrets
* config
* lockfile
* privacy
* telemetry

Sensitive changes are not automatically wrong, but they usually require stronger authorization or review.

---

## Compatibility

Keep the compatibility CLI working unless explicitly asked to rename it:

```bash
venv/bin/python spool_trust.py \
  --diff ... \
  --task ... \
  --agent-summary ... \
  --test-output ... \
  --repo-memory ... \
  --out ...
```

Old import paths may remain as compatibility shims if tests or existing fixtures rely on them.

Do not remove backward compatibility casually.

---

## Testing
