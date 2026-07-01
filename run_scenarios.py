#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import DEFAULT_LLM_MODEL, EXTRACTOR_VERSION
from src.candidate_shadow import (
    filter_clean_decisions_by_claim_ownership,
    finalize_clean_decisions_with_state_resolution,
)
from src.conflict_detector import detect_conflicts, remove_conflicted_clean_decisions
from src.heuristic_extractor import extract_decisions_from_events
from src.input_loader import load_input_source
from src.llm_extractor import extract_decisions_with_llm
from src.memory_artifacts import build_memory_artifacts
from src.meeting_minutes import (
    apply_meeting_minutes_clean_guard,
    build_clean_memory_payload,
    extract_meeting_memory,
    merge_meeting_decisions,
)
from src.review_queue import build_review_queue, review_queue_to_markdown
from src.schemas import DecisionRecord
from src.slack_parser import group_events_by_thread
from src.source_resolver import build_event_lookup
from src.validators import (
    ValidationIssue,
    finalize_clean_tradeoff_canonicalization,
    mark_support_levels,
    postprocess_decisions,
    validate_decisions,
)


SCENARIO_SETS: dict[str, list[str]] = {
    "new": [
        "data/new_general_workability.json",
        "data/new_no_decision.json",
        "data/new_pricing_change.json",
        "data/new_vendor_selection.json",
        "data/new_decision_reversal.json",
        "data/new_messy_implicit.json",
    ],
    "original": [
        "data/original_fake.json",
        "data/mobile_app.json",
        "data/pricing_change.json",
        "data/vendor_selection.json",
        "data/decision_reversal.json",
        "data/messy.json",
    ],
    "docs": [
        "data/google_doc_vendor_selection.json",
        "data/mixed_slack_doc_vendor_selection.json",
        "data/uploaded_doc_vendor_selection.md",
    ],
    "review": [
        "data/review_proposal_no_approval.json",
        "data/review_missing_owner.json",
        "data/review_low_confidence_ambiguous.json",
    ],
    "conflict": [
        "data/conflict_slack_doc_vendor_selection.json",
        "data/no_conflict_doc_rejected_alternative.json",
        "data/conflict_stale_doc_vs_new_slack.json",
    ],
    "meeting_minutes": [
        "data/meeting_minutes_vymun_april15.md",
        "data/meeting_minutes_vymun_may13.md",
        "data/meeting_vymun_may20_todo.md",
        "data/meeting_vymun_sep30_todo.md",
        "data/block_task_vymun_todo.md",
        "data/block_task_training_notes.md",
        "data/block_task_logistics_table.md",
    ],
    "agent_packets": [
        "data/agent_change_packet_v081.md",
    ],
}
SCENARIO_SETS["all"] = [*SCENARIO_SETS["original"], *SCENARIO_SETS["new"]]

EXPECTED_COUNTS = {
    "new_general_workability": 2,
    "new_no_decision": 0,
    "new_pricing_change": 1,
    "new_vendor_selection": 1,
    "new_decision_reversal": 1,
    "new_messy_implicit": 1,
    "original_fake": 2,
    "mobile_app": 0,
    "pricing_change": 1,
    "vendor_selection": 1,
    "decision_reversal": 1,
    "messy": 1,
    "google_doc_vendor_selection": 1,
    "mixed_slack_doc_vendor_selection": 1,
    "uploaded_doc_vendor_selection": 1,
    "review_proposal_no_approval": 0,
    "review_missing_owner": 1,
    "review_low_confidence_ambiguous": 0,
    "conflict_slack_doc_vendor_selection": 0,
    "no_conflict_doc_rejected_alternative": 1,
    "conflict_stale_doc_vs_new_slack": 0,
    "meeting_minutes_vymun_april15": 8,
    "meeting_minutes_vymun_may13": 8,
    "meeting_vymun_may20_todo": 0,
    "meeting_vymun_sep30_todo": 0,
    "block_task_vymun_todo": 0,
    "block_task_training_notes": 0,
    "block_task_logistics_table": 0,
    "agent_change_packet_v081": 0,
}

SCENARIO_TYPES = {
    "original_fake": "general_workability",
    "new_general_workability": "general_workability",
    "mobile_app": "no_decision",
    "new_no_decision": "no_decision",
    "pricing_change": "pricing_change",
    "new_pricing_change": "pricing_change",
    "vendor_selection": "vendor_selection",
    "new_vendor_selection": "vendor_selection",
    "decision_reversal": "decision_reversal",
    "new_decision_reversal": "decision_reversal",
    "messy": "messy",
    "new_messy_implicit": "messy",
    "google_doc_vendor_selection": "document_vendor_selection",
    "mixed_slack_doc_vendor_selection": "document_vendor_selection",
    "uploaded_doc_vendor_selection": "document_vendor_selection",
    "review_proposal_no_approval": "review_queue",
    "review_missing_owner": "review_queue",
    "review_low_confidence_ambiguous": "review_queue",
    "conflict_slack_doc_vendor_selection": "conflict_detection",
    "no_conflict_doc_rejected_alternative": "conflict_detection",
    "conflict_stale_doc_vs_new_slack": "conflict_detection",
    "meeting_minutes_vymun_april15": "meeting_minutes",
    "meeting_minutes_vymun_may13": "meeting_minutes",
    "meeting_vymun_may20_todo": "meeting_minutes",
    "meeting_vymun_sep30_todo": "meeting_minutes",
    "block_task_vymun_todo": "meeting_minutes",
    "block_task_training_notes": "meeting_minutes",
    "block_task_logistics_table": "meeting_minutes",
    "agent_change_packet_v081": "agent_change_packet",
}


@dataclass
class ScenarioArtifacts:
    scenario_name: str
    input_file: str
    clean_payload: dict[str, Any]
    raw_payload: dict[str, Any]
    normalized_events: list[dict[str, Any]]
    source_bundles: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    state_resolution: dict[str, Any]
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    meeting_metadata: dict[str, Any] = field(default_factory=dict)
    clean_memory: dict[str, Any] = field(default_factory=dict)
    action_items: dict[str, Any] = field(default_factory=dict)
    open_questions: dict[str, Any] = field(default_factory=dict)
    document_memory_debug: dict[str, Any] = field(default_factory=dict)
    block_classification_debug: dict[str, Any] = field(default_factory=dict)
    review_queue: dict[str, Any] = field(default_factory=lambda: {"metadata": {"review_item_count": 0}, "items": []})
    candidate_memory: dict[str, Any] = field(default_factory=dict)
    approved_memory: dict[str, Any] = field(default_factory=dict)
    rejected_memory: dict[str, Any] = field(default_factory=dict)
    review_actions: dict[str, Any] = field(default_factory=dict)
    memory_state_debug: dict[str, Any] = field(default_factory=dict)
    agent_change_packet_debug: dict[str, Any] = field(default_factory=dict)
    agent_claims: dict[str, Any] = field(default_factory=dict)
    agent_audit_report: dict[str, Any] = field(default_factory=dict)
    agent_audit_report_md: str = ""


