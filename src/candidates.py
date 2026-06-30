from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


CandidateState = Literal[
    "confirmed",
    "implicit_confirmed",
    "proposed",
    "unresolved",
    "rejected",
    "superseded",
    "reversed",
    "needs_review",
]

EvidenceSignalType = Literal[
    "proposal_signal",
    "approval_signal",
    "finality_signal",
    "owner_signal",
    "deadline_signal",
    "timeline_signal",
    "objection_signal",
    "assumption_signal",
    "tradeoff_signal",
    "rejected_alternative_signal",
    "reversal_signal",
    "unresolved_signal",
    "source_support",
]


@dataclass
class EvidenceSignal:
    signal_type: EvidenceSignalType
    source_event_ids: list[str] = field(default_factory=list)
    confidence: float = 0.75
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateClaim:
    claim: str
    category: str
    source_event_ids: list[str] = field(default_factory=list)
    support_level: str = "direct"
    confidence: float = 0.75
    target_name: str | None = None
    target_role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionCandidate:
    candidate_id: str
    proposed_action: str
    normalized_action: str
    state: CandidateState
    confidence: float
    source_event_ids: list[str] = field(default_factory=list)
    decision_event_ids: list[str] = field(default_factory=list)
    proposal_event_ids: list[str] = field(default_factory=list)
    approval_event_ids: list[str] = field(default_factory=list)
    rejection_event_ids: list[str] = field(default_factory=list)
    reversal_event_ids: list[str] = field(default_factory=list)
    supersedes_candidate_ids: list[str] = field(default_factory=list)
    claims: list[CandidateClaim] = field(default_factory=list)
    evidence_signals: list[EvidenceSignal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["claims"] = [claim.to_dict() for claim in self.claims]
        result["evidence_signals"] = [signal.to_dict() for signal in self.evidence_signals]
        return result
