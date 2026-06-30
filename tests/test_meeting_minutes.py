from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import run_scenarios
from src.input_loader import load_input_source
from src.meeting_minutes import build_clean_memory_payload, classify_document, extract_meeting_memory
from src.schemas import Claim, DecisionRecord
from src.source_resolver import build_event_lookup
from src.source_resolver import resolve_source


class MeetingMinutesTest(unittest.TestCase):
    def test_markdown_meeting_minutes_extracts_memory_objects(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_april15.md")

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertEqual(extraction.meeting_metadata["meeting_date"], "2025-04-15")
        self.assertEqual(extraction.meeting_metadata["location"], "Glass Room")
        self.assertGreaterEqual(len(extraction.decisions), 8)
        self.assertEqual(extraction.action_items["metadata"]["action_item_count"], 6)
        self.assertEqual(extraction.open_questions["metadata"]["open_question_count"], 3)
        decision_text = "\n".join(decision.decision for decision in extraction.decisions)
        self.assertIn("Weekly Zoom meetings will be Tuesday at 7PM.", decision_text)
        self.assertIn("Staff application deadline is May 2nd.", decision_text)
        self.assertNotIn("Theme for VYMUN", decision_text)
        owners = {owner for item in extraction.action_items["action_items"] for owner in item["owner_names"]}
        self.assertIn("James Wang", owners)
        self.assertIn("Frances Nakhuda", owners)
        self.assertIn("Zi Kun Meng", owners)

    def test_meeting_minutes_expands_media_open_question_quote(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_april15.md")
        media = next(item for item in extraction.open_questions["open_questions"] if "Media items" in item["title"])
        quote = media["sources"][0]["quote"]

        self.assertIn("To be Discussed Next Time", quote)
        self.assertIn("Website", quote)
        self.assertIn("Instagram", quote)

    def test_meeting_minutes_expands_action_item_wrapped_task_quote(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_april15.md")
        frances = next(item for item in extraction.action_items["action_items"] if "Poll/email" in item["title"])
        quote = frances["sources"][0]["quote"]

        self.assertIn("Frances Nakhuda", quote)
        self.assertIn("12:00pm", quote)
        self.assertIn("welcome email", quote)

    def test_pdf_meeting_minutes_expands_split_owner_task_quote(self) -> None:
        pdf_path = Path("/Users/admin/Downloads/Meeting #1_ April 15th, 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Local sample PDF is not available.")
        extraction = self._extract(str(pdf_path))
        slide_deck = next(item for item in extraction.action_items["action_items"] if "slide deck" in item["title"].lower())
        quote = slide_deck["sources"][0]["quote"]

        self.assertIn("Hugo Chung", quote)
        self.assertIn("Evangeline Zixuan Lin", quote)
        self.assertIn("Slide deck", quote)

    def test_clean_memory_payload_groups_memory_objects(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_april15.md")
        clean_memory = build_clean_memory_payload(extraction, {"items": [{"reason": "ambiguous_decision"}]})

        self.assertEqual(
            set(clean_memory),
            {"metadata", "confirmed_memory", "action_items", "open_questions", "review_items"},
        )
        self.assertEqual(clean_memory["metadata"]["confirmed_memory_count"], 8)
        self.assertEqual(len(clean_memory["action_items"]), 6)
        self.assertEqual(len(clean_memory["open_questions"]), 3)
        self.assertEqual(len(clean_memory["review_items"]), 1)

    def test_meeting_minutes_memory_taxonomy(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_april15.md")
        clean_memory = build_clean_memory_payload(extraction)
        types_by_title = {item["title"]: item["type"] for item in clean_memory["confirmed_memory"]}

        self.assertEqual(types_by_title["Weekly Zoom meetings start April 28th."], "deadline")
        self.assertEqual(types_by_title["Staff application deadline is May 2nd."], "deadline")
        self.assertEqual(
            types_by_title["Purchases should go through Mrs. Boland / Mrs. Haider rather than being bought individually."],
            "policy_or_process",
        )
        self.assertEqual(types_by_title["Some committees may need three topics."], "change")

    def test_real_pdf_meeting_minutes_extracts_actions_and_open_questions(self) -> None:
        pdf_path = Path("/Users/admin/Downloads/Meeting #1_ April 15th, 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Local sample PDF is not available.")

        extraction = self._extract(str(pdf_path))

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(len(extraction.decisions), 8)
        self.assertGreaterEqual(extraction.action_items["metadata"]["action_item_count"], 5)
        self.assertGreaterEqual(extraction.open_questions["metadata"]["open_question_count"], 2)
        self.assertTrue(any(item["title"].startswith("Edit the staff application") for item in extraction.action_items["action_items"]))
        self.assertTrue(any("Media items" in item["title"] for item in extraction.open_questions["open_questions"]))

    def test_non_meeting_document_is_not_meeting_minutes(self) -> None:
        ingested = load_input_source("data/mixed_slack_doc_vendor_selection.json")

        self.assertEqual(classify_document(ingested.normalized_events), "decision_memo")

    def test_weak_meeting_classifier_handles_numbered_agenda_without_attendance(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(
                "# Meeting #2: May 20, 2025\n\n"
                "Zoom: team room\n\n"
                "## 1. Staffing\n\n"
                "- James to send follow up.\n\n"
                "## 2. Catering\n\n"
                "- Owen look through old orders.\n"
            )
            temp_path = handle.name

        try:
            ingested = load_input_source(temp_path)
            self.assertEqual(classify_document(ingested.normalized_events), "meeting_minutes")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_todo_list_fallback_extracts_action_items(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(
                "# Notes: May 20, 2025\n\n"
                "## To-Do List\n\n"
                "- James to start sending out director acceptances.\n"
                "- Owen to send follow up.\n"
                "- Everyone to look at pin options.\n"
            )
            temp_path = handle.name

        try:
            extraction = self._extract(temp_path)
            self.assertEqual(extraction.classification, "meeting_minutes")
            actions = extraction.action_items["action_items"]
            self.assertGreaterEqual(len(actions), 3)
            james = next(item for item in actions if "director acceptances" in item["title"])
            everyone = next(item for item in actions if "pin options" in item["title"])
            self.assertEqual(james["owner_names"], ["James"])
            self.assertEqual(everyone["owner_names"], [])
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_todo_section_without_extracted_actions_gets_warning(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write("# Notes: May 20, 2025\n\n## To-Do List\n\n- Budget.\n")
            temp_path = handle.name

        try:
            extraction = self._extract(temp_path)
            warnings = extraction.document_memory_debug["review_warnings"]
            self.assertTrue(any(item["reason"] == "extraction_warning" and item["severity"] == "high" for item in warnings))
            self.assertEqual(extraction.action_items["metadata"]["action_item_count"], 0)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_may20_todo_fixture_recovers_all_explicit_tasks(self) -> None:
        extraction = self._extract("data/meeting_vymun_may20_todo.md")
        clean_memory = build_clean_memory_payload(extraction)
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 4)
        self.assertGreaterEqual(len(extraction.block_classification_debug["detected_task_spans"]), 1)
        self.assertIn("Sec bio", action_text)
        self.assertIn("Mrs. Haider", action_text)
        self.assertIn("Finalize directors", action_text)
        self.assertIn("Reach out to schools", action_text)
        self.assertNotIn("Need to touch on social media etiquette", action_text)
        self.assertNotIn("email update", action_text)

    def test_sep30_todo_fixture_recovers_two_spans_and_rejects_sjr(self) -> None:
        extraction = self._extract("data/meeting_vymun_sep30_todo.md")
        clean_memory = build_clean_memory_payload(extraction)
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])
        spans = extraction.block_classification_debug["detected_task_spans"]

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 10)
        self.assertGreaterEqual(len(spans), 2)
        self.assertIn("Order Pizza Jr", action_text)
        self.assertIn("Send staff names", action_text)
        self.assertIn("Email maintenance", action_text)
        self.assertIn("Call Domino", action_text)
        self.assertIn("Connect with Chef Stacey", action_text)
        self.assertIn("Follow up with Mulgrave", action_text)
        self.assertNotIn("SJR: 18 students coming from Winnipeg", action_text)

    def test_real_may20_pdf_recovers_todo_tasks(self) -> None:
        pdf_path = Path("/Users/admin/Downloads/Meeting #5_ May 20th, 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Local Meeting #5 PDF is not available.")

        extraction = self._extract(str(pdf_path))
        clean_memory = build_clean_memory_payload(extraction)
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 4)
        self.assertIn("Sec bio", action_text)
        self.assertIn("Ms Janzen", action_text)
        self.assertIn("Finalize directors", action_text)
        self.assertIn("Reach out to schools", action_text)
        self.assertNotIn("Need to touch on social media etiquette", action_text)
        self.assertNotIn("email update", action_text)

    def test_real_sep30_pdf_recovers_two_todo_spans(self) -> None:
        pdf_path = Path("/Users/admin/Downloads/Meeting #12_ September 30th, 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Local Meeting #12 PDF is not available.")

        extraction = self._extract(str(pdf_path))
        clean_memory = build_clean_memory_payload(extraction)
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 10)
        self.assertGreaterEqual(len(extraction.block_classification_debug["detected_task_spans"]), 2)
        self.assertIn("Order Pizza Jr", action_text)
        self.assertIn("Email maintenance", action_text)
        self.assertIn("Connect with Chef Stacey", action_text)
        self.assertNotIn("SJR: 18 students coming from Winnipeg", action_text)

    def test_may13_meeting_minutes_extracts_memory_actions_and_questions(self) -> None:
        extraction = self._extract("data/meeting_minutes_vymun_may13.md")
        clean_memory = build_clean_memory_payload(extraction)

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertEqual(extraction.meeting_metadata["meeting_date"], "2025-05-13")
        self.assertGreaterEqual(clean_memory["metadata"]["confirmed_memory_count"], 4)
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 8)
        self.assertGreaterEqual(clean_memory["metadata"]["open_question_count"], 4)
        confirmed_text = "\n".join(item["title"] for item in clean_memory["confirmed_memory"])
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])
        question_text = "\n".join(item["title"] for item in clean_memory["open_questions"])
        self.assertIn("Room Where It Happens", confirmed_text)
        self.assertIn("Start sending out director acceptances", action_text)
        self.assertIn("Finish sec bios by Thursday", action_text)
        self.assertIn("Frankie", action_text)
        self.assertIn("Look through materials left from last year", action_text)
        self.assertIn("Jr committees", question_text)
        self.assertNotIn("Jr committees", confirmed_text)

    def test_real_may13_pdf_recovers_meeting_minutes(self) -> None:
        pdf_path = Path("/Users/admin/Downloads/Meeting #4_ May 13, 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Local Meeting #4 PDF is not available.")

        extraction = self._extract(str(pdf_path))
        clean_memory = build_clean_memory_payload(extraction)

        self.assertEqual(extraction.classification, "meeting_minutes")
        self.assertGreaterEqual(clean_memory["metadata"]["confirmed_memory_count"], 4)
        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 8)
        self.assertGreaterEqual(clean_memory["metadata"]["open_question_count"], 4)

    def test_meeting_minutes_scenario_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            argv = [
                "run_scenarios.py",
                "--scenario-set",
                "meeting_minutes",
                "--mode",
                "heuristic",
                "--pipeline",
                "candidates-shadow",
                "--run-id",
                "meeting_minutes_test",
                "--output-root",
                str(Path(temp_dir) / "runs"),
            ]
            with patch("sys.argv", argv), patch("sys.stdout", stdout):
                run_scenarios.main()

            run_dir = Path(temp_dir) / "runs" / "meeting_minutes_test"
            decisions = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            actions = json.loads((run_dir / "action_items_by_scenario.json").read_text(encoding="utf-8"))
            questions = json.loads((run_dir / "open_questions_by_scenario.json").read_text(encoding="utf-8"))
            review = json.loads((run_dir / "review_queue_by_scenario.json").read_text(encoding="utf-8"))
            clean_memory = json.loads((run_dir / "clean_memory_by_scenario.json").read_text(encoding="utf-8"))

        self.assertIn("Overall verdict: PASS", stdout.getvalue())
        self.assertEqual(decisions["meeting_minutes_vymun_april15"]["metadata"]["decision_count"], 8)
        self.assertEqual(decisions["meeting_minutes_vymun_may13"]["metadata"]["decision_count"], 8)
        self.assertEqual(clean_memory["meeting_minutes_vymun_april15"]["metadata"]["confirmed_memory_count"], 8)
        self.assertGreaterEqual(clean_memory["meeting_minutes_vymun_may13"]["metadata"]["action_item_count"], 8)
        self.assertIn("confirmed_memory", clean_memory["meeting_minutes_vymun_april15"])
        self.assertEqual(actions["meeting_minutes_vymun_april15"]["metadata"]["action_item_count"], 6)
        self.assertGreaterEqual(questions["meeting_minutes_vymun_april15"]["metadata"]["open_question_count"], 2)
        self.assertIn("ambiguous_decision", {item["reason"] for item in review["meeting_minutes_vymun_april15"]["items"]})

    def test_meeting_minutes_clean_guard_removes_llm_agenda_decisions(self) -> None:
        def fake_llm(_grouped_threads, event_lookup, _model=None, _max_threads=None):
            theme_event = next((event for event in event_lookup.values() if "Metamorphosis/Change" in event.text), None)
            if theme_event is None:
                return []
            source = resolve_source(theme_event.message_ts, event_lookup)
            return [
                DecisionRecord(
                    decision_id="dec_fake_theme",
                    decision="Choose Theme for VYMUN Sr. and Jr.",
                    date="2025-04-15",
                    owner=None,
                    owner_role=None,
                    participants=[],
                    status="confirmed",
                    confidence=0.82,
                    reasoning=[
                        Claim(
                            claim="The theme options are being brainstormed.",
                            source=source,
                            support_level="direct",
                            confidence=0.82,
                        )
                    ],
                    decision_sources=[source],
                    sources=[source],
                )
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            argv = [
                "run_scenarios.py",
                "--scenario-set",
                "meeting_minutes",
                "--mode",
                "llm",
                "--pipeline",
                "candidates-shadow",
                "--run-id",
                "meeting_minutes_llm_guard_test",
                "--output-root",
                str(Path(temp_dir) / "runs"),
            ]
            with (
                patch("sys.argv", argv),
                patch("sys.stdout", stdout),
                patch("run_scenarios.extract_decisions_with_llm", fake_llm),
            ):
                run_scenarios.main()

            run_dir = Path(temp_dir) / "runs" / "meeting_minutes_llm_guard_test"
            decisions = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            raw_decisions = json.loads((run_dir / "raw_decisions_by_scenario.json").read_text(encoding="utf-8"))

        clean_payload = decisions["meeting_minutes_vymun_april15"]
        raw_payload = raw_decisions["meeting_minutes_vymun_april15"]
        clean_text = "\n".join(decision["decision"] for decision in clean_payload["decisions"])
        raw_text = "\n".join(decision["decision"] for decision in raw_payload["decisions"])

        self.assertIn("Overall verdict: PASS", stdout.getvalue())
        self.assertEqual(clean_payload["metadata"]["decision_count"], 8)
        self.assertNotIn("Choose Theme for VYMUN Sr. and Jr.", clean_text)
        self.assertIn("Choose Theme for VYMUN Sr. and Jr.", raw_text)

    def _extract(self, path: str):
        ingested = load_input_source(path)
        return extract_meeting_memory(
            ingested.normalized_events,
            build_event_lookup(ingested.canonical_events),
            path,
            "heuristic",
            "candidates-shadow",
        )


if __name__ == "__main__":
    unittest.main()
