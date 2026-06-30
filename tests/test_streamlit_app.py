from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import streamlit_app
from src.schemas import CLAIM_CATEGORIES
from src.ui_helpers import load_json_if_exists, run_extraction_to_ui_folder


class StreamlitSmokeTest(unittest.TestCase):
    def test_module_imports_without_streamlit_dependency(self) -> None:
        self.assertTrue(hasattr(streamlit_app, "run_app"))

    def test_display_includes_structured_fields(self) -> None:
        clean_memory_source = inspect.getsource(streamlit_app._render_clean_memory)
        review_queue_source = inspect.getsource(streamlit_app._render_review_queue)
        conflicts_source = inspect.getsource(streamlit_app._render_conflicts)

        self.assertIn("timelines", CLAIM_CATEGORIES)
        self.assertIn("revisit_trigger", clean_memory_source)
        self.assertIn("effective_date", clean_memory_source)
        self.assertIn("deadlines", clean_memory_source)
        self.assertIn("action_items", clean_memory_source)
        self.assertIn("confirmed_memory", inspect.getsource(streamlit_app._render_confirmed_meeting_memory))
        self.assertIn("review_queue.json not found for this run.", review_queue_source)
        self.assertIn("No review items for this run.", review_queue_source)
        self.assertIn("severity", review_queue_source)
        self.assertIn("conflicts_debug.json not found for this run.", conflicts_source)
        self.assertIn("No conflicts for this run.", conflicts_source)

    def test_ui_helper_writes_candidates_shadow_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_extraction_to_ui_folder(
                "data/mixed_slack_doc_vendor_selection.json",
                mode="heuristic",
                pipeline="candidates-shadow",
                output_root=Path(temp_dir),
                run_id="ui_test",
            )

            run_dir = result.run_dir
            self.assertTrue((run_dir / "decisions.json").exists())
            self.assertTrue((run_dir / "normalized_events_debug.json").exists())
            self.assertTrue((run_dir / "source_bundles_debug.json").exists())
            self.assertTrue((run_dir / "candidates_debug.json").exists())
            self.assertTrue((run_dir / "state_resolution_debug.json").exists())
            self.assertTrue((run_dir / "conflicts_debug.json").exists())
            self.assertTrue((run_dir / "meeting_metadata.json").exists())
            self.assertTrue((run_dir / "clean_memory.json").exists())
            self.assertTrue((run_dir / "action_items.json").exists())
            self.assertTrue((run_dir / "open_questions.json").exists())
            self.assertTrue((run_dir / "document_memory_debug.json").exists())
            self.assertTrue((run_dir / "block_classification_debug.json").exists())
            self.assertTrue((run_dir / "review_queue.json").exists())
            self.assertTrue((run_dir / "review_queue.md").exists())
            self.assertTrue((run_dir / "candidate_memory.json").exists())
            self.assertTrue((run_dir / "approved_memory.json").exists())
            self.assertTrue((run_dir / "rejected_memory.json").exists())
            self.assertTrue((run_dir / "review_actions.json").exists())
            self.assertTrue((run_dir / "memory_state_debug.json").exists())
            self.assertTrue((run_dir / "run_metadata.json").exists())

            decisions = load_json_if_exists(run_dir / "decisions.json")
            state = load_json_if_exists(run_dir / "state_resolution_debug.json")
            bundles = load_json_if_exists(run_dir / "source_bundles_debug.json")
            conflicts = load_json_if_exists(run_dir / "conflicts_debug.json")
            meeting_metadata = load_json_if_exists(run_dir / "meeting_metadata.json")
            clean_memory = load_json_if_exists(run_dir / "clean_memory.json")
            review_queue = load_json_if_exists(run_dir / "review_queue.json")
            candidate_memory = load_json_if_exists(run_dir / "candidate_memory.json")
            approved_memory = load_json_if_exists(run_dir / "approved_memory.json")

            self.assertEqual(len(decisions["decisions"]), 1)
            self.assertTrue(state["excluded_from_active_memory"])
            self.assertEqual({bundle["bundle_type"] for bundle in bundles}, {"slack_thread", "document"})
            self.assertEqual(conflicts, [])
            self.assertEqual(meeting_metadata["classification"], "decision_memo")
            self.assertIn("confirmed_memory", clean_memory)
            self.assertIn("review_item_count", review_queue["metadata"])
            self.assertIn("candidates", candidate_memory)
            self.assertEqual(approved_memory["metadata"]["approved_count"], 0)
            self.assertEqual(result.output_paths["conflicts_debug"], str(run_dir / "conflicts_debug.json"))
            self.assertEqual(result.output_paths["meeting_metadata"], str(run_dir / "meeting_metadata.json"))
            self.assertEqual(result.output_paths["clean_memory"], str(run_dir / "clean_memory.json"))
            self.assertEqual(result.output_paths["action_items"], str(run_dir / "action_items.json"))
            self.assertEqual(result.output_paths["open_questions"], str(run_dir / "open_questions.json"))
            self.assertEqual(result.output_paths["document_memory_debug"], str(run_dir / "document_memory_debug.json"))
            self.assertEqual(result.output_paths["block_classification_debug"], str(run_dir / "block_classification_debug.json"))
            self.assertEqual(result.output_paths["review_queue"], str(run_dir / "review_queue.json"))
            self.assertEqual(result.output_paths["review_queue_md"], str(run_dir / "review_queue.md"))
            self.assertEqual(result.output_paths["candidate_memory"], str(run_dir / "candidate_memory.json"))
            self.assertEqual(result.output_paths["approved_memory"], str(run_dir / "approved_memory.json"))
            self.assertEqual(result.output_paths["memory_state_debug"], str(run_dir / "memory_state_debug.json"))


if __name__ == "__main__":
    unittest.main()
