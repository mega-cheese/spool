"""Merge authorization primitives for AI-generated pull requests."""

from .candidate_decisions import CandidateDecision, extract_candidate_decisions
from .check_pr import PRCheckResult, PRIngestionError, run_pr_check
from .codex_prompt import render_codex_prompt
from .merge_authorization import PRTrustReport, analyze_pr_trust
from .merge_authorization_report import write_trust_report
from .pr_body_sections import PRBodySections, parse_pr_body_sections
from .pr_comment import PRCommentError, load_report as load_trust_report, render_pr_comment
from .pr_context import PREvidence, load_pr_evidence
from .scope_gate import ScopeReport, evaluate_scope, parse_changed_files
from .decision_memory import ApprovedDecision, DecisionMemory, load_approved_decisions
from .evidence_coverage import EvidenceCoverage, build_evidence_coverage
from .init_repo import init_repo
from .memory_update_plan import DecisionMemoryUpdatePlan, build_memory_update_plan
from .memory_update_writer import write_memory_update_plan
from .merge_authorization_result import MergeAuthorizationResult
from .review_session import ReviewSession
from .reviewer_actions import ReviewerAction

__all__ = [
    "ApprovedDecision",
    "CandidateDecision",
    "DecisionMemory",
    "DecisionMemoryUpdatePlan",
    "EvidenceCoverage",
    "MergeAuthorizationResult",
    "PREvidence",
    "PRCheckResult",
    "PRBodySections",
    "PRCommentError",
    "PRIngestionError",
    "PRTrustReport",
    "ReviewSession",
    "ReviewerAction",
    "ScopeReport",
    "analyze_pr_trust",
    "build_evidence_coverage",
    "build_memory_update_plan",
    "evaluate_scope",
    "extract_candidate_decisions",
    "init_repo",
    "load_approved_decisions",
    "load_pr_evidence",
    "load_trust_report",
    "parse_changed_files",
    "parse_pr_body_sections",
    "render_codex_prompt",
    "render_pr_comment",
    "run_pr_check",
    "write_trust_report",
    "write_memory_update_plan",
]
