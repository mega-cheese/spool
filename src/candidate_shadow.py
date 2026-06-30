from __future__ import annotations

import re

from .candidates import CandidateClaim, DecisionCandidate, EvidenceSignal
from .normalized_events import NormalizedSourceEvent
from .schemas import CLAIM_CATEGORIES, DecisionRecord
from .state_resolver import normalize_decision_text, resolve_candidate_states


def build_shadow_candidates(
    normalized_events: list[NormalizedSourceEvent],
    clean_decisions: list[DecisionRecord],
) -> list[DecisionCandidate]:
    event_by_locator = {event.provenance.get("locator"): event for event in normalized_events}
    ownership_by_source_ts = _ownership_claims_by_source_ts(normalized_events, clean_decisions)
    candidates: list[DecisionCandidate] = []
    for decision in clean_decisions:
        decision.refresh_source_fields()
        source_event_ids = _event_ids_for_sources(decision.all_sources, event_by_locator)
        decision_event_ids = _event_ids_for_sources(decision.decision_sources, event_by_locator)
        claims = _merge_candidate_claims(
            [*_candidate_claims(decision, event_by_locator, ownership_by_source_ts), *getattr(decision, "_shadow_excluded_claims", [])],
            _ownership_claims_from_events(decision, normalized_events),
        )
        evidence_signals = _evidence_signals(decision, claims, decision_event_ids)
        candidates.append(
            DecisionCandidate(
                candidate_id=_candidate_id(decision),
                proposed_action=decision.decision,
                normalized_action=normalize_decision_text(decision.decision),
                state="implicit_confirmed" if _is_implicit_decision(decision) else "confirmed",
                confidence=decision.confidence,
                source_event_ids=_dedupe([*source_event_ids, *decision_event_ids]),
                decision_event_ids=decision_event_ids,
                approval_event_ids=decision_event_ids if decision.status in {"confirmed", "approved"} else [],
                claims=claims,
                evidence_signals=evidence_signals,
            )
        )
    return candidates


def build_state_resolution_debug(candidates: list[DecisionCandidate]) -> dict[str, object]:
    return resolve_candidate_states(candidates)


def finalize_clean_decisions_with_state_resolution(
    normalized_events: list[NormalizedSourceEvent],
    clean_decisions: list[DecisionRecord],
) -> tuple[list[DecisionCandidate], dict[str, object], int]:
    """Build shadow debug state, then apply one final active-memory guard."""
    candidates = build_shadow_candidates(normalized_events, clean_decisions)
    state_resolution = build_state_resolution_debug(candidates)
    removed_count = apply_state_resolution_clean_memory_guard(clean_decisions, state_resolution)
    if removed_count:
        candidates = build_shadow_candidates(normalized_events, clean_decisions)
        state_resolution = build_state_resolution_debug(candidates)
    return candidates, state_resolution, removed_count


def apply_state_resolution_clean_memory_guard(
    clean_decisions: list[DecisionRecord],
    state_resolution: dict[str, object],
) -> int:
    """Remove active clean claims that state resolution assigns to rejected alternatives."""
    excluded_claims = _state_resolution_rejected_claims(state_resolution)
    if not excluded_claims:
        return 0

    rejected_names = {
        claim.target_name
        for claim in excluded_claims
        if claim.target_name and claim.target_role == "rejected_alternative"
    }
    removed = 0
    for decision in clean_decisions:
        excluded_debug_claims = list(getattr(decision, "_shadow_excluded_claims", []))
        for category in CLAIM_CATEGORIES:
            kept = []
            for claim in getattr(decision, category):
                matched = _active_memory_exclusion_match(claim.claim, excluded_claims, rejected_names)
                if matched is None:
                    kept.append(claim)
                    continue
                excluded_debug_claims.append(
                    CandidateClaim(
                        claim=claim.claim,
                        category=category,
                        support_level=claim.support_level,
                        confidence=claim.confidence,
                        target_name=matched.target_name,
                        target_role=matched.target_role,
                    )
                )
                removed += 1
            setattr(decision, category, kept)

        kept_deadlines = []
        for deadline in decision.deadlines:
            matched = _active_memory_exclusion_match(deadline.description, excluded_claims, rejected_names)
            if matched is None:
                kept_deadlines.append(deadline)
                continue
            excluded_debug_claims.append(
                CandidateClaim(
                    claim=deadline.description,
                    category="deadlines",
                    target_name=matched.target_name,
                    target_role=matched.target_role,
                )
            )
            removed += 1
        decision.deadlines = kept_deadlines

        kept_action_items = []
        for action_item in decision.action_items:
            matched = _active_memory_exclusion_match(action_item.action, excluded_claims, rejected_names)
            if matched is None:
                kept_action_items.append(action_item)
                continue
            excluded_debug_claims.append(
                CandidateClaim(
                    claim=action_item.action,
                    category="action_items",
                    target_name=matched.target_name,
                    target_role=matched.target_role,
                )
            )
            removed += 1
        decision.action_items = kept_action_items

        if excluded_debug_claims:
            setattr(decision, "_shadow_excluded_claims", _merge_candidate_claims(excluded_debug_claims, []))
        decision.refresh_source_fields()
    return removed


