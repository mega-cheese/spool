from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..normalized_events import NormalizedSourceEvent


class DocumentParseError(ValueError):
    """Raised when a local document cannot be parsed into text sections."""


SUPPORTED_UPLOAD_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}


def document_fixture_to_normalized_events(document: dict[str, Any]) -> list[NormalizedSourceEvent]:
    source_type = str(document.get("source_type") or "google_doc")
    document_id = str(document.get("doc_id") or document.get("document_id") or _document_hash(document))
    title = str(document.get("title") or document_id)
    workspace = _workspace_name(document.get("workspace"), default="local_upload")
    sections = _fixture_sections(document)
    metadata = {
        "source_type": source_type,
        "document_id": document_id,
        "title": title,
        "workspace": workspace,
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "author": document.get("author"),
        "author_role": document.get("author_role"),
        "url": document.get("url"),
    }
    return [
        _section_to_event(
            source_type=source_type,
            document_id=document_id,
            title=title,
            workspace=workspace,
            section=section,
            section_index=index,
            created_at=_optional_str(document.get("created_at")),
            updated_at=_optional_str(document.get("updated_at")),
            author=_optional_str(document.get("author")),
            author_role=_optional_str(document.get("author_role")),
            source_url=_optional_str(document.get("url")),
            raw_metadata=metadata,
        )
        for index, section in enumerate(sections, start=1)
    ]


def uploaded_document_to_normalized_events(
    path: str | Path,
    workspace: str = "local_upload",
) -> list[NormalizedSourceEvent]:
    document_path = Path(path)
    if not document_path.exists():
        raise DocumentParseError(f"Document file does not exist: {document_path}")
    suffix = document_path.suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise DocumentParseError(f"Unsupported uploaded document type: {suffix or '<none>'}")

    sections = _sections_from_uploaded_file(document_path)
    if not sections:
        raise DocumentParseError(f"No text could be extracted from {document_path}. OCR is not yet supported.")

    file_hash = _file_hash(document_path)
    updated_at = datetime.fromtimestamp(document_path.stat().st_mtime, tz=timezone.utc).isoformat()
    metadata = {
        "source_type": "uploaded_document",
        "document_id": file_hash,
        "title": document_path.name,
        "workspace": workspace,
        "path": str(document_path),
        "updated_at": updated_at,
    }
    return [
        _section_to_event(
            source_type="uploaded_document",
            document_id=file_hash,
            title=document_path.name,
            workspace=workspace,
            section=section,
            section_index=index,
            created_at=None,
            updated_at=updated_at,
            author=None,
            author_role=None,
            source_url=None,
            raw_metadata=metadata,
        )
        for index, section in enumerate(sections, start=1)
    ]


def is_structured_document_fixture(payload: dict[str, Any]) -> bool:
    source_type = payload.get("source_type")
    return source_type in {"google_doc", "uploaded_document"} and isinstance(payload.get("sections"), list)


def deterministic_document_event_id(source_type: str, document_id: str, section_index: int) -> str:
    return "_".join(_safe_token(part) for part in (source_type, document_id, f"section_{section_index}") if part)


def _section_to_event(
    source_type: str,
    document_id: str,
    title: str,
    workspace: str,
    section: dict[str, Any],
    section_index: int,
    created_at: str | None,
    updated_at: str | None,
    author: str | None,
    author_role: str | None,
    source_url: str | None,
    raw_metadata: dict[str, Any],
) -> NormalizedSourceEvent:
    heading = str(section.get("heading") or "Document")
    text = str(section.get("text") or "").strip()
    locator = f"{document_id}#section-{section_index}"
    page = section.get("page")
    provenance: dict[str, Any] = {
        "source_type": source_type,
        "locator": locator,
        "quote": text,
        "workspace": workspace,
        "document_id": document_id,
        "document_title": title,
        "section_heading": heading,
    }
    if page is not None:
        provenance["page"] = page
    lines = section.get("lines")
    return NormalizedSourceEvent(
        event_id=deterministic_document_event_id(source_type, document_id, section_index),
        source_type=source_type,
        source_subtype="document_section",
        external_id=locator,
        source_url=source_url,
        workspace={"name": workspace},
        container={"type": "document", "id": document_id, "name": title},
        context={"type": "document", "root_id": document_id, "section_heading": heading},
        actor={"name": author, "role": author_role},
        event_type="document_section",
        created_at=created_at,
        updated_at=updated_at,
        content={"text": text},
        provenance=provenance,
        raw_ref={
            "source": "document_fixture" if source_type == "google_doc" else "uploaded_document",
            "section": dict(section),
            "lines": list(lines or []),
            "document": dict(raw_metadata),
        },
    )


