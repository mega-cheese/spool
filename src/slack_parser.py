from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .canonical import slack_json_to_events
from .schemas import CanonicalEvent


def load_slack_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_slack_json(slack_json: dict[str, Any]) -> list[CanonicalEvent]:
    return slack_json_to_events(slack_json)


def parse_slack_file(path: str | Path) -> list[CanonicalEvent]:
    return parse_slack_json(load_slack_json(path))


def group_events_by_thread(events: list[CanonicalEvent]) -> dict[str, list[CanonicalEvent]]:
    grouped: dict[str, list[CanonicalEvent]] = defaultdict(list)
    for event in events:
        grouped[event.thread_ts].append(event)
    return {
        thread_ts: sorted(thread_events, key=lambda event: event.message_ts)
        for thread_ts, thread_events in grouped.items()
    }
