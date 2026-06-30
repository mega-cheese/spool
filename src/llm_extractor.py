from __future__ import annotations

import json
import os
from typing import Any

from . import DEFAULT_LLM_MODEL
from .schemas import CLAIM_CATEGORIES, ActionItem, CanonicalEvent, Claim, Deadline, DecisionRecord
from .source_resolver import attach_sources


LLM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "decision": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "owner": {"type": ["string", "null"]},
                    "status": {"type": "string", "enum": ["candidate", "confirmed"]},
                    "decision_source_message_ts": {"type": "string"},
                    "effective_date": {"type": ["string", "null"]},
                    "reasoning": {"$ref": "#/$defs/claim_list"},
                    "objections": {"$ref": "#/$defs/claim_list"},
                    "assumptions": {"$ref": "#/$defs/claim_list"},
                    "risks": {"$ref": "#/$defs/claim_list"},
                    "tradeoffs": {"$ref": "#/$defs/claim_list"},
                    "timelines": {"$ref": "#/$defs/claim_list"},
                    "deadlines": {"$ref": "#/$defs/deadline_list"},
                    "action_items": {"$ref": "#/$defs/action_item_list"},
                    "revisit_date": {"type": ["string", "null"]},
                    "revisit_trigger": {"type": ["string", "null"]},
                },
                "required": [
                    "decision",
                    "date",
                    "owner",
                    "status",
                    "decision_source_message_ts",
                    "effective_date",
                    "reasoning",
                    "objections",
                    "assumptions",
                    "risks",
                    "tradeoffs",
                    "timelines",
                    "deadlines",
                    "action_items",
                    "revisit_date",
                    "revisit_trigger",
                ],
            },
        }
    },
    "required": ["decisions"],
    "$defs": {
        "claim_list": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "source_message_ts": {"type": "string"},
                    "support_level": {"type": "string", "enum": ["direct", "indirect", "unsupported"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "needs_review": {"type": "boolean"},
                },
                "required": ["claim", "source_message_ts", "support_level", "confidence", "needs_review"],
            },
        },
        "deadline_list": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "source_message_ts": {"type": "string"},
                },
                "required": ["date", "description", "source_message_ts"],
            },
        },
        "action_item_list": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                    "due_text": {"type": ["string", "null"]},
                    "source_message_ts": {"type": "string"},
                },
                "required": ["action", "owner", "due_date", "due_text", "source_message_ts"],
            },
        }
    },
}


SYSTEM_PROMPT = """You extract source-backed company decision records from one Slack thread.

Return only clear candidate decisions. Return {"decisions": []} if no decision exists.
Return {"decisions": []} for unresolved or deferred threads, including "let's not decide today", "not decide yet", "do not commit yet", or requests to bring evidence back before choosing.
If a thread contains an earlier approved decision and a later explicit reversal or replacement, return only the later active decision. Do not return the earlier superseded decision as active.
Implicit decisions are valid only when a concrete plan has convergence plus implementation commitment, such as owner assignment, accepted owner response, scope boundary, timeline, deadline, or accepted tradeoff. Do not return brainstorming, vague preferences, unresolved alternatives, or threads with no owner or implementation step.
Every decision and every claim must cite a valid message_ts from the provided thread.
Do not invent participants, owners, dates, source references, or quotes.
Do not write source quotes. Python code will attach quotes from message_ts.
Use no outside knowledge. Prefer fewer, better-supported claims.
Use plain English only. Do not use mixed-language text.
Keep claims short and atomic.
Prefer wording close to the source quote.
Do not invent or generalize beyond the cited message.
Every claim must cite exactly one valid message_ts.
Return empty arrays for unsupported categories.
Set support_level to direct, indirect, or unsupported. Set needs_review true for indirect or unsupported claims.

Classify each claim into the most specific category:
- reasoning = why the decision makes sense
- objection = explicit pushback or concern
- assumption = explicitly stated assumption
- risk = possible negative outcome
- tradeoff = accepted cost explicitly stated in the thread
- timeline = implementation estimate, duration, delivery window, or expected implementation timing
- deadline = external milestone or hard due date
- action_item = assigned next step

Status defaults to candidate. Use confirmed only if the thread explicitly says final decision, approved, agreed, or equivalent.
Use date only for the date the decision was made from message metadata. If no decision-made date is available, use null.
Use reasoning for why the decision was made.
Use objections for explicit concern, worry, disagreement, or pushback.
Use tradeoffs only for accepted costs explicitly stated in the thread. If the source says "the tradeoff is X" or "the tradeoff with Y is X", classify X as tradeoff, not risk.
Use assumptions only for explicitly stated assumptions.
Use risks for possible negative outcomes or failure modes.
Use timelines for chosen-plan implementation estimates, durations, delivery windows, or expected timing. Do not include rejected-alternative timing, final decision text, effective dates, external deadlines, or revisit triggers in timelines.
Use deadlines for external milestones or hard due dates, such as "Enterprise SLA starts on 2026-07-15."
Use action_items for assigned next steps, not as decision records or timelines.
Use effective_date for the calendar date when the decision starts applying, such as pricing starting on 2026-08-01. Do not copy effective_date into date.
Use revisit_date only for ISO-like calendar dates.
Use revisit_trigger for non-date revisit conditions like "after launch", "after rollout", "after 30 days", or "if Redis becomes a bottleneck."
If the source says "Engineering needs about four more weeks.", put that claim in timelines.
If the source says "If Redis becomes a bottleneck later, we can revisit the design after launch.", put "Redis could become a bottleneck later." in risks and "After launch if Redis becomes a bottleneck." in revisit_trigger.
If the source says "The tradeoff is extra infra complexity", output:
"Extra infrastructure complexity is the accepted tradeoff."
Unsupported categories must be empty arrays."""