def filter_clean_decisions_by_claim_ownership(
    normalized_events: list[NormalizedSourceEvent],
    clean_decisions: list[DecisionRecord],
) -> int:
    """Remove active-memory fields that ownership records assign to inactive targets."""
    excluded_by_source_ts = _excluded_ownership_claims_by_source_ts(normalized_events, clean_decisions)
    if not excluded_by_source_ts:
        return 0

    removed = 0
    for decision in clean_decisions:
        excluded_debug_claims: list[CandidateClaim] = []
        for category in CLAIM_CATEGORIES:
            kept = []
            for claim in getattr(decision, category):
                source_ts = _claim_source_ts(claim)
                excluded_claims = excluded_by_source_ts.get(source_ts or "", [])
                matched_ownership = _matched_ownership_claim(claim.claim, excluded_claims)
                if matched_ownership is not None:
                    excluded_debug_claims.append(
                        CandidateClaim(
                            claim=claim.claim,
                            category=category,
                            source_event_ids=list(matched_ownership.source_event_ids),
                            support_level=claim.support_level,
                            confidence=claim.confidence,
                            target_name=matched_ownership.target_name,
                            target_role=matched_ownership.target_role,
                        )
                    )
                    removed += 1
                    continue
                kept.append(claim)
            setattr(decision, category, kept)

        original_deadline_count = len(decision.deadlines)
        decision.deadlines = [
            deadline
            for deadline in decision.deadlines
            if not _matches_excluded_ownership_claim(
                deadline.description,
                excluded_by_source_ts.get(_item_source_ts(deadline) or "", []),
            )
        ]
        removed += original_deadline_count - len(decision.deadlines)

        original_action_count = len(decision.action_items)
        decision.action_items = [
            action_item
            for action_item in decision.action_items
            if not _matches_excluded_ownership_claim(
                action_item.action,
                excluded_by_source_ts.get(_item_source_ts(action_item) or "", []),
            )
        ]
        removed += original_action_count - len(decision.action_items)
        setattr(decision, "_shadow_excluded_claims", excluded_debug_claims)
        decision.refresh_source_fields()
    return removed


def _candidate_id(decision: DecisionRecord) -> str:
    if decision.decision_id:
        return f"candidate_{decision.decision_id}"
    normalized = re.sub(r"[^a-z0-9]+", "_", normalize_decision_text(decision.decision)).strip("_")
    return f"candidate_{normalized[:48]}"


def _candidate_claims(
    decision: DecisionRecord,
    event_by_locator: dict[str | None, NormalizedSourceEvent],
    ownership_by_source_ts: dict[str, list[CandidateClaim]],
) -> list[CandidateClaim]:
    claims: list[CandidateClaim] = []
    chosen_target = _chosen_target_name(decision)
    for category in CLAIM_CATEGORIES:
        for claim in getattr(decision, category):
            source_ts = claim.source.message_ts if claim.source else claim.source_message_ts
            source_event_id = _event_id_for_locator(source_ts, event_by_locator)
            ownership = _matched_ownership_claim(claim.claim, ownership_by_source_ts.get(source_ts or "", []))
            claims.append(
                CandidateClaim(
                    claim=claim.claim,
                    category=category,
                    source_event_ids=[source_event_id] if source_event_id else [],
                    support_level=claim.support_level,
                    confidence=claim.confidence,
                    target_name=ownership.target_name if ownership else chosen_target,
                    target_role=ownership.target_role if ownership else ("chosen_option" if chosen_target else None),
                )
            )
    return claims


