from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.pr_evidence import load_pr_evidence
from src.pr_trust import analyze_pr_trust
from src.scope_gate import evaluate_scope, parse_changed_files
from src.trust_report import write_trust_report


EXAMPLES = Path("examples/pr_trust")


class PRTrustTest(unittest.TestCase):
    def test_loads_pr_evidence_without_meeting_or_doc_assumptions(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "basic_supported" / "diff.patch",
            task_path=EXAMPLES / "basic_supported" / "task.md",
            agent_summary_path=EXAMPLES / "basic_supported" / "agent_summary.md",
            test_output_path=EXAMPLES / "basic_supported" / "test_output.txt",
            repo_memory_path=Path(".spool/approved_repo_memory.json"),
        )

        self.assertIn("review_actions.py", evidence.diff_text)
        self.assertIn("Allowed files", evidence.task_text)
        self.assertIn("Tests passed", evidence.agent_summary_text)
        self.assertIn("OK", evidence.test_output_text)
        self.assertFalse(evidence.repo_memory_exists)
        self.assertEqual(evidence.repo_memory_items, [])

    def test_parses_changed_files_from_unified_diff(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "basic_supported" / "diff.patch",
            task_path=EXAMPLES / "basic_supported" / "task.md",
            agent_summary_path=EXAMPLES / "basic_supported" / "agent_summary.md",
        )

        changes = parse_changed_files(evidence.diff_text)
        by_path = {change.path: change.change_type for change in changes}

        self.assertEqual(by_path["src/review_actions.py"], "modified")
        self.assertEqual(by_path["tests/test_review_actions.py"], "added")

    def test_detects_missing_test_evidence(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "missing_test_evidence" / "diff.patch",
            task_path=EXAMPLES / "missing_test_evidence" / "task.md",
            agent_summary_path=EXAMPLES / "missing_test_evidence" / "agent_summary.md",
        )

        report = analyze_pr_trust(evidence)
        test_claims = [claim for claim in report.claims if claim.claim_type == "test_result_claim"]

        self.assertEqual(report.overall_verdict, "human_review")
        self.assertTrue(test_claims)
        self.assertTrue(any(claim.verdict == "missing_evidence" for claim in test_claims))
        self.assertTrue(any("test output" in item.lower() for item in report.weak_or_missing_evidence))

    def test_detects_simple_scope_drift_from_allowed_files(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "scope_drift" / "diff.patch",
            task_path=EXAMPLES / "scope_drift" / "task.md",
            agent_summary_path=EXAMPLES / "scope_drift" / "agent_summary.md",
            test_output_path=EXAMPLES / "scope_drift" / "test_output.txt",
        )

        scope = evaluate_scope(evidence.task_text, parse_changed_files(evidence.diff_text))
        report = analyze_pr_trust(evidence)

        self.assertIn("src/auth.py", scope.scope_violations)
        self.assertEqual(report.overall_verdict, "repair")
        self.assertIn("src/auth.py", report.scope_report.scope_violations)

    def test_generates_json_and_markdown_trust_report(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "basic_supported" / "diff.patch",
            task_path=EXAMPLES / "basic_supported" / "task.md",
            agent_summary_path=EXAMPLES / "basic_supported" / "agent_summary.md",
            test_output_path=EXAMPLES / "basic_supported" / "test_output.txt",
        )
        report = analyze_pr_trust(evidence)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_trust_report(report, temp_dir, pretty=True)
            payload = json.loads(Path(paths["trust_report_json"]).read_text(encoding="utf-8"))
            markdown = Path(paths["trust_report_md"]).read_text(encoding="utf-8")

        self.assertEqual(payload["overall_verdict"], "continue")
        self.assertIn("agent_claims", payload)
        self.assertIn("scope", payload)
        self.assertIn("Update review action persistence", payload["scope"]["approved_task"])
        self.assertIn("# Spool Trust Report", markdown)
        for heading in (
            "## Overall Verdict",
            "## Scope / Authorization",
            "## Agent Claims",
            "## Weak or Missing Evidence",
            "## Buried Decisions",
            "## Repo Memory Check",
            "## Risks",
            "## Reviewer Actions",
        ):
            self.assertIn(heading, markdown)

    def test_detects_buried_decision_as_candidate_repo_memory(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "buried_decision" / "diff.patch",
            task_path=EXAMPLES / "buried_decision" / "task.md",
            agent_summary_path=EXAMPLES / "buried_decision" / "agent_summary.md",
            test_output_path=EXAMPLES / "buried_decision" / "test_output.txt",
        )

        report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "human_review")
        self.assertTrue(
            any("qwen/glm/deepseek" in decision.decision for decision in report.buried_decisions)
        )
        self.assertTrue(all("README.md was updated" not in decision.decision for decision in report.buried_decisions))

    def test_repo_memory_conflict_stops_report(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "memory_conflict" / "diff.patch",
            task_path=EXAMPLES / "memory_conflict" / "task.md",
            agent_summary_path=EXAMPLES / "memory_conflict" / "agent_summary.md",
            test_output_path=EXAMPLES / "memory_conflict" / "test_output.txt",
            repo_memory_path=EXAMPLES / "memory_conflict" / "approved_repo_memory.json",
        )

        report = analyze_pr_trust(evidence)
        overwrite_claims = [claim for claim in report.claims if "overwrite" in claim.claim.lower()]

        self.assertEqual(report.overall_verdict, "stop")
        self.assertTrue(overwrite_claims)
        self.assertEqual(overwrite_claims[0].claim_type, "capability_claim")
        self.assertEqual(overwrite_claims[0].verdict, "supported")
        self.assertTrue(any(item.verdict == "contradicted" for item in report.repo_memory_check))
        self.assertTrue(any("append-only" in item.memory for item in report.repo_memory_check))

    def test_cli_runs_successfully_on_basic_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    "spool_trust.py",
                    "--diff",
                    str(EXAMPLES / "basic_supported" / "diff.patch"),
                    "--task",
                    str(EXAMPLES / "basic_supported" / "task.md"),
                    "--agent-summary",
                    str(EXAMPLES / "basic_supported" / "agent_summary.md"),
                    "--test-output",
                    str(EXAMPLES / "basic_supported" / "test_output.txt"),
                    "--repo-memory",
                    ".spool/approved_repo_memory.json",
                    "--out",
                    temp_dir,
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Overall verdict: continue", result.stdout)
            self.assertTrue((Path(temp_dir) / "trust_report.json").exists())
            self.assertTrue((Path(temp_dir) / "trust_report.md").exists())


if __name__ == "__main__":
    unittest.main()
