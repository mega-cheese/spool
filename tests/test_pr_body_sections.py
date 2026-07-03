from __future__ import annotations

import unittest

from src.spool.merge_authorization.pr_body_sections import parse_pr_body_sections


class PRBodySectionsTest(unittest.TestCase):
    def test_parses_spool_task_agent_summary_tests_and_candidate_decisions(self) -> None:
        parsed = parse_pr_body_sections(
            """
Intro text before sections.

## Spool Task

Add local read-only GitHub PR ingestion for Spool.

Allowed files/areas:
- README.md
- src/spool/merge_authorization/

## Agent Summary

Implemented read-only GitHub PR ingestion using gh pr view and gh pr diff.

## Tests Run

PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests

Result: 299 tests OK

## Candidate Decisions

None.
""".lstrip()
        )

        self.assertIn("Add local read-only GitHub PR ingestion", parsed.assignment_text)
        self.assertIn("Allowed files/areas", parsed.assignment_text)
        self.assertEqual(parsed.assignment_source, "Spool Task")
        self.assertIn("Implemented read-only GitHub PR ingestion", parsed.agent_summary_text)
        self.assertEqual(parsed.agent_summary_source, "Agent Summary")
        self.assertIn("299 tests OK", parsed.test_evidence_text)
        self.assertEqual(parsed.test_evidence_source, "Tests Run")
        self.assertEqual(parsed.candidate_decisions_text, "None.")
        self.assertEqual(parsed.candidate_decisions_source, "Candidate Decisions")

    def test_heading_matching_is_case_insensitive(self) -> None:
        parsed = parse_pr_body_sections(
            """
## assignment
Task text.

## implementation summary
Summary text.

## testing
Tests OK.

## decisions
None.
""".lstrip()
        )

        self.assertEqual(parsed.assignment_text, "Task text.")
        self.assertEqual(parsed.assignment_source, "assignment")
        self.assertEqual(parsed.agent_summary_text, "Summary text.")
        self.assertEqual(parsed.agent_summary_source, "implementation summary")
        self.assertEqual(parsed.test_evidence_text, "Tests OK.")
        self.assertEqual(parsed.test_evidence_source, "testing")
        self.assertEqual(parsed.candidate_decisions_text, "None.")
        self.assertEqual(parsed.candidate_decisions_source, "decisions")

    def test_missing_sections_return_empty_strings(self) -> None:
        parsed = parse_pr_body_sections("Regular PR body without Spool sections.")

        self.assertEqual(parsed.assignment_text, "")
        self.assertIsNone(parsed.assignment_source)
        self.assertEqual(parsed.agent_summary_text, "")
        self.assertIsNone(parsed.agent_summary_source)
        self.assertEqual(parsed.test_evidence_text, "")
        self.assertIsNone(parsed.test_evidence_source)
        self.assertEqual(parsed.candidate_decisions_text, "")
        self.assertIsNone(parsed.candidate_decisions_source)


if __name__ == "__main__":
    unittest.main()
