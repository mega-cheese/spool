from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .candidate_decisions import CandidateDecision, extract_candidate_decisions
from .decision_memory import DecisionMemory
from .evidence_coverage import EvidenceCoverage, build_evidence_coverage
from .pr_context import PREvidence, PRChecksEvidence
from .scope_gate import ScopeReport, evaluate_scope, parse_changed_files


CLAIM_VERDICTS = {
    "supported",
    "weakly_supported",
    "unsupported",
    "contradicted",
    "not_applicable",
    "no_relevant_memory",
    "missing_evidence",
    "needs_review",
}

SUBSTANTIVE_CLAIM_ROLES = {
    "implementation_claim",
    "behavior_claim",
    "invariance_claim",
    "test_result_claim",
}


@dataclass(frozen=True)
class SummaryStatement:
    text: str
    source_section: str
    claim_role: str
    inventory_path: str = ""


@dataclass
class TrustClaim:
    claim_id: str
    claim: str
    claim_type: str
    verdict: str
    evidence_target: str
    evidence_quote: str = ""
    reason: str = ""
    claim_role: str = ""
    source_section: str = "Agent Summary"
    verification_state: str = "assessed"
    affects_verdict: bool = False
    evidence_source: str = ""
    independent_corroboration: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "claim_type": self.claim_type,
            "verdict": self.verdict,
            "evidence_target": self.evidence_target,
            "evidence_quote": self.evidence_quote,
            "reason": self.reason,
            "claim_role": self.claim_role or _legacy_claim_role(self),
            "source_section": self.source_section,
            "verification_state": self.verification_state,
            "affects_verdict": self.affects_verdict,
            "evidence_source": self.evidence_source or self.evidence_target,
            "independent_corroboration": self.independent_corroboration,
        }


@dataclass
class ClaimDiagnostic:
    source_index: int
    claim: str
    source: str
    status: str
    claim_type: str
    claim_role: str
    source_section: str
    verification_state: str
    affects_verdict: bool
    evidence_source: str
    independent_corroboration: bool
    evidence_quote: str
    reason: str
    evidence_strength: str
    supporting_evidence: list[dict[str, str]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    evidence_checked: list[str] = field(default_factory=list)
    reviewer_action: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "claim": self.claim,
            "source_index": self.source_index,
            "source": self.source,
            "status": self.status,
            "claim_type": self.claim_type,
            "claim_role": self.claim_role,
            "source_section": self.source_section,
            "verification_state": self.verification_state,
            "affects_verdict": self.affects_verdict,
            "evidence_source": self.evidence_source,
            "independent_corroboration": self.independent_corroboration,
            "evidence_quote": self.evidence_quote,
            "reason": self.reason,
            "why": self.reason,
            "evidence_strength": self.evidence_strength,
            "supporting_evidence": [dict(item) for item in self.supporting_evidence],
            "missing_evidence": list(self.missing_evidence),
            "evidence_checked": list(self.evidence_checked),
            "reviewer_action": self.reviewer_action,
        }


@dataclass
class BuriedDecision:
    decision_id: str
    decision: str
    source: str
    reason: str
    verdict: str = "needs_review"

    def to_dict(self) -> dict[str, str]:
        return {
            "decision_id": self.decision_id,
            "decision": self.decision,
            "source": self.source,
            "reason": self.reason,
            "verdict": self.verdict,
        }


@dataclass
class RepoMemoryCheck:
    memory_id: str
    memory: str
    verdict: str
    reason: str
    evidence_quote: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "memory_id": self.memory_id,
            "memory": self.memory,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence_quote": self.evidence_quote,
        }


@dataclass
class PRTrustReport:
    overall_verdict: str
    scope_report: ScopeReport
    claims: list[TrustClaim] = field(default_factory=list)
    weak_or_missing_evidence: list[str] = field(default_factory=list)
    buried_decisions: list[BuriedDecision] = field(default_factory=list)
    candidate_decisions: list[CandidateDecision] = field(default_factory=list)
    repo_memory_check: list[RepoMemoryCheck] = field(default_factory=list)
    decision_memory: DecisionMemory = field(default_factory=lambda: DecisionMemory(source=None, status="missing"))
    evidence_coverage: EvidenceCoverage = field(default_factory=EvidenceCoverage)
    ci_checks: PRChecksEvidence = field(default_factory=PRChecksEvidence)
    risks: list[str] = field(default_factory=list)
    reviewer_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        matched_decisions = [item.memory_id for item in self.repo_memory_check if item.verdict == "supported"]
        conflicts = [item.memory_id for item in self.repo_memory_check if item.verdict == "contradicted"]
        return {
            "overall_verdict": self.overall_verdict,
            "summary": {
                "changed_files_checked": len(self.scope_report.changed_files),
                "allowed_scope_status": _allowed_scope_status(self.scope_report),
                "supported_claims_count": sum(1 for claim in self.claims if claim.verdict == "supported"),
                "unsupported_claims_count": sum(1 for claim in self.claims if claim.verdict == "unsupported"),
                "unassessed_claims_count": sum(
                    1 for claim in self.claims if claim.verification_state in {"unassessed", "self_reported"}
                ),
                "contradicted_claims_count": sum(1 for claim in self.claims if claim.verdict == "contradicted"),
                "test_evidence_status": _test_evidence_status(self.claims),
                "repo_memory_status": _repo_memory_status(self.repo_memory_check),
                "decision_memory_status": _decision_memory_summary_status(self.decision_memory),
            },
            "scope": self.scope_report.to_dict(),
            "evidence_coverage": self.evidence_coverage.to_dict(),
            "ci_checks": self.ci_checks.to_dict(),
            "agent_claims": [claim.to_dict() for claim in self.claims],
            "claim_diagnostics": [diagnostic.to_dict() for diagnostic in _claim_diagnostics(self.claims)],
            "weak_or_missing_evidence": list(self.weak_or_missing_evidence),
            "candidate_decisions": [decision.to_dict() for decision in self.candidate_decisions],
            "buried_decisions": [decision.to_dict() for decision in self.buried_decisions],
            "decision_memory": self.decision_memory.to_report_dict(
                matched_decisions=matched_decisions,
                conflicts=conflicts,
            ),
            "repo_memory_check": [item.to_dict() for item in self.repo_memory_check],
            "risks": list(self.risks),
            "reviewer_actions": list(self.reviewer_actions),
        }


