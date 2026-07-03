#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


FIXTURES_DIR = Path("examples/pr_trust_adversarial")
OUTPUT_ROOT = Path("outputs/trust_runs_adversarial")


@dataclass(frozen=True)
class FixtureResult:
    name: str
    actual_verdict: str
    expected_verdict: str | None

    @property
    def is_informational(self) -> bool:
        return self.expected_verdict is None

    @property
    def is_mismatch(self) -> bool:
        return self.expected_verdict is not None and self.actual_verdict != self.expected_verdict

    def summary_line(self) -> str:
        if self.expected_verdict:
            return f"{self.name}: {self.actual_verdict} (expected: {self.expected_verdict})"
        return f"{self.name}: {self.actual_verdict} (no expected verdict)"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    fixtures_root = repo_root / FIXTURES_DIR
    output_root = repo_root / OUTPUT_ROOT

    if not fixtures_root.exists():
        print(f"Fixture directory not found: {fixtures_root}", file=sys.stderr)
        return 1

    results: list[FixtureResult] = []
    for fixture_dir in sorted(path for path in fixtures_root.iterdir() if path.is_dir()):
        result = run_fixture(repo_root, output_root, fixture_dir)
        results.append(result)
        print(result.summary_line())

    mismatches = [result for result in results if result.is_mismatch]
    informational_count = sum(1 for result in results if result.is_informational)
    checked_count = len(results) - informational_count

    if mismatches:
        print(f"Adversarial PR trust mismatches: {len(mismatches)}", file=sys.stderr)
        for result in mismatches:
            print(
                f"- {result.name}: expected {result.expected_verdict}, got {result.actual_verdict}",
                file=sys.stderr,
            )
        return 1

    print(
        "Adversarial PR trust fixtures passed: "
        f"{checked_count} expected verdicts matched; "
        f"{informational_count} informational fixtures."
    )
    return 0


def run_fixture(repo_root: Path, output_root: Path, fixture_dir: Path) -> FixtureResult:
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

    return FixtureResult(
        name=fixture_dir.name,
        actual_verdict=verdict,
        expected_verdict=expected,
    )


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