def _fixture_sections(document: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(document.get("sections") or [], start=1):
        if not isinstance(section, dict):
            continue
        text = str(section.get("text") or "").strip()
        if not text:
            continue
        sections.append(
            {
                "heading": str(section.get("heading") or f"Section {index}"),
                "text": text,
                **({"page": section.get("page")} if section.get("page") is not None else {}),
            }
        )
    return sections


def _sections_from_uploaded_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _sections_from_markdown(path.read_text(encoding="utf-8"))
    if suffix == ".txt":
        return _sections_from_plain_text(path.read_text(encoding="utf-8"))
    if suffix == ".docx":
        return _sections_from_docx(path)
    if suffix == ".pdf":
        return _sections_from_pdf(path)
    raise DocumentParseError(f"Unsupported uploaded document type: {suffix or '<none>'}")


def _sections_from_markdown(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_heading = "Document"
    current_lines: list[str] = []

    for line in text.splitlines():
        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            _append_section(sections, current_heading, current_lines)
            current_heading = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    _append_section(sections, current_heading, current_lines)
    return sections or _sections_from_plain_text(text)


def _sections_from_plain_text(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    current_heading = "Document"
    current_lines: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        indent = len(line) - len(line.lstrip(" "))
        if _looks_like_heading(stripped, next_line, indent):
            _append_section(sections, current_heading, current_lines)
            current_heading = stripped.rstrip(":")
            current_lines = []
        else:
            current_lines.append(line)
    _append_section(sections, current_heading, current_lines)
    if sections:
        return sections
    stripped = text.strip()
    return [{"heading": "Document", "text": stripped}] if stripped else []


def _sections_from_docx(path: Path) -> list[dict[str, Any]]:
    try:
        from docx import Document
    except ImportError as exc:
        raise DocumentParseError("DOCX parsing requires python-docx. Install requirements to enable .docx uploads.") from exc

    document = Document(str(path))
    sections: list[dict[str, Any]] = []
    current_heading = "Document"
    current_lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        if text and style_name.lower().startswith("heading"):
            _append_section(sections, current_heading, current_lines)
            current_heading = text
            current_lines = []
        else:
            current_lines.append(text)
    _append_section(sections, current_heading, current_lines)
    return sections


def _sections_from_pdf(path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentParseError("PDF parsing requires pypdf. Install requirements to enable .pdf uploads.") from exc

    reader = PdfReader(str(path))
    sections: list[dict[str, Any]] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text(extraction_mode="layout") or "").strip()
        except Exception:
            text = (page.extract_text() or "").strip()
        if not text:
            continue
        page_sections = _sections_from_plain_text(text)
        for section in page_sections:
            section["page"] = page_index
            sections.append(section)
    if not sections:
        raise DocumentParseError("PDF text extraction returned no text. The PDF may be image-only; OCR is not yet supported.")
    return sections


def _append_section(sections: list[dict[str, Any]], heading: str, lines: list[str]) -> None:
    text = "\n".join(line.rstrip() for line in lines).strip()
    if text:
        sections.append({"heading": heading or "Document", "text": text, "lines": [_line_info(line) for line in lines if line.strip()]})


def _looks_like_heading(line: str, next_line: str, indent: int = 0) -> bool:
    if not line or not next_line:
        return False
    if len(line) > 80 or line.endswith((".", "!", "?")):
        return False
    if indent > 4:
        return False
    return bool(
        line.endswith(":")
        or re.match(r"^[A-Z][A-Za-z0-9 /&-]{2,}$", line)
        or re.match(r"^\d+\.\s+[A-Z][A-Za-z0-9 /&?().-]{3,}$", line)
    )


def _line_info(line: str) -> dict[str, Any]:
    text = line.rstrip()
    stripped = text.strip()
    bullet_match = re.match(r"^(\s*)(?:[-*☐□]|\d+\.|[a-z]\.|[ivx]+\.)\s+(.*)$", text, re.IGNORECASE)
    return {
        "text": stripped,
        "indent": len(text) - len(text.lstrip(" ")),
        "bullet_marker": bullet_match.group(0).split()[0] if bullet_match else None,
        "body": bullet_match.group(2).strip() if bullet_match else stripped,
        "checkbox": bool(re.match(r"^\s*[☐□]\s+", text)),
    }


def _workspace_name(value: Any, default: str) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or default)
    return str(value or default)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    return f"file_{digest[:16]}"


def _document_hash(document: dict[str, Any]) -> str:
    seed = repr(sorted((str(key), repr(value)) for key, value in document.items())).encode("utf-8")
    return "doc_" + hashlib.sha1(seed).hexdigest()[:16]


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