@dataclass
class ScenarioVerification:
    scenario_name: str
    scenario_type: str
    variant: str
    expected_count: int
    actual_count: int
    count_pass: bool
    invariant_failures: list[dict[str, Any]] = field(default_factory=list)
    failure_path: str | None = None
    review_item_count: int = 0
    review_reasons: list[str] = field(default_factory=list)

    @property
    def invariant_pass(self) -> bool:
        return not self.invariant_failures

    @property
    def verdict(self) -> str:
        return "PASS" if self.count_pass and self.invariant_pass else "FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Spool scenario verification as one organized batch.")
    parser.add_argument("--scenario-set", choices=sorted(SCENARIO_SETS), default="new")
    parser.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    parser.add_argument("--pipeline", choices=["legacy", "candidates-shadow"], default="legacy")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL))
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", default="outputs/runs")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--max-threads", type=int, default=None)
    parser.add_argument("--include-review-claims", action="store_true")
    parser.add_argument("--review-policy", choices=["conservative"], default="conservative")
    parser.add_argument("--auto-approve", choices=["true", "false"], default="false")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    input_files = resolve_scenario_set(args.scenario_set)
    requested_run_id = args.run_id or _generated_run_id(args.scenario_set, args.mode, args.pipeline)
    output_dir = _create_batch_output_dir(Path(args.output_root), requested_run_id, overwrite=args.overwrite)

    artifacts_by_scenario: dict[str, ScenarioArtifacts] = {}
    verifications: list[ScenarioVerification] = []
    for input_file in input_files:
        artifacts = _run_scenario(input_file, args)
        artifacts_by_scenario[artifacts.scenario_name] = artifacts
        verifications.append(_verify_scenario(artifacts))

    output_paths = _write_aggregate_files(output_dir, artifacts_by_scenario, pretty=args.pretty)
    _expand_failures(output_dir, artifacts_by_scenario, verifications, pretty=args.pretty)
    summary_path = _write_markdown_summary(output_dir, verifications)
    csv_path = _write_csv_summary(output_dir, verifications)
    manifest_path = output_dir / "batch_manifest.json"
    output_paths["batch_manifest"] = str(manifest_path)
    output_paths["verification_summary"] = str(summary_path)
    output_paths["verification_summary_csv"] = str(csv_path)
    _write_batch_manifest(output_dir, args, input_files, output_paths, verifications, pretty=args.pretty)

    failed = [verification.scenario_name for verification in verifications if verification.verdict == "FAIL"]
    overall = "PASS" if not failed else "FAIL"
    print(f"Batch run folder: {output_dir}/")
    print(f"Overall verdict: {overall}")
    print(f"Failed scenarios: {', '.join(failed) if failed else 'none'}")
    print(f"Summary: {summary_path}")


def resolve_scenario_set(scenario_set: str) -> list[str]:
    return list(SCENARIO_SETS[scenario_set])


def _run_scenario(input_file: str, args: argparse.Namespace) -> ScenarioArtifacts:
    scenario_name = Path(input_file).stem
    ingested = load_input_source(input_file)
    events = ingested.canonical_events
    grouped_threads = group_events_by_thread(events)
    event_lookup = build_event_lookup(events)
    normalized_events = ingested.normalized_events
    source_bundles = ingested.source_bundles

    if args.mode == "llm":
        decisions = extract_decisions_with_llm(grouped_threads, event_lookup, args.model, args.max_threads)
    else:
        decisions = extract_decisions_from_events(grouped_threads, event_lookup, args.max_threads)
    if args.verify:
        decisions = mark_support_levels(decisions)
    meeting_memory = extract_meeting_memory(normalized_events, event_lookup, input_file, args.mode, args.pipeline)

    if args.mode == "llm":
        processed = postprocess_decisions(decisions, event_lookup, include_review_claims=args.include_review_claims)
        raw_decisions = processed.raw_decisions
        clean_decisions = processed.clean_decisions
        if meeting_memory.classification == "agent_change_packet":
            clean_decisions.clear()
            meeting_added_count = 0
            meeting_guard_removed_count = 0
        else:
            meeting_added_count = merge_meeting_decisions(clean_decisions, raw_decisions, meeting_memory)
            meeting_guard_removed_count = apply_meeting_minutes_clean_guard(clean_decisions, meeting_memory)
        ownership_removed_count = (
            filter_clean_decisions_by_claim_ownership(normalized_events, clean_decisions)
            if args.pipeline == "candidates-shadow"
            else 0
        )
        candidates, state_resolution, final_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
        final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(clean_decisions, state_resolution)
        if final_tradeoff_change_count and args.pipeline == "candidates-shadow":
            candidates, state_resolution, extra_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
            final_guard_removed_count += extra_guard_removed_count
        conflicts = detect_conflicts(
            candidates=candidates,
            raw_decisions=raw_decisions,
            clean_decisions=clean_decisions,
            source_bundles=source_bundles,
            normalized_events=normalized_events,
            state_resolution=state_resolution,
        )
        conflict_removed_count = remove_conflicted_clean_decisions(clean_decisions, conflicts)
        if conflict_removed_count and args.pipeline == "candidates-shadow":
            candidates, state_resolution, _conflict_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
        raw_metadata = _metadata(input_file, raw_decisions, args.mode, args.model, processed.raw_issues, include_validation_details=True)
        clean_metadata = _metadata(
            input_file,
            clean_decisions,
            args.mode,
            args.model,
            processed.clean_issues,
            raw_validation_issues=processed.raw_issues,
            clean_validation_issues=processed.clean_issues,
            removed_claim_count=processed.removed_claim_count + ownership_removed_count + final_guard_removed_count + meeting_guard_removed_count,
            repair_count=processed.repair_count + final_tradeoff_change_count,
            include_validation_details=False,
        )
        validation_issues_for_review = [*processed.raw_issues, *processed.clean_issues]
    else:
        raw_decisions = copy.deepcopy(decisions)
        clean_decisions, issues = validate_decisions(copy.deepcopy(decisions), event_lookup)
        if meeting_memory.classification == "agent_change_packet":
            clean_decisions.clear()
            meeting_added_count = 0
        else:
            meeting_added_count = merge_meeting_decisions(clean_decisions, raw_decisions, meeting_memory)
            apply_meeting_minutes_clean_guard(clean_decisions, meeting_memory)
        if args.pipeline == "candidates-shadow":
            filter_clean_decisions_by_claim_ownership(normalized_events, clean_decisions)
        candidates, state_resolution, _final_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
        final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(clean_decisions, state_resolution)
        if final_tradeoff_change_count and args.pipeline == "candidates-shadow":
            candidates, state_resolution, _extra_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
        conflicts = detect_conflicts(
            candidates=candidates,
            raw_decisions=raw_decisions,
            clean_decisions=clean_decisions,
            source_bundles=source_bundles,
            normalized_events=normalized_events,
            state_resolution=state_resolution,
        )
        conflict_removed_count = remove_conflicted_clean_decisions(clean_decisions, conflicts)
        if conflict_removed_count and args.pipeline == "candidates-shadow":
            candidates, state_resolution, _conflict_guard_removed_count = _shadow_artifacts(normalized_events, clean_decisions, args.pipeline)
        raw_metadata = _metadata(input_file, raw_decisions, args.mode, None, [], include_validation_details=True)
        clean_metadata = _metadata(input_file, clean_decisions, args.mode, None, issues, clean_validation_issues=issues, include_validation_details=False)
        validation_issues_for_review = issues

    review_queue = build_review_queue(
        clean_decisions,
        raw_decisions=raw_decisions,
        candidates=candidates,
        state_resolution=state_resolution,
        source_bundles=source_bundles,
        normalized_events=normalized_events,
        conflicts=conflicts,
        document_memory=meeting_memory.document_memory_debug,
        input_file=input_file,
        mode=args.mode,
        pipeline=args.pipeline,
        validation_issues=validation_issues_for_review,
    )
    clean_payload = _decision_payload(clean_metadata, clean_decisions)
    clean_memory = build_clean_memory_payload(meeting_memory, review_queue)
    memory_artifacts = build_memory_artifacts(
        input_file=input_file,
        mode=args.mode,
        pipeline=args.pipeline,
        decisions_payload=clean_payload,
        clean_memory=clean_memory,
        review_queue=review_queue,
        coverage_warnings=meeting_memory.block_classification_debug.get("coverage_audit_warnings", []),
        extra_candidates=meeting_memory.extra_candidate_memory,
        auto_approve=args.auto_approve == "true",
    )

    return ScenarioArtifacts(
        scenario_name=scenario_name,
        input_file=input_file,
        clean_payload=clean_payload,
        raw_payload=_decision_payload(raw_metadata, raw_decisions, include_internal=True),
        normalized_events=[event.to_dict() for event in normalized_events],
        source_bundles=[bundle.to_dict() for bundle in source_bundles],
        candidates=[candidate.to_dict() for candidate in candidates],
        state_resolution=state_resolution,
        conflicts=conflicts,
        meeting_metadata=meeting_memory.meeting_metadata,
        clean_memory=clean_memory,
        action_items=meeting_memory.action_items,
        open_questions=meeting_memory.open_questions,
        document_memory_debug=meeting_memory.document_memory_debug,
        block_classification_debug=meeting_memory.block_classification_debug,
        review_queue=review_queue,
        candidate_memory=memory_artifacts["candidate_memory.json"],
        approved_memory=memory_artifacts["approved_memory.json"],
        rejected_memory=memory_artifacts["rejected_memory.json"],
        review_actions=memory_artifacts["review_actions.json"],
        memory_state_debug=memory_artifacts["memory_state_debug.json"],
        agent_change_packet_debug=meeting_memory.agent_change_packet_debug or {},
        agent_claims=meeting_memory.agent_claims or {},
        agent_audit_report=meeting_memory.agent_audit_report or {},
        agent_audit_report_md=meeting_memory.agent_audit_markdown or "",
    )


