from __future__ import annotations

import hashlib
import re
from typing import Any


MONTHS = {
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
}
POLICY_BLOCK_TYPES = {"policy_or_process", "template_or_example", "reference_links", "open_questions"}
OWNER_PATTERN = r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?"
TASK_SPAN_HEADING_RE = re.compile(
    r"^\s*(to\s*do|to-?do(?:\s+list)?|todo|tasks?|action items?|next steps?|follow-?ups?|this week's tasks|individual tasks)(?:\s*\([^)]+\))?\s*:?\s*$",
    re.IGNORECASE,
)
ACTION_VERB_RE = re.compile(
    r"\b(send|ask|contact|reach out|follow up|finalize|update|write|order|book|arrange|prepare|print|email|share|assign|find|call|connect|confirm|upload|create|make|schedule|review|assemble|coordinate|invite|message|get|record|open|take|look|finish|reorganize)\b",
    re.IGNORECASE,
)
SUSPICIOUS_OWNERS = {"Need", "SJR", "UBC", "VYMUN", "Document", "Staff", "Supplies", "Invitation"}


def extract_explicit_task_spans(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    def close_active() -> None:
        nonlocal active
        if active is not None:
            _finalize_span(active)
            if active["lines"]:
                spans.append(active)
            active = None

    for block in blocks:
        lines = block.get("lines") or []
        block_heading = str(block.get("heading") or "")
        if TASK_SPAN_HEADING_RE.match(block_heading):
            close_active()
            active = _new_span(block, block_heading, -1)
        elif active is not None and block_heading not in {"", "Document"} and _is_major_new_section(block_heading):
            close_active()

        if (
            active is not None
            and block_heading not in {"", "Document"}
            and not TASK_SPAN_HEADING_RE.match(block_heading)
            and _heading_should_continue_task_span(block_heading)
        ):
            active["lines"].append(_span_heading_line(block, block_heading))

        for index, line in enumerate(lines):
            body = _line_body(line)
            if TASK_SPAN_HEADING_RE.match(body):
                close_active()
                active = _new_span(block, body, index)
                continue
            if active is None:
                continue
            if _is_major_new_section(body):
                close_active()
                continue
            active["lines"].append(_span_line(block, index, line))
    close_active()
    return spans


def extract_action_items_from_blocks(
    blocks: list[dict[str, Any]],
    default_year: int | None = None,
    task_spans: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    spans = task_spans if task_spans is not None else extract_explicit_task_spans(blocks)
    span_block_ids = {line.get("block_id") for span in spans for line in span.get("lines", [])}
    for span in spans:
        for row_index, row in enumerate(_task_rows_from_span(span)):
            parsed = parse_task_line(row["body"], default_year=default_year, explicit_span=True)
            if parsed is None:
                continue
            quote = _trim_quote(" ".join([str(span.get("heading") or ""), row["quote"]]), 700)
            actions.append(_action_item_from_parsed(parsed, row["block"], row["line_index"], span=span, quote_text=quote))
    for block in blocks:
        if block.get("block_type") in POLICY_BLOCK_TYPES:
            continue
        if block.get("block_id") in span_block_ids and block.get("block_type") in {"task_section", "task_table"}:
            continue
        if block.get("block_type") not in {"task_section", "task_table", "agenda_section"}:
            continue
        for index, line in enumerate(block.get("lines") or []):
            body = _normalize_spaces(str(line.get("body") or line.get("text") or ""))
            if not body or _is_child_line_without_task(block, line):
                continue
            parsed = parse_task_line(body, default_year=default_year, explicit_span=False)
            if parsed is None:
                continue
            action = _action_item_from_parsed(parsed, block, index)
            actions.append(action)
    return _dedupe_actions(actions)


def parse_task_line(text: str, default_year: int | None = None, explicit_span: bool = False) -> dict[str, Any] | None:
    clean = _clean_text(text)
    if not clean or _looks_like_policy_or_template(clean):
        return None
    if _is_context_false_positive(clean):
        return None

    sec_bios = re.match(r"^Sec Bios\s*\((?P<owners>[^)]+)\)\s*-\s*(?P<due>.+)$", clean, re.IGNORECASE)
    if sec_bios:
        return {
            "title": "Finish sec bios.",
            "owner_names": _split_owner_names(sec_bios.group("owners")),
            "due_date": _parse_due_date(sec_bios.group("due"), default_year),
            "status": _status_from_text(clean),
        }

    row = re.match(
        r"^(?P<owners>.+?)\s+"
        r"(?P<date>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(?P<task>.+)$",
        clean,
        re.IGNORECASE,
    )
    if row:
        return {
            "title": _with_period(_sentence_case(row.group("task"))),
            "owner_names": _split_owner_names(row.group("owners"), keep_groups=explicit_span),
            "due_date": _date_from_month_day(row.group("date"), row.group("day"), default_year),
            "status": _status_from_text(clean),
        }

    owner_suffix = _parse_owner_suffix(clean, keep_groups=explicit_span)
    if owner_suffix:
        task, owner_names = owner_suffix
        if not _is_action_verb_phrase(task) and not explicit_span:
            return None
        return {
            "title": _with_period(_sentence_case(task)),
            "owner_names": owner_names,
            "due_date": _parse_due_date(task, default_year),
            "status": _status_from_text(clean),
        }

    colon = re.match(rf"^(?P<owners>{OWNER_PATTERN}(?:\s+and\s+{OWNER_PATTERN})?)\s*:\s*(?P<task>.+)$", clean)
    if colon:
        owner_names = _split_owner_names(colon.group("owners"), keep_groups=explicit_span)
        if _has_suspicious_owner(owner_names) or not _is_action_verb_phrase(colon.group("task")):
            return None
        return {
            "title": _with_period(_sentence_case(colon.group("task"))),
            "owner_names": owner_names,
            "due_date": _parse_due_date(colon.group("task"), default_year),
            "status": _status_from_text(clean),
        }

    owner_to = re.match(rf"^(?P<owners>{OWNER_PATTERN}(?:\s+and\s+{OWNER_PATTERN})?)\s+to\s+(?P<task>.+)$", clean)
    if owner_to and owner_to.group("owners").lower() not in {"everyone"}:
        owner_names = _split_owner_names(owner_to.group("owners"), keep_groups=explicit_span)
        if _has_suspicious_owner(owner_names):
            return None
        return {
            "title": _with_period(_sentence_case(owner_to.group("task"))),
            "owner_names": owner_names,
            "due_date": _parse_due_date(owner_to.group("task"), default_year),
            "status": _status_from_text(clean),
        }
    if clean.lower().startswith("everyone to "):
        task = clean[12:]
        return {
            "title": _with_period(_sentence_case(task)),
            "owner_names": [],
            "due_date": _parse_due_date(task, default_year),
            "status": _status_from_text(clean),
        }

    owner_will = re.match(rf"^(?P<owners>{OWNER_PATTERN}(?:\s+and\s+{OWNER_PATTERN})?)\s+will\s+(?P<task>.+)$", clean)
    if owner_will:
        owner_names = _split_owner_names(owner_will.group("owners"), keep_groups=explicit_span)
        if _has_suspicious_owner(owner_names):
            return None
        return {
            "title": _with_period(_sentence_case(owner_will.group("task"))),
            "owner_names": owner_names,
            "due_date": _parse_due_date(owner_will.group("task"), default_year),
            "status": _status_from_text(clean),
        }

    dash = re.match(rf"^(?P<owners>{OWNER_PATTERN}(?:\s+and\s+{OWNER_PATTERN})?)\s+-\s+(?P<task>.+)$", clean)
    if dash:
        owner_names = _split_owner_names(dash.group("owners"), keep_groups=explicit_span)
        if _has_suspicious_owner(owner_names) or "?" in dash.group("task"):
            return None
        return {
            "title": _with_period(_sentence_case(dash.group("task"))),
            "owner_names": owner_names,
            "due_date": _parse_due_date(dash.group("task"), default_year),
            "status": _status_from_text(clean),
        }

    if explicit_span:
        owner_prefix = _parse_owner_prefix_without_connector(clean)
        if owner_prefix:
            task, owner_names = owner_prefix
            return {
                "title": _with_period(_sentence_case(task)),
                "owner_names": owner_names,
                "due_date": _parse_due_date(task, default_year),
                "status": _status_from_text(clean),
            }
        if _is_action_verb_phrase(clean):
            return {
                "title": _with_period(_sentence_case(clean)),
                "owner_names": [],
                "due_date": _parse_due_date(clean, default_year),
                "status": _status_from_text(clean),
            }

    if clean.lower().startswith("if you did an interview"):
        return {
            "title": "Fill in interview information for James.",
            "owner_names": ["Interviewers"],
            "due_date": None,
            "status": _status_from_text(clean),
        }

    return None


def _action_item_from_parsed(
    parsed: dict[str, Any],
    block: dict[str, Any],
    line_index: int,
    span: dict[str, Any] | None = None,
    quote_text: str | None = None,
) -> dict[str, Any]:
    source = dict(block.get("source") or {})
    quote = quote_text or _quote_for_line(block, line_index)
    source["quote"] = quote
    title = parsed["title"]
    object_id = _object_id(title, parsed.get("owner_names") or [], block.get("source_event_id"), line_index)
    result = {
        "object_id": object_id,
        "type": "action_item",
        "title": title,
        "summary": f"{_human_join(parsed.get('owner_names') or []) + ' should ' if parsed.get('owner_names') else 'The team should '}{title[:1].lower() + title[1:]}",
        "owner_names": parsed.get("owner_names") or [],
        "due_date": parsed.get("due_date"),
        "date": parsed.get("due_date"),
        "status": parsed.get("status") or "open",
        "source_event_ids": [block["source_event_id"]] if block.get("source_event_id") else [],
        "sources": [source] if source else [],
        "quote": quote,
        "confidence": 0.8,
        "block_id": block.get("block_id"),
    }
    if span is not None:
        result["span_id"] = span.get("span_id")
    return result


def _quote_for_line(block: dict[str, Any], line_index: int) -> str:
    lines = block.get("lines") or []
    selected = []
    if line_index > 0 and _line_needs_parent(lines[line_index], lines[line_index - 1]):
        selected.append(_line_text(lines[line_index - 1]))
    selected.append(_line_text(lines[line_index]))
    if line_index + 1 < len(lines) and _line_needs_child(lines[line_index], lines[line_index + 1]):
        selected.append(_line_text(lines[line_index + 1]))
    heading = _normalize_spaces(str(block.get("heading") or ""))
    return _trim_quote(" ".join([heading, *selected]), 700)


def _line_needs_parent(line: dict[str, Any], previous: dict[str, Any]) -> bool:
    body = _line_body(line)
    previous_body = _line_body(previous)
    return bool(re.search(r"^[A-Z][A-Za-z,\s]+$", body) and re.search(r"\bfinish by\b|task|bios", previous_body, re.IGNORECASE))


def _line_needs_child(line: dict[str, Any], next_line: dict[str, Any]) -> bool:
    body = _line_body(line)
    next_body = _line_body(next_line)
    if re.search(r"\bfinish by\b", body, re.IGNORECASE) and re.search(r"[A-Z][a-z]+,\s+[A-Z][a-z]+", next_body):
        return True
    return False


def _is_child_line_without_task(block: dict[str, Any], line: dict[str, Any]) -> bool:
    if block.get("block_type") == "task_table":
        return False
    indent = int(line.get("indent") or 0)
    top_indent = min((int(item.get("indent") or 0) for item in block.get("lines") or [] if _line_body(item)), default=0)
    return indent > top_indent + 4 and parse_task_line(_line_body(line)) is None


def _new_span(block: dict[str, Any], heading: str, heading_line_index: int) -> dict[str, Any]:
    digest = hashlib.sha1("|".join([str(block.get("block_id") or ""), heading, str(heading_line_index)]).encode("utf-8")).hexdigest()
    return {
        "span_id": f"span_{digest[:12]}",
        "heading": _normalize_spaces(heading),
        "heading_line_index": heading_line_index,
        "source_event_id": block.get("source_event_id"),
        "source_event_ids": [block["source_event_id"]] if block.get("source_event_id") else [],
        "block_ids": [block["block_id"]] if block.get("block_id") else [],
        "source": block.get("source") or {},
        "page": block.get("page"),
        "lines": [],
        "line_count": 0,
        "likely_task_rows": [],
    }


def _span_heading_line(block: dict[str, Any], heading: str) -> dict[str, Any]:
    return {
        "line_index": -1,
        "text": _normalize_spaces(heading),
        "body": _normalize_spaces(heading),
        "indent": 0,
        "source_event_id": block.get("source_event_id"),
        "block_id": block.get("block_id"),
        "source": block.get("source") or {},
        "page": block.get("page"),
    }


def _span_line(block: dict[str, Any], index: int, line: dict[str, Any]) -> dict[str, Any]:
    return {
        "line_index": index,
        "text": _line_text(line),
        "body": _line_body(line),
        "indent": int(line.get("indent") or 0),
        "source_event_id": block.get("source_event_id"),
        "block_id": block.get("block_id"),
        "source": block.get("source") or {},
        "page": block.get("page"),
    }


def _finalize_span(span: dict[str, Any]) -> None:
    lines = [line for line in span.get("lines", []) if _normalize_spaces(str(line.get("body") or line.get("text") or ""))]
    span["lines"] = lines
    span["line_count"] = len(lines)
    span["start_line_index"] = lines[0].get("line_index") if lines else None
    span["end_line_index"] = lines[-1].get("line_index") if lines else None
    span["quote"] = _trim_quote(" ".join([str(span.get("heading") or ""), *(str(line.get("text") or line.get("body") or "") for line in lines)]), 1000)
    span["source_event_ids"] = list(dict.fromkeys(line.get("source_event_id") for line in lines if line.get("source_event_id")))
    span["block_ids"] = list(dict.fromkeys(line.get("block_id") for line in lines if line.get("block_id")))
    rows = _task_rows_from_span(span)
    likely_rows = []
    for row in rows:
        if parse_task_line(row["body"], explicit_span=True) is None:
            continue
        likely_rows.append(
            {
                "line_index": row["line_index"],
                "text": row["quote"],
                "body": row["body"],
                "source_event_id": row["block"].get("source_event_id"),
                "block_id": row["block"].get("block_id"),
            }
        )
    span["likely_task_rows"] = likely_rows


def _task_rows_from_span(span: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in span.get("lines", []):
        body = _normalize_spaces(str(line.get("body") or line.get("text") or ""))
        if not body or TASK_SPAN_HEADING_RE.match(body):
            continue
        if rows and _should_merge_span_line(rows[-1]["body"], body):
            rows[-1]["body"] = _normalize_spaces(f"{rows[-1]['body'].rstrip('- ')} {body}")
            rows[-1]["quote"] = _trim_quote(f"{rows[-1]['quote']} {line.get('text') or body}", 700)
            rows[-1]["line_refs"].append(
                {
                    "source_event_id": line.get("source_event_id"),
                    "block_id": line.get("block_id"),
                    "line_index": line.get("line_index"),
                }
            )
            continue
        rows.append(
            {
                "body": body,
                "quote": str(line.get("text") or body),
                "line_index": int(line.get("line_index") or 0),
                "block": _block_for_span_line(line),
                "line_refs": [
                    {
                        "source_event_id": line.get("source_event_id"),
                        "block_id": line.get("block_id"),
                        "line_index": line.get("line_index"),
                    }
                ],
            }
        )
    return rows


def _block_for_span_line(line: dict[str, Any]) -> dict[str, Any]:
    body = _normalize_spaces(str(line.get("body") or line.get("text") or ""))
    text = _normalize_spaces(str(line.get("text") or body))
    return {
        "block_id": line.get("block_id"),
        "source_event_id": line.get("source_event_id"),
        "heading": "",
        "text": text,
        "lines": [{"text": text, "body": body, "indent": int(line.get("indent") or 0)}],
        "source": line.get("source") or {},
        "page": line.get("page"),
    }


def _should_merge_span_line(previous: str, current: str) -> bool:
    previous = _normalize_spaces(previous)
    current = _normalize_spaces(current)
    if not previous or not current:
        return False
    if previous.endswith("-"):
        return True
    if previous.count("(") > previous.count(")"):
        return True
    if re.search(r"\b(for|as|and|of|to|with|before|after)$", previous, re.IGNORECASE):
        return True
    if _looks_like_owner_only_continuation(current):
        return True
    if _looks_like_email_owner_line(current):
        return True
    if current[:1].islower():
        return True
    return False


def _looks_like_owner_only_continuation(text: str) -> bool:
    cleaned = _normalize_spaces(text).strip(" .)")
    if not cleaned:
        return False
    return _looks_like_owner_text(cleaned, keep_groups=True)


def _looks_like_email_owner_line(text: str) -> bool:
    return bool(re.search(r"\b\S+@\S+\b", text) and re.search(r"\([^)]+\)\s*$", text))


def _is_major_new_section(text: str) -> bool:
    body = _normalize_spaces(text)
    if not body or TASK_SPAN_HEADING_RE.match(body):
        return False
    if re.match(r"^\d+\.\s+\S", body):
        return True
    return bool(
        re.match(
            r"^(present|absent|agenda|location|zoom|discussion|notes|decisions|open questions|parking lot|overview|mandatory notes?|tips?|general guidelines?|communications workflow|team organizational practices|references?|templates?|examples?)\b\s*:?",
            body,
            re.IGNORECASE,
        )
    )


def _heading_should_continue_task_span(heading: str) -> bool:
    clean = _clean_text(heading)
    return bool(_is_action_verb_phrase(clean) or _parse_owner_suffix(clean, keep_groups=True))


def _parse_owner_suffix(clean: str, keep_groups: bool = False) -> tuple[str, list[str]] | None:
    parenthetical = re.match(r"^(?P<task>.+?)\s*\((?P<owners>[^)]+)\)\s*$", clean)
    if parenthetical:
        owner_text = parenthetical.group("owners")
        if "-" in owner_text:
            possible_owner = owner_text.rsplit("-", 1)[1]
            if _looks_like_owner_text(possible_owner, keep_groups=keep_groups):
                owners = _split_owner_names(possible_owner, keep_groups=keep_groups)
                return parenthetical.group("task"), owners
        if _looks_like_owner_text(owner_text, keep_groups=keep_groups):
            return parenthetical.group("task"), _split_owner_names(owner_text, keep_groups=keep_groups)

    dash_suffix = re.match(r"^(?P<task>.+?)\s+-\s+(?P<owners>[^-]+?)\)?$", clean)
    if dash_suffix and _looks_like_owner_text(dash_suffix.group("owners"), keep_groups=keep_groups):
        return dash_suffix.group("task"), _split_owner_names(dash_suffix.group("owners"), keep_groups=keep_groups)

    if keep_groups:
        trailing_owner = re.match(
            r"^(?P<task>.+?)\s+(?P<owners>[A-Z][A-Za-z.'-]+(?:\s+or\s+[A-Z][A-Za-z.'-]+)?)$",
            clean,
            re.IGNORECASE,
        )
        if (
            trailing_owner
            and ":" not in trailing_owner.group("task")
            and _is_action_verb_phrase(trailing_owner.group("task"))
            and _looks_like_owner_text(trailing_owner.group("owners"), keep_groups=keep_groups)
        ):
            return trailing_owner.group("task"), _split_owner_names(trailing_owner.group("owners"), keep_groups=keep_groups)
    return None


def _parse_owner_prefix_without_connector(clean: str) -> tuple[str, list[str]] | None:
    titled_owner = re.match(r"^(?P<owner>(?:Mrs|Ms|Mr|Dr)\.?\s+[A-Z][A-Za-z.'-]+)\s+(?P<task>.+)$", clean)
    if titled_owner and _looks_like_unmarked_task(titled_owner.group("task")):
        return titled_owner.group("task"), _split_owner_names(titled_owner.group("owner"), keep_groups=True)

    one_name = re.match(r"^(?P<owner>[A-Z][A-Za-z.'-]+)\s+(?P<task>.+)$", clean)
    if one_name and one_name.group("owner") not in SUSPICIOUS_OWNERS and _looks_like_unmarked_task(one_name.group("task")):
        return one_name.group("task"), _split_owner_names(one_name.group("owner"), keep_groups=True)
    return None


def _looks_like_unmarked_task(text: str) -> bool:
    return bool(
        _is_action_verb_phrase(text)
        or re.search(r"\b(sec bios?|staff names?|director|directors|reg(?:istration)? link|bio)\b", text, re.IGNORECASE)
    )


def _is_action_verb_phrase(text: str) -> bool:
    return bool(
        ACTION_VERB_RE.search(text)
        or re.search(r"\b(invitation to|staff training|thank you gifts?|gift list|delegate resources|announcements?/displays?)\b", text, re.IGNORECASE)
    )


def _looks_like_owner_text(text: str, keep_groups: bool = False) -> bool:
    cleaned = _normalize_spaces(text).strip(" .!?)(")
    if not cleaned:
        return False
    if re.search(r"\b(closing ceremony|global studies|lunch|afterschool|conference|risk management|forms?)\b", cleaned, re.IGNORECASE):
        return False
    owners = _split_owner_names(cleaned, keep_groups=keep_groups)
    if not owners or _has_suspicious_owner(owners):
        return False
    if len(owners) > 4:
        return False
    return all(owner == "Everyone" or re.match(r"^(?:Mrs\.|Ms\.|Mr\.|Dr\.)?\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?$", owner) for owner in owners)


def _has_suspicious_owner(owner_names: list[str]) -> bool:
    normalized = {_normalize_spaces(owner).strip(" .") for owner in owner_names}
    if normalized & SUSPICIOUS_OWNERS:
        return True
    suspicious = {owner.lower() for owner in SUSPICIOUS_OWNERS}
    return any(re.search(r"\b" + re.escape(word) + r"\b", owner.lower()) for owner in normalized for word in suspicious)


def _is_context_false_positive(clean: str) -> bool:
    if re.match(r"^need\s+to\s+touch\b", clean, re.IGNORECASE):
        return True
    if re.match(r"^sjr\s*:", clean, re.IGNORECASE):
        return True
    if re.match(r"^[A-Z][A-Za-z.'-]+\s+-\s+.+\?$", clean):
        return True
    return False


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for action in actions:
        key = (_normalize_for_key(action.get("title", "")), tuple(action.get("owner_names") or []), tuple(action.get("source_event_ids") or []))
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def _looks_like_policy_or_template(text: str) -> bool:
    return bool(
        re.search(
            r"\b(maintain a professional setting|try to have a fun|engaging tone|sample reply|example message|dear .+?,)\b",
            text,
            re.IGNORECASE,
        )
    )


def _parse_due_date(text: str, default_year: int | None) -> str | None:
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        text,
        re.IGNORECASE,
    )
    if match:
        return _date_from_month_day(match.group(1), match.group(2), default_year)
    return None


def _date_from_month_day(month: str, day: str, year: int | None) -> str | None:
    if year is None:
        return None
    month_num = MONTHS[month.lower()]
    return f"{year:04d}-{month_num:02d}-{int(day):02d}"


def _status_from_text(text: str) -> str:
    if re.search(r"☑|✅|\[x\]|~~.+~~", text, re.IGNORECASE):
        return "done"
    return "open"


def _split_owner_names(text: str, keep_groups: bool = False) -> list[str]:
    text = _normalize_spaces(text).strip(" .!?)(")
    text = text.replace("&", " and ").replace("/", " and ")
    text = re.sub(r"\bor\b", " and ", text, flags=re.IGNORECASE)
    parts = re.split(r"\s+and\s+|,\s*", text)
    owners = []
    for part in parts:
        owner = _canonical_owner_name(part)
        owner_key = owner.lower().strip(" .!")
        if owner_key in {"everyone", "team", "everybody"}:
            if keep_groups:
                owners.append("Everyone")
            continue
        if owner:
            owners.append(owner)
    return owners


def _canonical_owner_name(name: str) -> str:
    cleaned = _normalize_spaces(name).strip(" .:!")
    mapping = {
        "mrs haider": "Mrs. Haider",
        "ms boland": "Ms. Boland",
    }
    return mapping.get(cleaned.lower(), cleaned)


def _line_text(line: dict[str, Any]) -> str:
    return _normalize_spaces(str(line.get("text") or ""))


def _line_body(line: dict[str, Any]) -> str:
    return _normalize_spaces(str(line.get("body") or line.get("text") or ""))


def _clean_text(text: str) -> str:
    text = text.replace("ﬁ", "fi").replace("ﬀ", "ff")
    text = re.sub(r"^\s*(?:[-*]\s+|(?:\d+|[a-z]+|[ivxlcdm]+)\.\s+)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\S+@\S+\b", " ", text)
    text = re.sub(r"\b(?:https?://|www\.)\S+\b", " ", text, flags=re.IGNORECASE)
    return _normalize_spaces(text).strip(" .")


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


def _normalize_for_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _object_id(title: str, owners: list[str], source_event_id: str | None, line_index: int) -> str:
    digest = hashlib.sha1("|".join([title, *owners, str(source_event_id or ""), str(line_index)]).encode("utf-8")).hexdigest()
    return f"memory_{digest[:12]}"
