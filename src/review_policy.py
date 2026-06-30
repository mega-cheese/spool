from __future__ import annotations

import re
from typing import Any

from .memory_objects import MemoryObject


SUSPICIOUS_OWNERS = {"need", "document", "staff", "supplies", "jbo rubrics", "sjr", "ubc"}


def apply_review_policy(
    candidates: list[MemoryObject],
    *,
    auto_approve: bool = False,
    coverage_warnings: list[dict[str, Any]] | None = None,
) -> tuple[list[MemoryObject], dict[str, Any]]:
    warning_source_ids = {
        source_id
        for warning in coverage_warnings or []
        for source_id in warning.get("source_event_ids", []) or []
    }
    reviewed = []
    for candidate in candidates:
        reviewed.append(classify_memory(candidate, auto_approve=auto_approve, warning_source_ids=warning_source_ids))
    debug = build_memory_state_debug(reviewed)
    return reviewed, debug


def classify_memory(
    candidate: MemoryObject,
    *,
    auto_approve: bool = False,
    warning_source_ids: set[str] | None = None,
) -> MemoryObject:
    warning_source_ids = warning_source_ids or set()
    reason_codes = list(candidate.review_reason_codes)
    score = _base_quality_score(candidate)

    if not candidate.title.strip():
        reason_codes.append("empty_title")
    if not candidate.sources and not candidate.quote:
        reason_codes.append("missing_source_and_quote")
    if not candidate.sources:
        reason_codes.append("missing_source")
    if not candidate.quote:
        reason_codes.append("missing_quote")
    if candidate.confidence and candidate.confidence < 0.7:
        reason_codes.append("low_confidence")
    if candidate.evidence_state != "supported":
        reason_codes.append("evidence_not_supported")
    if _has_suspicious_owner(candidate):
        reason_codes.append("suspicious_owner")
    if candidate.type == "commitment" and candidate.title.rstrip().endswith("?"):
        reason_codes.append("question_like_commitment")
    if candidate.type == "commitment" and not _has_action_verb(candidate.title):
        reason_codes.append("weak_task_parse")
    if re.search(r"\bshould how\b", candidate.summary, re.IGNORECASE):
        reason_codes.append("ugly_parser_text")
    if _quote_is_metadata_only(candidate.quote):
        reason_codes.append("metadata_only_quote")
    if warning_source_ids and set(candidate.source_event_ids) & warning_source_ids:
        reason_codes.append("coverage_warning")

    reason_codes = list(dict.fromkeys(reason_codes))
    candidate.review_reason_codes = reason_codes
    candidate.memory_quality_score = max(0.0, min(1.0, score - 0.08 * len(reason_codes)))

    if _clear_false_positive(candidate, reason_codes):
        candidate.review_state = "rejected"
        candidate.status = "rejected"
    elif reason_codes:
        candidate.review_state = "needs_review"
        if candidate.status == "candidate":
            candidate.status = "needs_review"
    elif auto_approve and _safe_to_auto_approve(candidate):
        candidate.review_state = "approved"
        candidate.status = "approved"
    else:
        candidate.review_state = "unreviewed"
        if candidate.type == "commitment" and candidate.status == "candidate":
            candidate.status = "unresolved"
    return candidate


def build_memory_state_debug(candidates: list[MemoryObject], *, input_file: str | None = None) -> dict[str, Any]:
    return {
        "metadata": {
            "input_file": input_file,
            "candidate_count": len(candidates),
            "approved_count": sum(1 for item in candidates if item.review_state == "approved"),
            "needs_review_count": sum(1 for item in candidates if item.review_state == "needs_review"),
            "rejected_count": sum(1 for item in candidates if item.review_state == "rejected"),
        },
        "items": [
            {
                "memory_id": item.memory_id,
                "type": item.type,
                "title": item.title,
                "review_state": item.review_state,
                "evidence_state": item.evidence_state,
                "memory_quality_score": round(float(item.memory_quality_score), 3),
                "reason_codes": list(item.review_reason_codes),
                "source_quote": item.quote,
            }
            for item in candidates
        ],
    }


def _base_quality_score(candidate: MemoryObject) -> float:
    score = 0.35
    if candidate.sources:
        score += 0.25
    if candidate.quote:
        score += 0.2
    if candidate.evidence_state == "supported":
        score += 0.15
    if candidate.confidence:
        score += min(0.15, max(0.0, candidate.confidence - 0.6) * 0.3)
    return min(1.0, score)


def _safe_to_auto_approve(candidate: MemoryObject) -> bool:
    return (
        candidate.confidence >= 0.85
        and candidate.evidence_state == "supported"
        and bool(candidate.quote)
        and not candidate.review_reason_codes
    )


def _clear_false_positive(candidate: MemoryObject, reason_codes: list[str]) -> bool:
    if "empty_title" in reason_codes:
        return True
    if "missing_source_and_quote" in reason_codes:
        return True
    if candidate.type == "commitment" and "suspicious_owner" in reason_codes and "weak_task_parse" in reason_codes:
        return True
    if candidate.type == "commitment" and _context_stat(candidate.title) and "weak_task_parse" in reason_codes:
        return True
    return False


def _has_suspicious_owner(candidate: MemoryObject) -> bool:
    return any(owner.strip().lower() in SUSPICIOUS_OWNERS for owner in candidate.owner_names)


def _has_action_verb(text: str) -> bool:
    return bool(
        re.search(
            r"\b(send|ask|contact|reach out|follow up|finalize|update|write|order|book|arrange|prepare|print|email|share|assign|find|call|connect|confirm|upload|create|make|schedule|review|assemble|coordinate|invite|message|get|record|open|take|look|finish|reorganize)\b",
            text,
            re.IGNORECASE,
        )
    )


def _quote_is_metadata_only(quote: str) -> bool:
    clean = quote.strip().lower()
    if not clean:
        return False
    return bool(re.fullmatch(r"(document|meeting|zoom|location|present|absent|www\.[^\s]+|https?://[^\s]+)(\s*[:.]?\s*)?", clean))


def _context_stat(text: str) -> bool:
    return bool(re.search(r"\b\d+\s+students?\b|\bcoming from\b|\btentative yes\b|\bno new updates\b", text, re.IGNORECASE))
