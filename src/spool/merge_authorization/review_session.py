from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .memory_update_plan import DecisionMemoryUpdatePlan
from .merge_authorization_result import MergeAuthorizationResult
from .reviewer_actions import ReviewerAction


@dataclass
class ReviewSession:
    evidence_sources: dict[str, str | None]
    merge_authorization_result: MergeAuthorizationResult
    reviewer_actions: list[ReviewerAction] = field(default_factory=list)
    memory_update_plan: DecisionMemoryUpdatePlan = field(default_factory=DecisionMemoryUpdatePlan)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_sources": dict(self.evidence_sources),
            "merge_authorization_result": self.merge_authorization_result.to_dict(),
            "reviewer_actions": [action.to_dict() for action in self.reviewer_actions],
            "memory_update_plan": self.memory_update_plan.to_dict(),
        }
