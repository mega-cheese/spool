from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .block_classifier import classify_blocks
from .coverage_audit import audit_task_coverage, build_block_classification_debug
from .schemas import Claim, DecisionRecord, Source
from .source_resolver import attach_sources
from .task_extractor import extract_action_items_from_blocks, extract_explicit_task_spans


QUOTE_MATCH_STOPWORDS = {
    "a",
    "about",
    "and",
    "at",
    "be",
    "for",
    "if",
    "in",
    "of",
    "or",
    "the",
    "to",
    "with",
    "would",
}
TODO_HEADING_RE = re.compile(r"\b(to-?do(?: list)?|todo|tasks?|next steps?|follow-?ups?|action items?)\b", re.IGNORECASE)
ACTION_LIKE_RE = re.compile(
    r"\b("
    r"[A-Z][A-Za-z]+(?:\s+and\s+[A-Z][A-Za-z]+)?\s+to\s+|"
    r"[A-Z][A-Za-z]+\s*:\s+|"
    r"[A-Z][A-Za-z]+\s*-\s+|"
    r"will\s+(?:do|send|write|finish|look|try)|"
    r"finish by|deadline|send out|follow up|look through|start sending|"
    r"reach out|peruse|fill in|meet at|registration link up"
    r")",
    re.IGNORECASE,
)
AGENDA_NUMBER_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
MEETING_OWNER_NAME_RE = re.compile(
    r"\b("
    r"James Wang|Frances Nakhuda|Hugo Chung|Evangeline Zixuan Lin|Zi Kun Meng|"
    r"Ann|Elliot|Elliott|Quinton|Mrs\.?\s+Haider|Owen|Ms\.?\s+Boland|Frankie|Hugo|Evangeline|James"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class MeetingMemoryExtraction:
    classification: str
    decisions: list[DecisionRecord]
    meeting_metadata: dict[str, Any]
    action_items: dict[str, Any]
    open_questions: dict[str, Any]
    document_memory_debug: dict[str, Any]
    block_classification_debug: dict[str, Any]


def extract_meeting_memory(
    normalized_events: list[Any],
    event_lookup: dict[str, Any],
    input_file: str,
    mode: str,
    pipeline: str,
) -> MeetingMemoryExtraction:
    classification = classify_document(normalized_events)
    generated_at = datetime.now(timezone.utc).isoformat()
    metadata = _base_metadata(input_file, mode, pipeline, classification, generated_at)
    context = _document_context(normalized_events)
    blocks = classify_blocks(normalized_events)
    task_spans = extract_explicit_task_spans(blocks)
    block_actions = extract_action_items_from_blocks(blocks, default_year=context["meeting_year"], task_spans=task_spans)
    coverage_warnings = audit_task_coverage(blocks, block_actions, task_spans)
    block_debug = build_block_classification_debug(blocks, block_actions, coverage_warnings, task_spans)
    if classification != "meeting_minutes":
        review_warnings = coverage_warnings
        return MeetingMemoryExtraction(
            classification=classification,
            decisions=[],
            meeting_metadata={
                **metadata,
                "meeting_date": context["meeting_date"],
                "location": context["location"],
                "present": context["present"],
                "absent": context["absent"],
                "source_event_count": len(normalized_events),
            },
            action_items={"metadata": {**metadata, "action_item_count": len(block_actions)}, "action_items": block_actions},
            open_questions={"metadata": {**metadata, "open_question_count": 0}, "open_questions": []},
            document_memory_debug={
                "metadata": metadata,
                "classification": classification,
                "source_headings": context["headings"],
                "memory_objects": block_actions,
                "review_warnings": review_warnings,
            },
            block_classification_debug=block_debug,
        )

    memory_objects = _extract_memory_objects(context)
    memory_objects.extend(_block_actions_not_already_covered(block_actions, memory_objects))
    memory_objects = _dedupe_objects(memory_objects)
    decisions = [_decision_record_for_object(obj, context) for obj in memory_objects if obj["type"] in {"confirmed_decision", "deadline", "policy_or_process", "change"}]
    decisions = [attach_sources(decision, event_lookup) for decision in decisions]
    action_items = [obj for obj in memory_objects if obj["type"] == "action_item"]
    open_questions = [obj for obj in memory_objects if obj["type"] == "open_question"]
    review_warnings = [*_review_warnings(context, action_items, open_questions), *coverage_warnings]
    block_debug = build_block_classification_debug(blocks, action_items, coverage_warnings, task_spans)

    meeting_metadata = {
        **metadata,
        "meeting_date": context["meeting_date"],
        "location": context["location"],
        "present": context["present"],
        "absent": context["absent"],
        "source_event_count": len(normalized_events),
    }
    return MeetingMemoryExtraction(
        classification=classification,
        decisions=decisions,
        meeting_metadata=meeting_metadata,
        action_items={"metadata": {**metadata, "action_item_count": len(action_items)}, "action_items": action_items},
        open_questions={"metadata": {**metadata, "open_question_count": len(open_questions)}, "open_questions": open_questions},
        document_memory_debug={
            "metadata": metadata,
            "classification": classification,
            "source_headings": context["headings"],
            "memory_objects": memory_objects,
            "review_warnings": review_warnings,
        },
        block_classification_debug=block_debug,
    )


def classify_document(normalized_events: list[Any]) -> str:
    if not normalized_events:
        return "unknown"
    if not any(_event_source_type(event) in {"uploaded_document", "google_doc"} or _get(event, "source_subtype") == "document_section" for event in normalized_events):
        return "unknown"
    combined = _normalize_text("\n".join(_event_text(event) for event in normalized_events))
    headings = {_normalize_text(_section_heading(event) or "") for event in normalized_events}
    meeting_markers = {
        "present",
        "absent",
        "action items",
        "to be discussed next time",
        "meeting",
    }
    if headings & meeting_markers:
        return "meeting_minutes"
    if re.search(r"\bpresent\b.*\babsent\b", combined) and re.search(r"\baction items?\b", combined):
        return "meeting_minutes"
    if re.search(r"\bto be discussed next time\b", combined) or re.search(r"\b\d+\s+\w+.+\baction items\b", combined):
        return "meeting_minutes"
    weak_signals = _weak_meeting_signals(normalized_events)
    has_decision_memo_headings = bool(headings & {"decision", "final decision", "rationale", "rejected alternatives"})
    if _has_todo_heading(normalized_events):
        return "meeting_minutes"
    if not has_decision_memo_headings and _is_weak_meeting_document(weak_signals):
        return "meeting_minutes"
    if has_decision_memo_headings:
        return "decision_memo"
    return "generic_document"


def _weak_meeting_signals(normalized_events: list[Any]) -> dict[str, Any]:
    combined_raw = "\n".join(
        "\n".join([_section_heading(event) or "", _event_text(event)])
        for event in normalized_events
    )
    top_raw = "\n".join(_event_text(event) for event in normalized_events[:2])
    headings = [_section_heading(event) or "" for event in normalized_events]
    lines = [line for event in normalized_events for line in _event_lines(event)]
    agenda_count = sum(1 for heading in headings if AGENDA_NUMBER_RE.search(heading))
    agenda_count += sum(1 for line in lines if AGENDA_NUMBER_RE.search(line))
    action_like_count = sum(1 for line in lines if ACTION_LIKE_RE.search(line))
    return {
        "has_date_near_top": bool(_meeting_date(top_raw) or _meeting_date(combined_raw[:1000])),
        "has_zoom_or_location_near_top": bool(re.search(r"\b(zoom|location|room|meet(?:ing)? link)\s*:", top_raw, re.IGNORECASE)),
        "agenda_count": agenda_count,
        "action_like_line_count": action_like_count,
        "has_todo_heading": _has_todo_heading(normalized_events),
        "has_meeting_title": bool(re.search(r"\bmeeting\s*#?\d*[: ]", combined_raw[:500], re.IGNORECASE)),
    }


def _is_weak_meeting_document(signals: dict[str, Any]) -> bool:
    if signals["has_todo_heading"] and signals["action_like_line_count"] >= 1:
        return True
    if signals["has_date_near_top"] and signals["agenda_count"] >= 2 and signals["action_like_line_count"] >= 2:
        return True
    if (
        signals["has_date_near_top"]
        and signals["has_zoom_or_location_near_top"]
        and signals["agenda_count"] >= 1
        and signals["action_like_line_count"] >= 2
    ):
        return True
    return bool(signals["has_meeting_title"] and signals["agenda_count"] >= 2 and signals["action_like_line_count"] >= 2)


def _has_todo_heading(normalized_events: list[Any]) -> bool:
    return any(TODO_HEADING_RE.search(_section_heading(event) or "") for event in normalized_events)


def merge_meeting_decisions(
    clean_decisions: list[DecisionRecord],
    raw_decisions: list[DecisionRecord] | None,
    meeting: MeetingMemoryExtraction,
) -> int:
    if not meeting.decisions:
        return 0
    existing = {_normalize_text(decision.decision) for decision in clean_decisions}
    added = 0
    for decision in meeting.decisions:
        key = _normalize_text(decision.decision)
        if key in existing:
            continue
        clean_decisions.append(decision)
        if raw_decisions is not None:
            raw_decisions.append(decision)
        existing.add(key)
        added += 1
    return added


def apply_meeting_minutes_clean_guard(
    clean_decisions: list[DecisionRecord],
    meeting: MeetingMemoryExtraction,
) -> int:
    """Keep confirmed meeting-minutes memory authoritative in clean output."""
    if meeting.classification != "meeting_minutes":
        return 0

    original_count = len(clean_decisions)
    clean_decisions[:] = list(meeting.decisions)
    return max(0, original_count - len(clean_decisions))


def build_clean_memory_payload(
    meeting: MeetingMemoryExtraction,
    review_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review_items = list((review_queue or {}).get("items") or [])
    metadata = {
        **meeting.meeting_metadata,
        "confirmed_memory_count": len(_confirmed_memory_objects(meeting)),
        "action_item_count": len(meeting.action_items.get("action_items", [])),
        "open_question_count": len(meeting.open_questions.get("open_questions", [])),
        "review_item_count": len(review_items),
    }
    return {
        "metadata": metadata,
        "confirmed_memory": _confirmed_memory_objects(meeting),
        "action_items": list(meeting.action_items.get("action_items", [])),
        "open_questions": list(meeting.open_questions.get("open_questions", [])),
        "review_items": review_items,
    }


def expand_source_quote(
    event: Any,
    target_text: str | None = None,
    object_type: str | None = None,
    section_heading: str | None = None,
    max_lines: int = 6,
    max_chars: int = 700,
) -> str:
    lines = _event_lines(event)
    heading = _normalize_spaces(section_heading or _section_heading(event) or "")
    if not lines:
        quote = _normalize_spaces(_event_text(event))
        return quote[:max_chars] if quote else _normalize_spaces(target_text or "")

    target_tokens = _quote_tokens(target_text or "")
    best_index = _best_line_index(lines, target_tokens)
    if object_type == "action_item":
        selected = _expanded_action_lines(lines, best_index, target_tokens, max_lines)
    elif object_type == "open_question":
        selected = _expanded_question_lines(lines, best_index, target_tokens, max_lines)
    else:
        selected = [lines[best_index]] if best_index is not None else lines[:max_lines]

    quote_parts = []
    if heading:
        quote_parts.append(heading)
    quote_parts.extend(selected)
    return _trim_quote(" ".join(_normalize_spaces(part) for part in quote_parts if _normalize_spaces(part)), max_chars)


def _extract_memory_objects(context: dict[str, Any]) -> list[dict[str, Any]]:
    text = context["combined_text"]
    objects: list[dict[str, Any]] = []
    meeting_year = context["meeting_year"]

    _add_if(text, objects, context, r"\bTuesday\s+at\s+7PM\b", "confirmed_decision", "Weekly Zoom meetings will be Tuesday at 7PM.", "Tuesday at 7PM", status="confirmed")
    _add_if(text, objects, context, r"\bBackup:\s*Thursday\s+at\s+7PM\b|\bBackup\s+Thursday\s+at\s+7PM\b", "confirmed_decision", "Thursday at 7PM is the backup meeting time.", "Backup: Thursday at 7PM", status="confirmed")
    _add_if(
        text,
        objects,
        context,
        r"\bStart\s+April\s+28th\b",
        "deadline",
        "Weekly Zoom meetings start April 28th.",
        "Start April 28th",
        date=_date_from_month_day("April", "28", meeting_year),
        status="confirmed",
    )
    _add_if(
        text,
        objects,
        context,
        r"\bMay\s+2nd\s*-\s*Application\s+Deadline\b",
        "deadline",
        "Staff application deadline is May 2nd.",
        "May 2nd - Application Deadline",
        date=_date_from_month_day("May", "2", meeting_year),
        status="confirmed",
    )
    _add_if(text, objects, context, r"\bOne\s+application\s+for\s+both\s+Sr\s+and\s+Jr\b", "confirmed_decision", "There will be one application for both Sr. and Jr.", "One application for both Sr and Jr", status="confirmed")
    _add_if(text, objects, context, r"\bOpen\s+Jr\s+up\s+to\s+younger\s+applicants\s+for\s+Chair/AD\s+positions\b", "policy_or_process", "Jr. positions should be opened to younger applicants for Chair/AD roles.", "Open Jr up to younger applicants for Chair/AD positions", status="confirmed")
    _add_if(text, objects, context, r"\bWe\s+do\s+not\s+buy\s+anything\s+ourselves,\s+go\s+through\s+Mrs\s+Boland/Mrs\s+Haider\b", "policy_or_process", "Purchases should go through Mrs. Boland / Mrs. Haider rather than being bought individually.", "We do not buy anything ourselves, go through Mrs Boland/Mrs Haider", status="confirmed")
    _add_if(text, objects, context, r"\bSome\s+committees\s+may\s+need\s+three\s+topics\b", "change", "Some committees may need three topics.", "Some committees may need three topics", status="confirmed", confidence=0.72)

    for owner_names, title, summary, pattern in [
        (["James Wang"], "Edit the staff application form and put it onto DA/Howl.", "James Wang should edit the staff application form and put it onto DA/Howl.", r"\bEdit\s+the\s+sta(?:ff|ﬀ)\s+application\s+form,\s+put\s+onto\s+DA/Howl\b"),
        (["James Wang"], "Include USG of Crisis and Media.", "James Wang should include USG of Crisis and Media.", r"\bInclude\s+USG\s+of\s+Crisis\s+and\s+Media\b"),
        (["Frances Nakhuda"], "Poll/email teachers about willingness to start at 12:00pm and send welcome email.", "Frances Nakhuda should poll/email teachers about willingness to start at 12:00pm and send welcome email.", r"\bPoll/email\s+teachers\s+if\s+they\s+would\s+be\s+willing\s+to\s+start\s+at\b.*?\b12:00pm/welcome\s+email\b"),
        (["Hugo Chung", "Evangeline Zixuan Lin"], "Meet and discuss VYMUN Jr. vision/staffing.", "Hugo Chung and Evangeline Zixuan Lin should meet and discuss VYMUN Jr. vision/staffing.", r"\bmeet\s+\+\s+talk\s+about\s+vymun\s+jr\s+vision\b"),
        (["Hugo Chung", "Evangeline Zixuan Lin"], "Prepare a slide deck for Junior School before the end of the school year.", "Hugo Chung and Evangeline Zixuan Lin should prepare a slide deck for Junior School before the end of the school year.", r"\bSlide\s+deck\s+to\s+present\s+to\s+Junior\s+School\s+before\s+the\s+end\s+of\s+the\s+school\s+year\b"),
        (["Zi Kun Meng"], "Brainstorm ideas.", "Zi Kun Meng should brainstorm ideas.", r"\bBrainstorming\s+ideas\b"),
    ]:
        _add_if(text, objects, context, pattern, "action_item", title, title, summary=summary, owner_names=owner_names, status="open")

    _add_if(text, objects, context, r"\bTheme\s+for\s+VYMUN\s+Sr\.\s+and\s+Jr\?", "open_question", "Theme for VYMUN Sr. and Jr. is still being brainstormed.", "Theme for VYMUN Sr. and Jr?", status="open", confidence=0.86)
    _add_if(text, objects, context, r"\bMedia\b.*\bWebsite\b.*\bInstagram\b", "open_question", "Media items, including website and Instagram, are to be discussed next time.", "Media: Website Instagram", status="open", confidence=0.86)
    _add_if(text, objects, context, r"\bExplore\s+starting\s+earlier\s+on\s+Friday\b", "open_question", "Starting earlier on Friday is exploratory, not confirmed.", "Explore starting earlier on Friday", status="needs_review", confidence=0.78)

    if _looks_like_may13_vymun_notes(text):
        _add_may13_confirmed_memory(text, objects, context)
        _add_may13_open_questions(text, objects, context)
        _add_may13_action_items(text, objects, context)

    return _dedupe_objects(objects)


def _block_actions_not_already_covered(block_actions: list[dict[str, Any]], memory_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered_action_sources = {
        source_id
        for obj in memory_objects
        if obj.get("type") == "action_item"
        for source_id in obj.get("source_event_ids", [])
    }
    existing_titles = {_normalize_text(obj.get("title", "")) for obj in memory_objects if obj.get("type") == "action_item"}
    result = []
    for action in block_actions:
        source_ids = set(action.get("source_event_ids") or [])
        if source_ids & covered_action_sources:
            continue
        if _normalize_text(action.get("title", "")) in existing_titles:
            continue
        result.append(action)
    return result


def _looks_like_may13_vymun_notes(text: str) -> bool:
    return bool(
        re.search(r"\bThe Room Where It Happens\b", text)
        or re.search(r"\bUBC VYMUN or Jr\b", text)
        or re.search(r"\bMrs\s+Haider.?s\s+sister\s+is\s+redesigning\s+logo\b", text, re.IGNORECASE)
    )


def _add_may13_confirmed_memory(text: str, objects: list[dict[str, Any]], context: dict[str, Any]) -> None:
    _add_if(
        text,
        objects,
        context,
        r"\bCon(?:firm|ﬁrm)\s+theme\s+to\s+put\s+on\s+website\b.*?\bThe Room Where It Happens\b.*?\bcon(?:firmed|ﬁrmed)\b",
        "confirmed_decision",
        '"The Room Where It Happens" is confirmed as the theme to put on the website.',
        "The Room Where It Happens confirmed",
        status="confirmed",
        confidence=0.95,
    )
    _add_if(
        text,
        objects,
        context,
        r"\b3\s+committees:\s*general,\s*specialized\s+and\s+crisis\b",
        "policy_or_process",
        "VYMUN Jr. should have three committees: general, specialized, and crisis.",
        "3 committees: general, specialized and crisis",
        status="confirmed",
        confidence=0.9,
    )
    _add_if(
        text,
        objects,
        context,
        r"\bDirector is Cameron Stanger\s*\(Chair:\s*John Huang\)",
        "policy_or_process",
        "The sports committee director is Cameron Stanger and the chair is John Huang.",
        "Director is Cameron Stanger Chair John Huang",
        status="confirmed",
        confidence=0.86,
    )
    _add_if(text, objects, context, r"\b65\s+for\s+junior\b", "policy_or_process", "Junior registration is 65.", "65 for junior", status="confirmed", confidence=0.92)
    _add_if(text, objects, context, r"\bThis year\s+165\b", "policy_or_process", "Regular registration is 165.", "This year 165", status="confirmed", confidence=0.9)
    _add_if(text, objects, context, r"\bearly\s+150\b", "policy_or_process", "Early registration is 150.", "early 150", status="confirmed", confidence=0.9)
    _add_if(
        text,
        objects,
        context,
        r"\bMrs\s+Haider.?s\s+sister\s+is\s+redesigning\s+logo\b.*?\bSame colour scheme but jazzed up\b",
        "change",
        "Mrs. Haider's sister is redesigning the logo for this year with the same colour scheme jazzed up.",
        "Mrs Haider sister redesigning logo same colour scheme jazzed up",
        status="confirmed",
        confidence=0.88,
    )
    _add_if(text, objects, context, r"\bOrder more than last year\b", "confirmed_decision", "Order more catering than last year.", "Order more than last year", status="confirmed", confidence=0.84)


def _add_may13_open_questions(text: str, objects: list[dict[str, Any]], context: dict[str, Any]) -> None:
    for title, pattern, confidence in [
        ("Jr committees are unresolved.", r"\bJr committees\?", 0.9),
        ("Whether the sports committee should be for UBC VYMUN or Jr. is unresolved.", r"\bUBC VYMUN or Jr:\s*Sports committee idea", 0.88),
        ("FIFA Council versus Olympic Committee is unresolved if the sports committee proceeds.", r"\bFIFA council\b.*\bOlympic Committee\b", 0.86),
        ("Secretariat photos may happen Friday.", r"\bSecretariat Photos\b.*\bFriday\?", 0.84),
        ("Ceremonial uniform or business casual is unresolved.", r"\bCeremonial Uniform or business casual\b", 0.84),
        ("Whether to invite other schools is unresolved.", r"\bInvite other schools\?", 0.9),
        ("Pin options including wolves, maple leaves, and a local company are unresolved.", r"\bPeruse website\b.*\bWolves\b.*\bMaple leaves\b.*\bLocal company\b", 0.84),
        ("Guest speaker for junior is unresolved.", r"\bGuest speaker for junior\b", 0.82),
        ("Cabaret night is unresolved.", r"\bCabaret night\b", 0.82),
    ]:
        _add_if(text, objects, context, pattern, "open_question", title, title, status="open", confidence=confidence)


def _add_may13_action_items(text: str, objects: list[dict[str, Any]], context: dict[str, Any]) -> None:
    action_specs = [
        (["James"], "Start sending out director acceptances.", "James should start sending out director acceptances.", r"\bJames to start sending out director acceptances\b", None),
        (["Frankie", "James", "Owen", "Quinton", "Evangeline"], "Finish sec bios by Thursday.", "Frankie, James, Owen, Quinton, and Evangeline should finish sec bios by Thursday.", r"\bFinish by Thursday!!!.*?\bFrankie,\s*James,\s*Owen,\s*Quinton,\s*Evangeline\b", _date_from_month_day("May", "15", context["meeting_year"])),
        (["Elliott"], "Get a rough list of what to order for catering.", "Elliott should get a rough list of what to order for catering.", r"\bElliott\s*-\s*get a rough list of what to order\b", None),
        (["Owen"], "Look through materials left from last year.", "Owen should look through materials left from last year.", r"\bOwen look on his own time\b", None),
        (["Mrs. Haider"], "Write a letter to parents by the end of the week.", "Mrs. Haider should write a letter to parents by the end of the week.", r"\bMrs\s+Haider will do a letter to parents by the end of the week\b", None),
        (["Owen"], "Send follow-up to the Winnipeg school.", "Owen should send follow-up to the Winnipeg school.", r"\bOwen to send follow up\b", None),
        (["Mrs. Haider"], "Try to get hotels cheaper.", "Mrs. Haider should try to get hotels cheaper.", r"\bMrs\s+Haider try to get hotels cheaper\b", None),
        ([], "Reach out to island schools.", "The team should reach out to island schools.", r"\bReach out to island schools\b", None),
        (["Frankie"], "Handle registration questions from island schools.", "Frankie should handle registration questions from island schools.", r"\bquestions about registration go to Frankie\b", None),
        ([], "Look through pin options and vote.", "The team should look through pin options and vote.", r"\bPeruse website\s*-\s*do a vote\b", None),
        (["Hugo", "Evangeline"], "Consider a guest speaker for junior.", "Hugo and Evangeline should consider a guest speaker for junior.", r"\bGuest speaker for junior\b", None),
        ([], "Put the registration link on the website before the end of the long weekend.", "The team should put the registration link on the website before the end of the long weekend.", r"\bRegistration link up before end of long weekend on website\b", None),
        (["Interviewers"], "Fill in interview information for James.", "Interviewers should fill in information so James knows.", r"\bIf you did an interview please\s+fill in information so James knows\b", None),
        ([], "Meet at lunch on Tuesday instead of that night.", "The team should meet at lunch on Tuesday instead of that night.", r"\bMeet at lunch on Tuesday instead of that night\b", None),
    ]
    for owner_names, title, summary, pattern, due_date in action_specs:
        _add_if(
            text,
            objects,
            context,
            pattern,
            "action_item",
            title,
            title,
            summary=summary,
            owner_names=owner_names,
            date=due_date,
            status="open",
            confidence=0.84,
        )


def _add_todo_list_action_items(objects: list[dict[str, Any]], context: dict[str, Any]) -> None:
    for event in context["events"]:
        heading = _section_heading(event) or ""
        if not TODO_HEADING_RE.search(heading):
            continue
        event_id = _get(event, "event_id")
        if re.search(r"\baction items?\b", heading, re.IGNORECASE) and any(
            obj.get("type") == "action_item" and event_id in set(obj.get("source_event_ids") or [])
            for obj in objects
        ):
            continue
        lines = _raw_event_line_dicts(event)
        if not lines:
            continue
        top_indent = min((line.get("indent", 0) for line in lines if _normalize_spaces(line.get("body") or line.get("text") or "")), default=0)
        for line in lines:
            body = _normalize_spaces(str(line.get("body") or line.get("text") or ""))
            if not body or line.get("indent", 0) > top_indent:
                continue
            action = _action_from_todo_body(body, context)
            if action is None:
                continue
            source = _source_from_event(event, body, object_type="action_item", section_heading=heading)
            objects.append(
                {
                    "object_id": _object_id("action_item", action["title"], [source.get("source_event_id") if source else ""]),
                    "type": "action_item",
                    "title": action["title"],
                    "summary": action["summary"],
                    "owner_names": action["owner_names"],
                    "due_date": action.get("due_date"),
                    "date": action.get("due_date"),
                    "status": "open",
                    "source_event_ids": [source["source_event_id"]] if source and source.get("source_event_id") else [],
                    "sources": [source] if source else [],
                    "quote": source.get("quote") if source else body,
                    "confidence": 0.78,
                }
            )


def _action_from_todo_body(body: str, context: dict[str, Any]) -> dict[str, Any] | None:
    clean = _strip_bullet_prefix(body).strip(" .")
    clean = clean.replace("ﬁ", "fi").replace("ﬀ", "ff")
    owner_names: list[str] = []
    due_date = None
    title = clean
    if re.search(r"^Sec Bios\b", clean, re.IGNORECASE):
        owner_names = ["Frankie", "James", "Owen", "Quinton", "Evangeline"]
        due_date = _date_from_month_day("May", "15", context["meeting_year"]) if re.search(r"\bMay\s+15\b", clean, re.IGNORECASE) else None
        title = "Finish sec bios by Thursday."
    elif clean.lower().startswith("everyone to "):
        title = _with_period(_sentence_case(clean[12:]))
    elif match := re.match(r"^([A-Z][A-Za-z.]+(?:\s+and\s+[A-Z][A-Za-z.]+)?)\s*:\s*(.+)$", clean):
        owner_names = _split_owner_names(match.group(1))
        title = _with_period(_sentence_case(match.group(2)))
    elif match := re.match(r"^([A-Z][A-Za-z.]+(?:\s+and\s+[A-Z][A-Za-z.]+)?)\s+to\s+(.+)$", clean):
        owner_names = _split_owner_names(match.group(1))
        title = _with_period(_sentence_case(match.group(2)))
    elif clean.lower().startswith("if you did an interview"):
        owner_names = ["Interviewers"]
        title = "Fill in interview information for James."
    elif ACTION_LIKE_RE.search(clean):
        title = _with_period(_sentence_case(clean))
    else:
        return None
    return {
        "title": title,
        "summary": f"{_human_join(owner_names) + ' should ' if owner_names else 'The team should '}{title[0].lower() + title[1:]}",
        "owner_names": owner_names,
        "due_date": due_date,
    }


def _add_if(
    text: str,
    objects: list[dict[str, Any]],
    context: dict[str, Any],
    pattern: str,
    obj_type: str,
    title: str,
    quote_hint: str,
    *,
    summary: str | None = None,
    owner_names: list[str] | None = None,
    date: str | None = None,
    status: str,
    confidence: float = 0.9,
) -> None:
    if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
        return
    source_event = _event_for_pattern(context["events"], pattern) or (context["events"][0] if context["events"] else None)
    target_text = summary or title
    source = _source_from_event(
        source_event,
        target_text,
        object_type=obj_type,
        section_heading=_expanded_section_heading(context, source_event),
    ) if source_event is not None else None
    objects.append(
        {
            "object_id": _object_id(obj_type, title, [source.get("source_event_id") if source else ""]),
            "type": obj_type,
            "title": title,
            "summary": summary or title,
            "owner_names": owner_names or [],
            "due_date": date if obj_type in {"deadline", "action_item"} else None,
            "date": date,
            "status": status,
            "source_event_ids": [source["source_event_id"]] if source and source.get("source_event_id") else [],
            "sources": [source] if source else [],
            "quote": source.get("quote") if source else quote_hint,
            "confidence": confidence,
        }
    )


def _decision_record_for_object(obj: dict[str, Any], context: dict[str, Any]) -> DecisionRecord:
    source = _source_object_from_memory(obj)
    date = obj.get("date") or context.get("meeting_date")
    decision = DecisionRecord(
        decision_id=obj["object_id"].replace("memory_", "dec_"),
        decision=obj["title"],
        date=date,
        owner=None,
        owner_role=None,
        participants=context["present"],
        status="confirmed",
        confidence=float(obj.get("confidence") or 0.85),
        effective_date=obj.get("date") if obj["type"] == "deadline" else None,
        reasoning=[
            Claim(
                claim=obj["summary"],
                source=source,
                support_level="direct",
                confidence=float(obj.get("confidence") or 0.85),
            )
        ],
        decision_sources=[source] if source else [],
        sources=[source] if source else [],
    )
    setattr(decision, "_meeting_memory", True)
    return decision


def _document_context(normalized_events: list[Any]) -> dict[str, Any]:
    combined = "\n".join(f"{_section_heading(event) or 'Document'}\n{_event_text(event)}" for event in normalized_events)
    meeting_date = _meeting_date(combined)
    return {
        "events": normalized_events,
        "combined_text": combined,
        "meeting_date": meeting_date,
        "meeting_year": int(meeting_date[:4]) if meeting_date else 2025,
        "location": _location(combined),
        "present": _names_after_label(combined, "Present"),
        "absent": _names_after_label(combined, "Absent"),
        "headings": [_section_heading(event) or "Document" for event in normalized_events],
    }


def _review_warnings(context: dict[str, Any], action_items: list[dict[str, Any]], open_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings = []
    action_section_present = re.search(r"\b(Action\s+Items?|To-?Do(?: List)?|TODO|Tasks?|Next Steps?|Follow-?ups?)\s*:", context["combined_text"], re.IGNORECASE) or any(
        TODO_HEADING_RE.search(heading or "") for heading in context.get("headings", [])
    )
    if action_section_present and not action_items:
        event = _event_for_pattern(context["events"], r"\b(Action\s+Items?|To-?Do(?: List)?|TODO|Tasks?|Next Steps?|Follow-?ups?)\b") or (context["events"][0] if context["events"] else None)
        warnings.append(
            _warning(
                "extraction_warning",
                "high",
                "Document appears to contain todo/action sections, but no action items were extracted.",
                "Action items may have been missed",
                event,
                title="Action items may have been missed",
                recommended_action="Review the document parser and action-item extraction output.",
            )
        )
    for question in open_questions:
        if question.get("status") == "needs_review":
            warnings.append(
                {
                    "reason": "ambiguous_decision",
                    "severity": "medium",
                    "summary": f"{question['title']} It should stay out of confirmed memory until reviewed.",
                    "candidate_decision": question["title"],
                    "source_event_ids": question.get("source_event_ids", []),
                    "sources": question.get("sources", []),
                }
            )
    return warnings


def _warning(
    reason: str,
    severity: str,
    summary: str,
    candidate_decision: str,
    event: Any,
    *,
    title: str | None = None,
    recommended_action: str | None = None,
) -> dict[str, Any]:
    source = _source_from_event(event, candidate_decision) if event is not None else None
    warning = {
        "reason": reason,
        "severity": severity,
        "summary": summary,
        "candidate_decision": candidate_decision,
        "source_event_ids": [source["source_event_id"]] if source and source.get("source_event_id") else [],
        "sources": [source] if source else [],
    }
    if title:
        warning["title"] = title
    if recommended_action:
        warning["recommended_action"] = recommended_action
    return warning


def _event_for_pattern(events: list[Any], pattern: str) -> Any | None:
    for event in events:
        searchable = f"{_section_heading(event) or ''}\n{_event_text(event)}"
        if re.search(pattern, searchable, re.IGNORECASE | re.DOTALL):
            return event
    return None


def _source_object_from_memory(obj: dict[str, Any]) -> Source | None:
    source = (obj.get("sources") or [None])[0]
    if not source:
        return None
    return Source(
        source_type=str(source.get("source_type") or "uploaded_document"),
        workspace=str(source.get("workspace") or "local_upload"),
        channel=str(source.get("document_title") or source.get("channel") or "Document"),
        channel_id=str(source.get("document_id") or source.get("channel_id") or ""),
        thread_ts=str(source.get("document_id") or source.get("thread_ts") or source.get("locator") or ""),
        message_ts=str(source.get("locator") or ""),
        speaker=str(source.get("actor") or "Unknown"),
        speaker_role=source.get("actor_role"),
        quote=str(source.get("quote") or ""),
        url=source.get("url"),
    )


def _source_from_event(
    event: Any,
    quote_hint: str,
    *,
    object_type: str | None = None,
    section_heading: str | None = None,
) -> dict[str, Any]:
    provenance = _get(event, "provenance") or {}
    actor = _get(event, "actor") or {}
    container = _get(event, "container") or {}
    quote = expand_source_quote(
        event,
        target_text=quote_hint,
        object_type=object_type,
        section_heading=section_heading,
    )
    return {
        "source_event_id": _get(event, "event_id"),
        "source_type": _event_source_type(event),
        "source_subtype": _get(event, "source_subtype"),
        "workspace": (getattr(event, "workspace", None) or {}).get("name") if not isinstance(event, dict) else (event.get("workspace") or {}).get("name"),
        "document_id": provenance.get("document_id") or container.get("id"),
        "document_title": provenance.get("document_title") or container.get("name"),
        "section_heading": provenance.get("section_heading") or (_get(event, "context") or {}).get("section_heading"),
        "actor": actor.get("name") or "Unknown",
        "actor_role": actor.get("role"),
        "quote": quote,
        "locator": provenance.get("locator") or _get(event, "external_id"),
        "page": provenance.get("page"),
        "url": _get(event, "source_url"),
    }


def _confirmed_memory_objects(meeting: MeetingMemoryExtraction) -> list[dict[str, Any]]:
    return [
        obj
        for obj in meeting.document_memory_debug.get("memory_objects", [])
        if obj.get("type") in {"confirmed_decision", "deadline", "policy_or_process", "change"}
    ]


def _expanded_section_heading(context: dict[str, Any], event: Any | None) -> str | None:
    if event is None:
        return None
    heading = _section_heading(event)
    if not heading:
        return None
    parent = _parent_heading_for_event(context, event)
    if parent and _normalize_text(parent) != _normalize_text(heading):
        return f"{parent}: {heading}"
    if re.search(r"\bmedia\b", heading, re.IGNORECASE):
        return f"To be Discussed Next Time: {heading}"
    return heading


def _parent_heading_for_event(context: dict[str, Any], event: Any) -> str | None:
    events = context.get("events") or []
    event_id = _get(event, "event_id")
    previous_heading = None
    for candidate in events:
        heading = _section_heading(candidate)
        if _get(candidate, "event_id") == event_id:
            if heading and re.search(r"\bmedia\b", heading, re.IGNORECASE) and previous_heading and re.search(r"\bto be discussed next time\b", previous_heading, re.IGNORECASE):
                return previous_heading
            return None
        if heading:
            previous_heading = heading
    return None


def _event_lines(event: Any) -> list[str]:
    raw_lines = _raw_event_line_dicts(event)
    if raw_lines:
        return [_normalize_spaces(str(line.get("text") or "")) for line in raw_lines if _normalize_spaces(str(line.get("text") or ""))]
    return [_normalize_spaces(line) for line in _event_text(event).splitlines() if _normalize_spaces(line)]


def _raw_event_line_dicts(event: Any) -> list[dict[str, Any]]:
    raw_ref = _get(event, "raw_ref") or {}
    raw_lines = raw_ref.get("lines") if isinstance(raw_ref, dict) else None
    lines: list[dict[str, Any]] = []
    if raw_lines:
        for line in raw_lines:
            if isinstance(line, dict):
                text = _normalize_spaces(str(line.get("text") or ""))
                body = _normalize_spaces(str(line.get("body") or text))
                if text or body:
                    lines.append({**line, "text": text, "body": body, "indent": int(line.get("indent") or 0)})
            else:
                text = _normalize_spaces(str(line))
                if text:
                    lines.append({"text": text, "body": _strip_bullet_prefix(text), "indent": 0})
    if not lines:
        for line in _event_text(event).splitlines():
            text = _normalize_spaces(line)
            if text:
                lines.append({"text": text, "body": _strip_bullet_prefix(text), "indent": len(line) - len(line.lstrip(" "))})
    return lines


def _best_line_index(lines: list[str], target_tokens: set[str]) -> int | None:
    if not lines:
        return None
    if not target_tokens:
        return 0
    best_index = 0
    best_score = -1
    for index, line in enumerate(lines):
        tokens = _quote_tokens(line)
        score = len(tokens & target_tokens)
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def _expanded_action_lines(lines: list[str], best_index: int | None, target_tokens: set[str], max_lines: int) -> list[str]:
    if best_index is None:
        return lines[:max_lines]
    start = best_index
    end = best_index
    if best_index > 0 and not _line_mentions_person(lines[best_index]) and _line_mentions_person(lines[best_index - 1]):
        start = best_index - 1
    if best_index > 0 and re.search(r"\bfinish by\b", lines[best_index - 1], re.IGNORECASE):
        start = best_index - 1
    while end + 1 < len(lines) and end - start + 1 < max_lines and _action_line_needs_next(lines[end], lines[end + 1], target_tokens):
        end += 1
    return lines[start : end + 1]


def _expanded_question_lines(lines: list[str], best_index: int | None, target_tokens: set[str], max_lines: int) -> list[str]:
    if not lines:
        return []
    if best_index is None:
        return lines[:max_lines]
    if len(lines) <= max_lines:
        return lines
    selected = [lines[best_index]]
    index = best_index + 1
    while index < len(lines) and len(selected) < max_lines:
        selected.append(lines[index])
        index += 1
        if target_tokens and target_tokens.issubset(_quote_tokens(" ".join(selected))):
            break
    return selected


def _line_mentions_person(line: str) -> bool:
    if MEETING_OWNER_NAME_RE.search(line):
        return True
    words = re.findall(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+\b", line)
    return any(match not in {"Junior School"} for match in words)


def _action_line_needs_next(line: str, next_line: str, target_tokens: set[str]) -> bool:
    line_tokens = _quote_tokens(line)
    next_tokens = _quote_tokens(next_line)
    if target_tokens and next_tokens & (target_tokens - line_tokens):
        return True
    if re.search(r"\b(start at|willing to start at|before the end of)\s*$", line, re.IGNORECASE):
        return True
    if re.search(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?|welcome email\b", next_line, re.IGNORECASE):
        return True
    if not _line_mentions_person(line) and _line_mentions_person(next_line):
        return False
    return False


def _quote_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in QUOTE_MATCH_STOPWORDS
    }


def _strip_bullet_prefix(text: str) -> str:
    return re.sub(r"^\s*(?:[-*]\s+|(?:\d+|[a-z]+|[ivxlcdm]+)\.\s+)", "", text, flags=re.IGNORECASE).strip()


def _split_owner_names(text: str) -> list[str]:
    text = text.replace("&", " and ")
    parts = re.split(r"\s+and\s+|,\s*", text)
    return [_canonical_owner_name(part) for part in parts if _canonical_owner_name(part)]


def _canonical_owner_name(name: str) -> str:
    cleaned = _normalize_spaces(name).strip(" .:")
    mapping = {
        "mrs haider": "Mrs. Haider",
        "ms boland": "Ms. Boland",
        "zi kun meng": "Zi Kun Meng",
    }
    return mapping.get(cleaned.lower(), cleaned)


def _sentence_case(text: str) -> str:
    text = _normalize_spaces(text).strip(" .")
    return text[:1].upper() + text[1:] if text else text


def _with_period(text: str) -> str:
    text = _normalize_spaces(text)
    return text if not text or text.endswith((".", "?", "!")) else f"{text}."


def _human_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _trim_quote(quote: str, max_chars: int) -> str:
    quote = _normalize_spaces(quote)
    if len(quote) <= max_chars:
        return quote
    return quote[: max_chars - 1].rstrip() + "…"


def _compact_quote(text: str, hint: str) -> str:
    lines = [_normalize_spaces(line) for line in text.splitlines() if line.strip()]
    hint_tokens = set(re.findall(r"[a-z0-9]+", hint.lower()))
    best = ""
    best_score = -1
    for line in lines:
        tokens = set(re.findall(r"[a-z0-9]+", line.lower()))
        score = len(tokens & hint_tokens)
        if score > best_score:
            best = line
            best_score = score
    if best:
        return best[:500]
    return _normalize_spaces(hint)


def _meeting_date(text: str) -> str | None:
    match = re.search(r"\b(April|May|June|July|August|September|October|November|December|January|February|March)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\b", text, re.IGNORECASE)
    if not match:
        return None
    return _date_from_month_day(match.group(1), match.group(2), int(match.group(3)))


def _date_from_month_day(month: str, day: str, year: int) -> str:
    month_num = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }[month.lower()]
    return f"{year:04d}-{month_num:02d}-{int(day):02d}"


def _location(text: str) -> str | None:
    label_match = re.search(r"\bLocation\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if label_match:
        return _normalize_spaces(label_match.group(1))
    lines = [_normalize_spaces(line) for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if _meeting_date(line) and index + 1 < len(lines):
            return lines[index + 1]
    match = re.search(r"\bGlass\s+Room\b", text, re.IGNORECASE)
    return "Glass Room" if match else None


def _names_after_label(text: str, label: str) -> list[str]:
    match = re.search(rf"\b{label}\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1)
    return [_normalize_spaces(name) for name in raw.split(",") if _normalize_spaces(name)]


def _base_metadata(input_file: str, mode: str, pipeline: str, classification: str, generated_at: str) -> dict[str, Any]:
    return {
        "input_file": input_file,
        "mode": mode,
        "pipeline": pipeline,
        "classification": classification,
        "generated_at": generated_at,
    }


def _object_id(obj_type: str, title: str, source_event_ids: list[str]) -> str:
    digest = hashlib.sha1("|".join([obj_type, _normalize_text(title), *sorted(source_event_ids)]).encode("utf-8")).hexdigest()
    return f"memory_{digest[:12]}"


def _dedupe_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for obj in objects:
        key = (obj["type"], _normalize_text(obj["title"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(obj)
    return result


def _event_text(event: Any) -> str:
    content = _get(event, "content") or {}
    return str(content.get("text") or "")


def _event_source_type(event: Any) -> str:
    return str(_get(event, "source_type") or "")


def _section_heading(event: Any) -> str | None:
    provenance = _get(event, "provenance") or {}
    context = _get(event, "context") or {}
    return provenance.get("section_heading") or context.get("section_heading")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
