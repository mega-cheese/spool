from __future__ import annotations

import unittest

from src.block_classifier import classify_blocks
from src.coverage_audit import audit_task_coverage, build_block_classification_debug
from src.input_loader import load_input_source
from src.meeting_minutes import build_clean_memory_payload, extract_meeting_memory
from src.source_resolver import build_event_lookup
from src.task_extractor import extract_action_items_from_blocks, extract_explicit_task_spans, parse_task_line


class BlockTaskExtractionTest(unittest.TestCase):
    def test_task_heading_detection(self) -> None:
        blocks = self._blocks("data/block_task_training_notes.md")

        self.assertIn("task_table", {block["block_type"] for block in blocks})

    def test_owner_colon_task_parsing(self) -> None:
        parsed = parse_task_line("Corina: Record the training session", default_year=2025)

        self.assertEqual(parsed["owner_names"], ["Corina"])
        self.assertEqual(parsed["title"], "Record the training session.")

    def test_owner_to_task_parsing(self) -> None:
        parsed = parse_task_line("James to start sending out director acceptances", default_year=2025)

        self.assertEqual(parsed["owner_names"], ["James"])
        self.assertEqual(parsed["title"], "Start sending out director acceptances.")

    def test_multi_owner_parsing(self) -> None:
        parsed = parse_task_line("Owen and Frankie to follow up with St John's Ravens Court", default_year=2025)

        self.assertEqual(parsed["owner_names"], ["Owen", "Frankie"])
        self.assertEqual(parsed["title"], "Follow up with St John's Ravens Court.")

    def test_owner_date_task_row_parsing(self) -> None:
        parsed = parse_task_line("Bernice and Owen September 23rd Start pairing students and tutors", default_year=2025)

        self.assertEqual(parsed["owner_names"], ["Bernice", "Owen"])
        self.assertEqual(parsed["due_date"], "2025-09-23")
        self.assertEqual(parsed["title"], "Start pairing students and tutors.")

    def test_policy_sections_do_not_become_tasks(self) -> None:
        extraction = self._extract("data/block_task_training_notes.md")
        action_text = "\n".join(item["title"] for item in extraction.action_items["action_items"])
        block_types = {block["block_type"] for block in extraction.block_classification_debug["blocks"]}

        self.assertIn("policy_or_process", block_types)
        self.assertNotIn("Maintain a professional setting", action_text)
        self.assertNotIn("Try to have a fun/engaging tone", action_text)

    def test_explicit_task_section_coverage_warning(self) -> None:
        blocks = [
            {
                "block_id": "block_empty",
                "source_event_id": "event_1",
                "block_type": "task_section",
                "heading": "Tasks",
                "text": "Tasks\nBudget.",
                "lines": [{"text": "Budget.", "body": "Budget.", "indent": 0}],
                "likely_task_rows": [],
                "source": {"source_event_id": "event_1", "quote": "Budget."},
            }
        ]

        warnings = audit_task_coverage(blocks, [])

        self.assertTrue(any(warning["title"] == "Action items may have been missed" for warning in warnings))

    def test_task_table_under_extraction_warning(self) -> None:
        blocks = [
            {
                "block_id": "block_table",
                "source_event_id": "event_1",
                "block_type": "task_table",
                "heading": "This Week's Tasks",
                "text": "",
                "lines": [],
                "likely_task_rows": [{"text": str(index)} for index in range(5)],
                "source": {"source_event_id": "event_1"},
            }
        ]

        warnings = audit_task_coverage(blocks, [{"object_id": "one", "block_id": "block_table"}])

        self.assertTrue(any(warning["title"] == "Task table may have been under-extracted" for warning in warnings))

    def test_internal_todo_headings_create_explicit_spans(self) -> None:
        blocks = self._blocks("data/meeting_vymun_sep30_todo.md")
        spans = extract_explicit_task_spans(blocks)

        self.assertGreaterEqual(len(spans), 2)
        self.assertEqual(spans[0]["heading"], "To-Do list (New)")
        self.assertEqual(spans[1]["heading"], "To-Do list (Copied from last week)")
        self.assertGreaterEqual(len(spans[1]["likely_task_rows"]), 10)

    def test_owner_prefix_without_connector_in_explicit_span(self) -> None:
        parsed = parse_task_line("Mrs Haider ask Ms Janzen for reg link", default_year=2025, explicit_span=True)

        self.assertEqual(parsed["owner_names"], ["Mrs. Haider"])
        self.assertEqual(parsed["title"], "Ask Ms Janzen for reg link.")

    def test_parenthetical_and_dash_owner_suffixes(self) -> None:
        parenthetical = parse_task_line("Order Pizza Jr. (Evangeline & Hugo)", default_year=2025, explicit_span=True)
        dash = parse_task_line("VYMUN UBC on Sr. School TVs - Elliott or Owen", default_year=2025, explicit_span=True)

        self.assertEqual(parenthetical["owner_names"], ["Evangeline", "Hugo"])
        self.assertEqual(parenthetical["title"], "Order Pizza Jr.")
        self.assertEqual(dash["owner_names"], ["Elliott", "Owen"])
        self.assertEqual(dash["title"], "VYMUN UBC on Sr. School TVs.")

    def test_explicit_span_rejects_chatter_false_positives(self) -> None:
        self.assertIsNone(parse_task_line("Need to touch on social media etiquette", explicit_span=True))
        self.assertIsNone(parse_task_line("SJR: 18 students coming from Winnipeg!", explicit_span=True))
        self.assertIsNone(parse_task_line("Owen - email update?", explicit_span=True))

    def test_explicit_span_coverage_warning_and_debug(self) -> None:
        blocks = [
            {
                "block_id": "block_notes",
                "source_event_id": "event_1",
                "block_type": "unknown",
                "heading": "Document",
                "text": "To Do\nBudget.",
                "lines": [
                    {"text": "To Do", "body": "To Do", "indent": 0},
                    {"text": "Budget.", "body": "Budget.", "indent": 0},
                ],
                "likely_task_rows": [],
                "source": {"source_event_id": "event_1", "quote": "To Do Budget."},
            }
        ]
        spans = extract_explicit_task_spans(blocks)
        actions = extract_action_items_from_blocks(blocks, task_spans=spans)
        warnings = audit_task_coverage(blocks, actions, spans)
        debug = build_block_classification_debug(blocks, actions, warnings, spans)

        self.assertEqual(len(spans), 1)
        self.assertFalse(actions)
        self.assertTrue(any(warning["title"] == "Explicit To Do section produced no action items" for warning in warnings))
        self.assertEqual(len(debug["detected_task_spans"]), 1)
        self.assertEqual(debug["detected_task_spans"][0]["start_line_index"], 1)
        self.assertIn("To Do", debug["detected_task_spans"][0]["quote"])

    def test_logistics_task_table_fixture_extracts_rows(self) -> None:
        extraction = self._extract("data/block_task_logistics_table.md")
        clean_memory = build_clean_memory_payload(extraction)
        action_text = "\n".join(item["title"] + " " + " ".join(item["owner_names"]) for item in clean_memory["action_items"])

        self.assertGreaterEqual(clean_memory["metadata"]["action_item_count"], 5)
        self.assertIn("task_table", {block["block_type"] for block in extraction.block_classification_debug["blocks"]})
        self.assertIn("Tyler", action_text)
        self.assertIn("Bernice", action_text)
        self.assertIn("Start pairing students and tutors", action_text)

    def _blocks(self, path: str):
        return classify_blocks(load_input_source(path).normalized_events)

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