def analyze_pr_trust(evidence: PREvidence, *, mode: str = "heuristic") -> PRTrustReport:
    if mode != "heuristic":
        raise NotImplementedError("spool_trust v0.9.1 supports only --mode heuristic.")
    changed_files = parse_changed_files(evidence.diff_text)
    evidence_coverage = build_evidence_coverage(evidence, changed_files)
    scope = evaluate_scope(
        evidence.task_text,
        changed_files,
        task_source=evidence.task_path or "not_provided",
        assignment_status=str(evidence_coverage.assignment),
    )
    claims = _extract_and_verify_claims(evidence, changed_files)
    candidate_decisions = extract_candidate_decisions(evidence.agent_summary_text, source="agent_summary")
    buried_decisions = _candidate_decisions_to_buried(candidate_decisions)
    repo_memory_check = _check_repo_memory(evidence)
    weak_or_missing = _weak_or_missing_evidence(claims)
    risks = _risks(scope, claims, buried_decisions, repo_memory_check)
    verdict = _overall_verdict(scope, claims, buried_decisions, repo_memory_check)
    reviewer_actions = _reviewer_actions(verdict, claims, buried_decisions, repo_memory_check)
    return PRTrustReport(
        overall_verdict=verdict,
        scope_report=scope,
        claims=claims,
        weak_or_missing_evidence=weak_or_missing,
        buried_decisions=buried_decisions,
        candidate_decisions=candidate_decisions,
        repo_memory_check=repo_memory_check,
        decision_memory=evidence.decision_memory,
        evidence_coverage=evidence_coverage,
        ci_checks=evidence.ci_checks,
        risks=risks,
        reviewer_actions=reviewer_actions,
    )


def _extract_and_verify_claims(evidence: PREvidence, changed_files: list[object]) -> list[TrustClaim]:
    changed_paths = {getattr(change, "path") for change in changed_files}
    claims: list[TrustClaim] = []
    for statement in _summary_claim_statements(evidence.agent_summary_text):
        line = statement.text
        lower = line.lower()
        if statement.claim_role == "file_inventory":
            claims.append(
                _file_claim(
                    line,
                    statement.inventory_path,
                    changed_paths,
                    evidence.diff_text,
                    claim_role="file_inventory",
                    source_section=statement.source_section,
                )
            )
            continue
        if statement.claim_role == "test_result_claim":
            claims.append(_test_claim(line, evidence, source_section=statement.source_section))
            continue
        paths = _paths_from_text(line)
        code_paths = [path for path in paths if _resolve_changed_path(path, changed_paths) in changed_paths]
        artifact_paths = [path for path in paths if path not in code_paths]
        claim_count = len(claims)
        if _is_behavior_claim(line, artifact_paths):
            claims.append(_behavior_claim(line, evidence, source_section=statement.source_section))
        if code_paths:
            for path in code_paths:
                claims.append(
                    _file_claim(
                        line,
                        path,
                        changed_paths,
                        evidence.diff_text,
                        claim_role=statement.claim_role,
                        source_section=statement.source_section,
                        added=bool(re.search(r"\badded\b", lower)),
                    )
                )
        if len(claims) == claim_count:
            claims.append(_unverified_statement_claim(statement))
    return _dedupe_claims(claims)


def _file_claim(
    line: str,
    path: str,
    changed_paths: set[str],
    diff_text: str,
    *,
    claim_role: str,
    source_section: str,
    added: bool = False,
) -> TrustClaim:
    resolved_path = _resolve_changed_path(path, changed_paths)
    claim_type = "file_added" if added or re.search(r"\b(?:added|created)\b", line, re.IGNORECASE) else "file_modified"
    if resolved_path in changed_paths:
        return _claim(
            line,
            claim_type,
            "diff.patch",
            "supported",
            evidence_quote=_diff_header_for_path(diff_text, resolved_path),
            reason=f"{resolved_path} appears in the PR diff.",
            claim_role=claim_role,
            source_section=source_section,
        )
    return _claim(
        line,
        claim_type,
        "diff.patch",
        "unsupported",
        reason=f"{path} does not appear in the PR diff.",
        claim_role=claim_role,
        source_section=source_section,
    )


def _unverified_statement_claim(statement: SummaryStatement) -> TrustClaim:
    if statement.claim_role in SUBSTANTIVE_CLAIM_ROLES:
        return _claim(
            statement.text,
            statement.claim_role,
            "",
            "not_applicable",
            reason="Spool retained this substantive claim but has no deterministic verifier for this claim form.",
            claim_role=statement.claim_role,
            source_section=statement.source_section,
            verification_state="unassessed",
            affects_verdict=False,
        )
    return _claim(
        statement.text,
        statement.claim_role,
        "agent_summary.md",
        "not_applicable",
        reason="This statement is reviewer context rather than a deterministic implementation claim.",
        claim_role=statement.claim_role,
        source_section=statement.source_section,
        verification_state="metadata",
        affects_verdict=False,
    )


def _is_behavior_claim(line: str, artifact_paths: list[str]) -> bool:
    if not artifact_paths:
        return False
    return bool(
        re.search(
            r"\b(append|appends|add|adds|preserve|preserves|retain|retains|retained|keep|keeps|overwrite|overwrites|replace|replaces|reset|rewrite|persist|save|write|drop|delete|remove)\b",
            line,
            re.IGNORECASE,
        )
    )


