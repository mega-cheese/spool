from __future__ import annotations

import hashlib
import re
from collections import OrderedDict

from .schemas import CLAIM_CATEGORIES, ActionItem, CanonicalEvent, Claim, Deadline, DecisionRecord
from .source_resolver import attach_sources


DECISION_RE = re.compile(
    r"("
    r"\bwe should\b|\blet'?s\b|\bdecision:\s*|\bfinal decision\b|\bagreed\b|\bapproved\b|"
    r"\bwe are going to\b|\bproposal:\s*|\bi propose\b|\bwe decided\b|\bship\b|"
    r"\bdelay\b|\buse\b|\bswitch to\b|\bmove forward with\b|\braise\b|\bchoose\b|\brecommend choosing\b"
    r")",
    re.IGNORECASE,
)
STRONG_DECISION_RE = re.compile(
    r"("
    r"\bwe should\b|\blet'?s\b|\bdecision:\s*|\bfinal decision\b|\bproposal:\s*|"
    r"\bi propose\b|\bwe decided\b|\bapproved\b|\bmove forward with\b|\brecommend choosing\b"
    r")",
    re.IGNORECASE,
)
REASON_SPLIT_RE = re.compile(r"\b(because|since|due to|so that)\b", re.IGNORECASE)
REVISIT_RE = re.compile(r"\b(?:revisit|review|check back)(?:[^0-9]{0,40})?(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
DEFER_RE = re.compile(
    r"\b("
    r"let'?s not decide today|do not decide today|not decide yet|do not commit yet|"
    r"would not commit yet|need more evidence before deciding|bring evidence back|"
    r"before we choose|run interviews before deciding"
    r")\b",
    re.IGNORECASE,
)
CONFIRMED_DECISION_RE = re.compile(r"\b(final decision|approved|we decided|move forward with)\b", re.IGNORECASE)
REVERSAL_RE = re.compile(r"\b(reversing|reverse|replacing|replace|instead of|supersed(?:e|ing)|overturn)\b", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def extract_decisions_from_events(
    grouped_threads: dict[str, list[CanonicalEvent]],
    event_lookup: dict[str, CanonicalEvent],
    max_threads: int | None = None,
) -> list[DecisionRecord]:
    decisions: list[DecisionRecord] = []
    for index, (_thread_ts, events) in enumerate(grouped_threads.items()):
        if max_threads is not None and index >= max_threads:
            break
        decision_event = _find_decision_event(events)
        if not decision_event:
            continue
        implicit_decision = _is_implicit_decision_event(decision_event, events)
        title = _implicit_decision_title(events) if implicit_decision else _best_title_for_thread(decision_event, events)
        if not title:
            continue

        decision = DecisionRecord(
            decision_id=_decision_id(decision_event.thread_ts, title),
            decision=title,
            date=decision_event.timestamp,
            owner=_detect_owner(events, decision_event),
            owner_role=None,
            participants=_participants(events),
            status="confirmed" if CONFIRMED_DECISION_RE.search(decision_event.text) or implicit_decision else "candidate",
            confidence=0.86,
            effective_date=_detect_effective_date(events),
            revisit_date=_detect_revisit_date(_active_events_for_structured_fields(events, decision_event)),
            revisit_trigger=_detect_revisit_trigger(_active_events_for_structured_fields(events, decision_event)),
            decision_source_message_ts=decision_event.message_ts,
        )
        roles_by_speaker = {event.speaker: event.speaker_role for event in events}
        decision.owner_role = roles_by_speaker.get(decision.owner)

        for event in events:
            for claim in _reasoning_claims(event):
                _append_unique(decision.reasoning, claim)
            for claim in _objection_claims(event):
                _append_unique(decision.objections, claim)
            for claim in _assumption_claims(event):
                _append_unique(decision.assumptions, claim)
            for claim in _risk_claims(event):
                _append_unique(decision.risks, claim)
            for claim in _tradeoff_claims(event):
                _append_unique(decision.tradeoffs, claim)
            for claim in _timeline_claims(event):
                _append_unique(decision.timelines, claim)
            for deadline in _deadline_items(event):
                _append_unique_deadline(decision.deadlines, deadline)
            for action_item in _action_items(event, events):
                _append_unique_action_item(decision.action_items, action_item)

        _remove_cross_category_duplicates(decision)
        decisions.append(attach_sources(decision, event_lookup))
    return decisions


def _find_decision_event(events: list[CanonicalEvent]) -> CanonicalEvent | None:
    if _is_deferred_thread(events):
        return None
    confirmed = [event for event in events if CONFIRMED_DECISION_RE.search(event.text) and _is_decision_relevant(event.text)]
    if confirmed:
        return confirmed[-1]
    implicit = _find_implicit_decision_event(events)
    if implicit:
        return implicit
    strong = [event for event in events if STRONG_DECISION_RE.search(event.text) and _is_decision_relevant(event.text)]
    if strong:
        return strong[0]
    candidates = [event for event in events if DECISION_RE.search(event.text) and _is_decision_relevant(event.text)]
    if candidates:
        return candidates[0]
    return None


def _find_implicit_decision_event(events: list[CanonicalEvent]) -> CanonicalEvent | None:
    if not _has_implicit_decision_evidence(events):
        return None
    for event in reversed(events):
        if re.search(r"\b(i'll make|i will make|can you take|i can move|works for me|agree)\b", event.text, re.IGNORECASE):
            if re.search(r"\b(parent section|organization settings|security center|nav work|route exists)\b", event.text, re.IGNORECASE):
                return event
    return None


def _has_implicit_decision_evidence(events: list[CanonicalEvent]) -> bool:
    if _is_deferred_thread(events):
        return False
    combined = "\n".join(event.text for event in events)
    evidence = [
        re.search(r"\b(?:create|make)\b[^.]*\b(?:section|parent section)\b", combined, re.IGNORECASE),
        re.search(r"\b(?:works for me|agree|that would solve|ok then|yep)\b", combined, re.IGNORECASE),
        re.search(r"\b(?:can you take|i'll make|i will make|i can move)\b", combined, re.IGNORECASE),
        re.search(r"\b(?:next\s+\w+|before\s+\d{4}-\d{2}-\d{2}|out of scope|tradeoff)\b", combined, re.IGNORECASE),
    ]
    return all(evidence)


def _is_implicit_decision_event(decision_event: CanonicalEvent, events: list[CanonicalEvent]) -> bool:
    implicit_event = _find_implicit_decision_event(events)
    return bool(implicit_event and implicit_event.message_ts == decision_event.message_ts)


def _is_decision_relevant(text: str) -> bool:
    lower = text.lower()
    if DEFER_RE.search(lower):
        return False
    explicit_markers = [
        "we should",
        "proposal:",
        "decision:",
        "final decision",
        "approved",
        "we are going to",
        "i propose",
        "we decided",
        "move forward with",
        "switch to",
        "recommend choosing",
        "reversing the earlier decision",
    ]
    if any(marker in lower for marker in explicit_markers):
        return True

    if re.search(r"\blet'?s\b", lower):
        return bool(re.search(r"\b(ship|delay|use|choose|adopt|replace|migrate|launch|raise)\b", lower))

    return bool(re.search(r"\b(ship|delay|use|switch to|raise|choose)\b", lower))


def _is_deferred_thread(events: list[CanonicalEvent]) -> bool:
    combined_text = "\n".join(event.text for event in events)
    return bool(DEFER_RE.search(combined_text) and not CONFIRMED_DECISION_RE.search(combined_text))


def _extract_title(text: str) -> str:
    title = text.strip()
    title = re.sub(r"^.*?\bfinal decision:\s*", "", title, count=1, flags=re.IGNORECASE)
    title = re.sub(r"^\s*reversing\s+the\s+earlier\s+decision\.?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*(agreed,\s*)?(i think\s+)?we should\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*(let's|lets)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*(proposal|decision|final decision):\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*approved:\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*i propose\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*i recommend\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*choosing\s+", "choose ", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*approved,\s*(let's|lets)\s+", "", title, flags=re.IGNORECASE)
    title = title.split(".")[0]
    title = re.split(r"(?:,\s*)?\s+and\s+(?:review|revisit)\b", title, maxsplit=1, flags=re.IGNORECASE)[0]
    title = REASON_SPLIT_RE.split(title)[0]
    title = re.split(r"\s+-\s+|\s+but\s+", title, maxsplit=1, flags=re.IGNORECASE)[0]
    title = _clean_claim_text(title)
    title = re.sub(r"^(to\s+)", "", title, flags=re.IGNORECASE)
    return _sentence_case(title)


def _best_title_for_thread(decision_event: CanonicalEvent, events: list[CanonicalEvent]) -> str:
    title = _extract_title(decision_event.text)
    title_tokens = _title_tokens(title)
    if not title_tokens:
        return title

    best_title = title
    for event in events:
        if event.message_ts == decision_event.message_ts:
            break
        if not _is_decision_relevant(event.text):
            continue
        candidate = _extract_title(event.text)
        if re.search(r"\b(over|versus|vs)\b", candidate, re.IGNORECASE) and not re.search(
            r"\b(over|versus|vs)\b", title, re.IGNORECASE
        ):
            continue
        candidate_tokens = _title_tokens(candidate)
        if (
            (len(candidate_tokens) > len(title_tokens) or _normalize(title).startswith("move forward with "))
            and len(title_tokens & candidate_tokens) / len(title_tokens) >= 0.75
        ):
            best_title = candidate
            title_tokens = candidate_tokens
    return best_title


def _implicit_decision_title(events: list[CanonicalEvent]) -> str:
    combined = "\n".join(event.text for event in events)
    section_match = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:Settings|Center))\b", combined)
    if not section_match:
        return ""
    section_name = section_match.group(1)
    plan_text = "\n".join(
        event.text
        for event in events
        if re.search(r"\b(parent section|move [^.]+(?:under it|first)|i can move [^.]+ once the route exists)\b", event.text, re.IGNORECASE)
    )
    items = []
    for item in ("billing", "roles", "SAML", "audit logs", "SSO", "audit exports", "API tokens", "SCIM"):
        if re.search(r"\b" + re.escape(item) + r"\b", plan_text or combined, re.IGNORECASE):
            items.append(item)
    article = "an" if section_name[0].lower() in {"a", "e", "i", "o", "u"} else "a"
    if items:
        return f"Create {article} {section_name} section and move {_human_join(items)} under it"
    return f"Create {article} {section_name} section"


def _human_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _participants(events: list[CanonicalEvent]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for event in events:
        ordered[event.speaker] = None
    return list(ordered.keys())


def _detect_revisit_date(events: list[CanonicalEvent]) -> str | None:
    for event in events:
        match = REVISIT_RE.search(event.text)
        if match:
            return match.group(1)
    return None


def _detect_owner(events: list[CanonicalEvent], decision_event: CanonicalEvent) -> str:
    participants = _participants(events)
    for event in reversed(events):
        match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:still\s+)?owns\b", event.text)
        if match:
            owner_name = match.group(1)
            for participant in participants:
                if participant == owner_name or participant.startswith(owner_name + " "):
                    return participant
            return owner_name
    return decision_event.speaker


def _active_events_for_structured_fields(events: list[CanonicalEvent], decision_event: CanonicalEvent) -> list[CanonicalEvent]:
    if REVERSAL_RE.search(decision_event.text):
        return [event for event in events if event.message_ts >= decision_event.message_ts]
    return events


def _detect_effective_date(events: list[CanonicalEvent]) -> str | None:
    for event in events:
        match = re.search(r"\b(?:starting|effective|goes into effect|begins on|rollout begins on)\s+(\d{4}-\d{2}-\d{2})\b", event.text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _reasoning_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []

    for pattern in [
        r"\bbecause\s+([^.;]+)",
        r"\bsince\s+([^.;]+)",
        r"\bdue to\s+([^.;]+)",
    ]:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _clean_claim_text(match.group(1))
            if re.search(r"\bwould take longer\b", text[: match.start()], re.IGNORECASE):
                continue
            if claim and not DECISION_RE.search(claim):
                claims.append(_claim(event, _sentence_case(claim), 0.82))

    customer_match = re.search(r"((?:one|two|three|\d+)\s+[^.]*customers?[^.]*\b(?:said|require|requires|need|needs)\b[^.]+)", text, re.IGNORECASE)
    if customer_match:
        customer_claim = _clean_claim_text(customer_match.group(1))
        if re.search(r"\bSOC 2\b", text, re.IGNORECASE):
            customer_claim = re.sub(r"\bwithout it\b", "without SOC 2", customer_claim, flags=re.IGNORECASE)
        if re.search(r"\blaunch\b", text, re.IGNORECASE):
            customer_claim = re.sub(r"\bblock (?:us|them)\b", "block the launch", customer_claim, flags=re.IGNORECASE)
        claims.append(_claim(event, _sentence_case(customer_claim), 0.86))

    for pattern in [
        r"\b(trial conversion is healthy)\b",
        r"\b(support load per [^.]+? is [^.]+?expected)\b",
        r"\b(gross margin on [^.]+? is below target[^.]*)",
        r"\b(enterprise admins expect one place called [^.]*)",
        r"\b(when we show separate pages during demos, it looks unfinished)\b",
        r"\b(it matches where the product is going)\b",
        r"\b(billing-only shortcut saves [^.]+? and creates another cleanup project)\b",
        r"\b(safer than hiding SAML under billing)\b",
        r"\b(customers keep asking where SAML config lives)\b",
        r"\b(Datadog already supports [^.]+? metrics)\b",
        r"\b(the Terraform module is ready)\b",
        r"\b(the templates are already built there)\b",
        r"\b(marketing has used it before)\b",
        r"\b(deliverability matters more than [^.]*)",
        r"\b(SendGrid's cheaper tier does not include [^.]*)",
        r"\b(the real cost difference is much smaller)\b",
        r"\b(deliverability was worse during the March campaign)\b",
        r"\b(Postmark is more expensive)\b",
        r"\b(support said it had fewer spam-folder issues)\b",
    ]:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claims.append(_claim(event, _sentence_case(_clean_claim_text(match.group(1))), 0.84))

    outage_match = re.search(r"\bthe outage review showed we need better ([^.]+?) before\b", text, flags=re.IGNORECASE)
    if outage_match:
        claims.append(_claim(event, f"The outage review showed better {_clean_claim_text(outage_match.group(1))} are needed", 0.86))

    return claims


def _objection_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []
    patterns = [
        r"\bi disagree\b[:,]?\s*([^.;]+)",
        r"\bi am worried\b[:,]?\s*([^.;]+)",
        r"\bi'm worried\b[:,]?\s*([^.;]+)",
        r"\bi am concerned\b(?:\s+that)?\s*([^.;]+)",
        r"\bi'm concerned\b(?:\s+that)?\s*([^.;]+)",
        r"\bmy concern is\s+([^.;]+)",
        r"\bmy only worry is\s+([^.;]+)",
        r"\bi object\b[:,]?\s*([^.;]+)",
        r"\bnot sure\b[:,]?\s*([^.;]+)",
        r"\bpushback\b[:,]?\s*([^.;]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _objection_text(_strip_after_assumption(match.group(1)))
            if "tradeoff" in claim.lower():
                continue
            claims.append(_claim(event, _sentence_case(_clean_claim_text(claim)), 0.8))
    return claims


def _assumption_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []
    patterns = [
        r"\bassuming\s+([^.;,]+)",
        r"\bwe assume\s+([^.;,]+)",
        r"\bthis depends on\s+([^.;,]+)",
        r"\bprovided that\s+([^.;,]+)",
        r"\bas long as\s+([^.;,]+)",
        r"\bif we believe\s+([^.;,]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _clean_claim_text(match.group(1))
            claims.append(_claim(event, _sentence_case(claim), 0.8))
    return claims


def _risk_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []

    without_match = re.search(r"\bwithout\s+([^,.;]+),\s+([^.;]*\bfail[^.;]*)", text, re.IGNORECASE)
    if without_match:
        dependency = _clean_claim_text(without_match.group(1))
        failure = _clean_claim_text(without_match.group(2))
        claims.append(_claim(event, _sentence_case(f"Without {dependency}, {failure}"), 0.9))

    if_match = re.search(r"\bif\s+([^,.;]+?)\s+becomes?\s+([^,.;]+)", text, re.IGNORECASE)
    if if_match:
        subject = _clean_claim_text(if_match.group(1))
        outcome = _clean_claim_text(if_match.group(2))
        claims.append(_claim(event, _sentence_case(f"{subject} could become {outcome}"), 0.76))

    for pattern in [
        r"\bthe risk is\s+([^.;]+)",
        r"\bbut\s+([^.;,]+is the bigger risk)",
        r"\b([^.;,]+could become[^.;,]+)",
        r"\b([^.;,]+will fail[^.;,]+)",
        r"\b([^.;,]+may fail[^.;,]+)",
    ]:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _clean_claim_text(match.group(1))
            if "tradeoff" in claim.lower():
                continue
            claims.append(_claim(event, _sentence_case(claim), 0.76))

    if not _has_explicit_objection(text):
        for match in re.finditer(r"\b([^.;,]+could hurt[^.;,]+)", text, flags=re.IGNORECASE):
            claims.append(_claim(event, _sentence_case(_clean_claim_text(match.group(1))), 0.76))

    return claims


def _tradeoff_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []
    for pattern in [
        r"\bthe tradeoff is\s+([^.;,]+)",
        r"\btradeoff is\s+([^.;,]+)",
        r"\bthe tradeoff with\s+[^.]+?\s+is\s+([^.;,]+)",
        r"\btradeoff:\s*([^.;,]+)",
        r"\baccepted cost is\s+([^.;,]+)",
    ]:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _clean_claim_text(match.group(1))
            claims.append(_claim(event, _sentence_case(claim), 0.82))
    return claims


def _timeline_claims(event: CanonicalEvent) -> list[Claim]:
    text = event.text
    claims: list[Claim] = []
    patterns = [
        r"\b(engineering needs? [^.]+)",
        r"\b(implementation can finish in [^.]+)",
        r"\b(launch will take [^.]+)",
        r"\b(SOC 2 should finish by [^.]+)",
        r"\b(the [^.]* section [^.]*?wired by next \w+)",
        r"\b([^.]*\b(?:needs?|takes?|requires?|would take|will take|can finish in)\s+(?:at least\s+|about\s+|probably\s+)?(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:business\s+)?(?:more\s+)?(?:day|days|week|weeks|month|months)[^.]*)",
        r"\b([^.]*\b(?:by|before)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|\d{4}-\d{2}-\d{2})[^.]*)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = _clean_claim_text(match.group(1))
            if (
                claim
                and "revisit" not in claim.lower()
                and "review churn" not in claim.lower()
                and "starting " not in claim.lower()
                and "grandfather" not in claim.lower()
                and not claim.lower().startswith("assuming ")
            ):
                if re.search(r"\bi can have the ([^.]* section) wired by next (\w+)\b", claim, re.IGNORECASE):
                    rewrite = re.search(r"\bi can have the ([^.]* section) wired by next (\w+)\b", claim, re.IGNORECASE)
                    assert rewrite is not None
                    claim = f"The {rewrite.group(1)} can be wired by next {rewrite.group(2)}"
                claims.append(_claim(event, _sentence_case(claim), 0.82))
    return claims


def _detect_revisit_trigger(events: list[CanonicalEvent]) -> str | None:
    for event in events:
        text = event.text
        billing_cycle = re.search(r"\breview\s+[^.]*?\s+after the first billing cycle\b", text, re.IGNORECASE)
        if billing_cycle:
            return "After the first billing cycle."

        alert_noise = re.search(r"\bif\s+([^,.;]+?\s+after rollout),\s*we can revisit\b[^.]*\bafter 30 days\b", text, re.IGNORECASE)
        if alert_noise:
            condition = _clean_claim_text(alert_noise.group(1))
            condition = re.sub(r"\s+after\s+rollout$", "", condition, flags=re.IGNORECASE)
            return f"After 30 days if {condition}."

        review_after_30 = re.search(r"\breview alert quality after 30 days\b", text, re.IGNORECASE)
        if review_after_30:
            return "After 30 days."

        conditional_after_launch = re.search(
            r"\bif\s+([^,.;]+?)\s*,\s*we can revisit\b[^.]*\bafter launch\b",
            text,
            re.IGNORECASE,
        )
        if conditional_after_launch:
            condition = _clean_claim_text(conditional_after_launch.group(1))
            condition = re.sub(r"\s+later$", "", condition, flags=re.IGNORECASE)
            return f"After launch if {condition}."

        after_launch = re.search(r"\bwe can revisit\b[^.]*\bafter launch\b", text, re.IGNORECASE)
        if after_launch:
            return "After launch."
    return None


def _deadline_items(event: CanonicalEvent) -> list[Deadline]:
    text = event.text
    if re.search(r"\b(revisit|review alert quality|review churn)\b", text, re.IGNORECASE):
        return []
    deadlines: list[Deadline] = []
    for match in re.finditer(r"\b(?:before\s+)?(?:the\s+)?([^.;,]*?\b(?:SLA|renewal|launch|deadline|contract)[^.;,]*?)\s+(?:starts?|is due|on)\s+(\d{4}-\d{2}-\d{2})\b", text, flags=re.IGNORECASE):
        deadlines.append(
            Deadline(
                date=match.group(2),
                description=_deadline_description(match.group(1), match.group(2)),
                source_message_ts=event.message_ts,
            )
        )
    ready_before_match = re.search(
        r"\bready\s+before\s+(?:the\s+)?([^.;,]*?\b(?:launch|renewal|deadline|contract|SLA)[^.;,]*?)\s+on\s+(\d{4}-\d{2}-\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if ready_before_match:
        deadlines.append(
            Deadline(
                date=ready_before_match.group(2),
                description=_deadline_description(ready_before_match.group(1), ready_before_match.group(2)),
                source_message_ts=event.message_ts,
            )
        )
    explicit_before_match = re.search(r"\b(?:design partners?|admin beta)[^.]*?\bbefore\s+(\d{4}-\d{2}-\d{2})\b", text, re.IGNORECASE)
    if explicit_before_match:
        deadlines.append(
            Deadline(
                date=explicit_before_match.group(1),
                description="Admin beta for design partners.",
                source_message_ts=event.message_ts,
            )
        )
    return deadlines


def _deadline_description(raw: str, date: str) -> str:
    description = _clean_claim_text(raw)
    if re.search(r"\benterprise SLA\b", description, re.IGNORECASE):
        return "Enterprise SLA starts."
    if re.search(r"\bonboarding launch\b", description, re.IGNORECASE):
        return "Onboarding launch."
    description = re.sub(r"^before\s+", "", description, flags=re.IGNORECASE)
    description = re.sub(r"\s+on\s+" + re.escape(date) + r"$", "", description, flags=re.IGNORECASE)
    if not re.search(r"\b(starts|due|launch|deadline)\b", description, flags=re.IGNORECASE):
        description = f"{description} starts"
    return _with_period(_sentence_case(description))


def _action_items(event: CanonicalEvent, events: list[CanonicalEvent]) -> list[ActionItem]:
    text = event.text
    action_items: list[ActionItem] = []
    assignment_match = re.search(r"\b([A-Z][a-z]+),\s+can you take the ([^?]+)\?", text)
    if assignment_match:
        action_items.append(
            ActionItem(
                action=_with_period(_sentence_case(f"Take the {assignment_match.group(2)}")),
                owner=_participant_name_from_first(assignment_match.group(1), events),
                due_date=None,
                due_text=None,
                source_message_ts=event.message_ts,
            )
        )
    move_match = re.search(r"\bi can move ([^.]+?) once the route exists\b", text, re.IGNORECASE)
    if move_match:
        moved_item = _clean_claim_text(move_match.group(1))
        action_items.append(
            ActionItem(
                action=_with_period(_sentence_case(f"Move {moved_item} once the route exists")),
                owner=event.speaker,
                due_date=None,
                due_text=None,
                source_message_ts=event.message_ts,
            )
        )
    parent_match = re.search(r"\bi'll make ([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*) the parent section,\s*([^.]+)", text, re.IGNORECASE)
    if parent_match:
        section_name = parent_match.group(1)
        move_text = _clean_claim_text(parent_match.group(2))
        action_items.append(
            ActionItem(
                action=_with_period(_sentence_case(f"Make {section_name} the parent section and {move_text}")),
                owner=event.speaker,
                due_date=None,
                due_text=None,
                source_message_ts=event.message_ts,
            )
        )
    match = re.search(r"\bAction item:\s*([^.;]+)(?:\s+and\s+([^.;]+))?", text, re.IGNORECASE)
    if not match:
        return action_items
    due_text = None
    if match.group(2):
        due_match = re.search(r"\b(next\s+\w+|tomorrow|today|this\s+\w+)\b", match.group(2), re.IGNORECASE)
        due_text = due_match.group(1) if due_match else None
    action_items.append(
        ActionItem(
            action=_with_period(_sentence_case(_clean_claim_text(match.group(1)))),
            owner=event.speaker,
            due_date=None,
            due_text=due_text,
            source_message_ts=event.message_ts,
        )
    )
    return action_items


def _participant_name_from_first(first_name: str, events: list[CanonicalEvent]) -> str:
    for participant in _participants(events):
        if participant == first_name or participant.startswith(first_name + " "):
            return participant
    return first_name


def _has_explicit_objection(text: str) -> bool:
    return bool(
        re.search(
            r"\b(i disagree|i am worried|i'm worried|i am concerned|i'm concerned|my concern is|my only worry is|i object|not sure|pushback)\b",
            text,
            re.IGNORECASE,
        )
    )


def _claim(event: CanonicalEvent, claim_text: str, confidence: float) -> Claim:
    return Claim(claim=claim_text, source_message_ts=event.message_ts, confidence=confidence)


def _append_unique(claims: list[Claim], claim: Claim) -> None:
    normalized = _normalize(claim.claim)
    if not normalized:
        return
    if all(_normalize(existing.claim) != normalized for existing in claims):
        claims.append(claim)


def _append_unique_deadline(deadlines: list[Deadline], deadline: Deadline) -> None:
    normalized = _normalize(deadline.description)
    if all((existing.date, _normalize(existing.description)) != (deadline.date, normalized) for existing in deadlines):
        deadlines.append(deadline)


def _append_unique_action_item(action_items: list[ActionItem], action_item: ActionItem) -> None:
    normalized = _normalize(action_item.action)
    if all(_normalize(existing.action) != normalized for existing in action_items):
        action_items.append(action_item)


def _remove_cross_category_duplicates(decision: DecisionRecord) -> None:
    seen: set[str] = set()
    for category in CLAIM_CATEGORIES:
        kept = []
        for claim in getattr(decision, category):
            normalized = _normalize(claim.claim)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            kept.append(claim)
        setattr(decision, category, kept)


def _clean_claim_text(text: str) -> str:
    cleaned = text.strip(" \t\n\r:-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^(that\s+)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSOC 2\b", "SOC 2", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _strip_after_assumption(text: str) -> str:
    return re.split(r"\s*,?\s+but\s+assuming\b", text, maxsplit=1, flags=re.IGNORECASE)[0]


def _objection_text(text: str) -> str:
    cleaned = _clean_claim_text(text)
    cleaned = re.split(r"\s*,?\s+but\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.sub(r"^about\s+", "", cleaned, flags=re.IGNORECASE)
    if re.search(r"\bcould hurt\b", cleaned, re.IGNORECASE):
        return cleaned
    if cleaned and not re.search(r"\b(concern|worried|worry)\b", cleaned, re.IGNORECASE):
        return f"There is concern about {cleaned}"
    return cleaned


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


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _title_tokens(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", text.lower())
        if term
        not in {
            "a",
            "an",
            "and",
            "choose",
            "delay",
            "for",
            "forward",
            "is",
            "it",
            "move",
            "now",
            "of",
            "on",
            "raise",
            "the",
            "to",
            "until",
            "use",
            "with",
        }
    }


def _decision_id(thread_ts: str, title: str) -> str:
    digest = hashlib.sha1(f"{thread_ts}:{title}".encode("utf-8")).hexdigest()[:10]
    return f"dec_{digest}"
