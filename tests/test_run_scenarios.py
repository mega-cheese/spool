from __future__ import annotations

import csv
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import run_scenarios
from src.heuristic_extractor import extract_decisions_from_events
from src.schemas import Claim, DecisionRecord
from src.source_resolver import attach_sources


class RunScenariosTest(unittest.TestCase):
    def test_scenario_sets_resolve_expected_fixture_groups(self) -> None:
        self.assertEqual(
            run_scenarios.resolve_scenario_set("new"),
            [
                "data/new_general_workability.json",
                "data/new_no_decision.json",
                "data/new_pricing_change.json",
                "data/new_vendor_selection.json",
                "data/new_decision_reversal.json",
                "data/new_messy_implicit.json",
            ],
        )
        self.assertEqual(len(run_scenarios.resolve_scenario_set("original")), 6)
        self.assertEqual(
            run_scenarios.resolve_scenario_set("all"),
            [*run_scenarios.resolve_scenario_set("original"), *run_scenarios.resolve_scenario_set("new")],
        )
        self.assertEqual(
            run_scenarios.resolve_scenario_set("review"),
            [
                "data/review_proposal_no_approval.json",
                "data/review_missing_owner.json",
                "data/review_low_confidence_ambiguous.json",
            ],
        )
        self.assertEqual(
            run_scenarios.resolve_scenario_set("conflict"),
            [
                "data/conflict_slack_doc_vendor_selection.json",
                "data/no_conflict_doc_rejected_alternative.json",
                "data/conflict_stale_doc_vs_new_slack.json",
            ],
        )
        self.assertEqual(
            run_scenarios.resolve_scenario_set("meeting_minutes"),
            [
                "data/meeting_minutes_vymun_april15.md",
                "data/meeting_minutes_vymun_may13.md",
                "data/meeting_vymun_may20_todo.md",
                "data/meeting_vymun_sep30_todo.md",
                "data/block_task_vymun_todo.md",
                "data/block_task_training_notes.md",
                "data/block_task_logistics_table.md",
            ],
        )

    def test_batch_run_writes_aggregates_summary_manifest_and_four_line_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = self._run_batch(temp_dir, "--scenario-set", "new", "--run-id", "batch_new")
            run_dir = Path(temp_dir) / "runs" / "batch_new"

            self.assertEqual(
                stdout.getvalue().splitlines(),
                [
                    f"Batch run folder: {run_dir}/",
                    "Overall verdict: PASS",
                    "Failed scenarios: none",
                    f"Summary: {run_dir / 'verification_summary.md'}",
                ],
            )
            self.assertTrue((run_dir / "batch_manifest.json").exists())
            self.assertTrue((run_dir / "verification_summary.md").exists())
            self.assertTrue((run_dir / "verification_summary.csv").exists())
            self.assertFalse((run_dir / "failures").exists())
            self.assertFalse((run_dir / "new_vendor_selection").exists())

            aggregate_files = [
                "decisions_by_scenario.json",
                "raw_decisions_by_scenario.json",
                "normalized_events_by_scenario.json",
                "source_bundles_by_scenario.json",
                "candidates_by_scenario.json",
                "state_resolution_by_scenario.json",
                "conflicts_by_scenario.json",
                "meeting_metadata_by_scenario.json",
                "clean_memory_by_scenario.json",
                "action_items_by_scenario.json",
                "open_questions_by_scenario.json",
                "document_memory_debug_by_scenario.json",
                "block_classification_debug_by_scenario.json",
                "review_queue_by_scenario.json",
                "candidate_memory_by_scenario.json",
                "approved_memory_by_scenario.json",
                "rejected_memory_by_scenario.json",
                "review_actions_by_scenario.json",
                "memory_state_debug_by_scenario.json",
            ]
            for filename in aggregate_files:
                self.assertTrue((run_dir / filename).exists())

            decisions_by_scenario = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            self.assertEqual(
                set(decisions_by_scenario),
                {Path(path).stem for path in run_scenarios.resolve_scenario_set("new")},
            )
            self.assertEqual(decisions_by_scenario["new_vendor_selection"]["metadata"]["decision_count"], 1)

            manifest = json.loads((run_dir / "batch_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], "batch_new")
            self.assertEqual(manifest["scenario_set"], "new")
            self.assertEqual(manifest["pipeline"], "candidates-shadow")
            self.assertEqual(manifest["overall_verdict"], "PASS")
            self.assertEqual(manifest["failed_scenarios"], [])
            for key in (
                "batch_manifest",
                "verification_summary",
                "verification_summary_csv",
                "decisions_by_scenario",
                "raw_decisions_by_scenario",
                "normalized_events_by_scenario",
                "source_bundles_by_scenario",
                "candidates_by_scenario",
                "state_resolution_by_scenario",
                "conflicts_by_scenario",
                "meeting_metadata_by_scenario",
                "clean_memory_by_scenario",
                "action_items_by_scenario",
                "open_questions_by_scenario",
                "document_memory_debug_by_scenario",
                "block_classification_debug_by_scenario",
                "review_queue_by_scenario",
                "candidate_memory_by_scenario",
                "approved_memory_by_scenario",
                "rejected_memory_by_scenario",
                "review_actions_by_scenario",
                "memory_state_debug_by_scenario",
            ):
                self.assertIn(key, manifest["output_paths"])

            summary = (run_dir / "verification_summary.md").read_text(encoding="utf-8")
            self.assertIn("| Scenario Type | Original | New | Pair Verdict |", summary)
            self.assertIn("| new_vendor_selection | 1 | 1 | PASS | PASS | PASS | 0 | 0 | - |", summary)

            with (run_dir / "verification_summary.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                rows[0].keys(),
                {
                    "scenario_type",
                    "scenario_name",
                    "variant",
                    "expected_count",
                    "actual_count",
                    "count_pass",
                    "invariant_pass",
                    "verdict",
                    "failure_count",
                    "review_item_count",
                    "review_reasons",
                },
            )

            state_by_scenario = json.loads((run_dir / "state_resolution_by_scenario.json").read_text(encoding="utf-8"))
            new_vendor_state = state_by_scenario["new_vendor_selection"]
            self.assertIn("claim_ownership", new_vendor_state)
            self.assertIn("excluded_from_active_memory", new_vendor_state)
            self.assertTrue(
                any(
                    item["target_name"] == "ConfigCat"
                    and item["target_role"] == "rejected_alternative"
                    for item in new_vendor_state["claim_ownership"]
                )
            )
            self.assertTrue(
                any(
                    item["claim"] == "ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt."
                    and item["reason"] == "claim targets rejected alternative"
                    for item in new_vendor_state["excluded_from_active_memory"]
                )
            )

    def test_repeated_batch_run_id_uses_safe_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._run_batch(temp_dir, "--scenario-set", "new", "--run-id", "repeatable")
            stdout = self._run_batch(temp_dir, "--scenario-set", "new", "--run-id", "repeatable")

            self.assertTrue((Path(temp_dir) / "runs" / "repeatable").is_dir())
            self.assertTrue((Path(temp_dir) / "runs" / "repeatable_2").is_dir())
            self.assertIn("Batch run folder: " + str(Path(temp_dir) / "runs" / "repeatable_2") + "/", stdout.getvalue())

    def test_failed_scenario_expands_only_that_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(run_scenarios.EXPECTED_COUNTS, {"new_vendor_selection": 99}):
                stdout = self._run_batch(temp_dir, "--scenario-set", "new", "--run-id", "batch_failure")

            run_dir = Path(temp_dir) / "runs" / "batch_failure"
            failure_dir = run_dir / "failures" / "new_vendor_selection"
            self.assertEqual(stdout.getvalue().splitlines()[1], "Overall verdict: FAIL")
            self.assertEqual(stdout.getvalue().splitlines()[2], "Failed scenarios: new_vendor_selection")
            self.assertTrue((failure_dir / "decisions.json").exists())
            self.assertTrue((failure_dir / "raw_decisions.json").exists())
            self.assertTrue((failure_dir / "normalized_events_debug.json").exists())
            self.assertTrue((failure_dir / "source_bundles_debug.json").exists())
            self.assertTrue((failure_dir / "candidates_debug.json").exists())
            self.assertTrue((failure_dir / "state_resolution_debug.json").exists())
            self.assertTrue((failure_dir / "conflicts_debug.json").exists())
            self.assertTrue((failure_dir / "meeting_metadata.json").exists())
            self.assertTrue((failure_dir / "clean_memory.json").exists())
            self.assertTrue((failure_dir / "action_items.json").exists())
            self.assertTrue((failure_dir / "open_questions.json").exists())
            self.assertTrue((failure_dir / "document_memory_debug.json").exists())
            self.assertTrue((failure_dir / "block_classification_debug.json").exists())
            self.assertTrue((failure_dir / "review_queue.json").exists())
            self.assertTrue((failure_dir / "review_queue.md").exists())
            self.assertFalse((run_dir / "failures" / "new_pricing_change").exists())

            manifest = json.loads((run_dir / "batch_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["failed_scenarios"], ["new_vendor_selection"])
            self.assertEqual(manifest["overall_verdict"], "FAIL")
            self.assertIn("new_vendor_selection", (run_dir / "verification_summary.md").read_text(encoding="utf-8"))

    def test_all_scenario_batch_passes_current_deterministic_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = self._run_batch(temp_dir, "--scenario-set", "all", "--run-id", "all_batch")
            run_dir = Path(temp_dir) / "runs" / "all_batch"

            self.assertIn("Overall verdict: PASS", stdout.getvalue())
            manifest = json.loads((run_dir / "batch_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["scenario_names"]), 12)
            self.assertEqual(manifest["failed_scenarios"], [])
            decisions_by_scenario = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            self.assertEqual(len(decisions_by_scenario), 12)
            review_by_scenario = json.loads((run_dir / "review_queue_by_scenario.json").read_text(encoding="utf-8"))
            self.assertEqual(review_by_scenario["original_fake"]["metadata"]["review_item_count"], 0)
            self.assertEqual(review_by_scenario["original_fake"]["items"], [])

    def test_review_scenario_batch_passes_and_aggregates_review_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = self._run_batch(temp_dir, "--scenario-set", "review", "--run-id", "review_queue_batch")
            run_dir = Path(temp_dir) / "runs" / "review_queue_batch"

            self.assertIn("Overall verdict: PASS", stdout.getvalue())
            self.assertIn("Failed scenarios: none", stdout.getvalue())
            review_by_scenario = json.loads((run_dir / "review_queue_by_scenario.json").read_text(encoding="utf-8"))

            self.assertIn(
                "approval_missing",
                {item["reason"] for item in review_by_scenario["review_proposal_no_approval"]["items"]},
            )
            self.assertIn(
                "owner_missing",
                {item["reason"] for item in review_by_scenario["review_missing_owner"]["items"]},
            )
            self.assertTrue(
                {"ambiguous_decision", "approval_missing"}
                & {item["reason"] for item in review_by_scenario["review_low_confidence_ambiguous"]["items"]}
            )
            self.assertIn("Review Item Count", (run_dir / "verification_summary.md").read_text(encoding="utf-8"))

    def test_conflict_scenario_batch_passes_and_aggregates_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = self._run_batch(temp_dir, "--scenario-set", "conflict", "--run-id", "conflict_batch")
            run_dir = Path(temp_dir) / "runs" / "conflict_batch"

            self.assertIn("Overall verdict: PASS", stdout.getvalue())
            self.assertIn("Failed scenarios: none", stdout.getvalue())

            decisions_by_scenario = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            review_by_scenario = json.loads((run_dir / "review_queue_by_scenario.json").read_text(encoding="utf-8"))
            conflicts_by_scenario = json.loads((run_dir / "conflicts_by_scenario.json").read_text(encoding="utf-8"))

            self.assertEqual(decisions_by_scenario["conflict_slack_doc_vendor_selection"]["metadata"]["decision_count"], 0)
            self.assertEqual(decisions_by_scenario["conflict_stale_doc_vs_new_slack"]["metadata"]["decision_count"], 0)
            self.assertEqual(decisions_by_scenario["no_conflict_doc_rejected_alternative"]["metadata"]["decision_count"], 1)
            self.assertEqual(len(conflicts_by_scenario["conflict_slack_doc_vendor_selection"]), 1)
            self.assertEqual(len(conflicts_by_scenario["conflict_stale_doc_vs_new_slack"]), 1)
            self.assertEqual(conflicts_by_scenario["no_conflict_doc_rejected_alternative"], [])
            conflict_item = review_by_scenario["conflict_slack_doc_vendor_selection"]["items"][0]
            self.assertEqual(conflict_item["reason"], "conflicting_sources")
            self.assertEqual(conflict_item["severity"], "high")
            self.assertEqual({option["option"] for option in conflict_item["options"]}, {"LaunchDarkly", "ConfigCat"})

    def test_mocked_llm_vendor_batch_applies_ownership_filter_and_tradeoff_canonicalization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("run_scenarios.extract_decisions_with_llm", side_effect=self._fake_llm_with_vendor_leaks):
                stdout = self._run_batch(temp_dir, "--mode", "llm", "--scenario-set", "all", "--run-id", "mocked_llm_vendor")
            run_dir = Path(temp_dir) / "runs" / "mocked_llm_vendor"

            self.assertIn("Overall verdict: PASS", stdout.getvalue())
            self.assertIn("Failed scenarios: none", stdout.getvalue())
            decisions_by_scenario = json.loads((run_dir / "decisions_by_scenario.json").read_text(encoding="utf-8"))
            candidates_by_scenario = json.loads((run_dir / "candidates_by_scenario.json").read_text(encoding="utf-8"))
            state_by_scenario = json.loads((run_dir / "state_resolution_by_scenario.json").read_text(encoding="utf-8"))

            vendor_decision = decisions_by_scenario["vendor_selection"]["decisions"][0]
            vendor_active_claims = [
                claim["claim"]
                for field in ("reasoning", "objections", "assumptions", "risks", "tradeoffs", "timelines")
                for claim in vendor_decision[field]
            ]
            self.assertEqual(
                [claim["claim"] for claim in vendor_decision["timelines"]],
                ["Implementation can finish in about ten business days."],
            )
            self.assertEqual(
                [claim["claim"] for claim in vendor_decision["tradeoffs"]],
                ["Higher monthly spend is the accepted tradeoff."],
            )
            self.assertFalse(any("New Relic" in claim for claim in vendor_active_claims))
            self.assertFalse(any(claim == "The migration would take longer." for claim in vendor_active_claims))
            self.assertFalse(
                any(
                    claim["claim"] == "The migration would take longer because several dashboards need to be rebuilt."
                    and claim["target_name"] == "Datadog"
                    and claim["target_role"] == "chosen_option"
                    for candidate in candidates_by_scenario["vendor_selection"]
                    for claim in candidate["claims"]
                )
            )
            self.assertTrue(
                any(
                    item["claim"] == "New Relic migration would take longer because several dashboards must be rebuilt."
                    and item["target_name"] == "New Relic"
                    and item["target_role"] == "rejected_alternative"
                    for item in state_by_scenario["vendor_selection"]["excluded_from_active_memory"]
                )
            )

            new_vendor_decision = decisions_by_scenario["new_vendor_selection"]["decisions"][0]
            new_vendor_active_claims = [
                claim["claim"]
                for field in ("reasoning", "objections", "assumptions", "risks", "tradeoffs", "timelines")
                for claim in new_vendor_decision[field]
            ]
            self.assertEqual(
                [claim["claim"] for claim in new_vendor_decision["timelines"]],
                ["Implementation can finish in about eight business days."],
            )
            self.assertEqual(
                [claim["claim"] for claim in new_vendor_decision["tradeoffs"]],
                ["Higher monthly spend is the accepted tradeoff."],
            )
            self.assertIn(
                "Another expensive platform tool is a concern.",
                [claim["claim"] for claim in new_vendor_decision["objections"]],
            )
            self.assertFalse(any("Higher monthly spend" in claim["claim"] for claim in new_vendor_decision["risks"]))
            self.assertFalse(any("ConfigCat" in claim for claim in new_vendor_active_claims))
            self.assertFalse(any(claim == "The migration would take longer." for claim in new_vendor_active_claims))
            self.assertNotIn("Higher monthly spend is the accepted cost is the accepted tradeoff.", new_vendor_active_claims)
            self.assertTrue(
                any(
                    item["claim"] == "ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt."
                    and item["reason"] == "claim targets rejected alternative"
                    for item in state_by_scenario["new_vendor_selection"]["excluded_from_active_memory"]
                )
            )

    def test_new_messy_timeline_invariant_allows_owner_wording(self) -> None:
        for timeline in (
            "Priya can have the Security Center section wired by next Thursday.",
            "Security Center section can be wired by next Thursday.",
            "Have the Security Center section wired by next Thursday.",
        ):
            with self.subTest(timeline=timeline):
                verification = self._verify_new_messy_timelines([timeline])

                self.assertEqual(verification.verdict, "PASS")

    def test_new_messy_timeline_invariant_fails_without_core_timeline(self) -> None:
        verification = self._verify_new_messy_timelines(["Priya can finish the IA work soon."])

        self.assertEqual(verification.verdict, "FAIL")
        self.assertTrue(
            any(failure["invariant"] == "core implementation timeline kept" for failure in verification.invariant_failures)
        )

    def test_new_messy_timeline_invariant_rejects_dependency_order_timelines(self) -> None:
        for bad_timeline in (
            "API tokens can follow after Owen's piece.",
            "Move API tokens after Owen's piece.",
            "SCIM can follow later.",
            "API tokens once route exists.",
        ):
            with self.subTest(bad_timeline=bad_timeline):
                verification = self._verify_new_messy_timelines(
                    [
                        "Priya can have the Security Center section wired by next Thursday.",
                        bad_timeline,
                    ]
                )

                self.assertEqual(verification.verdict, "FAIL")
                self.assertTrue(
                    any(failure["invariant"] == "dependency/order timeline excluded" for failure in verification.invariant_failures)
                )

    def _run_batch(self, temp_dir: str, *extra_args: str) -> StringIO:
        stdout = StringIO()
        argv = [
            "run_scenarios.py",
            "--mode",
            "heuristic",
            "--pipeline",
            "candidates-shadow",
            "--output-root",
            str(Path(temp_dir) / "runs"),
            *extra_args,
        ]
        with (
            patch("sys.argv", argv),
            patch("sys.stdout", stdout),
        ):
            run_scenarios.main()
        return stdout

    def _verify_new_messy_timelines(self, timelines: list[str]) -> run_scenarios.ScenarioVerification:
        artifacts = run_scenarios.ScenarioArtifacts(
            scenario_name="new_messy_implicit",
            input_file="data/new_messy_implicit.json",
            clean_payload={
                "metadata": {"decision_count": 1},
                "decisions": [
                    {
                        "decision": "Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.",
                        "timelines": [{"claim": timeline} for timeline in timelines],
                    }
                ],
            },
            raw_payload={},
            normalized_events=[],
            source_bundles=[],
            candidates=[],
            state_resolution={},
        )
        verification = run_scenarios.ScenarioVerification(
            scenario_name="new_messy_implicit",
            scenario_type="messy",
            variant="new",
            expected_count=1,
            actual_count=1,
            count_pass=True,
        )
        run_scenarios._verify_messy_scenario(artifacts, verification)
        return verification

    def _fake_llm_with_vendor_leaks(self, grouped_threads, event_lookup, model, max_threads):
        if "1782295440.000500" in event_lookup:
            event = event_lookup["1782295440.000500"]
            decision = DecisionRecord(
                decision_id="dec_datadog_llm_leak",
                decision="Choose Datadog for observability now.",
                date=event.timestamp,
                owner="Priya Shah",
                owner_role="Staff Engineer",
                participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
                status="confirmed",
                confidence=0.86,
                objections=[
                    Claim(claim="New Relic is cheaper by about 18%.", source_message_ts="1782295260.000200"),
                    Claim(claim="The migration would take longer.", source_message_ts="1782295260.000200"),
                ],
                timelines=[
                    Claim(
                        claim="The migration would take longer because several dashboards need to be rebuilt.",
                        source_message_ts="1782295260.000200",
                    ),
                    Claim(claim="Implementation can finish in about ten business days.", source_message_ts="1782295380.000400"),
                ],
                tradeoffs=[
                    Claim(
                        claim="The higher monthly spend is a cost of choosing Datadog is the accepted tradeoff.",
                        source_message_ts="1782295260.000200",
                    )
                ],
                decision_source_message_ts=event.message_ts,
            )
            return [attach_sources(decision, event_lookup)]
        if "1791030240.000500" in event_lookup:
            event = event_lookup["1791030240.000500"]
            decision = DecisionRecord(
                decision_id="dec_launchdarkly_llm_spend_leak",
                decision="Choose LaunchDarkly for feature flags now.",
                date=event.timestamp,
                owner="Priya Shah",
                owner_role="Staff Engineer",
                participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
                status="confirmed",
                confidence=0.86,
                objections=[
                    Claim(claim="ConfigCat is cheaper by about 22%.", source_message_ts="1791030060.000200"),
                    Claim(claim="The migration would take longer.", source_message_ts="1791030060.000200"),
                    Claim(claim="Another expensive platform tool is a concern.", source_message_ts="1791030120.000300"),
                ],
                risks=[
                    Claim(claim="Higher monthly spend is the accepted cost.", source_message_ts="1791030060.000200"),
                ],
                timelines=[
                    Claim(
                        claim="ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt.",
                        source_message_ts="1791030060.000200",
                    ),
                    Claim(claim="Implementation can finish in about eight business days.", source_message_ts="1791030180.000400"),
                ],
                tradeoffs=[
                    Claim(
                        claim="Higher monthly spend is the accepted cost is the accepted tradeoff.",
                        source_message_ts="1791030060.000200",
                    ),
                    Claim(claim="LaunchDarkly has higher monthly spend.", source_message_ts="1791030060.000200"),
                ],
                decision_source_message_ts=event.message_ts,
            )
            return [attach_sources(decision, event_lookup)]
        return extract_decisions_from_events(grouped_threads, event_lookup, max_threads)


if __name__ == "__main__":
    unittest.main()
