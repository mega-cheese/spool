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
from .pr_body_sections import parse_pr_body_sections
from .pr_context import load_pr_evidence


PR_VIEW_FIELDS = "number,title,body,url,headRefName,baseRefName,state"
PR_CHECK_FIELDS = "bucket,completedAt,description,event,link,name,startedAt,state,workflow"


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
    checks_payload = fetch_pr_checks(pr_ref)
    evidence_paths = write_pr_evidence_files(metadata, diff_text, checks_payload, output_dir)
    assignment_path = str(task_path) if task_path else evidence_paths.get("assignment")
    agent_summary_path = evidence_paths.get("agent_summary") or evidence_paths["pr_body"]
    test_output_path = evidence_paths.get("test_output")

    evidence = load_pr_evidence(
        diff_path=evidence_paths["diff"],
        task_path=assignment_path,
        agent_summary_path=agent_summary_path,
        test_output_path=test_output_path,
        pr_metadata_path=evidence_paths.get("pr_metadata"),
        pr_title_text=str(metadata.get("title") or ""),
        ci_checks_path=evidence_paths.get("ci_checks"),
        repo_memory_path=repo_memory_path,
    )
    report = analyze_pr_trust(evidence)
    report_paths = write_trust_report(report, output_dir, pretty=pretty)
    return PRCheckResult(
        report=report,
        report_paths=report_paths,
        evidence_paths={
            **evidence_paths,
            "task": assignment_path,
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


def fetch_pr_checks(pr_ref: str) -> dict[str, Any]:
    try:
        stdout = _run_gh(
            ["pr", "checks", pr_ref, "--json", PR_CHECK_FIELDS],
            fetch_label="PR checks",
            allow_exit_codes={8},
        )
    except PRIngestionError as exc:
        return {"status": "unavailable", "error": str(exc), "checks": []}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"status": "malformed", "error": f"Could not parse PR checks from `gh pr checks`: {exc}", "checks": []}
    if not isinstance(payload, list):
        return {"status": "malformed", "error": "Could not parse PR checks from `gh pr checks`: expected a JSON array.", "checks": []}
    return {"status": "available" if payload else "empty", "checks": payload}


def write_pr_evidence_files(
    metadata: dict[str, Any],
    diff_text: str,
    checks_payload: dict[str, Any],
    output_dir: Path,
) -> dict[str, str | None]:
    metadata_path = output_dir / "pr_metadata.json"
    pr_body_path = output_dir / "pr_body.md"
    diff_path = output_dir / "diff.patch"
    checks_path = output_dir / "checks.json"

    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    title = str(metadata.get("title") or "").strip()
    body = str(metadata.get("body") or "").strip()
    pr_body_path.write_text(_pr_body_text(title, body), encoding="utf-8")
    diff_path.write_text(diff_text, encoding="utf-8")
    checks_path.write_text(json.dumps(checks_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sections = parse_pr_body_sections(body)
    assignment_path = _write_optional_section(output_dir / "assignment.md", sections.assignment_text)
    agent_summary_path = _write_optional_section(output_dir / "agent_summary.md", sections.agent_summary_text)
    test_output_path = _write_optional_section(output_dir / "test_output.txt", sections.test_evidence_text)

    return {
        "pr_metadata": str(metadata_path),
        "pr_body": str(pr_body_path),
        "diff": str(diff_path),
        "ci_checks": str(checks_path),
        "assignment": str(assignment_path) if assignment_path else None,
        "agent_summary": str(agent_summary_path) if agent_summary_path else None,
        "test_output": str(test_output_path) if test_output_path else None,
    }


def print_summary(result: PRCheckResult) -> None:
    payload = result.report.to_dict()
    summary = payload.get("summary") or {}
    claims = payload.get("agent_claims") or []
    scope = payload.get("scope") or {}
    scope_violations = scope.get("scope_violations") or []
    unsupported_count = int(summary.get("unsupported_claims_count", sum(1 for claim in claims if claim.get("verdict") == "unsupported")))
    weak_missing_count = len(payload.get("weak_or_missing_evidence") or [])
    checks = payload.get("ci_checks") or {}
    report_path = result.report_paths.get("trust_report_md") or result.report_paths.get("trust_report_json")

    print(f"verdict: {payload.get('overall_verdict')}")
    print(f"allowed_scope_status: {summary.get('allowed_scope_status')}")
    print(f"unsupported_claims_count: {unsupported_count}")
    print(f"scope_violations: {', '.join(scope_violations) if scope_violations else 'None'}")
    print(f"weak_missing_evidence_count: {weak_missing_count}")
    print(f"checks_status: {checks.get('status', 'not_supported')}")
    if checks.get("status") == "available":
        print(f"checks_passed: {checks.get('passed_count', 0)}")
        print(f"checks_failed: {checks.get('failed_count', 0)}")
        print(f"checks_pending: {checks.get('pending_count', 0)}")
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


def _run_gh(args: list[str], *, fetch_label: str, allow_exit_codes: set[int] | None = None) -> str:
    command = ["gh", *args]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise PRIngestionError("GitHub CLI `gh` is not installed or is not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        if allow_exit_codes and exc.returncode in allow_exit_codes:
            stdout = getattr(exc, "stdout", None) or getattr(exc, "output", None) or ""
            if stdout:
                return str(stdout)
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


def _write_optional_section(path: Path, text: str) -> Path | None:
    if not text.strip():
        return None
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
