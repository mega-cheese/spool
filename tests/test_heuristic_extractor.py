from __future__ import annotations

import unittest

from src.heuristic_extractor import extract_decisions_from_events
from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json
from src.source_resolver import build_event_lookup


class HeuristicExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        events = parse_slack_json(load_slack_json("data/original_fake.json"))
        self.lookup = build_event_lookup(events)
        self.grouped = group_events_by_thread(events)
        self.decisions = extract_decisions_from_events(self.grouped, self.lookup)

    def test_detects_exactly_two_decisions(self) -> None:
        self.assertEqual(len(self.decisions), 2)
        titles = [decision.decision for decision in self.decisions]
        self.assertIn("Delay the enterprise launch until SOC 2 is complete", titles)
        self.assertIn("Use Redis for session state", titles)

    def test_sources_and_claims_are_attached(self) -> None:
        for decision in self.decisions:
            self.assertGreaterEqual(len(decision.sources), 1)
            for claim in decision.all_claims():
                self.assertIsNotNone(claim.source)
                self.assertIn(claim.source.message_ts, self.lookup)

    def test_redis_reasoning_is_atomic(self) -> None:
        redis = next(decision for decision in self.decisions if "Redis" in decision.decision)
        reasoning = [claim.claim for claim in redis.reasoning]

        self.assertIn("Postgres latency is too high under load", reasoning)
        self.assertNotIn("Proposal: use Redis for session state because Postgres latency is too high under load", reasoning)

    def test_tradeoff_not_objection(self) -> None:
        redis = next(decision for decision in self.decisions if "Redis" in decision.decision)
        objections = " ".join(claim.claim.lower() for claim in redis.objections)
        tradeoffs = " ".join(claim.claim.lower() for claim in redis.tradeoffs)

        self.assertNotIn("extra infra complexity", objections)
        self.assertIn("extra infra complexity", tradeoffs)
        self.assertEqual(redis.revisit_trigger, "After launch if Redis becomes a bottleneck.")

    def test_assumption_risk_and_revisit_date(self) -> None:
        launch = next(decision for decision in self.decisions if "enterprise launch" in decision.decision)
        assumptions = [claim.claim for claim in launch.assumptions]
        objections_or_risks = [claim.claim for claim in launch.objections + launch.risks]

        self.assertIn("The pilots will wait", assumptions)
        self.assertTrue(any("Q3 pipeline" in claim for claim in objections_or_risks))
        self.assertEqual(launch.revisit_date, "2026-09-12")

    def test_unrelated_chatter_has_no_decision(self) -> None:
        random_thread = {"1782203000.000100": self.grouped["1782203000.000100"]}
        self.assertEqual(extract_decisions_from_events(random_thread, self.lookup), [])


if __name__ == "__main__":
    unittest.main()
