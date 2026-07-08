from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.spool.merge_authorization.decision_memory import load_approved_decisions
from src.spool.merge_authorization.decision_approval import main


class SpoolApproveDecisionsTest(unittest.TestCase):
    def _report_payload(self, candidate_decisions: list[dict[str, object]] | None = None) -> dict[str, object]:
        return {
            "overall_verdict": "human_review",
            "candidate_decisions": candidate_decisions
            if candidate_decisions is not None
            else [
                {
                    "id": "CAND-001",
                    "title": "Spool loads .spool/approved_decisions.yml by default when present",
                    "decision_type": "process_policy",
                    "source": "agent_summary",
                    "quote": "Spool loads .spool/approved_decisions.yml by default when present.",
                    "recommended_action": "approve_or_reject",
                },
                {
                    "id": "CAND-002",
                    "title": "PR checks remain read-only and do not automatically approve candidate decisions",
                    "decision_type": "process_policy",
                    "source": "agent_summary",
                    "quote": "PR checks remain read-only and do not automatically approve candidate decisions.",
                    "recommended_action": "approve_or_reject",
                },
                {
                    "id": "CAND-003",
                    "title": "Report JSON schema changes must remain backward compatible",
                    "decision_type": "api_policy",
                    "source": "agent_summary",
                    "quote": "Report JSON schema changes must remain backward compatible.",
                    "recommended_action": "approve_or_reject",
                },
            ],
        }

    def _write_report(self, root: Path, payload: dict[str, object]) -> Path:
        report_path = root / "trust_report.json"
        report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return report_path

    def test_lists_candidate_decisions_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["--report", str(report_path)])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Candidate decisions in report:", output)
        self.assertIn("1. Spool loads .spool/approved_decisions.yml by default when present.", output)
        self.assertIn("Re-run with --approve", output)
        self.assertFalse((root / ".spool" / "approved_decisions.yml").exists())

    def test_approve_all_dry_run_does_not_mutate_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["--report", str(report_path), "--approve", "all"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Dry run: 3 decision(s) selected for approval.", output)
        self.assertIn("Would add:", output)
        self.assertIn("Re-run with --write", output)
        self.assertFalse((root / ".spool" / "approved_decisions.yml").exists())

    def test_repeated_approve_indexes_select_only_requested_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["--report", str(report_path), "--approve", "1", "--approve", "3"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Dry run: 2 decision(s) selected for approval.", output)
        self.assertIn("Spool loads .spool/approved_decisions.yml by default when present.", output)
        self.assertIn("Report JSON schema changes must remain backward compatible.", output)
        self.assertNotIn("PR checks remain read-only and do not automatically approve candidate decisions.", output)

    def test_write_appends_selected_decisions_to_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            memory_path = root / ".spool" / "approved_decisions.yml"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--report",
                        str(report_path),
                        "--approve",
                        "all",
                        "--repo-memory",
                        str(memory_path),
                        "--write",
                    ]
                )

            text = memory_path.read_text(encoding="utf-8")
            memory = load_approved_decisions(memory_path)

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn(f"Approved memory updated: {memory_path}", output)
        self.assertIn("Added: 3", output)
        self.assertEqual(memory.status, "available")
        self.assertEqual(len(memory.decisions), 3)
        self.assertIn("decisions:", text)
        self.assertIn("source_candidate_id: CAND-001", text)

    def test_existing_decisions_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            memory_path = root / ".spool" / "approved_decisions.yml"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                """
decisions:
  - id: DEC-001
    status: approved
    title: Existing decision
    type: process_policy
    scope:
      applies_to_paths: []
      applies_to_concepts: []
    must:
      - Existing decision.
    must_not: []
    requires_approval_for: []
    approved_by:
      - Avery
    evidence: []
""".lstrip(),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--report",
                    str(report_path),
                    "--approve",
                    "1",
                    "--repo-memory",
                    str(memory_path),
                    "--write",
                ]
            )
            memory = load_approved_decisions(memory_path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(memory.decisions), 2)
        self.assertEqual(memory.decisions[0].id, "DEC-001")

    def test_exact_duplicate_decisions_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = self._report_payload(candidate_decisions=[self._report_payload()["candidate_decisions"][0]])
            report_path = self._write_report(root, payload)
            memory_path = root / ".spool" / "approved_decisions.yml"
            first_exit_code = main(
                [
                    "--report",
                    str(report_path),
                    "--approve",
                    "all",
                    "--repo-memory",
                    str(memory_path),
                    "--write",
                ]
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                second_exit_code = main(
                    [
                        "--report",
                        str(report_path),
                        "--approve",
                        "all",
                        "--repo-memory",
                        str(memory_path),
                        "--write",
                    ]
                )
            memory = load_approved_decisions(memory_path)

        output = stdout.getvalue()
        self.assertEqual(first_exit_code, 0)
        self.assertEqual(second_exit_code, 0)
        self.assertIn("Added: 0", output)
        self.assertIn("Skipped duplicates: 1", output)
        self.assertEqual(len(memory.decisions), 1)

    def test_missing_memory_file_is_created_only_in_write_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            memory_path = root / ".spool" / "approved_decisions.yml"
            dry_run_exit = main(
                [
                    "--report",
                    str(report_path),
                    "--approve",
                    "1",
                    "--repo-memory",
                    str(memory_path),
                ]
            )
            self.assertEqual(dry_run_exit, 0)
            self.assertFalse(memory_path.exists())

            write_exit = main(
                [
                    "--report",
                    str(report_path),
                    "--approve",
                    "1",
                    "--repo-memory",
                    str(memory_path),
                    "--write",
                ]
            )

            self.assertEqual(write_exit, 0)
            self.assertTrue(memory_path.exists())

    def test_invalid_approval_index_exits_nonzero_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["--report", str(report_path), "--approve", "4"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Invalid approval index", stderr.getvalue())

    def test_missing_report_exits_nonzero_with_clear_error(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = main(["--report", "/tmp/definitely-missing-trust-report.json"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not read trust report", stderr.getvalue())

    def test_malformed_report_exits_nonzero_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "trust_report.json"
            report_path.write_text("{not json}\n", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["--report", str(report_path)])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not parse trust report JSON", stderr.getvalue())

    def test_report_with_no_candidate_decisions_exits_zero_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload(candidate_decisions=[]))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["--report", str(report_path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("No candidate decisions found in report.", stdout.getvalue())

    def test_loader_reads_memory_written_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            memory_path = root / ".spool" / "approved_decisions.yml"

            exit_code = main(
                [
                    "--report",
                    str(report_path),
                    "--approve",
                    "1",
                    "--repo-memory",
                    str(memory_path),
                    "--write",
                ]
            )
            memory = load_approved_decisions(memory_path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(memory.status, "available")
        self.assertEqual(len(memory.decisions), 1)
        self.assertTrue(memory.decisions[0].id.startswith("decision_"))


if __name__ == "__main__":
    unittest.main()
