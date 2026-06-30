from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.document_adapter import (
    DocumentParseError,
    document_fixture_to_normalized_events,
    is_structured_document_fixture,
    uploaded_document_to_normalized_events,
)
from .adapters.slack_adapter import slack_fixture_to_normalized_events
from .bundles import SourceBundle, bundle_source_events
from .schemas import CanonicalEvent
from .slack_parser import parse_slack_json


@dataclass
class IngestedInput:
    canonical_events: list[CanonicalEvent]
    normalized_events: list[Any]
    source_bundles: list[SourceBundle]
    raw_input: Any
    inferred_source_type: str


def load_input_source(path: str | Path, source_type: str | None = None) -> IngestedInput:
    input_path = Path(path)
    requested_type = None if source_type in {None, "auto"} else source_type
    if requested_type == "uploaded_document" or input_path.suffix.lower() in {".txt", ".md", ".docx", ".pdf"}:
        normalized_events = uploaded_document_to_normalized_events(input_path)
        return _ingested(normalized_events, raw_input={"path": str(input_path)}, inferred_source_type="uploaded_document")

    if input_path.suffix.lower() != ".json":
        raise DocumentParseError(f"Cannot infer source type from {input_path}. Use a .json, .txt, .md, .docx, or .pdf file.")

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if requested_type == "google_doc" or (requested_type is None and isinstance(payload, dict) and is_structured_document_fixture(payload)):
        normalized_events = document_fixture_to_normalized_events(payload)
        return _ingested(normalized_events, raw_input=payload, inferred_source_type="google_doc")

    if requested_type == "mixed" or (requested_type is None and isinstance(payload, dict) and isinstance(payload.get("sources"), list)):
        normalized_events = _mixed_fixture_to_normalized_events(payload, input_path.parent)
        return _ingested(normalized_events, raw_input=payload, inferred_source_type="mixed")

    normalized_events = slack_fixture_to_normalized_events(payload)
    canonical_events = parse_slack_json(payload)
    return IngestedInput(
        canonical_events=canonical_events,
        normalized_events=normalized_events,
        source_bundles=bundle_source_events(normalized_events),
        raw_input=payload,
        inferred_source_type="slack",
    )


def _mixed_fixture_to_normalized_events(payload: dict[str, Any], base_dir: Path) -> list[Any]:
    workspace = payload.get("workspace")
    events: list[Any] = []
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_type = source.get("source_type")
        if source_type == "slack":
            slack_payload = {key: value for key, value in source.items() if key != "source_type"}
            slack_payload.setdefault("workspace", workspace)
            events.extend(slack_fixture_to_normalized_events(slack_payload))
        elif source_type == "google_doc":
            document_payload = dict(source)
            document_payload.setdefault("workspace", workspace)
            events.extend(document_fixture_to_normalized_events(document_payload))
        elif source_type == "uploaded_document":
            source_path = source.get("path")
            if not source_path:
                raise DocumentParseError("Mixed uploaded_document sources require a path.")
            upload_path = Path(str(source_path))
            if not upload_path.is_absolute():
                upload_path = base_dir / upload_path
            events.extend(uploaded_document_to_normalized_events(upload_path, workspace=str(workspace or "local_upload")))
    return events


def _ingested(normalized_events: list[Any], raw_input: Any, inferred_source_type: str) -> IngestedInput:
    return IngestedInput(
        canonical_events=[_normalized_event_to_canonical(event) for event in normalized_events],
        normalized_events=normalized_events,
        source_bundles=bundle_source_events(normalized_events),
        raw_input=raw_input,
        inferred_source_type=inferred_source_type,
    )


def _normalized_event_to_canonical(event: Any) -> CanonicalEvent:
    container = event.container or {}
    context = event.context or {}
    actor = event.actor or {}
    provenance = event.provenance or {}
    return CanonicalEvent(
        source_type=str(event.source_type),
        workspace=str((event.workspace or {}).get("name") or ""),
        channel=str(container.get("name") or container.get("type") or ""),
        channel_id=str(container.get("id") or ""),
        thread_ts=str(context.get("root_id") or event.external_id),
        message_ts=str(provenance.get("locator") or event.external_id),
        timestamp=event.created_at,
        speaker=str(actor.get("name") or "Unknown"),
        speaker_role=actor.get("role"),
        text=str((event.content or {}).get("text") or ""),
        url=event.source_url,
    )
