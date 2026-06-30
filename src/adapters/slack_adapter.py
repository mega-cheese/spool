from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..normalized_events import NormalizedSourceEvent


def slack_fixture_to_normalized_events(slack_json: dict[str, Any]) -> list[NormalizedSourceEvent]:
    workspace_name = _workspace_name(slack_json.get("workspace"))
    events: list[NormalizedSourceEvent] = []

    for message in slack_json.get("messages", []):
        if message.get("text"):
            events.append(
                _message_to_normalized_event(
                    workspace_name,
                    str(message.get("channel") or message.get("channel_name") or ""),
                    str(message.get("channel_id") or ""),
                    message,
                )
            )

    for channel_data in slack_json.get("channels", []):
        channel = str(channel_data.get("channel") or channel_data.get("channel_name") or "")
        channel_id = str(channel_data.get("channel_id") or "")
        for message in channel_data.get("messages", []):
            if message.get("text"):
                events.append(_message_to_normalized_event(workspace_name, channel, channel_id, message))

    return sorted(events, key=lambda event: (event.container.get("id") or "", event.external_id))


def deterministic_event_id(source_type: str, channel_id: str, message_ts: str) -> str:
    return "_".join(
        _safe_token(part)
        for part in (source_type, channel_id or "unknown_channel", message_ts)
        if part is not None
    )


def _message_to_normalized_event(
    workspace_name: str,
    channel: str,
    channel_id: str,
    message: dict[str, Any],
) -> NormalizedSourceEvent:
    message_ts = str(message.get("ts", ""))
    thread_ts = str(message.get("thread_ts") or message_ts)
    text = str(message.get("text") or "")
    speaker = str(message.get("user_name") or message.get("user") or message.get("speaker") or "Unknown")
    speaker_role = message.get("role") or message.get("speaker_role")
    return NormalizedSourceEvent(
        event_id=deterministic_event_id("slack", channel_id, message_ts),
        source_type="slack",
        source_subtype="message",
        external_id=message_ts,
        source_url=message.get("url"),
        workspace={"name": workspace_name},
        container={"type": "channel", "id": channel_id, "name": channel},
        context={"type": "thread", "root_id": thread_ts},
        actor={"name": speaker, "role": speaker_role},
        event_type="message",
        created_at=_created_at(message),
        updated_at=None,
        content={"text": text},
        provenance={
            "source_type": "slack",
            "locator": message_ts,
            "quote": text,
            "workspace": workspace_name,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        },
        raw_ref={"source": "local_slack_fixture", "message": dict(message)},
    )


def _workspace_name(workspace: Any) -> str:
    if isinstance(workspace, dict):
        return str(workspace.get("name") or workspace.get("id") or "")
    return str(workspace or "")


def _created_at(message: dict[str, Any]) -> str | None:
    ts = message.get("ts")
    if ts:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            pass
    date_value = message.get("date")
    return str(date_value) if date_value else None


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
