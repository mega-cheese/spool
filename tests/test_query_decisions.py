from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from query_decisions import answer_question
from src.heuristic_extractor import extract_decisions_from_events
from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json
from src.source_resolver import build_event_lookup


class QueryDecisionsTest(unittest.TestCase):
    def test_only_approved_memory_answers(self) -> None:
        events = parse_slack_json(load_slack_json("data/original_fake.json"))
        decisions = extract_decisions_from_events(group_events_by_thread(events), build_event_lookup(events))
        payload = {"decisions": [decision.to_dict() for decision in decisions]}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "approved.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                answer_question(path, "Why did we delay launch?"),
                "I do not have reliable approved memory for that.",
            )

            payload["decisions"][0]["status"] = "approved"
            path.write_text(json.dumps(payload), encoding="utf-8")
            answer = answer_question(path, "Why did we delay enterprise launch?")
            self.assertIn("Decision:", answer)
            self.assertIn("Sources:", answer)

    def test_timelines_and_revisit_trigger_are_searchable(self) -> None:
        payload = {
            "decisions": [
                {
                    "decision_id": "dec_test",
                    "decision": "Use Redis for session state",
                    "status": "approved",
                    "participants": ["Leo Park"],
                    "owner": "Leo Park",
                    "reasoning": [],
                    "objections": [],
                    "assumptions": [],
                    "risks": [],
                    "tradeoffs": [],
                    "timelines": [
                        {
                            "claim": "Engineering needs about four more weeks.",
                            "source": {"speaker": "Leo Park", "message_ts": "1", "quote": "Engineering needs about four more weeks."},
                        }
                    ],
                    "revisit_trigger": "After launch if Redis becomes a bottleneck.",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "approved.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            timeline_answer = answer_question(path, "What takes four weeks?")
            self.assertIn("timing context", timeline_answer)
            self.assertIn("Engineering needs about four more weeks", timeline_answer)

            trigger_answer = answer_question(path, "When do we revisit bottleneck?")
            self.assertIn("Decision:", trigger_answer)
            self.assertIn("After launch if Redis becomes a bottleneck", trigger_answer)

    def test_structured_dates_deadlines_and_action_items_are_searchable(self) -> None:
        payload = {
            "decisions": [
                {
                    "decision_id": "dec_test",
                    "decision": "Choose Datadog for observability now",
                    "status": "approved",
                    "participants": ["Priya Shah"],
                    "owner": "Priya Shah",
                    "effective_date": "2026-08-01",
                    "reasoning": [],
                    "objections": [],
                    "assumptions": [],
                    "risks": [],
                    "tradeoffs": [],
                    "timelines": [],
                    "deadlines": [
                        {
                            "date": "2026-07-15",
                            "description": "Enterprise SLA starts.",
                            "source": {"speaker": "Hannah Lee", "message_ts": "2", "quote": "before the enterprise SLA starts on 2026-07-15."},
                        }
                    ],
                    "action_items": [
                        {
                            "action": "Run five admin interviews.",
                            "owner": "Evan Brooks",
                            "due_text": "next Friday",
                            "source": {"speaker": "Evan Brooks", "message_ts": "3", "quote": "Action item: run five admin interviews and bring evidence back next Friday."},
                        }
                    ],
                    "revisit_trigger": None,
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "approved.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            deadline_answer = answer_question(path, "When does the enterprise SLA start?")
            self.assertIn("Deadline: 2026-07-15 - Enterprise SLA starts.", deadline_answer)

            action_answer = answer_question(path, "Who should run admin interviews?")
            self.assertIn("Action item: Run five admin interviews. (next Friday)", action_answer)

            effective_answer = answer_question(path, "What starts 2026-08-01?")
            self.assertIn("Effective date: 2026-08-01", effective_answer)


if __name__ == "__main__":
    unittest.main()