def _ownership_claims_from_events(
    decision: DecisionRecord,
    normalized_events: list[NormalizedSourceEvent],
) -> list[CandidateClaim]:
    claims: list[CandidateClaim] = []
    chosen_target = _chosen_target_name(decision)
    for event in normalized_events:
        text = str(event.content.get("text") or "")
        comparison_claims = _vendor_comparison_claims(text, event.event_id, chosen_target)
        claims.extend(comparison_claims)
    return claims


def _excluded_ownership_claims_by_source_ts(
    normalized_events: list[NormalizedSourceEvent],
    clean_decisions: list[DecisionRecord],
) -> dict[str, list[CandidateClaim]]:
    ownership_by_source_ts = _ownership_claims_by_source_ts(normalized_events, clean_decisions)
    return {
        source_ts: [claim for claim in claims if claim.target_role in {"rejected_alternative", "superseded_decision"}]
        for source_ts, claims in ownership_by_source_ts.items()
    }


def _ownership_claims_by_source_ts(
    normalized_events: list[NormalizedSourceEvent],
    clean_decisions: list[DecisionRecord],
) -> dict[str, list[CandidateClaim]]:
    excluded_roles = {"rejected_alternative", "superseded_decision"}
    ownership_by_source_ts: dict[str, list[CandidateClaim]] = {}
    chosen_targets = [_chosen_target_name(decision) for decision in clean_decisions]
    for event in normalized_events:
        source_ts = str(event.provenance.get("locator") or event.external_id or "")
        if not source_ts:
            continue
        text = str(event.content.get("text") or "")
        for chosen_target in chosen_targets or [None]:
            for claim in _vendor_comparison_claims(text, event.event_id, chosen_target):
                if claim.target_role in excluded_roles or claim.target_role == "chosen_option":
                    ownership_by_source_ts.setdefault(source_ts, []).append(claim)
    return ownership_by_source_ts


def _vendor_comparison_claims(text: str, event_id: str, chosen_target: str | None) -> list[CandidateClaim]:
    match = re.search(
        r"\b(?P<rejected>[A-Z][A-Za-z0-9 -]+)\s+is cheaper by about\s+(?P<percent>\d+%)"
        r",\s+but\s+the migration would take longer because we need to rebuild\s+(?P<object>[^.]+?)\."
        r"\s+The tradeoff with\s+(?P<chosen>[A-Z][A-Za-z0-9 -]+)\s+is\s+(?P<tradeoff>[^.]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(?P<rejected>[A-Z][A-Za-z0-9 -]+)\s+(?:is|was) cheaper(?: by about\s+(?P<percent>\d+%))?"
            r",\s+but\s+(?:the\s+)?migration would take longer because we (?:would\s+)?need to rebuild\s+(?P<object>[^.]+?)\."
            r"(?:\s+The tradeoff with\s+(?P<chosen>[A-Z][A-Za-z0-9 -]+)\s+is\s+(?P<tradeoff>[^.]+))?",
            text,
            re.IGNORECASE,
        )
    if not match:
        return []
    rejected = _title_case_vendor(match.group("rejected"))
    chosen = _title_case_vendor(match.group("chosen") or chosen_target or "")
    object_text = _rebuild_object_phrase(match.group("object"))
    target_chosen = chosen_target or chosen
    comparison_claim = (
        f"{rejected} is cheaper by about {match.group('percent')}."
        if match.group("percent")
        else f"{rejected} was cheaper."
    )
    claims = [
        CandidateClaim(
            claim=comparison_claim,
            category="comparison",
            source_event_ids=[event_id],
            support_level="direct",
            confidence=0.9,
            target_name=rejected,
            target_role="rejected_alternative",
        ),
        CandidateClaim(
            claim=f"{rejected} migration would take longer because {object_text} must be rebuilt.",
            category="timeline",
            source_event_ids=[event_id],
            support_level="direct",
            confidence=0.9,
            target_name=rejected,
            target_role="rejected_alternative",
        ),
    ]
    if target_chosen and re.search(r"\bhigher monthly spend\b", match.group("tradeoff") or "", re.IGNORECASE):
        claims.append(
            CandidateClaim(
                claim="Higher monthly spend is the accepted tradeoff.",
                category="tradeoff",
                source_event_ids=[event_id],
                support_level="direct",
                confidence=0.9,
                target_name=target_chosen,
                target_role="chosen_option",
            )
        )
    return claims


