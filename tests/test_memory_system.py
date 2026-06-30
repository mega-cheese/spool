from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.memory_artifacts import build_memory_artifacts
from src.memory_objects import MemoryObject, build_candidate_memory_payload
from src.memory_store import approve_memory, edit_memory, load_approved_memory, reject_memory, save_approved_memory
from src.review_policy import apply_review_policy


class MemorySystemTest(unittest.TestCase):
    def test_decision_action_and_open_question_mapping(self) -> None:
        payload = build_candidate_memory_payload(
            input_file="data/example.md",
            mode="heuristic",
            pipeline="candidates-shadow",
            decisions_payload=self._decision_payload(),
            clean_memory={
                "action_items": [self._action_item()],
                "open_questions": [self._open_question()],
                "confirmed_memory": [],
            },
            review_queue={"items": []},
        )

        types = {item["type"] for item in payload["candidates"]}

        self.assertIn("decision", types)
        self.assertIn("commitment", types)
        self.assertIn("open_question", types)
        commitment = next(item for item in payload["candidates"] if item["type"] == "commitment")
        self.assertEqual(commitment["status"], "unresolved")
        self.assertEqual(commitment["evidence_state"], "supported")

    def test_memory_artifacts_are_written_shape_and_default_not_auto_approved(self) -> None:
        artifacts = build_memory_artifacts(
            input_file="data/example.md",
            mode="heuristic",
            pipeline="candidates-shadow",
            decisions_payload=self._decision_payload(),
            clean_memory={"action_items": [self._action_item()], "open_questions": [], "confirmed_memory": []},
            review_queue={"items": []},
            auto_approve=False,
        )

        self.assertIn("candidate_memory.json", artifacts)
        self.assertIn("approved_memory.json", artifacts)
        self.assertIn("rejected_memory.json", artifacts)
        self.assertIn("review_actions.json", artifacts)
        self.assertIn("memory_state_debug.json", artifacts)
        self.assertGreaterEqual(artifacts["candidate_memory.json"]["metadata"]["candidate_count"], 1)
        self.assertEqual(artifacts["approved_memory.json"]["metadata"]["approved_count"], 0)
        self.assertEqual(artifacts["review_actions.json"]["metadata"]["action_count"], 0)

    def test_suspicious_owner_and_question_like_commitment_need_review(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_bad",
            source_object_id="task_bad",
            type="commitment",
            title="How are awards determined?",
            summary="JBo Rubrics should how are awards determined?",
            owner_names=["JBo Rubrics"],
            status="unresolved",
            confidence=0.9,
            source_event_ids=["event_1"],
            sources=[self._source()],
            quote="How are awards determined?",
            evidence_state="supported",
        )

        reviewed, debug = apply_review_policy([candidate], auto_approve=False)

        self.assertIn(reviewed[0].review_state, {"needs_review", "rejected"})
        self.assertIn("suspicious_owner", reviewed[0].review_reason_codes)
        self.assertIn("question_like_commitment", reviewed[0].review_reason_codes)
        self.assertEqual(debug["metadata"]["needs_review_count"] + debug["metadata"]["rejected_count"], 1)

    def test_question_like_commitment_needs_review(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_question",
            source_object_id="task_question",
            type="commitment",
            title="Send the update?",
            summary="Priya should send the update?",
            owner_names=["Priya"],
            status="unresolved",
            confidence=0.9,
            source_event_ids=["event_1"],
            sources=[self._source()],
            quote="Priya to send the update?",
            evidence_state="supported",
        )

        reviewed, _debug = apply_review_policy([candidate], auto_approve=False)

        self.assertEqual(reviewed[0].review_state, "needs_review")
        self.assertIn("question_like_commitment", reviewed[0].review_reason_codes)

    def test_missing_quote_has_non_supported_evidence_and_needs_review(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_weak",
            source_object_id="task_weak",
            type="commitment",
            title="Send the update.",
            summary="Send the update.",
            confidence=0.8,
            sources=[self._source()],
            quote="",
            evidence_state="unknown",
        )

        reviewed, _debug = apply_review_policy([candidate], auto_approve=False)

        self.assertIn(reviewed[0].evidence_state, {"unknown", "unsupported"})
        self.assertEqual(reviewed[0].review_state, "needs_review")
        self.assertIn("missing_quote", reviewed[0].review_reason_codes)

    def test_supported_high_confidence_commitment_stays_unreviewed_without_auto_approve(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_good",
            source_object_id="task_good",
            type="commitment",
            title="Send the update.",
            summary="Priya should send the update.",
            owner_names=["Priya"],
            status="unresolved",
            confidence=0.95,
            source_event_ids=["event_1"],
            sources=[self._source()],
            quote="Priya to send the update.",
            evidence_state="supported",
        )

        reviewed, _debug = apply_review_policy([candidate], auto_approve=False)

        self.assertEqual(reviewed[0].review_state, "unreviewed")
        self.assertEqual(reviewed[0].status, "unresolved")

    def test_review_store_actions_approve_reject_and_edit(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_review",
            source_object_id="task_review",
            type="commitment",
            title="Send the update.",
            summary="Priya should send the update.",
            owner_names=["Priya"],
            status="unresolved",
            sources=[self._source()],
            quote="Priya to send the update.",
            evidence_state="supported",
        )
        action_log = []

        approved = approve_memory(candidate, reviewer="system", action_log=action_log)
        rejected = reject_memory(candidate, reviewer="system", reason="not company memory", action_log=action_log)
        edited = edit_memory(candidate, {"title": "Send the customer update."}, reviewer="system", action_log=action_log)

        self.assertEqual(approved.review_state, "approved")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(rejected.review_state, "rejected")
        self.assertEqual(rejected.metadata["rejection_reason"], "not company memory")
        self.assertEqual(edited.title, "Send the customer update.")
        self.assertEqual(edited.review_state, "edited")
        self.assertEqual([item["action"] for item in action_log], ["approve", "reject", "edit"])

    def test_memory_store_load_and_save(self) -> None:
        candidate = MemoryObject(
            memory_id="mem_store",
            source_object_id="decision_1",
            type="decision",
            title="Use LaunchDarkly.",
            summary="Use LaunchDarkly.",
            status="approved",
            review_state="approved",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "approved_memory.json"
            save_approved_memory(path, [candidate], metadata={"input_file": "data/example.json"}, pretty=True)
            loaded = load_approved_memory(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].memory_id, "mem_store")

    def _decision_payload(self):
        return {
            "metadata": {"input_file": "data/example.md"},
            "decisions": [
                {
                    "decision_id": "dec_1",
                    "decision": "Use LaunchDarkly for feature flags.",
                    "owner": "Priya",
                    "status": "confirmed",
                    "confidence": 0.92,
                    "decision_sources": [self._source()],
                    "supporting_sources": [],
                    "all_sources": [self._source()],
                    "action_items": [],
                    "risks": [],
                    "assumptions": [],
                }
            ],
        }

    def _action_item(self):
        return {
            "object_id": "task_1",
            "title": "Send the update.",
            "summary": "Priya should send the update.",
            "owner_names": ["Priya"],
            "status": "open",
            "confidence": 0.91,
            "source_event_ids": ["event_1"],
            "sources": [self._source()],
            "quote": "Priya to send the update.",
        }

    def _open_question(self):
        return {
            "object_id": "question_1",
            "title": "Whether the rollout date is final is unresolved.",
            "summary": "The rollout date is unresolved.",
            "status": "open",
            "confidence": 0.8,
            "source_event_ids": ["event_2"],
            "sources": [self._source("event_2", "Is the rollout date final?")],
            "quote": "Is the rollout date final?",
        }

    def _source(self, source_event_id: str = "event_1", quote: str = "Priya to send the update."):
        return {
            "source_event_id": source_event_id,
            "source_type": "uploaded_document",
            "workspace": "local_upload",
            "channel": "Document",
            "channel_id": "doc_1",
            "message_ts": source_event_id,
            "quote": quote,
        }


if __name__ == "__main__":
    unittest.main()
