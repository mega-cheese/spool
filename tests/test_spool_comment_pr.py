from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src.spool.merge_authorization.pr_comment import MARKER, PRCommentError, load_report, main, render_pr_comment


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
        comment = render_pr_comment(
            self._report_payload(
                summary={
                    "allowed_scope_status": "within_allowed_scope",
                    "supported_claims_count": 1,
                    "unsupported_claims_count": 0,
                    "test_evidence_status": "supported",
                    "decision_memory_status": "not_configured",
                },
                claim_diagnostics=[
                    {
                        "claim": "Updated `src/review_actions.py`.",
                        "source": "agent_summary",
                        "status": "supported",
                        "reason": "src/review_actions.py appears in the PR diff.",
                        "evidence_strength": "direct",
                        "supporting_evidence": [
                            {
                                "type": "changed_file",
                                "path": "src/review_actions.py",
                                "explanation": "The claimed path appears in the PR diff.",
                            }
                        ],
                        "missing_evidence": [],
                        "evidence_checked": ["agent_summary.md", "diff.patch"],
                        "reviewer_action": "No claim-specific action required.",
                    }
                ],
            )
        )

        self.assertIn("## Spool Merge Authorization", comment)
        self.assertIn("**Verdict:** Ready to continue (`continue`)", comment)
        self.assertIn("**Scope:** within_allowed_scope", comment)
        self.assertIn("**Agent claims:** 1 supported, 0 unassessed, 0 unsupported, 0 contradicted", comment)
        self.assertIn("**Test evidence:** PR body tests available; GitHub checks unavailable", comment)
        self.assertIn("**Decision memory:** not_configured", comment)
        self.assertIn("### Claim summary", comment)
        self.assertIn("- Updated `src/review_actions.py`. (`supported`, direct) - src/review_actions.py appears in the PR diff.", comment)
        self.assertIn("Evidence: `src/review_actions.py`.", comment)
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
            claim_diagnostics=[
                {
                    "claim": "Tests passed.",
                    "source": "agent_summary",
                    "status": "missing_evidence",
                    "reason": "The agent says tests passed, but no test output was provided.",
                    "evidence_strength": "none",
                    "supporting_evidence": [],
                    "missing_evidence": ["Test output was not provided."],
                    "evidence_checked": ["agent_summary.md", "checks.json", "test_output.txt"],
                    "reviewer_action": "Ask the agent to provide the missing evidence or revise this claim.",
                }
            ],
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
        self.assertIn("### Claim summary", comment)
        self.assertIn("- Tests passed. (`missing_evidence`, none) - The agent says tests passed, but no test output was provided.", comment)
        self.assertIn("Action: Ask the agent to provide the missing evidence or revise this claim.", comment)

    def test_contradicted_claim_is_counted_and_suppresses_no_unsupported_message(self) -> None:
        payload = self._report_payload(
            overall_verdict="stop",
            summary={
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 4,
                "unassessed_claims_count": 5,
                "unsupported_claims_count": 0,
                "contradicted_claims_count": 1,
                "test_evidence_status": "contradicted",
                "decision_memory_status": "not_configured",
            },
            claim_diagnostics=[
                {
                    "claim": "Full suite: 403 tests passed.",
                    "source": "agent_summary",
                    "status": "contradicted",
                    "claim_type": "test_result_claim",
                    "claim_role": "test_result_claim",
                    "verification_state": "assessed",
                    "affects_verdict": True,
                    "reason": "The PR body says 403 tests passed, but captured output reports 2 failures.",
                    "evidence_strength": "direct",
                    "supporting_evidence": [
                        {
                            "type": "test_output",
                            "path": "test_output.txt",
                            "quote": "FAILED (failures=2)",
                            "explanation": "Captured test output reports 2 failures.",
                        }
                    ],
                    "missing_evidence": [],
                    "reviewer_action": "Stop: “Full suite: 403 tests passed.” conflicts with captured output reporting 2 failures.",
                }
            ],
            reviewer_actions=["Stop: “Full suite: 403 tests passed.” conflicts with captured output reporting 2 failures."],
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Agent claims:** 4 supported, 5 unassessed, 0 unsupported, 1 contradicted", comment)
        self.assertIn("One agent test-result claim conflicts with the available evidence.", comment)
        self.assertNotIn("No unsupported agent claims were found.", comment)
        self.assertIn("Stop: “Full suite: 403 tests passed.” conflicts with captured output reporting 2 failures.", comment)

    def test_renders_unsupported_claim_details_in_comment(self) -> None:
        payload = self._report_payload(
            overall_verdict="repair",
            summary={
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 1,
                "unsupported_claims_count": 2,
                "test_evidence_status": "supported",
                "decision_memory_status": "not_configured",
            },
            claim_diagnostics=[
                {
                    "claim": "Added `src/missing_a.py`.",
                    "source": "agent_summary",
                    "status": "unsupported",
                    "reason": "src/missing_a.py does not appear in the PR diff.",
                    "evidence_strength": "none",
                    "supporting_evidence": [],
                    "missing_evidence": ["No changed file matching `src/missing_a.py` was found in the PR diff."],
                    "evidence_checked": ["agent_summary.md", "diff.patch"],
                    "reviewer_action": "Ask the agent to point to the supporting diff or revise/remove this claim.",
                },
                {
                    "claim": "Added `src/missing_b.py`.",
                    "source": "agent_summary",
                    "status": "unsupported",
                    "reason": "src/missing_b.py does not appear in the PR diff.",
                    "evidence_strength": "none",
                    "supporting_evidence": [],
                    "missing_evidence": ["No changed file matching `src/missing_b.py` was found in the PR diff."],
                    "evidence_checked": ["agent_summary.md", "diff.patch"],
                    "reviewer_action": "Ask the agent to point to the supporting diff or revise/remove this claim.",
                },
            ],
            reviewer_actions=["request repair"],
        )

        comment = render_pr_comment(payload)

        self.assertIn("**Agent claims:** 1 supported, 0 unassessed, 2 unsupported, 0 contradicted", comment)
        self.assertIn("### Claim summary", comment)
        self.assertIn("- Added `src/missing_a.py`. (`unsupported`, none) - src/missing_a.py does not appear in the PR diff.", comment)
        self.assertIn("Action: Ask the agent to point to the supporting diff or revise/remove this claim.", comment)
        self.assertIn("- Added `src/missing_b.py`. (`unsupported`, none) - src/missing_b.py does not appear in the PR diff.", comment)

    def test_renders_unsupported_claim_details_with_cap(self) -> None:
        diagnostics = [
            {
                "claim": f"Added `src/missing_{index}.py`.",
                "source": "agent_summary",
                "status": "unsupported",
                "reason": f"src/missing_{index}.py does not appear in the PR diff.",
                "evidence_strength": "none",
                "supporting_evidence": [],
                "missing_evidence": [f"No changed file matching `src/missing_{index}.py` was found in the PR diff."],
                "evidence_checked": ["agent_summary.md", "diff.patch"],
                "reviewer_action": "Ask the agent to point to the supporting diff or revise/remove this claim.",
            }
            for index in range(6)
        ]
        payload = self._report_payload(
            overall_verdict="repair",
            summary={
                "allowed_scope_status": "within_allowed_scope",
                "supported_claims_count": 0,
                "unsupported_claims_count": 6,
                "test_evidence_status": "supported",
                "decision_memory_status": "not_configured",
            },
            claim_diagnostics=diagnostics,
            reviewer_actions=["request repair"],
        )

        comment = render_pr_comment(payload)

        self.assertIn("Added `src/missing_0.py`.", comment)
        self.assertIn("Added `src/missing_4.py`.", comment)
        self.assertNotIn("Added `src/missing_5.py`.", comment)
        self.assertIn("1 more claim in the full claim ledger in `trust_report.md` / `trust_report.json`.", comment)

    def test_claim_summary_caps_at_five_and_prioritizes_unsupported_and_weak(self) -> None:
        diagnostics = [
            {
                "claim": "Supported claim 1.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "src/a.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            },
            {
                "claim": "Unsupported claim.",
                "source": "agent_summary",
                "status": "unsupported",
                "reason": "No supporting evidence found.",
                "evidence_strength": "none",
                "supporting_evidence": [],
                "missing_evidence": ["No matching path was found."],
                "reviewer_action": "Ask the agent to revise/remove this claim.",
            },
            {
                "claim": "Weak claim.",
                "source": "agent_summary",
                "status": "needs_review",
                "reason": "Only heuristic evidence was found.",
                "evidence_strength": "weak",
                "supporting_evidence": [{"type": "diff_pattern", "path": "src/b.py", "explanation": "Related pattern appears."}],
                "missing_evidence": ["No exact behavior evidence was found."],
                "reviewer_action": "Review this claim against the PR evidence before merging.",
            },
            {
                "claim": "Supported claim 2.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "src/c.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            },
            {
                "claim": "Supported claim 3.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "src/d.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            },
            {
                "claim": "Supported claim 4.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "src/e.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            },
        ]
        comment = render_pr_comment(self._report_payload(claim_diagnostics=diagnostics))

        self.assertLess(comment.index("Unsupported claim."), comment.index("Weak claim."))
        self.assertLess(comment.index("Weak claim."), comment.index("Supported claim 1."))
        self.assertIn("Supported claim 3.", comment)
        self.assertNotIn("Supported claim 4.", comment)
        self.assertIn("1 more claim in the full claim ledger in `trust_report.md` / `trust_report.json`.", comment)

    def test_claim_summary_caps_after_severity_order_and_truncates_long_text(self) -> None:
        long_claim = "Supported claim " + ("with many details " * 20)
        long_reason = "Direct path evidence exists " + ("and remains readable " * 20)
        diagnostics = [
            {
                "claim": f"Supported claim {index}.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": f"src/{index}.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            }
            for index in range(5)
        ]
        diagnostics.insert(
            0,
            {
                "claim": long_claim,
                "source": "agent_summary",
                "status": "contradicted",
                "reason": long_reason,
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "test_output", "path": "test_output.txt", "quote": "FAILED", "explanation": "Captured test output contradicts the claim."}],
                "missing_evidence": [],
                "reviewer_action": "Review this claim against the PR evidence.",
            }
        )
        diagnostics.append(
            {
                "claim": "Supported claim 4.",
                "source": "agent_summary",
                "status": "supported",
                "reason": "Direct path evidence exists.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "src/4.py", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            }
        )

        comment = render_pr_comment(self._report_payload(claim_diagnostics=diagnostics))

        self.assertLess(comment.index("Supported claim with many details"), comment.index("Supported claim 0."))
        self.assertIn("Supported claim 3.", comment)
        self.assertNotIn("Supported claim 4.", comment)
        self.assertNotIn(long_claim, comment)
        self.assertIn("...", comment)
        self.assertIn("Evidence: `test_output.txt`.", comment)
        self.assertNotIn("Action: No claim-specific action required", comment)

    def test_supported_inventory_does_not_consume_substantive_claim_slots(self) -> None:
        inventory = [
            {
                "claim": f"src/file_{index}.py.",
                "source": "agent_summary",
                "source_section": "Files changed",
                "source_index": index,
                "status": "supported",
                "claim_type": "file_modified",
                "claim_role": "file_inventory",
                "verification_state": "assessed",
                "affects_verdict": False,
                "reason": f"src/file_{index}.py appears in the PR diff.",
                "evidence_strength": "direct",
                "supporting_evidence": [
                    {"type": "changed_file", "path": f"src/file_{index}.py", "explanation": "Path appears."}
                ],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            }
            for index in range(6)
        ]
        substantive = [
            {
                "claim": f"Substantive claim {index}.",
                "source": "agent_summary",
                "source_section": "Key fixes",
                "source_index": index + 6,
                "status": "not_applicable",
                "claim_type": "implementation_claim",
                "claim_role": "implementation_claim",
                "verification_state": "unassessed",
                "affects_verdict": False,
                "reason": "Spool retained this substantive claim but has no deterministic verifier for this claim form.",
                "evidence_strength": "none",
                "supporting_evidence": [],
                "missing_evidence": [],
                "reviewer_action": "Review the relevant diff and tests manually.",
            }
            for index in range(6)
        ]

        comment = render_pr_comment(self._report_payload(claim_diagnostics=inventory + substantive))

        self.assertIn("**Changed-file inventory:** 6 claimed files, 6 present in the diff.", comment)
        self.assertIn("Substantive claim 0.", comment)
        self.assertIn("Substantive claim 4.", comment)
        self.assertLess(comment.index("Substantive claim 0."), comment.index("**Changed-file inventory:**"))
        self.assertNotIn("Substantive claim 5.", comment)
        self.assertNotIn("src/file_0.py.", comment)
        self.assertIn("(`unassessed`, none)", comment)
        self.assertIn("1 more claim", comment)

    def test_unsupported_inventory_is_prominent_but_supported_inventory_is_compact(self) -> None:
        diagnostics = [
            {
                "claim": "README.md.",
                "source": "agent_summary",
                "source_section": "Files changed",
                "source_index": 0,
                "status": "supported",
                "claim_type": "file_modified",
                "claim_role": "file_inventory",
                "verification_state": "assessed",
                "affects_verdict": False,
                "reason": "README.md appears in the PR diff.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "README.md", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            },
            {
                "claim": "src/missing.py.",
                "source": "agent_summary",
                "source_section": "Files changed",
                "source_index": 1,
                "status": "unsupported",
                "claim_type": "file_modified",
                "claim_role": "file_inventory",
                "verification_state": "assessed",
                "affects_verdict": False,
                "reason": "src/missing.py does not appear in the PR diff.",
                "evidence_strength": "none",
                "supporting_evidence": [],
                "missing_evidence": ["No changed file matching `src/missing.py` was found in the PR diff."],
                "reviewer_action": "Ask the agent to point to the supporting diff or revise/remove this claim.",
            },
            {
                "claim": "Verdict policy remains unchanged.",
                "source": "agent_summary",
                "source_section": "Key fixes",
                "source_index": 2,
                "status": "not_applicable",
                "claim_type": "invariance_claim",
                "claim_role": "invariance_claim",
                "verification_state": "unassessed",
                "affects_verdict": False,
                "reason": "Spool retained this substantive claim but has no deterministic verifier for this claim form.",
                "evidence_strength": "none",
                "supporting_evidence": [],
                "missing_evidence": [],
                "reviewer_action": "Review the relevant diff and tests manually.",
            },
        ]

        comment = render_pr_comment(self._report_payload(claim_diagnostics=diagnostics))

        self.assertIn("**Changed-file inventory:** 2 claimed files, 1 present in the diff.", comment)
        self.assertIn("src/missing.py.", comment)
        self.assertIn("Verdict policy remains unchanged.", comment)
        self.assertLess(comment.index("src/missing.py."), comment.index("Verdict policy remains unchanged."))
        self.assertNotIn("- README.md.", comment)

    def test_only_supported_file_claims_render_as_concise_inventory_summary(self) -> None:
        diagnostics = [
            {
                "claim": "README.md.",
                "source": "agent_summary",
                "source_section": "Files changed",
                "source_index": 0,
                "status": "supported",
                "claim_type": "file_modified",
                "claim_role": "file_inventory",
                "verification_state": "assessed",
                "affects_verdict": False,
                "reason": "README.md appears in the PR diff.",
                "evidence_strength": "direct",
                "supporting_evidence": [{"type": "changed_file", "path": "README.md", "explanation": "Path appears."}],
                "missing_evidence": [],
                "reviewer_action": "No claim-specific action required.",
            }
        ]

        comment = render_pr_comment(self._report_payload(claim_diagnostics=diagnostics))

        self.assertIn("**Changed-file inventory:** 1 claimed file, 1 present in the diff.", comment)
        self.assertNotIn("### Claim summary", comment)
        self.assertNotIn("- README.md.", comment)

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
        self.assertIn("**Agent claims:** 0 supported, 0 unassessed, 0 unsupported, 0 contradicted", comment)
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

        def _run(command, **kwargs):
            if command[:2] == ["gh", "api"] and "-X" not in command:
                return subprocess.CompletedProcess(args=command, returncode=0, stdout="[]", stderr="")
            raise error

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stderr = io.StringIO()
            with mock.patch("subprocess.run", side_effect=_run), redirect_stderr(stderr):
                exit_code = main(["1", "--report", str(report_path), "--post"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not post PR comment", stderr.getvalue())

    def test_post_surfaces_lookup_failure_clearly(self) -> None:
        error = subprocess.CalledProcessError(returncode=1, cmd=["gh", "api"], stderr="not authenticated")
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stderr = io.StringIO()
            with mock.patch("subprocess.run", side_effect=error), redirect_stderr(stderr):
                exit_code = main(["1", "--report", str(report_path), "--post"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not list PR comments", stderr.getvalue())

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

    def test_rendered_comment_starts_with_hidden_marker(self) -> None:
        comment = render_pr_comment(self._report_payload())

        self.assertTrue(comment.startswith(MARKER))

    def _gh_side_effect(self, list_stdout: str):
        def _run(command, **kwargs):
            if command[:2] == ["gh", "api"] and "-X" not in command:
                return subprocess.CompletedProcess(args=command, returncode=0, stdout=list_stdout, stderr="")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        return _run

    def test_post_updates_existing_marker_comment_instead_of_creating(self) -> None:
        existing_comments = json.dumps(
            [
                {
                    "id": 555,
                    "issue_url": "https://api.github.com/repos/acme/widgets/issues/1",
                    "body": f"{MARKER}\nstale report",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            mocked_run = mock.Mock(side_effect=self._gh_side_effect(existing_comments))
            with mock.patch("subprocess.run", mocked_run):
                exit_code = main(["1", "--report", str(report_path), "--out", str(out_path), "--post"])

        self.assertEqual(exit_code, 0)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        patch_commands = [cmd for cmd in commands if "-X" in cmd and "PATCH" in cmd]
        create_commands = [cmd for cmd in commands if cmd[:3] == ["gh", "pr", "comment"]]
        self.assertEqual(len(patch_commands), 1)
        self.assertIn("repos/{owner}/{repo}/issues/comments/555", patch_commands[0])
        self.assertEqual(create_commands, [])

    def test_post_update_uses_field_form_for_body_file_contents(self) -> None:
        existing_comments = json.dumps(
            [
                {
                    "id": 555,
                    "issue_url": "https://api.github.com/repos/acme/widgets/issues/1",
                    "body": f"{MARKER}\nstale report",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            mocked_run = mock.Mock(side_effect=self._gh_side_effect(existing_comments))
            with mock.patch("subprocess.run", mocked_run):
                exit_code = main(["1", "--report", str(report_path), "--out", str(out_path), "--post"])

        self.assertEqual(exit_code, 0)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        patch_command = next(cmd for cmd in commands if "-X" in cmd and "PATCH" in cmd)
        self.assertIn("-F", patch_command)
        self.assertIn(f"body=@{out_path}", patch_command)
        self.assertNotIn("-f", patch_command)

    def test_post_falls_back_to_create_when_no_marker_comment_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            mocked_run = mock.Mock(side_effect=self._gh_side_effect(json.dumps([])))
            with mock.patch("subprocess.run", mocked_run):
                exit_code = main(["1", "--report", str(report_path), "--out", str(out_path), "--post"])

        self.assertEqual(exit_code, 0)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        patch_commands = [cmd for cmd in commands if "-X" in cmd and "PATCH" in cmd]
        create_commands = [cmd for cmd in commands if cmd[:3] == ["gh", "pr", "comment"]]
        self.assertEqual(patch_commands, [])
        self.assertEqual(len(create_commands), 1)

    def test_force_new_always_creates_even_when_marker_comment_exists(self) -> None:
        existing_comments = json.dumps(
            [
                {
                    "id": 555,
                    "issue_url": "https://api.github.com/repos/acme/widgets/issues/1",
                    "body": f"{MARKER}\nstale report",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = self._write_report(root, self._report_payload())
            out_path = root / "spool_comment.md"

            mocked_run = mock.Mock(side_effect=self._gh_side_effect(existing_comments))
            with mock.patch("subprocess.run", mocked_run):
                exit_code = main(
                    ["1", "--report", str(report_path), "--out", str(out_path), "--post", "--force-new"]
                )

        self.assertEqual(exit_code, 0)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        list_commands = [cmd for cmd in commands if cmd[:2] == ["gh", "api"] and "-X" not in cmd]
        patch_commands = [cmd for cmd in commands if "-X" in cmd and "PATCH" in cmd]
        create_commands = [cmd for cmd in commands if cmd[:3] == ["gh", "pr", "comment"]]
        self.assertEqual(list_commands, [])
        self.assertEqual(patch_commands, [])
        self.assertEqual(len(create_commands), 1)

    def test_dry_run_never_invokes_gh_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stdout = io.StringIO()
            with mock.patch("subprocess.run") as mocked_run, redirect_stdout(stdout):
                exit_code = main(["1", "--report", str(report_path), "--dry-run"])

        self.assertEqual(exit_code, 0)
        mocked_run.assert_not_called()
        self.assertIn(MARKER, stdout.getvalue())
        self.assertIn("dry_run:", stdout.getvalue())

    def test_dry_run_with_force_new_never_invokes_gh_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(Path(temp_dir), self._report_payload())
            stdout = io.StringIO()
            with mock.patch("subprocess.run") as mocked_run, redirect_stdout(stdout):
                exit_code = main(["1", "--report", str(report_path), "--dry-run", "--force-new"])

        self.assertEqual(exit_code, 0)
        mocked_run.assert_not_called()
        self.assertIn("--force-new", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