def _shadow_artifacts(
    normalized_events,
    clean_decisions: list[DecisionRecord],
    pipeline: str,
) -> tuple[list[Any], dict[str, Any], int]:
    if pipeline != "candidates-shadow":
        return [], {
            "resolver_version": "shadow-0.1",
            "candidate_count": 0,
            "states": {},
            "claim_ownership": [],
            "excluded_from_active_memory": [],
            "duplicate_pairs": [],
        }, 0
    return finalize_clean_decisions_with_state_resolution(normalized_events, clean_decisions)


def _verify_scenario(artifacts: ScenarioArtifacts) -> ScenarioVerification:
    scenario_name = artifacts.scenario_name
    decisions = artifacts.clean_payload["decisions"]
    review_items = artifacts.review_queue.get("items", [])
    review_reasons = sorted({item.get("reason") for item in review_items if item.get("reason")})
    verification = ScenarioVerification(
        scenario_name=scenario_name,
        scenario_type=SCENARIO_TYPES[scenario_name],
        variant=(
            "review"
            if scenario_name.startswith("review_")
            else (
                "conflict"
                if scenario_name.startswith("conflict_") or scenario_name.startswith("no_conflict_")
                else ("agent_packet" if SCENARIO_TYPES[scenario_name] == "agent_change_packet" else ("new" if scenario_name.startswith("new_") else "original"))
            )
        ),
        expected_count=EXPECTED_COUNTS[scenario_name],
        actual_count=len(decisions),
        count_pass=len(decisions) == EXPECTED_COUNTS[scenario_name],
        review_item_count=len(review_items),
        review_reasons=review_reasons,
    )
    if scenario_name in {"vendor_selection", "new_vendor_selection"}:
        _verify_vendor_scenario(artifacts, verification)
    elif scenario_name in {"decision_reversal", "new_decision_reversal"}:
        _verify_reversal_scenario(artifacts, verification)
    elif scenario_name in {"messy", "new_messy_implicit"}:
        _verify_messy_scenario(artifacts, verification)
    elif scenario_name in {"mobile_app", "new_no_decision"}:
        _verify_no_decision_scenario(artifacts, verification)
    elif scenario_name in {"google_doc_vendor_selection", "mixed_slack_doc_vendor_selection", "uploaded_doc_vendor_selection"}:
        _verify_document_vendor_scenario(artifacts, verification)
    elif scenario_name.startswith("review_"):
        _verify_review_scenario(artifacts, verification)
    elif scenario_name in {"conflict_slack_doc_vendor_selection", "no_conflict_doc_rejected_alternative", "conflict_stale_doc_vs_new_slack"}:
        _verify_conflict_scenario(artifacts, verification)
    elif SCENARIO_TYPES.get(scenario_name) == "meeting_minutes":
        _verify_meeting_minutes_scenario(artifacts, verification)
    return verification


