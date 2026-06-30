from __future__ import annotations

import hashlib
import re
from typing import Any

from .schemas import DecisionRecord


NON_DECISION_HEADINGS = {
    "rejected alternative",
    "rejected alternatives",
    "tradeoff",
    "tradeoffs",
    "risk",
    "risks",
    "timeline",
    "timelines",
    "rationale",
    "context",
}
STRONG_DOCUMENT_HEADINGS = {"decision", "final decision", "recommendation", "approved"}


def detect_conflicts(
    candidates: list[Any] | None = None,
    raw_decisions: list[DecisionRecord] | None = None,
    clean_decisions: list[DecisionRecord] | None = None,
    source_bundles: list[Any] | None = None,
    normalized_events: list[Any] | None = None,
    state_resolution: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    del candidates, raw_decisions, clean_decisions, source_bundles, state_resolution
    signals = [_signal_from_event(event) for event in normalized_events or []]
    signals = [signal for signal in signals if signal is not None]

    conflicts: list[dict[str, Any]] = []
    for area_key, area_signals in _group_signals_by_area(signals).items():
        options: dict[str, list[dict[str, Any]]] = {}
        for signal in area_signals:
            options.setdefault(_normalize_option(signal["option"]), []).append(signal)
        if len(options) < 2:
            continue
        for left_key, right_key in _option_pairs(sorted(options)):
            left = _best_signal(options[left_key])
            right = _best_signal(options[right_key])
            if not left or not right:
                continue
            if not _mutually_exclusive_options(left["option"], right["option"]):
                continue
            conflicts.append(_conflict_from_signals(area_key, left, right))
    return _dedupe_conflicts(conflicts)


def remove_conflicted_clean_decisions(clean_decisions: list[DecisionRecord], conflicts: list[dict[str, Any]]) -> int:
    if not conflicts:
        return 0
    kept: list[DecisionRecord] = []
    removed = 0
    for decision in clean_decisions:
        if _decision_matches_conflict(decision, conflicts):
            removed += 1
            continue
        kept.append(decision)
    clean_decisions[:] = kept
    return removed


def _signal_from_event(event: Any) -> dict[str, Any] | None:
    text = _event_text(event)
    if not text:
        return None
    heading = _section_heading(event)
    if heading and _normalize_heading(heading) in NON_DECISION_HEADINGS:
        return None
    match = _decision_match(text)
    if not match:
        return None
    if _event_source_type(event) != "slack" and heading:
        normalized_heading = _normalize_heading(heading)
        if normalized_heading not in STRONG_DOCUMENT_HEADINGS and not _text_has_strong_prefix(text):
            return None
    option = _clean_option(match.group("option"))
    area = _clean_area(match.group("area"))
    if not option or not area:
        return None
    source = _source_from_event(event)
    return {
        "option": option,
        "area": area,
        "area_key": _normalize_area(area),
        "conflict_area": _display_area(area),
        "source_type": _event_source_type(event),
        "source_subtype": _get(event, "source_subtype"),
        "section_heading": heading,
        "event_id": _event_id(event),
        "quote": _quote(event) or text,
        "created_at": _get(event, "created_at"),
        "updated_at": _get(event, "updated_at"),
        "source": source,
    }


def _decision_match(text: str) -> re.Match[str] | None:
    patterns = [
        r"\b(?:approved|final decision|decision)\s*:\s*(?:choose|use)\s+(?P<option>[A-Z][A-Za-z0-9 ._-]+?)\s+for\s+(?P<area>[A-Za-z0-9 /_-]+?)(?:\s+now)?\s*(?:[.!?]|$)",
        r"\bwe (?:will|decided to)\s+(?:choose|use)\s+(?P<option>[A-Z][A-Za-z0-9 ._-]+?)\s+for\s+(?P<area>[A-Za-z0-9 /_-]+?)\s*(?:[.!?]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match
    return None


def _conflict_from_signals(area_key: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted([left, right], key=lambda signal: (signal.get("source_type") != "slack", signal.get("option") or ""))
    source_event_ids = _dedupe(signal["event_id"] for signal in ordered if signal.get("event_id"))
    source_types = {signal.get("source_type") for signal in ordered}
    summary = f"{_source_label(ordered[0])} selects {ordered[0]['option']}, but {_source_label(ordered[1])} selects {ordered[1]['option']}."
    dates = [signal.get("created_at") or signal.get("updated_at") for signal in ordered if signal.get("created_at") or signal.get("updated_at")]
    if dates and len(set(dates)) > 1 and source_types >= {"slack", "google_doc"}:
        summary += " The sources have different timestamps, but no automatic authority rule is configured."
    options = [
        {
            "option": signal["option"],
            "source_type": signal.get("source_type"),
            "source_subtype": signal.get("source_subtype"),
            "section_heading": signal.get("section_heading"),
            "source_event_ids": [signal["event_id"]] if signal.get("event_id") else [],
            "quote": signal.get("quote"),
        }
        for signal in ordered
    ]
    return {
        "conflict_id": _conflict_id(area_key, [signal["option"] for signal in ordered], source_event_ids),
        "type": "conflicting_sources",
        "severity": "high",
        "status": "open",
        "conflict_area": ordered[0].get("conflict_area") or _display_area(area_key),
        "summary": summary,
        "options": options,
        "source_event_ids": source_event_ids,
        "sources": [signal["source"] for signal in ordered if signal.get("source")],
        "related_candidate_ids": [],
        "recommended_action": "Human review required: choose which source is authoritative before writing this decision to clean memory.",
    }


def _decision_matches_conflict(decision: DecisionRecord, conflicts: list[dict[str, Any]]) -> bool:
    decision_text = decision.decision or ""
    decision_option = _option_from_decision_text(decision_text)
    decision_area = _area_from_decision_text(decision_text)
    decision_area_key = _normalize_area(decision_area or decision_text)
    decision_source_locators = _decision_source_locators(decision)
    for conflict in conflicts:
        conflict_source_locators = {
            str(source.get("locator") or "")
            for source in conflict.get("sources") or []
            if isinstance(source, dict) and source.get("locator")
        }
        if decision_source_locators & conflict_source_locators:
            return True
        conflict_area = str(conflict.get("conflict_area") or "")
        conflict_area_key = _normalize_area(conflict_area)
        if decision_area_key and conflict_area_key and not _area_keys_match(decision_area_key, conflict_area_key):
            continue
        for option in conflict.get("options") or []:
            option_text = str(option.get("option") or "")
            if decision_option and _normalize_option(decision_option) == _normalize_option(option_text):
                return True
            if _mentions_option(decision_text, option_text):
                return True
    return False


def _option_from_decision_text(text: str) -> str | None:
    match = re.search(r"\b(?:choose|choosing|use|move to)\s+([A-Z][A-Za-z0-9 ._-]+?)(?:\s+for|\s+instead|\s+now\b|\.|$)", text, re.IGNORECASE)
    return _clean_option(match.group(1)) if match else None


def _area_from_decision_text(text: str) -> str | None:
    match = re.search(r"\b(?:choose|use)\s+[A-Z][A-Za-z0-9 ._-]+?\s+for\s+([A-Za-z0-9 /_-]+?)(?:\s+now)?\s*(?:[.!?]|$)", text, re.IGNORECASE)
    if match:
        return _clean_area(match.group(1))
    prefix_match = re.search(r"\bfor\s+([A-Za-z0-9 /_-]+?),\s+(?:i\s+)?recommend\s+choosing\b", text, re.IGNORECASE)
    return _clean_area(prefix_match.group(1)) if prefix_match else None


def _decision_source_locators(decision: DecisionRecord) -> set[str]:
    locators = set()
    for source in [*decision.decision_sources, *decision.supporting_sources, *decision.all_sources, *decision.sources]:
        message_ts = getattr(source, "message_ts", None)
        if message_ts:
            locators.add(str(message_ts))
    return locators


def _group_signals_by_area(signals: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        grouped.setdefault(signal["area_key"], []).append(signal)
    return grouped


def _option_pairs(keys: list[str]) -> list[tuple[str, str]]:
    return [(keys[index], keys[other]) for index in range(len(keys)) for other in range(index + 1, len(keys))]


def _best_signal(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not signals:
        return None
    return sorted(signals, key=lambda signal: (_signal_strength(signal), signal.get("created_at") or signal.get("updated_at") or ""), reverse=True)[0]


def _signal_strength(signal: dict[str, Any]) -> int:
    quote = str(signal.get("quote") or "")
    heading = _normalize_heading(str(signal.get("section_heading") or ""))
    if re.search(r"\b(approved|final decision|decision)\s*:", quote, re.IGNORECASE):
        return 3
    if heading in {"decision", "final decision", "approved"}:
        return 2
    return 1


def _mutually_exclusive_options(left: str, right: str) -> bool:
    left_key = _normalize_option(left)
    right_key = _normalize_option(right)
    if not left_key or not right_key or left_key == right_key:
        return False
    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    if left_tokens and right_tokens and left_tokens <= right_tokens:
        return False
    if left_tokens and right_tokens and right_tokens <= left_tokens:
        return False
    return True


def _source_label(signal: dict[str, Any]) -> str:
    source_type = str(signal.get("source_type") or "source").replace("_", " ").title()
    heading = signal.get("section_heading")
    if heading:
        return f"{source_type} {heading} section"
    if signal.get("source_type") == "slack":
        return "Slack approval"
    return source_type


def _source_from_event(event: Any) -> dict[str, Any]:
    actor = _get(event, "actor") or {}
    provenance = _get(event, "provenance") or {}
    return {
        "source_event_id": _event_id(event),
        "source_type": _event_source_type(event),
        "source_subtype": _get(event, "source_subtype"),
        "section_heading": provenance.get("section_heading") or (_get(event, "context") or {}).get("section_heading"),
        "actor": actor.get("name") or "Unknown",
        "actor_role": actor.get("role"),
        "quote": provenance.get("quote") or _event_text(event),
        "locator": provenance.get("locator") or _get(event, "external_id"),
        "url": _get(event, "source_url"),
    }


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        deduped.setdefault(str(conflict.get("conflict_id")), conflict)
    return list(deduped.values())


def _conflict_id(area_key: str, options: list[str], source_event_ids: list[str]) -> str:
    digest = hashlib.sha1("|".join([area_key, *sorted(_normalize_option(option) for option in options), *sorted(source_event_ids)]).encode("utf-8")).hexdigest()
    return f"conflict_{digest[:12]}"


def _clean_option(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" .:-")
    cleaned = re.sub(r"\b(now|instead)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .:-")
    return cleaned


def _clean_area(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" .:-")
    cleaned = re.sub(r"\b(now|today|this quarter)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .:-")
    return cleaned


def _display_area(area: str) -> str:
    normalized = _normalize_area(area)
    if normalized == "feature flags":
        return "feature flags vendor"
    if normalized == "observability":
        return "observability vendor"
    if normalized == "transactional email":
        return "transactional email vendor"
    return area


def _normalize_area(area: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", area.lower()).strip()
    normalized = re.sub(r"\b(vendor|tool|platform|now)\b", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_option(option: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", option.lower()).strip()


def _area_keys_match(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    return bool(left_tokens and right_tokens and len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.67)


def _mentions_option(text: str, option: str) -> bool:
    if not option:
        return False
    return bool(re.search(r"\b" + re.escape(option) + r"\b", text, re.IGNORECASE))


def _event_text(event: Any) -> str:
    content = _get(event, "content") or {}
    return str(content.get("text") or "")


def _quote(event: Any) -> str | None:
    provenance = _get(event, "provenance") or {}
    return provenance.get("quote")


def _section_heading(event: Any) -> str | None:
    provenance = _get(event, "provenance") or {}
    context = _get(event, "context") or {}
    return provenance.get("section_heading") or context.get("section_heading")


def _normalize_heading(heading: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", heading.lower())).strip()


def _text_has_strong_prefix(text: str) -> bool:
    return bool(re.search(r"\b(approved|final decision|decision)\s*:", text, re.IGNORECASE))


def _event_source_type(event: Any) -> str:
    return str(_get(event, "source_type") or "")


def _event_id(event: Any) -> str | None:
    return _get(event, "event_id")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result
