#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


FIXTURES_DIR = Path("examples/pr_trust_adversarial")
OUTPUT_ROOT = Path("outputs/trust_runs_adversarial")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    fixtures_root = repo_root / FIXTURES_DIR
    output_root = repo_root / OUTPUT_ROOT

    if not fixtures_root.exists():
        print(f"Fixture directory not found: {fixtures_root}", file=sys.stderr)
        return 1

    for fixture_dir in sorted(path for path in fixtures_root.iterdir() if path.is_dir()):
        run_fixture(repo_root, output_root, fixture_dir)

    return 0


def run_fixture(repo_root: Path, output_root: Path, fixture_dir: Path) -> None:
    out_dir = output_root / fixture_dir.name
    command = [
        sys.executable,
        "spool_trust.py",
        "--diff",
        str(fixture_dir / "diff.patch"),
        "--agent-summary",
        str(fixture_dir / "agent_summary.md"),
        "--out",
        str(out_dir),
        "--pretty",
    ]

    task_path = fixture_dir / "task.md"
    if task_path.exists():
        command.extend(["--task", str(task_path)])

    test_output_path = fixture_dir / "test_output.txt"
    if test_output_path.exists():
        command.extend(["--test-output", str(test_output_path)])

    repo_memory_path = fixture_dir / "approved_decisions.yml"
    if not repo_memory_path.exists():
        repo_memory_path = fixture_dir / "approved_repo_memory.json"
    if repo_memory_path.exists():
        command.extend(["--repo-memory", str(repo_memory_path)])

    subprocess.run(command, cwd=repo_root, check=True, capture_output=True, text=True)

    report_path = out_dir / "trust_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    verdict = str(payload.get("overall_verdict") or "unknown")
    expected = parse_expected_verdict(fixture_dir / "expected_notes.md")

    summary = f"{fixture_dir.name}: {verdict}"
    if expected:
        summary += f" (expected: {expected})"
    print(summary)


def parse_expected_verdict(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"Expected verdict:\s*`?([a-z_]+)`?", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
