from __future__ import annotations

import unittest

from src.heuristic_extractor import extract_decisions_from_events
from src.schemas import ActionItem, Claim, Deadline, DecisionRecord
from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json
from src.source_resolver import attach_sources, build_event_lookup, resolve_source
from src.validators import (
    contains_unexpected_language_artifact,
    finalize_clean_tradeoff_canonicalization,
    normalize_tradeoff_claim,
    postprocess_decisions,
    validate_decisions,
)


class ValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        events = parse_slack_json(load_slack_json("data/original_fake.json"))
        self.lookup = build_event_lookup(events)
        self.grouped = group_events_by_thread(events)

    def test_valid_heuristic_output_passes(self) -> None:
        decisions = extract_decisions_from_events(self.grouped, self.lookup)
        valid, issues = validate_decisions(decisions, self.lookup)
        errors = [issue for issue in issues if issue.severity == "error"]

        self.assertEqual(len(valid), 2)
        self.assertEqual(errors, [])

    def test_rejects_source_free_claims(self) -> None:
        decision = DecisionRecord(
            decision_id="dec_test",
            decision="Use Redis",
            date="2026-06-23",
            owner="Leo Park",
            owner_role="CTO",
            participants=["Leo Park"],
            sources=[resolve_source("1782202000.000100", self.lookup)],
            reasoning=[Claim(claim="This has no source.")],
        )
        valid, issues = validate_decisions([decision], self.lookup)

        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].reasoning, [])
        self.assertTrue(any(issue.severity == "error" for issue in issues))

    def test_downgrades_unconfirmed_status(self) -> None:
        event = self.lookup["1782203000.000100"]
        decision = DecisionRecord(
            decision_id="dec_test",
            decision="Lunch at noon",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            sources=[resolve_source(event.message_ts, self.lookup)],
            decision_source_message_ts=event.message_ts,
        )
        valid, _issues = validate_decisions([decision], self.lookup)

        self.assertEqual(valid[0].status, "candidate")

    def test_unsupported_claim_is_removed_from_clean_but_kept_raw(self) -> None:
        decision = self._redis_decision(
            assumptions=[
                Claim(
                    claim="The two pilot customers will wait.",
                    source_message_ts="1782201120.000300",
                    support_level="unsupported",
                    confidence=0.25,
                    needs_review=True,
                )
            ]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual(len(processed.raw_decisions[0].assumptions), 1)
        self.assertEqual(processed.clean_decisions[0].assumptions, [])
        self.assertGreater(len(processed.raw_issues), 0)
        self.assertEqual(processed.clean_issues, [])
        self.assertEqual(processed.removed_claim_count, 1)
        self.assertTrue(any(issue.type == "unsupported_claim" for issue in processed.raw_issues))

    def test_include_review_claims_keeps_review_claims_and_counts_issues(self) -> None:
        decision = self._redis_decision(
            assumptions=[
                Claim(
                    claim="The two pilot customers will wait.",
                    source_message_ts="1782201120.000300",
                    support_level="unsupported",
                    confidence=0.25,
                    needs_review=True,
                )
            ]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup, include_review_claims=True)

        self.assertEqual(len(processed.clean_decisions[0].assumptions), 1)
        self.assertTrue(processed.clean_decisions[0].assumptions[0].needs_review)
        self.assertGreater(len(processed.clean_issues), 0)
        self.assertEqual(processed.removed_claim_count, 0)

    def test_mixed_language_claim_is_flagged_and_removed(self) -> None:
        claim_text = "The decision स्वीकारs extra infrastructure complexity"
        decision = self._redis_decision(
            tradeoffs=[
                Claim(
                    claim=claim_text,
                    source_message_ts="1782202060.000200",
                    support_level="direct",
                    confidence=0.8,
                )
            ]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertTrue(contains_unexpected_language_artifact(claim_text))
        self.assertTrue(any(issue.type == "unexpected_language" for issue in processed.raw_issues))
        self.assertEqual(processed.clean_decisions[0].tradeoffs, [])

    def test_source_fields_are_split(self) -> None:
        decisions = extract_decisions_from_events(self.grouped, self.lookup)
        redis = next(decision for decision in decisions if "Redis" in decision.decision)
        output = redis.to_dict()

        self.assertNotIn("sources", output)
        self.assertIn("decision_sources", output)
        self.assertIn("supporting_sources", output)
        self.assertIn("all_sources", output)
        self.assertTrue(output["decision_sources"])
        self.assertTrue(output["supporting_sources"])
        all_message_ts = {source["message_ts"] for source in output["all_sources"]}
        self.assertIn(output["decision_sources"][0]["message_ts"], all_message_ts)

    def test_clean_output_has_no_unsupported_needs_review_or_invalid_sources(self) -> None:
        decision = self._redis_decision(
            reasoning=[
                Claim(
                    claim="Postgres latency is too high under load",
                    source_message_ts="1782202000.000100",
                    support_level="direct",
                    confidence=0.9,
                )
            ],
            assumptions=[
                Claim(
                    claim="This should not survive.",
                    source_message_ts="1782201120.000300",
                    support_level="unsupported",
                    confidence=0.2,
                    needs_review=True,
                ),
                Claim(
                    claim="This source does not exist.",
                    source_message_ts="missing",
                    support_level="direct",
                    confidence=0.8,
                ),
            ],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)
        clean_claims = processed.clean_decisions[0].all_claims()

        self.assertTrue(clean_claims)
        self.assertTrue(all(claim.support_level != "unsupported" for claim in clean_claims))
        self.assertTrue(all(not claim.needs_review for claim in clean_claims))
        self.assertTrue(all(claim.source and claim.source.message_ts in self.lookup for claim in clean_claims))

    def test_tradeoff_normalizer_simplifies_common_phrase(self) -> None:
        self.assertEqual(
            normalize_tradeoff_claim("The decision accepts extra infrastructure complexity in exchange for lower latency."),
            "Extra infrastructure complexity is the accepted tradeoff.",
        )

    def test_messy_llm_output_is_repaired_for_presentation_quality(self) -> None:
        events = parse_slack_json(load_slack_json("data/messy.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_messy_llm",
            decision="Create an Organization Settings section as the parent for billing, roles, SAML, and audit logs, instead of putting SAML under billing.",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Maya Chen", "Leo Park", "Priya Shah", "Sarah Kim", "Owen Diaz"],
            status="confirmed",
            confidence=0.86,
            decision_source_message_ts="1782460720.001300",
            reasoning=[
                Claim(claim="It matches where the product is going.", source_message_ts="1782460360.000700", support_level="direct", confidence=0.93),
                Claim(claim="It avoids the cleanup project that the billing-only shortcut would create.", source_message_ts="1782460360.000700", support_level="direct", confidence=0.9),
            ],
            risks=[
                Claim(claim="A basic IA now is safer than hiding SAML under billing.", source_message_ts="1782460780.001400", support_level="direct", confidence=0.9),
            ],
            tradeoffs=[
                Claim(
                    claim="Accepting a basic IA now instead of doing the full permissions model is the accepted tradeoff.",
                    source_message_ts="1782460780.001400",
                    support_level="direct",
                    confidence=0.98,
                ),
                Claim(
                    claim="The billing-only shortcut saves about 3 days is the accepted tradeoff.",
                    source_message_ts="1782460360.000700",
                    support_level="direct",
                    confidence=0.92,
                ),
            ],
            timelines=[
                Claim(claim="The new section can be wired by next Friday.", source_message_ts="1782460480.000900", support_level="direct", confidence=0.98),
                Claim(claim="Audit logs can follow after Owen's piece.", source_message_ts="1782460660.001200", support_level="direct", confidence=0.94),
            ],
            deadlines=[
                Deadline(date="2026-08-05", description="Admin beta promised to design partners.", source_message_ts="1782460420.000800")
            ],
            action_items=[
                ActionItem(action="Take the nav work.", owner="Priya Shah", due_date=None, due_text="next Friday", source_message_ts="1782460600.001100"),
                ActionItem(action="Move audit logs once the route exists.", owner="Owen Diaz", due_date=None, due_text=None, source_message_ts="1782460600.001100"),
                ActionItem(
                    action="Make Organization Settings the parent section and move billing and roles first, then SAML.",
                    owner="Priya Shah",
                    due_date=None,
                    due_text=None,
                    source_message_ts="1782460660.001200",
                ),
            ],
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.decision, "Create an Organization Settings section and move billing, roles, SAML, and audit logs under it.")
        self.assertEqual(clean.decision_sources[0].message_ts, "1782460660.001200")
        self.assertIn("There is concern about slipping the admin beta.", [claim.claim for claim in clean.objections])
        self.assertIn("A basic IA now is safer than hiding SAML under billing.", [claim.claim for claim in clean.reasoning])
        self.assertEqual(clean.risks, [])
        self.assertEqual(
            [claim.claim for claim in clean.tradeoffs],
            ["Accepting a basic IA now instead of doing the full permissions model is the accepted tradeoff."],
        )
        self.assertEqual([claim.claim for claim in clean.timelines], ["The new section can be wired by next Friday."])
        self.assertIn(
            ("Priya Shah", "Make Organization Settings the parent section and move billing and roles first, then SAML."),
            [(item.owner, item.action) for item in clean.action_items],
        )
        self.assertEqual([claim.claim for claim in processed.raw_decisions[0].risks], ["A basic IA now is safer than hiding SAML under billing."])
        self.assertEqual(processed.raw_decisions[0].decision_sources[0].message_ts, "1782460720.001300")

    def test_vague_soc2_reasoning_is_removed_when_concrete_reasoning_and_assumption_exist(self) -> None:
        decision = self._launch_decision(
            reasoning=[
                Claim(claim="This is safer if the pilots will wait.", source_message_ts="1782201120.000300", support_level="direct", confidence=0.9),
                Claim(claim="It is safer to delay.", source_message_ts="1782201180.000400", support_level="direct", confidence=0.9),
                Claim(
                    claim="Two pilot customers said security review will block the launch without SOC 2.",
                    source_message_ts="1782201000.000100",
                    support_level="direct",
                    confidence=0.95,
                ),
                Claim(
                    claim="Security review will probably fail without audit logs and SSO.",
                    source_message_ts="1782201060.000200",
                    support_level="direct",
                    confidence=0.94,
                ),
            ],
            assumptions=[Claim(claim="The pilots will wait.", source_message_ts="1782201120.000300", support_level="direct", confidence=0.9)],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(
            [claim.claim for claim in clean.reasoning],
            [
                "Two pilot customers said security review will block the launch without SOC 2.",
                "Security review will probably fail without audit logs and SSO.",
            ],
        )
        self.assertEqual([claim.claim for claim in clean.assumptions], ["The pilots will wait."])

    def test_semantic_action_item_dedupe_keeps_clearer_audit_logs_dependency(self) -> None:
        events = parse_slack_json(load_slack_json("data/messy.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_messy_actions",
            decision="Create an Organization Settings section and move billing, roles, SAML, and audit logs under it",
            date=None,
            owner="Priya Shah",
            owner_role=None,
            participants=["Priya Shah", "Owen Diaz"],
            status="confirmed",
            decision_source_message_ts="1782460660.001200",
            action_items=[
                ActionItem(action="Move audit logs after Owen's piece.", owner="Owen Diaz", due_date=None, due_text=None, source_message_ts="1782460660.001200"),
                ActionItem(action="Move audit logs once the route exists.", owner="Owen Diaz", due_date=None, due_text=None, source_message_ts="1782460600.001100"),
            ],
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        clean_actions = [(item.owner, item.action) for item in processed.clean_decisions[0].action_items]
        self.assertIn(("Owen Diaz", "Move audit logs once the route exists."), clean_actions)
        self.assertEqual(sum("audit logs" in action.lower() for _owner, action in clean_actions), 1)

    def test_unsupported_iso_revisit_date_is_cleared(self) -> None:
        decision = self._redis_decision()
        decision.revisit_date = "2026-09-12"
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertIsNone(processed.clean_decisions[0].revisit_date)
        self.assertEqual(processed.clean_decisions[0].revisit_trigger, "After launch if Redis becomes a bottleneck.")

    def test_non_date_revisit_phrase_moves_to_trigger(self) -> None:
        decision = self._redis_decision()
        decision.revisit_date = "after launch"
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertIsNone(processed.clean_decisions[0].revisit_date)
        self.assertEqual(processed.clean_decisions[0].revisit_trigger, "After launch.")
        self.assertEqual(processed.raw_decisions[0].revisit_date, "after launch")

    def test_absent_revisit_fields_are_null(self) -> None:
        decision = self._redis_decision()
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertIsNone(processed.clean_decisions[0].revisit_date)
        self.assertIsNone(processed.clean_decisions[0].revisit_trigger)

    def test_revisit_date_claim_is_removed_from_clean_assumptions(self) -> None:
        decision = self._launch_decision(
            assumptions=[Claim(claim="Revisit on 2026-09-12.", source_message_ts="1782201180.000400")]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual([claim.claim for claim in processed.raw_decisions[0].assumptions], ["Revisit on 2026-09-12."])
        self.assertEqual(processed.clean_decisions[0].assumptions, [])
        self.assertEqual(processed.clean_decisions[0].revisit_date, "2026-09-12")

    def test_revisit_only_claims_are_removed_from_all_clean_claim_arrays(self) -> None:
        decision = self._launch_decision(
            reasoning=[Claim(claim="Let's revisit on 2026-09-12.", source_message_ts="1782201180.000400")],
            objections=[Claim(claim="Revisit after launch.", source_message_ts="1782202120.000300")],
            assumptions=[Claim(claim="Revisit on 2026-09-12.", source_message_ts="1782201180.000400")],
            risks=[Claim(claim="We can revisit the design after launch.", source_message_ts="1782202120.000300")],
            tradeoffs=[Claim(claim="Revisit after launch.", source_message_ts="1782202120.000300")],
            timelines=[
                Claim(claim="Revisit after launch.", source_message_ts="1782202120.000300"),
                Claim(claim="Engineering needs about four more weeks.", source_message_ts="1782201060.000200"),
            ],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)
        clean = processed.clean_decisions[0]
        clean_claim_texts = [claim.claim for claim in clean.all_claims()]

        self.assertEqual(clean.revisit_date, "2026-09-12")
        self.assertEqual(clean.revisit_trigger, "After launch.")
        self.assertNotIn("Revisit on 2026-09-12.", clean_claim_texts)
        self.assertNotIn("Let's revisit on 2026-09-12.", clean_claim_texts)
        self.assertNotIn("Revisit after launch.", clean_claim_texts)
        self.assertNotIn("We can revisit the design after launch.", clean_claim_texts)
        self.assertEqual([claim.claim for claim in clean.timelines], ["Engineering needs about four more weeks."])
        self.assertIn("Revisit after launch.", [claim.claim for claim in processed.raw_decisions[0].timelines])

    def test_revisit_deadline_duplicate_is_removed_from_clean_but_kept_raw(self) -> None:
        decision = self._launch_decision()
        decision.revisit_date = "2026-09-12"
        decision.deadlines = [
            Deadline(
                date="2026-09-12",
                description="Revisit the decision.",
                source_message_ts="1782201180.000400",
            )
        ]
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual([(deadline.date, deadline.description) for deadline in processed.raw_decisions[0].deadlines], [("2026-09-12", "Revisit the decision.")])
        self.assertEqual(processed.clean_decisions[0].revisit_date, "2026-09-12")
        self.assertEqual(processed.clean_decisions[0].deadlines, [])

    def test_revisit_action_item_duplicate_is_removed_from_clean_but_kept_raw(self) -> None:
        decision = self._redis_decision()
        decision.revisit_trigger = "After launch if Redis becomes a bottleneck."
        decision.action_items = [
            ActionItem(
                action="Revisit the design after launch if Redis becomes a bottleneck.",
                owner="Leo Park",
                source_message_ts="1782202120.000300",
            )
        ]
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual([item.action for item in processed.raw_decisions[0].action_items], ["Revisit the design after launch if Redis becomes a bottleneck."])
        self.assertEqual(processed.clean_decisions[0].revisit_trigger, "After launch if Redis becomes a bottleneck.")
        self.assertEqual(processed.clean_decisions[0].action_items, [])

    def test_tradeoff_duplicate_risk_is_removed_from_clean_but_kept_raw(self) -> None:
        events = parse_slack_json(load_slack_json("data/pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782288180.000400"]
        decision = DecisionRecord(
            decision_id="dec_pricing_test",
            decision="Raise Pro pricing to $29 for new customers",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            risks=[Claim(claim="Some price-sensitive solo users may be lost.", source_message_ts="1782288060.000200")],
            tradeoffs=[Claim(claim="Extra loss of some price-sensitive solo users.", source_message_ts="1782288060.000200")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual([claim.claim for claim in processed.raw_decisions[0].risks], ["Some price-sensitive solo users may be lost."])
        self.assertEqual(processed.clean_decisions[0].risks, [])
        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Losing some price-sensitive solo users is the accepted tradeoff."],
        )

    def test_computed_relative_revisit_date_is_cleared_from_clean_but_kept_raw(self) -> None:
        events = parse_slack_json(load_slack_json("data/vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782295440.000500"]
        decision = DecisionRecord(
            decision_id="dec_vendor_test",
            decision="Choose Datadog for observability now",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            revisit_date="2025-08-23",
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(processed.raw_decisions[0].revisit_date, "2025-08-23")
        self.assertIsNone(processed.clean_decisions[0].revisit_date)
        self.assertEqual(processed.clean_decisions[0].revisit_trigger, "After 30 days if alert noise is still high.")

    def test_effective_date_is_not_reused_as_decision_date(self) -> None:
        events = parse_slack_json(load_slack_json("data/pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782288180.000400"]
        decision = DecisionRecord(
            decision_id="dec_pricing_date_test",
            decision="Raise Pro pricing to $29 for new customers starting 2026-08-01",
            date="2026-08-01",
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            effective_date="2026-08-01",
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(processed.raw_decisions[0].date, "2026-08-01")
        self.assertIsNone(processed.clean_decisions[0].date)
        self.assertEqual(processed.clean_decisions[0].effective_date, "2026-08-01")

    def test_concern_misclassified_as_assumption_moves_to_objections(self) -> None:
        events = parse_slack_json(load_slack_json("data/pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782288180.000400"]
        decision = DecisionRecord(
            decision_id="dec_pricing_objection_test",
            decision="Raise Pro pricing to $29 for new customers",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            assumptions=[Claim(claim="There is concern about surprising existing customers.", source_message_ts="1782288120.000300")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(processed.clean_decisions[0].assumptions, [])
        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].objections],
            ["There is concern about surprising existing customers."],
        )

    def test_source_tradeoff_misclassified_as_risk_moves_to_tradeoffs(self) -> None:
        events = parse_slack_json(load_slack_json("data/pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782288180.000400"]
        decision = DecisionRecord(
            decision_id="dec_pricing_tradeoff_test",
            decision="Raise Pro pricing to $29 for new customers",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            risks=[Claim(claim="Some price-sensitive solo users may be lost.", source_message_ts="1782288060.000200")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(processed.clean_decisions[0].risks, [])
        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Losing some price-sensitive solo users is the accepted tradeoff."],
        )

    def test_decision_component_timeline_is_removed_from_clean_but_kept_raw(self) -> None:
        events = parse_slack_json(load_slack_json("data/pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782288180.000400"]
        decision = DecisionRecord(
            decision_id="dec_pricing_timeline_test",
            decision="Raise Pro pricing to $29 for new customers starting 2026-08-01, grandfather existing Pro customers for 6 months",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            effective_date="2026-08-01",
            timelines=[Claim(claim="Grandfather existing Pro customers for 6 months.", source_message_ts="1782288180.000400")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual([claim.claim for claim in processed.raw_decisions[0].timelines], ["Grandfather existing Pro customers for 6 months."])
        self.assertEqual(processed.clean_decisions[0].timelines, [])

    def test_rejected_alternative_timeline_is_removed_from_clean_but_kept_raw(self) -> None:
        events = parse_slack_json(load_slack_json("data/vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782295440.000500"]
        decision = DecisionRecord(
            decision_id="dec_vendor_timeline_test",
            decision="Choose Datadog for observability now",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            timelines=[
                Claim(
                    claim="The migration would take longer because several dashboards need to be rebuilt.",
                    source_message_ts="1782295260.000200",
                ),
                Claim(
                    claim="Implementation can finish in about ten business days.",
                    source_message_ts="1782295380.000400",
                ),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertIn(
            "The migration would take longer because several dashboards need to be rebuilt.",
            [claim.claim for claim in processed.raw_decisions[0].timelines],
        )
        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].timelines],
            ["Implementation can finish in about ten business days."],
        )

    def test_rejected_vendor_wrapper_timeline_is_removed_from_chosen_vendor_decision(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_launchdarkly_timeline_test",
            decision="Choose LaunchDarkly for feature flags now",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            timelines=[
                Claim(
                    claim="The migration would take longer because the flag evaluation wrapper must be rebuilt.",
                    source_message_ts="1791030060.000200",
                ),
                Claim(
                    claim="Implementation can finish in about eight business days.",
                    source_message_ts="1791030180.000400",
                ),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].timelines],
            ["Implementation can finish in about eight business days."],
        )

    def test_rejected_vendor_tradeoff_comparison_is_removed_from_chosen_vendor_decision(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_launchdarkly_tradeoff_leak_test",
            decision="Choose LaunchDarkly for feature flags now",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            tradeoffs=[
                Claim(
                    claim="ConfigCat is cheaper by about 22%, but migration would take longer is the accepted tradeoff.",
                    source_message_ts="1791030060.000200",
                ),
                Claim(
                    claim="Higher monthly spend is the accepted tradeoff.",
                    source_message_ts="1791030060.000200",
                ),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Higher monthly spend is the accepted tradeoff."],
        )

    def test_llm_vendor_output_canonicalizes_tradeoff_and_removes_short_rejected_timeline(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_launchdarkly_llm_shape_cleanup",
            decision="Choose LaunchDarkly for feature flags now.",
            date=event.timestamp,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
            status="confirmed",
            tradeoffs=[
                Claim(
                    claim="Monthly spend will be higher is the accepted tradeoff.",
                    source_message_ts="1791030060.000200",
                )
            ],
            timelines=[
                Claim(claim="The migration would take longer.", source_message_ts="1791030060.000200"),
                Claim(claim="Implementation can finish in about eight business days.", source_message_ts="1791030180.000400"),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual([claim.claim for claim in clean.tradeoffs], ["Higher monthly spend is the accepted tradeoff."])
        self.assertEqual([claim.claim for claim in clean.timelines], ["Implementation can finish in about eight business days."])

    def test_vendor_output_removes_owned_rejected_alternative_timeline_and_keeps_canonical_tradeoff(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791030240.000500"]
        decision = DecisionRecord(
            decision_id="dec_launchdarkly_owned_claim_cleanup",
            decision="Choose LaunchDarkly for feature flags now.",
            date=event.timestamp,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Tom Garcia", "Hannah Lee"],
            status="confirmed",
            tradeoffs=[
                Claim(
                    claim="The monthly spend will be higher with LaunchDarkly is the accepted tradeoff.",
                    source_message_ts="1791030060.000200",
                )
            ],
            timelines=[
                Claim(
                    claim="ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt.",
                    source_message_ts="1791030060.000200",
                ),
                Claim(claim="Implementation can finish in about eight business days.", source_message_ts="1791030180.000400"),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual([claim.claim for claim in clean.tradeoffs], ["Higher monthly spend is the accepted tradeoff."])
        self.assertNotIn(
            "ConfigCat migration would take longer because the flag evaluation wrapper must be rebuilt.",
            [claim.claim for claim in clean.timelines],
        )
        self.assertEqual([claim.claim for claim in clean.timelines], ["Implementation can finish in about eight business days."])

    def test_vendor_tradeoff_canonicalizes_higher_monthly_spend_wording(self) -> None:
        self.assertEqual(
            normalize_tradeoff_claim("Monthly spend will be higher is the accepted tradeoff."),
            "Higher monthly spend is the accepted tradeoff.",
        )
        for wording in (
            "The higher monthly spend is a downside is the accepted tradeoff.",
            "Higher monthly spend is the accepted cost.",
            "Higher monthly spend is the accepted cost is the accepted tradeoff.",
            "LaunchDarkly has higher monthly spend is the accepted tradeoff.",
            "The accepted tradeoff is higher monthly spend.",
            "The higher monthly spend is a cost of choosing LaunchDarkly.",
            "The monthly spend is higher.",
        ):
            with self.subTest(wording=wording):
                self.assertEqual(normalize_tradeoff_claim(wording), "Higher monthly spend is the accepted tradeoff.")
        events = parse_slack_json(load_slack_json("data/vendor_selection.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782295440.000500"]
        decision = DecisionRecord(
            decision_id="dec_vendor_tradeoff_wording",
            decision="Choose Datadog for observability now",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            tradeoffs=[Claim(claim="The monthly spend will be higher is the accepted tradeoff.", source_message_ts="1782295260.000200")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Higher monthly spend is the accepted tradeoff."],
        )

    def test_vendor_tradeoff_canonicalizes_vendor_specific_higher_monthly_spend_wording(self) -> None:
        event = self.lookup["1782202180.000400"]
        decision = DecisionRecord(
            decision_id="dec_vendor_tradeoff_vendor_name",
            decision="Choose LaunchDarkly",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            tradeoffs=[Claim(claim="The monthly spend is higher with LaunchDarkly is the accepted tradeoff.", source_message_ts="1782202060.000200")],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Higher monthly spend is the accepted tradeoff."],
        )

    def test_final_tradeoff_canonicalization_moves_miscategorized_monthly_spend_risk(self) -> None:
        event = self.lookup["1782202060.000200"]
        decision = DecisionRecord(
            decision_id="dec_final_tradeoff_cleanup",
            decision="Choose LaunchDarkly",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            objections=[Claim(claim="Another expensive platform tool is a concern.", source_message_ts=event.message_ts)],
            risks=[Claim(claim="Higher monthly spend is the accepted cost.", source_message_ts=event.message_ts)],
            tradeoffs=[
                Claim(
                    claim="Higher monthly spend is the accepted cost is the accepted tradeoff.",
                    source_message_ts=event.message_ts,
                ),
                Claim(claim="The monthly spend is higher.", source_message_ts=event.message_ts),
            ],
            decision_source_message_ts="1782202180.000400",
        )
        attach_sources(decision, self.lookup)

        changed = finalize_clean_tradeoff_canonicalization([decision])

        self.assertGreater(changed, 0)
        self.assertEqual([claim.claim for claim in decision.tradeoffs], ["Higher monthly spend is the accepted tradeoff."])
        self.assertEqual(decision.risks, [])
        self.assertEqual([claim.claim for claim in decision.objections], ["Another expensive platform tool is a concern."])

    def test_reversal_timeline_duplicate_and_incorrect_spam_folder_risk_are_removed(self) -> None:
        events = parse_slack_json(load_slack_json("data/decision_reversal.json"))
        lookup = build_event_lookup(events)
        event = lookup["1782380300.000600"]
        decision = DecisionRecord(
            decision_id="dec_postmark_llm_cleanup",
            decision="Use Postmark for lifecycle emails instead of SendGrid",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            timelines=[Claim(claim="It needs to be ready before the onboarding launch.", source_message_ts="1782380300.000600")],
            deadlines=[Deadline(date="2026-08-20", description="Onboarding launch.", source_message_ts="1782380300.000600")],
            risks=[
                Claim(
                    claim="SendGrid had fewer spam-folder issues than Postmark was reported to have.",
                    source_message_ts="1782380060.000200",
                ),
                Claim(
                    claim="SendGrid's cheaper tier does not include the dedicated IP package needed, so the real cost difference is much smaller.",
                    source_message_ts="1782380240.000500",
                ),
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.timelines, [])
        self.assertEqual(clean.risks, [])
        self.assertIn(
            "SendGrid's cheaper tier does not include the dedicated IP package needed, so the real cost difference is much smaller.",
            [claim.claim for claim in clean.reasoning],
        )
        self.assertNotIn(
            "SendGrid had fewer spam-folder issues than Postmark was reported to have.",
            [claim.claim for claim in clean.reasoning],
        )

    def test_messy_tradeoff_and_decision_are_canonicalized_and_commitment_action_is_restored(self) -> None:
        events = parse_slack_json(load_slack_json("data/messy.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_messy_wording",
            decision="Create an Organization Settings section and move billing, roles, SAML, and audit logs under it",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Owen Diaz"],
            status="confirmed",
            tradeoffs=[Claim(claim="Accept a basic IA now instead of doing the full permissions model is the accepted tradeoff.", source_message_ts="1782460780.001400")],
            action_items=[
                ActionItem(action="Take the nav work.", owner="Priya Shah", due_date=None, due_text=None, source_message_ts="1782460600.001100"),
                ActionItem(action="Move audit logs once the route exists.", owner="Owen Diaz", due_date=None, due_text=None, source_message_ts="1782460600.001100"),
            ],
            decision_source_message_ts="1782460720.001300",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.decision, "Create an Organization Settings section and move billing, roles, SAML, and audit logs under it.")
        self.assertEqual(
            [claim.claim for claim in clean.tradeoffs],
            ["Accepting a basic IA now instead of doing the full permissions model is the accepted tradeoff."],
        )
        self.assertIn(
            ("Priya Shah", "Make Organization Settings the parent section and move billing and roles first, then SAML."),
            [(item.owner, item.action) for item in clean.action_items],
        )

    def test_later_reversal_removes_earlier_same_thread_decision_from_clean_output(self) -> None:
        events = parse_slack_json(load_slack_json("data/decision_reversal.json"))
        lookup = build_event_lookup(events)
        earlier = DecisionRecord(
            decision_id="dec_sendgrid_test",
            decision="Use SendGrid for lifecycle emails",
            date=None,
            owner="Marco Silva",
            owner_role="Finance Lead",
            participants=["Nina Patel", "Marco Silva", "Aisha Rahman"],
            status="confirmed",
            revisit_trigger="After the first campaign.",
            decision_source_message_ts="1782380180.000400",
        )
        later = DecisionRecord(
            decision_id="dec_postmark_test",
            decision="Use Postmark for lifecycle emails instead of SendGrid",
            date=None,
            owner="Marco Silva",
            owner_role="Finance Lead",
            participants=["Nina Patel", "Marco Silva", "Aisha Rahman"],
            status="confirmed",
            decision_source_message_ts="1782380300.000600",
        )
        attach_sources(earlier, lookup)
        attach_sources(later, lookup)

        processed = postprocess_decisions([earlier, later], lookup)

        self.assertEqual([decision.decision for decision in processed.raw_decisions], [earlier.decision, later.decision])
        self.assertEqual([decision.decision for decision in processed.clean_decisions], [later.decision])
        self.assertIsNone(processed.clean_decisions[0].revisit_trigger)

    def test_clean_date_is_derived_from_decision_source_message_ts(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_general_workability.json"))
        lookup = build_event_lookup(events)
        source_event = lookup["1791001180.000400"]
        decision = DecisionRecord(
            decision_id="dec_new_date_normalization",
            decision="Delay the analytics dashboard beta until role-based access is complete",
            date="2026-08-20",
            owner=source_event.speaker,
            owner_role=source_event.speaker_role,
            participants=[source_event.speaker],
            status="confirmed",
            decision_source_message_ts=source_event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(processed.clean_decisions[0].date, "2026-10-03")

    def test_vague_safer_reasoning_is_removed_when_same_source_already_yields_objection_and_assumption(self) -> None:
        decision = self._launch_decision(
            reasoning=[Claim(claim="The safer call is to delay.", source_message_ts="1782201120.000300")],
            objections=[Claim(claim="The delay could hurt Q3 pipeline", source_message_ts="1782201120.000300")],
            assumptions=[Claim(claim="The pilots will wait", source_message_ts="1782201120.000300")],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        self.assertEqual(processed.clean_decisions[0].reasoning, [])

    def test_reversal_cleanup_removes_earlier_assumptions_and_risks_but_keeps_objection(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_decision_reversal.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_new_reversal_cleanup",
            decision="Use Segment for event tracking instead of RudderStack",
            date=None,
            owner="Nina Patel",
            owner_role="Head of Growth",
            participants=["Nina Patel", "Marco Silva", "Aisha Rahman"],
            status="confirmed",
            objections=[Claim(claim="There is concern about using RudderStack because the support team had trouble debugging missing events during the March onboarding campaign", source_message_ts="1791040120.000300")],
            assumptions=[Claim(claim="Cost matters more than support workflow", source_message_ts="1791040120.000300")],
            risks=[Claim(claim="Support debugging could become a blocker", source_message_ts="1791040180.000400")],
            decision_source_message_ts="1791040300.000600",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.assumptions, [])
        self.assertEqual(clean.risks, [])
        self.assertEqual([claim.claim for claim in clean.objections], ["There is concern about using RudderStack because the support team had trouble debugging missing events during the March onboarding campaign"])

    def test_reversal_cost_difference_tradeoff_is_canonicalized_as_reasoning(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_decision_reversal.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_new_reversal_tradeoff_cleanup",
            decision="Use Segment for event tracking instead of RudderStack",
            date=None,
            owner="Nina Patel",
            owner_role="Head of Growth",
            participants=["Nina Patel", "Marco Silva", "Aisha Rahman"],
            status="confirmed",
            tradeoffs=[
                Claim(
                    claim="The small cost difference is accepted in favor of debuggability is the accepted tradeoff.",
                    source_message_ts="1791040300.000600",
                )
            ],
            decision_source_message_ts="1791040300.000600",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.tradeoffs, [])
        self.assertIn(
            "Better debuggability is prioritized over the small cost difference.",
            [claim.claim for claim in clean.reasoning],
        )

    def test_duplicate_normalized_decisions_are_merged(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_messy_implicit.json"))
        lookup = build_event_lookup(events)
        earlier = DecisionRecord(
            decision_id="dec_security_center_earlier",
            decision="Create a Security Center parent section",
            date=None,
            owner="Maya Chen",
            owner_role="VP Product",
            participants=["Maya Chen", "Priya Shah"],
            status="confirmed",
            objections=[Claim(claim="There is concern about slipping the security review demo", source_message_ts="1791050240.000500")],
            decision_source_message_ts="1791050360.000700",
        )
        later = DecisionRecord(
            decision_id="dec_security_center_later",
            decision="Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Owen Diaz"],
            status="confirmed",
            timelines=[Claim(claim="The Security Center section can be wired by next Thursday", source_message_ts="1791050300.000600")],
            action_items=[
                ActionItem(action="Make Security Center the parent section and move SSO and audit exports first, then SCIM.", owner="Priya Shah", source_message_ts="1791050480.000900")
            ],
            decision_source_message_ts="1791050480.000900",
        )
        attach_sources(earlier, lookup)
        attach_sources(later, lookup)

        processed = postprocess_decisions([earlier, later], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(len(processed.clean_decisions), 1)
        self.assertEqual(clean.owner, "Priya Shah")
        self.assertIn("There is concern about slipping the security review demo", [claim.claim for claim in clean.objections])
        self.assertIn("The Security Center section can be wired by next Thursday", [claim.claim for claim in clean.timelines])
        self.assertIn(
            ("Priya Shah", "Make Security Center the parent section and move SSO and audit exports first, then SCIM."),
            [(item.owner, item.action) for item in clean.action_items],
        )

    def test_vague_safer_reasoning_removal_preserves_same_source_objection(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_general_workability.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_safer_objection_preserved",
            decision="Delay the analytics dashboard beta until role-based access is complete",
            date=None,
            owner="Maya Chen",
            owner_role="VP Product",
            participants=["Maya Chen", "Sarah Kim"],
            status="confirmed",
            reasoning=[Claim(claim="The safer call is to delay.", source_message_ts="1791001120.000300")],
            objections=[Claim(claim="A delay could weaken the enterprise demo pipeline.", source_message_ts="1791001120.000300")],
            assumptions=[Claim(claim="The design partners will wait.", source_message_ts="1791001120.000300")],
            decision_source_message_ts="1791001180.000400",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual(clean.reasoning, [])
        self.assertEqual([claim.claim for claim in clean.objections], ["A delay could weaken the enterprise demo pipeline."])

    def test_new_pricing_direct_reasoning_is_preserved(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791020180.000400"]
        decision = DecisionRecord(
            decision_id="dec_new_pricing_reasoning",
            decision="Move Team pricing to $59 for new customers starting 2026-09-01",
            date=None,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            reasoning=[
                Claim(
                    claim="Onboarding time per Team account is higher than modeled.",
                    source_message_ts="1791020000.000100",
                    support_level="direct",
                )
            ],
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertIn(
            "Onboarding time per Team account is higher than modeled.",
            [claim.claim for claim in processed.clean_decisions[0].reasoning],
        )

    def test_pricing_decision_text_restores_effective_date_when_structured_field_exists(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_pricing_change.json"))
        lookup = build_event_lookup(events)
        event = lookup["1791020180.000400"]
        decision = DecisionRecord(
            decision_id="dec_new_pricing_title_date",
            decision="Move Team pricing to $59 for new customers, and grandfather existing Team customers for 4 months.",
            date=None,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            effective_date="2026-09-01",
            decision_source_message_ts=event.message_ts,
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            processed.clean_decisions[0].decision,
            "Move Team pricing to $59 for new customers starting 2026-09-01 and grandfather existing Team customers for 4 months.",
        )

    def test_scope_constraint_tradeoff_is_removed_from_messy_implicit_decision(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_messy_implicit.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_scope_tradeoff_cleanup",
            decision="Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Leo Park"],
            status="confirmed",
            tradeoffs=[
                Claim(claim="No custom permission scopes in this pass is the accepted tradeoff.", source_message_ts="1791050300.000600"),
                Claim(
                    claim="Accepting a basic security IA now instead of doing the full permissions model is the accepted tradeoff.",
                    source_message_ts="1791050540.001000",
                ),
            ],
            decision_source_message_ts="1791050480.000900",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].tradeoffs],
            ["Accepting a basic security IA now instead of doing the full permissions model is the accepted tradeoff."],
        )

    def test_implementation_dependency_is_removed_from_messy_implicit_timelines(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_messy_implicit.json"))
        lookup = build_event_lookup(events)
        decision = DecisionRecord(
            decision_id="dec_dependency_timeline_cleanup",
            decision="Create a Security Center section and move SSO, audit exports, API tokens, and SCIM under it.",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Owen Diaz"],
            status="confirmed",
            timelines=[
                Claim(claim="Move API tokens after Owen's piece.", source_message_ts="1791050480.000900"),
                Claim(claim="Have the Security Center section wired by next Thursday.", source_message_ts="1791050300.000600"),
            ],
            decision_source_message_ts="1791050480.000900",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertEqual(
            [claim.claim for claim in processed.clean_decisions[0].timelines],
            ["Have the Security Center section wired by next Thursday."],
        )

    def test_deadline_context_objection_is_removed_from_clean_output(self) -> None:
        events = parse_slack_json(load_slack_json("data/new_messy_implicit.json"))
        lookup = build_event_lookup(events)
        source_event = lookup["1791050240.000500"]
        decision = DecisionRecord(
            decision_id="dec_deadline_objection_cleanup",
            decision="Create a Security Center section.",
            date=None,
            owner="Priya Shah",
            owner_role="Staff Engineer",
            participants=["Priya Shah", "Maya Chen"],
            status="confirmed",
            objections=[
                Claim(
                    claim="The updated admin flow is needed before the Sep 10 demo.",
                    source_message_ts=source_event.message_ts,
                )
            ],
            decision_source_message_ts="1791050480.000900",
        )
        attach_sources(decision, lookup)

        processed = postprocess_decisions([decision], lookup)

        self.assertNotIn(
            "The updated admin flow is needed before the Sep 10 demo.",
            [claim.claim for claim in processed.clean_decisions[0].objections],
        )

    def test_structured_revisit_fields_survive_clean_postprocessing(self) -> None:
        decisions = extract_decisions_from_events(self.grouped, self.lookup)
        redis = next(decision for decision in decisions if "Redis" in decision.decision)
        launch = next(decision for decision in decisions if "enterprise launch" in decision.decision)

        processed = postprocess_decisions([launch, redis], self.lookup)
        clean_launch, clean_redis = processed.clean_decisions

        self.assertEqual(clean_launch.revisit_date, "2026-09-12")
        self.assertEqual(clean_redis.revisit_trigger, "After launch if Redis becomes a bottleneck.")

    def test_engineering_estimate_goes_to_timelines_not_reasoning(self) -> None:
        decisions = extract_decisions_from_events(self.grouped, self.lookup)
        launch = next(decision for decision in decisions if "enterprise launch" in decision.decision)

        self.assertNotIn("Engineering needs about four more weeks", [claim.claim for claim in launch.reasoning])
        self.assertIn("Engineering needs about four more weeks", [claim.claim for claim in launch.timelines])
        self.assertTrue(all(claim.source for claim in launch.timelines))

    def test_clean_dedup_prefers_objections_for_concern_phrasing(self) -> None:
        duplicate = "The delay could hurt Q3 pipeline"
        decision = self._launch_decision(
            objections=[Claim(claim=duplicate, source_message_ts="1782201120.000300")],
            risks=[Claim(claim=duplicate, source_message_ts="1782201120.000300")],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual([claim.claim for claim in clean.objections], [duplicate])
        self.assertEqual(clean.risks, [])

    def test_failure_mode_prefers_risks_without_pushback(self) -> None:
        decision = self._redis_decision(
            objections=[
                Claim(
                    claim="Redis could become a bottleneck later",
                    source_message_ts="1782202120.000300",
                )
            ]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual(clean.objections, [])
        self.assertEqual([claim.claim for claim in clean.risks], ["Redis could become a bottleneck later"])

    def test_soc2_security_review_driver_prefers_reasoning_over_risks(self) -> None:
        decision = self._launch_decision(
            risks=[
                Claim(
                    claim="Without audit logs and SSO, security review will probably fail.",
                    source_message_ts="1782201060.000200",
                )
            ]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual(clean.risks, [])
        self.assertEqual(
            [claim.claim for claim in clean.reasoning],
            ["Without audit logs and SSO, security review will probably fail."],
        )

    def test_near_duplicate_soc2_security_review_claims_prefer_specific_reasoning(self) -> None:
        decision = self._launch_decision(
            reasoning=[
                Claim(
                    claim="Two pilot customers said security review will block the launch without SOC 2.",
                    source_message_ts="1782201000.000100",
                )
            ],
            risks=[
                Claim(
                    claim="Security review will block the launch without SOC 2.",
                    source_message_ts="1782201000.000100",
                )
            ],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual(
            [claim.claim for claim in clean.reasoning],
            ["Two pilot customers said security review will block the launch without SOC 2."],
        )
        self.assertEqual(clean.risks, [])
        self.assertEqual(
            [claim.claim for claim in processed.raw_decisions[0].risks],
            ["Security review will block the launch without SOC 2."],
        )

    def test_redis_clean_output_remains_unchanged(self) -> None:
        decisions = extract_decisions_from_events(self.grouped, self.lookup)
        redis = next(decision for decision in decisions if "Redis" in decision.decision)

        processed = postprocess_decisions([redis], self.lookup)
        clean = processed.clean_decisions[0]

        self.assertEqual([claim.claim for claim in clean.reasoning], ["Postgres latency is too high under load"])
        self.assertEqual(clean.objections, [])
        self.assertEqual(clean.assumptions, [])
        self.assertEqual(
            [claim.claim for claim in clean.risks],
            ["Latency is the bigger risk", "Redis could become a bottleneck later"],
        )
        self.assertEqual(
            [claim.claim for claim in clean.tradeoffs],
            ["Extra infrastructure complexity is the accepted tradeoff."],
        )
        self.assertEqual(clean.timelines, [])
        self.assertEqual(clean.revisit_trigger, "After launch if Redis becomes a bottleneck.")

    def test_accepted_cost_prefers_tradeoffs(self) -> None:
        decision = self._redis_decision(
            reasoning=[Claim(claim="Extra infra complexity", source_message_ts="1782202060.000200")]
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual(clean.reasoning, [])
        self.assertEqual(
            [claim.claim for claim in clean.tradeoffs],
            ["Extra infrastructure complexity is the accepted tradeoff."],
        )

    def test_near_identical_reordered_claims_are_deduped(self) -> None:
        decision = self._launch_decision(
            objections=[Claim(claim="The delay could hurt Q3 pipeline", source_message_ts="1782201120.000300")],
            risks=[Claim(claim="Q3 pipeline could be hurt by the delay", source_message_ts="1782201120.000300")],
        )
        attach_sources(decision, self.lookup)

        processed = postprocess_decisions([decision], self.lookup)

        clean = processed.clean_decisions[0]
        self.assertEqual([claim.claim for claim in clean.objections], ["The delay could hurt Q3 pipeline"])
        self.assertEqual(clean.risks, [])

    def test_decision_record_is_keyword_only_to_avoid_positional_schema_breaks(self) -> None:
        with self.assertRaises(TypeError):
            DecisionRecord(  # type: ignore[call-arg]
                "dec_test",
                "Use Redis",
                "2026-06-23",
                "Leo Park",
                "CTO",
                ["Leo Park"],
            )

    def _redis_decision(
        self,
        reasoning: list[Claim] | None = None,
        objections: list[Claim] | None = None,
        assumptions: list[Claim] | None = None,
        risks: list[Claim] | None = None,
        tradeoffs: list[Claim] | None = None,
        timelines: list[Claim] | None = None,
    ) -> DecisionRecord:
        event = self.lookup["1782202180.000400"]
        return DecisionRecord(
            decision_id="dec_redis_test",
            decision="Use Redis for session state",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            reasoning=reasoning or [],
            objections=objections or [],
            assumptions=assumptions or [],
            risks=risks or [],
            tradeoffs=tradeoffs or [],
            timelines=timelines or [],
            decision_source_message_ts=event.message_ts,
        )

    def _launch_decision(
        self,
        reasoning: list[Claim] | None = None,
        objections: list[Claim] | None = None,
        assumptions: list[Claim] | None = None,
        risks: list[Claim] | None = None,
        tradeoffs: list[Claim] | None = None,
        timelines: list[Claim] | None = None,
    ) -> DecisionRecord:
        event = self.lookup["1782201180.000400"]
        return DecisionRecord(
            decision_id="dec_launch_test",
            decision="Delay the enterprise launch until SOC 2 is complete",
            date=event.timestamp,
            owner=event.speaker,
            owner_role=event.speaker_role,
            participants=[event.speaker],
            status="confirmed",
            reasoning=reasoning or [],
            objections=objections or [],
            assumptions=assumptions or [],
            risks=risks or [],
            tradeoffs=tradeoffs or [],
            timelines=timelines or [],
            decision_source_message_ts=event.message_ts,
        )


if __name__ == "__main__":
    unittest.main()