def _verify_vendor_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    decisions = artifacts.clean_payload["decisions"]
    timelines = _claims(decisions, "timelines")
    tradeoffs = _claims(decisions, "tradeoffs")
    if any(_is_rejected_vendor_timeline(claim) for claim in timelines):
        _fail(verification, "rejected alternative timeline excluded", "no rejected migration timeline in clean timelines", timelines)
    expected_timeline = "Implementation can finish in about eight business days" if verification.variant == "new" else "Implementation can finish in about ten business days"
    if not any(expected_timeline in claim for claim in timelines):
        _fail(verification, "chosen vendor implementation timeline kept", expected_timeline, timelines)
    if tradeoffs != ["Higher monthly spend is the accepted tradeoff."]:
        _fail(verification, "canonical vendor tradeoff", ["Higher monthly spend is the accepted tradeoff."], tradeoffs)

    chosen = "LaunchDarkly" if verification.variant == "new" else "Datadog"
    rejected = "ConfigCat" if verification.variant == "new" else "New Relic"
    active_claims = [
        claim
        for field in ("reasoning", "objections", "assumptions", "risks", "tradeoffs", "timelines")
        for claim in _claims(decisions, field)
    ]
    rejected_active_claims = [
        claim
        for claim in active_claims
        if re.search(r"\b" + re.escape(rejected) + r"\b", claim, re.IGNORECASE)
        or re.search(r"\bmigration would take longer\b", claim, re.IGNORECASE)
        or re.search(r"\bis cheaper by about\b", claim, re.IGNORECASE)
    ]
    if rejected_active_claims:
        _fail(verification, "rejected alternative claims excluded from active fields", f"no active {rejected} claims", rejected_active_claims)
    ownership = artifacts.state_resolution.get("claim_ownership", [])
    excluded = artifacts.state_resolution.get("excluded_from_active_memory", [])
    candidate_claims = [claim for candidate in artifacts.candidates for claim in candidate.get("claims", [])]
    if not any(item.get("target_name") == chosen and item.get("target_role") == "chosen_option" for item in ownership):
        _fail(verification, "chosen vendor ownership", f"{chosen} tagged chosen_option", ownership)
    if not any(item.get("target_name") == rejected and item.get("target_role") == "rejected_alternative" for item in ownership):
        _fail(verification, "rejected vendor ownership", f"{rejected} tagged rejected_alternative", ownership)
    if "claim_ownership" not in artifacts.state_resolution or "excluded_from_active_memory" not in artifacts.state_resolution:
        _fail(verification, "state resolution ownership sections", "claim_ownership and excluded_from_active_memory", artifacts.state_resolution)
    if not any(
        item.get("target_name") == rejected
        and item.get("target_role") == "rejected_alternative"
        and item.get("reason") == "claim targets rejected alternative"
        and _is_rejected_vendor_timeline(item.get("claim", ""))
        for item in excluded
    ):
        _fail(verification, "rejected timeline excluded from active memory", f"{rejected} rejected timeline excluded", excluded)
    if any(
        claim.get("category") == "timelines"
        and claim.get("target_name") == chosen
        and claim.get("target_role") == "chosen_option"
        and _is_rejected_vendor_timeline(claim.get("claim", ""))
        for claim in candidate_claims
    ):
        _fail(
            verification,
            "no chosen-option ownership for rejected migration timeline",
            f"no {chosen}/chosen_option rejected timeline",
            candidate_claims,
        )


def _verify_reversal_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    decisions = artifacts.clean_payload["decisions"]
    if len(decisions) != 1:
        return
    decision = decisions[0]
    expected_active = "Segment" if verification.variant == "new" else "Postmark"
    if expected_active not in decision.get("decision", ""):
        _fail(verification, "active reversed decision", expected_active, decision.get("decision"))
    forbidden_fields = ["assumptions", "risks", "tradeoffs", "timelines"]
    combined = " ".join(claim["claim"] for field in forbidden_fields for claim in decision.get(field, []))
    if verification.variant == "new" and re.search(r"\b(cost matters more than support workflow|support debugging could become a blocker)\b", combined, re.IGNORECASE):
        _fail(verification, "no superseded Segment-context leakage", "no superseded-only claims", combined)
    if verification.variant == "original" and re.search(r"\b(first campaign|SendGrid is cheaper for the first 100k)\b", combined, re.IGNORECASE):
        _fail(verification, "no superseded Postmark-context leakage", "no superseded-only claims", combined)
    if decision.get("revisit_trigger"):
        _fail(verification, "no superseded revisit trigger", None, decision.get("revisit_trigger"))


def _verify_messy_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    decisions = artifacts.clean_payload["decisions"]
    if len(decisions) != 1:
        return
    timelines = _claims(decisions, "timelines")
    required_terms = ["security center", "wired", "next thursday"] if verification.variant == "new" else ["new section", "wired", "next friday"]
    if not any(_timeline_contains_core_terms(claim, required_terms) for claim in timelines):
        _fail(verification, "core implementation timeline kept", required_terms, timelines)
    if any(_is_dependency_order_timeline(claim) for claim in timelines):
        _fail(verification, "dependency/order timeline excluded", "no dependency/order timeline", timelines)


def _verify_no_decision_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    if verification.actual_count != 0:
        return
    confirmed_states = {"confirmed", "implicit_confirmed"}
    states = [candidate.get("state") for candidate in artifacts.candidates]
    if any(state in confirmed_states for state in states):
        _fail(verification, "no confirmed candidates for no-decision scenario", "no confirmed candidate states", states)


def _verify_document_vendor_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    decisions = artifacts.clean_payload["decisions"]
    if len(decisions) != 1:
        return
    decision = decisions[0]
    decision_text = decision.get("decision", "")
    timelines = _claims(decisions, "timelines")
    tradeoffs = _claims(decisions, "tradeoffs")
    source_types = {event.get("source_type") for event in artifacts.normalized_events}
    bundle_types = {bundle.get("bundle_type") for bundle in artifacts.source_bundles}

    if "LaunchDarkly" not in decision_text:
        _fail(verification, "document decision chooses LaunchDarkly", "LaunchDarkly in decision", decision_text)
    if re.search(r"\b(?:choose|use)\s+ConfigCat\b", decision_text, re.IGNORECASE):
        _fail(verification, "document decision does not choose ConfigCat", "no ConfigCat chosen decision", decision_text)
    if not any(re.search(r"\bimplementation\b.*\babout eight business days\b", claim, re.IGNORECASE) for claim in timelines):
        _fail(verification, "document implementation timeline kept", "implementation in about eight business days", timelines)
    if any(_is_rejected_vendor_timeline(claim) or re.search(r"\bConfigCat\b.*\bmigration\b", claim, re.IGNORECASE) for claim in timelines):
        _fail(verification, "document rejected alternative timeline excluded", "no ConfigCat migration timeline", timelines)
    if tradeoffs and tradeoffs != ["Higher monthly spend is the accepted tradeoff."]:
        _fail(verification, "document canonical vendor tradeoff", ["Higher monthly spend is the accepted tradeoff."], tradeoffs)
    if "document" not in bundle_types:
        _fail(verification, "document bundle emitted", "document bundle", artifacts.source_bundles)
    if not ({"google_doc", "uploaded_document"} & source_types):
        _fail(verification, "document normalized events emitted", "google_doc or uploaded_document events", sorted(source_types))
    if artifacts.scenario_name == "mixed_slack_doc_vendor_selection" and not {"slack", "google_doc"} <= source_types:
        _fail(verification, "mixed source types emitted", "slack and google_doc", sorted(source_types))

    all_source_types = {
        source.get("source_type")
        for source in [
            *decision.get("decision_sources", []),
            *decision.get("supporting_sources", []),
            *decision.get("all_sources", []),
        ]
    }
    if not ({"google_doc", "uploaded_document"} & all_source_types):
        _fail(verification, "document source retained in clean sources", "document source in supporting/all sources", sorted(all_source_types))
    if artifacts.scenario_name == "mixed_slack_doc_vendor_selection":
        decision_source_types = {source.get("source_type") for source in decision.get("decision_sources", [])}
        if "slack" not in decision_source_types:
            _fail(verification, "mixed keeps Slack approval decision source", "slack in decision_sources", decision.get("decision_sources", []))