def _behavior_claim(line: str, evidence: PREvidence, *, source_section: str = "Agent Summary") -> TrustClaim:
    lower = line.lower()
    diff = evidence.diff_text
    normalized_lower = _normalize(lower)
    if _mentions_overwrite_or_replacement(normalized_lower):
        supported = _diff_shows_overwrite(diff)
        return _claim(
            line,
            "capability_claim",
            "diff.patch",
            "supported" if supported else "needs_review",
            evidence_quote=_first_matching_line(diff, ["json.dumps([action])", "json([action])", "[action]"]),
            reason=(
                "The diff shows review actions being written as a single-action list."
                if supported
                else "No deterministic behavior matcher verified this claim."
            ),
            claim_role="behavior_claim",
            source_section=source_section,
        )
    if _mentions_append_or_history_preservation(normalized_lower):
        supported = _diff_shows_append(diff)
        return _claim(
            line,
            "capability_claim",
            "diff.patch",
            "supported" if supported else "needs_review",
            evidence_quote=_first_matching_line(diff, ["actions.append(action)", "append_review_action"]),
            reason=(
                "The diff shows append behavior for review actions."
                if supported
                else "No deterministic behavior matcher verified this claim."
            ),
            claim_role="behavior_claim",
            source_section=source_section,
        )
    if _mentions_source_evidence_preservation(normalized_lower):
        supported = _diff_shows_source_evidence_preserved(diff)
        return _claim(
            line,
            "capability_claim",
            "diff.patch",
            "supported" if supported else "needs_review",
            evidence_quote=_first_matching_line(diff, ["source_event_ids", "quote", "source"]),
            reason=(
                "The diff shows source evidence fields being retained."
                if supported
                else "No deterministic behavior matcher verified this claim."
            ),
            claim_role="behavior_claim",
            source_section=source_section,
        )
    return _claim(
        line,
        "capability_claim",
        "diff.patch",
        "needs_review",
        reason="No deterministic behavior matcher handled this claim.",
        claim_role="behavior_claim",
        source_section=source_section,
    )


def _test_claim(line: str, evidence: PREvidence, *, source_section: str = "Agent Summary") -> TrustClaim:
    claim_text = _clean_sentence(line)
    if evidence.ci_checks.status == "available" and evidence.ci_checks.total_count > 0:
        if evidence.ci_checks.failed_count:
            failed = ", ".join(evidence.ci_checks.failed_checks)
            return _claim(
                claim_text,
                "test_result_claim",
                "checks.json",
                "contradicted",
                evidence_quote=failed,
                reason=f"The agent says tests passed, but GitHub checks include failing GitHub checks: {failed}.",
                claim_role="test_result_claim",
                source_section=source_section,
                evidence_source="checks.json",
                independent_corroboration=True,
            )
        if evidence.ci_checks.pending_count:
            pending = ", ".join(evidence.ci_checks.pending_checks)
            return _claim(
                claim_text,
                "test_result_claim",
                "checks.json",
                "needs_review",
                evidence_quote=pending,
                reason=f"The agent says tests passed, but pending GitHub checks are still unresolved: {pending}.",
                claim_role="test_result_claim",
                source_section=source_section,
                evidence_source="checks.json",
                independent_corroboration=True,
            )
        if evidence.ci_checks.all_passing:
            return _claim(
                claim_text,
                "test_result_claim",
                "checks.json",
                "supported",
                evidence_quote=_first_check_evidence_line(evidence.ci_checks),
                reason="GitHub checks were fetched and all returned checks are passing.",
                claim_role="test_result_claim",
                source_section=source_section,
                evidence_source="checks.json",
                independent_corroboration=True,
            )
        return _claim(
            claim_text,
            "test_result_claim",
            "checks.json",
            "needs_review",
            reason="GitHub checks were fetched, but their statuses were inconclusive.",
            claim_role="test_result_claim",
            source_section=source_section,
            evidence_source="checks.json",
            independent_corroboration=True,
        )
    if evidence.test_output_source == "pr_body_tests_run" and evidence.has_test_output:
        return _claim(
            claim_text,
            "test_result_claim",
            "pr_body_tests_run",
            "not_applicable",
            evidence_quote=_first_test_evidence_line(evidence.test_output_text),
            reason="The result is stated in the PR body, but no independent test output or GitHub check was available.",
            claim_role="test_result_claim",
            source_section=source_section,
            verification_state="self_reported",
            affects_verdict=False,
            evidence_source="pr_body_tests_run",
            independent_corroboration=False,
        )
    if not evidence.has_test_output:
        return _claim(
            claim_text,
            "test_result_claim",
            "test_output.txt",
            "missing_evidence",
            reason="The agent says tests passed, but no test output was provided.",
            claim_role="test_result_claim",
            source_section=source_section,
            evidence_source="test_output.txt",
            independent_corroboration=False,
        )
    assessment, detail = _assess_test_output(claim_text, evidence.test_output_text)
    evidence_quote = _first_test_evidence_line(evidence.test_output_text)
    if assessment == "supported":
        return _claim(
            claim_text,
            "test_result_claim",
            "test_output.txt",
            "supported",
            evidence_quote=evidence_quote,
            reason=detail,
            claim_role="test_result_claim",
            source_section=source_section,
            evidence_source="test_output.txt",
            independent_corroboration=True,
        )
    if assessment == "contradicted":
        return _claim(
            claim_text,
            "test_result_claim",
            "test_output.txt",
            "contradicted",
            evidence_quote=evidence_quote,
            reason=detail,
            claim_role="test_result_claim",
            source_section=source_section,
            evidence_source="test_output.txt",
            independent_corroboration=True,
        )
    return _claim(
        claim_text,
        "test_result_claim",
        "test_output.txt",
        "not_applicable",
        evidence_quote=evidence_quote,
        reason=detail,
        claim_role="test_result_claim",
        source_section=source_section,
        verification_state="unassessed",
        affects_verdict=False,
        evidence_source="test_output.txt",
        independent_corroboration=False,
    )


def _detect_buried_decisions(summary_text: str) -> list[BuriedDecision]:
    decisions: list[BuriedDecision] = []
    for line in _summary_claim_lines(summary_text):
        if _is_low_value_memory_candidate(line):
            continue
        if _is_durable_buried_decision(line):
            decision = re.sub(r"^\s*going forward,\s*", "", line, flags=re.IGNORECASE).strip()
            decisions.append(
                BuriedDecision(
                    decision_id=_id("decision", decision),
                    decision=decision,
                    source="agent_summary",
                    reason="The agent summary introduced a reusable product or architecture rule.",
                )
            )
    return _dedupe_decisions(decisions)


def _candidate_decisions_to_buried(candidates: list[CandidateDecision]) -> list[BuriedDecision]:
    return [
        BuriedDecision(
            decision_id=candidate.id,
            decision=_legacy_buried_decision_text(candidate.quote),
            source=candidate.source,
            reason=candidate.reason,
            verdict="needs_review",
        )
        for candidate in candidates
    ]


def _legacy_buried_decision_text(quote: str) -> str:
    return re.sub(r"^\s*(going forward|from now on),?\s*", "", quote, flags=re.IGNORECASE).strip().rstrip(".")


