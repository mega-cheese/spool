from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .merge_authorization import PRTrustReport, analyze_pr_trust
from .merge_authorization_report import write_trust_report
from .pr_context import load_pr_evidence


PR_VIEW_FIELDS = "number,title,body,url,headRefName,baseRefName,state"


class PRIngestionError(RuntimeError):
    pass


@dataclass
class PRCheckResult:
    report: PRTrustReport
    report_paths: dict[str, str]
    evidence_paths: dict[str, str | None]
    pr_metadata: dict[str, Any]


def run_pr_check(
    pr_ref: str,
    *,
    out_dir: str | Path,
    task_path: str | Path | None = None,
    repo_memory_path: str | Path | None = None,
    pretty: bool = True,
) -> PRCheckResult:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = fetch_pr_metadata(pr_ref)
    diff_text = fetch_pr_diff(pr_ref)
    evidence_paths = write_pr_evidence_files(metadata, diff_text, output_dir)

    evidence = load_pr_evidence(
        diff_path=evidence_paths["diff"],
        task_path=task_path,
        agent_summary_path=evidence_paths["pr_body"],
        repo_memory_path=repo_memory_path,
    )
    report = analyze_pr_trust(evidence)
    report_paths = write_trust_report(report, output_dir, pretty=pretty)
    return PRCheckResult(
        report=report,
        report_paths=report_paths,
        evidence_paths={
            **evidence_paths,
            "task": str(task_path) if task_path else None,
            "repo_memory": str(repo_memory_path) if repo_memory_path else None,
        },
        pr_metadata=metadata,
    )


def fetch_pr_metadata(pr_ref: str) -> dict[str, Any]:
    stdout = _run_gh(
        ["pr", "view", pr_ref, "--json", PR_VIEW_FIELDS],
        fetch_label="PR metadata",
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PRIngestionError(f"Could not parse PR metadata from `gh pr view`: {exc}") from exc
    if not isinstance(payload, dict):
        raise PRIngestionError("Could not parse PR metadata from `gh pr view`: expected a JSON object.")
    return payload


def fetch_pr_diff(pr_ref: str) -> str:
    return _run_gh(["pr", "diff", pr_ref], fetch_label="PR diff")


def write_pr_evidence_files(metadata: dict[str, Any], diff_text: str, output_dir: Path) -> dict[str, str]:
    metadata_path = output_dir / "pr_metadata.json"
    pr_body_path = output_dir / "pr_body.md"
    diff_path = output_dir / "diff.patch"

    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    title = str(metadata.get("title") or "").strip()
    body = str(metadata.get("body") or "").strip()
    pr_body_path.write_text(_pr_body_text(title, body), encoding="utf-8")
    diff_path.write_text(diff_text, encoding="utf-8")

    return {
        "pr_metadata": str(metadata_path),
        "pr_body": str(pr_body_path),
        "diff": str(diff_path),
    }


def print_summary(result: PRCheckResult) -> None:
    payload = result.report.to_dict()
    summary = payload.get("summary") or {}
    claims = payload.get("agent_claims") or []
    scope = payload.get("scope") or {}
    scope_violations = scope.get("scope_violations") or []
    unsupported_count = sum(1 for claim in claims if claim.get("verdict") == "unsupported")
    weak_missing_count = len(payload.get("weak_or_missing_evidence") or [])
    report_path = result.report_paths.get("trust_report_md") or result.report_paths.get("trust_report_json")

    print(f"verdict: {payload.get('overall_verdict')}")
    print(f"allowed_scope_status: {summary.get('allowed_scope_status')}")
    print(f"unsupported_claims_count: {unsupported_count}")
    print(f"scope_violations: {', '.join(scope_violations) if scope_violations else 'None'}")
    print(f"weak_missing_evidence_count: {weak_missing_count}")
    print(f"report_path: {Path(str(report_path)).resolve()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect local GitHub PR evidence with gh and run Spool.")
    parser.add_argument("pr", help="GitHub PR number, URL, or branch understood by `gh pr view`.")
    parser.add_argument("--task", help="Optional assignment/task file.")
    parser.add_argument("--repo-memory", help="Optional approved decision memory file.")
    parser.add_argument("--out", required=True, help="Output directory for evidence files and Spool reports.")
    parser.add_argument("--no-pretty", action="store_true", help="Write compact trust_report.json.")
    args = parser.parse_args(argv)

    try:
        result = run_pr_check(
            args.pr,
            out_dir=args.out,
            task_path=args.task,
            repo_memory_path=args.repo_memory,
            pretty=not args.no_pretty,
        )
    except PRIngestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_summary(result)
    return 0


def _run_gh(args: list[str], *, fetch_label: str) -> str:
    command = ["gh", *args]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise PRIngestionError("GitHub CLI `gh` is not installed or is not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        if _looks_like_auth_error(message):
            raise PRIngestionError("GitHub CLI is not authenticated. Run `gh auth login` and try again.") from exc
        detail = f": {message}" if message else ""
        raise PRIngestionError(f"Could not fetch {fetch_label} with `gh`{detail}") from exc
    return result.stdout


def _looks_like_auth_error(message: str) -> bool:
    lower = message.lower()
    return "auth login" in lower or "not authenticated" in lower or "authentication" in lower


def _pr_body_text(title: str, body: str) -> str:
    parts = []
    if title:
        parts.append(f"# {title}")
    if body:
        parts.append(body)
    return "\n\n".join(parts).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