def _verify_review_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    decisions = artifacts.clean_payload["decisions"]
    confirmed_decisions = [decision for decision in decisions if decision.get("status") == "confirmed"]
    reasons = {item.get("reason") for item in artifacts.review_queue.get("items", [])}
    if artifacts.scenario_name in {"review_proposal_no_approval", "review_low_confidence_ambiguous"}:
        verification.count_pass = not confirmed_decisions
        if confirmed_decisions:
            _fail(verification, "no confirmed clean decision", "no confirmed decision", confirmed_decisions)
    if artifacts.scenario_name == "review_proposal_no_approval" and "approval_missing" not in reasons:
        _fail(verification, "approval missing review item", "approval_missing", sorted(reasons))
    if artifacts.scenario_name == "review_missing_owner" and "owner_missing" not in reasons:
        _fail(verification, "owner missing review item", "owner_missing", sorted(reasons))
    if artifacts.scenario_name == "review_low_confidence_ambiguous" and not ({"ambiguous_decision", "approval_missing"} & reasons):
        _fail(verification, "ambiguous/approval review item", "ambiguous_decision or approval_missing", sorted(reasons))


def _verify_conflict_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    review_items = artifacts.review_queue.get("items", [])
    conflict_items = [item for item in review_items if item.get("reason") == "conflicting_sources"]
    if artifacts.scenario_name == "no_conflict_doc_rejected_alternative":
        if artifacts.conflicts:
            _fail(verification, "no conflict for rejected alternative", [], artifacts.conflicts)
        if conflict_items:
            _fail(verification, "no conflict review item for rejected alternative", [], conflict_items)
        decisions = artifacts.clean_payload.get("decisions", [])
        if len(decisions) == 1 and "LaunchDarkly" not in decisions[0].get("decision", ""):
            _fail(verification, "no-conflict clean decision keeps LaunchDarkly", "LaunchDarkly", decisions[0].get("decision"))
        return

    if len(artifacts.conflicts) != 1:
        _fail(verification, "one conflict debug object", 1, artifacts.conflicts)
    if len(conflict_items) != 1:
        _fail(verification, "one conflict review item", 1, conflict_items)
        return
    item = conflict_items[0]
    if item.get("severity") != "high":
        _fail(verification, "conflict severity high", "high", item.get("severity"))
    option_text = " ".join(option.get("option", "") for option in item.get("options", []))
    if not ("LaunchDarkly" in option_text and "ConfigCat" in option_text):
        _fail(verification, "conflict options include both vendors", "LaunchDarkly and ConfigCat", option_text)
    source_types = {source.get("source_type") for source in item.get("sources", [])}
    if not {"slack", "google_doc"} <= source_types:
        _fail(verification, "conflict sources include Slack and Google Doc", ["slack", "google_doc"], sorted(source_types))
    confirmed = [decision for decision in artifacts.clean_payload.get("decisions", []) if decision.get("status") == "confirmed"]
    if confirmed:
        _fail(verification, "no confirmed clean memory for conflict", [], confirmed)


def _verify_meeting_minutes_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    if artifacts.scenario_name == "meeting_minutes_vymun_may13":
        _verify_may13_meeting_minutes_scenario(artifacts, verification)
        return
    if artifacts.scenario_name == "meeting_vymun_may20_todo":
        _verify_may20_todo_scenario(artifacts, verification)
        return
    if artifacts.scenario_name == "meeting_vymun_sep30_todo":
        _verify_sep30_todo_scenario(artifacts, verification)
        return
    if artifacts.scenario_name.startswith("block_task_"):
        _verify_block_task_scenario(artifacts, verification)
        return

    decisions = [decision.get("decision", "") for decision in artifacts.clean_payload.get("decisions", [])]
    action_items = artifacts.action_items.get("action_items", [])
    open_questions = artifacts.open_questions.get("open_questions", [])
    decision_text = "\n".join(decisions)
    required_decision_fragments = [
        "Tuesday at 7PM",
        "backup meeting time",
        "April 28th",
        "May 2nd",
        "one application for both Sr. and Jr",
    ]
    for fragment in required_decision_fragments:
        if fragment not in decision_text:
            _fail(verification, f"meeting decision retained: {fragment}", fragment, decisions)
    if len(action_items) < 5:
        _fail(verification, "meeting action item count", "at least 5", len(action_items))
    owners = {owner for item in action_items for owner in item.get("owner_names", [])}
    for owner in ["James Wang", "Frances Nakhuda", "Hugo Chung", "Evangeline Zixuan Lin", "Zi Kun Meng"]:
        if owner not in owners:
            _fail(verification, f"meeting action owner retained: {owner}", owner, sorted(owners))
    if len(open_questions) < 2:
        _fail(verification, "meeting open question count", "at least 2", len(open_questions))
    confirmed_theme = [decision for decision in decisions if re.search(r"\btheme\b", decision, re.IGNORECASE)]
    if confirmed_theme:
        _fail(verification, "theme not confirmed decision", "no confirmed theme decision", confirmed_theme)
    if artifacts.meeting_metadata.get("classification") != "meeting_minutes":
        _fail(verification, "meeting classified as meeting_minutes", "meeting_minutes", artifacts.meeting_metadata.get("classification"))
    if artifacts.review_queue.get("metadata", {}).get("review_item_count", 0) < 1:
        _fail(verification, "meeting ambiguity review item", "at least one review item", artifacts.review_queue)