def _check_repo_memory(evidence: PREvidence) -> list[RepoMemoryCheck]:
    if evidence.decision_memory.status == "malformed":
        return [
            RepoMemoryCheck(
                memory_id="decision_memory_malformed",
                memory="Approved decision memory is malformed.",
                verdict="needs_review",
                reason="Approved decision memory could not be parsed clearly.",
            )
        ]
    if not evidence.repo_memory_exists or not evidence.repo_memory_items:
        return [
            RepoMemoryCheck(
                memory_id="repo_memory_missing",
                memory="No approved repo memory was provided.",
                verdict="no_relevant_memory",
                reason="Missing approved decision memory is not a failure.",
            )
        ]

    combined = _normalize("\n".join([evidence.agent_summary_text, evidence.diff_text, evidence.task_text]))
    checks: list[RepoMemoryCheck] = []
    for item in evidence.repo_memory_items:
        memory_text = f"{item.title} {item.summary}"
        normalized_memory = _normalize(memory_text)
        if _repo_memory_contradicted(normalized_memory, combined):
            checks.append(
                RepoMemoryCheck(
                    memory_id=item.memory_id,
                    memory=item.title,
                    verdict="contradicted",
                    reason="The PR evidence appears to contradict approved repo memory.",
                    evidence_quote=_first_matching_line(
                        evidence.agent_summary_text + "\n" + evidence.diff_text,
                        ["overwrite", "latest", "json([action])", "automatically approved", "auto approved", "status = 'approved'"],
                    ),
                )
            )
        elif _repo_memory_supported(normalized_memory, combined):
            checks.append(
                RepoMemoryCheck(
                    memory_id=item.memory_id,
                    memory=item.title,
                    verdict="supported",
                    reason="A deterministic rule found positive alignment with this approved repo memory.",
                )
            )
        elif _memory_keywords_overlap(normalized_memory, combined):
            checks.append(
                RepoMemoryCheck(
                    memory_id=item.memory_id,
                    memory=item.title,
                    verdict="needs_review",
                    reason="Repo memory shares vocabulary with the PR, but no deterministic agreement or contradiction rule matched.",
                )
            )
        else:
            checks.append(
                RepoMemoryCheck(
                    memory_id=item.memory_id,
                    memory=item.title,
                    verdict="no_relevant_memory",
                    reason="No relevant evidence matched this approved repo memory item.",
                )
            )
    return checks


def _overall_verdict(
    scope: ScopeReport,
    claims: list[TrustClaim],
    buried_decisions: list[BuriedDecision],
    repo_memory_check: list[RepoMemoryCheck],
) -> str:
    if scope.forbidden_violations or any(item.verdict == "contradicted" for item in repo_memory_check):
        return "stop"
    if _has_sensitive_scope_violation(scope):
        return "stop"
    if any(claim.claim_type == "test_result_claim" and claim.verdict == "contradicted" for claim in claims):
        return "stop"
    if scope.scope_violations or any(claim.verdict == "contradicted" for claim in claims):
        return "repair"
    if scope.scope_risks:
        return "human_review"
    if any(item.verdict == "needs_review" for item in repo_memory_check):
        return "human_review"
    if buried_decisions or any(claim.verdict in {"missing_evidence", "weakly_supported", "needs_review"} for claim in claims):
        return "human_review"
    return "continue"


def _weak_or_missing_evidence(claims: list[TrustClaim]) -> list[str]:
    result: list[str] = []
    for claim in claims:
        if claim.verdict in {"missing_evidence", "weakly_supported", "needs_review"}:
            result.append(claim.reason or claim.claim)
    return result


def _claim_diagnostics(claims: list[TrustClaim]) -> list[ClaimDiagnostic]:
    diagnostics: list[ClaimDiagnostic] = []
    for index, claim in enumerate(claims):
        diagnostics.append(
            ClaimDiagnostic(
                source_index=index,
                claim=claim.claim,
                source="agent_summary",
                status=claim.verdict,
                claim_type=claim.claim_type,
                claim_role=claim.claim_role or _legacy_claim_role(claim),
                source_section=claim.source_section,
                verification_state=claim.verification_state,
                affects_verdict=claim.affects_verdict,
                evidence_source=claim.evidence_source or claim.evidence_target,
                independent_corroboration=claim.independent_corroboration,
                evidence_quote=claim.evidence_quote,
                reason=claim.reason or _default_claim_diagnostic_reason(claim),
                evidence_strength=_claim_evidence_strength(claim),
                supporting_evidence=_claim_supporting_evidence(claim),
                missing_evidence=_claim_missing_evidence(claim),
                evidence_checked=_claim_evidence_checked(claim),
                reviewer_action=_claim_reviewer_action(claim),
            )
        )
    return diagnostics


def _claim_status_rank(status: str) -> int:
    order = {
        "contradicted": 0,
        "unsupported": 1,
        "missing_evidence": 2,
        "weakly_supported": 3,
        "needs_review": 4,
        "supported": 5,
        "not_applicable": 6,
        "no_relevant_memory": 7,
    }
    return order.get(status, 8)


def _claim_evidence_checked(claim: TrustClaim) -> list[str]:
    checked: list[str] = ["agent_summary.md"]
    if claim.claim_type == "test_result_claim":
        checked.extend(["pr_body.md", "test_output.txt", "checks.json"])
    elif claim.claim_type in {"file_added", "file_modified", "capability_claim"}:
        checked.append("diff.patch")
    if claim.evidence_target:
        checked.append(claim.evidence_target)
    return _dedupe(checked)


def _claim_evidence_strength(claim: TrustClaim) -> str:
    if claim.verification_state in {"unassessed", "self_reported"}:
        return "none"
    if claim.verdict in {"unsupported", "missing_evidence"}:
        return "none"
    if claim.verdict == "weakly_supported":
        return "weak"
    if claim.verdict == "needs_review":
        return "weak" if claim.evidence_quote else "none"
    if claim.verdict == "supported":
        if claim.claim_type == "test_result_claim":
            return "corroborated" if _is_agent_reported_test_claim(claim) else "direct"
        if claim.claim_type in {"file_added", "file_modified"}:
            return "corroborated" if claim.claim_role and claim.claim_role != "file_inventory" else "direct"
        if claim.claim_type == "capability_claim":
            return "corroborated"
        return "corroborated"
    if claim.verdict == "contradicted":
        return "direct" if claim.evidence_quote else "corroborated"
    return "none"


