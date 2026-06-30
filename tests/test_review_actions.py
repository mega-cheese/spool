from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.memory_objects import MemoryObject
from src.memory_store import apply_action_to_store
from src.review_actions import apply_review_action, create_review_action, validate_review_action
from src.review_policy import build_memory_state_debug


class ReviewActionsTest(unittest.TestCase):
    def test_approve_candidate_writes_to_approved_memory(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            result = apply_action_to_store(run_dir, create_review_action("mem_1", "approve", reviewer="Owen"), pretty=True)

            approved = self._load(run_dir / "approved_memory.json")["approved_memory"]
            candidate = self._candidate(run_dir, "mem_1")
            self.assertFalse(result["errors"])
            self.assertEqual(len(approved), 1)
            self.assertEqual(approved[0]["memory_id"], "mem_1")
            self.assertEqual(candidate["review_state"], "approved")
            self.assertEqual(candidate["status"], "unresolved")

    def test_approve_does_not_duplicate_already_approved_memory(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))

            approved = self._load(run_dir / "approved_memory.json")["approved_memory"]
            self.assertEqual(len(approved), 1)

    def test_reject_candidate_writes_to_rejected_memory(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="False positive"))

            rejected = self._load(run_dir / "rejected_memory.json")["rejected_memory"]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0]["metadata"]["rejection_reason"], "False positive")

    def test_reject_removes_from_approved_memory(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))
            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="Changed mind"))

            self.assertEqual(self._load(run_dir / "approved_memory.json")["approved_memory"], [])
            self.assertEqual(len(self._load(run_dir / "rejected_memory.json")["rejected_memory"]), 1)

    def test_approve_removes_from_rejected_memory(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "reject", reason="Initial reject"))
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))

            self.assertEqual(self._load(run_dir / "rejected_memory.json")["rejected_memory"], [])
            self.assertEqual(len(self._load(run_dir / "approved_memory.json")["approved_memory"]), 1)

    def test_edit_updates_safe_fields_and_preserves_source_evidence(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(
                run_dir,
                create_review_action("mem_1", "edit", patch={"title": "Owen should reach out to schools.", "owner_names": ["Owen"]}),
            )

            candidate = self._candidate(run_dir, "mem_1")
            self.assertEqual(candidate["title"], "Owen should reach out to schools.")
            self.assertEqual(candidate["owner_names"], ["Owen"])
            self.assertEqual(candidate["quote"], "Owen to reach out to schools.")
            self.assertTrue(candidate["sources"])
            self.assertEqual(candidate["review_state"], "edited")

    def test_edit_cannot_remove_all_source_evidence(self) -> None:
        candidate = self._memory("mem_1")
        action = create_review_action("mem_1", "edit", patch={"sources": [], "source_event_ids": [], "quote": ""})

        updated, errors, _warnings = apply_review_action(candidate, action)

        self.assertIsNone(updated)
        self.assertTrue(any(error["code"] == "unsafe_edit_field" for error in errors))

    def test_mark_resolved_changes_commitment_status(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_resolved", reason="Confirmed in notes"))

            candidate = self._candidate(run_dir, "mem_1")
            self.assertEqual(candidate["status"], "resolved")
            self.assertEqual(candidate["metadata"]["resolved_reason"], "Confirmed in notes")

    def test_mark_superseded_records_reason(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_superseded", reason="Replaced by later decision"))

            candidate = self._candidate(run_dir, "mem_1")
            self.assertEqual(candidate["status"], "superseded")
            self.assertEqual(candidate["metadata"]["superseded_reason"], "Replaced by later decision")

    def test_mark_stale_records_reason(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_stale", reason="No longer current"))

            candidate = self._candidate(run_dir, "mem_1")
            self.assertEqual(candidate["status"], "stale")
            self.assertEqual(candidate["metadata"]["stale_reason"], "No longer current")

    def test_mark_needs_review_sets_review_state(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_needs_review", reason="Ambiguous owner"))

            candidate = self._candidate(run_dir, "mem_1")
            self.assertEqual(candidate["review_state"], "needs_review")
            self.assertEqual(candidate["metadata"]["review_note"], "Ambiguous owner")

    def test_actions_append_to_review_actions(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "mark_needs_review"))
            apply_action_to_store(run_dir, create_review_action("mem_1", "edit", patch={"summary": "Updated summary."}))

            actions = self._load(run_dir / "review_actions.json")["actions"]
            self.assertEqual([action["action"] for action in actions], ["mark_needs_review", "edit"])

    def test_invalid_memory_id_returns_clear_error(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            result = apply_action_to_store(run_dir, create_review_action("missing", "approve"))

            self.assertTrue(result["errors"])
            self.assertEqual(result["errors"][0]["code"], "memory_not_found")

    def test_invalid_action_returns_clear_error(self) -> None:
        errors = validate_review_action({"memory_id": "mem_1", "action": "dance"})

        self.assertEqual(errors[0]["code"], "invalid_action")

    def test_unsupported_memory_cannot_be_silently_approved(self) -> None:
        with self._run_dir([self._memory("mem_1", evidence_state="unsupported")]) as run_dir:
            result = apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))

            self.assertTrue(any(error["code"] == "unsupported_approval" for error in result["errors"]))
            self.assertEqual(self._load(run_dir / "approved_memory.json")["approved_memory"], [])

    def test_cli_approve_command(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            result = subprocess.run(
                [sys.executable, "review_memory.py", "--run-dir", str(run_dir), "--memory-id", "mem_1", "--action", "approve", "--reviewer", "Owen"],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("approved_count: 1", result.stdout)
            self.assertEqual(len(self._load(run_dir / "approved_memory.json")["approved_memory"]), 1)

    def test_cli_reject_command(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            result = subprocess.run(
                [sys.executable, "review_memory.py", "--run-dir", str(run_dir), "--memory-id", "mem_1", "--action", "reject", "--reason", "False positive"],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("rejected_count: 1", result.stdout)
            self.assertEqual(len(self._load(run_dir / "rejected_memory.json")["rejected_memory"]), 1)

    def test_cli_edit_command_with_json_patch(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    "review_memory.py",
                    "--run-dir",
                    str(run_dir),
                    "--memory-id",
                    "mem_1",
                    "--action",
                    "edit",
                    "--patch",
                    '{"title":"Owen should reach out to schools.","owner_names":["Owen"]}',
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("review_state: edited", result.stdout)
            self.assertEqual(self._candidate(run_dir, "mem_1")["owner_names"], ["Owen"])

    def test_memory_state_debug_reflects_latest_action(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))

            debug_item = self._load(run_dir / "memory_state_debug.json")["items"][0]
            self.assertEqual(debug_item["latest_review_action"], "approve")
            self.assertEqual(debug_item["review_state"], "approved")
            self.assertEqual(debug_item["status"], "unresolved")

    def test_candidate_memory_remains_source_backed_after_actions(self) -> None:
        with self._run_dir([self._memory("mem_1")]) as run_dir:
            apply_action_to_store(run_dir, create_review_action("mem_1", "approve"))

            candidate = self._candidate(run_dir, "mem_1")
            self.assertTrue(candidate["sources"])
            self.assertTrue(candidate["source_event_ids"])
            self.assertEqual(candidate["quote"], "Owen to reach out to schools.")

    def test_build_memory_state_debug_includes_status_and_latest_action_fields(self) -> None:
        action = create_review_action("mem_1", "approve")
        debug = build_memory_state_debug([self._memory("mem_1")], review_actions=[action])

        self.assertEqual(debug["items"][0]["status"], "unresolved")
        self.assertEqual(debug["items"][0]["latest_review_action"], "approve")
        self.assertEqual(debug["items"][0]["latest_review_action_at"], action["created_at"])

    def _run_dir(self, candidates: list[MemoryObject]):
        class RunDirContext:
            def __enter__(inner_self):
                inner_self.temp_dir = tempfile.TemporaryDirectory()
                run_dir = Path(inner_self.temp_dir.name)
                self._write_initial_run(run_dir, candidates)
                return run_dir

            def __exit__(inner_self, exc_type, exc, traceback):
                inner_self.temp_dir.cleanup()

        return RunDirContext()

    def _write_initial_run(self, run_dir: Path, candidates: list[MemoryObject]) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        payloads = {
            "candidate_memory.json": {
                "metadata": {"input_file": "data/example.md", "candidate_count": len(candidates)},
                "candidates": [candidate.to_dict() for candidate in candidates],
            },
            "approved_memory.json": {"metadata": {"input_file": "data/example.md", "approved_count": 0}, "approved_memory": []},
            "rejected_memory.json": {"metadata": {"input_file": "data/example.md", "rejected_count": 0}, "rejected_memory": []},
            "review_actions.json": {"metadata": {"input_file": "data/example.md", "action_count": 0}, "actions": []},
            "memory_state_debug.json": build_memory_state_debug(candidates, input_file="data/example.md"),
        }
        for filename, payload in payloads.items():
            (run_dir / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _memory(self, memory_id: str, evidence_state: str = "supported") -> MemoryObject:
        return MemoryObject(
            memory_id=memory_id,
            source_object_id=f"task_{memory_id}",
            type="commitment",
            title="Reach out to schools.",
            summary="Owen should reach out to schools.",
            owner_names=["Owen"],
            status="unresolved",
            confidence=0.9,
            source_event_ids=["event_1"],
            sources=[{"source_event_id": "event_1", "source_type": "uploaded_document", "quote": "Owen to reach out to schools."}],
            quote="Owen to reach out to schools.",
            evidence_state=evidence_state,
            review_state="unreviewed",
            memory_quality_score=0.95,
        )

    def _load(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def _candidate(self, run_dir: Path, memory_id: str):
        candidates = self._load(run_dir / "candidate_memory.json")["candidates"]
        return next(candidate for candidate in candidates if candidate["memory_id"] == memory_id)


if __name__ == "__main__":
    unittest.main()
