"""Merge authorization primitives for AI-generated pull requests."""

from .merge_authorization import PRTrustReport, analyze_pr_trust
from .merge_authorization_report import write_trust_report
from .pr_context import PREvidence, load_pr_evidence
from .scope_gate import ScopeReport, evaluate_scope, parse_changed_files
from .decision_memory import ApprovedDecision, DecisionMemory, load_approved_decisions

__all__ = [
    "ApprovedDecision",
    "DecisionMemory",
    "PREvidence",
    "PRTrustReport",
    "ScopeReport",
    "analyze_pr_trust",
    "evaluate_scope",
    "load_approved_decisions",
    "load_pr_evidence",
    "parse_changed_files",
    "write_trust_report",
]