def _claim_supporting_evidence(claim: TrustClaim) -> list[dict[str, str]]:
    if claim.verdict in {"unsupported", "missing_evidence"}:
        return []
    path = _claim_primary_path(claim)
    if claim.claim_type in {"file_added", "file_modified"} and path and claim.verdict == "supported":
        return [
            {
                "type": "changed_file",
                "path": path,
                "explanation": "The claimed path appears in the PR diff.",
            }
        ]
    if claim.claim_type == "test_result_claim" and (
        claim.verdict in {"supported", "contradicted", "needs_review"}
        or claim.verification_state in {"unassessed", "self_reported"}
    ):
        if claim.evidence_target == "checks.json":
            return [
                {
                    "type": "ci_check",
                    "path": "checks.json",
                    "quote": claim.evidence_quote,
                    "explanation": (
                        "Fetched GitHub check evidence supports the test claim."
                        if claim.verdict == "supported"
                        else "Fetched GitHub check evidence does not directly support the test claim."
                    ),
                }
            ]
        if _is_agent_reported_test_claim(claim):
            return [
                {
                    "type": "pr_body_test_result",
                    "path": "pr_body",
                    "quote": claim.evidence_quote,
                    "explanation": f"The PR body reports that {claim.claim.rstrip('.')}.",
                }
            ]
        return [
            {
                "type": "test_output",
                "path": "test_output.txt",
                "quote": claim.evidence_quote,
                "explanation": (
                    "Captured test output contains a passing result."
                    if claim.verdict == "supported"
                    else (
                        "Captured test output affirmatively contradicts the test claim."
                        if claim.verdict == "contradicted"
                        else "Captured test output is incomplete or unrecognized, so it cannot confirm or contradict the claim."
                    )
                ),
            }
        ]
    if claim.claim_type == "capability_claim" and claim.evidence_quote:
        return [
            {
                "type": "diff_pattern",
                "path": claim.evidence_target,
                "quote": claim.evidence_quote,
                "explanation": "A deterministic diff pattern supports this behavior claim.",
            }
        ]
    if claim.evidence_quote:
        return [
            {
                "type": "evidence_quote",
                "path": claim.evidence_target,
                "quote": claim.evidence_quote,
                "explanation": "Deterministic evidence matched this claim.",
            }
        ]
    return []


def _claim_missing_evidence(claim: TrustClaim) -> list[str]:
    if claim.verdict == "unsupported":
        path = _claim_primary_path(claim)
        if path:
            return [f"No changed file matching `{path}` was found in the PR diff."]
        return ["No deterministic supporting evidence was found for this claim."]
    if claim.verdict == "missing_evidence":
        if claim.claim_type == "test_result_claim":
            return ["Test output was not provided.", "GitHub checks were not available as supporting evidence."]
        return [claim.reason or "Required evidence for this claim was not provided."]
    if claim.verdict in {"weakly_supported", "needs_review"} and not claim.evidence_quote:
        return ["No exact deterministic evidence was found for this claim."]
    return []


def _claim_reviewer_action(claim: TrustClaim) -> str:
    if claim.verification_state == "self_reported":
        return "Confirm the test run independently before merge."
    if claim.verification_state == "unassessed":
        return "Review the relevant diff and tests manually."
    if claim.verification_state == "metadata":
        return "No claim-specific action required."
    if claim.verdict == "supported":
        if _claim_evidence_strength(claim) == "direct":
            return "No claim-specific action required."
        return "No claim-specific action required, but the support is deterministic rather than an exact proof."
    if claim.verdict == "unsupported":
        return "Ask the agent to point to the supporting diff or revise/remove this claim."
    if claim.verdict == "missing_evidence":
        return "Ask the agent to provide the missing evidence or revise this claim."
    if claim.verdict == "weakly_supported":
        return "Inspect the referenced evidence and ask the agent to strengthen this claim if needed."
    if claim.verdict == "needs_review":
        return "Review this claim against the PR evidence before merging."
    return "Review this claim against the PR evidence."


def _claim_primary_path(claim: TrustClaim) -> str:
    match = re.search(r"([\w./-]+\.[A-Za-z0-9]+)\s+(?:appears|does not appear)", claim.reason)
    if match:
        return match.group(1)
    paths = _paths_from_text(claim.claim)
    if paths:
        return paths[0]
    return ""


def _is_agent_reported_test_claim(claim: TrustClaim) -> bool:
    return claim.claim_type == "test_result_claim" and claim.evidence_target == "pr_body_tests_run"


def _default_claim_diagnostic_reason(claim: TrustClaim) -> str:
    if claim.verdict == "unsupported":
        return "No deterministic PR evidence supported this agent claim."
    if claim.verdict == "missing_evidence":
        return "Required evidence for this claim was not provided."
    if claim.verdict == "weakly_supported":
        return "Only weak deterministic evidence supported this claim."
    if claim.verdict == "needs_review":
        return "Spool could not deterministically verify this claim."
    return claim.claim


def _risks(
    scope: ScopeReport,
    claims: list[TrustClaim],
    buried_decisions: list[BuriedDecision],
    repo_memory_check: list[RepoMemoryCheck],
) -> list[str]:
    risks: list[str] = []
    risks.extend(scope.scope_risks)
    if scope.scope_violations:
        risks.append(f"Changed files outside the approved task boundary: {', '.join(scope.scope_violations)}.")
    if scope.forbidden_violations:
        risks.append(f"Changed forbidden paths: {', '.join(scope.forbidden_violations)}.")
    if any(claim.verdict == "missing_evidence" for claim in claims):
        risks.append("Validation claims are missing supporting test output.")
    if buried_decisions:
        risks.append(
            "This PR proposes project-level decisions that may affect future Spool behavior. "
            "A reviewer should approve or reject them before they are added to repo memory."
        )
    if any(item.verdict == "contradicted" for item in repo_memory_check):
        risks.append("The PR appears to conflict with approved repo memory.")
    if any(item.verdict == "needs_review" for item in repo_memory_check):
        risks.append("Approved repo memory has unclear relationship to this PR and needs review.")
    return risks


