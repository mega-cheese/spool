from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .normalized_events import NormalizedSourceEvent


@dataclass
class SourceBundle:
    bundle_id: str
    bundle_type: str
    source_types: list[str]
    root_id: str
    title: str | None
    time_range: dict[str, str | None]
    events: list[NormalizedSourceEvent]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["events"] = [event.to_dict() for event in self.events]
        return result


def bundle_slack_events(events: list[NormalizedSourceEvent]) -> list[SourceBundle]:
    return _bundle_slack_events([event for event in events if event.source_type == "slack"])


def bundle_source_events(events: list[NormalizedSourceEvent]) -> list[SourceBundle]:
    slack_bundles = _bundle_slack_events([event for event in events if event.source_type == "slack"])
    document_bundles = _bundle_document_events(
        [
            event
            for event in events
            if event.source_subtype == "document_section" or event.container.get("type") == "document"
        ]
    )
    return [*slack_bundles, *document_bundles]


def _bundle_slack_events(events: list[NormalizedSourceEvent]) -> list[SourceBundle]:
    grouped: dict[tuple[str, str, str], list[NormalizedSourceEvent]] = {}
    for event in events:
        workspace = str(event.workspace.get("name") or "")
        channel_id = str(event.container.get("id") or "")
        root_id = str(event.context.get("root_id") or event.external_id)
        grouped.setdefault((workspace, channel_id, root_id), []).append(event)

    bundles: list[SourceBundle] = []
    for (workspace, channel_id, root_id), bundle_events in sorted(grouped.items()):
        ordered_events = sorted(bundle_events, key=lambda event: event.external_id)
        created_values = [event.created_at for event in ordered_events if event.created_at]
        bundles.append(
            SourceBundle(
                bundle_id=_bundle_id("slack_thread", workspace, channel_id, root_id),
                bundle_type="slack_thread",
                source_types=sorted({event.source_type for event in ordered_events}),
                root_id=root_id,
                title=_title_from_events(ordered_events),
                time_range={
                    "start": min(created_values) if created_values else None,
                    "end": max(created_values) if created_values else None,
                },
                events=ordered_events,
            )
        )
    return bundles


def _bundle_document_events(events: list[NormalizedSourceEvent]) -> list[SourceBundle]:
    grouped: dict[tuple[str, str], list[NormalizedSourceEvent]] = {}
    for event in events:
        workspace = str(event.workspace.get("name") or "")
        document_id = str(event.container.get("id") or event.context.get("root_id") or event.external_id)
        grouped.setdefault((workspace, document_id), []).append(event)

    bundles: list[SourceBundle] = []
    for (workspace, document_id), bundle_events in sorted(grouped.items()):
        ordered_events = sorted(bundle_events, key=_document_event_sort_key)
        created_values = [event.created_at for event in ordered_events if event.created_at]
        updated_values = [event.updated_at for event in ordered_events if event.updated_at]
        bundles.append(
            SourceBundle(
                bundle_id=_bundle_id("document", workspace, "", document_id),
                bundle_type="document",
                source_types=sorted({event.source_type for event in ordered_events}),
                root_id=document_id,
                title=str(ordered_events[0].container.get("name") or document_id) if ordered_events else None,
                time_range={
                    "start": min(created_values) if created_values else None,
                    "end": max(updated_values or created_values) if (updated_values or created_values) else None,
                },
                events=ordered_events,
            )
        )
    return bundles


def _document_event_sort_key(event: NormalizedSourceEvent) -> tuple[int, str]:
    match = re.search(r"#section-(\d+)$", event.external_id)
    return (int(match.group(1)) if match else 0, event.external_id)


def _title_from_events(events: list[NormalizedSourceEvent]) -> str | None:
    if not events:
        return None
    text = str(events[0].content.get("text") or "").strip()
    if not text:
        return None
    return text[:117] + "..." if len(text) > 120 else text


def _bundle_id(bundle_type: str, workspace: str, channel_id: str, root_id: str) -> str:
    return "_".join(_safe_token(part) for part in (bundle_type, workspace, channel_id, root_id) if part)


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