def _verify_may13_meeting_minutes_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    clean_memory = artifacts.clean_memory or {}
    confirmed = clean_memory.get("confirmed_memory", [])
    action_items = clean_memory.get("action_items", [])
    open_questions = clean_memory.get("open_questions", [])
    confirmed_text = "\n".join(item.get("title", "") for item in confirmed)
    action_text = "\n".join(item.get("title", "") + " " + " ".join(item.get("owner_names") or []) for item in action_items)
    question_text = "\n".join(item.get("title", "") for item in open_questions)

    if artifacts.meeting_metadata.get("classification") not in {"meeting_minutes", "meeting_like_document"}:
        _fail(verification, "May 13 classified as meeting notes", "meeting_minutes or meeting_like_document", artifacts.meeting_metadata.get("classification"))
    if len(action_items) < 8:
        _fail(verification, "May 13 action item count", "at least 8", len(action_items))
    if len(confirmed) < 4:
        _fail(verification, "May 13 confirmed memory count", "at least 4", len(confirmed))
    if len(open_questions) < 4:
        _fail(verification, "May 13 open question count", "at least 4", len(open_questions))
    for fragment in [
        "Room Where It Happens",
        "three committees",
        "Junior registration is 65",
        "Order more catering",
    ]:
        if fragment not in confirmed_text:
            _fail(verification, f"May 13 confirmed memory retained: {fragment}", fragment, confirmed_text)
    for fragment in [
        "Start sending out director acceptances",
        "Finish sec bios by Thursday",
        "Frankie",
        "Owen",
        "Look through materials left from last year",
    ]:
        if fragment not in action_text:
            _fail(verification, f"May 13 action item retained: {fragment}", fragment, action_text)
    if "Jr committees" not in question_text:
        _fail(verification, "May 13 Jr committees open question", "Jr committees", question_text)
    if "Jr committees" in confirmed_text:
        _fail(verification, "May 13 Jr committees not confirmed memory", "not confirmed", confirmed_text)


def _verify_may20_todo_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    action_items = artifacts.clean_memory.get("action_items", [])
    action_text = "\n".join(item.get("title", "") + " " + " ".join(item.get("owner_names") or []) for item in action_items)
    normalized_action_text = _normalize_text(action_text)
    spans = (artifacts.block_classification_debug or {}).get("detected_task_spans", [])

    if artifacts.meeting_metadata.get("classification") != "meeting_minutes":
        _fail(verification, "May 20 classified as meeting minutes", "meeting_minutes", artifacts.meeting_metadata.get("classification"))
    if len(action_items) < 4:
        _fail(verification, "May 20 To Do action count", "at least 4", len(action_items))
    if not spans:
        _fail(verification, "May 20 explicit To Do span detected", "at least one detected span", spans)
    for fragment in ["Evangeline", "Mrs. Haider", "Ms Janzen", "James", "finalize directors", "Owen", "reach out to schools"]:
        if _normalize_text(fragment) not in normalized_action_text:
            _fail(verification, f"May 20 To Do action retained: {fragment}", fragment, action_text)
    for false_positive in ["Need to touch on social media etiquette", "email update"]:
        if false_positive in action_text:
            _fail(verification, f"May 20 chatter not action: {false_positive}", "not action", action_text)


def _verify_sep30_todo_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    action_items = artifacts.clean_memory.get("action_items", [])
    action_text = "\n".join(item.get("title", "") + " " + " ".join(item.get("owner_names") or []) for item in action_items)
    spans = (artifacts.block_classification_debug or {}).get("detected_task_spans", [])

    if artifacts.meeting_metadata.get("classification") != "meeting_minutes":
        _fail(verification, "Sep 30 classified as meeting minutes", "meeting_minutes", artifacts.meeting_metadata.get("classification"))
    if len(action_items) < 10:
        _fail(verification, "Sep 30 To Do action count", "at least 10", len(action_items))
    if len(spans) < 2:
        _fail(verification, "Sep 30 two explicit To Do spans detected", "at least 2 detected spans", spans)
    for fragment in [
        "Order Pizza Jr",
        "Evangeline",
        "Hugo",
        "Send staff names",
        "James",
        "Email maintenance",
        "Owen",
        "Call Domino",
        "Connect with Chef Stacey",
        "Follow up with Mulgrave",
    ]:
        if fragment not in action_text:
            _fail(verification, f"Sep 30 To Do action retained: {fragment}", fragment, action_text)
    for false_positive in ["SJR: 18 students coming from Winnipeg", "18 students coming from Winnipeg"]:
        if false_positive in action_text:
            _fail(verification, f"Sep 30 chatter not action: {false_positive}", "not action", action_text)


def _verify_block_task_scenario(artifacts: ScenarioArtifacts, verification: ScenarioVerification) -> None:
    clean_memory = artifacts.clean_memory or {}
    action_items = clean_memory.get("action_items", [])
    blocks = (artifacts.block_classification_debug or {}).get("blocks", [])
    block_types = {block.get("block_type") for block in blocks}
    action_text = "\n".join(item.get("title", "") + " " + " ".join(item.get("owner_names") or []) for item in action_items)

    if artifacts.scenario_name == "block_task_vymun_todo":
        if not ({"task_section", "task_table"} & block_types):
            _fail(verification, "VYMUN todo task block detected", "task_section or task_table", sorted(block_types))
        if len(action_items) < 4:
            _fail(verification, "VYMUN todo action count", "at least 4", len(action_items))
        for fragment in ["James", "Elliott", "Owen", "director acceptances"]:
            if fragment not in action_text:
                _fail(verification, f"VYMUN todo action retained: {fragment}", fragment, action_text)
        return

    if artifacts.scenario_name == "block_task_training_notes":
        if not ({"task_section", "task_table"} & block_types):
            _fail(verification, "training task block detected", "task_section or task_table", sorted(block_types))
        if "policy_or_process" not in block_types:
            _fail(verification, "training policy sections detected", "policy_or_process", sorted(block_types))
        if len(action_items) < 4:
            _fail(verification, "training action count", "at least 4", len(action_items))
        for fragment in ["Corina", "Leanna", "Amanda", "Record the training session"]:
            if fragment not in action_text:
                _fail(verification, f"training action retained: {fragment}", fragment, action_text)
        for false_positive in ["Maintain a professional setting", "Try to have a fun"]:
            if false_positive in action_text:
                _fail(verification, f"training policy not action: {false_positive}", "not action", action_text)
        return

    if artifacts.scenario_name == "block_task_logistics_table":
        if "task_table" not in block_types:
            _fail(verification, "logistics task table detected", "task_table", sorted(block_types))
        if len(action_items) < 5:
            _fail(verification, "logistics action count", "at least 5", len(action_items))
        for fragment in ["Tyler", "Owen", "Bernice", "Start pairing students and tutors"]:
            if fragment not in action_text:
                _fail(verification, f"logistics task retained: {fragment}", fragment, action_text)


