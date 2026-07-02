from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.spool.merge_authorization.memory_update_plan import build_memory_update_plan
from src.spool.merge_authorization.memory_update_writer import write_memory_update_plan
from src.spool.merge_authorization.merge_authorization import analyze_pr_trust
from src.spool.merge_authorization.merge_authorization_result import MergeAuthorizationResult
from src.spool.merge_authorization.pr_context import load_pr_evidence
from src.spool.merge_authorization.decision_memory import load_approved_decisions
from src.spool.merge_authorization.review_session import ReviewSession
from src.spool.merge_authorization.reviewer_actions import ReviewerAction


class ReviewSessionArchitectureTest(unittest.TestCase):
    def _report_result(self, *, summary: str) -> MergeAuthorizationResult:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            diff = root / "diff.patch"
            task = root / "task.md"
            agent_summary = root / "agent_summary.md"
            test_output = root / "test_output.txt"
            diff.write_text(
                """
diff --git a/src/review_actions.py b/src/review_actions.py
index 1111111..2222222 100644
--- a/src/review_actions.py
+++ b/src/review_actions.py
@@ -1,2 +1,3 @@
 def append_review_action(actions, action):
+    action["source"] = "review"
     actions.append(action)
""".lstrip(),
                encoding="utf-8",
            )
            task.write_text("Allowed files:\n- `src/review_actions.py`\n", encoding="utf-8")
            agent_summary.write_text(summary, encoding="utf-8")
            test_output.write_text("OK\n", encoding="utf-8")
            evidence = load_pr_evidence(
                diff_path=diff,
                task_path=task,
                agent_summary_path=agent_summary,
                test_output_path=test_output,
            )

            report = analyze_pr_trust(evidence)

        return MergeAuthorizationResult.from_report(report)

    def test_approve_merge_with_no_candidates_creates_no_memory_updates(self) -> None:
        result = self._report_result(summary="Updated `src/review_actions.py`.")

        plan = build_memory_update_plan(
            result,
            [ReviewerAction(action_type="approve_merge", reviewer="Owen")],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual(plan.approved_decisions, [])
        self.assertEqual(plan.rejected_candidate_decisions, [])
        self.assertEqual(plan.to_dict()["approved_decisions"], [])

    def test_approve_merge_with_candidates_does_not_approve_memory(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        plan = build_memory_update_plan(
            result,
            [ReviewerAction(action_type="approve_merge", reviewer="Owen")],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual(len(result.candidate_decisions), 1)
        self.assertEqual(plan.approved_decisions, [])
        self.assertEqual(plan.pending_candidate_decisions, ["CAND-001"])

    def test_approve_candidate_decision_creates_memory_update(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(action_type="approve_merge", reviewer="Owen"),
                ReviewerAction(
                    action_type="approve_candidate_decision",
                    reviewer="Avery",
                    candidate_decision_id="CAND-001",
                ),
            ],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual(len(plan.approved_decisions), 1)
        entry = plan.approved_decisions[0]
        self.assertEqual(entry["id"], "CAND-001")
        self.assertEqual(entry["status"], "approved")
        self.assertEqual(entry["type"], "implementation_constraint")
        self.assertEqual(entry["approved_by"], ["Avery"])
        self.assertEqual(entry["approved_at"], "2026-07-02T12:00:00Z")
        self.assertEqual(entry["source"], "CAND-001")
        self.assertEqual(entry["scope"], {"applies_to_paths": [], "applies_to_concepts": []})
        self.assertEqual(entry["evidence"][0]["source_type"], "candidate_decision")
        self.assertIn("preserve source evidence", entry["evidence"][0]["quote"])

    def test_reject_candidate_decision_creates_no_memory_update(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(
                    action_type="reject_candidate_decision",
                    reviewer="Owen",
                    candidate_decision_id="CAND-001",
                )
            ],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual(plan.approved_decisions, [])
        self.assertEqual(plan.rejected_candidate_decisions, ["CAND-001"])

    def test_request_repair_does_not_approve_memory_without_explicit_candidate_approval(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        plan = build_memory_update_plan(
            result,
            [ReviewerAction(action_type="request_repair", reviewer="Owen")],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual(plan.approved_decisions, [])
        self.assertEqual(plan.pending_candidate_decisions, ["CAND-001"])

    def test_request_repair_can_coexist_with_explicit_candidate_approval(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(action_type="request_repair", reviewer="Owen"),
                ReviewerAction(
                    action_type="approve_candidate_decision",
                    reviewer="Avery",
                    candidate_decision_id="CAND-001",
                ),
            ],
            approved_at="2026-07-02T12:00:00Z",
        )

        self.assertEqual([entry["id"] for entry in plan.approved_decisions], ["CAND-001"])

    def test_invalid_candidate_id_is_rejected(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )

        with self.assertRaisesRegex(ValueError, "Unknown candidate decision id"):
            build_memory_update_plan(
                result,
                [
                    ReviewerAction(
                        action_type="approve_candidate_decision",
                        reviewer="Owen",
                        candidate_decision_id="CAND-999",
                    )
                ],
            )

    def test_invalid_reviewer_action_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid reviewer action"):
            ReviewerAction(action_type="merge_and_approve_memory", reviewer="Owen")

    def test_review_session_serializes_core_loop(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        actions = [
            ReviewerAction(action_type="approve_merge", reviewer="Owen"),
            ReviewerAction(
                action_type="approve_candidate_decision",
                reviewer="Avery",
                candidate_decision_id="CAND-001",
            ),
        ]
        plan = build_memory_update_plan(result, actions, approved_at="2026-07-02T12:00:00Z")

        session = ReviewSession(
            evidence_sources={
                "diff": "diff.patch",
                "assignment": "task.md",
                "pr_body": "agent_summary.md",
                "test_output": "test_output.txt",
            },
            merge_authorization_result=result,
            reviewer_actions=actions,
            memory_update_plan=plan,
        )
        payload = session.to_dict()

        self.assertEqual(payload["evidence_sources"]["diff"], "diff.patch")
        self.assertEqual(payload["merge_authorization_result"]["overall_verdict"], result.overall_verdict)
        self.assertEqual(payload["reviewer_actions"][0]["action_type"], "approve_merge")
        self.assertEqual(payload["memory_update_plan"]["approved_decisions"][0]["id"], "CAND-001")

    def test_writer_writes_approved_candidate_decision_to_new_output_path(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(
                    action_type="approve_candidate_decision",
                    reviewer="Owen",
                    candidate_decision_id="CAND-001",
                )
            ],
            approved_at="2026-07-03",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / ".spool" / "approved_decisions.yml"
            summary = write_memory_update_plan(plan, output_path)
            text = output_path.read_text(encoding="utf-8")
            memory = load_approved_decisions(output_path)

        self.assertEqual(summary["written_count"], 1)
        self.assertEqual(summary["existing_count"], 0)
        self.assertEqual(summary["output_path"], str(output_path))
        self.assertIn("decisions:", text)
        self.assertIn("id: CAND-001", text)
        self.assertIn("source_candidate_id: CAND-001", text)
        self.assertIn('created_at: "2026-07-03"', text)
        self.assertEqual(memory.status, "available")
        self.assertEqual(memory.decisions[0].id, "CAND-001")
        self.assertEqual(memory.decisions[0].scope.applies_to_paths, [])
        self.assertEqual(memory.decisions[0].scope.applies_to_concepts, [])
        self.assertEqual(memory.decisions[0].evidence[0].source_type, "candidate_decision")

    def test_writer_excludes_rejected_and_pending_candidate_decisions(self) -> None:
        result = self._report_result(
            summary=(
                "Going forward, review actions must always preserve source evidence.\n"
                "From now on, candidate memory must not become approved automatically."
            ),
        )
        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(
                    action_type="reject_candidate_decision",
                    reviewer="Owen",
                    candidate_decision_id="CAND-001",
                )
            ],
            approved_at="2026-07-03",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "approved_decisions.yml"
            summary = write_memory_update_plan(plan, output_path)
            text = output_path.read_text(encoding="utf-8")
            memory = load_approved_decisions(output_path)

        self.assertEqual(summary["written_count"], 0)
        self.assertEqual(summary["skipped_rejected_count"], 1)
        self.assertEqual(summary["skipped_pending_count"], 1)
        self.assertEqual(text.strip(), "decisions: []")
        self.assertEqual(memory.status, "available")
        self.assertEqual(memory.decisions, [])

    def test_writer_approve_merge_alone_writes_no_decisions(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        plan = build_memory_update_plan(
            result,
            [ReviewerAction(action_type="approve_merge", reviewer="Owen")],
            approved_at="2026-07-03",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "approved_decisions.yml"
            summary = write_memory_update_plan(plan, output_path)

        self.assertEqual(summary["written_count"], 0)
        self.assertEqual(summary["skipped_pending_count"], 1)

    def test_writer_appends_to_existing_memory_and_preserves_entries(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(
                    action_type="approve_candidate_decision",
                    reviewer="Owen",
                    candidate_decision_id="CAND-001",
                )
            ],
            approved_at="2026-07-03",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing_path = root / "existing.yml"
            output_path = root / "out" / "approved_decisions.yml"
            existing_path.write_text(
                """
decisions:
  - id: DEC-001
    status: approved
    title: Review actions are append-only
    type: implementation_constraint
    scope:
      applies_to_paths:
        - src/review_actions.py
      applies_to_concepts: []
    must:
      - Review actions must append to review_actions.json.
    must_not: []
    requires_approval_for: []
    approved_by:
      - Avery
    evidence:
      - source_type: repo_decision
        quote: Review actions are append-only.
    created_at: 2026-07-01
""".lstrip(),
                encoding="utf-8",
            )

            summary = write_memory_update_plan(plan, output_path, existing_memory_path=existing_path)
            memory = load_approved_decisions(output_path)

        self.assertEqual(summary["existing_count"], 1)
        self.assertEqual(summary["written_count"], 1)
        self.assertEqual([decision.id for decision in memory.decisions], ["DEC-001", "CAND-001"])
        self.assertEqual(memory.decisions[0].title, "Review actions are append-only")

    def test_writer_duplicate_decision_id_raises_clear_error(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        plan = build_memory_update_plan(
            result,
            [
                ReviewerAction(
                    action_type="approve_candidate_decision",
                    reviewer="Owen",
                    candidate_decision_id="CAND-001",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing_path = root / "existing.yml"
            output_path = root / "approved_decisions.yml"
            existing_path.write_text(
                """
decisions:
  - id: CAND-001
    status: approved
    title: Existing decision
    type: process_policy
""".lstrip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate approved decision id: CAND-001"):
                write_memory_update_plan(plan, output_path, existing_memory_path=existing_path)

    def test_writer_requires_explicit_output_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit output_path"):
            write_memory_update_plan(build_memory_update_plan(self._report_result(summary="Updated code."), []), None)

    def test_review_session_creation_does_not_mutate_repo_memory(self) -> None:
        result = self._report_result(
            summary="Going forward, review actions must always preserve source evidence.",
        )
        actions = [ReviewerAction(action_type="approve_merge", reviewer="Owen")]
        plan = build_memory_update_plan(result, actions, approved_at="2026-07-03")

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_memory_path = Path(temp_dir) / ".spool" / "approved_decisions.yml"
            ReviewSession(
                evidence_sources={"diff": "diff.patch"},
                merge_authorization_result=result,
                reviewer_actions=actions,
                memory_update_plan=plan,
            )

            self.assertFalse(repo_memory_path.exists())


if __name__ == "__main__":
    unittest.main()
