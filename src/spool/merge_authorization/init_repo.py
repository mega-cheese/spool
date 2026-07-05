from __future__ import annotations

import argparse
from pathlib import Path


SPOOL_AGENTS_MARKER = "<!-- spool-pr-evidence -->"

CONFIG_YML = """assignment_sources:
  - pr_body_spool_task
  - linked_github_issue
  - pr_body

agent_summary_sources:
  - pr_body_agent_summary
  - pr_body

test_evidence_sources:
  - github_checks
  - pr_body_tests_run

ignore_paths:
  - .idea/
  - __pycache__/
  - "*.pyc"
  - outputs/
  - dogfood/

risky_paths:
  - requirements.txt
  - pyproject.toml
  - package.json
  - .github/workflows/
"""

APPROVED_DECISIONS_YML = """decisions: []
"""

AGENT_INSTRUCTIONS_MD = """# Spool Agent Instructions

When opening or updating a pull request, fill out the Spool sections in the PR body.

## Required PR sections

- `## Spool Task`
- `## Agent Summary`
- `## Tests Run`
- `## Candidate Decisions`

## Rules

- Restate the user's task in `## Spool Task`.
- Include allowed files/areas if they were provided.
- If the original task is unknown, write `Unknown`.
- Summarize implementation work in `## Agent Summary`.
- List important files changed.
- List exact test commands and results in `## Tests Run`.
- If no tests were run, write `Not run`.
- Candidate decisions are proposed project memory, not a change summary.
- List only durable product, architecture, security, API, policy, or process decisions that future PRs should respect.
- Do not list normal implementation details as candidate decisions.
- Do not list tests, docs, refactors, bug fixes, or file additions as candidate decisions.
- If there are no candidate decisions, write `None`.
- If the PR only implements an already-approved decision, write `None` or explain that no new durable decisions are introduced.
- Do not claim tests passed unless tests or CI actually passed.
- Do not claim a file changed unless it appears in the diff.
- Do not approve candidate decisions yourself.
- Treat the PR body as claims for Spool to verify, not as final truth.
"""

PR_TEMPLATE_MD = """## Spool Task

<!-- Restate the user's task or linked issue. Include allowed files/areas if known. -->

Allowed files/areas:
- <!-- Example: src/spool/merge_authorization/ -->
- <!-- Example: tests/ -->
- <!-- Example: README.md -->

## Agent Summary

<!-- Summarize what changed. List important files changed. -->

Files changed:
- <!-- Example: src/example.py -->

## Tests Run

<!-- List exact commands and results. If no tests were run, write "Not run." -->

## Candidate Decisions

<!--
List only durable product, architecture, security, API, policy, or process decisions that future PRs should respect.

Do not list ordinary implementation details, tests, docs, refactors, bug fixes, or file additions.

If this PR introduces no new durable decisions, write "None."
-->
"""

AGENTS_SPOOL_SECTION = f"""{SPOOL_AGENTS_MARKER}
## Spool PR Evidence

When creating or updating pull requests, follow `.spool/agent_instructions.md` and fill out the Spool sections in the pull request template.

Spool treats agent-written PR sections as claims to verify against the diff, tests, and approved repo decisions. If a task, test result, or decision is unknown, say so explicitly instead of guessing.
"""


def init_repo(
    root: str | Path = ".",
    *,
    include_agents_md: bool = True,
    include_pr_template: bool = True,
) -> dict[str, object]:
    repo_root = Path(root)
    summary: dict[str, list[str] | int] = {
        "created": [],
        "exists": [],
        "updated": [],
        "created_count": 0,
        "exists_count": 0,
        "updated_count": 0,
    }

    _write_if_absent(repo_root, ".spool/config.yml", CONFIG_YML, summary)
    _write_if_absent(repo_root, ".spool/approved_decisions.yml", APPROVED_DECISIONS_YML, summary)
    _write_if_absent(repo_root, ".spool/agent_instructions.md", AGENT_INSTRUCTIONS_MD, summary)
    if include_pr_template:
        _write_if_absent(repo_root, ".github/pull_request_template.md", PR_TEMPLATE_MD, summary)
    if include_agents_md:
        _ensure_agents_section(repo_root, summary)

    _refresh_counts(summary)
    return dict(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize local Spool merge authorization files.")
    parser.add_argument("--root", default=".", help="Repository root to initialize.")
    parser.add_argument("--yes", action="store_true", help="Accepted for non-interactive scripts; no prompts are shown.")
    parser.add_argument("--no-agents-md", action="store_true", help="Do not create or update AGENTS.md.")
    parser.add_argument("--no-pr-template", action="store_true", help="Do not create .github/pull_request_template.md.")
    args = parser.parse_args(argv)

    summary = init_repo(
        args.root,
        include_agents_md=not args.no_agents_md,
        include_pr_template=not args.no_pr_template,
    )
    print_summary(summary)
    return 0


def print_summary(summary: dict[str, object]) -> None:
    print("Spool repo initialization")
    for key in ("created", "updated", "exists"):
        paths = list(summary.get(key) or [])
        label = key.replace("_", " ")
        print(f"{label}: {len(paths)}")
        for path in paths:
            print(f"- {path}")


def _write_if_absent(root: Path, relative_path: str, content: str, summary: dict[str, list[str] | int]) -> None:
    path = root / relative_path
    if path.exists():
        _append_summary(summary, "exists", relative_path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_with_trailing_newline(content), encoding="utf-8")
    _append_summary(summary, "created", relative_path)


def _ensure_agents_section(root: Path, summary: dict[str, list[str] | int]) -> None:
    relative_path = "AGENTS.md"
    path = root / relative_path
    if not path.exists():
        path.write_text(_with_trailing_newline(AGENTS_SPOOL_SECTION), encoding="utf-8")
        _append_summary(summary, "created", relative_path)
        return

    text = path.read_text(encoding="utf-8")
    if SPOOL_AGENTS_MARKER in text:
        _append_summary(summary, "exists", relative_path)
        return

    separator = "\n\n" if text.endswith("\n") else "\n\n"
    path.write_text(text.rstrip() + separator + _with_trailing_newline(AGENTS_SPOOL_SECTION), encoding="utf-8")
    _append_summary(summary, "updated", relative_path)


def _append_summary(summary: dict[str, list[str] | int], key: str, value: str) -> None:
    items = summary[key]
    if not isinstance(items, list):
        raise TypeError(f"summary[{key!r}] must be a list")
    items.append(value)


def _refresh_counts(summary: dict[str, list[str] | int]) -> None:
    for key in ("created", "exists", "updated"):
        items = summary[key]
        if not isinstance(items, list):
            raise TypeError(f"summary[{key!r}] must be a list")
        summary[f"{key}_count"] = len(items)


def _with_trailing_newline(text: str) -> str:
    return text.rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
