from __future__ import annotations

from dataclasses import dataclass


VALID_REVIEWER_ACTIONS = {
    "approve_merge",
    "request_repair",
    "reject_merge",
    "approve_candidate_decision",
    "reject_candidate_decision",
    "mark_claim_verified",
    "mark_claim_false",
}

CANDIDATE_ACTIONS = {"approve_candidate_decision", "reject_candidate_decision"}
CLAIM_ACTIONS = {"mark_claim_verified", "mark_claim_false"}


@dataclass(frozen=True)
class ReviewerAction:
    action_type: str
    reviewer: str
    candidate_decision_id: str | None = None
    claim_id: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action_type not in VALID_REVIEWER_ACTIONS:
            raise ValueError(f"Invalid reviewer action: {self.action_type}")
        if not self.reviewer.strip():
            raise ValueError("Reviewer action requires a reviewer.")
        if self.action_type in CANDIDATE_ACTIONS and not self.candidate_decision_id:
            raise ValueError(f"{self.action_type} requires candidate_decision_id.")
        if self.action_type in CLAIM_ACTIONS and not self.claim_id:
            raise ValueError(f"{self.action_type} requires claim_id.")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "action_type": self.action_type,
            "reviewer": self.reviewer,
            "candidate_decision_id": self.candidate_decision_id,
            "claim_id": self.claim_id,
            "reason": self.reason,
        }
