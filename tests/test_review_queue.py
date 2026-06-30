from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.review_queue import build_review_queue, review_queue_to_markdown, write_review_queue
from src.schemas import DecisionRecord, Source


class ReviewQueueTest(unittest.TestCase):
    def test_confirmed_clean_decision_with_source_has_empty_review_queue(self) -> None:
        decision = self._decision(status="confirmed", decision_sources=[self._source()])

        review_queue = build_review_queue(
            [decision],
            input_file="data/new_vendor_selection.json",
            mode="heuristic",
            pipeline="candidates-shadow",
        )

        self.assertEqual(review_queue["metadata"]["review_item_count"], 0)
        self.assertEqual(review_queue["items"], [])
        self.assertEqual(review_queue_to_markdown(review_queue), "# Review Queue\n\nNo review items.\n")

    def test_review_item_shape_for_unapproved_candidate(self) -> None:
        raw_decision = self._decision(
            status="candidate",
            decision="Choose LaunchDarkly for feature flags.",
            decision_sources=[],
        )
        events = [
            {
                "event_id": "slack_C_PLATFORM_1",
                "source_type": "slack",
                "source_subtype": "message",
                "external_id": "1800000000.000100",
                "actor": {"name": "Priya Shah", "role": "Staff Engineer"},
                "content": {"text": "Proposal: choose LaunchDarkly for feature flags."},
                "provenance": {"locator": "1800000000.000100", "quote": "Proposal: choose LaunchDarkly for feature flags."},
                "context": {"root_id": "1800000000.000100"},
            }
        ]

        review_queue = build_review_queue(
            [],
            raw_decisions=[raw_decision],
            normalized_events=events,
            input_file="data/review_proposal_no_approval.json",
            mode="heuristic",
            pipeline="candidates-shadow",
        )

        self.assertEqual(review_queue["metadata"]["review_item_count"], 1)
        item = review_queue["items"][0]
        for field in (
            "review_id",
            "type",
            "severity",
            "status",
            "title",
            "summary",
            "candidate_decision",
            "reason",
            "recommended_action",
            "source_event_ids",
            "sources",
            "related_candidate_ids",
            "created_at",
        ):
            self.assertIn(field, item)
        self.assertEqual(item["reason"], "approval_missing")
        self.assertEqual(item["status"], "open")

    def test_non_decision_lunch_thread_does_not_create_review_item(self) -> None:
        events = self._events(
            [
                ("slack_C_RANDOM_1", "Nina Patel", "Designer", "The new launch poster looks sharp. Lunch at noon?"),
                ("slack_C_RANDOM_2", "Sarah Kim", "Head of Sales", "Yes, let's grab sandwiches after the standup."),
            ]
        )

        review_queue = build_review_queue([], normalized_events=events)

        self.assertEqual(review_queue["metadata"]["review_item_count"], 0)
        self.assertEqual(review_queue["items"], [])
        self.assertEqual(review_queue_to_markdown(review_queue), "# Review Queue\n\nNo review items.\n")

    def test_placeholder_candidate_text_is_suppressed(self) -> None:
        review_queue = build_review_queue(
            [],
            candidates=[
                {
                    "candidate_id": "cand_placeholder",
                    "state": "proposed",
                    "proposed_action": "Possible decision from source thread.",
                    "source_event_ids": ["slack_C_RANDOM_1"],
                }
            ],
            normalized_events=self._events(
                [
                    ("slack_C_RANDOM_1", "Nina Patel", "Designer", "The new launch poster looks sharp. Lunch at noon?"),
                ]
            ),
        )

        self.assertEqual(review_queue["metadata"]["review_item_count"], 0)
        self.assertEqual(review_queue["items"], [])

    def test_real_proposal_without_approval_creates_review_item(self) -> None:
        for proposal_text, expected_decision in (
            (
                "I recommend choosing LaunchDarkly for feature flags because audit support is ready.",
                "LaunchDarkly for feature flags because audit support is ready.",
            ),
            (
                "Proposal: choose LaunchDarkly for feature flags. The SDK is already tested and audit support looks stronger.",
                "LaunchDarkly for feature flags.",
            ),
        ):
            with self.subTest(proposal_text=proposal_text):
                review_queue = build_review_queue(
                    [],
                    normalized_events=self._events(
                        [
                            (
                                "slack_C_PLATFORM_1",
                                "Priya Shah",
                                "Staff Engineer",
                                proposal_text,
                            ),
                        ]
                    ),
                )

                self.assertEqual(review_queue["metadata"]["review_item_count"], 1)
                self.assertEqual(review_queue["items"][0]["reason"], "approval_missing")
                self.assertEqual(review_queue["items"][0]["candidate_decision"], expected_decision)

    def test_ambiguous_work_decision_creates_review_item(self) -> None:
        review_queue = build_review_queue(
            [],
            normalized_events=self._events(
                [
                    (
                        "slack_C_PRODUCT_1",
                        "Owen Diaz",
                        "Product Manager",
                        "Maybe we should delay analytics beta until RBAC is finished.",
                    ),
                    ("slack_C_PRODUCT_2", "Priya Shah", "Staff Engineer", "Seems reasonable."),
                ]
            ),
        )

        self.assertEqual(review_queue["metadata"]["review_item_count"], 1)
        self.assertIn(review_queue["items"][0]["reason"], {"ambiguous_decision", "approval_missing"})

    def test_owner_missing_reason_for_confirmed_decision_without_owner(self) -> None:
        decision = self._decision(status="confirmed", owner=None, decision_sources=[self._source()])

        review_queue = build_review_queue([decision])

        self.assertEqual(review_queue["metadata"]["review_item_count"], 1)
        self.assertEqual(review_queue["items"][0]["reason"], "owner_missing")

    def test_write_review_queue_creates_json_and_markdown(self) -> None:
        review_queue = build_review_queue([])

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, md_path = write_review_queue(review_queue, temp_dir, pretty=True)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["items"], [])
            self.assertEqual(md_path.read_text(encoding="utf-8"), "# Review Queue\n\nNo review items.\n")

    def _decision(
        self,
        *,
        status: str,
        decision: str = "Choose LaunchDarkly for feature flags now.",
        owner: str | None = "Priya Shah",
        decision_sources: list[Source] | None = None,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id="dec_test",
            decision=decision,
            date="2026-09-01",
            owner=owner,
            owner_role="Staff Engineer" if owner else None,
            participants=["Priya Shah"],
            status=status,
            confidence=0.86,
            decision_sources=decision_sources or [],
        )

    def _source(self) -> Source:
        return Source(
            source_type="slack",
            workspace="spool",
            channel="platform",
            channel_id="C_PLATFORM",
            thread_ts="1800000000.000100",
            message_ts="1800000000.000100",
            speaker="Priya Shah",
            speaker_role="Staff Engineer",
            quote="Approved: choose LaunchDarkly for feature flags now.",
        )

    def _events(self, rows: list[tuple[str, str, str, str]]) -> list[dict[str, object]]:
        root_id = rows[0][0] if rows else "root"
        events = []
        for index, (event_id, actor_name, actor_role, text) in enumerate(rows, start=1):
            locator = f"1800000000.000{index}"
            events.append(
                {
                    "event_id": event_id,
                    "source_type": "slack",
                    "source_subtype": "message",
                    "external_id": locator,
                    "actor": {"name": actor_name, "role": actor_role},
                    "content": {"text": text},
                    "provenance": {"locator": locator, "quote": text},
                    "context": {"root_id": root_id},
                }
            )
        return events


if __name__ == "__main__":
    unittest.main()
