from __future__ import annotations

import hashlib
import re
from typing import Any


TASK_HEADING_RE = re.compile(
    r"\b(to-?do(?: list)?|todo|tasks?|this week's tasks|individual tasks|action items?|next steps?|follow-?ups?)\b",
    re.IGNORECASE,
)
POLICY_HEADING_RE = re.compile(
    r"\b(mandatory notes?|tips?|general guidelines?|communications workflow|team organizational practices)\b",
    re.IGNORECASE,
)
TEMPLATE_HEADING_RE = re.compile(r"\b(template|example|sample|script|message)\b", re.IGNORECASE)
REFERENCE_RE = re.compile(r"https?://|www\.|@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
AGENDA_HEADING_RE = re.compile(r"^\s*\d+\.\s+\S")
QUESTION_RE = re.compile(r"\?|^\s*(should|do|does|can|could|whether)\b", re.IGNORECASE)
OWNER_TASK_RE = re.compile(
    r"\b("
    r"[A-Z][A-Za-z.'-]+(?:\s+and\s+[A-Z][A-Za-z.'-]+)?\s+(?:to|will)\s+|"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?\s*:\s+|"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?\s+-\s+"
    r")"
)
DATE_TASK_ROW_RE = re.compile(
    r"^[A-Z][A-Za-z.'-]+(?:\s+and\s+[A-Z][A-Za-z.'-]+)?\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?\s+.+",
    re.IGNORECASE,
)


def classify_blocks(normalized_events: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for event in normalized_events:
        if _event_source_type(event) not in {"uploaded_document", "google_doc"} and _get(event, "source_subtype") != "document_section":
            continue
        heading = _section_heading(event) or "Document"
        text = _event_text(event)
        lines = _event_lines(event)
        block_type, confidence, signals = _classify_block(heading, text, lines)
        block = {
            "block_id": _block_id(_get(event, "event_id"), heading, text),
            "source_event_id": _get(event, "event_id"),
            "block_type": block_type,
            "heading": heading,
            "text": text,
            "lines": lines,
            "page": (_get(event, "provenance") or {}).get("page"),
            "confidence": confidence,
            "signals": signals,
            "likely_task_rows": _likely_task_rows(lines),
            "source": _source_payload(event),
        }
        blocks.append(block)
    return blocks


def _classify_block(heading: str, text: str, lines: list[dict[str, Any]]) -> tuple[str, float, list[str]]:
    signals: list[str] = []
    normalized_heading = heading.strip()
    owner_task_count = sum(1 for line in lines if _is_owner_task_line(_line_body(line)))
    table_rows = _likely_task_rows(lines)
    question_count = sum(1 for line in lines if QUESTION_RE.search(_line_body(line)))
    link_like_count = len(REFERENCE_RE.findall(text))

    if re.search(r"\bmeeting\s*#?\d*[: ]", normalized_heading, re.IGNORECASE) or re.search(r"\b(present|absent|location|zoom)\s*:", text, re.IGNORECASE):
        signals.append("meeting metadata")
        return "meeting_metadata", 0.86, signals
    if QUESTION_RE.search(normalized_heading) or (question_count >= 1 and question_count >= max(1, len(lines) // 2)):
        signals.append("question-heavy block")
        return "open_questions", 0.8, signals
    if TASK_HEADING_RE.search(normalized_heading):
        signals.append("task heading")
        if len(table_rows) >= 3:
            signals.append("table-like task rows")
            return "task_table", 0.94, signals
        return "task_section", 0.95, signals
    if len(table_rows) >= 3:
        signals.append("table-like task rows")
        return "task_table", 0.9, signals
    if owner_task_count >= 3:
        signals.append("multiple owner-task lines")
        return "task_section", 0.82, signals
    if POLICY_HEADING_RE.search(normalized_heading):
        signals.append("policy/process heading")
        return "policy_or_process", 0.9, signals
    if TEMPLATE_HEADING_RE.search(normalized_heading) or _looks_like_template(text):
        signals.append("template/example language")
        return "template_or_example", 0.82, signals
    if link_like_count >= 2 and len(lines) <= 8:
        signals.append("mostly reference links")
        return "reference_links", 0.78, signals
    if AGENDA_HEADING_RE.search(normalized_heading) or any(AGENDA_HEADING_RE.search(_line_text(line)) for line in lines[:3]):
        signals.append("numbered agenda")
        return "agenda_section", 0.74, signals
    return "unknown", 0.45, signals


def _likely_task_rows(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(lines):
        body = _line_body(line)
        if _is_owner_task_line(body) or DATE_TASK_ROW_RE.match(body):
            rows.append({"line_index": index, "text": _line_text(line), "body": body})
    return rows


def _is_owner_task_line(text: str) -> bool:
    return bool(OWNER_TASK_RE.search(text) or DATE_TASK_ROW_RE.match(text))


def _looks_like_template(text: str) -> bool:
    return bool(re.search(r"\b(copy/paste|template|sample reply|subject line|dear .+?,|hello .+?,)\b", text, re.IGNORECASE))


def _event_lines(event: Any) -> list[dict[str, Any]]:
    raw_ref = _get(event, "raw_ref") or {}
    raw_lines = raw_ref.get("lines") if isinstance(raw_ref, dict) else None
    lines: list[dict[str, Any]] = []
    if raw_lines:
        for line in raw_lines:
            if isinstance(line, dict):
                text = _normalize_spaces(str(line.get("text") or ""))
                body = _normalize_spaces(str(line.get("body") or text))
                if text or body:
                    lines.append(
                        {
                            "text": text,
                            "body": body,
                            "indent": int(line.get("indent") or 0),
                            "bullet_marker": line.get("bullet_marker"),
                            "checkbox": bool(line.get("checkbox")),
                        }
                    )
            else:
                text = _normalize_spaces(str(line))
                if text:
                    lines.append({"text": text, "body": _strip_bullet_prefix(text), "indent": 0, "bullet_marker": None, "checkbox": False})
    if not lines:
        for raw_line in _event_text(event).splitlines():
            text = _normalize_spaces(raw_line)
            if text:
                lines.append(
                    {
                        "text": text,
                        "body": _strip_bullet_prefix(text),
                        "indent": len(raw_line) - len(raw_line.lstrip(" ")),
                        "bullet_marker": None,
                        "checkbox": False,
                    }
                )
    return lines


def _source_payload(event: Any) -> dict[str, Any]:
    provenance = _get(event, "provenance") or {}
    actor = _get(event, "actor") or {}
    container = _get(event, "container") or {}
    return {
        "source_event_id": _get(event, "event_id"),
        "source_type": _event_source_type(event),
        "source_subtype": _get(event, "source_subtype"),
        "workspace": (_get(event, "workspace") or {}).get("name"),
        "document_id": provenance.get("document_id") or container.get("id"),
        "document_title": provenance.get("document_title") or container.get("name"),
        "section_heading": provenance.get("section_heading") or (_get(event, "context") or {}).get("section_heading"),
        "actor": actor.get("name") or "Unknown",
        "actor_role": actor.get("role"),
        "locator": provenance.get("locator") or _get(event, "external_id"),
        "page": provenance.get("page"),
        "url": _get(event, "source_url"),
    }


def _line_text(line: dict[str, Any]) -> str:
    return _normalize_spaces(str(line.get("text") or ""))


def _line_body(line: dict[str, Any]) -> str:
    return _normalize_spaces(str(line.get("body") or line.get("text") or ""))


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


def _block_id(event_id: str | None, heading: str, text: str) -> str:
    digest = hashlib.sha1("|".join([str(event_id or ""), heading, text[:250]]).encode("utf-8")).hexdigest()
    return f"block_{digest[:12]}"


def _strip_bullet_prefix(text: str) -> str:
    return re.sub(r"^\s*(?:[-*]\s+|(?:\d+|[a-z]+|[ivxlcdm]+)\.\s+)", "", text, flags=re.IGNORECASE).strip()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
