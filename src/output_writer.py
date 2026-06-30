from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import DecisionRecord, output_to_dict


def write_output(
    output_path: str | Path,
    metadata: dict[str, Any],
    decisions: list[DecisionRecord],
    pretty: bool = False,
    include_internal: bool = False,
) -> dict[str, Any]:
    payload = output_to_dict(metadata, decisions, include_internal=include_internal)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")
    return payload


def read_decision_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return {"metadata": {}, "decisions": payload}
    return payload
