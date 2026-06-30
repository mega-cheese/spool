from __future__ import annotations

import re
from typing import Any

from .candidates import DecisionCandidate


def normalize_decision_text(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"\binstead of\b", "over", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\b(a|an|the|and|for|to|now|decision|final|use|choose)\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def are_duplicate_candidates(left: DecisionCandidate | str, right: DecisionCandidate | str) -> bool:
    left_text = left.normalized_action if isinstance(left, DecisionCandidate) else normalize_decision_text(left)
    right_text = right.normalized_action if isinstance(right, DecisionCandidate) else normalize_decision_text(right)
    left_tokens = set(left_text.split())
    right_tokens = set(right_text.split())
    if not left_tokens or not right_tokens:
        return False
    return left_text == right_text or _jaccard(left_tokens, right_tokens) >= 0.72 or _containment(left_tokens, right_tokens) >= 0.86


def mark_superseded_or_reversed_candidates(candidates: list[DecisionCandidate]) -> list[DecisionCandidate]:
    for candidate in candidates:
        if candidate.reversal_event_ids and candidate.state == "confirmed":
            candidate.state = "reversed"
        for superseded_id in candidate.supersedes_candidate_ids:
            for other in candidates:
                if other.candidate_id == superseded_id and other.state == "confirmed":
                    other.state = "superseded"
    return candidates


def claim_source_belongs_to_active_candidate(source_event_id: str, candidate: DecisionCandidate) -> bool:
    return source_event_id in set(candidate.source_event_ids) and candidate.state in {"confirmed", "implicit_confirmed", "proposed"}


def identify_rejected_alternative_evidence(text: str) -> bool:
    return bool(
        re.search(r"\b(cheaper|alternative|instead of|over|versus|vs)\b", text, re.IGNORECASE)
        and re.search(r"\b(rejected|would take longer|need to rebuild|must be rebuilt|not choosing|not choose)\b", text, re.IGNORECASE)
    )


def resolve_candidate_states(candidates: list[DecisionCandidate]) -> dict[str, Any]:
    mark_superseded_or_reversed_candidates(candidates)
    claim_ownership = _claim_ownership(candidates)
    return {
        "resolver_version": "shadow-0.1",
        "candidate_count": len(candidates),
        "states": {candidate.candidate_id: candidate.state for candidate in candidates},
        "claim_ownership": claim_ownership,
        "excluded_from_active_memory": [
            {
                "claim": ownership["claim"],
                "reason": "claim targets rejected alternative",
                "target_name": ownership["target_name"],
                "target_role": ownership["target_role"],
            }
            for ownership in claim_ownership
            if ownership.get("target_role") == "rejected_alternative"
        ],
        "duplicate_pairs": [
            [left.candidate_id, right.candidate_id]
            for index, left in enumerate(candidates)
            for right in candidates[index + 1 :]
            if are_duplicate_candidates(left, right)
        ],
    }


def _claim_ownership(candidates: list[DecisionCandidate]) -> list[dict[str, Any]]:
    ownership: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for candidate in candidates:
        for claim in candidate.claims:
            if not claim.target_name and not claim.target_role:
                continue
            key = (claim.claim, claim.category, claim.target_name, claim.target_role)
            if key in seen:
                continue
            seen.add(key)
            ownership.append(
                {
                    "claim": claim.claim,
                    "category": claim.category,
                    "target_name": claim.target_name,
                    "target_role": claim.target_role,
                }
            )
    return ownership


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right)


def _containment(left: set[str], right: set[str]) -> float:
    return len(left & right) / min(len(left), len(right))