def _write_aggregate_files(output_dir: Path, artifacts_by_scenario: dict[str, ScenarioArtifacts], pretty: bool) -> dict[str, str]:
    aggregate_payloads = {
        "decisions_by_scenario.json": {name: artifacts.clean_payload for name, artifacts in artifacts_by_scenario.items()},
        "raw_decisions_by_scenario.json": {name: artifacts.raw_payload for name, artifacts in artifacts_by_scenario.items()},
        "normalized_events_by_scenario.json": {name: artifacts.normalized_events for name, artifacts in artifacts_by_scenario.items()},
        "source_bundles_by_scenario.json": {name: artifacts.source_bundles for name, artifacts in artifacts_by_scenario.items()},
        "candidates_by_scenario.json": {name: artifacts.candidates for name, artifacts in artifacts_by_scenario.items()},
        "state_resolution_by_scenario.json": {name: artifacts.state_resolution for name, artifacts in artifacts_by_scenario.items()},
        "conflicts_by_scenario.json": {name: artifacts.conflicts for name, artifacts in artifacts_by_scenario.items()},
        "meeting_metadata_by_scenario.json": {name: artifacts.meeting_metadata for name, artifacts in artifacts_by_scenario.items()},
        "clean_memory_by_scenario.json": {name: artifacts.clean_memory for name, artifacts in artifacts_by_scenario.items()},
        "action_items_by_scenario.json": {name: artifacts.action_items for name, artifacts in artifacts_by_scenario.items()},
        "open_questions_by_scenario.json": {name: artifacts.open_questions for name, artifacts in artifacts_by_scenario.items()},
        "document_memory_debug_by_scenario.json": {name: artifacts.document_memory_debug for name, artifacts in artifacts_by_scenario.items()},
        "block_classification_debug_by_scenario.json": {name: artifacts.block_classification_debug for name, artifacts in artifacts_by_scenario.items()},
        "review_queue_by_scenario.json": {name: artifacts.review_queue for name, artifacts in artifacts_by_scenario.items()},
        "candidate_memory_by_scenario.json": {name: artifacts.candidate_memory for name, artifacts in artifacts_by_scenario.items()},
        "approved_memory_by_scenario.json": {name: artifacts.approved_memory for name, artifacts in artifacts_by_scenario.items()},
        "rejected_memory_by_scenario.json": {name: artifacts.rejected_memory for name, artifacts in artifacts_by_scenario.items()},
        "review_actions_by_scenario.json": {name: artifacts.review_actions for name, artifacts in artifacts_by_scenario.items()},
        "memory_state_debug_by_scenario.json": {name: artifacts.memory_state_debug for name, artifacts in artifacts_by_scenario.items()},
        "agent_change_packet_debug_by_scenario.json": {name: artifacts.agent_change_packet_debug for name, artifacts in artifacts_by_scenario.items()},
        "agent_claims_by_scenario.json": {name: artifacts.agent_claims for name, artifacts in artifacts_by_scenario.items()},
        "agent_audit_report_by_scenario.json": {name: artifacts.agent_audit_report for name, artifacts in artifacts_by_scenario.items()},
    }
    output_paths: dict[str, str] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in aggregate_payloads.items():
        path = output_dir / filename
        _write_json(path, payload, pretty=pretty)
        output_paths[filename.removesuffix(".json")] = str(path)
    return output_paths


def _expand_failures(
    output_dir: Path,
    artifacts_by_scenario: dict[str, ScenarioArtifacts],
    verifications: list[ScenarioVerification],
    pretty: bool,
) -> None:
    for verification in verifications:
        if verification.verdict != "FAIL":
            continue
        artifacts = artifacts_by_scenario[verification.scenario_name]
        failure_dir = output_dir / "failures" / verification.scenario_name
        verification.failure_path = str(failure_dir)
        payloads = {
            "decisions.json": artifacts.clean_payload,
            "raw_decisions.json": artifacts.raw_payload,
            "normalized_events_debug.json": artifacts.normalized_events,
            "source_bundles_debug.json": artifacts.source_bundles,
            "candidates_debug.json": artifacts.candidates,
            "state_resolution_debug.json": artifacts.state_resolution,
            "conflicts_debug.json": artifacts.conflicts,
            "meeting_metadata.json": artifacts.meeting_metadata,
            "clean_memory.json": artifacts.clean_memory,
            "action_items.json": artifacts.action_items,
            "open_questions.json": artifacts.open_questions,
            "document_memory_debug.json": artifacts.document_memory_debug,
            "block_classification_debug.json": artifacts.block_classification_debug,
            "review_queue.json": artifacts.review_queue,
            "review_queue.md": review_queue_to_markdown(artifacts.review_queue),
            "candidate_memory.json": artifacts.candidate_memory,
            "approved_memory.json": artifacts.approved_memory,
            "rejected_memory.json": artifacts.rejected_memory,
            "review_actions.json": artifacts.review_actions,
            "memory_state_debug.json": artifacts.memory_state_debug,
            "agent_change_packet_debug.json": artifacts.agent_change_packet_debug,
            "agent_claims.json": artifacts.agent_claims,
            "agent_audit_report.json": artifacts.agent_audit_report,
            "agent_audit_report.md": artifacts.agent_audit_report_md,
        }
        for filename, payload in payloads.items():
            if filename.endswith(".md"):
                (failure_dir / filename).parent.mkdir(parents=True, exist_ok=True)
                (failure_dir / filename).write_text(str(payload), encoding="utf-8")
            else:
                _write_json(failure_dir / filename, payload, pretty=pretty)


def _write_batch_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    input_files: list[str],
    output_paths: dict[str, str],
    verifications: list[ScenarioVerification],
    pretty: bool,
) -> Path:
    failed = [verification.scenario_name for verification in verifications if verification.verdict == "FAIL"]
    passed = [verification.scenario_name for verification in verifications if verification.verdict == "PASS"]
    manifest = {
        "run_id": output_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_set": args.scenario_set,
        "mode": args.mode,
        "pipeline": args.pipeline,
        "model": args.model if args.mode == "llm" else None,
        "input_files": input_files,
        "scenario_names": [Path(path).stem for path in input_files],
        "output_dir": str(output_dir),
        "output_paths": output_paths,
        "failed_scenarios": failed,
        "passed_scenarios": passed,
        "overall_verdict": "PASS" if not failed else "FAIL",
    }
    path = output_dir / "batch_manifest.json"
    _write_json(path, manifest, pretty=pretty)
    return path


