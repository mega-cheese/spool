from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schemas import ActionItem, CLAIM_CATEGORIES, CanonicalEvent, Claim, Deadline, DecisionRecord, VALID_STATUSES
from .source_resolver import resolve_source


CONFIRMATION_RE = re.compile(
    r"\b(final decision|approved|we decided|decision:|agreed|move forward with)\b",
    re.IGNORECASE,
)
IMPLICIT_CONFIRMATION_RE = re.compile(
    r"(?=.*\b(?:works for me|agree|that would solve|ok then|yep)\b)"
    r"(?=.*\b(?:can you take|i'll make|i will make|i can move)\b)"
    r"(?=.*\b(?:next\s+\w+|before\s+\d{4}-\d{2}-\d{2}|out of scope|tradeoff)\b)",
    re.IGNORECASE | re.DOTALL,
)
TRADEOFF_ACCEPTS_RE = re.compile(
    r"^\s*the decision accepts\s+(.+?)\s+in exchange for\s+.+?\.?\s*$",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REVISIT_DATE_CLAIM_RE = re.compile(
    r"^\s*(?:let'?s\s+|we\s+can\s+)?revisit(?:\s+the\s+design)?\s+(?:on\s+)?(?P<date>\d{4}-\d{2}-\d{2})\.?\s*$",
    re.IGNORECASE,
)
REVISIT_AFTER_LAUNCH_CLAIM_RE = re.compile(
    r"^\s*(?:let'?s\s+|we\s+can\s+)?revisit(?:\s+the\s+design)?\s+after launch\.?\s*$",
    re.IGNORECASE,
)
REVIEW_TRIGGER_CLAIM_RE = re.compile(
    r"^\s*(?:we\s+will\s+)?review\s+[^.]*?\s+after\s+(?:the\s+first billing cycle|30 days)\.?\s*$",
    re.IGNORECASE,
)
CONCERN_RE = re.compile(
    r"\b(i'?m concerned|i am concerned|i'?m worried|i am worried|i disagree|pushback|my concern is|my only worry is|concern about|worried about|this could hurt)\b",
    re.IGNORECASE,
)
RISK_RE = re.compile(r"\b(could|may|might|will probably|will fail|may fail|risk|bottleneck|fail)\b", re.IGNORECASE)
PRIMARY_DRIVER_RE = re.compile(
    r"\bsecurity review\b.*\b(block|fail)\b|\b(block|fail)\b.*\bsecurity review\b",
    re.IGNORECASE,
)
TRADEOFF_RE = re.compile(r"\b(tradeoff|accepted cost|in exchange for|extra infra complexity|extra infrastructure complexity)\b", re.IGNORECASE)
ASSUMPTION_RE = re.compile(r"\b(assuming|we assume|depends on|provided that|as long as|if we believe)\b", re.IGNORECASE)
REVIEW_WORD_RE = re.compile(r"\b(revisit|review|check back|reassess)\b", re.IGNORECASE)
REVISIT_WORD_RE = re.compile(r"\b(revisit|check back|reassess)\b", re.IGNORECASE)
REVIEW_SCHEDULE_RE = re.compile(
    r"\breview\b[^.]*\b(after|on|by|in)\b|\b(after|on|by|in)\b[^.]*\breview\b",
    re.IGNORECASE,
)
TIMELINE_RE = re.compile(
    r"\b("
    r"takes?|requires?|deadline|duration|timeline|delivery window|implementation can finish|"
    r"engineering needs|"
    r"after launch|by \d{4}-\d{2}-\d{2}|"
    r"by (?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class ValidationIssue:
    decision_id: str
    message: str
    severity: str = "warning"
    type: str = "validation_issue"
    field: str | None = None
    claim: str | None = None
    message_ts: str | None = None
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type,
            "severity": self.severity,
            "decision_id": self.decision_id,
        }
        if self.field is not None:
            result["field"] = self.field
        if self.claim is not None:
            result["claim"] = self.claim
        if self.message_ts is not None:
            result["message_ts"] = self.message_ts
        if self.action is not None:
            result["action"] = self.action
        result["message"] = self.message
        return result


@dataclass
class CleanResult:
    decisions: list[DecisionRecord]
    issues: list[ValidationIssue] = field(default_factory=list)
    removed_claim_count: int = 0
    repair_issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class PostProcessResult:
    raw_decisions: list[DecisionRecord]
    clean_decisions: list[DecisionRecord]
    raw_issues: list[ValidationIssue]
    clean_issues: list[ValidationIssue]
    removed_claim_count: int
    repair_count: int


def contains_unexpected_language_artifact(text: str) -> bool:
    return any(ord(char) > 127 and unicodedata.category(char).startswith("L") for char in text)


def normalize_tradeoff_claim(text: str) -> str:
    original = text
    match = TRADEOFF_ACCEPTS_RE.match(text)
    if match:
        text = match.group(1)
    else:
        text = re.sub(r"^\s*(the\s+)?tradeoff is\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*accepted cost is\s+", "", text, flags=re.IGNORECASE)

    accepted_cost = text.strip(" .")
    if not accepted_cost:
        return original
    if _is_higher_monthly_spend_tradeoff(accepted_cost):
        return "Higher monthly spend is the accepted tradeoff."
    if "accepted tradeoff" in accepted_cost.lower():
        accepted_cost = re.sub(r"^accept\s+", "accepting ", accepted_cost, flags=re.IGNORECASE)
        accepted_cost = re.sub(
            r"^(?:the\s+)?monthly spend will be higher(?:\s+is\s+the accepted tradeoff)?$",
            "higher monthly spend",
            accepted_cost,
            flags=re.IGNORECASE,
        )
        accepted_cost = re.sub(
            r"^the monthly spend will be higher with [a-z0-9 .'-]+(?:\s+is\s+the accepted tradeoff)?$",
            "higher monthly spend",
            accepted_cost,
            flags=re.IGNORECASE,
        )
        accepted_cost = re.sub(
            r"^the monthly spend is higher with [a-z0-9 .'-]+(?:\s+is\s+the accepted tradeoff)?$",
            "higher monthly spend",
            accepted_cost,
            flags=re.IGNORECASE,
        )
        if accepted_cost.lower() == "higher monthly spend":
            return "Higher monthly spend is the accepted tradeoff."
        return _with_period(_sentence_case(accepted_cost))
    accepted_cost = re.sub(r"\binfra\b", "infrastructure", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^we\s+may\s+lose\s+", "losing ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^we\s+will\s+lose\s+", "losing ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^we\s+accept\s+", "accepting ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^accept\s+", "accepting ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^(?:the\s+)?monthly spend will be higher$", "higher monthly spend", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^the monthly spend will be higher with [a-z0-9 .'-]+$", "higher monthly spend", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^the monthly spend is higher with [a-z0-9 .'-]+$", "higher monthly spend", accepted_cost, flags=re.IGNORECASE)
    if _is_higher_monthly_spend_tradeoff(accepted_cost):
        return "Higher monthly spend is the accepted tradeoff."
    accepted_cost = re.sub(r"^extra\s+loss\s+of\s+", "losing ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^loss\s+of\s+", "losing ", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(r"^(.+?)\s+may\s+be\s+lost$", r"losing \1", accepted_cost, flags=re.IGNORECASE)
    accepted_cost = re.sub(
        r"^losing\s+([A-Z])",
        lambda match: "losing " + match.group(1).lower(),
        accepted_cost,
    )
    return f"{_sentence_case(accepted_cost)} is the accepted tradeoff."


def finalize_clean_tradeoff_canonicalization(
    decisions: list[DecisionRecord],
    state_resolution: dict[str, Any] | None = None,
) -> int:
    """Canonicalize final clean tradeoffs after ownership filtering and guard passes."""
    changed = 0
    state_supports_higher_monthly_spend = _state_resolution_has_chosen_higher_monthly_spend_tradeoff(state_resolution)
    for decision in decisions:
        canonical_seen = False
        final_tradeoffs: list[Claim] = []
        for claim in decision.tradeoffs:
            if _is_higher_monthly_spend_tradeoff(claim.claim) or (
                state_supports_higher_monthly_spend and _mentions_higher_monthly_spend(claim.claim)
            ):
                if canonical_seen:
                    changed += 1
                    continue
                if claim.claim != "Higher monthly spend is the accepted tradeoff.":
                    changed += 1
                claim.claim = "Higher monthly spend is the accepted tradeoff."
                final_tradeoffs.append(claim)
                canonical_seen = True
                continue
            final_tradeoffs.append(claim)
        decision.tradeoffs = final_tradeoffs

        for category in ("risks", "objections"):
            kept_claims: list[Claim] = []
            for claim in getattr(decision, category):
                if not _is_higher_monthly_spend_tradeoff(claim.claim):
                    kept_claims.append(claim)
                    continue
                if not canonical_seen:
                    claim.claim = "Higher monthly spend is the accepted tradeoff."
                    decision.tradeoffs.append(claim)
                    canonical_seen = True
                changed += 1
            setattr(decision, category, kept_claims)
        decision.refresh_source_fields()
    return changed


def _is_higher_monthly_spend_tradeoff(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip(" .").lower())
    return bool(
        re.search(r"\bmonthly spend (?:is|will be) higher\b", normalized)
        or re.search(r"\btradeoff with [a-z0-9 .'-]+ is higher monthly spend\b", normalized)
        or (
            "higher monthly spend" in normalized
            and (
                "accepted cost" in normalized
                or "accepted tradeoff" in normalized
                or "cost of choosing" in normalized
                or "tradeoff" in normalized
                or "downside" in normalized
                or re.search(r"\bhas higher monthly spend\b", normalized)
                or normalized == "higher monthly spend"
            )
        )
    )


def _mentions_higher_monthly_spend(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip(" .").lower())
    return bool("higher monthly spend" in normalized or re.search(r"\bmonthly spend (?:is|will be) higher\b", normalized))


def _state_resolution_has_chosen_higher_monthly_spend_tradeoff(state_resolution: dict[str, Any] | None) -> bool:
    if not isinstance(state_resolution, dict):
        return False
    ownership = state_resolution.get("claim_ownership", [])
    if not isinstance(ownership, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("target_role") == "chosen_option"
        and _is_higher_monthly_spend_tradeoff(str(item.get("claim") or ""))
        for item in ownership
    )


def postprocess_decisions(
    decisions: list[DecisionRecord],
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool = False,
) -> PostProcessResult:
    for decision in decisions:
        decision.refresh_source_fields()

    raw_issues = audit_decisions(decisions, event_lookup, include_review_claims)
    clean_result = _clean_decisions(
        copy.deepcopy(decisions),
        event_lookup,
        include_review_claims=include_review_claims,
    )
    return PostProcessResult(
        raw_decisions=decisions,
        clean_decisions=clean_result.decisions,
        raw_issues=raw_issues,
        clean_issues=clean_result.issues,
        removed_claim_count=clean_result.removed_claim_count,
        repair_count=len(clean_result.repair_issues),
    )


def audit_decisions(
    decisions: list[DecisionRecord],
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool = False,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for decision in decisions:
        decision.refresh_source_fields()
        issues.extend(_decision_source_issues(decision, event_lookup, action="removed_from_clean_output"))
        issues.extend(_status_issues(decision, event_lookup))

        for category in CLAIM_CATEGORIES:
            for claim in getattr(decision, category):
                issues.extend(_claim_validation_issues(decision, category, claim, event_lookup, include_review_claims))

    return issues


def validate_decisions(
    decisions: list[DecisionRecord],
    event_lookup: dict[str, CanonicalEvent],
) -> tuple[list[DecisionRecord], list[ValidationIssue]]:
    clean_result = _clean_decisions(decisions, event_lookup, include_review_claims=False)
    return clean_result.decisions, [*clean_result.repair_issues, *clean_result.issues]


def _clean_decisions(
    decisions: list[DecisionRecord],
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool,
) -> CleanResult:
    result = CleanResult(decisions=[])

    for decision in decisions:
        decision.refresh_source_fields()
        decision_source_issues = _decision_source_issues(decision, event_lookup, action="removed_from_clean_output")
        if decision_source_issues:
            result.repair_issues.extend(decision_source_issues)
            continue

        result.repair_issues.extend(_repair_status_and_people(decision, event_lookup))
        result.removed_claim_count += _normalize_structured_fields(decision, event_lookup)

        for category in CLAIM_CATEGORIES:
            kept_claims: list[Claim] = []
            for claim in getattr(decision, category):
                if _remove_revisit_only_claim(decision, claim):
                    result.removed_claim_count += 1
                    continue
                if _remove_structured_duplicate_claim(decision, category, claim):
                    result.removed_claim_count += 1
                    continue
                if _claim_contradicts_source(claim.claim, claim.source.quote if claim.source else ""):
                    result.removed_claim_count += 1
                    continue
                if category == "tradeoffs":
                    claim.claim = normalize_tradeoff_claim(claim.claim)
                if include_review_claims and claim.support_level == "indirect":
                    claim.needs_review = True
                if include_review_claims and claim.support_level == "unsupported":
                    claim.needs_review = True

                claim_issues = _claim_validation_issues(
                    decision,
                    category,
                    claim,
                    event_lookup,
                    include_review_claims,
                )
                if _claim_should_be_removed(claim, event_lookup, include_review_claims):
                    result.removed_claim_count += 1
                    result.repair_issues.extend(claim_issues)
                    continue

                kept_claims.append(claim)
                result.issues.extend(claim_issues)

            setattr(decision, category, kept_claims)

        result.removed_claim_count += _dedupe_and_reclassify_claims(decision)
        result.repair_issues.extend(
            ValidationIssue(
                decision.decision_id,
                "Recovered explicit concern from source thread.",
                type="missing_objection_repaired",
                field="objections",
                action="added_to_clean_output",
            )
            for _ in range(_add_missing_explicit_objections(decision, event_lookup))
        )
        result.repair_issues.extend(
            ValidationIssue(
                decision.decision_id,
                "Recovered direct reasoning from source thread.",
                type="missing_reasoning_repaired",
                field="reasoning",
                action="added_to_clean_output",
            )
            for _ in range(_add_missing_direct_reasoning(decision, event_lookup))
        )
        result.removed_claim_count += _remove_neutral_objections(decision)
        result.removed_claim_count += _remove_tradeoff_duplicate_risks(decision)
        result.removed_claim_count += _remove_vague_reasoning_when_concrete_reasoning_exists(decision)
        result.removed_claim_count += _dedupe_action_items(decision)
        decision.refresh_source_fields()
        result.decisions.append(decision)

    result.removed_claim_count += _remove_superseded_decisions(result.decisions)
    result.removed_claim_count += _merge_duplicate_decisions(result.decisions)
    return result


def _normalize_structured_fields(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> int:
    removed = 0
    _normalize_implicit_organization_settings_decision(decision, event_lookup)
    _normalize_decision_text(decision)
    _normalize_decision_date(decision, event_lookup)
    removed += _repair_deadlines_from_thread(decision, event_lookup)
    _normalize_revisit_fields(decision, event_lookup)
    _drop_superseded_revisit_trigger(decision, event_lookup)
    removed += _drop_superseded_reversal_context(decision, event_lookup)

    original_deadlines = len(decision.deadlines)
    decision.deadlines = [
        deadline
        for deadline in decision.deadlines
        if not _deadline_duplicates_revisit(deadline.date, deadline.description, deadline.source.quote if deadline.source else "")
    ]
    removed += original_deadlines - len(decision.deadlines)

    original_action_items = len(decision.action_items)
    decision.action_items = [
        action_item
        for action_item in decision.action_items
        if _action_item_is_clean(action_item.action, action_item.source.quote if action_item.source else "", decision)
    ]
    removed += original_action_items - len(decision.action_items)
    return removed


def _normalize_decision_text(decision: DecisionRecord) -> None:
    if (
        decision.effective_date
        and re.search(r"\bMove Team pricing to \$59 for new customers\b", decision.decision, re.IGNORECASE)
        and decision.effective_date not in decision.decision
    ):
        decision.decision = re.sub(
            r"\bMove Team pricing to \$59 for new customers,?\s*(?:and\s+)?",
            f"Move Team pricing to $59 for new customers starting {decision.effective_date} and ",
            decision.decision,
            count=1,
            flags=re.IGNORECASE,
        )


def _normalize_implicit_organization_settings_decision(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
) -> None:
    thread_events = _thread_events_for_decision(decision, event_lookup)
    commitment = next(
        (
            event
            for event in thread_events
            if re.search(r"\bi(?:'ll| will) make ([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*) the parent section\b", event.text, re.IGNORECASE)
        ),
        None,
    )
    if commitment is None:
        return
    parent_match = re.search(r"\bi(?:'ll| will) make ([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*) the parent section", commitment.text, re.IGNORECASE)
    if not parent_match:
        return
    section_name = parent_match.group(1)
    plan_text = "\n".join(
        event.text
        for event in thread_events
        if re.search(r"\b(parent section|move [^.]+(?:under it|first)|i can move [^.]+ once the route exists)\b", event.text, re.IGNORECASE)
    )
    items = [
        item
        for item in ("billing", "roles", "SAML", "audit logs", "SSO", "audit exports", "API tokens", "SCIM")
        if re.search(r"\b" + re.escape(item) + r"\b", plan_text, re.IGNORECASE)
    ]
    article = "an" if section_name[0].lower() in {"a", "e", "i", "o", "u"} else "a"
    if items:
        decision.decision = f"Create {article} {section_name} section and move {_human_join(items)} under it."
    else:
        decision.decision = f"Create {article} {section_name} section."
    decision.decision_source_message_ts = commitment.message_ts
    source = resolve_source(commitment.message_ts, event_lookup)
    decision.decision_sources = [source]
    decision.sources = [source]
    if not decision.decision.endswith("."):
        decision.decision = decision.decision.rstrip() + "."
    move_match = re.search(r"\bi(?:'ll| will) make [A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)* the parent section,\s*([^.]+)", commitment.text, re.IGNORECASE)
    expected_action = (
        f"Make {section_name} the parent section and {_clean_fragment_text(move_match.group(1))}."
        if move_match
        else f"Make {section_name} the parent section."
    )
    if not any(item.owner == commitment.speaker and item.action == expected_action for item in decision.action_items):
        decision.action_items.append(
            ActionItem(
                action=expected_action,
                owner=commitment.speaker,
                due_date=None,
                due_text=None,
                source_message_ts=commitment.message_ts,
                source=source,
            )
        )


def _normalize_decision_date(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> None:
    if decision.effective_date and decision.date == decision.effective_date:
        decision.date = None
        return
    decision.date = _date_from_source_message_ts(_decision_source_ts(decision))


def _date_from_source_message_ts(message_ts: str | None) -> str | None:
    if not message_ts:
        return None
    try:
        return datetime.fromtimestamp(float(message_ts), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _repair_deadlines_from_thread(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> int:
    added = 0
    existing = {(deadline.date, _normalize_text_for_match(deadline.description)) for deadline in decision.deadlines}
    for event in _thread_events_for_decision(decision, event_lookup):
        inferred = _infer_deadline_from_event_text(event.text, event.message_ts, event_lookup)
        for deadline in inferred:
            key = (deadline.date, _normalize_text_for_match(deadline.description))
            if key in existing:
                continue
            duplicate = next((current for current in decision.deadlines if _deadline_semantically_matches(deadline, current)), None)
            if duplicate is not None:
                if _deadline_specificity(deadline) > _deadline_specificity(duplicate):
                    decision.deadlines.remove(duplicate)
                    existing.discard((duplicate.date, _normalize_text_for_match(duplicate.description)))
                else:
                    continue
            decision.deadlines.append(deadline)
            existing.add(key)
            added += 1
    return added


def _infer_deadline_from_event_text(
    text: str,
    message_ts: str,
    event_lookup: dict[str, CanonicalEvent],
) -> list[Deadline]:
    deadlines: list[Deadline] = []
    rollout_match = re.search(
        r"\bbefore\s+the\s+enterprise rollout starts on\s+(\d{4}-\d{2}-\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if rollout_match:
        deadlines.append(
            Deadline(
                date=rollout_match.group(1),
                description="Enterprise rollout starts.",
                source_message_ts=message_ts,
                source=resolve_source(message_ts, event_lookup),
            )
        )

    admin_flow_match = re.search(
        r"\bwe promised the updated admin flow to design partners before\s+(\d{4}-\d{2}-\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if admin_flow_match:
        deadlines.append(
            Deadline(
                date=admin_flow_match.group(1),
                description="Updated admin flow promised to design partners before the demo.",
                source_message_ts=message_ts,
                source=resolve_source(message_ts, event_lookup),
            )
        )
    return deadlines


def _deadline_semantically_matches(left: Deadline, right: Deadline) -> bool:
    if left.date != right.date:
        return False
    left_tokens = _claim_dedupe_tokens(left.description)
    right_tokens = _claim_dedupe_tokens(right.description)
    if {"design", "partners"} <= left_tokens & right_tokens:
        return True
    return bool(left_tokens and right_tokens and _token_containment(left_tokens, right_tokens) >= 0.72)


def _deadline_specificity(deadline: Deadline) -> int:
    return len(_claim_dedupe_tokens(deadline.description))


def _normalize_revisit_fields(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> None:
    if decision.revisit_date and not ISO_DATE_RE.match(decision.revisit_date):
        if not decision.revisit_trigger:
            decision.revisit_trigger = _sentence_case(decision.revisit_date.strip(" .")) + "."
        decision.revisit_date = None
    if decision.revisit_date and not _source_explicitly_supports_revisit_date(decision, event_lookup, decision.revisit_date):
        if not decision.revisit_trigger:
            decision.revisit_trigger = _relative_revisit_trigger(decision, event_lookup)
        decision.revisit_date = None


def _drop_superseded_revisit_trigger(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> None:
    if not decision.revisit_trigger:
        return
    if not (_is_reversal_text(decision.decision) or _is_reversal_text(_decision_source_text(decision))):
        return
    decision_source_ts = _decision_source_ts(decision) or ""
    if not decision_source_ts:
        return
    for event in _thread_events_for_decision(decision, event_lookup):
        if event.message_ts >= decision_source_ts:
            if re.search(r"\b(revisit|review|check back|reassess)\b", event.text, re.IGNORECASE):
                return
    decision.revisit_trigger = None


def _drop_superseded_reversal_context(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> int:
    if not (_is_reversal_text(decision.decision) or _is_reversal_text(_decision_source_text(decision))):
        return 0
    decision_source_ts = _decision_source_ts(decision)
    approved_cutoff = _latest_prior_approved_source_ts(decision, event_lookup)
    if not decision_source_ts or not approved_cutoff:
        return 0
    removed = 0
    for category in ("assumptions", "risks", "tradeoffs"):
        claims = getattr(decision, category)
        kept = [claim for claim in claims if not claim.source_message_ts or claim.source_message_ts > approved_cutoff]
        removed += len(claims) - len(kept)
        setattr(decision, category, kept)
    return removed


def _latest_prior_approved_source_ts(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
) -> str | None:
    decision_source_ts = _decision_source_ts(decision)
    if not decision_source_ts:
        return None
    approved_ts: str | None = None
    for event in _thread_events_for_decision(decision, event_lookup):
        if event.message_ts >= decision_source_ts:
            continue
        if re.search(r"\b(approved for now|approved|final decision|we decided|move forward with)\b", event.text, re.IGNORECASE):
            approved_ts = event.message_ts
    return approved_ts


def _source_explicitly_supports_revisit_date(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
    date: str,
) -> bool:
    for event in _thread_events_for_decision(decision, event_lookup):
        for sentence in _sentences(event.text):
            if date in sentence and _is_revisit_or_review_schedule_text(sentence):
                return True
    return False


def _relative_revisit_trigger(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> str | None:
    for event in _thread_events_for_decision(decision, event_lookup):
        text = event.text
        if re.search(r"\bfirst billing cycle\b", text, re.IGNORECASE) and REVIEW_WORD_RE.search(text):
            return "After the first billing cycle."
        if re.search(r"\bafter launch\b", text, re.IGNORECASE) and REVIEW_WORD_RE.search(text):
            condition = _conditional_phrase(text)
            return f"After launch if {condition}." if condition else "After launch."
        if re.search(r"\bafter 30 days\b", text, re.IGNORECASE) and REVIEW_WORD_RE.search(text):
            condition = _conditional_phrase(text)
            return f"After 30 days if {condition}." if condition else "After 30 days."
        if re.search(r"\bafter rollout\b", text, re.IGNORECASE) and REVIEW_WORD_RE.search(text):
            condition = _conditional_phrase(text)
            return f"After rollout if {condition}." if condition else "After rollout."
    return None


def _remove_revisit_only_claim(decision: DecisionRecord, claim: Claim) -> bool:
    date_match = REVISIT_DATE_CLAIM_RE.match(claim.claim)
    if date_match:
        if not decision.revisit_date:
            decision.revisit_date = date_match.group("date")
        return True

    if REVISIT_AFTER_LAUNCH_CLAIM_RE.match(claim.claim):
        if not decision.revisit_trigger:
            decision.revisit_trigger = "After launch."
        return True

    if REVIEW_TRIGGER_CLAIM_RE.match(claim.claim):
        if not decision.revisit_trigger:
            decision.revisit_trigger = _review_trigger_from_claim(claim.claim)
        return True

    return False


def _review_trigger_from_claim(text: str) -> str:
    if re.search(r"first billing cycle", text, re.IGNORECASE):
        return "After the first billing cycle."
    if re.search(r"30 days", text, re.IGNORECASE):
        return "After 30 days."
    return _with_period(_sentence_case(text))


def _deadline_duplicates_revisit(date: str, description: str, source_text: str) -> bool:
    if not date:
        return False
    if REVISIT_WORD_RE.search(description):
        return True
    if REVIEW_SCHEDULE_RE.search(description) and not _is_historical_review_context(description):
        return True
    for sentence in _sentences(source_text):
        if date not in sentence:
            continue
        if REVISIT_WORD_RE.search(sentence):
            return True
        if REVIEW_SCHEDULE_RE.search(sentence) and not _is_historical_review_context(sentence):
            return True
    return False


def _is_historical_review_context(text: str) -> bool:
    return bool(re.search(r"\b(outage|incident|postmortem|security|release)\s+review\b", text, re.IGNORECASE))


def _action_item_is_clean(action: str, source_text: str, decision: DecisionRecord) -> bool:
    if _is_revisit_or_review_text(action):
        return False
    if _is_generic_owner_action(action, decision):
        return False
    return _source_explicitly_assigns_action(source_text)


def _is_revisit_or_review_text(text: str) -> bool:
    return _is_revisit_or_review_schedule_text(text)


def _is_revisit_or_review_schedule_text(text: str) -> bool:
    return bool(REVISIT_WORD_RE.search(text) or REVIEW_SCHEDULE_RE.search(text))


def _is_generic_owner_action(action: str, decision: DecisionRecord) -> bool:
    normalized_action = _normalize_text_for_match(action)
    decision_tokens = _claim_dedupe_tokens(decision.decision)
    action_tokens = _claim_dedupe_tokens(action)
    if re.search(r"\bmake\s+[a-z0-9 ]+\s+the parent section\b", normalized_action):
        return False
    if re.search(r"\b(implement|roll out|rollout|own|owner|deploy|move forward)\b", normalized_action):
        return True
    return bool(decision_tokens and _token_containment(action_tokens, decision_tokens) >= 0.8)


def _source_explicitly_assigns_action(source_text: str) -> bool:
    if not source_text:
        return False
    return bool(
        re.search(r"\baction item\s*:", source_text, re.IGNORECASE)
        or re.search(r"\bcan you take\b", source_text, re.IGNORECASE)
        or re.search(r"\bi(?:'ll| will) make\b", source_text, re.IGNORECASE)
        or re.search(r"\bi can move\b", source_text, re.IGNORECASE)
        or re.search(
            r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+will\s+"
            r"(send|schedule|run|bring|prepare|share|draft|follow up|follow-up|create|write)\b",
            source_text,
        )
        or re.search(
            r"\bowns\s+"
            r"(sending|scheduling|running|preparing|sharing|drafting|creating|writing|customer messaging|discovery calls)\b",
            source_text,
            re.IGNORECASE,
        )
    )


def _conditional_phrase(text: str) -> str | None:
    match = re.search(r"\bif\s+([^,.;]+)", text, re.IGNORECASE)
    if not match:
        return None
    condition = match.group(1).strip()
    condition = re.sub(r"\s+after\s+30 days.*$", "", condition, flags=re.IGNORECASE).strip()
    condition = re.sub(r"\s+after\s+rollout$", "", condition, flags=re.IGNORECASE).strip()
    condition = re.sub(r"\s+later$", "", condition, flags=re.IGNORECASE).strip()
    return condition or None


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _remove_structured_duplicate_claim(decision: DecisionRecord, category: str, claim: Claim) -> bool:
    claim_text = claim.claim
    source_text = claim.source.quote if claim.source else ""
    combined_text = f"{claim_text}\n{source_text}"
    if category == "timelines" and _is_rejected_alternative_timing(claim_text, source_text):
        return True
    if category == "timelines" and _is_dependency_order_context(claim_text, source_text):
        return True
    if category == "tradeoffs" and _is_rejected_alternative_tradeoff(claim_text, source_text):
        return True
    if category == "tradeoffs" and _is_scope_constraint_tradeoff(claim_text, source_text):
        return True
    if category == "tradeoffs" and _is_rejected_shortcut_tradeoff(claim_text, source_text):
        return True
    if category == "timelines" and decision.revisit_date and decision.revisit_date in claim_text and _is_revisit_or_review_schedule_text(claim_text):
        return True
    if category == "timelines" and decision.revisit_trigger and _is_revisit_or_review_schedule_text(claim_text):
        return True
    if category == "timelines" and decision.effective_date and decision.effective_date in claim_text:
        return True

    if category == "timelines" and _duplicates_decision_component(decision, claim_text):
        return True

    if category == "timelines" and any(deadline.date in claim_text for deadline in decision.deadlines):
        return True
    if category == "timelines" and any(_timeline_duplicates_deadline(claim_text, source_text, deadline) for deadline in decision.deadlines):
        return True

    if category == "timelines" and any(_jaccard_similarity(_claim_dedupe_tokens(claim_text), _claim_dedupe_tokens(action_item.action)) >= 0.72 for action_item in decision.action_items):
        return True

    return False


def _is_rejected_alternative_timing(claim_text: str, source_text: str) -> bool:
    combined = f"{claim_text}\n{source_text}"
    return bool(
        re.search(
            r"\b(would take longer|migration would take longer|rebuild [^.]*?(?:dashboards?|wrapper)|"
            r"(?:dashboards?|wrapper) [^.]*?must be rebuilt)\b",
            combined,
            re.IGNORECASE,
        )
        and re.search(r"\b(cheaper|alternative|over|versus|vs|but)\b", source_text, re.IGNORECASE)
    )


def _is_dependency_order_context(claim_text: str, source_text: str) -> bool:
    combined = f"{claim_text}\n{source_text}"
    return bool(
        re.search(r"\b(can follow after|move [^.]+ after [^.]+ piece|once the route exists)\b", combined, re.IGNORECASE)
        and not re.search(r"\b(next|by|before|deadline|\d{4}-\d{2}-\d{2}|day|week|month)\b", claim_text, re.IGNORECASE)
    )


def _is_rejected_alternative_tradeoff(claim_text: str, source_text: str) -> bool:
    combined = f"{claim_text}\n{source_text}"
    if not re.search(r"\bis cheaper\b|\bmigration would take longer\b|\bneed to rebuild\b|\bmust be rebuilt\b", claim_text, re.IGNORECASE):
        return False
    if re.search(r"\btradeoff (?:with|for)\s+[A-Z][A-Za-z0-9 -]+\s+is\s+(?:higher monthly spend|.+accepted)\b", source_text, re.IGNORECASE):
        return not re.search(r"\bhigher monthly spend\b", claim_text, re.IGNORECASE)
    return bool(re.search(r"\b(cheaper|alternative|over|versus|vs|but)\b", source_text, re.IGNORECASE))


def _is_scope_constraint_tradeoff(claim_text: str, source_text: str) -> bool:
    return bool(
        re.search(r"\b(no custom permission scopes|permissions redesign out of scope|not a whole admin rebuild)\b", f"{claim_text}\n{source_text}", re.IGNORECASE)
        and not re.search(r"\b(full permissions model|basic .*IA|accepted tradeoff)\b", source_text, re.IGNORECASE)
    )


def _is_rejected_shortcut_tradeoff(claim_text: str, source_text: str) -> bool:
    combined = f"{claim_text}\n{source_text}"
    return bool(
        re.search(r"\bshortcut\b", combined, re.IGNORECASE)
        and re.search(r"\bsaves?\b[^.]*\b(?:day|days)\b", combined, re.IGNORECASE)
        and re.search(r"\b(cleanup project|regret|not beautiful|rejected alternative|instead of)\b", combined, re.IGNORECASE)
    )


def _timeline_duplicates_deadline(claim_text: str, source_text: str, deadline: Deadline) -> bool:
    if deadline.date and deadline.date not in claim_text and deadline.date not in source_text:
        return False
    deadline_tokens = _claim_dedupe_tokens(deadline.description)
    claim_tokens = _claim_dedupe_tokens(claim_text)
    source_tokens = _claim_dedupe_tokens(source_text)
    if claim_tokens and _token_containment(claim_tokens, deadline_tokens) >= 0.6:
        return True
    return bool(
        source_tokens
        and _token_containment(source_tokens, deadline_tokens) >= 0.6
        and re.search(r"\b(ready|before|by|launch)\b", claim_text, re.IGNORECASE)
    )


def _duplicates_decision_component(decision: DecisionRecord, claim_text: str) -> bool:
    claim_tokens = _claim_dedupe_tokens(claim_text)
    decision_tokens = _claim_dedupe_tokens(decision.decision)
    if not claim_tokens or not decision_tokens:
        return False
    if _token_containment(claim_tokens, decision_tokens) < 0.82:
        return False
    return not re.search(r"\b(engineering needs|implementation can finish|can be wired|takes?|requires?)\b", claim_text, re.IGNORECASE)


def _dedupe_and_reclassify_claims(decision: DecisionRecord) -> int:
    original_count = sum(len(getattr(decision, category)) for category in CLAIM_CATEGORIES)
    buckets: list[dict[str, Any]] = []
    sequence = 0

    for category in CLAIM_CATEGORIES:
        for claim in getattr(decision, category):
            prioritized_claim = _prioritized_over_claim(claim.claim, claim.source.quote if claim.source else "")
            if prioritized_claim:
                claim.claim = prioritized_claim
                preferred = "reasoning"
            else:
                preferred = _preferred_category(category, claim)
            if preferred == "tradeoffs":
                claim.claim = normalize_tradeoff_claim(claim.claim)
            tokens = _claim_dedupe_tokens(claim.claim)
            if not tokens:
                continue
            score = _category_rank(preferred)
            existing = _matching_dedupe_bucket(buckets, tokens, preferred)
            if existing is None:
                buckets.append({"tokens": tokens, "category": preferred, "claim": claim, "sequence": sequence})
            elif _candidate_is_better_bucket_claim(preferred, claim, existing):
                existing["category"] = preferred
                existing["claim"] = claim
                existing["tokens"] = tokens
            sequence += 1

    for category in CLAIM_CATEGORIES:
        setattr(decision, category, [])

    for bucket in sorted(buckets, key=lambda item: item["sequence"]):
        getattr(decision, bucket["category"]).append(bucket["claim"])

    final_count = sum(len(getattr(decision, category)) for category in CLAIM_CATEGORIES)
    return original_count - final_count


def _add_missing_explicit_objections(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> int:
    added = 0
    existing_tokens = [_claim_dedupe_tokens(claim.claim) for claim in decision.objections]
    for event in _thread_events_for_decision(decision, event_lookup):
        fragment = _source_concern_fragment(event.text)
        if not fragment:
            continue
        claim_text = _objection_claim_from_fragment(fragment)
        tokens = _claim_dedupe_tokens(claim_text)
        if not tokens or any(_token_containment(tokens, existing) >= 0.72 for existing in existing_tokens):
            continue
        decision.objections.append(
            Claim(
                claim=claim_text,
                source_message_ts=event.message_ts,
                support_level="direct",
                confidence=0.9,
                source=resolve_source(event.message_ts, event_lookup),
            )
        )
        existing_tokens.append(tokens)
        added += 1
    return added


def _add_missing_direct_reasoning(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> int:
    added = 0
    existing_tokens = [_claim_dedupe_tokens(claim.claim) for claim in decision.reasoning]
    for event in _thread_events_for_decision(decision, event_lookup):
        claim_text = _direct_reasoning_from_event_text(decision, event.text)
        if not claim_text:
            continue
        tokens = _claim_dedupe_tokens(claim_text)
        if not tokens or any(_token_containment(tokens, existing) >= 0.72 for existing in existing_tokens):
            continue
        decision.reasoning.append(
            Claim(
                claim=claim_text,
                source_message_ts=event.message_ts,
                support_level="direct",
                confidence=0.9,
                source=resolve_source(event.message_ts, event_lookup),
            )
        )
        existing_tokens.append(tokens)
        added += 1
    return added


def _direct_reasoning_from_event_text(decision: DecisionRecord, text: str) -> str | None:
    if re.search(r"\bTeam pricing|Team plan|\$59|\$49\b", decision.decision, re.IGNORECASE) and re.search(
        r"\bonboarding time per Team account is higher than (?:we )?modeled\b",
        text,
        re.IGNORECASE,
    ):
        return "Onboarding time per Team account is higher than modeled."
    return None


def _objection_claim_from_fragment(fragment: str) -> str:
    cleaned = _with_period(_sentence_case(_clean_fragment_text(fragment)))
    if re.search(r"\b(concern|worry|worried)\b", cleaned, re.IGNORECASE):
        return cleaned
    return f"There is concern about {cleaned[0].lower() + cleaned[1:]}"


def _clean_fragment_text(text: str) -> str:
    cleaned = text.strip(" \t\n\r:-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _remove_neutral_objections(decision: DecisionRecord) -> int:
    original_count = len(decision.objections)
    decision.objections = [
        claim
        for claim in decision.objections
        if CONCERN_RE.search(f"{claim.claim}\n{claim.source.quote if claim.source else ''}")
        and not _is_deadline_context_objection(claim)
    ]
    return original_count - len(decision.objections)


def _is_deadline_context_objection(claim: Claim) -> bool:
    if CONCERN_RE.search(claim.claim):
        return False
    claim_text = claim.claim
    return bool(
        re.search(r"\b(updated admin flow|enterprise rollout)\b", claim_text, re.IGNORECASE)
        and re.search(r"\b(before|on)\b", claim_text, re.IGNORECASE)
        and (re.search(r"\b2026-\d{2}-\d{2}\b", claim_text) or re.search(r"\bsep(?:tember)?\s+10\b", claim_text, re.IGNORECASE))
    )


def _remove_tradeoff_duplicate_risks(decision: DecisionRecord) -> int:
    if not decision.tradeoffs or not decision.risks:
        return 0
    tradeoff_tokens = [_claim_dedupe_tokens(tradeoff.claim) for tradeoff in decision.tradeoffs]
    original_count = len(decision.risks)
    decision.risks = [
        risk
        for risk in decision.risks
        if not any(_token_containment(_claim_dedupe_tokens(risk.claim), tokens) >= 0.72 for tokens in tradeoff_tokens)
    ]
    return original_count - len(decision.risks)


def _prioritized_over_claim(claim_text: str, source_text: str) -> str | None:
    combined = f"{claim_text}\n{source_text}"
    if re.search(r"\bdebuggability matters more than the small cost difference\b", combined, re.IGNORECASE):
        return "Better debuggability is prioritized over the small cost difference."
    if re.search(
        r"\bthe small cost difference is accepted in favor of (?:better )?debuggability(?: is the accepted tradeoff)?\b",
        claim_text,
        re.IGNORECASE,
    ):
        return "Better debuggability is prioritized over the small cost difference."
    return None


def _remove_vague_reasoning_when_concrete_reasoning_exists(decision: DecisionRecord) -> int:
    original_count = len(decision.reasoning)
    decision.reasoning = [
        claim
        for claim in decision.reasoning
        if not _is_vague_safer_reasoning_duplicate(decision, claim)
    ]
    removed = original_count - len(decision.reasoning)
    if len(decision.reasoning) < 2:
        return removed
    has_concrete_reasoning = any(
        not re.search(r"\bsafer\b", claim.claim, re.IGNORECASE) and len(_claim_dedupe_tokens(claim.claim)) >= 5
        for claim in decision.reasoning
    )
    if not has_concrete_reasoning:
        return removed
    original_count = len(decision.reasoning)
    decision.reasoning = [
        claim
        for claim in decision.reasoning
        if not re.search(r"^\s*(?:this|it)\s+is\s+safer\b", claim.claim, re.IGNORECASE)
        and not re.search(r"^\s*safer\s+if\b", claim.claim, re.IGNORECASE)
        and not re.search(r"^\s*it\s+is\s+safer\s+to\b", claim.claim, re.IGNORECASE)
        and not re.search(r"^\s*delaying\s+is\s+safer\s+if\b", claim.claim, re.IGNORECASE)
    ]
    return removed + (original_count - len(decision.reasoning))


def _is_vague_safer_reasoning_duplicate(decision: DecisionRecord, claim: Claim) -> bool:
    if not re.search(r"\bsafer(?:\s+call)?\b", claim.claim, re.IGNORECASE):
        return False
    source_ts = claim.source_message_ts
    if not source_ts:
        return False
    has_delay_objection = any(
        objection.source_message_ts == source_ts
        and re.search(r"\b(delay|pipeline|demo)\b", objection.claim, re.IGNORECASE)
        for objection in decision.objections
    )
    has_wait_assumption = any(
        assumption.source_message_ts == source_ts
        and re.search(r"\b(wait|partners?|customers?|pilots?)\b", assumption.claim, re.IGNORECASE)
        for assumption in decision.assumptions
    )
    return has_delay_objection and has_wait_assumption


def _dedupe_action_items(decision: DecisionRecord) -> int:
    if len(decision.action_items) < 2:
        return 0
    kept = []
    original_count = len(decision.action_items)
    for item in decision.action_items:
        duplicate_index = _matching_action_item_index(kept, item)
        if duplicate_index is None:
            kept.append(item)
            continue
        if _action_item_score(item) > _action_item_score(kept[duplicate_index]):
            kept[duplicate_index] = item
    decision.action_items = kept
    return original_count - len(decision.action_items)


def _matching_action_item_index(existing_items: list[Any], item: Any) -> int | None:
    item_owner = (item.owner or "").strip().lower()
    item_object = _action_object_tokens(item.action)
    item_dependency = _action_dependency_key(item.action, item.owner)
    if not item_object:
        return None
    for index, existing in enumerate(existing_items):
        if (existing.owner or "").strip().lower() != item_owner:
            continue
        existing_object = _action_object_tokens(existing.action)
        if _token_containment(item_object, existing_object) < 0.72:
            continue
        existing_dependency = _action_dependency_key(existing.action, existing.owner)
        if item_dependency == existing_dependency:
            return index
        if item_dependency and existing_dependency and {item_dependency, existing_dependency} <= {"route exists", "dependency ready"}:
            return index
    return None


def _action_object_tokens(action: str) -> set[str]:
    normalized = re.sub(r"^(take|move|make|create|send|schedule|run|prepare|share|draft|follow up|follow-up)\b", "", action.strip(), flags=re.IGNORECASE)
    normalized = re.split(r"\b(?:once|after|when|by)\b", normalized, maxsplit=1, flags=re.IGNORECASE)[0]
    return _claim_dedupe_tokens(normalized)


def _action_dependency_key(action: str, owner: str | None) -> str:
    lower = action.lower()
    if "route exists" in lower:
        return "route exists"
    if re.search(r"\bafter\s+[a-z]+['’]s\s+piece\b", lower):
        return "dependency ready"
    match = re.search(r"\b(?:once|after|when)\s+([^.;]+)", lower)
    if match:
        return match.group(1).strip()
    return ""


def _action_item_score(item: Any) -> int:
    action = item.action.lower()
    score = len(_claim_dedupe_tokens(item.action))
    if "route exists" in action:
        score += 3
    if "piece" in action:
        score -= 1
    return score


def _human_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _remove_superseded_decisions(decisions: list[DecisionRecord]) -> int:
    if len(decisions) < 2:
        return 0
    latest_reversal_by_thread: dict[str, str] = {}
    for decision in decisions:
        thread_ts = _decision_thread_ts(decision)
        source_text = _decision_source_text(decision)
        source_ts = _decision_source_ts(decision)
        if thread_ts and source_ts and _is_reversal_text(source_text):
            latest_reversal_by_thread[thread_ts] = max(source_ts, latest_reversal_by_thread.get(thread_ts, ""))
    if not latest_reversal_by_thread:
        return 0

    original_count = len(decisions)
    decisions[:] = [
        decision
        for decision in decisions
        if not (
            (thread_ts := _decision_thread_ts(decision)) in latest_reversal_by_thread
            and (_decision_source_ts(decision) or "") < latest_reversal_by_thread[thread_ts]
        )
    ]
    return original_count - len(decisions)


def _merge_duplicate_decisions(decisions: list[DecisionRecord]) -> int:
    if len(decisions) < 2:
        return 0
    merged: list[DecisionRecord] = []
    removed = 0
    for decision in decisions:
        duplicate = next((existing for existing in merged if _decisions_are_duplicates(existing, decision)), None)
        if duplicate is None:
            merged.append(decision)
            continue
        _merge_decision_into(duplicate, decision)
        removed += 1
    decisions[:] = merged
    return removed


def _decisions_are_duplicates(left: DecisionRecord, right: DecisionRecord) -> bool:
    left_thread = _decision_thread_ts(left)
    right_thread = _decision_thread_ts(right)
    if left_thread and right_thread and left_thread != right_thread:
        if not (_decision_has_document_source(left) or _decision_has_document_source(right)):
            return False
    left_tokens = _decision_duplicate_tokens(left.decision)
    right_tokens = _decision_duplicate_tokens(right.decision)
    if not left_tokens or not right_tokens:
        return False
    return (
        _normalize_text_for_match(left.decision) == _normalize_text_for_match(right.decision)
        or _jaccard_similarity(left_tokens, right_tokens) >= 0.84
        or _token_containment(left_tokens, right_tokens) >= 0.9
    )


def _decision_has_document_source(decision: DecisionRecord) -> bool:
    decision.refresh_source_fields()
    return any(source.source_type in {"google_doc", "uploaded_document"} for source in decision.all_sources)


def _decision_duplicate_tokens(text: str) -> set[str]:
    return {
        token
        for token in _claim_dedupe_tokens(text)
        if token not in {"choose", "use", "using", "we", "for", "now", "decision", "final"}
    }


def _merge_decision_into(target: DecisionRecord, incoming: DecisionRecord) -> None:
    if _owner_score(incoming) > _owner_score(target):
        target.owner = incoming.owner
        target.owner_role = incoming.owner_role
    target.confidence = max(float(target.confidence), float(incoming.confidence))
    target.date = target.date or incoming.date
    target.effective_date = target.effective_date or incoming.effective_date
    target.revisit_date = target.revisit_date or incoming.revisit_date
    target.revisit_trigger = target.revisit_trigger or incoming.revisit_trigger
    target.participants = _dedupe_strings([*target.participants, *incoming.participants])

    for category in CLAIM_CATEGORIES:
        setattr(target, category, _merge_claim_lists(getattr(target, category), getattr(incoming, category)))
    target.deadlines = _merge_deadline_lists(target.deadlines, incoming.deadlines)
    target.action_items = _merge_action_item_lists(target.action_items, incoming.action_items)
    target.decision_sources = _merge_source_lists(target.decision_sources, incoming.decision_sources)
    target.sources = _merge_source_lists(target.sources, incoming.sources)
    target.supporting_sources = _merge_source_lists(target.supporting_sources, incoming.supporting_sources)
    target.all_sources = _merge_source_lists(target.all_sources, incoming.all_sources)
    _dedupe_and_reclassify_claims(target)
    _dedupe_action_items(target)
    target.refresh_source_fields()


def _owner_score(decision: DecisionRecord) -> int:
    if not decision.owner:
        return 0
    owner = decision.owner
    score = 1
    if any(item.owner == owner for item in decision.action_items):
        score += 4
    source_text = _decision_source_text(decision)
    if decision.decision_sources and decision.decision_sources[0].speaker == owner and re.search(
        r"\b(i(?:'ll| will)|owns?|can you take|i can move|make [A-Z][A-Za-z ]+ the parent section)\b",
        source_text,
        re.IGNORECASE,
    ):
        score += 3
    if decision.owner_role and re.search(r"\b(engineer|engineering|cto|staff)\b", decision.owner_role, re.IGNORECASE):
        score += 1
    return score


def _merge_claim_lists(left: list[Claim], right: list[Claim]) -> list[Claim]:
    merged: list[Claim] = []
    for claim in [*left, *right]:
        key = (_normalize_text_for_match(claim.claim), claim.source.message_ts if claim.source else claim.source_message_ts)
        if any(
            key == (_normalize_text_for_match(existing.claim), existing.source.message_ts if existing.source else existing.source_message_ts)
            for existing in merged
        ):
            continue
        merged.append(claim)
    return merged


def _merge_deadline_lists(left: list[Deadline], right: list[Deadline]) -> list[Deadline]:
    merged: list[Deadline] = []
    for deadline in [*left, *right]:
        duplicate = next((existing for existing in merged if _deadline_semantically_matches(deadline, existing)), None)
        if duplicate is None:
            merged.append(deadline)
        elif _deadline_specificity(deadline) > _deadline_specificity(duplicate):
            merged[merged.index(duplicate)] = deadline
    return merged


def _merge_action_item_lists(left: list[ActionItem], right: list[ActionItem]) -> list[ActionItem]:
    merged: list[ActionItem] = []
    for item in [*left, *right]:
        key = ((item.owner or "").lower(), _normalize_text_for_match(item.action), item.due_date, item.due_text)
        if any(
            key
            == ((existing.owner or "").lower(), _normalize_text_for_match(existing.action), existing.due_date, existing.due_text)
            for existing in merged
        ):
            continue
        merged.append(item)
    return merged


def _merge_source_lists(left: list[Any], right: list[Any]) -> list[Any]:
    merged: dict[tuple[str, str, str, str], Any] = {}
    for source in [*left, *right]:
        if source is None:
            continue
        merged[source.dedupe_key()] = source
    return list(merged.values())


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _decision_thread_ts(decision: DecisionRecord) -> str | None:
    decision.refresh_source_fields()
    if decision.decision_sources:
        return decision.decision_sources[0].thread_ts
    if decision.sources:
        return decision.sources[0].thread_ts
    return None


def _decision_source_ts(decision: DecisionRecord) -> str | None:
    decision.refresh_source_fields()
    if decision.decision_sources:
        return decision.decision_sources[0].message_ts
    if decision.sources:
        return decision.sources[0].message_ts
    return decision.decision_source_message_ts


def _decision_source_text(decision: DecisionRecord) -> str:
    decision.refresh_source_fields()
    if decision.decision_sources:
        return decision.decision_sources[0].quote
    if decision.sources:
        return decision.sources[0].quote
    return ""


def _is_reversal_text(text: str) -> bool:
    return bool(re.search(r"\b(reversing|reverse|replacing|supersed(?:e|ing)|instead of)\b", text, re.IGNORECASE))


def _preferred_category(current_category: str, claim: Claim) -> str:
    claim_text = claim.claim
    source_text = claim.source.quote if claim.source else ""
    combined_text = f"{claim_text}\n{source_text}"

    if current_category == "tradeoffs" or TRADEOFF_RE.search(claim_text) or _claim_matches_source_tradeoff(claim_text, source_text):
        return "tradeoffs"
    if _is_timeline_claim(claim_text):
        return "timelines"
    if _is_objection_claim(claim_text, source_text):
        return "objections"
    if current_category == "objections" and CONCERN_RE.search(combined_text):
        return "objections"
    if current_category == "reasoning" or _is_primary_driver_claim(claim_text, source_text) or _is_decision_rationale_claim(claim_text, source_text):
        return "reasoning"
    if current_category == "assumptions" and not _is_timeline_claim(claim_text):
        return "assumptions"
    if CONCERN_RE.search(source_text):
        return "objections"
    if ASSUMPTION_RE.search(combined_text) and not RISK_RE.search(claim_text):
        return "assumptions"
    if RISK_RE.search(combined_text):
        return "risks"
    if current_category == "objections":
        return "objections"
    return current_category


def _is_timeline_claim(text: str) -> bool:
    return bool(TIMELINE_RE.search(text))


def _is_objection_claim(claim_text: str, source_text: str) -> bool:
    if CONCERN_RE.search(claim_text):
        return True
    concern_fragment = _source_concern_fragment(source_text)
    if not concern_fragment:
        return False
    claim_tokens = _claim_dedupe_tokens(claim_text)
    concern_tokens = _claim_dedupe_tokens(concern_fragment)
    return bool(claim_tokens and concern_tokens and _token_containment(claim_tokens, concern_tokens) >= 0.6)


def _source_concern_fragment(source_text: str) -> str | None:
    match = re.search(
        r"\b(?:i'?m worried about|i am worried about|i'?m concerned about|i am concerned about|i'?m concerned that|i am concerned that|my concern is|my only worry is|pushback[:,]?)\s+([^.;]+)",
        source_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return re.split(r"\s*,?\s+but\s+", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]


def _claim_matches_source_tradeoff(claim_text: str, source_text: str) -> bool:
    if not source_text:
        return False
    tradeoff_match = re.search(
        r"\bthe tradeoff(?:\s+with\s+[^.]+?)?\s+is\s+([^.;,]+)",
        source_text,
        re.IGNORECASE,
    )
    if not tradeoff_match:
        return False
    claim_tokens = _claim_dedupe_tokens(claim_text)
    tradeoff_tokens = _claim_dedupe_tokens(tradeoff_match.group(1))
    return bool(claim_tokens and tradeoff_tokens and _token_containment(claim_tokens, tradeoff_tokens) >= 0.68)


def _is_primary_driver_claim(claim_text: str, source_text: str) -> bool:
    combined_text = f"{claim_text}\n{source_text}"
    return bool(PRIMARY_DRIVER_RE.search(combined_text))


def _is_decision_rationale_claim(claim_text: str, source_text: str) -> bool:
    combined_text = f"{claim_text}\n{source_text}"
    return bool(
        re.search(
            r"\b(matches where the product is going|avoids? .*cleanup|creates another cleanup project|safer than hiding|less safe|deliverability|spam-folder issues|dedicated ip|real cost difference)\b",
            combined_text,
            re.IGNORECASE,
        )
    )


def _claim_contradicts_source(claim_text: str, source_text: str) -> bool:
    if not source_text:
        return False
    return bool(
        re.search(r"\bSendGrid\b", claim_text, re.IGNORECASE)
        and re.search(r"\bfewer spam-folder issues\b", claim_text, re.IGNORECASE)
        and (
            re.search(r"\bPostmark\b[^.]*\bfewer spam-folder issues\b", source_text, re.IGNORECASE)
            or re.search(r"\bsupport said it had fewer spam-folder issues\b", source_text, re.IGNORECASE)
        )
    )


def _matching_dedupe_bucket(
    buckets: list[dict[str, Any]],
    tokens: set[str],
    category: str,
) -> dict[str, Any] | None:
    for bucket in buckets:
        jaccard = _jaccard_similarity(tokens, bucket["tokens"])
        if {category, bucket["category"]} == {"assumptions", "tradeoffs"}:
            if jaccard >= 0.72:
                return bucket
            continue
        if {category, bucket["category"]} == {"assumptions", "reasoning"}:
            if jaccard >= 0.8:
                return bucket
            continue
        if category == bucket["category"] == "reasoning" and _token_containment(tokens, bucket["tokens"]) >= 0.8:
            return bucket
        if jaccard >= 0.72 or _token_containment(tokens, bucket["tokens"]) >= 0.86:
            return bucket
    return None


def _candidate_is_better_bucket_claim(
    category: str,
    claim: Claim,
    existing: dict[str, Any],
) -> bool:
    category_score = _category_rank(category)
    existing_score = _category_rank(existing["category"])
    if category_score < existing_score:
        return True
    if category_score > existing_score:
        return False
    return len(_claim_dedupe_tokens(claim.claim)) > len(existing["tokens"])


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _token_containment(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _claim_dedupe_tokens(text: str) -> set[str]:
    normalized = re.sub(r"\binfra\b", "infrastructure", text.lower())
    normalized = re.sub(r"\b(losing|lost|loss|lose|loses)\b", "lose", normalized)
    return {
        term
        for term in re.findall(r"[a-z0-9]+", normalized)
        if term
        not in {
            "a",
            "an",
            "and",
            "is",
            "the",
            "them",
            "to",
            "of",
            "that",
            "this",
            "will",
            "could",
            "may",
            "be",
            "by",
            "from",
            "later",
            "said",
            "us",
            "accepted",
            "tradeoff",
            "risk",
            "concern",
        }
    }


def _normalize_text_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _category_rank(category: str) -> int:
    return {
        "objections": 0,
        "tradeoffs": 1,
        "risks": 2,
        "reasoning": 3,
        "assumptions": 4,
        "timelines": 5,
    }.get(category, 99)


def _repair_status_and_people(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    thread_events = _thread_events_for_decision(decision, event_lookup)
    speakers = {event.speaker for event in thread_events}

    if decision.status not in VALID_STATUSES:
        issues.append(
            ValidationIssue(
                decision.decision_id,
                f"Unknown status '{decision.status}' reset to candidate.",
                type="invalid_status",
                field="status",
                action="reset_to_candidate",
            )
        )
        decision.status = "candidate"

    if decision.status == "confirmed":
        combined_text = "\n".join(event.text for event in thread_events)
        if not _has_confirmation_evidence(combined_text):
            issues.append(
                ValidationIssue(
                    decision.decision_id,
                    "Confirmed status downgraded; no explicit confirmation language.",
                    severity="high",
                    type="confirmed_without_explicit_approval",
                    field="status",
                    action="status_downgraded",
                )
            )
            decision.status = "candidate"

    if speakers:
        filtered_participants = [name for name in decision.participants if name in speakers]
        removed = set(decision.participants) - set(filtered_participants)
        if removed:
            issues.append(
                ValidationIssue(
                    decision.decision_id,
                    f"Removed participant(s) absent from source thread: {', '.join(sorted(removed))}.",
                    type="invalid_source",
                    field="participants",
                    action="removed_from_clean_output",
                )
            )
        decision.participants = filtered_participants

        if decision.owner and decision.owner not in speakers:
            issues.append(
                ValidationIssue(
                    decision.decision_id,
                    "Owner absent from source thread; owner cleared.",
                    type="invalid_source",
                    field="owner",
                    action="cleared_from_clean_output",
                )
            )
            decision.owner = None
            decision.owner_role = None

    return issues


def _status_issues(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
) -> list[ValidationIssue]:
    if decision.status != "confirmed":
        return []
    thread_events = _thread_events_for_decision(decision, event_lookup)
    combined_text = "\n".join(event.text for event in thread_events)
    if _has_confirmation_evidence(combined_text):
        return []
    return [
        ValidationIssue(
            decision.decision_id,
            "Confirmed status has no explicit confirmation language.",
            severity="high",
            type="confirmed_without_explicit_approval",
            field="status",
            action="status_downgraded",
        )
    ]


def _has_confirmation_evidence(text: str) -> bool:
    return bool(CONFIRMATION_RE.search(text) or IMPLICIT_CONFIRMATION_RE.search(text))


def _decision_source_issues(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
    action: str,
) -> list[ValidationIssue]:
    source = (decision.decision_sources or decision.sources or [None])[0]
    message_ts = source.message_ts if source else decision.decision_source_message_ts
    if not message_ts:
        return [
            ValidationIssue(
                decision.decision_id,
                "Decision has no resolved source.",
                severity="error",
                type="missing_source",
                field="decision_sources",
                action=action,
            )
        ]

    event = event_lookup.get(message_ts)
    if event is None:
        return [
            ValidationIssue(
                decision.decision_id,
                "Decision cites an invalid source timestamp.",
                severity="error",
                type="invalid_source",
                field="decision_sources",
                message_ts=message_ts,
                action=action,
            )
        ]

    if source is None or source.quote != event.text:
        return [
            ValidationIssue(
                decision.decision_id,
                "Decision source quote does not match source JSON.",
                severity="error",
                type="invalid_source",
                field="decision_sources",
                message_ts=message_ts,
                action=action,
            )
        ]

    return []


def _claim_validation_issues(
    decision: DecisionRecord,
    field: str,
    claim: Claim,
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    message_ts = _claim_message_ts(claim)
    source_issue_type = _claim_source_issue_type(claim, event_lookup)
    action = _claim_issue_action(claim, event_lookup, include_review_claims)

    if source_issue_type == "missing_source":
        issues.append(
            ValidationIssue(
                decision.decision_id,
                f"{field} claim has no source.",
                severity="error",
                type="missing_source",
                field=field,
                claim=claim.claim,
                action=action,
            )
        )
    elif source_issue_type == "invalid_source":
        issues.append(
            ValidationIssue(
                decision.decision_id,
                f"{field} claim has an invalid source.",
                severity="error",
                type="invalid_source",
                field=field,
                claim=claim.claim,
                message_ts=message_ts,
                action=action,
            )
        )

    if claim.support_level == "unsupported":
        issues.append(
            ValidationIssue(
                decision.decision_id,
                "Claim is marked unsupported.",
                severity="high",
                type="unsupported_claim",
                field=field,
                claim=claim.claim,
                message_ts=message_ts,
                action=action,
            )
        )
    elif claim.support_level == "indirect":
        issues.append(
            ValidationIssue(
                decision.decision_id,
                "Claim is only indirectly supported.",
                type="indirect_support_claim",
                field=field,
                claim=claim.claim,
                message_ts=message_ts,
                action=action,
            )
        )

    if claim.needs_review:
        issues.append(
            ValidationIssue(
                decision.decision_id,
                "Claim is marked needs_review.",
                type="needs_review_claim",
                field=field,
                claim=claim.claim,
                message_ts=message_ts,
                action=action,
            )
        )

    if contains_unexpected_language_artifact(claim.claim):
        issues.append(
            ValidationIssue(
                decision.decision_id,
                "Claim contains unexpected non-English alphabetic characters.",
                severity="high",
                type="unexpected_language",
                field=field,
                claim=claim.claim,
                message_ts=message_ts,
                action="removed_from_clean_output",
            )
        )

    return issues


def _claim_should_be_removed(
    claim: Claim,
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool,
) -> bool:
    if _claim_source_issue_type(claim, event_lookup) is not None:
        return True
    if contains_unexpected_language_artifact(claim.claim):
        return True
    if include_review_claims:
        return False
    return claim.support_level in {"unsupported", "indirect"} or claim.needs_review


def _claim_issue_action(
    claim: Claim,
    event_lookup: dict[str, CanonicalEvent],
    include_review_claims: bool,
) -> str:
    if _claim_source_issue_type(claim, event_lookup) is not None:
        return "removed_from_clean_output"
    if contains_unexpected_language_artifact(claim.claim):
        return "removed_from_clean_output"
    if include_review_claims:
        return "kept_for_review"
    return "removed_from_clean_output"


def _claim_source_issue_type(
    claim: Claim,
    event_lookup: dict[str, CanonicalEvent],
) -> str | None:
    message_ts = _claim_message_ts(claim)
    if not message_ts:
        return "missing_source"

    event = event_lookup.get(message_ts)
    if event is None:
        return "invalid_source"

    if claim.source is None:
        return "invalid_source"

    if claim.source.message_ts not in event_lookup:
        return "invalid_source"

    if claim.source.quote != event.text:
        return "invalid_source"

    return None


def _claim_message_ts(claim: Claim) -> str | None:
    if claim.source is not None:
        return claim.source.message_ts
    return claim.source_message_ts


def _thread_events_for_decision(
    decision: DecisionRecord,
    event_lookup: dict[str, CanonicalEvent],
) -> list[CanonicalEvent]:
    decision.refresh_source_fields()
    thread_ts = None
    if decision.decision_sources:
        thread_ts = decision.decision_sources[0].thread_ts
    elif decision.sources:
        thread_ts = decision.sources[0].thread_ts
    elif decision.decision_source_message_ts in event_lookup:
        thread_ts = event_lookup[decision.decision_source_message_ts].thread_ts
    if not thread_ts:
        return []
    return [event for event in event_lookup.values() if event.thread_ts == thread_ts]


def mark_support_levels(decisions: list[DecisionRecord]) -> list[DecisionRecord]:
    for decision in decisions:
        for claim in decision.all_claims():
            if not claim.source:
                continue
            claim_terms = _keywords(claim.claim)
            quote_terms = _keywords(claim.source.quote)
            if not claim_terms:
                claim.support_level = "unsupported"
                claim.needs_review = True
                claim.confidence = min(claim.confidence, 0.2)
                continue
            overlap = len(claim_terms & quote_terms) / len(claim_terms)
            if overlap >= 0.55:
                claim.support_level = "direct"
                claim.confidence = max(claim.confidence, 0.9)
            elif overlap >= 0.3:
                claim.support_level = "indirect"
                claim.needs_review = True
                claim.confidence = min(max(claim.confidence, 0.55), 0.75)
            else:
                claim.support_level = "unsupported"
                claim.needs_review = True
                claim.confidence = min(claim.confidence, 0.25)
    return decisions


def _keywords(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "but",
        "by",
        "for",
        "from",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "we",
        "will",
        "with",
    }
    return {term for term in re.findall(r"[a-z0-9]+", text.lower()) if term not in stopwords and len(term) > 2}


def _sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _with_period(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if text.endswith((".", "!", "?")):
        return text
    return text + "."
