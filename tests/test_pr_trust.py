from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.spool.merge_authorization.merge_authorization import analyze_pr_trust
from src.spool.merge_authorization.merge_authorization_report import write_trust_report
from src.spool.merge_authorization.pr_context import load_pr_evidence
from src.spool.merge_authorization.scope_gate import evaluate_scope, parse_changed_files


EXAMPLES = Path("examples/pr_trust")
ADVERSARIAL_EXAMPLES = Path("examples/pr_trust_adversarial")


class PRTrustTest(unittest.TestCase):
    def _evidence_from_text(
        self,
        temp_dir: str,
        *,
        diff: str,
        task: str | None,
        summary: str,
        test_output: str | None = None,
        repo_memory: dict | list | None = None,
    ):
        root = Path(temp_dir)
        diff_path = root / "diff.patch"
        summary_path = root / "agent_summary.md"
        diff_path.write_text(diff, encoding="utf-8")
        task_path = None
        if task is not None:
            task_path = root / "task.md"
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

    def test_parses_allowed_files_areas_heading_with_files_and_directories(self) -> None:
        task = """
Make local PR-body/diff-only mode work naturally.

Allowed files/areas:
- AGENTS.md
- spool_trust.py
- src/spool/merge_authorization/
- tests/test_pr_trust.py
- scripts/run_pr_trust_adversarial.py
- examples/pr_trust_adversarial/
""".lstrip()
        changes = [
            *parse_changed_files(self._simple_diff("AGENTS.md", ["scope guidance"])),
            *parse_changed_files(self._simple_diff("spool_trust.py", ["parser.add_argument('--task')"])),
            *parse_changed_files(self._simple_diff("src/spool/merge_authorization/scope_gate.py", ["value = 1"])),
            *parse_changed_files(self._simple_diff("examples/pr_trust_adversarial/new/diff.patch", ["diff text"])),
        ]

        scope = evaluate_scope(task, changes)

        self.assertEqual(
            scope.allowed_files,
            [
                "AGENTS.md",
                "spool_trust.py",
                "src/spool/merge_authorization/",
                "tests/test_pr_trust.py",
                "scripts/run_pr_trust_adversarial.py",
                "examples/pr_trust_adversarial/",
            ],
        )
        self.assertEqual(scope.scope_violations, [])
        self.assertEqual(scope.scope_risks, [])

    def test_parses_allowed_directories_heading_and_numbered_plain_bullets(self) -> None:
        task = """
Allowed directories:
1. src/spool/merge_authorization/
* examples/pr_trust_adversarial/
tests/test_pr_trust.py
""".lstrip()
        changes = [
            *parse_changed_files(self._simple_diff("src/spool/merge_authorization/pr_context.py", ["value = 1"])),
            *parse_changed_files(self._simple_diff("examples/pr_trust_adversarial/x/task.md", ["Task"])),
            *parse_changed_files(self._simple_diff("tests/test_pr_trust.py", ["assert True"])),
        ]

        scope = evaluate_scope(task, changes)

        self.assertEqual(
            scope.allowed_files,
            ["src/spool/merge_authorization/", "examples/pr_trust_adversarial/", "tests/test_pr_trust.py"],
        )
        self.assertEqual(scope.scope_violations, [])

    def test_changed_file_outside_allowed_area_is_scope_violation(self) -> None:
        task = """
Allowed scope:
- src/spool/merge_authorization/
- tests/test_pr_trust.py
""".lstrip()
        changes = [
            *parse_changed_files(self._simple_diff("src/spool/merge_authorization/scope_gate.py", ["value = 1"])),
            *parse_changed_files(self._simple_diff("src/legacy_memory.py", ["value = 1"])),
        ]

        scope = evaluate_scope(task, changes)

        self.assertEqual(scope.scope_violations, ["src/legacy_memory.py"])
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=(
                    self._simple_diff("src/spool/merge_authorization/scope_gate.py", ["value = 1"])
                    + self._simple_diff("src/legacy_memory.py", ["value = 1"])
                ),
                task=task,
                summary="Updated scope parsing.",
                test_output="OK\n",
            )
            report = analyze_pr_trust(evidence)

        self.assertEqual(report.to_dict()["summary"]["allowed_scope_status"], "outside_allowed_scope")

    def test_dogfood_allowed_areas_report_is_within_allowed_scope(self) -> None:
        task = """
Make local PR-body/diff-only mode work naturally.

Allowed files/areas:
- AGENTS.md
- spool_trust.py
- src/spool/merge_authorization/
- tests/test_pr_trust.py
- scripts/run_pr_trust_adversarial.py
- examples/pr_trust_adversarial/

Do not add GitHub integration, dashboard, Streamlit, Slack/docs ingestion, meeting ingestion, broad LLM mode, compliance exports, or web server.
""".lstrip()
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=(
                    self._simple_diff("spool_trust.py", ["parser.add_argument('--task')"])
                    + self._simple_diff("src/spool/merge_authorization/scope_gate.py", ["value = 1"])
                    + self._simple_diff("examples/pr_trust_adversarial/allowed_areas_scope_parsing/task.md", ["Allowed files/areas:"])
                ),
                task=task,
                summary="Updated scope parsing for local assignment text.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)
            paths = write_trust_report(report, temp_dir, pretty=True)
            payload = json.loads(Path(paths["trust_report_json"]).read_text(encoding="utf-8"))
            markdown = Path(paths["trust_report_md"]).read_text(encoding="utf-8")

        self.assertEqual(payload["summary"]["allowed_scope_status"], "within_allowed_scope")
        self.assertEqual(payload["scope"]["scope_violations"], [])
        self.assertFalse(any("Could not determine approved file scope" in risk for risk in payload["risks"]))
        self.assertEqual(report.overall_verdict, "continue")
        self.assertIn("Allowed files/areas:", markdown)
        self.assertIn("- src/spool/merge_authorization/", markdown)

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
        self.assertEqual(payload["summary"]["unsupported_claims_count"], 0)
        self.assertEqual(payload["scope"]["scope_violations"], [])
        self.assertIn("Update review action persistence", payload["scope"]["approved_task"])
        self.assertIn("# Spool Merge Authorization Report", markdown)
        for heading in (
            "## Overall Verdict",
            "## Scope / Authorization",
            "## Evidence Coverage",
            "## Agent Claims",
            "## Weak or Missing Evidence",
            "## Candidate Decisions Requiring Approval",
            "## Repo Memory Check",
            "## Risks",
            "## Reviewer Actions",
        ):
            self.assertIn(heading, markdown)
        self.assertNotIn("## Buried Decisions", markdown)

    def test_local_file_mode_pr_title_remains_not_supported(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "basic_supported" / "diff.patch",
            task_path=EXAMPLES / "basic_supported" / "task.md",
            agent_summary_path=EXAMPLES / "basic_supported" / "agent_summary.md",
            test_output_path=EXAMPLES / "basic_supported" / "test_output.txt",
        )

        payload = analyze_pr_trust(evidence).to_dict()

        self.assertEqual(payload["evidence_coverage"]["pr_title"], "not_supported")
        self.assertEqual(payload["summary"]["unsupported_claims_count"], 0)

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
        self.assertTrue(any("Candidate memory" in decision.title for decision in report.candidate_decisions))
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

    def test_adversarial_runner_uses_fixture_local_repo_memory(self) -> None:
        output_root = Path("outputs/trust_runs_adversarial")
        target_report = output_root / "memory_contradiction_append_only" / "trust_report.json"
        if target_report.exists():
            target_report.unlink()

        result = subprocess.run(
            [sys.executable, "scripts/run_pr_trust_adversarial.py"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("memory_contradiction_append_only", result.stdout)
        self.assertIn("stop", result.stdout.lower())
        self.assertIn("expected: stop", result.stdout.lower())
        self.assertTrue(target_report.exists())
        payload = json.loads(target_report.read_text(encoding="utf-8"))
        self.assertEqual(payload["overall_verdict"], "stop")
        self.assertEqual(payload["summary"]["repo_memory_status"], "contradicted")

    def test_approved_decisions_yaml_violation_stops_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_path = root / "approved_decisions.yml"
            memory_path.write_text(
                """
decisions:
  - id: DEC-001
    status: approved
    title: Review actions are append-only
    type: implementation_constraint
    must:
      - Review actions must be appended to review_actions.json.
    must_not:
      - Overwrite review_actions.json with only the latest action.
""".lstrip(),
                encoding="utf-8",
            )
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["path.write_text(json.dumps([action]))"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Review actions now overwrite existing action history.",
                test_output="OK\n",
            )
            evidence = load_pr_evidence(
                diff_path=evidence.diff_path,
                task_path=evidence.task_path,
                agent_summary_path=evidence.agent_summary_path,
                test_output_path=evidence.test_output_path,
                repo_memory_path=memory_path,
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(report.overall_verdict, "stop")
        self.assertEqual(payload["decision_memory"]["status"], "available")
        self.assertEqual(payload["decision_memory"]["approved_decisions_count"], 1)
        self.assertEqual(payload["decision_memory"]["conflicts"], ["DEC-001"])

    def test_cli_accepts_approved_decisions_yaml_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path("examples/pr_trust_adversarial/approved_decisions_violation")
            result = subprocess.run(
                [
                    sys.executable,
                    "spool_trust.py",
                    "--diff",
                    str(fixture / "diff.patch"),
                    "--task",
                    str(fixture / "task.md"),
                    "--agent-summary",
                    str(fixture / "agent_summary.md"),
                    "--test-output",
                    str(fixture / "test_output.txt"),
                    "--repo-memory",
                    str(fixture / "approved_decisions.yml"),
                    "--out",
                    temp_dir,
                    "--pretty",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            payload = json.loads((Path(temp_dir) / "trust_report.json").read_text(encoding="utf-8"))

        self.assertIn("Overall verdict: stop", result.stdout)
        self.assertEqual(payload["decision_memory"]["source"], str(fixture / "approved_decisions.yml"))
        self.assertEqual(payload["decision_memory"]["conflicts"], ["DEC-001"])

    def test_missing_decision_memory_report_metadata_is_not_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Updated `src/review_actions.py`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["decision_memory"]["status"], "not_configured")
        self.assertEqual(payload["decision_memory"]["approved_decisions_count"], 0)
        self.assertTrue(all(item.verdict != "unsupported" for item in report.repo_memory_check))

    def test_report_contains_evidence_coverage_and_candidate_decisions(self) -> None:
        evidence = load_pr_evidence(
            diff_path=EXAMPLES / "buried_decision" / "diff.patch",
            task_path=EXAMPLES / "buried_decision" / "task.md",
            agent_summary_path=EXAMPLES / "buried_decision" / "agent_summary.md",
            test_output_path=EXAMPLES / "buried_decision" / "test_output.txt",
        )

        report = analyze_pr_trust(evidence)
        payload = report.to_dict()

        self.assertIn("evidence_coverage", payload)
        self.assertEqual(payload["evidence_coverage"]["pr_body"], "available")
        self.assertEqual(payload["evidence_coverage"]["assignment"], "available")
        self.assertIn("candidate_decisions", payload)
        self.assertGreaterEqual(len(payload["candidate_decisions"]), 1)

    def test_empty_assignment_reports_partial_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task="",
                summary="Updated `src/review_actions.py`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["evidence_coverage"]["assignment"], "empty")
        self.assertTrue(any("partial" in note.lower() for note in payload["evidence_coverage"]["notes"]))

    def test_missing_test_output_coverage_matches_missing_evidence_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Tests passed.",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertTrue(any(claim.verdict == "missing_evidence" for claim in report.claims))
        self.assertEqual(report.overall_verdict, "human_review")

    def test_loads_pr_evidence_without_task_as_partial_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task=None,
                summary="Updated `src/review_actions.py`.",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertIsNone(evidence.task_path)
        self.assertEqual(payload["evidence_coverage"]["assignment"], "not_provided")
        self.assertEqual(payload["summary"]["allowed_scope_status"], "partial_assignment_review")
        self.assertEqual(payload["scope"]["approved_task"], "")
        self.assertEqual(payload["scope"]["allowed_files"], [])
        self.assertEqual(payload["scope"]["scope_violations"], [])
        self.assertTrue(any("partial" in note.lower() for note in payload["evidence_coverage"]["notes"]))
        self.assertNotEqual(report.overall_verdict, "stop")

    def test_empty_task_marks_assignment_empty_without_scope_certainty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task="",
                summary="Updated `src/review_actions.py`.",
                test_output="OK\n",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["evidence_coverage"]["assignment"], "empty")
        self.assertEqual(payload["summary"]["allowed_scope_status"], "partial_assignment_review")
        self.assertEqual(payload["scope"]["allowed_files"], [])
        self.assertEqual(payload["scope"]["scope_violations"], [])
        self.assertTrue(any("partial" in risk.lower() for risk in payload["risks"]))

    def test_no_test_output_without_test_claim_does_not_create_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task="Allowed files:\n- `src/review_actions.py`\n",
                summary="Updated `src/review_actions.py`.",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertFalse(any(claim.verdict == "missing_evidence" for claim in report.claims))
        self.assertFalse(any("Validation claims are missing" in risk for risk in report.risks))
        self.assertNotEqual(report.overall_verdict, "human_review")

    def test_cli_runs_without_task_or_test_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    "spool_trust.py",
                    "--diff",
                    str(EXAMPLES / "basic_supported" / "diff.patch"),
                    "--agent-summary",
                    str(EXAMPLES / "basic_supported" / "agent_summary.md"),
                    "--out",
                    temp_dir,
                    "--pretty",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            payload = json.loads((Path(temp_dir) / "trust_report.json").read_text(encoding="utf-8"))
            markdown = (Path(temp_dir) / "trust_report.md").read_text(encoding="utf-8")

        self.assertIn("Overall verdict:", result.stdout)
        self.assertEqual(payload["evidence_coverage"]["assignment"], "not_provided")
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "not_configured")
        self.assertIn("Assignment evidence was not provided", markdown)

    def test_cli_runs_without_test_output_when_task_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(
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
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            payload = json.loads((Path(temp_dir) / "trust_report.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["evidence_coverage"]["assignment"], "available")
        self.assertEqual(payload["evidence_coverage"]["test_output"], "not_provided")
        self.assertTrue(any(claim["verdict"] == "missing_evidence" for claim in payload["agent_claims"]))

    def test_no_memory_reports_not_configured_decision_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = self._evidence_from_text(
                temp_dir,
                diff=self._simple_diff("src/review_actions.py", ["actions.append(action)"]),
                task=None,
                summary="Updated `src/review_actions.py`.",
            )

            report = analyze_pr_trust(evidence)

        payload = report.to_dict()
        self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "not_configured")
        self.assertEqual(payload["evidence_coverage"]["legacy_repo_memory"], "not_configured")
        self.assertEqual(payload["decision_memory"]["status"], "not_configured")
        self.assertIn("No approved decision memory was configured", " ".join(payload["decision_memory"]["notes"]))

    def test_pr_body_diff_only_adversarial_fixtures(self) -> None:
        cases = {
            "pr_body_diff_only_clean": {
                "assignment": "not_provided",
                "test_output": "not_provided",
                "verdict": {"human_review", "continue"},
                "missing_test_claim": False,
            },
            "pr_body_diff_only_tests_claimed": {
                "assignment": "not_provided",
                "test_output": "not_provided",
                "verdict": {"human_review"},
                "missing_test_claim": True,
            },
            "empty_task_partial_review": {
                "assignment": "empty",
                "test_output": "not_provided",
                "verdict": {"human_review"},
                "missing_test_claim": False,
            },
            "no_test_claim_no_test_output": {
                "assignment": "available",
                "test_output": "not_provided",
                "verdict": {"continue"},
                "missing_test_claim": False,
            },
            "allowed_areas_scope_parsing": {
                "assignment": "available",
                "test_output": "available",
                "verdict": {"continue"},
                "missing_test_claim": False,
                "allowed_scope_status": "within_allowed_scope",
            },
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                fixture = ADVERSARIAL_EXAMPLES / name
                task_path = fixture / "task.md"
                test_output_path = fixture / "test_output.txt"
                evidence = load_pr_evidence(
                    diff_path=fixture / "diff.patch",
                    task_path=task_path if task_path.exists() else None,
                    agent_summary_path=fixture / "agent_summary.md",
                    test_output_path=test_output_path if test_output_path.exists() else None,
                )

                report = analyze_pr_trust(evidence)
                payload = report.to_dict()

                self.assertIn(report.overall_verdict, expected["verdict"])
                self.assertNotEqual(report.overall_verdict, "stop")
                self.assertEqual(payload["evidence_coverage"]["assignment"], expected["assignment"])
                self.assertEqual(payload["evidence_coverage"]["test_output"], expected["test_output"])
                self.assertEqual(payload["evidence_coverage"]["approved_decisions"], "not_configured")
                self.assertEqual(
                    any(claim.verdict == "missing_evidence" for claim in report.claims),
                    expected["missing_test_claim"],
                )
                if expected["assignment"] in {"not_provided", "empty"}:
                    self.assertEqual(payload["summary"]["allowed_scope_status"], "partial_assignment_review")
                    self.assertEqual(payload["scope"]["allowed_files"], [])
                if expected.get("allowed_scope_status"):
                    self.assertEqual(payload["summary"]["allowed_scope_status"], expected["allowed_scope_status"])
                    self.assertEqual(payload["scope"]["scope_violations"], [])
                    self.assertFalse(any("Could not determine approved file scope" in risk for risk in payload["risks"]))


if __name__ == "__main__":
    unittest.main()