def _reviewer_actions(
    verdict: str,
    claims: list[TrustClaim],
    decisions: list[BuriedDecision],
    repo_memory: list[RepoMemoryCheck],
) -> list[str]:
    actions = []
    contradicted_test_claims = [
        claim for claim in claims if claim.claim_type == "test_result_claim" and claim.verdict == "contradicted"
    ]
    for claim in contradicted_test_claims:
        actions.append(_contradicted_test_reviewer_action(claim))
    if verdict == "continue":
        actions.append("continue")
    elif verdict == "repair":
        actions.append("request repair")
    elif verdict == "stop":
        actions.append("human review required")
        if not contradicted_test_claims:
            actions.append("stop merge until conflict or violation is resolved")
    else:
        actions.append("human review required")
    if decisions:
        actions.append("approve or reject proposed project-level decisions before adding them to repo memory")
    if any(item.verdict == "contradicted" for item in repo_memory):
        actions.append("resolve repo memory conflict")
    return actions


def _allowed_scope_status(scope: ScopeReport) -> str:
    if scope.assignment_status in {"not_provided", "empty", "missing"}:
        return "partial_assignment_review"
    if scope.forbidden_violations:
        return "forbidden_violation"
    if scope.scope_violations:
        return "outside_allowed_scope"
    if scope.risky_expansions:
        return "risky_expansion_review"
    if scope.scope_risks:
        return "unbounded_or_ambiguous"
    if scope.allowed_files:
        return "within_allowed_scope"
    return "not_bounded"


def _test_evidence_status(claims: list[TrustClaim]) -> str:
    test_claims = [claim for claim in claims if claim.claim_type == "test_result_claim"]
    if not test_claims:
        return "not_claimed"
    if any(claim.verdict == "contradicted" for claim in test_claims):
        return "contradicted"
    if any(claim.verdict == "missing_evidence" for claim in test_claims):
        return "missing"
    if all(claim.verdict == "supported" for claim in test_claims):
        return "supported"
    return "needs_review"


def _repo_memory_status(items: list[RepoMemoryCheck]) -> str:
    if any(item.verdict == "contradicted" for item in items):
        return "contradicted"
    if any(item.verdict == "needs_review" for item in items):
        return "needs_review"
    if any(item.verdict == "supported" for item in items):
        return "supported"
    return "no_relevant_memory"


def _decision_memory_summary_status(memory: DecisionMemory) -> str:
    if memory.status == "available":
        return "configured"
    if memory.status in {"missing", "malformed"}:
        return "unavailable"
    return "not_configured"


def _summary_claim_lines(text: str) -> list[str]:
    return [statement.text for statement in _summary_claim_statements(text)]


def _summary_claim_statements(text: str) -> list[SummaryStatement]:
    result: list[SummaryStatement] = []
    source_section = "Agent Summary"
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        heading = re.fullmatch(r"#{1,6}\s+(.+?)(?:\s+#+)?", stripped)
        if heading:
            source_section = heading.group(1).strip()
            continue
        section_label = re.fullmatch(r"([A-Za-z][A-Za-z0-9 /&_-]{0,60}):", stripped)
        if section_label:
            source_section = section_label.group(1).strip()
            continue
        stripped = re.sub(r"^(?:[-*]|\d+\.)\s+", "", stripped).strip()
        if not stripped:
            continue
        text_value = _clean_sentence(stripped)
        inventory_path = _file_inventory_path(text_value)
        result.append(
            SummaryStatement(
                text=text_value,
                source_section=source_section,
                claim_role=_classify_claim_role(text_value, source_section, inventory_path=inventory_path),
                inventory_path=inventory_path,
            )
        )
    return result


def _classify_claim_role(text: str, source_section: str, *, inventory_path: str = "") -> str:
    if inventory_path:
        return "file_inventory"
    lower = text.lower()
    section = source_section.lower()
    if "limitation" in section or re.match(r"^(?:remaining\s+)?limitation\b", lower):
        return "limitation"
    if re.search(r"\b(process|notes?|workflow)\b", section) or _is_process_statement(lower):
        return "process_claim"
    if _is_test_result_claim(text):
        return "test_result_claim"
    if re.search(r"\b(remains? unchanged|is unchanged|are unchanged|no changes? to|same as before)\b", lower):
        return "invariance_claim"
    if re.match(r"^(?:added|updated|modified|implemented|introduced|created)\b", lower):
        return "implementation_claim"
    if re.search(
        r"\b(preserve|preserves|corroborate|corroborated|rank|ranks|cap|caps|render|renders|classify|classifies|exclude|excludes|prevent|prevents|load|loads|match|matches)\b",
        lower,
    ):
        return "behavior_claim"
    return "implementation_claim"


def _is_process_statement(lower_text: str) -> bool:
    return bool(
        re.search(r"\b(no commit|not committed|no push|not pushed|dry run|manually verified|reviewed the diff)\b", lower_text)
    )


def _is_test_pass_claim(text: str) -> bool:
    return bool(re.search(r"\btests?\s+(?:passed|pass|are passing)\b", text, re.IGNORECASE))


def _is_test_result_claim(text: str) -> bool:
    if _is_test_pass_claim(text):
        return True
    return bool(
        re.search(r"\bran\b.*\btests?\b.*\b(?:ok|pass(?:ed)?)\b", text, re.IGNORECASE)
        or re.search(r"\b\d+\s+tests?\b.*\b(?:ok|pass(?:ed)?)\b", text, re.IGNORECASE)
    )


def _test_output_passed(text: str) -> bool:
    return bool(
        re.search(
            r"(^|\n)\s*OK\s*(\n|$)|\b\d+\s+(?:tests?\s+)?passed\b|\bPASS\b|\bran\b.*\btests?\b.*\bOK\b",
            text,
            re.IGNORECASE,
        )
    )


