from __future__ import annotations

from .schemas import CLAIM_CATEGORIES, CanonicalEvent, DecisionRecord, Source


class InvalidSourceError(ValueError):
    """Raised when a claim cites a message timestamp absent from the source JSON."""


def build_event_lookup(events: list[CanonicalEvent]) -> dict[str, CanonicalEvent]:
    return {event.message_ts: event for event in events}


def resolve_source(message_ts: str, event_lookup: dict[str, CanonicalEvent]) -> Source:
    if message_ts not in event_lookup:
        raise InvalidSourceError(message_ts)

    event = event_lookup[message_ts]
    return Source(
        source_type=event.source_type,
        workspace=event.workspace,
        channel=event.channel,
        channel_id=event.channel_id,
        thread_ts=event.thread_ts,
        message_ts=event.message_ts,
        speaker=event.speaker,
        speaker_role=event.speaker_role,
        quote=event.text,
        url=event.url,
    )


def attach_sources(decision: DecisionRecord, event_lookup: dict[str, CanonicalEvent]) -> DecisionRecord:
    if decision.decision_source_message_ts:
        try:
            source = resolve_source(decision.decision_source_message_ts, event_lookup)
            decision.decision_sources = [source]
            decision.sources = [source]
        except InvalidSourceError:
            decision.decision_sources = []
            decision.sources = []

    for category in CLAIM_CATEGORIES:
        kept_claims = []
        for claim in getattr(decision, category):
            if not claim.source and claim.source_message_ts:
                try:
                    claim.source = resolve_source(claim.source_message_ts, event_lookup)
                except InvalidSourceError:
                    pass
            kept_claims.append(claim)
        setattr(decision, category, kept_claims)

    for deadline in decision.deadlines:
        if not deadline.source and deadline.source_message_ts:
            try:
                deadline.source = resolve_source(deadline.source_message_ts, event_lookup)
            except InvalidSourceError:
                pass

    for action_item in decision.action_items:
        if not action_item.source and action_item.source_message_ts:
            try:
                action_item.source = resolve_source(action_item.source_message_ts, event_lookup)
            except InvalidSourceError:
                pass

    decision.refresh_source_fields()
    return decision


def attach_sources_to_decisions(
    decisions: list[DecisionRecord],
    event_lookup: dict[str, CanonicalEvent],
) -> list[DecisionRecord]:
    return [attach_sources(decision, event_lookup) for decision in decisions]
