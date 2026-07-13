from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src.spool.merge_authorization.check_pr import PRIngestionError, main, run_pr_check


class SpoolCheckPRTest(unittest.TestCase):
    def _gh_completed(self, stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=stdout, stderr="")

    def _mock_gh(
        self,
        *,
        body: str = "Updated `src/review_actions.py`.",
        checks: list[dict[str, str]] | None = None,
        checks_error: subprocess.CalledProcessError | None = None,
    ) -> mock.Mock:
        metadata = {
            "number": 123,
            "title": "Update review actions",
            "body": body,
            "url": "https://github.com/acme/spool/pull/123",
            "headRefName": "agent/change",
            "baseRefName": "main",
            "state": "OPEN",
        }
        diff = """
diff --git a/src/review_actions.py b/src/review_actions.py
index 1111111..2222222 100644
--- a/src/review_actions.py
+++ b/src/review_actions.py
@@ -1,2 +1,3 @@
 def append_review_action(actions, action):
+    action["source"] = "github_pr"
     actions.append(action)
""".lstrip()
        checks_result = checks if checks is not None else []
        side_effect: list[object] = [self._gh_completed(json.dumps(metadata)), self._gh_completed(diff)]
        side_effect.append(checks_error or self._gh_completed(json.dumps(checks_result)))
        return mock.Mock(side_effect=side_effect)

    def _sectioned_body(self) -> str:
        return """
## Spool Task

Add local read-only GitHub PR ingestion for Spool.

Allowed files/areas:
- src/review_actions.py

## Agent Summary

Updated `src/review_actions.py`.

## Tests Run

OK

## Candidate Decisions

None.
""".lstrip()

    def _sectioned_body_with_test_claim(self) -> str:
        return """
## Spool Task

Add local read-only GitHub PR ingestion for Spool.

Allowed files/areas:
- src/review_actions.py

## Agent Summary

Updated `src/review_actions.py`.
Tests passed.

## Tests Run

OK

## Candidate Decisions

None.
""".lstrip()

    def test_run_pr_check_collects_github_evidence_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = root / "task.md"
            out_dir = root / "spool-pr-123"
            task.write_text("Allowed files:\n- `src/review_actions.py`\n", encoding="utf-8")
            mocked_run = self._mock_gh()

            with mock.patch("subprocess.run", mocked_run):
                result = run_pr_check("123", out_dir=out_dir, task_path=task)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            metadata = json.loads((out_dir / "pr_metadata.json").read_text(encoding="utf-8"))
            pr_body = (out_dir / "pr_body.md").read_text(encoding="utf-8")
            diff_exists = (out_dir / "diff.patch").exists()
            report_md_exists = (out_dir / "trust_report.md").exists()
            checks_exists = (out_dir / "checks.json").exists()
            test_output_exists = (out_dir / "test_output.txt").exists()

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["summary"]["allowed_scope_status"], "within_allowed_scope")
        self.assertEqual(payload["summary"]["unsupported_claims_count"], 0)
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertEqual(payload["evidence_coverage"]["pr_title"], "available")
        self.assertEqual(payload["evidence_coverage"]["ci_checks"], "empty")
        self.assertEqual(metadata["number"], 123)
        self.assertIn("# Update review actions", pr_body)
        self.assertIn("Updated `src/review_actions.py`.", pr_body)
        self.assertTrue(diff_exists)
        self.assertTrue(report_md_exists)
        self.assertTrue(checks_exists)
        self.assertFalse(test_output_exists)
        self.assertEqual(mocked_run.call_args_list[0].args[0][:3], ["gh", "pr", "view"])
        self.assertEqual(mocked_run.call_args_list[1].args[0][:3], ["gh", "pr", "diff"])
        self.assertEqual(mocked_run.call_args_list[2].args[0][:3], ["gh", "pr", "checks"])

    def test_github_pr_ingestion_does_not_report_pr_title_as_local_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", self._mock_gh()):
                run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            markdown = (out_dir / "trust_report.md").read_text(encoding="utf-8")

        notes = payload["evidence_coverage"]["notes"]
        self.assertIn("PR title: available", markdown)
        self.assertFalse(any("PR title is not available in local file mode." == note for note in notes))

    def test_task_is_optional_and_repo_memory_can_be_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "approved_decisions.yml"
            out_dir = root / "spool-pr-123"
            memory.write_text("decisions: []\n", encoding="utf-8")

            with mock.patch("subprocess.run", self._mock_gh()):
                result = run_pr_check("123", out_dir=out_dir, repo_memory_path=memory)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "human_review")
        self.assertEqual(payload["evidence_coverage"]["assignment"], "not_provided")
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "available")

    def test_run_pr_check_loads_default_spool_approved_decisions_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_dir = root / "spool-pr-123"
            memory = root / ".spool" / "approved_decisions.yml"
            memory.parent.mkdir(parents=True)
            memory.write_text("decisions: []\n", encoding="utf-8")

            with mock.patch("subprocess.run", self._mock_gh()):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["decision_memory_status"], "configured")
        self.assertEqual(payload["decision_memory"]["source"], str(memory))
        self.assertEqual(payload["decision_memory"]["status"], "available")
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "available")
        self.assertEqual(result.evidence_paths["repo_memory"], str(memory))

    def test_explicit_repo_memory_overrides_default_spool_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_memory = root / ".spool" / "approved_decisions.yml"
            explicit_memory = root / "custom_approved_decisions.yml"
            out_dir = root / "spool-pr-123"
            default_memory.parent.mkdir(parents=True)
            default_memory.write_text("decisions: []\n", encoding="utf-8")
            explicit_memory.write_text(
                """
decisions:
  - id: DEC-123
    status: approved
    title: Explicit memory wins
""".lstrip(),
                encoding="utf-8",
            )

            with mock.patch("subprocess.run", self._mock_gh()):
                result = run_pr_check("123", out_dir=out_dir, repo_memory_path=explicit_memory)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["decision_memory_status"], "configured")
        self.assertEqual(payload["decision_memory"]["source"], str(explicit_memory))
        self.assertEqual(payload["decision_memory"]["approved_decisions_count"], 1)
        self.assertEqual(result.evidence_paths["repo_memory"], str(explicit_memory))

    def test_missing_default_memory_preserves_not_configured_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"

            with mock.patch("subprocess.run", self._mock_gh()):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["decision_memory_status"], "not_configured")
        self.assertEqual(payload["decision_memory"]["status"], "not_configured")
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "not_configured")
        self.assertIsNone(result.evidence_paths["repo_memory"])

    def test_explicit_missing_memory_path_is_reported_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_dir = root / "spool-pr-123"
            missing_memory = root / "missing-approved-decisions.yml"

            with mock.patch("subprocess.run", self._mock_gh()):
                result = run_pr_check("123", out_dir=out_dir, repo_memory_path=missing_memory)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            markdown = (out_dir / "trust_report.md").read_text(encoding="utf-8")

        self.assertEqual(payload["summary"]["decision_memory_status"], "unavailable")
        self.assertEqual(payload["decision_memory"]["status"], "missing")
        self.assertEqual(payload["decision_memory"]["source"], str(missing_memory))
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "missing")
        self.assertIn("Decision memory: unavailable", markdown)
        self.assertEqual(result.evidence_paths["repo_memory"], str(missing_memory))

    def test_pr_body_task_section_is_used_when_task_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_dir = root / "spool-pr-123"

            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body())):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            assignment_text = (out_dir / "assignment.md").read_text(encoding="utf-8")
            agent_summary_text = (out_dir / "agent_summary.md").read_text(encoding="utf-8")
            test_output_text = (out_dir / "test_output.txt").read_text(encoding="utf-8")

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["evidence_coverage"]["assignment"], "available")
        self.assertEqual(payload["evidence_coverage"]["test_output"], "available")
        self.assertEqual(payload["summary"]["allowed_scope_status"], "within_allowed_scope")
        self.assertIn("Allowed files/areas", assignment_text)
        self.assertEqual(agent_summary_text.strip(), "Updated `src/review_actions.py`.")
        self.assertEqual(test_output_text.strip(), "OK")

    def test_task_file_overrides_pr_body_task_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = root / "task.md"
            out_dir = root / "spool-pr-123"
            task.write_text("Allowed files:\n- `src/other.py`\n", encoding="utf-8")

            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body())):
                result = run_pr_check("123", out_dir=out_dir, task_path=task)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "repair")
        self.assertEqual(payload["scope"]["task_source"], str(task))
        self.assertEqual(payload["scope"]["scope_violations"], ["src/review_actions.py"])
        self.assertFalse((out_dir / "assignment.md").exists())

    def test_no_sections_preserves_full_pr_body_summary_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"

            with mock.patch("subprocess.run", self._mock_gh(body="Updated `src/review_actions.py`.")):
                run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            pr_body = (out_dir / "pr_body.md").read_text(encoding="utf-8")

        self.assertEqual(payload["evidence_coverage"]["assignment"], "not_provided")
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertFalse((out_dir / "assignment.md").exists())
        self.assertFalse((out_dir / "agent_summary.md").exists())
        self.assertFalse((out_dir / "test_output.txt").exists())
        self.assertIn("Updated `src/review_actions.py`.", pr_body)

    def test_cli_prints_concise_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = root / "task.md"
            out_dir = root / "spool-pr-123"
            task.write_text("Allowed files:\n- `src/review_actions.py`\n", encoding="utf-8")
            stdout = io.StringIO()

            with mock.patch("subprocess.run", self._mock_gh()), redirect_stdout(stdout):
                exit_code = main(["123", "--task", str(task), "--out", str(out_dir)])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("verdict: continue", output)
        self.assertIn("allowed_scope_status: within_allowed_scope", output)
        self.assertIn("unsupported_claims_count: 0", output)
        self.assertIn("scope_violations: None", output)
        self.assertIn("weak_missing_evidence_count: 0", output)
        self.assertIn("checks_status: empty", output)
        self.assertIn("report_path:", output)

    def test_run_pr_check_fetches_successful_checks_and_supports_test_claim(self) -> None:
        checks = [
            {
                "name": "unit",
                "state": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-07-02T00:00:00Z",
                "completedAt": "2026-07-02T00:01:00Z",
                "link": "https://github.com/acme/spool/actions/runs/1",
            },
            {
                "name": "lint",
                "state": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": "2026-07-02T00:00:00Z",
                "completedAt": "2026-07-02T00:01:00Z",
                "link": "https://github.com/acme/spool/actions/runs/2",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body_with_test_claim(), checks=checks)):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            checks_payload = json.loads((out_dir / "checks.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["evidence_coverage"]["ci_checks"], "available")
        self.assertEqual(payload["ci_checks"]["status"], "available")
        self.assertEqual(payload["ci_checks"]["passed_count"], 2)
        self.assertEqual(payload["ci_checks"]["failed_count"], 0)
        self.assertEqual(checks_payload["status"], "available")
        test_claims = [claim for claim in payload["agent_claims"] if claim["claim_type"] == "test_result_claim"]
        self.assertEqual(test_claims[0]["verdict"], "supported")
        self.assertEqual(test_claims[0]["evidence_target"], "checks.json")

    def test_failing_checks_contradict_test_pass_claim(self) -> None:
        checks = [
            {"name": "unit", "state": "COMPLETED", "conclusion": "FAILURE", "link": "https://github.com/acme/spool/actions/runs/1"},
            {"name": "lint", "state": "COMPLETED", "conclusion": "SUCCESS", "link": "https://github.com/acme/spool/actions/runs/2"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body_with_test_claim(), checks=checks)):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "stop")
        self.assertEqual(payload["ci_checks"]["failed_count"], 1)
        self.assertEqual(payload["ci_checks"]["failed_checks"], ["unit"])
        test_claims = [claim for claim in payload["agent_claims"] if claim["claim_type"] == "test_result_claim"]
        self.assertEqual(test_claims[0]["verdict"], "contradicted")
        self.assertIn("failing GitHub checks", test_claims[0]["reason"])

    def test_pending_checks_make_test_pass_claim_need_review(self) -> None:
        checks = [{"name": "unit", "state": "IN_PROGRESS", "conclusion": "", "link": "https://github.com/acme/spool/actions/runs/1"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body_with_test_claim(), checks=checks)):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "human_review")
        self.assertEqual(payload["ci_checks"]["pending_count"], 1)
        test_claims = [claim for claim in payload["agent_claims"] if claim["claim_type"] == "test_result_claim"]
        self.assertEqual(test_claims[0]["verdict"], "needs_review")
        self.assertIn("pending GitHub checks", test_claims[0]["reason"])

    def test_pending_checks_exit_code_from_gh_is_still_parsed(self) -> None:
        metadata = {
            "number": 123,
            "title": "Update review actions",
            "body": self._sectioned_body_with_test_claim(),
            "url": "https://github.com/acme/spool/pull/123",
            "headRefName": "agent/change",
            "baseRefName": "main",
            "state": "OPEN",
        }
        diff = """
diff --git a/src/review_actions.py b/src/review_actions.py
index 1111111..2222222 100644
--- a/src/review_actions.py
+++ b/src/review_actions.py
@@ -1,2 +1,3 @@
 def append_review_action(actions, action):
+    action["source"] = "github_pr"
     actions.append(action)
""".lstrip()
        pending = [{"name": "unit", "bucket": "pending", "state": "PENDING", "link": "https://github.com/acme/spool/actions/runs/1"}]
        checks_error = subprocess.CalledProcessError(
            returncode=8,
            cmd=["gh", "pr", "checks"],
            output=json.dumps(pending),
            stderr="checks pending",
        )
        mocked_run = mock.Mock(
            side_effect=[
                self._gh_completed(json.dumps(metadata)),
                self._gh_completed(diff),
                checks_error,
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", mocked_run):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "human_review")
        self.assertEqual(payload["ci_checks"]["status"], "available")
        self.assertEqual(payload["ci_checks"]["pending_count"], 1)

    def test_empty_checks_fall_back_to_pr_body_tests_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch("subprocess.run", self._mock_gh(body=self._sectioned_body_with_test_claim(), checks=[])):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["evidence_coverage"]["ci_checks"], "empty")
        test_claims = [claim for claim in payload["agent_claims"] if claim["claim_type"] == "test_result_claim"]
        self.assertEqual(test_claims[0]["verdict"], "supported")
        self.assertEqual(test_claims[0]["evidence_target"], "test_output.txt")

    def test_checks_fetch_failure_writes_unavailable_evidence_and_continues(self) -> None:
        checks_error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "checks"],
            stderr="checks are not available for this pull request",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "spool-pr-123"
            with mock.patch(
                "subprocess.run",
                self._mock_gh(body=self._sectioned_body_with_test_claim(), checks_error=checks_error),
            ):
                result = run_pr_check("123", out_dir=out_dir)

            payload = json.loads((out_dir / "trust_report.json").read_text(encoding="utf-8"))
            checks_payload = json.loads((out_dir / "checks.json").read_text(encoding="utf-8"))

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["evidence_coverage"]["ci_checks"], "unavailable")
        self.assertEqual(payload["ci_checks"]["status"], "unavailable")
        self.assertIn("checks are not available", checks_payload["error"])
        test_claims = [claim for claim in payload["agent_claims"] if claim["claim_type"] == "test_result_claim"]
        self.assertEqual(test_claims[0]["verdict"], "supported")
        self.assertEqual(test_claims[0]["evidence_target"], "test_output.txt")

    def test_missing_gh_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stderr = io.StringIO()
            with mock.patch("subprocess.run", side_effect=FileNotFoundError()), redirect_stderr(stderr):
                exit_code = main(["123", "--out", str(Path(temp_dir) / "out")])

        self.assertEqual(exit_code, 1)
        self.assertIn("GitHub CLI `gh` is not installed", stderr.getvalue())

    def test_gh_auth_error_is_clear(self) -> None:
        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "view"],
            stderr="run `gh auth login` to authenticate",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            stderr = io.StringIO()
            with mock.patch("subprocess.run", side_effect=error), redirect_stderr(stderr):
                exit_code = main(["123", "--out", str(Path(temp_dir) / "out")])

        self.assertEqual(exit_code, 1)
        self.assertIn("GitHub CLI is not authenticated", stderr.getvalue())

    def test_pr_fetch_error_is_clear(self) -> None:
        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "view"],
            stderr="no pull requests found for branch",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("subprocess.run", side_effect=error):
                with self.assertRaisesRegex(PRIngestionError, "Could not fetch PR metadata"):
                    run_pr_check("nope", out_dir=Path(temp_dir) / "out")


if __name__ == "__main__":
    unittest.main()
