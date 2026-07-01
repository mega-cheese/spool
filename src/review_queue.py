from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import DecisionRecord


REVIEW_REASONS = {
    "approval_missing",
    "owner_missing",
    "conflicting_sources",
    "stale_source",
    "unsupported_claim",
    "low_confidence",
    "ambiguous_decision",
    "extraction_warning",
    "unsupported_agent_claim",
    "weak_validation_evidence",
    "possible_scope_drift",
    "missing_source_evidence",
}


def build_review_queue(
    decisions: list[DecisionRecord],
    raw_decisions: list[DecisionRecord] | None = None,
    candidates: list[Any] | None = None,
    state_resolution: dict[str, Any] | None = None,
    source_bundles: list[Any] | None = None,
    normalized_events: list[Any] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    document_memory: dict[str, Any] | None = None,
    input_file: str | None = None,
    mode: str | None = None,
    pipeline: str | None = None,
    validation_issues: list[Any] | None = None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    events = list(normalized_events or [])
    event_by_id = {_event_id(event): event for event in events if _event_id(event)}
    items: list[dict[str, Any]] = []

    for decision in decisions:
        if decision.status != "confirmed" or not decision.decision_sources:
            if not is_reviewable_candidate(decision.decision, raw_decision=decision, source_events=_events_for_decision(decision, events)):
                continue
            items.append(
                _review_item(
                    reason="approval_missing",
                    severity="medium",
                    candidate_decision=decision.decision,
                    summary="The system found a possible decision but does not have enough evidence to safely write it to clean memory.",
                    recommended_action="Confirm whether this should enter clean memory.",
                    source_event_ids=_source_event_ids_for_decision(decision, events),
                    sources=_sources_for_decision(decision, events),
                    related_candidate_ids=[],
                    created_at=created_at,
                )
            )
        if decision.status == "confirmed" and _decision_owner_needs_review(decision):
            items.append(
                _review_item(
                    reason="owner_missing",
                    severity="medium",
                    candidate_decision=decision.decision,
                    summary="The decision appears approved, but ownership is missing or explicitly unresolved.",
                    recommended_action="Assign an implementation owner before relying on this memory operationally.",
                    source_event_ids=_source_event_ids_for_decision(decision, events),
                    sources=_sources_for_decision(decision, events),
                    related_candidate_ids=[],
                    created_at=created_at,
                )
            )
        if decision.confidence < 0.70:
            items.append(
                _review_item(
                    reason="low_confidence",
                    severity="medium",
                    candidate_decision=decision.decision,
                    summary="The decision confidence is below the clean-memory threshold for unattended trust.",
                    recommended_action="Review the cited sources and confirm whether this decision should remain in clean memory.",
                    source_event_ids=_source_event_ids_for_decision(decision, events),
                    sources=_sources_for_decision(decision, events),
                    related_candidate_ids=[],
                    created_at=created_at,
                )
            )

    clean_keys = {_normalize_text(decision.decision) for decision in decisions}
    clean_event_ids = {
        event_id
        for decision in decisions
        if decision.status == "confirmed"
        for event_id in _source_event_ids_for_decision(decision, events)
    }
    for raw in raw_decisions or []:
        raw_key = _normalize_text(raw.decision)
        if raw_key in clean_keys and raw.status == "confirmed":
            continue
        if raw.status != "confirmed" or not raw.decision_sources:
            source_events = _events_for_decision(raw, events)
            if not is_reviewable_candidate(raw.decision, raw_decision=raw, source_events=source_events):
                continue
            reason = "ambiguous_decision" if _decision_text_is_ambiguous(raw.decision, raw) else "approval_missing"
            items.append(
                _review_item(
                    reason=reason,
                    severity="medium",
                    candidate_decision=raw.decision,
                    summary=_summary_for_reason(reason),
                    recommended_action="Confirm whether this should enter clean memory.",
                    source_event_ids=_source_event_ids_for_decision(raw, events),
                    sources=_sources_for_decision(raw, events),
                    related_candidate_ids=[],
                    created_at=created_at,
                )
            )

    for candidate in candidates or []:
        candidate_dict = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
        if not isinstance(candidate_dict, dict):
            continue
        approval_event_ids = candidate_dict.get("approval_event_ids") or []
        state = candidate_dict.get("state")
        if state not in {"confirmed", "implicit_confirmed"} or not approval_event_ids:
            decision_text = str(candidate_dict.get("proposed_action") or "")
            source_events = [event_by_id[event_id] for event_id in candidate_dict.get("source_event_ids", []) if event_id in event_by_id]
            if (
                decision_text
                and _normalize_text(decision_text) not in clean_keys
                and is_reviewable_candidate(candidate_dict, source_events=source_events)
            ):
                items.append(
                    _review_item(
                        reason="approval_missing",
                        severity="medium",
                        candidate_decision=decision_text,
                        summary="The candidate does not have enough approval evidence to enter active memory.",
                        recommended_action="Confirm whether this should enter clean memory.",
                        source_event_ids=list(candidate_dict.get("source_event_ids") or []),
                        sources=[_source_from_event(event) for event in source_events],
                        related_candidate_ids=[str(candidate_dict.get("candidate_id"))] if candidate_dict.get("candidate_id") else [],
                        created_at=created_at,
                    )
                )

    items.extend(_review_items_from_validation_issues(validation_issues or [], events, created_at))
    items.extend(_review_items_from_source_events(events, clean_keys, clean_event_ids, created_at))
    items.extend(_review_items_from_conflicts(conflicts or [], created_at))
    items.extend(_review_items_from_document_memory(document_memory or {}, created_at))
    items = _dedupe_items(items)

    return {
        "metadata": {
            "input_file": input_file,
            "mode": mode,
            "pipeline": pipeline,
            "generated_at": created_at,
            "review_item_count": len(items),
        },
        "items": items,
    }


def is_reviewable_candidate(
    candidate: Any,
    raw_decision: DecisionRecord | None = None,
    source_events: list[Any] | None = None,
) -> bool:
    """Return True only for specific company-memory candidates worth human review."""
    candidate_text = _candidate_text(candidate)
    source_text = "\n".join(_event_text(event) for event in source_events or [])
    raw_text = raw_decision.decision if raw_decision is not None else ""
    claim_text = _candidate_claim_text(candidate)
    combined = "\n".join(part for part in [candidate_text, raw_text, claim_text, source_text] if part)

    if is_placeholder_candidate_text(candidate_text or raw_text):
        return False
    if not combined.strip():
        return False
    if _is_social_or_logistical_only(combined):
        return False
    if _has_specific_work_decision_signal(combined):
        return True
    if candidate_text and not is_placeholder_candidate_text(candidate_text) and _has_work_object(combined):
        return True
    return False


def is_placeholder_candidate_text(text: Any) -> bool:
    if text is None:
        return True
    normalized = _normalize_text(str(text))
    if not normalized:
        return True
    return normalized in {
        "possible decision from source thread",
        "possible decision",
        "decision from source thread",
        "decision needs human review",
        "unknown decision",
        "untitled decision",
        "approved decision with missing owner",
        "unsupported claim",
    }


def write_review_queue(review_queue: dict[str, Any], output_dir: str | Path, pretty: bool = False) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "review_queue.json"
    md_path = output_path / "review_queue.md"
    json_path.write_text(json.dumps(review_queue, indent=2 if pretty else None, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(review_queue_to_markdown(review_queue), encoding="utf-8")
    return json_path, md_path


def write_review_queue_sidecar(review_queue: dict[str, Any], decision_output_path: str | Path, pretty: bool = False) -> tuple[Path, Path]:
    output_path = Path(decision_output_path)
    json_path = output_path.with_name(f"{output_path.stem}.review_queue.json")
    md_path = output_path.with_name(f"{output_path.stem}.review_queue.md")
    json_path.write_text(json.dumps(review_queue, indent=2 if pretty else None, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(review_queue_to_markdown(review_queue), encoding="utf-8")
    return json_path, md_path


def review_queue_to_markdown(review_queue: dict[str, Any]) -> str:
    items = review_queue.get("items", [])
    lines = ["# Review Queue", ""]
    if not items:
        lines.append("No review items.")
        return "\n".join(lines).rstrip() + "\n"
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"## {index}. {item.get('title') or 'Decision needs human review'}",
                "",
                f"Severity: {item.get('severity')}",
                f"Reason: {item.get('reason')}",
                f"Candidate decision: {item.get('candidate_decision') or 'Unknown'}",
                "",
                "Summary:",
                item.get("summary") or "",
                "",
                "Recommended action:",
                item.get("recommended_action") or "",
                "",
            ]
        )
        options = item.get("options") or []
        if options:
            lines.extend(["Options:", ""])
            for option in options:
                option_name = option.get("option") or "Unknown option"
                source_type = str(option.get("source_type") or "source").replace("_", " ").title()
                heading = option.get("section_heading")
                label = f"{source_type} {heading} section" if heading else source_type
                quote = option.get("quote") or ""
                lines.append(f"* {option_name} - {label}: {quote}")
            lines.append("")
        lines.append("Sources:")
        sources = item.get("sources") or []
        if not sources:
            lines.append("* Evidence unavailable or not yet linked.")
        for source in sources:
            actor = source.get("actor") or source.get("speaker") or "Unknown"
            quote = source.get("quote") or ""
            lines.append(f"* {actor}: {quote}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _review_items_from_conflicts(conflicts: list[dict[str, Any]], created_at: str) -> list[dict[str, Any]]:
    items = []
    for conflict in conflicts:
        items.append(
            _review_item(
                reason="conflicting_sources",
                severity="high",
                candidate_decision=str(conflict.get("conflict_area") or "Conflicting decision"),
                summary=str(conflict.get("summary") or "Credible sources disagree about this decision."),
                recommended_action=str(
                    conflict.get("recommended_action")
                    or "Human review required: choose which source is authoritative before writing this decision to clean memory."
                ),
                source_event_ids=list(conflict.get("source_event_ids") or []),
                sources=list(conflict.get("sources") or []),
                related_candidate_ids=list(conflict.get("related_candidate_ids") or []),
                created_at=created_at,
                title="Conflicting sources for decision",
                item_type="conflicting_sources",
                options=list(conflict.get("options") or []),
                conflict_id=str(conflict.get("conflict_id") or ""),
            )
        )
    return items


def _review_items_from_document_memory(document_memory: dict[str, Any], created_at: str) -> list[dict[str, Any]]:
    items = []
    for warning in document_memory.get("review_warnings") or []:
        items.append(
            _review_item(
                reason=str(warning.get("reason") or "extraction_warning"),
                severity=str(warning.get("severity") or "medium"),
                candidate_decision=str(warning.get("candidate_decision") or "Meeting minutes extraction warning"),
                summary=str(warning.get("summary") or "Meeting minutes may need human review."),
                recommended_action=str(warning.get("recommended_action") or "Review the source note before writing this into confirmed memory."),
                source_event_ids=list(warning.get("source_event_ids") or []),
                sources=list(warning.get("sources") or []),
                related_candidate_ids=list(warning.get("related_candidate_ids") or []),
                created_at=created_at,
                title=str(warning.get("title") or "Decision needs human review"),
            )
        )
    return items


def _review_items_from_validation_issues(issues: list[Any], events: list[Any], created_at: str) -> list[dict[str, Any]]:
    items = []
    for issue in issues:
        issue_type = getattr(issue, "type", None) or (issue.get("type") if isinstance(issue, dict) else None)
        if issue_type != "unsupported_claim":
            continue
        claim = getattr(issue, "claim", None) or (issue.get("claim") if isinstance(issue, dict) else None)
        message_ts = getattr(issue, "message_ts", None) or (issue.get("message_ts") if isinstance(issue, dict) else None)
        source_events = [event for event in events if _locator(event) == message_ts]
        items.append(
            _review_item(
                reason="unsupported_claim",
                severity="high",
                candidate_decision=str(claim or "Unsupported claim"),
                summary="A claim was detected without enough source support for clean memory.",
                recommended_action="Review the source and decide whether the claim should be corrected, removed, or supported.",
                source_event_ids=[_event_id(event) for event in source_events if _event_id(event)],
                sources=[_source_from_event(event) for event in source_events],
                related_candidate_ids=[],
                created_at=created_at,
            )
        )
    return items


def _review_items_from_source_events(
    events: list[Any],
    clean_keys: set[str],
    clean_event_ids: set[str],
    created_at: str,
) -> list[dict[str, Any]]:
    items = []
    for _root_id, thread_events in _events_by_root(events).items():
        thread_event_ids = {_event_id(event) for event in thread_events if _event_id(event)}
        if thread_event_ids & clean_event_ids:
            continue
        combined = "\n".join(_event_text(event) for event in thread_events)
        if _has_approval(combined):
            if _owner_missing_text(combined):
                decision_text = _decision_from_thread(thread_events) or "Approved decision with missing owner."
                if _normalize_text(decision_text) in clean_keys:
                    items.append(
                        _review_item(
                            reason="owner_missing",
                            severity="medium",
                            candidate_decision=decision_text,
                            summary="The source explicitly says ownership is not assigned yet.",
                            recommended_action="Assign an implementation owner before relying on this memory operationally.",
                            source_event_ids=[_event_id(event) for event in thread_events if _event_id(event)],
                            sources=[_source_from_event(event) for event in thread_events],
                            related_candidate_ids=[],
                            created_at=created_at,
                        )
                    )
            continue
        if not _has_proposal(combined):
            continue
        decision_text = _decision_from_thread(thread_events) or "Possible decision from source thread."
        if _normalize_text(decision_text) in clean_keys:
            continue
        if not is_reviewable_candidate(decision_text, source_events=thread_events):
            continue
        reason = "ambiguous_decision" if _ambiguous_text(combined) else "approval_missing"
        items.append(
            _review_item(
                reason=reason,
                severity="medium",
                candidate_decision=decision_text,
                summary=_summary_for_reason(reason),
                recommended_action="Confirm whether this should enter clean memory.",
                source_event_ids=[_event_id(event) for event in thread_events if _event_id(event)],
                sources=[_source_from_event(event) for event in thread_events],
                related_candidate_ids=[],
                created_at=created_at,
            )
        )
    return items


def _review_item(
    *,
    reason: str,
    severity: str,
    candidate_decision: str,
    summary: str,
    recommended_action: str,
    source_event_ids: list[str],
    sources: list[dict[str, Any]],
    related_candidate_ids: list[str],
    created_at: str,
    title: str = "Decision needs human review",
    item_type: str = "needs_human_review",
    options: list[dict[str, Any]] | None = None,
    conflict_id: str | None = None,
) -> dict[str, Any]:
    if reason not in REVIEW_REASONS:
        reason = "extraction_warning"
    review_id = _review_id(reason, candidate_decision, source_event_ids)
    result = {
        "review_id": review_id,
        "type": item_type,
        "severity": severity,
        "status": "open",
        "title": title,
        "summary": summary,
        "candidate_decision": candidate_decision,
        "reason": reason,
        "recommended_action": recommended_action,
        "source_event_ids": source_event_ids,
        "sources": sources,
        "related_candidate_ids": related_candidate_ids,
        "created_at": created_at,
    }
    if options:
        result["options"] = options
    if conflict_id:
        result["conflict_id"] = conflict_id
    return result


def _summary_for_reason(reason: str) -> str:
    if reason == "ambiguous_decision":
        return "The system found decision-like language, but the source is ambiguous or tentative."
    return "The system found a possible decision but does not have enough evidence to safely write it to clean memory."


def _decision_owner_needs_review(decision: DecisionRecord) -> bool:
    source_text = "\n".join(source.quote for source in decision.all_sources if source.quote)
    action_text = "\n".join(item.action for item in decision.action_items)
    combined = f"{source_text}\n{action_text}"
    if getattr(decision, "_meeting_memory", False):
        return _owner_missing_text(combined)
    if not decision.owner:
        return True
    return _owner_missing_text(combined)


def _owner_missing_text(text: str) -> bool:
    return bool(re.search(r"\b(no owner|owner TBD|owner to be assigned|need(?:s)? an owner|without an owner)\b", text, re.IGNORECASE))


def _decision_text_is_ambiguous(decision_text: str, decision: DecisionRecord) -> bool:
    source_text = "\n".join(source.quote for source in decision.all_sources if source.quote)
    return _ambiguous_text(f"{decision_text}\n{source_text}")


def _ambiguous_text(text: str) -> bool:
    return bool(re.search(r"\b(maybe|probably|seems reasonable|not sure|tentative|might)\b", text, re.IGNORECASE))


def _has_approval(text: str) -> bool:
    return bool(re.search(r"\b(approved|final decision|we decided|move forward with|agreed)\b", text, re.IGNORECASE))


def _has_proposal(text: str) -> bool:
    return bool(re.search(r"(?:\bproposal\s*:|\bi propose\b|\brecommend choosing\b|\bwe should\b|\bmaybe we should\b|\blet'?s\b)", text, re.IGNORECASE))


def _candidate_text(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, DecisionRecord):
        return candidate.decision
    candidate_dict = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
    if isinstance(candidate_dict, dict):
        return str(candidate_dict.get("proposed_action") or candidate_dict.get("decision") or candidate_dict.get("candidate_decision") or "")
    return str(candidate or "")


def _candidate_claim_text(candidate: Any) -> str:
    candidate_dict = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
    if not isinstance(candidate_dict, dict):
        return ""
    claims = candidate_dict.get("claims") or []
    claim_texts: list[str] = []
    for claim in claims:
        if isinstance(claim, dict):
            claim_texts.append(str(claim.get("claim") or ""))
        else:
            claim_texts.append(str(getattr(claim, "claim", "") or ""))
    return "\n".join(claim_texts)


def _is_social_or_logistical_only(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return True
    if _has_specific_work_decision_signal(text) or _has_work_object(text):
        return False
    social_terms = {
        "lunch",
        "sandwich",
        "sandwiches",
        "coffee",
        "calendar",
        "noon",
        "joke",
        "jokes",
        "grab",
        "after standup",
        "looks sharp",
        "looks good",
        "nice work",
    }
    return any(term in normalized for term in social_terms)


def _has_specific_work_decision_signal(text: str) -> bool:
    patterns = [
        r"\bchoose\s+[A-Z][A-Za-z0-9_-]*(?:\s+for\b|\s+over\b|\s+instead\b|$)",
        r"\bchoosing\s+[A-Z][A-Za-z0-9_-]*(?:\s+for\b|\s+over\b|\s+instead\b|$)",
        r"\bdelay\s+(?:the\s+)?[\w -]*(?:launch|rollout|beta|demo|release)\b",
        r"\buse\s+[\w -]*(?:redis|postmark|segment|datadog|launchdarkly|vendor|tool|system|architecture|service)\b",
        r"\bmove\s+[\w -]*(?:settings|section|billing|roles|saml|review|workstream|api tokens|audit logs)\b",
        r"\bchange\s+[\w -]*pricing\b",
        r"\bimplement\s+[\w -]*(?:feature|flow|api|service|integration|support)\b",
        r"\badopt\s+[\w -]*(?:tool|vendor|platform|policy|process)\b",
        r"\bmigrat(?:e|ion)\s+[\w -]*(?:system|service|vendor|dashboard|wrapper|data|infra)\b",
        r"\bapprove\s+[\w -]*(?:rollout|launch|plan|policy|decision|implementation)\b",
        r"\bcreate\s+[\w -]*(?:policy|section|center|security center|architecture|process)\b",
        r"\bassign\s+[\w -]*(?:owner|ownership)\b",
        r"\bupdate\s+[\w -]*(?:architecture|pricing|policy|settings|flow|model|ia)\b",
        r"\b(?:proposal|propose|recommend|recommended|final decision|approved|reversing|reversal)\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns) and _has_work_object(text)


def _has_work_object(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"vendor|launch|rollout|release|demo|beta|redis|pricing|price|feature|tool|platform|"
            r"architecture|policy|owner|ownership|settings|section|billing|roles|saml|audit|api|"
            r"tokens|scim|security|rbac|analytics|dashboard|workflow|workstream|migration|wrapper|"
            r"datadog|launchdarkly|configcat|new relic|postmark|sendgrid|segment|rudderstack|"
            r"ia|permissions|implementation|engineering|product|customer|customers|partners|pilots"
            r")\b",
            text,
            re.IGNORECASE,
        )
    )


def _decision_from_thread(events: list[Any]) -> str | None:
    for event in events:
        text = _event_text(event)
        patterns = [
            r"\b(?:proposal:\s*)?(?:i propose|we should|maybe we should|recommend choosing|choose|use)\s+([^.;]+)",
            r"\bapproved:\s*([^.;]+)",
            r"\bfinal decision:\s*([^.;]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return _sentence_case(match.group(1).strip())
    return None


def _source_event_ids_for_decision(decision: DecisionRecord, events: list[Any]) -> list[str]:
    locators = {source.message_ts for source in [*decision.decision_sources, *decision.all_sources] if source.message_ts}
    return [event_id for event in events if (event_id := _event_id(event)) and _locator(event) in locators]


def _events_for_decision(decision: DecisionRecord, events: list[Any]) -> list[Any]:
    event_ids = set(_source_event_ids_for_decision(decision, events))
    return [event for event in events if _event_id(event) in event_ids]


def _sources_for_decision(decision: DecisionRecord, events: list[Any]) -> list[dict[str, Any]]:
    event_ids = set(_source_event_ids_for_decision(decision, events))
    if event_ids:
        return [_source_from_event(event) for event in events if _event_id(event) in event_ids]
    return [
        {
            "source_type": source.source_type,
            "speaker": source.speaker,
            "actor": source.speaker,
            "quote": source.quote,
            "locator": source.message_ts,
            "url": source.url,
        }
        for source in decision.all_sources
    ]


def _source_from_event(event: Any) -> dict[str, Any]:
    actor = _get(event, "actor") or {}
    provenance = _get(event, "provenance") or {}
    return {
        "source_event_id": _event_id(event),
        "source_type": _get(event, "source_type"),
        "source_subtype": _get(event, "source_subtype"),
        "actor": actor.get("name") or "Unknown",
        "actor_role": actor.get("role"),
        "quote": provenance.get("quote") or _event_text(event),
        "locator": provenance.get("locator") or _get(event, "external_id"),
        "url": _get(event, "source_url"),
    }


def _events_by_root(events: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for event in events:
        context = _get(event, "context") or {}
        root_id = str(context.get("root_id") or _get(event, "external_id") or _event_id(event) or "unknown")
        grouped.setdefault(root_id, []).append(event)
    return grouped


def _event_text(event: Any) -> str:
    content = _get(event, "content") or {}
    return str(content.get("text") or "")


def _event_id(event: Any) -> str | None:
    return _get(event, "event_id")


def _locator(event: Any) -> str | None:
    provenance = _get(event, "provenance") or {}
    return provenance.get("locator") or _get(event, "external_id")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for item in items:
        existing = next(
            (
                existing_item
                for existing_item in deduped
                if (existing_item.get("reason") or "") == (item.get("reason") or "")
                and _similar_candidate_text(existing_item.get("candidate_decision") or "", item.get("candidate_decision") or "")
            ),
            None,
        )
        if existing is None:
            deduped.append(item)
            continue
        _merge_review_item(existing, item)
    return deduped


def _merge_review_item(existing: dict[str, Any], item: dict[str, Any]) -> None:
    existing_text = str(existing.get("candidate_decision") or "")
    new_text = str(item.get("candidate_decision") or "")
    if _candidate_text_quality(new_text) > _candidate_text_quality(existing_text):
        existing["candidate_decision"] = new_text
        existing["review_id"] = _review_id(existing.get("reason") or "extraction_warning", new_text, existing.get("source_event_ids", []))
    existing_sources = existing.setdefault("sources", [])
    existing_source_ids = set(existing.setdefault("source_event_ids", []))
    for source_id in item.get("source_event_ids", []):
        if source_id not in existing_source_ids:
            existing["source_event_ids"].append(source_id)
            existing_source_ids.add(source_id)
    existing_source_keys = {source.get("source_event_id") or source.get("locator") for source in existing_sources}
    for source in item.get("sources", []):
        source_key = source.get("source_event_id") or source.get("locator")
        if source_key not in existing_source_keys:
            existing_sources.append(source)
            existing_source_keys.add(source_key)
    existing_related_ids = set(existing.setdefault("related_candidate_ids", []))
    for related_id in item.get("related_candidate_ids", []):
        if related_id not in existing_related_ids:
            existing["related_candidate_ids"].append(related_id)
            existing_related_ids.add(related_id)


def _similar_candidate_text(left: str, right: str) -> bool:
    left_norm = _normalize_candidate_for_similarity(left)
    right_norm = _normalize_candidate_for_similarity(right)
    if not left_norm or not right_norm:
        return left_norm == right_norm
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.72


def _normalize_candidate_for_similarity(text: str) -> str:
    normalized = _normalize_text(text)
    return re.sub(r"^(choose|use|move|delay|change|implement|adopt|migrate|approve|create|assign|update)\s+", "", normalized).strip()


def _candidate_text_quality(text: str) -> tuple[int, int]:
    if is_placeholder_candidate_text(text):
        return (0, 0)
    normalized = _normalize_text(text)
    has_decision_verb = bool(
        re.match(r"^(choose|use|move|delay|change|implement|adopt|migrate|approve|create|assign|update)\b", normalized)
    )
    return (1 if has_decision_verb else 0, len(normalized))


def _review_id(reason: str, candidate_decision: str, source_event_ids: list[str]) -> str:
    digest = hashlib.sha1("|".join([reason, _normalize_text(candidate_decision), *sorted(source_event_ids)]).encode("utf-8")).hexdigest()
    return f"review_{digest[:12]}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _sentence_case(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned.endswith(".") else cleaned + "."
