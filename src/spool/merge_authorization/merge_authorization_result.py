from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MergeAuthorizationResult:
    overall_verdict: str
    evidence_coverage: dict[str, Any] = field(default_factory=dict)
    agent_claims: list[dict[str, Any]] = field(default_factory=list)
    weak_or_missing_evidence: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    decision_memory: dict[str, Any] = field(default_factory=dict)
    candidate_decisions: list[dict[str, Any]] = field(default_factory=list)
    reviewer_actions: list[str] = field(default_factory=list)
    raw_report: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_report(cls, report: Any) -> "MergeAuthorizationResult":
        payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MergeAuthorizationResult":
        return cls(
            overall_verdict=str(payload.get("overall_verdict") or ""),
            evidence_coverage=dict(payload.get("evidence_coverage") or {}),
            agent_claims=list(payload.get("agent_claims") or []),
            weak_or_missing_evidence=list(payload.get("weak_or_missing_evidence") or []),
            scope=dict(payload.get("scope") or {}),
            decision_memory=dict(payload.get("decision_memory") or {}),
            candidate_decisions=list(payload.get("candidate_decisions") or []),
            reviewer_actions=list(payload.get("reviewer_actions") or []),
            raw_report=dict(payload),
        )

    def candidate_by_id(self) -> dict[str, dict[str, Any]]:
        return {
            str(candidate.get("id")): dict(candidate)
            for candidate in self.candidate_decisions
            if candidate.get("id")
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_verdict": self.overall_verdict,
            "evidence_coverage": dict(self.evidence_coverage),
            "agent_claims": list(self.agent_claims),
            "weak_or_missing_evidence": list(self.weak_or_missing_evidence),
            "scope": dict(self.scope),
            "decision_memory": dict(self.decision_memory),
            "candidate_decisions": list(self.candidate_decisions),
            "reviewer_actions": list(self.reviewer_actions),
        }
