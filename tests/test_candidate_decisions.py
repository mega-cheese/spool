from __future__ import annotations

import unittest

from src.spool.merge_authorization.candidate_decisions import extract_candidate_decisions


class CandidateDecisionTest(unittest.TestCase):
    def test_extracts_future_facing_product_policy_candidate(self) -> None:
        candidates = extract_candidate_decisions(
            "Going forward, all unsupported models should hide upload by default.",
            source="agent_summary",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "CAND-001")
        self.assertEqual(candidates[0].decision_type, "product_behavior")
        self.assertEqual(candidates[0].status, "candidate")
        self.assertEqual(candidates[0].recommended_action, "approve_or_reject")
        self.assertEqual(candidates[0].title, "All unsupported models should hide upload by default")

    def test_ignores_ordinary_should_language(self) -> None:
        candidates = extract_candidate_decisions(
            "Tests should pass.\nThe helper should return null for invalid input.",
            source="agent_summary",
        )

        self.assertEqual(candidates, [])

    def test_classifies_sensitive_auth_policy_candidate(self) -> None:
        candidates = extract_candidate_decisions(
            "Auth config changes should be allowed from UI tasks if tests pass.",
            source="agent_summary",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].decision_type, "security")


if __name__ == "__main__":
    unittest.main()