def _assess_test_output(claim: str, text: str) -> tuple[str, str]:
    failure_count = _reported_failure_count(text)
    if failure_count:
        noun = "failure" if failure_count == 1 else "failures"
        return "contradicted", f"The claim says tests passed, but captured output reports {failure_count} {noun}."
    error_count = _reported_error_count(text)
    if error_count:
        noun = "error" if error_count == 1 else "errors"
        return "contradicted", f"The claim says tests passed, but captured output reports {error_count} {noun}."
    nonzero_result = re.search(r"\b(?:exit|return)\s+(?:code|status)?\s*[:=]?\s*([1-9]\d*)\b", text, re.IGNORECASE)
    if nonzero_result:
        return "contradicted", f"The claim says tests passed, but captured output reports nonzero result {nonzero_result.group(1)}."
    if re.search(r"(^|\n)\s*(?:FAILED|FAIL|ERROR)\b", text, re.IGNORECASE):
        return "contradicted", "The claim says tests passed, but captured output explicitly reports failure."

    claimed_total = _claimed_test_total(claim)
    observed_total = _reported_passed_total(text)
    if claimed_total is not None and observed_total is not None and claimed_total != observed_total:
        return (
            "contradicted",
            f"The claim reports {claimed_total} tests passed, but captured output reports {observed_total} passed.",
        )
    if _test_output_passed(text):
        return "supported", "The captured test output explicitly reports a passing result."
    return (
        "unassessed",
        "Captured test output was provided, but it does not contain enough information to confirm or contradict the test claim.",
    )


def _claimed_test_total(text: str) -> int | None:
    match = re.search(r"\b(\d+)\s+tests?\s+(?:passed|pass)\b", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _reported_passed_total(text: str) -> int | None:
    match = re.search(r"\b(\d+)\s+(?:tests?\s+)?passed\b", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    ran_match = re.search(r"\bran\s+(\d+)\s+tests?\b[\s\S]*?(?:^|\n)\s*OK\s*(?:\n|$)", text, re.IGNORECASE)
    return int(ran_match.group(1)) if ran_match else None


def _reported_failure_count(text: str) -> int:
    counts = [
        int(value)
        for pattern in (r"\bfailures?\s*[=:]\s*(\d+)\b", r"\b(\d+)\s+(?:failed|failures?)\b")
        for value in re.findall(pattern, text, re.IGNORECASE)
    ]
    return max(counts, default=0)


def _reported_error_count(text: str) -> int:
    counts = [
        int(value)
        for pattern in (r"\berrors?\s*[=:]\s*(\d+)\b", r"\b(\d+)\s+errors?\b")
        for value in re.findall(pattern, text, re.IGNORECASE)
    ]
    return max(counts, default=0)


def _contradicted_test_reviewer_action(claim: TrustClaim) -> str:
    conflict = claim.reason.split(", but ", 1)[-1].rstrip(".")
    conflict = re.sub(r"^captured output reports\b", "captured output reporting", conflict, flags=re.IGNORECASE)
    conflict = re.sub(r"^the captured output reports\b", "captured output reporting", conflict, flags=re.IGNORECASE)
    return f"Stop: “{claim.claim}” conflicts with {conflict}."


def _first_check_evidence_line(checks: PRChecksEvidence) -> str:
    if not checks.checks:
        return ""
    first = checks.checks[0]
    status = first.conclusion or first.state
    return f"{first.name}: {status}".strip()


def _paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    inventory_path = _file_inventory_path(text)
    if inventory_path:
        paths.append(inventory_path)
    paths.extend(match for match in re.findall(r"`([^`]+)`", text) if _is_repo_file_path(_strip_line_reference(match)))
    paths.extend(
        label
        for label in re.findall(r"\[([^\]]+)\]\([^)]+\)", text)
        if _is_repo_file_path(_strip_line_reference(label.strip("`")))
    )
    paths.extend(
        re.findall(
            r"(?<![A-Za-z0-9_./-])(?:\.?[A-Za-z0-9_-]+/)*\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?![A-Za-z0-9_/-])",
            text,
        )
    )
    return _dedupe([_strip_line_reference(path.strip().strip("`")) for path in paths if path.strip()])


def _file_inventory_path(text: str) -> str:
    candidate = text.strip()
    if candidate.endswith("."):
        candidate = candidate[:-1].rstrip()
    candidate = re.sub(r"^(?:added|modified|updated|created|deleted|removed)\s*:?\s+", "", candidate, flags=re.IGNORECASE)
    link = re.fullmatch(r"\[([^\]]+)\]\([^)]+\)", candidate)
    if link:
        candidate = link.group(1).strip()
    candidate = _strip_line_reference(candidate.strip("`"))
    return candidate if _is_repo_file_path(candidate) else ""


def _strip_line_reference(path: str) -> str:
    return re.sub(
        r"(?:#L\d+(?:-L\d+)?|:\d+(?::\d+)?|\s+\(?lines?\s+\d+(?:-\d+)?\)?)$",
        "",
        path.strip(),
        flags=re.IGNORECASE,
    ).strip()


def _is_repo_file_path(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:\.?[A-Za-z0-9_-]+/)*\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+",
            value,
        )
    )


def _resolve_changed_path(path: str, changed_paths: set[str]) -> str:
    clean_path = _strip_line_reference(path).removeprefix("./")
    if clean_path in changed_paths:
        return clean_path
    matches = sorted(changed for changed in changed_paths if changed.endswith(f"/{clean_path}"))
    return matches[0] if len(matches) == 1 else clean_path


