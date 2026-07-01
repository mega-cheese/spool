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
    def _evidence_from_text(
        self,
        temp_dir: str,
        *,
        diff: str,
        task: str,
        summary: str,
        test_output: str | None = None,
        repo_memory: dict | list | None = None,
    ):
        root = Path(temp_dir)
        diff_path = root / "diff.patch"
        task_path = root / "task.md"
        summary_path = root / "agent_summary.md"
        diff_path.write_text(diff, encoding="utf-8")
        task_path.write_text(task, encoding="utf-8")
        summary_path.write_text(summary, encoding="utf-8")
        test_path = None
        if test_output is not None:
            test_path = root / "test_output.txt"
            test_path.write_text(test_output, encoding="utf-8")
        memory_path = None
        if repo_memory is not None:
            memory_path = root / "approved_repo_memory.json"
            memory_path.write_text(json.dumps(repo_memory), encoding="utf-8")
        return load_pr_evidence(
            diff_path=diff_path,
            task_path=task_path,
            agent_summary_path=summary_path,
            test_output_path=test_path,
            repo_memory_path=memory_path,
        )

    def _simple_diff(self, path: str, added_lines: list[str]) -> str:
        body = "\n".join(f"+{line}" for line in added_lines)
        return (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "--- /dev/null\n"
            f"+++ b/{path}\n"
            "@@ -0,0 +1,3 @@\n"
            f"{body}\n"
        )

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
        self.assertEqual(report.overall_verdict, "stop")
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

    def test_repo_memory_append_only_contradiction_stops_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["path.write_text(json.dumps([action]))"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Review actions now overwrite existing action history.",
                test_output="OK\n",
                repo_memory={"approved_memory": [{"title": "Review actions are append-only.", "summary": "Review actions must preserve history."}]},
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "stop")
        self.assertTrue(any(item.verdict == "contradicted" for item in report.repo_memory_check))

    def test_repo_memory_candidate_auto_approval_contradiction_stops_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/memory_store.py", ["if memory.confidence > 0.9: memory.status = 'approved'"]),
                task="Allowed files:\n- `src/memory_store.py`\n",
                summary="Candidate memory is now automatically approved when confidence is high.",
                test_output="OK\n",
                repo_memory={
                    "approved_memory": [
                        {
                            "title": "Candidate memory must not auto-approve.",
                            "summary": "Candidate memory must remain pending until explicit review approval.",
                        }
                    ]
                },
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "stop")
        self.assertTrue(any(item.verdict == "contradicted" for item in report.repo_memory_check))

    def test_repo_memory_keyword_overlap_needs_review_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_queue.py", ["def review_candidate_memory(memory): return memory"]),
                task="Allowed files:\n- `src/review_queue.py`\n",
                summary="Updated candidate memory review handling.",
                test_output="OK\n",
                repo_memory={"approved_memory": [{"title": "Candidate memory requires review.", "summary": "Candidate memory should be reviewed before approval."}]},
            )

            report = analyze_pr_trust(evidence)

        verdicts = [item.verdict for item in report.repo_memory_check]
        self.assertIn("needs_review", verdicts)
        self.assertNotIn("supported", verdicts)

    def test_behavior_claim_persist_without_replacing_is_supported_by_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["def append_review_action(path, action):", "    actions.append(action)"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Review actions persist in `review_actions.json` without replacing previous actions.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        behavior_claims = [claim for claim in report.claims if claim.claim_type == "capability_claim"]
        self.assertTrue(behavior_claims)
        self.assertEqual(behavior_claims[0].verdict, "supported")

    def test_behavior_claim_preserves_source_evidence_needs_review_without_source_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["def edit_memory(memory): return memory"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Editing memory preserves source evidence in `review_actions.json`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        behavior_claims = [claim for claim in report.claims if claim.claim_type == "capability_claim"]
        self.assertTrue(behavior_claims)
        self.assertIn(behavior_claims[0].verdict, {"weakly_supported", "needs_review"})
        self.assertIn("No deterministic behavior matcher verified this claim", behavior_claims[0].reason)

    def test_behavior_claim_source_evidence_retained_is_supported_by_source_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["new_memory['quote'] = old_memory['quote']", "new_memory['source_event_ids'] = old_memory['source_event_ids']"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Source evidence is retained in `review_actions.json`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        behavior_claims = [claim for claim in report.claims if claim.claim_type == "capability_claim"]
        self.assertTrue(behavior_claims)
        self.assertEqual(behavior_claims[0].verdict, "supported")

    def test_buried_decision_ignores_ordinary_should_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("tests/test_parser.py", ["def test_parser(): assert parse('') is None"]),
                task="Allowed files:\n- `tests/test_parser.py`\n",
                summary="Tests should pass.\nThe function should handle empty input.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.buried_decisions, [])

    def test_buried_decision_detects_durable_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/memory_store.py", ["def approve(memory): return memory"]),
                task="Allowed files:\n- `src/memory_store.py`\n",
                summary="Candidate memory must not become approved automatically.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        self.assertTrue(any("Candidate memory" in decision.decision for decision in report.buried_decisions))
        self.assertEqual(report.overall_verdict, "human_review")

    def test_ambiguous_scope_language_creates_scope_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/upload_gate.py", ["def gate(): return True"]) + self._simple_diff("src/other.py", ["value = 1"]),
                task="Only change the upload gating logic.",
                summary="Updated upload gating.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "human_review")
        self.assertTrue(any("Could not determine approved file scope" in risk for risk in report.risks))

    def test_sensitive_scope_violation_stops_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/auth.py", ["def allow(): return True"]),
                task="Allowed files:\n- `src/ui.ts`\n",
                summary="Updated `src/auth.py`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "stop")
        self.assertIn("src/auth.py", report.scope_report.scope_violations)

    def test_failed_test_output_contradicts_test_pass_claim_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["value = 1"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Tests passed.",
                test_output="FAILED (failures=1)\n",
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "stop")
        self.assertTrue(any(claim.verdict == "contradicted" for claim in report.claims))

    def test_missing_test_output_claim_requires_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["value = 1"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Tests passed.",
            )

            report = analyze_pr_trust(evidence)

        self.assertEqual(report.overall_verdict, "human_review")

    def test_cli_llm_mode_errors_clearly(self) -> None:
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
                    "--out",
                    temp_dir,
                    "--mode",
                    "llm",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("heuristic", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
