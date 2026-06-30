from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.heuristic_extractor import extract_decisions_from_events
from src.llm_extractor import LLM_OUTPUT_SCHEMA, SYSTEM_PROMPT
from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json
from src.source_resolver import build_event_lookup
from src.validators import postprocess_decisions


class ScenarioFixtureTest(unittest.TestCase):
    def test_original_fixture_still_produces_clean_soc2_and_redis_decisions(self) -> None:
        processed = self._processed("data/original_fake.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 2)
        launch = next(decision for decision in decisions if "SOC 2" in decision.decision)
        redis = next(decision for decision in decisions if "Redis" in decision.decision)

        self.assertEqual(launch.status, "confirmed")
        self.assertIn("enterprise launch", launch.decision)
        self.assertEqual(launch.revisit_date, "2026-09-12")
        self.assertEqual(launch.deadlines, [])
        self.assertTrue(all("revisit" not in item.action.lower() for item in launch.action_items))
        self.assertIn("Two pilot customers said security review will block the launch without SOC 2", self._claims(launch, "reasoning"))
        self.assertNotIn("Security review will block the launch without SOC 2", self._claims(launch, "reasoning"))
        self.assertIn("Without audit logs and SSO, security review will probably fail", self._claims(launch, "reasoning"))
        self.assertTrue(all("safer" not in claim.lower() for claim in self._claims(launch, "reasoning")))
        self.assertIn("Engineering needs about four more weeks", self._claims(launch, "timelines"))
        self.assertNotIn("Engineering needs about four more weeks", self._claims(launch, "reasoning"))
        self.assertIn("The delay could hurt Q3 pipeline", self._claims(launch, "objections"))
        self.assertIn("The pilots will wait", self._claims(launch, "assumptions"))

        self.assertEqual(redis.revisit_trigger, "After launch if Redis becomes a bottleneck.")
        self.assertEqual(redis.action_items, [])
        self.assertIn("Postgres latency is too high under load", self._claims(redis, "reasoning"))
        self.assertIn("Extra infrastructure complexity is the accepted tradeoff.", self._claims(redis, "tradeoffs"))
        self.assertIn("Redis could become a bottleneck later", self._claims(redis, "risks"))

    def test_pricing_fixture_classifies_effective_date_objection_tradeoff_and_trigger(self) -> None:
        processed = self._processed("data/pricing_change.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 1)
        pricing = decisions[0]
        self.assertEqual(pricing.status, "confirmed")
        self.assertEqual(pricing.effective_date, "2026-08-01")
        self.assertNotEqual(pricing.date, "2026-08-01")
        self.assertEqual(pricing.revisit_trigger, "After the first billing cycle.")
        self.assertIn("There is concern about surprising existing customers", self._claims(pricing, "objections"))
        self.assertNotIn("There is concern about surprising existing customers", self._claims(pricing, "assumptions"))
        self.assertIn("Gross margin on Pro is below target once onboarding calls are included", self._claims(pricing, "reasoning"))
        self.assertIn("Losing some price-sensitive solo users is the accepted tradeoff.", self._claims(pricing, "tradeoffs"))
        self.assertTrue(all("price-sensitive solo users" not in claim for claim in self._claims(pricing, "risks")))
        self.assertTrue(any("grandfather current Pro users" in claim for claim in self._claims(pricing, "assumptions")))
        self.assertTrue(all("Raise Pro pricing" not in claim for claim in self._claims(pricing, "timelines")))
        self.assertTrue(all("review churn" not in claim.lower() for claim in self._claims(pricing, "timelines")))

    def test_mobile_fixture_outputs_zero_clean_decisions(self) -> None:
        processed = self._processed("data/mobile_app.json")

        self.assertEqual(len(processed.raw_decisions), 0)
        self.assertEqual(processed.clean_decisions, [])

    def test_vendor_fixture_classifies_deadline_timeline_assumption_and_tradeoff(self) -> None:
        processed = self._processed("data/vendor_selection.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 1)
        vendor = decisions[0]
        self.assertEqual(vendor.status, "confirmed")
        self.assertEqual(vendor.owner, "Priya Shah")
        self.assertIsNone(vendor.revisit_date)
        self.assertEqual(vendor.revisit_trigger, "After 30 days if alert noise is still high.")
        self.assertIn("Datadog already supports our Kubernetes metrics", self._claims(vendor, "reasoning"))
        self.assertIn("The Terraform module is ready", self._claims(vendor, "reasoning"))
        self.assertIn("The outage review showed better Kubernetes alerts are needed", self._claims(vendor, "reasoning"))
        self.assertIn("There is concern about locking into another expensive vendor", self._claims(vendor, "objections"))
        self.assertTrue(all("New Relic is cheaper" not in claim for claim in self._claims(vendor, "objections")))
        self.assertIn("Finance approves the higher monthly spend", self._claims(vendor, "assumptions"))
        self.assertIn("Higher monthly spend is the accepted tradeoff.", self._claims(vendor, "tradeoffs"))
        self.assertTrue(all("higher monthly spend" not in claim.lower() for claim in self._claims(vendor, "risks")))
        self.assertNotIn("New Relic is cheaper by about 18% is the accepted tradeoff.", self._claims(vendor, "tradeoffs"))
        self.assertIn("Implementation can finish in about ten business days", self._claims(vendor, "timelines"))
        self.assertEqual(self._claims(vendor, "timelines"), ["Implementation can finish in about ten business days"])
        self.assertTrue(all("migration would take longer" not in claim.lower() for claim in self._claims(vendor, "timelines")))
        self.assertNotIn("Implementation can finish in about ten business days", self._claims(vendor, "assumptions"))
        self.assertTrue(all("review alert quality" not in claim.lower() for claim in self._claims(vendor, "timelines")))
        self.assertEqual([(deadline.date, deadline.description) for deadline in vendor.deadlines], [("2026-07-15", "Enterprise SLA starts.")])
        self.assertTrue(vendor.deadlines[0].source)
        self.assertTrue(all("review alert quality" not in item.action.lower() for item in vendor.action_items))
        self.assertTrue(all("implement datadog" not in item.action.lower() for item in vendor.action_items))

    def test_messy_fixture_extracts_one_confirmed_implicit_organization_settings_decision(self) -> None:
        processed = self._processed("data/messy.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.status, "confirmed")
        self.assertIn("Organization Settings", decision.decision)
        self.assertTrue(decision.decision.endswith("."))
        self.assertNotIn("billing settings page", decision.decision.lower())
        self.assertNotIn("permissions redesign", decision.decision.lower())
        self.assertEqual(decision.owner, "Priya Shah")
        self.assertIsNone(decision.revisit_date)
        self.assertIsNone(decision.revisit_trigger)
        self.assertIn("Enterprise admins expect one place called something like Organization Settings", self._claims(decision, "reasoning"))
        self.assertIn("When we show separate pages during demos, it looks unfinished", self._claims(decision, "reasoning"))
        self.assertIn("Billing-only shortcut saves maybe 3 days and creates another cleanup project", self._claims(decision, "reasoning"))
        self.assertIn("It matches where the product is going", self._claims(decision, "reasoning"))
        self.assertIn("Safer than hiding SAML under billing", self._claims(decision, "reasoning"))
        self.assertIn("There is concern about slipping the admin beta", self._claims(decision, "objections"))
        self.assertEqual([(deadline.date, deadline.description) for deadline in decision.deadlines], [("2026-08-05", "Admin beta for design partners.")])
        self.assertIn("The new section can be wired by next Friday", self._claims(decision, "timelines"))
        self.assertIn("Accepting a basic IA now instead of doing the full permissions model is the accepted tradeoff.", self._claims(decision, "tradeoffs"))
        action_pairs = {(item.owner, item.action) for item in decision.action_items}
        self.assertIn(("Priya Shah", "Take the nav work."), action_pairs)
        self.assertIn(("Priya Shah", "Make Organization Settings the parent section and move billing and roles first, then SAML."), action_pairs)
        self.assertIn(("Owen Diaz", "Move audit logs once the route exists."), action_pairs)
        self.assertEqual(sum("audit logs" in item.action.lower() for item in decision.action_items), 1)
        all_text = " ".join(
            [
                decision.decision,
                *[claim.claim for claim in decision.all_claims()],
                *[item.action for item in decision.action_items],
            ]
        ).lower()
        self.assertNotIn("coffee machine", all_text)
        self.assertNotIn("customer call", all_text)

    def test_clean_output_has_no_review_flags_unsupported_claims_revisit_leaks_or_duplicates(self) -> None:
        for path in (
            "data/original_fake.json",
            "data/pricing_change.json",
            "data/mobile_app.json",
            "data/vendor_selection.json",
            "data/decision_reversal.json",
            "data/messy.json",
        ):
            with self.subTest(path=path):
                processed = self._processed(path)
                for decision in processed.clean_decisions:
                    claims = decision.all_claims()
                    self.assertTrue(all(claim.support_level != "unsupported" for claim in claims))
                    self.assertTrue(all(not claim.needs_review for claim in claims))
                    self.assertTrue(all(not self._is_revisit_only_claim(claim.claim) for claim in claims))
                    normalized = [self._normalize(claim.claim) for claim in claims]
                    self.assertEqual(len(normalized), len(set(normalized)))

    def test_llm_schema_and_prompt_include_new_public_fields(self) -> None:
        decision_schema = LLM_OUTPUT_SCHEMA["properties"]["decisions"]["items"]
        for field in ("effective_date", "deadlines", "action_items", "timelines", "revisit_trigger"):
            self.assertIn(field, decision_schema["properties"])
            self.assertIn(field, decision_schema["required"])

        self.assertIn("Use effective_date", SYSTEM_PROMPT)
        self.assertIn("Use deadlines", SYSTEM_PROMPT)
        self.assertIn("Use action_items", SYSTEM_PROMPT)
        self.assertIn("Return {\"decisions\": []} for unresolved or deferred threads", SYSTEM_PROMPT)
        self.assertIn("later explicit reversal or replacement", SYSTEM_PROMPT)
        self.assertIn("Implicit decisions are valid only when a concrete plan has convergence", SYSTEM_PROMPT)

    def test_decision_reversal_keeps_only_latest_active_postmark_decision(self) -> None:
        processed = self._processed("data/decision_reversal.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertIn("Postmark", decision.decision)
        self.assertNotEqual(decision.decision, "Use SendGrid for lifecycle emails")
        self.assertEqual(decision.status, "confirmed")
        self.assertEqual(decision.owner, "Marco Silva")
        self.assertIsNone(decision.revisit_date)
        self.assertIsNone(decision.revisit_trigger)
        self.assertEqual(decision.tradeoffs, [])
        self.assertEqual(decision.risks, [])
        self.assertEqual([(deadline.date, deadline.description) for deadline in decision.deadlines], [("2026-08-20", "Onboarding launch.")])
        reasoning = " ".join(self._claims(decision, "reasoning")).lower()
        self.assertTrue("deliverability" in reasoning or "dedicated ip" in reasoning)
        self.assertTrue(any("march campaign" in claim.lower() for claim in self._claims(decision, "reasoning")))
        self.assertTrue(any("dedicated ip" in claim.lower() for claim in self._claims(decision, "reasoning")))
        self.assertTrue(any("password-reset emails" in claim for claim in self._claims(decision, "objections")))

    def test_new_fixture_counts_match_expected(self) -> None:
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

    def test_new_general_workability_fixture_produces_two_explicit_decisions(self) -> None:
        processed = self._processed("data/new_general_workability.json")
        decisions = processed.clean_decisions

        self.assertEqual(len(decisions), 2)
        self.assertTrue(any("analytics dashboard beta" in decision.decision for decision in decisions))
        self.assertTrue(any("S3 pre-signed URLs" in decision.decision for decision in decisions))
        self.assertTrue(all(decision.date == "2026-10-03" for decision in decisions))
        self.assertTrue(all(decision.date != "2026-08-20" for decision in decisions))
        launch = next(decision for decision in decisions if "analytics dashboard beta" in decision.decision)
        self.assertTrue(all("safer" not in claim.lower() for claim in self._claims(launch, "reasoning")))

    def test_new_no_decision_fixture_stays_empty(self) -> None:
        processed = self._processed("data/new_no_decision.json")

        self.assertEqual(processed.raw_decisions, [])
        self.assertEqual(processed.clean_decisions, [])

    def test_new_pricing_fixture_preserves_direct_onboarding_reasoning(self) -> None:
        processed = self._processed("data/new_pricing_change.json")
        decision = processed.clean_decisions[0]

        self.assertEqual(len(processed.clean_decisions), 1)
        self.assertEqual(
            decision.decision,
            "Move Team pricing to $59 for new customers starting 2026-09-01, grandfather existing Team customers for 4 months",
        )
        self.assertEqual(decision.effective_date, "2026-09-01")
        self.assertIn(
            "Onboarding time per Team account is higher than modeled.",
            self._claims(decision, "reasoning"),
        )

    def test_new_vendor_selection_fixture_keeps_chosen_timeline_only(self) -> None:
        processed = self._processed("data/new_vendor_selection.json")
        decision = processed.clean_decisions[0]

        self.assertEqual(decision.decision, "Choose LaunchDarkly for feature flags now")
        self.assertEqual(self._claims(decision, "timelines"), ["Implementation can finish in about eight business days"])
        self.assertEqual(self._claims(decision, "tradeoffs"), ["Higher monthly spend is the accepted tradeoff."])
        self.assertNotIn(
            "ConfigCat is cheaper by about 22%, but migration would take longer is the accepted tradeoff.",
            self._claims(decision, "tradeoffs"),
        )
        self.assertEqual([(deadline.date, deadline.description) for deadline in decision.deadlines], [("2026-09-18", "Enterprise rollout starts.")])
        self.assertIn(
            "There is concern about adding another expensive platform tool",
            self._claims(decision, "objections"),
        )

    def test_new_decision_reversal_fixture_keeps_only_segment_decision(self) -> None:
        processed = self._processed("data/new_decision_reversal.json")
        decision = processed.clean_decisions[0]

        self.assertEqual(len(processed.clean_decisions), 1)
        self.assertIn("Segment", decision.decision)
        self.assertIsNone(decision.revisit_trigger)
        self.assertEqual(decision.assumptions, [])
        self.assertEqual(decision.risks, [])
        self.assertTrue(any(deadline.date == "2026-09-25" for deadline in decision.deadlines))

    def test_new_messy_implicit_fixture_extracts_security_center_decision(self) -> None:
        processed = self._processed("data/new_messy_implicit.json")
        decision = processed.clean_decisions[0]

        self.assertEqual(len(processed.clean_decisions), 1)
        self.assertEqual(decision.decision, "Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.")
        self.assertEqual(self._claims(decision, "timelines"), ["The Security Center section can be wired by next Thursday"])
        self.assertNotIn("Move API tokens after Owen's piece.", self._claims(decision, "timelines"))
        self.assertEqual(
            self._claims(decision, "tradeoffs"),
            ["Accepting a basic security IA now instead of doing the full permissions model is the accepted tradeoff."],
        )
        self.assertNotIn("No custom permission scopes in this pass is the accepted tradeoff.", self._claims(decision, "tradeoffs"))
        self.assertEqual(
            [(deadline.date, deadline.description) for deadline in decision.deadlines],
            [("2026-09-10", "Updated admin flow promised to design partners before the demo.")],
        )
        self.assertNotIn(
            "The updated admin flow is needed before the Sep 10 demo.",
            self._claims(decision, "objections"),
        )
        self.assertIn(("Priya Shah", "Take the nav work."), {(item.owner, item.action) for item in decision.action_items})
        self.assertIn(("Owen Diaz", "Move API tokens once the route exists."), {(item.owner, item.action) for item in decision.action_items})
        self.assertIn(
            ("Priya Shah", "Make Security Center the parent section and move SSO and audit exports first, then SCIM."),
            {(item.owner, item.action) for item in decision.action_items},
        )

    def test_run_script_targets_new_six_fixtures(self) -> None:
        script = Path("run_all_scenarios_concat.sh").read_text(encoding="utf-8")

        self.assertIn("new_general_workability:data/new_general_workability.json", script)
        self.assertIn("new_no_decision:data/new_no_decision.json", script)
        self.assertIn("new_pricing_change:data/new_pricing_change.json", script)
        self.assertIn("new_vendor_selection:data/new_vendor_selection.json", script)
        self.assertIn("new_decision_reversal:data/new_decision_reversal.json", script)
        self.assertIn("new_messy_implicit:data/new_messy_implicit.json", script)
        self.assertNotIn("original_fake:data/original_fake.json", script)

    def _processed(self, path: str):
        events = parse_slack_json(load_slack_json(path))
        lookup = build_event_lookup(events)
        raw = extract_decisions_from_events(group_events_by_thread(events), lookup)
        return postprocess_decisions(raw, lookup)

    def _claims(self, decision, category: str) -> list[str]:
        return [claim.claim for claim in getattr(decision, category)]

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    def _is_revisit_only_claim(self, text: str) -> bool:
        normalized = text.lower().strip()
        return normalized.startswith(("revisit ", "let's revisit", "we can revisit", "review churn", "review alert quality"))


if __name__ == "__main__":
    unittest.main()
