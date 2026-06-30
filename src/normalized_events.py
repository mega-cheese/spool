from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NormalizedSourceEvent:
    event_id: str
    source_type: str
    source_subtype: str | None
    external_id: str
    source_url: str | None
    workspace: dict[str, Any]
    container: dict[str, Any]
    context: dict[str, Any]
    actor: dict[str, Any]
    event_type: str
    created_at: str | None
    updated_at: str | None
    content: dict[str, Any]
    provenance: dict[str, Any]
    raw_ref: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