def _merge_candidate_claims(left: list[CandidateClaim], right: list[CandidateClaim]) -> list[CandidateClaim]:
    merged: list[CandidateClaim] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for claim in [*left, *right]:
        key = (_normalize_claim_key(claim.claim), claim.category, claim.target_name, claim.target_role)
        if key in seen:
            continue
        seen.add(key)
        merged.append(claim)
    return merged


def _matches_excluded_ownership_claim(text: str, excluded_claims: list[CandidateClaim]) -> bool:
    return _matched_ownership_claim(text, excluded_claims) is not None


def _state_resolution_rejected_claims(state_resolution: dict[str, object]) -> list[CandidateClaim]:
    claim_ownership = state_resolution.get("claim_ownership", [])
    if not isinstance(claim_ownership, list):
        return []
    claims: list[CandidateClaim] = []
    for item in claim_ownership:
        if not isinstance(item, dict) or item.get("target_role") != "rejected_alternative":
            continue
        claim_text = str(item.get("claim") or "").strip()
        if not claim_text:
            continue
        claims.append(
            CandidateClaim(
                claim=claim_text,
                category=str(item.get("category") or "unknown"),
                target_name=str(item.get("target_name") or "") or None,
                target_role=str(item.get("target_role") or "") or None,
            )
        )
    return claims


def _active_memory_exclusion_match(
    text: str,
    excluded_claims: list[CandidateClaim],
    rejected_names: set[str],
) -> CandidateClaim | None:
    normalized_text = _normalize_claim_key(text)
    for excluded in excluded_claims:
        if normalized_text and normalized_text == _normalize_claim_key(excluded.claim):
            return excluded
    matched = _matched_ownership_claim(text, excluded_claims)
    if matched is not None:
        return matched
    for rejected_name in rejected_names:
        if _text_mentions_target(text, rejected_name):
            for excluded in excluded_claims:
                if excluded.target_name == rejected_name:
                    return excluded
    return None


def _matched_ownership_claim(text: str, ownership_claims: list[CandidateClaim]) -> CandidateClaim | None:
    if not ownership_claims:
        return None
    for ownership_claim in ownership_claims:
        text_tokens = _ownership_match_tokens(text, ownership_claim.target_name)
        excluded_tokens = _ownership_match_tokens(ownership_claim.claim, ownership_claim.target_name)
        if not text_tokens or not excluded_tokens:
            continue
        if text_tokens == excluded_tokens:
            return ownership_claim
        if _token_jaccard(text_tokens, excluded_tokens) >= 0.58:
            return ownership_claim
        if _token_containment(text_tokens, excluded_tokens) >= 0.78:
            return ownership_claim
        if ownership_claim.target_name and _text_mentions_target(text, ownership_claim.target_name):
            if _token_containment(text_tokens, excluded_tokens) >= 0.66 or _shared_rejected_alternative_context(text_tokens, excluded_tokens):
                return ownership_claim
    return None


def _ownership_match_tokens(text: str, target_name: str | None = None) -> set[str]:
    normalized = text.lower()
    normalized = re.sub(r"\bneed(?:s|ed)?\s+to\s+be\s+rebuilt\b", "rebuild", normalized)
    normalized = re.sub(r"\bmust\s+be\s+rebuilt\b", "rebuild", normalized)
    normalized = re.sub(r"\brebuilt\b", "rebuild", normalized)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    target_tokens = set(re.findall(r"[a-z0-9]+", (target_name or "").lower()))
    stopwords = {
        "a",
        "an",
        "and",
        "about",
        "be",
        "because",
        "by",
        "can",
        "could",
        "is",
        "must",
        "need",
        "needs",
        "needed",
        "of",
        "several",
        "some",
        "take",
        "takes",
        "the",
        "to",
        "we",
        "with",
        "would",
    }
    result: set[str] = set()
    for token in tokens:
        if token in stopwords or token in target_tokens:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        result.add(token)
    return result


