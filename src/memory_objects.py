from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


MEMORY_TYPES = {
    "decision",
    "commitment",
    "open_question",
    "risk",
    "assumption",
    "contradiction",
    "rejected_alternative",
    "context",
    "unknown",
}
MEMORY_STATUSES = {"candidate", "approved", "rejected", "needs_review", "resolved", "unresolved", "superseded", "stale", "unknown"}
EVIDENCE_STATES = {"supported", "weakly_supported", "unsupported", "contradicted", "unknown"}
REVIEW_STATES = {"unreviewed", "approved", "rejected", "edited", "needs_review"}


@dataclass
class MemoryObject:
    memory_id: str
    source_object_id: str | None
    type: str
    title: str
    summary: str
    owner_names: list[str] = field(default_factory=list)
    status: str = "candidate"
    confidence: float = 0.0
    source_event_ids: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    quote: str = ""
    evidence_state: str = "unknown"
    review_state: str = "unreviewed"
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    review_reason_codes: list[str] = field(default_factory=list)
    memory_quality_score: float = 0.0

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = self.created_at
        self.type = self.type if self.type in MEMORY_TYPES else "unknown"
        self.status = self.status if self.status in MEMORY_STATUSES else "unknown"
        self.evidence_state = self.evidence_state if self.evidence_state in EVIDENCE_STATES else "unknown"
        self.review_state = self.review_state if self.review_state in REVIEW_STATES else "unreviewed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "source_object_id": self.source_object_id,
            "type": self.type,
            "title": self.title,
            "summary": self.summary,
            "owner_names": list(self.owner_names),
            "status": self.status,
            "confidence": round(float(self.confidence), 3),
            "source_event_ids": list(self.source_event_ids),
            "sources": list(self.sources),
            "quote": self.quote,
            "evidence_state": self.evidence_state,
            "review_state": self.review_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "review_reason_codes": list(self.review_reason_codes),
            "memory_quality_score": round(float(self.memory_quality_score), 3),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemoryObject":
        return cls(
            memory_id=str(payload.get("memory_id") or _memory_id(payload.get("type"), payload.get("source_object_id"), payload.get("title"), payload.get("source_event_ids"))),
            source_object_id=payload.get("source_object_id"),
            type=str(payload.get("type") or "unknown"),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or payload.get("title") or ""),
            owner_names=list(payload.get("owner_names") or []),
            status=str(payload.get("status") or "candidate"),
            confidence=float(payload.get("confidence") or 0.0),
            source_event_ids=list(payload.get("source_event_ids") or []),
            sources=list(payload.get("sources") or []),
            quote=str(payload.get("quote") or ""),
            evidence_state=str(payload.get("evidence_state") or "unknown"),
            review_state=str(payload.get("review_state") or "unreviewed"),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            metadata=dict(payload.get("metadata") or {}),
            review_reason_codes=list(payload.get("review_reason_codes") or []),
            memory_quality_score=float(payload.get("memory_quality_score") or 0.0),
        )


