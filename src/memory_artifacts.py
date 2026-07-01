from __future__ import annotations

from typing import Any

from .memory_objects import MemoryObject, build_candidate_memory_payload
from .memory_store import approved_memory_payload, rejected_memory_payload, review_actions_payload
from .review_policy import apply_review_policy


def build_memory_artifacts(
    *,
    input_file: str,
    mode: str,
    pipeline: str,
    decisions_payload: dict[str, Any],
    clean_memory: dict[str, Any] | None = None,
    review_queue: dict[str, Any] | None = None,
    coverage_warnings: list[dict[str, Any]] | None = None,
    extra_candidates: list[MemoryObject] | None = None,
    auto_approve: bool = False,
) -> dict[str, Any]:
    if (clean_memory or {}).get("metadata", {}).get("classification") == "agent_change_packet":
        auto_approve = False
    candidate_payload = build_candidate_memory_payload(
        input_file=input_file,
        mode=mode,
        pipeline=pipeline,
        decisions_payload=decisions_payload,
        clean_memory=clean_memory,
        review_queue=review_queue,
    )
    if extra_candidates:
        existing_ids = {item.get("memory_id") for item in candidate_payload.get("candidates", []) if isinstance(item, dict)}
        for candidate in extra_candidates:
            candidate_dict = candidate.to_dict()
            if candidate_dict["memory_id"] in existing_ids:
                continue
            candidate_payload.setdefault("candidates", []).append(candidate_dict)
            existing_ids.add(candidate_dict["memory_id"])
        candidate_payload["metadata"]["candidate_count"] = len(candidate_payload.get("candidates", []))
    candidates = [MemoryObject.from_dict(item) for item in candidate_payload.get("candidates", [])]
    reviewed, memory_state_debug = apply_review_policy(
        candidates,
        auto_approve=auto_approve,
        coverage_warnings=coverage_warnings,
    )
    memory_state_debug["metadata"]["input_file"] = input_file
    candidate_payload["candidates"] = [candidate.to_dict() for candidate in reviewed]
    approved = [candidate for candidate in reviewed if candidate.review_state == "approved"]
    rejected = [candidate for candidate in reviewed if candidate.review_state == "rejected"]
    return {
        "candidate_memory.json": candidate_payload,
        "approved_memory.json": approved_memory_payload(input_file, approved),
        "rejected_memory.json": rejected_memory_payload(input_file, rejected),
        "review_actions.json": review_actions_payload(input_file, []),
        "memory_state_debug.json": memory_state_debug,
    }