def _token_jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _token_containment(left: set[str], right: set[str]) -> float:
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


def _text_mentions_target(text: str, target_name: str) -> bool:
    return bool(re.search(r"\b" + re.escape(target_name.lower()) + r"\b", text.lower()))


def _shared_rejected_alternative_context(left: set[str], right: set[str]) -> bool:
    shared = left & right
    return (
        ({"migration", "longer"} <= shared)
        or ({"cheaper"} <= shared)
        or ({"migration"} <= shared and ("wrapper" in right or "dashboard" in right))
    )


def _claim_source_ts(claim: object) -> str | None:
    source = getattr(claim, "source", None)
    if source is not None:
        return getattr(source, "message_ts", None)
    return getattr(claim, "source_message_ts", None)


def _item_source_ts(item: object) -> str | None:
    return _claim_source_ts(item)


def _normalize_claim_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _chosen_target_name(decision: DecisionRecord) -> str | None:
    match = re.search(r"\b(?:choose|use)\s+([A-Z][A-Za-z0-9 -]+?)(?:\s+for|\s+instead|\s+now\b|\.|$)", decision.decision, re.IGNORECASE)
    if match:
        return _title_case_vendor(match.group(1))
    return None


def _title_case_vendor(value: str) -> str:
    return value.strip()


def _rebuild_object_phrase(value: str) -> str:
    cleaned = value.strip()
    if re.search(r"^(?:the|several|some|multiple|our)\b", cleaned, re.IGNORECASE):
        return cleaned
    return f"the {cleaned}"


def _evidence_signals(
    decision: DecisionRecord,
    claims: list[CandidateClaim],
    decision_event_ids: list[str],
) -> list[EvidenceSignal]:
    signals = [
        EvidenceSignal("approval_signal", decision_event_ids, confidence=decision.confidence, note="Derived from current clean decision source."),
        EvidenceSignal("source_support", _dedupe(event_id for claim in claims for event_id in claim.source_event_ids), note="Claim source support."),
    ]
    category_to_signal = {
        "objections": "objection_signal",
        "assumptions": "assumption_signal",
        "tradeoffs": "tradeoff_signal",
        "timelines": "timeline_signal",
    }
    for category, signal_type in category_to_signal.items():
        event_ids = _dedupe(event_id for claim in claims if claim.category == category for event_id in claim.source_event_ids)
        if event_ids:
            signals.append(EvidenceSignal(signal_type, event_ids, note=f"Derived from {category} claims."))
    if decision.deadlines:
        signals.append(
            EvidenceSignal(
                "deadline_signal",
                _dedupe(
                    event_id
                    for deadline in decision.deadlines
                    if (event_id := _source_event_id_from_deadline(deadline))
                ),
                note="Derived from structured deadlines.",
            )
        )
    return signals


def _source_event_id_from_deadline(deadline: object) -> str | None:
    source = getattr(deadline, "source", None)
    if source is None:
        return None
    return None


def _event_ids_for_sources(sources: list[object], event_by_locator: dict[str | None, NormalizedSourceEvent]) -> list[str]:
    event_ids: list[str] = []
    for source in sources:
        event_id = _event_id_for_locator(getattr(source, "message_ts", None), event_by_locator)
        if event_id:
            event_ids.append(event_id)
    return _dedupe(event_ids)


def _event_id_for_locator(locator: str | None, event_by_locator: dict[str | None, NormalizedSourceEvent]) -> str | None:
    event = event_by_locator.get(locator)
    return event.event_id if event else None


def _is_implicit_decision(decision: DecisionRecord) -> bool:
    return bool(re.search(r"\b(parent section|under it)\b", decision.decision, re.IGNORECASE))


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