def extract_decisions_with_llm(
    grouped_threads: dict[str, list[CanonicalEvent]],
    event_lookup: dict[str, CanonicalEvent],
    model: str | None = None,
    max_threads: int | None = None,
) -> list[DecisionRecord]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("LLM mode requires the openai package. Run: python3 -m pip install -r requirements.txt") from exc

    model = model or os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL)
    client = OpenAI()
    decisions: list[DecisionRecord] = []

    for index, (_thread_ts, events) in enumerate(grouped_threads.items()):
        if max_threads is not None and index >= max_threads:
            break
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _thread_prompt(events)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "decision_memory_extraction",
                    "strict": True,
                    "schema": LLM_OUTPUT_SCHEMA,
                }
            },
        )
        payload = _parse_response_payload(response)
        for raw_decision in payload.get("decisions", []):
            decision = _decision_from_llm(raw_decision, events)
            decisions.append(attach_sources(decision, event_lookup))

    return decisions


def _thread_prompt(events: list[CanonicalEvent]) -> str:
    rows = []
    for event in events:
        rows.append(
            {
                "message_ts": event.message_ts,
                "date": event.timestamp,
                "speaker": event.speaker,
                "speaker_role": event.speaker_role,
                "text": event.text,
            }
        )
    return json.dumps({"thread": rows}, indent=2)


def _parse_response_payload(response: Any) -> dict[str, Any]:
    raw = getattr(response, "output_text", None)
    if raw:
        return json.loads(raw)

    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
    elif hasattr(response, "to_dict"):
        dumped = response.to_dict()
    else:
        dumped = json.loads(str(response))

    for item in dumped.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                return json.loads(content["text"])

    raise RuntimeError("OpenAI response did not contain parseable JSON text.")


def _decision_from_llm(raw: dict[str, Any], events: list[CanonicalEvent]) -> DecisionRecord:
    source_event = next((event for event in events if event.message_ts == raw["decision_source_message_ts"]), events[0])
    participants = []
    roles_by_speaker: dict[str, str | None] = {}
    for event in events:
        if event.speaker not in participants:
            participants.append(event.speaker)
        roles_by_speaker[event.speaker] = event.speaker_role

    decision = DecisionRecord(
        decision_id=_stable_id(source_event.thread_ts, raw["decision"]),
        decision=raw["decision"],
        date=raw.get("date") or source_event.timestamp,
        owner=raw.get("owner"),
        owner_role=roles_by_speaker.get(raw.get("owner")),
        participants=participants,
        status=raw.get("status") or "candidate",
        confidence=0.86,
        effective_date=raw.get("effective_date"),
        revisit_date=raw.get("revisit_date"),
        revisit_trigger=raw.get("revisit_trigger"),
        decision_source_message_ts=raw["decision_source_message_ts"],
    )

    for category in CLAIM_CATEGORIES:
        claims = [
            Claim(
                claim=item["claim"],
                source_message_ts=item.get("source_message_ts"),
                support_level=_support_level(item.get("support_level")),
                confidence=float(item.get("confidence", 0.75)),
                needs_review=bool(item.get("needs_review", False)),
            )
            for item in raw.get(category, [])
            if item.get("claim")
        ]
        setattr(decision, category, claims)

    decision.deadlines = [
        Deadline(
            date=item["date"],
            description=item["description"],
            source_message_ts=item.get("source_message_ts"),
        )
        for item in raw.get("deadlines", [])
        if item.get("date") and item.get("description")
    ]
    decision.action_items = [
        ActionItem(
            action=item["action"],
            owner=item.get("owner"),
            due_date=item.get("due_date"),
            due_text=item.get("due_text"),
            source_message_ts=item.get("source_message_ts"),
        )
        for item in raw.get("action_items", [])
        if item.get("action")
    ]

    return decision


def _stable_id(thread_ts: str, decision: str) -> str:
    import hashlib

    return "dec_" + hashlib.sha1(f"{thread_ts}:{decision}".encode("utf-8")).hexdigest()[:10]


def _support_level(value: Any) -> str:
    if value in {"direct", "indirect", "unsupported"}:
        return str(value)
    return "direct"
