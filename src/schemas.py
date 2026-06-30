from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CLAIM_CATEGORIES = ("reasoning", "objections", "assumptions", "risks", "tradeoffs", "timelines")
VALID_STATUSES = {"candidate", "confirmed", "approved", "rejected", "archived", "superseded"}


@dataclass
class CanonicalEvent:
    source_type: str
    workspace: str
    channel: str
    channel_id: str
    thread_ts: str
    message_ts: str
    timestamp: str | None
    speaker: str
    speaker_role: str | None
    text: str
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "workspace": self.workspace,
            "channel": self.channel,
            "channel_id": self.channel_id,
            "thread_ts": self.thread_ts,
            "message_ts": self.message_ts,
            "timestamp": self.timestamp,
            "speaker": self.speaker,
            "speaker_role": self.speaker_role,
            "text": self.text,
            "url": self.url,
        }


@dataclass
class Source:
    source_type: str
    workspace: str
    channel: str
    channel_id: str
    thread_ts: str
    message_ts: str
    speaker: str
    speaker_role: str | None
    quote: str
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "workspace": self.workspace,
            "channel": self.channel,
            "channel_id": self.channel_id,
            "thread_ts": self.thread_ts,
            "message_ts": self.message_ts,
            "speaker": self.speaker,
            "speaker_role": self.speaker_role,
            "quote": self.quote,
            "url": self.url,
        }

    def dedupe_key(self) -> tuple[str, str, str, str]:
        return (self.source_type, self.workspace, self.channel_id, self.message_ts)


@dataclass
class Claim:
    claim: str
    source_message_ts: str | None = None
    source: Source | None = None
    support_level: str = "direct"
    confidence: float = 0.75
    needs_review: bool = False

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "claim": self.claim,
            "support_level": self.support_level,
            "confidence": round(float(self.confidence), 3),
        }
        if self.needs_review:
            result["needs_review"] = True
        if include_internal and self.source_message_ts:
            result["source_message_ts"] = self.source_message_ts
        if self.source is not None:
            result["source"] = self.source.to_dict()
        return result


@dataclass
class Deadline:
    date: str
    description: str
    source_message_ts: str | None = None
    source: Source | None = None

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "date": self.date,
            "description": self.description,
        }
        if include_internal and self.source_message_ts:
            result["source_message_ts"] = self.source_message_ts
        if self.source is not None:
            result["source"] = self.source.to_dict()
        return result


@dataclass
class ActionItem:
    action: str
    owner: str | None = None
    due_date: str | None = None
    due_text: str | None = None
    source_message_ts: str | None = None
    source: Source | None = None

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "owner": self.owner,
            "due_date": self.due_date,
            "due_text": self.due_text,
        }
        if include_internal and self.source_message_ts:
            result["source_message_ts"] = self.source_message_ts
        if self.source is not None:
            result["source"] = self.source.to_dict()
        return result


@dataclass(kw_only=True)
class DecisionRecord:
    decision_id: str
    decision: str
    date: str | None
    owner: str | None
    owner_role: str | None
    participants: list[str]
    status: str = "candidate"
    confidence: float = 0.75
    effective_date: str | None = None
    reasoning: list[Claim] = field(default_factory=list)
    objections: list[Claim] = field(default_factory=list)
    assumptions: list[Claim] = field(default_factory=list)
    risks: list[Claim] = field(default_factory=list)
    tradeoffs: list[Claim] = field(default_factory=list)
    timelines: list[Claim] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    revisit_date: str | None = None
    revisit_trigger: str | None = None
    sources: list[Source] = field(default_factory=list)
    decision_source_message_ts: str | None = None
    decision_sources: list[Source] = field(default_factory=list)
    supporting_sources: list[Source] = field(default_factory=list)
    all_sources: list[Source] = field(default_factory=list)

    def all_claims(self) -> list[Claim]:
        claims: list[Claim] = []
        for category in CLAIM_CATEGORIES:
            claims.extend(getattr(self, category))
        return claims

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        self.refresh_source_fields()
        result = {
            "decision_id": self.decision_id,
            "decision": self.decision,
            "date": self.date,
            "owner": self.owner,
            "owner_role": self.owner_role,
            "participants": self.participants,
            "status": self.status,
            "confidence": round(float(self.confidence), 3),
            "effective_date": self.effective_date,
            "reasoning": [claim.to_dict(include_internal) for claim in self.reasoning],
            "objections": [claim.to_dict(include_internal) for claim in self.objections],
            "assumptions": [claim.to_dict(include_internal) for claim in self.assumptions],
            "risks": [claim.to_dict(include_internal) for claim in self.risks],
            "tradeoffs": [claim.to_dict(include_internal) for claim in self.tradeoffs],
            "timelines": [claim.to_dict(include_internal) for claim in self.timelines],
            "deadlines": [deadline.to_dict(include_internal) for deadline in self.deadlines],
            "action_items": [action_item.to_dict(include_internal) for action_item in self.action_items],
            "revisit_date": self.revisit_date,
            "revisit_trigger": self.revisit_trigger,
            "decision_sources": [source.to_dict() for source in self.decision_sources],
            "supporting_sources": [source.to_dict() for source in self.supporting_sources],
            "all_sources": [source.to_dict() for source in self.all_sources],
        }
        if include_internal and self.decision_source_message_ts:
            result["decision_source_message_ts"] = self.decision_source_message_ts
        return result

    def refresh_source_fields(self) -> None:
        decision_sources = self.decision_sources or self.sources
        self.decision_sources = _dedupe_sources(decision_sources)
        self.sources = list(self.decision_sources)
        self.supporting_sources = _dedupe_sources(
            source
            for source in [
                *(claim.source for claim in self.all_claims() if claim.source is not None),
                *(deadline.source for deadline in self.deadlines if deadline.source is not None),
                *(action_item.source for action_item in self.action_items if action_item.source is not None),
            ]
        )
        self.all_sources = _dedupe_sources([*self.decision_sources, *self.supporting_sources])


def _dedupe_sources(sources: Any) -> list[Source]:
    deduped: dict[tuple[str, str, str, str], Source] = {}
    for source in sources:
        if source is None:
            continue
        deduped.setdefault(source.dedupe_key(), source)
    return list(deduped.values())


def output_to_dict(
    metadata: dict[str, Any],
    decisions: list[DecisionRecord],
    include_internal: bool = False,
) -> dict[str, Any]:
    return {
        "metadata": metadata,
        "decisions": [decision.to_dict(include_internal) for decision in decisions],
    }
