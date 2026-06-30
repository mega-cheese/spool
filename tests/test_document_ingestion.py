from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import run_scenarios
from src.adapters.document_adapter import (
    DocumentParseError,
    document_fixture_to_normalized_events,
    uploaded_document_to_normalized_events,
)
from src.bundles import bundle_source_events
from src.candidate_shadow import build_shadow_candidates, build_state_resolution_debug, filter_clean_decisions_by_claim_ownership
from src.heuristic_extractor import extract_decisions_from_events
from src.input_loader import load_input_source
from src.schemas import Claim, DecisionRecord
from src.source_resolver import attach_sources, build_event_lookup
from src.slack_parser import group_events_by_thread


class DocumentIngestionTest(unittest.TestCase):
    def test_document_adapter_creates_deterministic_event_ids(self) -> None:
        payload = json.loads(Path("data/google_doc_vendor_selection.json").read_text(encoding="utf-8"))

        first = document_fixture_to_normalized_events(payload)
        second = document_fixture_to_normalized_events(payload)

        self.assertEqual([event.event_id for event in first], [event.event_id for event in second])
        self.assertEqual(first[0].event_id, "google_doc_doc_architecture_decision_001_section_1")
        self.assertEqual(first[0].external_id, "doc_architecture_decision_001#section-1")

    def test_document_sections_preserve_heading_and_quote_provenance(self) -> None:
        payload = json.loads(Path("data/google_doc_vendor_selection.json").read_text(encoding="utf-8"))
        events = document_fixture_to_normalized_events(payload)
        decision = events[0]

        self.assertEqual(decision.source_type, "google_doc")
        self.assertEqual(decision.source_subtype, "document_section")
        self.assertEqual(decision.context["section_heading"], "Decision")
        self.assertEqual(decision.provenance["section_heading"], "Decision")
        self.assertEqual(decision.provenance["quote"], "Final decision: use LaunchDarkly for feature flags.")
        self.assertEqual(decision.container["type"], "document")

    def test_document_source_bundle_groups_sections_by_document_id(self) -> None:
        payload = json.loads(Path("data/google_doc_vendor_selection.json").read_text(encoding="utf-8"))
        bundles = bundle_source_events(document_fixture_to_normalized_events(payload))

        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].bundle_type, "document")
        self.assertEqual(bundles[0].source_types, ["google_doc"])
        self.assertEqual(bundles[0].root_id, "doc_architecture_decision_001")
        self.assertEqual(bundles[0].title, "Feature Flag Vendor Decision")
        self.assertEqual(len(bundles[0].events), 7)

    def test_markdown_document_can_be_parsed_into_sections(self) -> None:
        events = uploaded_document_to_normalized_events("data/uploaded_doc_vendor_selection.md")

        self.assertEqual(events[0].source_type, "uploaded_document")
        self.assertEqual(events[0].context["section_heading"], "Decision")
        self.assertTrue(any(event.context["section_heading"] == "Rejected Alternatives" for event in events))
        self.assertTrue(any("Implementation can finish in about eight business days." in event.content["text"] for event in events))

    def test_docx_parser_works_when_dependency_is_available(self) -> None:
        try:
            from docx import Document
        except ImportError:
            self.skipTest("python-docx is not installed in this environment")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memo.docx"
            document = Document()
            document.add_heading("Decision", level=1)
            document.add_paragraph("Final decision: use LaunchDarkly for feature flags.")
            document.add_heading("Timeline", level=1)
            document.add_paragraph("Implementation can finish in about eight business days.")
            document.save(str(path))

            events = uploaded_document_to_normalized_events(path)

        self.assertEqual([event.context["section_heading"] for event in events], ["Decision", "Timeline"])

    def test_pdf_parser_extracts_or_fails_with_clear_non_ocr_error(self) -> None:
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed in this environment")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaises(DocumentParseError) as context:
                uploaded_document_to_normalized_events(path)

        self.assertIn("OCR is not yet supported", str(context.exception))

    def test_mixed_slack_doc_fixture_produces_both_source_types(self) -> None:
        ingested = load_input_source("data/mixed_slack_doc_vendor_selection.json")

        self.assertEqual(ingested.inferred_source_type, "mixed")
        self.assertEqual({event.source_type for event in ingested.normalized_events}, {"slack", "google_doc"})
        self.assertEqual({bundle.bundle_type for bundle in ingested.source_bundles}, {"slack_thread", "document"})

    def test_docs_scenario_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            argv = [
                "run_scenarios.py",
                "--scenario-set",
                "docs",
                "--mode",
                "heuristic",
                "--pipeline",
                "candidates-shadow",
                "--run-id",
                "docs_test",
                "--output-root",
                str(Path(temp_dir) / "runs"),
            ]
            with (
                patch("sys.argv", argv),
                patch("sys.stdout", stdout),
            ):
                run_scenarios.main()

            run_dir = Path(temp_dir) / "runs" / "docs_test"
            decisions_by_scenario = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))

        self.assertIn("Overall verdict: PASS", stdout.getvalue())
        self.assertEqual(set(decisions_by_scenario), {Path(path).stem for path in run_scenarios.SCENARIO_SETS["docs"]})

    def test_docs_scenario_set_works_through_mocked_llm_mode(self) -> None:
        def fake_llm(grouped_threads, event_lookup, model, max_threads):
            return extract_decisions_from_events(grouped_threads, event_lookup, max_threads)

        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            argv = [
                "run_scenarios.py",
                "--scenario-set",
                "docs",
                "--mode",
                "llm",
                "--pipeline",
                "candidates-shadow",
                "--run-id",
                "docs_llm_test",
                "--output-root",
                str(Path(temp_dir) / "runs"),
            ]
            with (
                patch("sys.argv", argv),
                patch("sys.stdout", stdout),
                patch("run_scenarios.extract_decisions_with_llm", side_effect=fake_llm),
            ):
                run_scenarios.main()

        self.assertIn("Overall verdict: PASS", stdout.getvalue())
        self.assertIn("Failed scenarios: none", stdout.getvalue())

    def test_mixed_docs_ownership_filter_tags_short_configcat_timeline_as_rejected(self) -> None:
        ingested = load_input_source("data/mixed_slack_doc_vendor_selection.json")
        lookup = build_event_lookup(ingested.canonical_events)
        decision_event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_mixed_llm_configcat_cleanup",
            decision="Choose LaunchDarkly for feature flags now.",
            date=decision_event.timestamp,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Tom Garcia", "Hannah Lee", "Unknown"],
            status="confirmed",
            confidence=0.86,
            reasoning=[
                Claim(
                    claim="LaunchDarkly supports audit requirements and the SDK is already tested in staging.",
                    source_message_ts="doc_architecture_decision_001#section-2",
                )
            ],
            tradeoffs=[Claim(claim="Higher monthly spend is the accepted tradeoff.", source_message_ts="1791030060.000200")],
            timelines=[
                Claim(claim="The migration would take longer with ConfigCat.", source_message_ts="doc_architecture_decision_001#section-3"),
                Claim(claim="Implementation can finish in about eight business days.", source_message_ts="1791030180.000400"),
            ],
            decision_source_message_ts=decision_event.message_ts,
        )
        attach_sources(decision, lookup)

        removed = filter_clean_decisions_by_claim_ownership(ingested.normalized_events, [decision])
        candidates = build_shadow_candidates(ingested.normalized_events, [decision])
        state = build_state_resolution_debug(candidates)

        self.assertEqual(removed, 1)
        self.assertEqual([claim.claim for claim in decision.timelines], ["Implementation can finish in about eight business days."])
        self.assertIn("google_doc", {source.source_type for source in decision.all_sources})
        self.assertIn(
            "The migration would take longer with ConfigCat.",
            [item["claim"] for item in state["excluded_from_active_memory"]],
        )
        self.assertTrue(
            any(
                item["claim"] == "The migration would take longer with ConfigCat."
                and item["target_name"] == "ConfigCat"
                and item["target_role"] == "rejected_alternative"
                for item in state["excluded_from_active_memory"]
            )
        )
        self.assertTrue(
            any(
                claim["claim"] == "The migration would take longer with ConfigCat."
                and claim["target_name"] == "ConfigCat"
                and claim["target_role"] == "rejected_alternative"
                for candidate in [candidate.to_dict() for candidate in candidates]
                for claim in candidate["claims"]
            )
        )


if __name__ == "__main__":
    unittest.main()