def build_candidate_memory_payload(
    *,
    input_file: str,
    mode: str,
    pipeline: str,
    decisions_payload: dict[str, Any],
    clean_memory: dict[str, Any] | None = None,
    review_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    candidates = build_candidate_memory_objects(
        decisions_payload=decisions_payload,
        clean_memory=clean_memory,
        review_queue=review_queue,
        generated_at=generated_at,
    )
    return {
        "metadata": {
            "input_file": input_file,
            "mode": mode,
            "pipeline": pipeline,
            "generated_at": generated_at,
            "candidate_count": len(candidates),
        },
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def build_candidate_memory_objects(
    *,
    decisions_payload: dict[str, Any],
    clean_memory: dict[str, Any] | None = None,
    review_queue: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> list[MemoryObject]:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    objects: list[MemoryObject] = []
    for decision in decisions_payload.get("decisions", []):
        objects.append(_memory_from_decision(decision, generated_at))
        for action_item in decision.get("action_items", []) or []:
            objects.append(_memory_from_decision_action_item(action_item, decision, generated_at))
        for field, memory_type in (("risks", "risk"), ("assumptions", "assumption")):
            for claim in decision.get(field, []) or []:
                objects.append(_memory_from_claim(claim, memory_type, decision, field, generated_at))

    clean_memory = clean_memory or {}
    for item in clean_memory.get("confirmed_memory", []) or []:
        objects.append(_memory_from_clean_memory_item(item, generated_at))
    for item in clean_memory.get("action_items", []) or []:
        objects.append(_memory_from_action_item(item, generated_at))
    for item in clean_memory.get("open_questions", []) or []:
        objects.append(_memory_from_open_question(item, generated_at))

    for item in (review_queue or {}).get("items", []) or []:
        objects.append(_memory_from_review_item(item, generated_at))
    return _dedupe_memory_objects(objects)


def _memory_from_decision(decision: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = _sources_from_item(decision)
    quote = _best_quote(sources, decision.get("decision"))
    source_ids = _source_event_ids_from_sources(sources)
    title = str(decision.get("decision") or "")
    return MemoryObject(
        memory_id=_memory_id("decision", decision.get("decision_id"), title, source_ids),
        source_object_id=decision.get("decision_id"),
        type="decision",
        title=title,
        summary=title,
        owner_names=[decision["owner"]] if decision.get("owner") else [],
        status="candidate",
        confidence=float(decision.get("confidence") or 0.0),
        source_event_ids=source_ids,
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "decision_record", "decision_status": decision.get("status")},
    )


def _memory_from_decision_action_item(action_item: dict[str, Any], decision: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = _sources_from_item(action_item) or _sources_from_item(decision)
    quote = _best_quote(sources, action_item.get("action"))
    owner_names = [action_item["owner"]] if action_item.get("owner") else []
    title = str(action_item.get("action") or "")
    return MemoryObject(
        memory_id=_memory_id("commitment", f"{decision.get('decision_id')}:action:{title}", title, _source_event_ids_from_sources(sources)),
        source_object_id=f"{decision.get('decision_id')}:action",
        type="commitment",
        title=title,
        summary=title,
        owner_names=owner_names,
        status=_commitment_status(action_item.get("status") or "open"),
        confidence=float(decision.get("confidence") or 0.75),
        source_event_ids=_source_event_ids_from_sources(sources),
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "decision_action_item", "decision_id": decision.get("decision_id")},
    )


def _memory_from_claim(claim: dict[str, Any], memory_type: str, decision: dict[str, Any], field: str, generated_at: str) -> MemoryObject:
    sources = [claim["source"]] if isinstance(claim.get("source"), dict) else _sources_from_item(decision)
    quote = _best_quote(sources, claim.get("claim"))
    title = str(claim.get("claim") or "")
    return MemoryObject(
        memory_id=_memory_id(memory_type, f"{decision.get('decision_id')}:{field}:{title}", title, _source_event_ids_from_sources(sources)),
        source_object_id=f"{decision.get('decision_id')}:{field}",
        type=memory_type,
        title=title,
        summary=title,
        status="candidate",
        confidence=float(claim.get("confidence") or decision.get("confidence") or 0.0),
        source_event_ids=_source_event_ids_from_sources(sources),
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "decision_claim", "decision_id": decision.get("decision_id"), "claim_category": field},
    )


def _memory_from_clean_memory_item(item: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = list(item.get("sources") or [])
    title = str(item.get("title") or "")
    quote = str(item.get("quote") or _best_quote(sources, title))
    source_ids = list(item.get("source_event_ids") or _source_event_ids_from_sources(sources))
    return MemoryObject(
        memory_id=_memory_id(_clean_type(item.get("type")), item.get("object_id"), title, source_ids),
        source_object_id=item.get("object_id"),
        type=_clean_type(item.get("type")),
        title=title,
        summary=str(item.get("summary") or title),
        owner_names=list(item.get("owner_names") or []),
        status="candidate",
        confidence=float(item.get("confidence") or 0.0),
        source_event_ids=source_ids,
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "clean_memory", "original_type": item.get("type")},
    )


def _memory_from_action_item(item: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = list(item.get("sources") or [])
    title = str(item.get("title") or "")
    quote = str(item.get("quote") or _best_quote(sources, title))
    source_ids = list(item.get("source_event_ids") or _source_event_ids_from_sources(sources))
    return MemoryObject(
        memory_id=_memory_id("commitment", item.get("object_id"), title, source_ids),
        source_object_id=item.get("object_id"),
        type="commitment",
        title=title,
        summary=str(item.get("summary") or title),
        owner_names=list(item.get("owner_names") or []),
        status=_commitment_status(item.get("status") or "open"),
        confidence=float(item.get("confidence") or 0.0),
        source_event_ids=source_ids,
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "action_item", "legacy_status": item.get("status")},
    )


def _memory_from_open_question(item: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = list(item.get("sources") or [])
    title = str(item.get("title") or "")
    quote = str(item.get("quote") or _best_quote(sources, title))
    source_ids = list(item.get("source_event_ids") or _source_event_ids_from_sources(sources))
    return MemoryObject(
        memory_id=_memory_id("open_question", item.get("object_id"), title, source_ids),
        source_object_id=item.get("object_id"),
        type="open_question",
        title=title,
        summary=str(item.get("summary") or title),
        status="unresolved",
        confidence=float(item.get("confidence") or 0.0),
        source_event_ids=source_ids,
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "open_question", "legacy_status": item.get("status")},
    )


def _memory_from_review_item(item: dict[str, Any], generated_at: str) -> MemoryObject:
    sources = list(item.get("sources") or [])
    title = str(item.get("candidate_decision") or item.get("title") or item.get("summary") or "")
    quote = _best_quote(sources, title)
    source_ids = list(item.get("source_event_ids") or _source_event_ids_from_sources(sources))
    return MemoryObject(
        memory_id=_memory_id(_review_item_type(item), item.get("review_item_id") or item.get("reason"), title, source_ids),
        source_object_id=item.get("review_item_id") or item.get("reason"),
        type=_review_item_type(item),
        title=title,
        summary=str(item.get("summary") or title),
        status="needs_review",
        confidence=float(item.get("confidence") or 0.0),
        source_event_ids=source_ids,
        sources=sources,
        quote=quote,
        evidence_state=_evidence_state(sources, quote),
        review_state="needs_review",
        created_at=generated_at,
        updated_at=generated_at,
        metadata={"source_kind": "review_queue", "reason": item.get("reason"), "severity": item.get("severity")},
        review_reason_codes=[str(item.get("reason"))] if item.get("reason") else [],
        memory_quality_score=0.4,
    )


def _clean_type(value: Any) -> str:
    mapping = {
        "action_item": "commitment",
        "confirmed_decision": "decision",
        "deadline": "context",
        "policy_or_process": "context",
        "change": "context",
        "open_question": "open_question",
    }
    return mapping.get(str(value or ""), "context")


def _review_item_type(item: dict[str, Any]) -> str:
    reason = str(item.get("reason") or "")
    if "conflict" in reason:
        return "contradiction"
    if "decision" in reason or "approval" in reason:
        return "decision"
    return "context"


def _sources_from_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = []
    for key in ("decision_sources", "supporting_sources", "all_sources", "sources"):
        value = item.get(key)
        if isinstance(value, list):
            sources.extend(source for source in value if isinstance(source, dict))
    return _dedupe_sources(sources)


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for source in sources:
        key = (
            source.get("source_type"),
            source.get("workspace"),
            source.get("channel_id") or source.get("document_id"),
            source.get("message_ts") or source.get("locator") or source.get("source_event_id"),
            source.get("quote"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def _source_event_ids_from_sources(sources: list[dict[str, Any]]) -> list[str]:
    ids = []
    for source in sources:
        for key in ("source_event_id", "message_ts", "locator"):
            if source.get(key):
                ids.append(str(source[key]))
                break
    return list(dict.fromkeys(ids))


def _best_quote(sources: list[dict[str, Any]], fallback: Any = "") -> str:
    for source in sources:
        quote = str(source.get("quote") or "").strip()
        if quote:
            return quote
    return str(fallback or "").strip()


def _evidence_state(sources: list[dict[str, Any]], quote: str) -> str:
    if sources and quote:
        return "supported"
    if sources or quote:
        return "weakly_supported"
    return "unknown"


def _commitment_status(status: Any) -> str:
    normalized = str(status or "").lower()
    if normalized in {"done", "resolved", "closed", "complete", "completed"}:
        return "resolved"
    if normalized in {"superseded", "stale", "needs_review", "unknown"}:
        return normalized
    if normalized in {"open", "candidate", "confirmed", "approved"}:
        return "unresolved"
    return "unknown"


def _dedupe_memory_objects(objects: list[MemoryObject]) -> list[MemoryObject]:
    result: list[MemoryObject] = []
    seen = set()
    for obj in objects:
        key = (obj.type, _normalize_key(obj.title), tuple(obj.source_event_ids), obj.source_object_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(obj)
    return result


def _memory_id(memory_type: Any, source_object_id: Any, title: Any, source_event_ids: Any) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                str(memory_type or ""),
                str(source_object_id or ""),
                str(title or ""),
                ",".join(str(item) for item in source_event_ids or []),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"mem_{digest[:12]}"


def _normalize_key(text: str) -> str:
    return " ".join(str(text or "").lower().split())
