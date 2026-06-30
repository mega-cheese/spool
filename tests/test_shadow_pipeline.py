from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.adapters.slack_adapter import deterministic_event_id, slack_fixture_to_normalized_events
from src.bundles import bundle_slack_events
from src.candidate_shadow import build_shadow_candidates, build_state_resolution_debug, filter_clean_decisions_by_claim_ownership
from src.heuristic_extractor import extract_decisions_from_events
from src.schemas import Claim, DecisionRecord
from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json
from src.source_resolver import attach_sources, build_event_lookup
from src.state_resolver import are_duplicate_candidates, normalize_decision_text
from src.validators import postprocess_decisions


class ShadowPipelineTest(unittest.TestCase):
    def test_legacy_pipeline_preserves_new_fixture_decision_counts(self) -> None:
        expected_counts = {
            "data/new_general_workability.json": 2,
            "data/new_no_decision.json": 0,
            "data/new_pricing_change.json": 1,
            "data/new_vendor_selection.json": 1,
            "data/new_decision_reversal.json": 1,
            "data/new_messy_implicit.json": 1,
        }
        for path, expected_count in expected_counts.items():
            with self.subTest(path=path):
                processed = self._processed(path)
                self.assertEqual(len(processed.clean_decisions), expected_count)

    def test_slack_adapter_converts_fixture_messages_to_normalized_events(self) -> None:
        slack_json = load_slack_json("data/new_vendor_selection.json")
        events = slack_fixture_to_normalized_events(slack_json)

        self.assertEqual(len(events), 5)
        first = events[0]
        self.assertEqual(first.source_type, "slack")
        self.assertEqual(first.source_subtype, "message")
        self.assertEqual(first.event_type, "message")
        self.assertEqual(first.workspace["name"], "AcmeAI")
        self.assertEqual(first.container["id"], "C_PLATFORM")
        self.assertEqual(first.context["root_id"], "1791030000.000100")
        self.assertEqual(first.external_id, "1791030000.000100")
        self.assertEqual(first.provenance["locator"], "1791030000.000100")
        self.assertEqual(first.provenance["quote"], first.content["text"])

    def test_normalized_event_ids_are_deterministic(self) -> None:
        slack_json = load_slack_json("data/new_vendor_selection.json")
        events = slack_fixture_to_normalized_events(slack_json)

        self.assertEqual(events[0].event_id, deterministic_event_id("slack", "C_PLATFORM", "1791030000.000100"))
        self.assertEqual(events[0].event_id, "slack_C_PLATFORM_1791030000_000100")

    def test_slack_messages_in_same_thread_become_one_source_bundle(self) -> None:
        slack_json = load_slack_json("data/new_vendor_selection.json")
        events = slack_fixture_to_normalized_events(slack_json)
        bundles = bundle_slack_events(events)

        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].bundle_type, "slack_thread")
        self.assertEqual(bundles[0].root_id, "1791030000.000100")
        self.assertEqual(len(bundles[0].events), 5)

    def test_candidate_debug_output_references_normalized_event_ids(self) -> None:
        slack_json = load_slack_json("data/new_vendor_selection.json")
        normalized_events = slack_fixture_to_normalized_events(slack_json)
        processed = self._processed("data/new_vendor_selection.json")

        candidates = build_shadow_candidates(normalized_events, processed.clean_decisions)

        self.assertEqual(len(candidates), 1)
        event_ids = {event.event_id for event in normalized_events}
        self.assertTrue(set(candidates[0].decision_event_ids) <= event_ids)
        self.assertTrue(set(candidates[0].source_event_ids) <= event_ids)
        self.assertTrue(candidates[0].decision_event_ids)

    def test_duplicate_decision_normalization_identifies_near_identical_strings(self) -> None:
        self.assertEqual(
            normalize_decision_text("Final decision: choose LaunchDarkly for feature flags now."),
            normalize_decision_text("Choose LaunchDarkly for feature flags."),
        )
        self.assertTrue(
            are_duplicate_candidates(
                "Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.",
                "Create Security Center parent section for SSO audit exports API tokens and SCIM.",
            )
        )

    def test_candidates_shadow_cli_keeps_clean_output_shape_and_writes_debug_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_output = Path(temp_dir) / "legacy_decisions.json"
            shadow_output = Path(temp_dir) / "decisions.json"
            self._run_extract("data/new_vendor_selection.json", legacy_output, "--pipeline", "legacy")
            self._run_extract("data/new_vendor_selection.json", shadow_output, "--pipeline", "candidates-shadow", "--run-id", "shadow_test")

            legacy_payload = json.loads(legacy_output.read_text(encoding="utf-8"))
            shadow_payload = json.loads(shadow_output.read_text(encoding="utf-8"))
            self.assertEqual(set(legacy_payload.keys()), set(shadow_payload.keys()))
            self.assertEqual(set(legacy_payload["metadata"].keys()), set(shadow_payload["metadata"].keys()))
            self.assertEqual(len(legacy_payload["decisions"]), len(shadow_payload["decisions"]))
            self.assertEqual(set(legacy_payload["decisions"][0].keys()), set(shadow_payload["decisions"][0].keys()))

            run_dir = Path(temp_dir) / "runs" / "shadow_test"
            self.assertTrue(run_dir.is_dir())
            run_decisions = json.loads((run_dir / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(set(shadow_payload.keys()), set(run_decisions.keys()))
            self.assertEqual(len(shadow_payload["decisions"]), len(run_decisions["decisions"]))
            self.assertFalse((Path(temp_dir) / "decisions_run_new_vendor_selection_shadow_test.json").exists())

            for filename in (
                "normalized_events_debug.json",
                "source_bundles_debug.json",
                "candidates_debug.json",
                "state_resolution_debug.json",
            ):
                self.assertTrue((run_dir / filename).exists())
                self.assertFalse((Path(temp_dir) / filename).exists())

            run_metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(run_metadata["run_id"], "shadow_test")
            for artifact in (
                "decisions",
                "raw_decisions",
                "normalized_events_debug",
                "source_bundles_debug",
                "candidates_debug",
                "state_resolution_debug",
            ):
                self.assertIn(artifact, run_metadata["artifacts"])

            normalized_events = json.loads((run_dir / "normalized_events_debug.json").read_text(encoding="utf-8"))
            candidates = json.loads((run_dir / "candidates_debug.json").read_text(encoding="utf-8"))
            event_ids = {event["event_id"] for event in normalized_events}
            self.assertTrue(set(candidates[0]["decision_event_ids"]) <= event_ids)

            state_resolution = json.loads((run_dir / "state_resolution_debug.json").read_text(encoding="utf-8"))
            self.assertIn("claim_ownership", state_resolution)
            self.assertIn("excluded_from_active_memory", state_resolution)
            ownership = state_resolution["claim_ownership"]
            self.assertTrue(
                any(
                    claim["claim"] == "ConfigCat is cheaper by about 22%."
                    and claim["category"] == "comparison"
                    and claim["target_name"] == "ConfigCat"
                    and claim["target_role"] == "rejected_alternative"
                    for claim in ownership
                )
            )
            self.assertTrue(
                any(
                    claim["claim"] == "Higher monthly spend is the accepted tradeoff."
                    and claim["category"] == "tradeoff"
                    and claim["target_name"] == "LaunchDarkly"
                    and claim["target_role"] == "chosen_option"
                    for claim in ownership
                )
            )
            self.assertTrue(
                any(
                    item["claim"] == "ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt."
                    and item["reason"] == "claim targets rejected alternative"
                    and item["target_name"] == "ConfigCat"
                    and item["target_role"] == "rejected_alternative"
                    for item in state_resolution["excluded_from_active_memory"]
                )
            )

            all_candidate_claims = [claim for candidate in candidates for claim in candidate["claims"]]
            self.assertTrue(
                any(
                    claim["target_name"] == "ConfigCat"
                    and claim["target_role"] == "rejected_alternative"
                    for claim in all_candidate_claims
                )
            )
            self.assertTrue(
                any(
                    claim["target_name"] == "LaunchDarkly"
                    and claim["target_role"] == "chosen_option"
                    for claim in all_candidate_claims
                )
            )

    def test_candidates_shadow_repeated_run_id_uses_new_folder_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "decisions.json"
            self._run_extract("data/new_vendor_selection.json", output_path, "--pipeline", "candidates-shadow", "--run-id", "shadow_test")
            self._run_extract("data/new_vendor_selection.json", output_path, "--pipeline", "candidates-shadow", "--run-id", "shadow_test")

            self.assertTrue((Path(temp_dir) / "runs" / "shadow_test").is_dir())
            self.assertTrue((Path(temp_dir) / "runs" / "shadow_test_2").is_dir())

    def test_shadow_ownership_filter_removes_original_vendor_generic_rejected_timeline(self) -> None:
        slack_json = load_slack_json("data/vendor_selection.json")
        events = parse_slack_json(slack_json)
        lookup = build_event_lookup(events)
        decision_event = lookup["1782295440.000500"]
        decision = DecisionRecord(
            decision_id="dec_datadog_shadow_cleanup",
            decision="Choose Datadog for observability now.",
            date=decision_event.timestamp,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
            status="confirmed",
            timelines=[
                Claim(
                    claim="The migration would take longer because several dashboards need to be rebuilt.",
                    source_message_ts="1782295260.000200",
                ),
                Claim(claim="Implementation can finish in about ten business days.", source_message_ts="1782295380.000400"),
            ],
            tradeoffs=[Claim(claim="Higher monthly spend is the accepted tradeoff.", source_message_ts="1782295260.000200")],
            decision_source_message_ts=decision_event.message_ts,
        )
        attach_sources(decision, lookup)
        normalized_events = slack_fixture_to_normalized_events(slack_json)

        removed = filter_clean_decisions_by_claim_ownership(normalized_events, [decision])
        candidates = build_shadow_candidates(normalized_events, [decision])
        state_resolution = build_state_resolution_debug(candidates)

        self.assertEqual(removed, 1)
        self.assertEqual([claim.claim for claim in decision.timelines], ["Implementation can finish in about ten business days."])
        self.assertFalse(
            any(
                claim["claim"] == "The migration would take longer because several dashboards need to be rebuilt."
                and claim["target_name"] == "Datadog"
                and claim["target_role"] == "chosen_option"
                for candidate in [candidate.to_dict() for candidate in candidates]
                for claim in candidate["claims"]
            )
        )
        self.assertTrue(
            any(
                item["claim"] == "New Relic migration would take longer because several dashboards must be rebuilt."
                and item["target_name"] == "New Relic"
                and item["target_role"] == "rejected_alternative"
                for item in state_resolution["excluded_from_active_memory"]
            )
        )

    def test_shadow_ownership_filter_keeps_new_vendor_rejected_timeline_excluded(self) -> None:
        slack_json = load_slack_json("data/new_vendor_selection.json")
        events = parse_slack_json(slack_json)
        lookup = build_event_lookup(events)
        decision_event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_launchdarkly_shadow_cleanup",
            decision="Choose LaunchDarkly for feature flags now.",
            date=decision_event.timestamp,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
            status="confirmed",
            timelines=[
                Claim(
                    claim="ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt.",
                    source_message_ts="1791030060.000200",
                ),
                Claim(claim="Implementation can finish in about eight business days.", source_message_ts="1791030180.000400"),
            ],
            tradeoffs=[Claim(claim="Higher monthly spend is the accepted tradeoff.", source_message_ts="1791030060.000200")],
            decision_source_message_ts=decision_event.message_ts,
        )
        attach_sources(decision, lookup)
        normalized_events = slack_fixture_to_normalized_events(slack_json)

        removed = filter_clean_decisions_by_claim_ownership(normalized_events, [decision])
        state_resolution = build_state_resolution_debug(build_shadow_candidates(normalized_events, [decision]))

        self.assertEqual(removed, 1)
        self.assertEqual([claim.claim for claim in decision.timelines], ["Implementation can finish in about eight business days."])
        self.assertTrue(
            any(
                item["claim"] == "ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt."
                and item["reason"] == "claim targets rejected alternative"
                for item in state_resolution["excluded_from_active_memory"]
            )
        )

    def _processed(self, path: str):
        events = parse_slack_json(load_slack_json(path))
        lookup = build_event_lookup(events)
        raw = extract_decisions_from_events(group_events_by_thread(events), lookup)
        return postprocess_decisions(raw, lookup)

    def _run_extract(self, input_path: str, output_path: Path, *extra_args: str) -> None:
        subprocess.run(
            [
                sys.executable,
                "extract_decisions.py",
                input_path,
                str(output_path),
                "--mode",
                "heuristic",
                *extra_args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
