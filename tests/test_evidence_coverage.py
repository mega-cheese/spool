from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.spool.merge_authorization.evidence_coverage import build_evidence_coverage
from src.spool.merge_authorization.pr_context import load_pr_evidence
from src.spool.merge_authorization.scope_gate import parse_changed_files


class EvidenceCoverageTest(unittest.TestCase):
    def _write(self, path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    def test_classifies_available_and_not_supported_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            diff = self._write(root / "diff.patch", "diff --git a/src/a.py b/src/a.py\n")
            task = self._write(root / "task.md", "# Task\n\nUpdate helper.\n")
            summary = self._write(root / "agent_summary.md", "Updated `src/a.py`.\n")
            memory = self._write(root / "approved_decisions.yml", "decisions:\n  - id: DEC-1\n    title: Keep helpers small\n")
            evidence = load_pr_evidence(
                diff_path=diff,
                task_path=task,
                agent_summary_path=summary,
                repo_memory_path=memory,
            )

            coverage = build_evidence_coverage(evidence, parse_changed_files(evidence.diff_text))

        self.assertEqual(coverage.pr_body, "available")
        self.assertEqual(coverage.diff, "available")
        self.assertEqual(coverage.changed_files, "available")
        self.assertEqual(coverage.assignment, "available")
        self.assertEqual(coverage.pr_title, "not_supported")
        self.assertEqual(coverage.ci_checks, "not_supported")
        self.assertEqual(coverage.test_output, "not_provided")
        self.assertEqual(coverage.approved_decisions, "available")
        self.assertEqual(coverage.legacy_repo_memory, "not_configured")
        self.assertTrue(any("No test output" in note for note in coverage.notes))

    def test_empty_assignment_is_reported_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            diff = self._write(root / "diff.patch", "diff --git a/src/a.py b/src/a.py\n")
            task = self._write(root / "task.md", "")
            summary = self._write(root / "agent_summary.md", "Updated `src/a.py`.\n")
            evidence = load_pr_evidence(diff_path=diff, task_path=task, agent_summary_path=summary)

            coverage = build_evidence_coverage(evidence, parse_changed_files(evidence.diff_text))

        self.assertEqual(coverage.assignment, "empty")
        self.assertTrue(any("partial" in note.lower() for note in coverage.notes))

    def test_empty_test_output_is_reported_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            diff = self._write(root / "diff.patch", "diff --git a/src/a.py b/src/a.py\n")
            task = self._write(root / "task.md", "Update helper.\n")
            summary = self._write(root / "agent_summary.md", "Tests passed.\n")
            test_output = self._write(root / "test_output.txt", "")
            evidence = load_pr_evidence(
                diff_path=diff,
                task_path=task,
                agent_summary_path=summary,
                test_output_path=test_output,
            )

            coverage = build_evidence_coverage(evidence, parse_changed_files(evidence.diff_text))

        self.assertEqual(coverage.test_output, "empty")


if __name__ == "__main__":
    unittest.main()
