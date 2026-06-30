from __future__ import annotations

import unittest

from src.conflict_detector import detect_conflicts, remove_conflicted_clean_decisions
from src.input_loader import load_input_source
from src.review_queue import build_review_queue
from src.schemas import DecisionRecord, Source


class ConflictDetectorTest(unittest.TestCase):
    def test_detects_slack_doc_vendor_conflict(self) -> None:
        conflicts = self._conflicts_for("data/conflict_slack_doc_vendor_selection.json")

        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["type"], "conflicting_sources")
        self.assertEqual(conflict["severity"], "high")
        self.assertEqual(conflict["conflict_area"], "feature flags vendor")
        self.assertEqual({option["option"] for option in conflict["options"]}, {"LaunchDarkly", "ConfigCat"})
        self.assertEqual({source["source_type"] for source in conflict["sources"]}, {"slack", "google_doc"})

    def test_rejected_alternative_doc_section_is_not_conflict(self) -> None:
        conflicts = self._conflicts_for("data/no_conflict_doc_rejected_alternative.json")

        self.assertEqual(conflicts, [])

    def test_same_chosen_option_across_sources_is_not_conflict(self) -> None:
        conflicts = self._conflicts_for("data/mixed_slack_doc_vendor_selection.json")

        self.assertEqual(conflicts, [])

    def test_conflict_creates_high_severity_review_item(self) -> None:
        ingested = load_input_source("data/conflict_slack_doc_vendor_selection.json")
        conflicts = detect_conflicts(normalized_events=ingested.normalized_events)

        review_queue = build_review_queue([], normalized_events=ingested.normalized_events, conflicts=conflicts)

        self.assertEqual(review_queue["metadata"]["review_item_count"], 1)
        item = review_queue["items"][0]
        self.assertEqual(item["reason"], "conflicting_sources")
        self.assertEqual(item["severity"], "high")
        self.assertEqual(item["title"], "Conflicting sources for decision")
        self.assertEqual({option["option"] for option in item["options"]}, {"LaunchDarkly", "ConfigCat"})

    def test_conflicted_clean_decision_is_removed(self) -> None:
        conflicts = self._conflicts_for("data/conflict_slack_doc_vendor_selection.json")
        decisions = [self._decision("Choose LaunchDarkly for feature flags now.")]

        removed = remove_conflicted_clean_decisions(decisions, conflicts)

        self.assertEqual(removed, 1)
        self.assertEqual(decisions, [])

    def test_casual_non_decision_text_has_no_conflict_or_review_item(self) -> None:
        ingested = load_input_source("data/original_fake.json")
        random_events = [event for event in ingested.normalized_events if event.container.get("id") == "C_RANDOM"]

        conflicts = detect_conflicts(normalized_events=random_events)
        review_queue = build_review_queue([], normalized_events=random_events, conflicts=conflicts)

        self.assertEqual(conflicts, [])
        self.assertEqual(review_queue["items"], [])

    def _conflicts_for(self, path: str) -> list[dict[str, object]]:
        ingested = load_input_source(path)
        return detect_conflicts(normalized_events=ingested.normalized_events, source_bundles=ingested.source_bundles)

    def _decision(self, decision: str) -> DecisionRecord:
        source = Source(
            source_type="slack",
            workspace="AcmeAI",
            channel="platform",
            channel_id="C_PLATFORM",
            thread_ts="1810000000.000100",
            message_ts="1810000060.000200",
            speaker="Hannah Lee",
            speaker_role="Director of Engineering",
            quote="Approved: choose LaunchDarkly for feature flags now.",
        )
        return DecisionRecord(
            decision_id="dec_conflict_test",
            decision=decision,
            date="2027-05-14",
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Hannah Lee"],
            status="confirmed",
            confidence=0.9,
            decision_sources=[source],
        )


if __name__ == "__main__":
    unittest.main()
