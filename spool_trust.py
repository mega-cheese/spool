#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from src.spool.merge_authorization.merge_authorization import analyze_pr_trust
from src.spool.merge_authorization.merge_authorization_report import write_trust_report
from src.spool.merge_authorization.pr_context import load_pr_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a PR-native Spool trust report.")
    parser.add_argument("--diff", required=True, help="Path to a git diff patch file.")
    parser.add_argument("--task", help="Optional path to the task or mandate file.")
    parser.add_argument("--agent-summary", required=True, help="Path to the agent summary or transcript.")
    parser.add_argument("--out", required=True, help="Output directory for trust_report.json and trust_report.md.")
    parser.add_argument("--test-output", help="Optional test or CI output file.")
    parser.add_argument("--repo-memory", help="Optional approved decision memory path. Supports approved_decisions.yml and legacy approved_repo_memory.json.")
    parser.add_argument("--mode", choices=["heuristic"], default="heuristic")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    evidence = load_pr_evidence(
        diff_path=args.diff,
        task_path=args.task,
        agent_summary_path=args.agent_summary,
        test_output_path=args.test_output,
        repo_memory_path=args.repo_memory,
    )
    report = analyze_pr_trust(evidence, mode=args.mode)
    paths = write_trust_report(report, args.out, pretty=args.pretty)

    print(f"Overall verdict: {report.overall_verdict}")
    print(f"Wrote {Path(paths['trust_report_json']).resolve()}")
    print(f"Wrote {Path(paths['trust_report_md']).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
