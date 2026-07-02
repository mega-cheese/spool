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

    def _mock_gh(self, *, body: str = "Updated `src/review_actions.py`.") -> mock.Mock:
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
        return mock.Mock(side_effect=[self._gh_completed(json.dumps(metadata)), self._gh_completed(diff)])

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
            test_output_exists = (out_dir / "test_output.txt").exists()

        self.assertEqual(result.report.overall_verdict, "continue")
        self.assertEqual(payload["summary"]["allowed_scope_status"], "within_allowed_scope")
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertEqual(metadata["number"], 123)
        self.assertIn("# Update review actions", pr_body)
        self.assertIn("Updated `src/review_actions.py`.", pr_body)
        self.assertTrue(diff_exists)
        self.assertTrue(report_md_exists)
        self.assertFalse(test_output_exists)
        self.assertEqual(mocked_run.call_args_list[0].args[0][:3], ["gh", "pr", "view"])
        self.assertEqual(mocked_run.call_args_list[1].args[0][:3], ["gh", "pr", "diff"])

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
        self.assertIn("report_path:", output)

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