def _is_low_value_memory_candidate(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\b(added|updated|changed|modified)\s+`?[\w./-]+\.(?:py|md|json)`?", lower):
        return True
    if "tests passed" in lower or "readme.md was updated" in lower:
        return True
    if re.search(r"\btests?\s+should\s+pass\b", lower):
        return True
    if re.search(r"\bshould\s+(?:return|handle|accept|raise|parse|render|call)\b", lower):
        return True
    return False


def _is_durable_buried_decision(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\bgoing forward\b", lower):
        return True
    if re.search(r"\b(?:must|should)\s+(?:never|not)\b", lower) and _mentions_durable_domain(lower):
        return True
    if re.search(r"\b(?:are|is)\s+append[- ]only\b", lower):
        return True
    if re.search(r"\bdefault behavior\s+is\s+to\b", lower):
        return True
    if re.search(r"\b(policy|repo memory|approved memory|candidate memory|architecture|product behavior)\b", lower) and re.search(r"\b(must|should|default|rule)\b", lower):
        return True
    return False


def _mentions_durable_domain(lower_text: str) -> bool:
    return bool(
        re.search(
            r"\b(candidate memory|approved memory|review actions?|repo memory|models?|image upload|default behavior|policy|architecture)\b",
            lower_text,
        )
    )


def _mentions_overwrite_or_replacement(normalized_text: str) -> bool:
    return bool(re.search(r"\b(overwrite|overwrites|replace|replaces|reset|rewrite|rewrites)\b", normalized_text))


def _mentions_append_or_history_preservation(normalized_text: str) -> bool:
    if re.search(r"\b(append|appends|add|adds|persist|persists)\b", normalized_text):
        return True
    return bool(re.search(r"\bwithout replacing\b|\bpreserve history\b|\bpreserves history\b|\bkeep history\b|\bkeeps history\b", normalized_text))


def _mentions_source_evidence_preservation(normalized_text: str) -> bool:
    return bool(
        re.search(r"\b(preserve|preserves|retain|retains|retained|keep|keeps)\b", normalized_text)
        and re.search(r"\b(source evidence|source|quote|source event ids)\b", normalized_text)
    )


def _diff_shows_append(diff_text: str) -> bool:
    return bool(re.search(r"\bactions\.append\(|\bappend_review_action\b", diff_text))


def _diff_shows_overwrite(diff_text: str) -> bool:
    return bool(re.search(r"json\.dumps\(\s*\[action\]\s*\)|json\(\s*\[action\]\s*\)|write_text\([^)]*\[action\]", diff_text))


def _diff_shows_source_evidence_preserved(diff_text: str) -> bool:
    normalized = _normalize(diff_text)
    has_source_field = any(term in normalized for term in ("source event ids", "source_event_ids", "quote", "source evidence"))
    has_preserve_signal = bool(re.search(r"\b(preserve|retain|keep|copy|old memory|existing)\b", normalized))
    return has_source_field and has_preserve_signal


def _repo_memory_contradicted(normalized_memory: str, combined: str) -> bool:
    if "append only" in normalized_memory and re.search(r"\boverwrite|replace|reset|rewrite|only the latest|json \[ action \]", combined):
        return True
    if re.search(r"candidate memory.*(?:must not|never|not).*auto", normalized_memory):
        return bool(re.search(r"candidate memory.*(?:automatically|auto).*approved|confidence.*approved|status approved", combined))
    return False


def _repo_memory_supported(normalized_memory: str, combined: str) -> bool:
    if "append only" in normalized_memory:
        return bool(re.search(r"\bappend review action\b|\bactions append\b|\bwithout replacing\b|\bpreserve history\b", combined))
    if re.search(r"candidate memory.*(?:must not|never|not).*auto", normalized_memory):
        return bool(re.search(r"candidate memory.*(?:does not|must not|never|not).*approved automatically|remain pending|explicit review approval", combined))
    return False


def _has_sensitive_scope_violation(scope: ScopeReport) -> bool:
    return any(_is_sensitive_path(path) for path in scope.scope_violations + scope.forbidden_violations)


def _is_sensitive_path(path: str) -> bool:
    return bool(re.search(r"\b(auth|permission|billing|payment|migration|schema|security|env|secrets|config|lockfile)\b", path, re.IGNORECASE))


def _claim(
    text: str,
    claim_type: str,
    evidence_target: str,
    verdict: str,
    *,
    evidence_quote: str = "",
    reason: str = "",
    claim_role: str = "",
    source_section: str = "Agent Summary",
    verification_state: str = "assessed",
    affects_verdict: bool | None = None,
    evidence_source: str = "",
    independent_corroboration: bool = False,
) -> TrustClaim:
    claim = _clean_sentence(text)
    return TrustClaim(
        claim_id=_id(claim_type, claim),
        claim=claim,
        claim_type=claim_type,
        verdict=verdict if verdict in CLAIM_VERDICTS else "needs_review",
        evidence_target=evidence_target,
        evidence_quote=evidence_quote,
        reason=reason,
        claim_role=claim_role,
        source_section=source_section,
        verification_state=verification_state,
        affects_verdict=(
            verdict in {"contradicted", "missing_evidence", "weakly_supported", "needs_review"}
            if affects_verdict is None
            else affects_verdict
        ),
        evidence_source=evidence_source or evidence_target,
        independent_corroboration=independent_corroboration,
    )


def _legacy_claim_role(claim: TrustClaim) -> str:
    if claim.claim_type == "test_result_claim":
        return "test_result_claim"
    if claim.claim_type == "capability_claim":
        return "behavior_claim"
    if claim.claim_type in {"file_added", "file_modified"}:
        return "implementation_claim"
    if claim.claim_type in SUBSTANTIVE_CLAIM_ROLES:
        return claim.claim_type
    return "implementation_claim"


def _diff_header_for_path(diff_text: str, path: str) -> str:
    for line in diff_text.splitlines():
        if line.startswith("diff --git ") and f" b/{path}" in line:
            return line
    return path


def _first_test_evidence_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if re.search(
            r"^OK$|\b\d+\s+(?:tests?\s+)?passed\b|\bPASS\b|\bran\b.*\btests?\b.*\bOK\b|"
            r"\b(?:FAILED|FAIL|ERROR)\b|\b(?:failures?|errors?)\s*[=:]\s*[1-9]\d*\b",
            stripped,
            re.IGNORECASE,
        ):
            return stripped
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _first_matching_line(text: str, terms: list[str]) -> str:
    normalized_terms = [_normalize(term) for term in terms]
    for line in text.splitlines():
        normalized_line = _normalize(line)
        if any(term and term in normalized_line for term in normalized_terms):
            return line.strip()
    return ""


def _memory_keywords_overlap(memory: str, combined: str) -> bool:
    keywords = [word for word in memory.split() if len(word) > 5]
    return any(keyword in combined for keyword in keywords[:4])


def _clean_sentence(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return ""
    return clean if clean.endswith((".", "?", "!")) else f"{clean}."


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(text).lower())).strip()


def _id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _dedupe_claims(claims: list[TrustClaim]) -> list[TrustClaim]:
    result: list[TrustClaim] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        key = (claim.claim_type, _normalize(claim.claim))
        if key in seen:
            continue
        seen.add(key)
        result.append(claim)
    return result


def _dedupe_decisions(decisions: list[BuriedDecision]) -> list[BuriedDecision]:
    result: list[BuriedDecision] = []
    seen: set[str] = set()
    for decision in decisions:
        key = _normalize(decision.decision)
        if key in seen:
            continue
        seen.add(key)
        result.append(decision)
    return result
