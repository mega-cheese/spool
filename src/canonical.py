from __future__ import annotations

from typing import Any

from .schemas import CanonicalEvent


def message_to_event(
    workspace: str,
    channel: str,
    channel_id: str,
    message: dict[str, Any],
) -> CanonicalEvent:
    message_ts = str(message.get("ts", ""))
    return CanonicalEvent(
        source_type="slack",
        workspace=workspace,
        channel=channel,
        channel_id=channel_id,
        thread_ts=str(message.get("thread_ts") or message_ts),
        message_ts=message_ts,
        timestamp=message.get("date"),
        speaker=str(message.get("user_name") or message.get("user") or message.get("speaker") or "Unknown"),
        speaker_role=message.get("role") or message.get("speaker_role"),
        text=str(message.get("text") or ""),
        url=message.get("url"),
    )


def slack_json_to_events(slack_json: dict[str, Any]) -> list[CanonicalEvent]:
    workspace = str(slack_json.get("workspace") or "")
    events: list[CanonicalEvent] = []
    for message in slack_json.get("messages", []):
        if message.get("text"):
            events.append(
                message_to_event(
                    workspace,
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
                events.append(message_to_event(workspace, channel, channel_id, message))
    return sorted(events, key=lambda event: event.message_ts)
