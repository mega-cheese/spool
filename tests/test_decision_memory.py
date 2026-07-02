from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.spool.merge_authorization.decision_memory import load_approved_decisions


class DecisionMemoryTest(unittest.TestCase):
    def test_loads_approved_decisions_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approved_decisions.yml"
            path.write_text(
                """
decisions:
  - id: DEC-001
    status: approved
    title: Review actions are append-only
    type: implementation_constraint
    scope:
      applies_to_paths:
        - src/review_actions.py
      applies_to_concepts:
        - review actions
    must:
      - Review actions must be appended to review_actions.json.
    must_not:
      - Overwrite review_actions.json with only the latest action.
    requires_approval_for:
      - Changing review action persistence behavior
    approved_by:
      - Owen
    evidence:
      - source_type: repo_decision
        quote: Review actions are append-only.
    created_at: 2026-07-01
""".lstrip(),
                encoding="utf-8",
            )

            memory = load_approved_decisions(path)

        self.assertEqual(memory.status, "available")
        self.assertEqual(memory.source, str(path))
        self.assertEqual(len(memory.decisions), 1)
        decision = memory.decisions[0]
        self.assertEqual(decision.id, "DEC-001")
        self.assertEqual(decision.scope.applies_to_paths, ["src/review_actions.py"])
        self.assertEqual(decision.must_not, ["Overwrite review_actions.json with only the latest action."])
        self.assertEqual(decision.evidence[0].quote, "Review actions are append-only.")

    def test_missing_approved_decisions_returns_missing_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = load_approved_decisions(Path(temp_dir) / "missing.yml")

        self.assertEqual(memory.status, "missing")
        self.assertEqual(memory.decisions, [])
        self.assertTrue(any("No approved decision memory" in note for note in memory.notes))

    def test_malformed_approved_decisions_reports_malformed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approved_decisions.yml"
            path.write_text("decisions:\n  - status: approved\n", encoding="utf-8")

            memory = load_approved_decisions(path)

        self.assertEqual(memory.status, "malformed")
        self.assertEqual(memory.decisions, [])
        self.assertTrue(memory.notes)

    def test_old_repo_memory_json_converts_to_decision_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approved_repo_memory.json"
            path.write_text(
                json.dumps(
                    {
                        "approved_memory": [
                            {
                                "memory_id": "mem_append_only",
                                "type": "decision",
                                "title": "Review actions are append-only.",
                                "summary": "Review actions must append to review_actions.json rather than overwrite prior actions.",
                                "status": "approved",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            memory = load_approved_decisions(path)

        self.assertEqual(memory.status, "available")
        self.assertEqual(len(memory.decisions), 1)
        self.assertEqual(memory.decisions[0].id, "mem_append_only")
        self.assertEqual(memory.decisions[0].must, ["Review actions must append to review_actions.json rather than overwrite prior actions."])


if __name__ == "__main__":
    unittest.main()