def _write_markdown_summary(output_dir: Path, verifications: list[ScenarioVerification]) -> Path:
    failed = [verification for verification in verifications if verification.verdict == "FAIL"]
    lines = [
        f"# Verification Summary",
        "",
        f"Overall verdict: {'PASS' if not failed else 'FAIL'}",
        "",
        "## Pair Summary",
        "",
        "| Scenario Type | Original | New | Pair Verdict |",
        "| --- | --- | --- | --- |",
    ]
    for scenario_type in [
        "general_workability",
        "no_decision",
        "pricing_change",
        "vendor_selection",
        "decision_reversal",
        "messy",
        "document_vendor_selection",
        "review_queue",
        "conflict_detection",
        "meeting_minutes",
        "agent_change_packet",
    ]:
        original = _verdict_for(verifications, scenario_type, "original")
        new = _verdict_for(verifications, scenario_type, "new")
        pair = "FAIL" if "FAIL" in {original, new} else ("PASS" if "PASS" in {original, new} else "-")
        lines.append(f"| {scenario_type} | {original} | {new} | {pair} |")
    lines.extend(
        [
            "",
            "## Scenario Summary",
            "",
            "| Scenario | Expected Count | Actual Count | Count Pass | Invariant Pass | Verdict | Failure Count | Review Item Count | Review Reasons |",
            "| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for verification in verifications:
        review_count = getattr(verification, "review_item_count", 0)
        review_reasons = ", ".join(getattr(verification, "review_reasons", [])) or "-"
        lines.append(
            f"| {verification.scenario_name} | {verification.expected_count} | {verification.actual_count} | "
            f"{_pass_fail(verification.count_pass)} | {_pass_fail(verification.invariant_pass)} | {verification.verdict} | "
            f"{len(verification.invariant_failures)} | {review_count} | {review_reasons} |"
        )
    if failed:
        lines.extend(["", "## Failures", ""])
        for verification in failed:
            lines.append(f"### {verification.scenario_name}")
            if verification.failure_path:
                lines.append(f"Failure files: `{verification.failure_path}`")
            for failure in verification.invariant_failures:
                lines.append(
                    f"- {failure['invariant']}: expected `{failure['expected']}`, actual `{failure['actual']}`"
                )
            lines.append("")
    path = output_dir / "verification_summary.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_csv_summary(output_dir: Path, verifications: list[ScenarioVerification]) -> Path:
    path = output_dir / "verification_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        for verification in verifications:
            writer.writerow(
                {
                    "scenario_type": verification.scenario_type,
                    "scenario_name": verification.scenario_name,
                    "variant": verification.variant,
                    "expected_count": verification.expected_count,
                    "actual_count": verification.actual_count,
                    "count_pass": verification.count_pass,
                    "invariant_pass": verification.invariant_pass,
                    "verdict": verification.verdict,
                    "failure_count": len(verification.invariant_failures),
                    "review_item_count": getattr(verification, "review_item_count", 0),
                    "review_reasons": ", ".join(getattr(verification, "review_reasons", [])),
                }
            )
    return path


def _decision_payload(metadata: dict[str, Any], decisions: list[DecisionRecord], include_internal: bool = False) -> dict[str, Any]:
    return {
        "metadata": metadata,
        "decisions": [decision.to_dict(include_internal=include_internal) for decision in decisions],
    }


def _metadata(
    input_file: str,
    decisions: list[DecisionRecord],
    mode: str,
    model: str | None,
    validation_issues: list[ValidationIssue],
    raw_validation_issues: list[ValidationIssue] | None = None,
    clean_validation_issues: list[ValidationIssue] | None = None,
    removed_claim_count: int | None = None,
    repair_count: int | None = None,
    include_validation_details: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "input_file": input_file,
        "decision_count": len(decisions),
        "extractor_version": EXTRACTOR_VERSION,
        "mode": mode,
        "model": model,
        "verified": len(validation_issues) == 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_validation_details:
        metadata["validation_issue_count"] = len(validation_issues)
        metadata["validation_issues"] = [issue.to_dict() for issue in validation_issues]
    if raw_validation_issues is not None:
        metadata["raw_validation_issue_count"] = len(raw_validation_issues)
    if clean_validation_issues is not None:
        metadata["clean_validation_issue_count"] = len(clean_validation_issues)
    if removed_claim_count is not None:
        metadata["removed_claim_count"] = removed_claim_count
    if repair_count is not None:
        metadata["repair_count"] = repair_count
        metadata["postprocessing_change_count"] = repair_count + (removed_claim_count or 0)
    return metadata


def _create_batch_output_dir(output_root: Path, run_id: str, overwrite: bool) -> Path:
    safe_run_id = _safe_token(run_id)
    output_root.mkdir(parents=True, exist_ok=True)
    candidate = output_root / safe_run_id
    if overwrite and candidate.exists():
        shutil.rmtree(candidate)
    if overwrite or not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    suffix = 2
    while True:
        suffixed = output_root / f"{safe_run_id}_{suffix}"
        if not suffixed.exists():
            suffixed.mkdir(parents=True, exist_ok=True)
            return suffixed
        suffix += 1


def _generated_run_id(scenario_set: str, mode: str, pipeline: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{scenario_set}__{mode}__{pipeline}__{timestamp}"


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value).strip("_") or "run"


def _write_json(path: Path, payload: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")


def _claims(decisions: list[dict[str, Any]], field: str) -> list[str]:
    return [claim["claim"] for decision in decisions for claim in decision.get(field, [])]


def _is_rejected_vendor_timeline(claim: str) -> bool:
    return bool(
        re.search(r"\bmigration would take longer\b", claim, re.IGNORECASE)
        or re.search(r"\b(flag evaluation wrapper|dashboards?)\b.*\b(?:rebuilt|rebuild)\b", claim, re.IGNORECASE)
    )


def _normalize_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _timeline_contains_core_terms(timeline: str, required_terms: list[str]) -> bool:
    normalized = _normalize_text(timeline)
    return all(_normalize_text(term) in normalized for term in required_terms)


def _is_dependency_order_timeline(timeline: str) -> bool:
    normalized = _normalize_text(timeline)
    return bool(
        re.search(r"\b(can follow after|move [a-z0-9 ]+ after [a-z0-9]+ s piece|once the route exists)\b", normalized)
        or re.search(r"\b(api tokens?|scim)\b.*\b(can follow later|follow later|after [a-z0-9]+ s piece|once route exists|once the route exists)\b", normalized)
    )


def _fail(verification: ScenarioVerification, invariant: str, expected: Any, actual: Any) -> None:
    verification.invariant_failures.append(
        {
            "invariant": invariant,
            "expected": expected,
            "actual": actual,
        }
    )


def _verdict_for(verifications: list[ScenarioVerification], scenario_type: str, variant: str) -> str:
    for verification in verifications:
        if verification.scenario_type == scenario_type and verification.variant == variant:
            return verification.verdict
    return "-"


def _pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


if __name__ == "__main__":
    main()
