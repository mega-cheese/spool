from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src.spool.merge_authorization.pr_comment import PRCommentError, load_report, main, render_pr_comment


class SpoolCommentPRTest(unittest.TestCase):
    def _report_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "overall_verdict": "continue",
            "summary": {
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 8,
                "unsupported_claims_count": 0,
                "test_evidence_status": "supported",
                "decision_memory_status": "not_configured",
            },
            "scope": {
                "scope_violations": [],
                "forbidden_violations": [],
            },
            "evidence_coverage": {
                "assignment": "available",
                "test_output": "available",
                "ci_checks": "unavailable",
            },
            "ci_checks": {
                "status": "unavailable",
                "passed_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "failed_checks": [],
                "pending_checks": [],
            },
            "risks": [],
            "reviewer_actions": ["continue"],
            "weak_or_missing_evidence": [],
            "agent_claims": [],
            "decision_memory": {
                "status": "not_configured",
            },
            "scope_report": {},
        }
        payload.update(overrides)
        return payload

    def _write_report(self, root: Path, payload: dict[str, object]) -> Path:
        report_path = root / "trust_report.json"
        report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return report_path

    def test_renders_continue_report_comment(self) -> None:
        comment = render_pr_comment(self._report_payload())

        self.assertIn("## Spool Merge Authorization", comment)
        self.assertIn("**Verdict:** Ready to continue (`continue`)", comment)
        self.assertIn("**Scope:** within_allowed_scope", comment)
        self.assertIn("**Agent claims:** 8 supported, 0 unsupported", comment)
        self.assertIn("**Test evidence:** PR body tests available; GitHub checks unavailable", comment)
        self.assertIn("**Decision memory:** not_configured", comment)
        self.assertIn("### Reviewer actions", comment)
        self.assertIn("- continue", comment)

    def test_renders_repair_report_with_scope_violations(self) -> None:
        payload = self._report_payload(
            overall_verdict="repair",
            summary={
                "allowed_scope_status": "outside_allowed_scope",
                "supported_claims_count": 4,
                "unsupported_claims_count": 1,
                "test_evidence_status": "supported",
                "decision_memory_status": "available",
            },
            scope={
                "scope_violations": ["src/auth.py"],
                "forbidden_violations": [],
            },
            risks=["Changed files outside the approved task boundary: src/auth.py."],
            reviewer_actions=["request repair"],
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Verdict:** Needs repair (`repair`)", comment)
        self.assertIn("**Scope violations:** `src/auth.py`", comment)
        self.assertIn("Scope violations were found: `src/auth.py`.", comment)
        self.assertIn("### Risks", comment)
        self.assertIn("- Changed files outside the approved task boundary: src/auth.py.", comment)

    def test_renders_human_review_report_with_weak_missing_evidence(self) -> None:
        payload = self._report_payload(
            overall_verdict="human_review",
            weak_or_missing_evidence=["The agent says tests passed, but no test output was provided."],
            reviewer_actions=["human review required"],
            evidence_coverage={
                "assignment": "available",
                "test_output": "not_provided",
                "ci_checks": "unavailable",
            },
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Verdict:** Needs human review (`human_review`)", comment)
        self.assertIn("**Weak/missing evidence:** 1", comment)
        self.assertIn("Spool found 1 weak or missing evidence item.", comment)

    def test_renders_candidate_decision_risk_with_clear_project_level_wording(self) -> None:
        risk = (
            "This PR proposes project-level decisions that may affect future Spool behavior. "
            "A reviewer should approve or reject them before they are added to repo memory."
        )
        payload = self._report_payload(
            overall_verdict="human_review",
            risks=[risk],
            reviewer_actions=["approve or reject proposed project-level decisions before adding them to repo memory"],
        )

        comment = render_pr_comment(payload)

        self.assertIn(risk, comment)
        self.assertIn("approve or reject proposed project-level decisions", comment)
        self.assertNotIn("introduced durable decisions", comment)

    def test_handles_missing_optional_fields(self) -> None:
        comment = render_pr_comment({"overall_verdict": "continue"})

        self.assertIn("**Verdict:** Ready to continue (`continue`)", comment)
        self.assertIn("**Scope:** unknown", comment)
        self.assertIn("**Agent claims:** 0 supported, 0 unsupported", comment)
        self.assertIn("**Test evidence:** unknown", comment)
        self.assertIn("**Decision memory:** unknown", comment)

    def test_dry_run_prints_comment_and_does_not_call_gh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stdout = io.StringIO()
            with mock.patch("subprocess.run") as mocked_run, redirect_stdout(stdout):
                exit_code = main(["1", "--report", str(report_path), "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertIn("## Spool Merge Authorization", stdout.getvalue())
        mocked_run.assert_not_called()

    def test_out_writes_comment_file_and_does_not_call_gh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            with mock.patch("subprocess.run") as mocked_run:
                exit_code = main(["1", "--report", str(report_path), "--out", str(out_path)])

            content = out_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("## Spool Merge Authorization", content)
        mocked_run.assert_not_called()

    def test_post_calls_gh_pr_comment_with_body_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            mocked_run = mock.Mock(return_value=subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="", stderr=""))
            with mock.patch("subprocess.run", mocked_run):
                exit_code = main(["1", "--report", str(report_path), "--out", str(out_path), "--post"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            mocked_run.call_args.args[0][:6],
            ["gh", "pr", "comment", "1", "--body-file", str(out_path)],
        )

    def test_post_surfaces_gh_failure_clearly(self) -> None:
        error = subprocess.CalledProcessError(returncode=1, cmd=["gh", "pr", "comment"], stderr="not authenticated")
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stderr = io.StringIO()
            with mock.patch("subprocess.run", side_effect=error), redirect_stderr(stderr):
                exit_code = main(["1", "--report", str(report_path), "--post"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not post PR comment", stderr.getvalue())

    def test_generated_comment_includes_checks_and_decision_memory_details(self) -> None:
        payload = self._report_payload(
            ci_checks={
                "status": "available",
                "passed_count": 4,
                "failed_count": 1,
                "pending_count": 0,
                "failed_checks": ["unit"],
                "pending_checks": [],
            },
            evidence_coverage={
                "assignment": "available",
                "test_output": "available",
                "ci_checks": "available",
            },
            decision_memory={"status": "available"},
            summary={
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 8,
                "unsupported_claims_count": 0,
                "test_evidence_status": "contradicted",
                "decision_memory_status": "available",
            },
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Test evidence:** GitHub checks failing", comment)
        self.assertIn("**Decision memory:** configured", comment)
        self.assertIn("GitHub checks include failures: `unit`.", comment)

    def test_generated_comment_surfaces_unavailable_decision_memory(self) -> None:
        payload = self._report_payload(
            summary={
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 8,
                "unsupported_claims_count": 0,
                "test_evidence_status": "supported",
                "decision_memory_status": "unavailable",
            },
            decision_memory={"status": "missing"},
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Decision memory:** unavailable", comment)

    def test_load_report_raises_clear_error_for_missing_file(self) -> None:
        with self.assertRaisesRegex(PRCommentError, "Could not read trust report"):
            load_report(Path("/tmp/definitely-missing-trust-report.json"))


if __name__ == "__main__":
    unittest.main()
