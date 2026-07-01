from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_objects import MemoryObject


EXPECTED_SECTIONS = [
    "Original Request",
    "Agent Implementation Summary",
    "Claimed Capabilities",
    "Validation Claimed by Agent",
    "Existing Product Rules / Approved Context",
    "Git Stat",
    "Git Diff",
    "Questions for Spool Audit",
    "Expected Useful Spool Output",
]

SECTION_ALIASES = {
    "original request": "Original Request",
    "agent implementation summary": "Agent Implementation Summary",
    "claimed capabilities": "Claimed Capabilities",
    "validation claimed by agent": "Validation Claimed by Agent",
    "validation": "Validation Claimed by Agent",
    "existing product rules approved context": "Existing Product Rules / Approved Context",
    "existing product rules": "Existing Product Rules / Approved Context",
    "approved context": "Existing Product Rules / Approved Context",
    "git stat": "Git Stat",
    "git show stat": "Git Stat",
    "git diff": "Git Diff",
    "git show patch": "Git Diff",
    "questions for spool audit": "Questions for Spool Audit",
    "questions for audit": "Questions for Spool Audit",
    "expected useful spool output": "Expected Useful Spool Output",
    "candidate technical decisions risks open questions": "Candidate Technical Decisions / Risks / Open Questions",
    "technical decisions risks open questions": "Candidate Technical Decisions / Risks / Open Questions",
    "candidate technical decisions": "Candidate Technical Decisions",
    "candidate implementation claims": "Candidate Implementation Claims",
    "candidate risks": "Candidate Risks",
    "candidate open questions": "Candidate Open Questions",
    "review checklist": "Review Checklist",
    "files changed according to agent": "Agent Implementation Summary",
}

STRONG_PHRASES = [
    "pseudo-pr",
    "agent-change audit",
    "git show --patch",
    "git show --stat",
    "implementation claims",
    "scope adherence",
    "unsupported or contradicted claims",
]

FORBIDDEN_SCOPE_TERMS = [
    "github integration",
    "browser extension",
    "database",
    "auth",
    "cloud sync",
    "slack api",
    "drive api",
    "gmail integration",
    "kanban",
    "reminder",
    "task tracker",
    "auto-approve",
    "automatic approval",
]
REVIEW_ACTIONS = [
    "approve",
    "reject",
    "edit",
    "mark_needs_review",
    "mark_resolved",
    "mark_unresolved",
    "mark_superseded",
    "mark_stale",
]
LOW_QUALITY_CLAIM_RE = re.compile(
    r"^(?:[-*_]+|"
    r"(?:[\w./-]+\.(?:py|json|md)|README)|"
    r"approve|reject|edit|resolved|unresolved|superseded|stale|"
    r"mark_needs_review|mark_resolved|mark_unresolved|mark_superseded|mark_stale"
    r")\.?$",
    re.IGNORECASE,
)


@dataclass
class AgentChangePacket:
    packet_id: str
    title: str
    original_request: str = ""
    agent_implementation_summary: str = ""
    claimed_capabilities: str = ""
    validation_claimed: str = ""
    existing_product_rules: str = ""
    git_stat: str = ""
    git_diff: str = ""
    questions_for_audit: str = ""
    expected_useful_output: str = ""
    sections_found: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    detector_signals: list[str] = field(default_factory=list)
    section_event_ids: dict[str, list[str]] = field(default_factory=dict)
    section_sources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "title": self.title,
            "original_request": self.original_request,
            "agent_implementation_summary": self.agent_implementation_summary,
            "claimed_capabilities": self.claimed_capabilities,
            "validation_claimed": self.validation_claimed,
            "existing_product_rules": self.existing_product_rules,
            "git_stat": self.git_stat,
            "git_diff": self.git_diff,
            "questions_for_audit": self.questions_for_audit,
            "expected_useful_output": self.expected_useful_output,
            "sections_found": list(self.sections_found),
            "missing_sections": list(self.missing_sections),
        }


@dataclass
class AgentClaim:
    claim_id: str
    claim: str
    claim_type: str
    source_section: str
    evidence_sections: list[str] = field(default_factory=list)
    verdict: str = "needs_review"
    confidence: float = 0.0
    reason: str = ""
    source_quote: str = ""
    evidence_quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentAuditReport:
    summary: dict[str, Any]
    scope_audit: dict[str, Any]
    claims: list[AgentClaim]
    technical_decisions: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    review_checklist: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": dict(self.summary),
            "scope_audit": dict(self.scope_audit),
            "claims": [claim.to_dict() for claim in self.claims],
            "technical_decisions": list(self.technical_decisions),
            "risks": list(self.risks),
            "open_questions": list(self.open_questions),
            "review_checklist": list(self.review_checklist),
        }


def detect_agent_change_packet(events_or_blocks: list[Any]) -> bool:
    return len(agent_change_packet_detector_signals(events_or_blocks)) >= 5


def agent_change_packet_detector_signals(events_or_blocks: list[Any]) -> list[str]:
    headings = [_heading(event) for event in events_or_blocks]
    combined = "\n".join(f"{heading}\n{_text(event)}" for heading, event in zip(headings, events_or_blocks))
    normalized_combined = _normalize(combined)
    signals: list[str] = []

    if any("agent change packet" in _normalize(heading) for heading in headings):
        signals.extend(["heading:agent_change_packet"] * 3)
    for heading in headings:
        canonical = _canonical_section(heading)
        if canonical in EXPECTED_SECTIONS:
            signals.append(f"section:{canonical}")
    for phrase in STRONG_PHRASES:
        if _normalize(phrase) in normalized_combined:
            signals.append(f"phrase:{phrase}")
    return signals


def parse_agent_change_packet(events_or_blocks: list[Any]) -> AgentChangePacket:
    section_text: dict[str, list[str]] = {}
    section_event_ids: dict[str, list[str]] = {}
    section_sources: dict[str, list[dict[str, Any]]] = {}
    headings = [_heading(event) for event in events_or_blocks]
    title = next((heading.strip() for heading in headings if "agent change packet" in _normalize(heading)), None)
    if title is None:
        title = _document_title(events_or_blocks) or "Agent Change Packet"

    for event in events_or_blocks:
        heading = _heading(event)
        canonical = _canonical_section(heading)
        text = _text(event)
        if canonical is None:
            continue
        section_text.setdefault(canonical, []).append(text)
        event_id = _event_id(event)
        if event_id:
            section_event_ids.setdefault(canonical, []).append(event_id)
        section_sources.setdefault(canonical, []).append(_source_from_event(event, text[:700], canonical))

    candidate_sections = [
        "Candidate Technical Decisions / Risks / Open Questions",
        "Candidate Technical Decisions",
        "Candidate Risks",
        "Candidate Open Questions",
        "Candidate Implementation Claims",
        "Review Checklist",
    ]
    sections_found = [section for section in [*EXPECTED_SECTIONS, *candidate_sections] if section in section_text]
    missing_sections = [section for section in EXPECTED_SECTIONS if section not in section_text]
    packet = AgentChangePacket(
        packet_id=_packet_id(title, "\n".join(_text(event) for event in events_or_blocks)),
        title=title,
        original_request=_join_section(section_text, "Original Request"),
        agent_implementation_summary=_join_section(section_text, "Agent Implementation Summary"),
        claimed_capabilities=_join_section(section_text, "Claimed Capabilities"),
        validation_claimed=_join_section(section_text, "Validation Claimed by Agent"),
        existing_product_rules=_join_section(section_text, "Existing Product Rules / Approved Context"),
        git_stat=_join_section(section_text, "Git Stat"),
        git_diff=_join_section(section_text, "Git Diff"),
        questions_for_audit=_join_section(section_text, "Questions for Spool Audit"),
        expected_useful_output=_join_section(section_text, "Expected Useful Spool Output"),
        sections_found=sections_found,
        missing_sections=missing_sections,
        detector_signals=agent_change_packet_detector_signals(events_or_blocks),
        section_event_ids=section_event_ids,
        section_sources=section_sources,
    )
    if "Candidate Technical Decisions / Risks / Open Questions" in section_text:
        packet.existing_product_rules = "\n\n".join(
            part for part in [packet.existing_product_rules, _join_section(section_text, "Candidate Technical Decisions / Risks / Open Questions")] if part
        )
    labeled_candidate_text = _labeled_candidate_section_text(section_text)
    if labeled_candidate_text:
        packet.existing_product_rules = "\n\n".join(part for part in [packet.existing_product_rules, labeled_candidate_text] if part)
    if "Candidate Implementation Claims" in section_text:
        packet.expected_useful_output = "\n\n".join(
            part for part in [packet.expected_useful_output, _join_section(section_text, "Candidate Implementation Claims")] if part
        )
    return packet


def extract_agent_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims: list[AgentClaim] = []
    for claim in _implementation_summary_claims(packet):
        claims.append(claim)
    for claim in _capability_claims(packet):
        claims.append(claim)
    claims.extend(_validation_claims(packet))
    claims.extend(_product_rule_claims(packet))
    claims.extend(_technical_decision_risk_question_claims(packet))
    return _dedupe_claims(claims)


def audit_agent_claims(packet: AgentChangePacket, claims: list[AgentClaim]) -> AgentAuditReport:
    audited = [_audit_claim(packet, claim) for claim in claims]
    scope = _scope_audit(packet, audited)
    technical_decisions = [_memory_summary(claim) for claim in audited if claim.claim_type == "technical_decision"]
    risks = [_memory_summary(claim) for claim in audited if claim.claim_type == "risk"]
    open_questions = [_memory_summary(claim) for claim in audited if claim.claim_type == "open_question"]
    checklist = _review_checklist(packet, audited, scope, risks, open_questions)
    summary = _summary(audited)
    overall = "supported"
    if summary["unsupported_claim_count"] or summary["contradicted_claim_count"]:
        overall = "failed" if summary["contradicted_claim_count"] else "needs_review"
    elif summary["weakly_supported_claim_count"] or summary["needs_review_count"] or scope["possible_scope_issues"]:
        overall = "needs_review"
    summary["overall_verdict"] = overall
    return AgentAuditReport(
        summary=summary,
        scope_audit=scope,
        claims=audited,
        technical_decisions=technical_decisions,
        risks=risks,
        open_questions=open_questions,
        review_checklist=checklist,
    )


def build_agent_candidate_memory_objects(packet: AgentChangePacket, report: AgentAuditReport) -> list[MemoryObject]:
    generated_at = datetime.now(timezone.utc).isoformat()
    objects: list[MemoryObject] = []
    for claim in _claims_for_candidate_memory(report.claims):
        memory_type = _memory_type_for_claim(claim)
        if memory_type is None:
            continue
        if _low_quality_claim_text(claim.claim):
            continue
        source_ids = list(packet.section_event_ids.get(claim.source_section) or [])
        sources = _sources_for_claim(packet, claim)
        if not source_ids or not sources or not claim.source_quote or claim.source_quote.strip() == "---":
            continue
        evidence_state = claim.verdict if claim.verdict in {"supported", "weakly_supported", "unsupported", "contradicted"} else "unknown"
        review_state = "needs_review" if memory_type in {"risk", "open_question"} or claim.verdict in {"unsupported", "contradicted", "needs_review"} else "unreviewed"
        status = "needs_review" if review_state == "needs_review" else ("unresolved" if memory_type == "open_question" else "candidate")
        objects.append(
            MemoryObject(
                memory_id=_memory_id(memory_type, claim.claim_id, claim.claim, source_ids),
                source_object_id=claim.claim_id,
                type=memory_type,
                title=claim.claim,
                summary=claim.claim,
                status=status,
                confidence=min(float(claim.confidence or 0.0), 0.84),
                source_event_ids=source_ids,
                sources=sources,
                quote=claim.source_quote,
                evidence_state=evidence_state,
                review_state=review_state,
                created_at=generated_at,
                updated_at=generated_at,
                metadata={
                    "packet_type": "agent_change_packet",
                    "claim_id": claim.claim_id,
                    "claim_type": claim.claim_type,
                    "audit_verdict": claim.verdict,
                    "source_section": claim.source_section,
                },
                review_reason_codes=(["agent_audit_needs_review"] if review_state == "needs_review" else []),
                memory_quality_score=_quality_for_verdict(claim.verdict),
            )
        )
    return _dedupe_memory_objects(objects)


def build_agent_change_artifact_payloads(
    input_file: str,
    packet: AgentChangePacket,
    claims: list[AgentClaim],
    report: AgentAuditReport,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    debug = {
        "metadata": {
            "input_file": input_file,
            "generated_at": generated_at,
            "classification": "agent_change_packet",
        },
        "sections_found": packet.sections_found,
        "missing_sections": packet.missing_sections,
        "detector_signals": packet.detector_signals,
        "raw_section_lengths": {
            "original_request": len(packet.original_request),
            "agent_implementation_summary": len(packet.agent_implementation_summary),
            "claimed_capabilities": len(packet.claimed_capabilities),
            "validation_claimed": len(packet.validation_claimed),
            "existing_product_rules": len(packet.existing_product_rules),
            "git_stat": len(packet.git_stat),
            "git_diff": len(packet.git_diff),
            "questions_for_audit": len(packet.questions_for_audit),
            "expected_useful_output": len(packet.expected_useful_output),
        },
        "claim_extraction_counts": _claim_counts(claims, key="claim_type"),
        "evidence_matching_counts": _claim_counts(report.claims, key="verdict"),
    }
    claims_payload = {
        "metadata": {
            "input_file": input_file,
            "generated_at": generated_at,
            "claim_count": len(report.claims),
        },
        "claims": [claim.to_dict() for claim in report.claims],
    }
    report_payload = {
        "metadata": {
            "input_file": input_file,
            "generated_at": generated_at,
            "classification": "agent_change_packet",
        },
        **report.to_dict(),
    }
    return {
        "agent_change_packet_debug.json": debug,
        "agent_claims.json": claims_payload,
        "agent_audit_report.json": report_payload,
        "agent_audit_report.md": agent_audit_report_to_markdown(report_payload),
    }


def write_agent_change_artifacts(
    run_dir: str | Path,
    packet: AgentChangePacket,
    claims: list[AgentClaim],
    report: AgentAuditReport,
    *,
    input_file: str = "",
    pretty: bool = False,
) -> dict[str, str]:
    output_dir = Path(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = build_agent_change_artifact_payloads(input_file, packet, claims, report)
    paths: dict[str, str] = {}
    for filename, payload in payloads.items():
        path = output_dir / filename
        if filename.endswith(".md"):
            path.write_text(str(payload), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2 if pretty else None, ensure_ascii=False) + "\n", encoding="utf-8")
        paths[filename] = str(path)
    return paths


def agent_audit_report_to_markdown(report_payload: dict[str, Any]) -> str:
    summary = report_payload.get("summary", {})
    claims = report_payload.get("claims", [])
    scope = report_payload.get("scope_audit", {})
    lines = [
        "# Spool Agent Audit Report",
        "",
        "## Overall Verdict",
        "",
        str(summary.get("overall_verdict") or "needs_review"),
        "",
        "## Scope Audit",
        "",
    ]
    lines.append(f"Scope verdict: {scope.get('scope_verdict') or 'needs_review'}")
    requested = scope.get("requested_features") or scope.get("requested") or []
    non_goals = scope.get("explicit_non_goals") or []
    if requested:
        lines.append(f"Requested features: {', '.join(requested[:8])}")
    if non_goals:
        lines.append(f"Explicit non-goals: {', '.join(non_goals[:8])}")
    for issue in scope.get("possible_scope_issues") or []:
        lines.append(f"- {issue}")
    lines.extend(["", "## Key Supported Claims", ""])
    lines.extend(_claim_detail_lines(claims, {"supported"}, limit=8))
    lines.extend(["", "## Weak Claims / Missing Evidence", ""])
    lines.extend(_claim_detail_lines(claims, {"weakly_supported", "needs_review"}, limit=8))
    lines.extend(["", "## Unsupported Claims", ""])
    lines.extend(_claim_detail_lines(claims, {"unsupported", "contradicted"}, limit=8))
    lines.extend(["", "## Technical Decisions", ""])
    lines.extend(f"- {item.get('claim')}" for item in (report_payload.get("technical_decisions", [])[:6]))
    lines.extend(["", "## Risks", ""])
    lines.extend(f"- {item.get('claim')}" for item in (report_payload.get("risks", [])[:6]))
    lines.extend(["", "## Open Questions", ""])
    lines.extend(f"- {item.get('claim')}" for item in (report_payload.get("open_questions", [])[:6]))
    lines.extend(["", "## Reviewer Checklist", ""])
    lines.extend(f"- {item}" for item in (report_payload.get("review_checklist", [])[:8]))
    return "\n".join(lines).rstrip() + "\n"


def _implementation_summary_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims = []
    for bullet in _implementation_claim_lines(packet.agent_implementation_summary):
        clean = _clean_sentence(_strip_code_ticks(bullet))
        if _low_quality_claim_text(clean):
            file_claim = _canonical_file_claim(clean)
            if file_claim is None:
                continue
            clean, claim_type = file_claim
            claims.append(_claim(clean, claim_type, "Agent Implementation Summary", bullet))
            continue
        lower = clean.lower()
        if lower.startswith("added "):
            claim_type = "file_added"
            if path := _path_from_claim(clean):
                clean = f"The implementation added {path}."
        elif lower.startswith(("updated ", "extended ")):
            claim_type = "file_modified"
            if path := _path_from_claim(clean):
                clean = f"The implementation modified {path}."
        elif lower.startswith(("preserves ", "avoids ")):
            claim_type = "compatibility"
        else:
            claim_type = "capability"
        claims.append(_claim(clean, claim_type, "Agent Implementation Summary", bullet))
    return claims


def _capability_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims: list[AgentClaim] = []
    text = packet.claimed_capabilities
    normalized = _normalize(text)
    if _all_actions_present(text) or "approve reject edit" in normalized or "approve/reject/edit" in text.lower():
        claims.append(
            _claim(
                "The CLI supports approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale.",
                "capability",
                "Claimed Capabilities",
                "Applying review actions",
            )
        )
    artifact_names = _artifact_names(text)
    update_artifacts = [name for name in ["candidate_memory.json", "approved_memory.json", "rejected_memory.json", "memory_state_debug.json"] if name in artifact_names]
    if len(update_artifacts) >= 3:
        claims.append(
            _claim(
                "Review actions update candidate_memory.json, approved_memory.json, rejected_memory.json, and memory_state_debug.json.",
                "capability",
                "Claimed Capabilities",
                "Updating review-state artifacts",
            )
        )
    if "review_actions.json" in artifact_names or re.search(r"\bappend", text, re.IGNORECASE):
        claims.append(_claim("review_actions.json is append-only.", "capability", "Claimed Capabilities", "Appending to review_actions.json"))
    for bullet in _bullets(text):
        clean = _clean_sentence(_strip_code_ticks(bullet))
        lower = clean.lower()
        if _low_quality_claim_text(clean):
            continue
        if any(action in lower for action in REVIEW_ACTIONS) or _artifact_names(clean):
            continue
        claims.append(_claim(clean, "capability", "Claimed Capabilities", bullet))
    return claims


def _validation_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims = []
    if match := re.search(r"Ran\s+(\d+)\s+tests\b.*?\bOK\b", packet.validation_claimed, re.IGNORECASE | re.DOTALL):
        claims.append(_claim(f"Unit tests passed with {match.group(1)} tests.", "test_result", "Validation Claimed by Agent", match.group(0)))
    elif match := re.search(r"\bPASS,\s*(\d+)\s+tests\b", packet.validation_claimed, re.IGNORECASE):
        claims.append(_claim(f"Unit tests passed with {match.group(1)} tests.", "test_result", "Validation Claimed by Agent", match.group(0)))
    if re.search(r"\bScenario regressions passed\b", packet.validation_claimed, re.IGNORECASE):
        claims.append(_claim("Scenario regressions passed.", "test_result", "Validation Claimed by Agent", "Scenario regressions passed"))
    for line in re.findall(r"\b([A-Za-z][^\n.]+:\s+PASS)\b", packet.validation_claimed):
        claims.append(_claim(_clean_sentence(line), "test_result", "Validation Claimed by Agent", line))
    if re.search(r"\bManual smoke passed\b", packet.validation_claimed, re.IGNORECASE):
        claims.append(_claim("Manual smoke passed in Streamlit.", "manual_smoke", "Validation Claimed by Agent", "Manual smoke passed in Streamlit"))
    if re.search(r"\bManual smoke test claimed\b", packet.validation_claimed, re.IGNORECASE):
        for line in _manual_smoke_lines(packet.validation_claimed):
            claims.append(_claim(_clean_sentence(line), "manual_smoke", "Validation Claimed by Agent", line))
    return claims


def _product_rule_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims = []
    product_rule_text = re.split(r"(?im)^\s*(?:technical decisions|risks|open questions)\s*:\s*$", packet.existing_product_rules)[0]
    for bullet in _bullets(product_rule_text):
        clean = _clean_sentence(_strip_code_ticks(bullet))
        if not clean:
            continue
        lower = clean.lower()
        if lower.startswith("do not") or "not auto-approve" in lower:
            claim_type = "non_goal" if lower.startswith("do not") else "technical_decision"
        else:
            claim_type = "technical_decision"
        claims.append(_claim(clean, claim_type, "Existing Product Rules / Approved Context", bullet))
    return claims


def _technical_decision_risk_question_claims(packet: AgentChangePacket) -> list[AgentClaim]:
    claims = []
    section = packet.existing_product_rules
    groups = _labeled_bullet_groups(section)
    for label, claim_type in (("technical decisions", "technical_decision"), ("risks", "risk"), ("open questions", "open_question")):
        for bullet in groups.get(label, []):
            claims.append(_claim(_clean_sentence(_strip_code_ticks(bullet)), claim_type, "Existing Product Rules / Approved Context", bullet))
    return claims


def _audit_claim(packet: AgentChangePacket, claim: AgentClaim) -> AgentClaim:
    audited = AgentClaim(**claim.to_dict())
    lower = audited.claim.lower()
    if audited.claim_type in {"file_added", "file_modified"}:
        _audit_file_claim(packet, audited)
    elif audited.claim_type in {"test_result", "manual_smoke"}:
        _weak_from_validation(packet, audited)
    elif "the cli supports approve" in lower or "approve/reject/edit" in lower or "mark_needs_review" in lower:
        _supported_if(
            packet,
            audited,
            _all_actions_present(packet.git_diff) and _has_review_action_test_evidence(packet.git_diff),
            "Git Diff",
            "Action names appear in ALLOWED_ACTIONS/CLI choices and tests cover review-action behavior.",
        )
    elif "review_actions.json is append-only" in lower or "appends to review_actions.json" in lower or "append" in lower and "review_actions.json" in lower:
        _supported_if(packet, audited, _has_append_evidence(packet), "Git Diff", "Append logic appears in code or tests.")
    elif "preserve" in lower and "source evidence" in lower:
        _supported_if(packet, audited, all(term in packet.git_diff for term in ("source_event_ids", "sources", "quote")), "Git Diff", "Evidence fields are preserved in edit logic.")
    elif "unsupported" in lower and ("approval" in lower or "approve" in lower):
        _supported_if(packet, audited, "unsupported_approval" in packet.git_diff or "contradicted" in packet.git_diff, "Git Diff", "Unsupported approval guard appears in the diff/tests.")
    elif "duplicate" in lower or "memory_id" in lower and "approved" in lower:
        _supported_if(packet, audited, "_upsert_memory" in packet.git_diff or "without_duplicate" in packet.git_diff, "Git Diff", "Upsert/no-duplicate logic appears in the diff/tests.")
    elif (artifact_names := _artifact_names(lower)) and all(name in packet.git_diff or name in packet.git_stat for name in artifact_names):
        audited.verdict = "supported"
        audited.confidence = 0.82
        audited.evidence_sections = ["Git Diff", "Git Stat"]
        audited.reason = "Referenced artifacts appear in git evidence."
        audited.evidence_quote = _first_matching_line(packet.git_diff + "\n" + packet.git_stat, artifact_names)
    elif audited.claim_type in {"technical_decision", "risk", "open_question", "non_goal"}:
        audited.verdict = "needs_review" if audited.claim_type in {"risk", "open_question"} else "weakly_supported"
        audited.confidence = 0.62 if audited.verdict == "weakly_supported" else 0.54
        audited.evidence_sections = [audited.source_section]
        audited.reason = "The packet states this directly; a reviewer should decide how it should affect memory."
        audited.evidence_quote = audited.source_quote
    else:
        audited.verdict = "needs_review"
        audited.confidence = 0.45
        audited.reason = "No deterministic evidence rule matched this claim."
    return audited


def _audit_file_claim(packet: AgentChangePacket, claim: AgentClaim) -> None:
    path = _path_from_claim(claim.claim)
    if not path:
        claim.verdict = "needs_review"
        claim.confidence = 0.35
        claim.reason = "No file path could be found in the claim."
        return
    evidence_text = packet.git_diff + "\n" + packet.git_stat
    contains_path = path in evidence_text or (path == "README.md" and "README" in evidence_text)
    diff_block = _diff_block_for_path(packet.git_diff, path)
    is_new = "new file mode" in diff_block or (claim.claim_type == "file_added" and _large_insertion_stat(packet.git_stat, path))
    if contains_path and (claim.claim_type == "file_modified" or is_new):
        claim.verdict = "supported"
        claim.confidence = 0.92 if is_new else 0.86
        claim.reason = "The git stat/diff contains the claimed file change."
        claim.evidence_sections = ["Git Diff", "Git Stat"]
        claim.evidence_quote = _diff_block_for_path(packet.git_diff, path)[:700] or _first_matching_line(packet.git_stat, [path])
    elif contains_path:
        claim.verdict = "weakly_supported"
        claim.confidence = 0.68
        claim.reason = "The file appears in git evidence, but the exact add/modify status is not fully proven."
        claim.evidence_sections = ["Git Stat"]
        claim.evidence_quote = _first_matching_line(packet.git_stat, [path])
    else:
        claim.verdict = "unsupported"
        claim.confidence = 0.2
        claim.reason = "The claimed file does not appear in git stat or diff evidence."


def _weak_from_validation(packet: AgentChangePacket, claim: AgentClaim) -> None:
    if claim.source_quote and _normalize(claim.source_quote) in _normalize(packet.validation_claimed):
        claim.verdict = "weakly_supported"
        claim.confidence = 0.64
        claim.evidence_sections = ["Validation Claimed by Agent"]
        claim.reason = "The validation section states this, but no independent log artifact is available."
        claim.evidence_quote = claim.source_quote
    else:
        claim.verdict = "needs_review"
        claim.confidence = 0.35
        claim.reason = "The validation claim is not visible in the validation section."


def _supported_if(packet: AgentChangePacket, claim: AgentClaim, condition: bool, section: str, reason: str) -> None:
    if condition:
        claim.verdict = "supported"
        claim.confidence = 0.86
        claim.evidence_sections = [section]
        claim.reason = reason
        claim.evidence_quote = _evidence_quote_for_claim(packet, claim)
    else:
        claim.verdict = "unsupported"
        claim.confidence = 0.25
        claim.reason = "The expected deterministic evidence was not found."


def _scope_audit(packet: AgentChangePacket, claims: list[AgentClaim]) -> dict[str, Any]:
    implemented = [claim.claim for claim in claims if claim.source_section in {"Agent Implementation Summary", "Claimed Capabilities"}]
    requested_features = _requested_features(packet)
    explicit_non_goals = _explicit_non_goals(packet)
    evidence = packet.git_diff + "\n" + packet.git_stat + "\n" + packet.agent_implementation_summary
    non_goals_checked = []
    possible_issues = []
    for term in FORBIDDEN_SCOPE_TERMS:
        found = _forbidden_scope_term_found(evidence, term)
        non_goals_checked.append({"term": term, "found_in_implementation_evidence": found})
        if found and term not in {"auto-approve", "automatic approval"}:
            possible_issues.append(f"Possible out-of-scope addition mentions {term}.")
    return {
        "scope_verdict": "partially_out_of_scope" if possible_issues else "in_scope",
        "requested_features": requested_features,
        "explicit_non_goals": explicit_non_goals,
        "implemented_features": implemented,
        "non_goals_checked": non_goals_checked,
        "possible_scope_issues": possible_issues,
        "requested": requested_features,
        "implemented": implemented,
    }


def _summary(claims: list[AgentClaim]) -> dict[str, Any]:
    counts = _claim_counts(claims, key="verdict")
    return {
        "overall_verdict": "needs_review",
        "supported_claim_count": counts.get("supported", 0),
        "weakly_supported_claim_count": counts.get("weakly_supported", 0),
        "unsupported_claim_count": counts.get("unsupported", 0),
        "contradicted_claim_count": counts.get("contradicted", 0),
        "needs_review_count": counts.get("needs_review", 0),
    }


def _review_checklist(
    packet: AgentChangePacket,
    claims: list[AgentClaim],
    scope: dict[str, Any],
    risks: list[dict[str, Any]],
    open_questions: list[dict[str, Any]],
) -> list[str]:
    checklist = []
    for claim in claims:
        if claim.verdict in {"unsupported", "contradicted"}:
            checklist.append(f"Inspect unsupported claim: {claim.claim}")
        elif claim.verdict == "weakly_supported" and claim.claim_type in {"test_result", "manual_smoke", "capability"}:
            checklist.append(f"Verify weakly supported claim: {claim.claim}")
    if packet.missing_sections:
        checklist.append(f"Review missing packet sections: {', '.join(packet.missing_sections)}")
    for issue in scope.get("possible_scope_issues") or []:
        checklist.append(issue)
    for risk in risks:
        checklist.append(f"Review audit risk: {risk.get('claim')}")
    for question in open_questions:
        checklist.append(f"Answer open question: {question.get('claim')}")
    if not checklist:
        checklist.append("Review supported claims and decide whether any should become approved memory.")
    return list(dict.fromkeys(checklist))


def _memory_summary(claim: AgentClaim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "claim": claim.claim,
        "source_section": claim.source_section,
        "verdict": claim.verdict,
        "confidence": claim.confidence,
        "source_quote": claim.source_quote,
    }


def _memory_type_for_claim(claim: AgentClaim) -> str | None:
    if claim.claim_type == "technical_decision":
        return "decision"
    if claim.claim_type == "risk":
        return "risk"
    if claim.claim_type == "open_question":
        return "open_question"
    if claim.claim_type in {"file_added", "file_modified", "capability", "compatibility", "test_result", "manual_smoke"}:
        return "context"
    return None


def _sources_for_claim(packet: AgentChangePacket, claim: AgentClaim) -> list[dict[str, Any]]:
    sources = [dict(source) for source in packet.section_sources.get(claim.source_section, [])]
    if not sources:
        return []
    sources[0]["quote"] = claim.source_quote or sources[0].get("quote", "")
    return sources[:1]


def _quality_for_verdict(verdict: str) -> float:
    return {
        "supported": 0.82,
        "weakly_supported": 0.62,
        "needs_review": 0.46,
        "unsupported": 0.25,
        "contradicted": 0.15,
    }.get(verdict, 0.4)


def _claim(text: str, claim_type: str, source_section: str, source_quote: str) -> AgentClaim:
    text = _clean_sentence(text)
    return AgentClaim(
        claim_id=f"claim_{hashlib.sha1('|'.join([claim_type, source_section, text]).encode('utf-8')).hexdigest()[:12]}",
        claim=text,
        claim_type=claim_type,
        source_section=source_section,
        source_quote=_clean_quote(source_quote),
    )


def _bullets(text: str) -> list[str]:
    result = []
    for line in text.splitlines():
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if match:
            result.append(match.group(1).strip())
    return result


def _labeled_bullet_groups(text: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        label_match = re.match(r"^\s*([A-Za-z ]+):\s*$", line)
        if label_match:
            current = _normalize_spaces(label_match.group(1)).lower()
            groups.setdefault(current, [])
            continue
        bullet_match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if bullet_match and current:
            groups.setdefault(current, []).append(bullet_match.group(1).strip())
    return groups


def _manual_smoke_lines(text: str) -> list[str]:
    match = re.search(r"Manual smoke test claimed:\s*(?P<body>.*)", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    lines = []
    for line in match.group("body").splitlines():
        clean = line.strip().strip("`").strip()
        if not clean or clean.lower() in {"text", "```"}:
            continue
        if clean.startswith("```"):
            continue
        lines.append(clean)
    return lines


def _dedupe_claims(claims: list[AgentClaim]) -> list[AgentClaim]:
    result = []
    seen = set()
    for claim in claims:
        key = (_normalize(claim.claim), claim.claim_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(claim)
    return result


def _dedupe_memory_objects(objects: list[MemoryObject]) -> list[MemoryObject]:
    result = []
    seen = set()
    for obj in objects:
        key = (obj.type, _normalize(obj.title), tuple(obj.source_event_ids))
        if key in seen:
            continue
        seen.add(key)
        result.append(obj)
    return result


def _all_actions_present(text: str) -> bool:
    return all(
        action in text
        for action in (
            "approve",
            "reject",
            "edit",
            "mark_needs_review",
            "mark_resolved",
            "mark_unresolved",
            "mark_superseded",
            "mark_stale",
        )
    )


def _artifact_names(text: str) -> list[str]:
    return re.findall(r"\b(?:candidate_memory|approved_memory|rejected_memory|memory_state_debug|review_actions)\.json\b", text)


def _path_from_claim(text: str) -> str | None:
    if match := re.search(r"\b((?:src|tests)/[A-Za-z0-9_./-]+\.py|[A-Za-z0-9_-]+\.py|README(?:\.md)?)\b", text):
        path = match.group(1)
        return "README.md" if path == "README" else path
    return None


def _diff_block_for_path(diff: str, path: str) -> str:
    pattern = re.compile(rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n(?P<body>.*?)(?=\ndiff --git |\Z)", re.DOTALL)
    match = pattern.search(diff)
    if match:
        return match.group(0)
    return ""


def _first_matching_line(text: str, terms: list[str]) -> str:
    normalized_terms = [_normalize(term) for term in terms]
    for line in text.splitlines():
        normalized = _normalize(line)
        if all(term in normalized for term in normalized_terms if term):
            return line.strip()
    return ""


def _evidence_quote_for_claim(packet: AgentChangePacket, claim: AgentClaim) -> str:
    terms = [term for term in re.findall(r"\b[A-Za-z_]+(?:\.json|_[A-Za-z_]+)?\b", claim.claim) if len(term) > 5]
    return _first_matching_line(packet.git_diff, terms[:2]) or packet.git_diff[:500]


def _claim_counts(claims: list[Any], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        value = getattr(claim, key, None) if not isinstance(claim, dict) else claim.get(key)
        counts[str(value or "unknown")] = counts.get(str(value or "unknown"), 0) + 1
    return counts


def _claim_lines(claims: list[dict[str, Any]], verdicts: set[str]) -> list[str]:
    lines = [f"- {claim.get('claim')}" for claim in claims if claim.get("verdict") in verdicts]
    return lines or ["- None"]


def _claim_detail_lines(claims: list[dict[str, Any]], verdicts: set[str], *, limit: int) -> list[str]:
    lines: list[str] = []
    for claim in claims:
        claim_text = str(claim.get("claim") or "")
        if claim.get("verdict") not in verdicts or _low_quality_claim_text(claim_text):
            continue
        lines.append(f'- Claim: "{claim_text}"')
        lines.append(f"  Verdict: {claim.get('verdict') or 'needs_review'}")
        evidence = str(claim.get("evidence_quote") or claim.get("source_quote") or claim.get("reason") or "").strip()
        if evidence:
            lines.append(f"  Evidence: {_normalize_spaces(evidence)[:240]}")
        else:
            lines.append(f"  Evidence: {claim.get('reason') or 'No deterministic evidence attached.'}")
        if len(lines) >= limit * 3:
            break
    return lines or ["- None"]


def _implementation_claim_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if bullet:
            lines.append(bullet.group(1).strip())
            continue
        if re.match(r"^(added|updated|extended|modified|preserves|avoids)\b", stripped, re.IGNORECASE):
            lines.append(stripped)
    return lines


def _canonical_file_claim(text: str) -> tuple[str, str] | None:
    path = _path_from_claim(text)
    if not path:
        return None
    normalized = _normalize(text)
    added_paths = {"src/review_actions.py", "review_memory.py", "tests/test_review_actions.py"}
    modified_paths = {"src/memory_store.py", "src/review_policy.py", "README.md"}
    if path in added_paths or normalized.startswith("added "):
        return (f"The implementation added {path}.", "file_added")
    if path in modified_paths or normalized.startswith(("updated ", "extended ", "modified ")):
        return (f"The implementation modified {path}.", "file_modified")
    return (f"The implementation modified {path}.", "file_modified")


def _low_quality_claim_text(text: str) -> bool:
    clean = _normalize_spaces(str(text)).strip().strip("`")
    if not clean:
        return True
    if clean in {"---", "***", "___"}:
        return True
    return bool(LOW_QUALITY_CLAIM_RE.fullmatch(clean))


def _claims_for_candidate_memory(claims: list[AgentClaim]) -> list[AgentClaim]:
    selected: list[AgentClaim] = []
    seen: set[str] = set()

    def add_matching(types: set[str], verdicts: set[str] | None, limit: int) -> None:
        count = 0
        for claim in claims:
            if claim.claim_type not in types:
                continue
            if verdicts is not None and claim.verdict not in verdicts:
                continue
            key = f"{claim.claim_type}:{_normalize(claim.claim)}"
            if key in seen or _low_quality_claim_text(claim.claim):
                continue
            selected.append(claim)
            seen.add(key)
            count += 1
            if count >= limit:
                break

    add_matching({"file_added", "file_modified"}, {"supported", "weakly_supported"}, 6)
    add_matching({"capability", "compatibility"}, {"supported", "weakly_supported"}, 5)
    add_matching({"technical_decision"}, None, 3)
    add_matching({"risk"}, None, 4)
    add_matching({"open_question"}, None, 3)
    return selected


def _requested_features(packet: AgentChangePacket) -> list[str]:
    text = _normalize(
        "\n".join(
            [
                packet.original_request,
                packet.agent_implementation_summary,
                packet.claimed_capabilities,
                packet.git_stat,
            ]
        )
    )
    features: list[str] = []

    def add(label: str, condition: bool) -> None:
        if condition and label not in features:
            features.append(label)

    add("review action model", "review action" in text or "review actions" in text)
    add("review action CLI", "review memory py" in text or "review_memory" in text or "cli" in text or " action choices" in text)
    add(
        "candidate/approved/rejected/review_actions artifact updates",
        all(term in text for term in ("candidate memory json", "approved memory json", "rejected memory json"))
        or "review actions json" in text,
    )
    add("memory_state_debug latest action fields", "memory state debug json" in text or "latest action" in text)
    add("tests", "tests" in text or "test review actions py" in text)
    add("README docs", "readme" in text or "docs" in text)
    add("source evidence preservation", "source evidence" in text)
    add("unsupported approval guard", "unsupported memory" in text or "unsupported approval" in text)
    return features


def _explicit_non_goals(packet: AgentChangePacket) -> list[str]:
    text = _normalize("\n".join([packet.original_request, packet.existing_product_rules]))
    non_goals: list[str] = []
    patterns = [
        ("browser extension", ("browser extension",)),
        ("integrations", ("integrations", "integration")),
        ("database", ("database",)),
        ("auth", ("auth", "authentication")),
        ("AI answer verifier", ("ai answer verifier", "answer verifier")),
        ("large UI redesign", ("large ui redesign", "ui redesign")),
        ("task reminder system", ("task reminder system", "reminder system")),
        ("todo tracker features", ("todo tracker", "todo tasks", "todo tracker features")),
        ("automatic approval by default", ("automatic approval", "auto approve", "auto approval")),
    ]
    for label, terms in patterns:
        if any(term in text for term in terms) and label not in non_goals:
            non_goals.append(label)
    return non_goals


def _forbidden_scope_term_found(text: str, term: str) -> bool:
    term_norm = _normalize(term)
    if not term_norm:
        return False
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(term_norm)}(?![a-z0-9])")
    for line in text.splitlines():
        normalized_line = _normalize(line)
        if not pattern.search(normalized_line):
            continue
        if _scope_line_is_negative_context(normalized_line, term_norm):
            continue
        return True
    return False


def _scope_line_is_negative_context(line: str, term_norm: str) -> bool:
    safe_patterns = [
        rf"\bwithout\b.{{0,80}}\b{re.escape(term_norm)}\b",
        rf"\bno\b.{{0,40}}\b{re.escape(term_norm)}\b",
        rf"\bnot\b.{{0,80}}\b{re.escape(term_norm)}\b",
        rf"\brather than\b.{{0,80}}\b{re.escape(term_norm)}\b",
        rf"\binstead of\b.{{0,80}}\b{re.escape(term_norm)}\b",
        rf"\bdo not\b.{{0,80}}\b{re.escape(term_norm)}\b",
    ]
    return any(re.search(pattern, line) for pattern in safe_patterns)


def _has_review_action_test_evidence(diff: str) -> bool:
    normalized = _normalize(diff)
    has_actions = all(action in diff for action in REVIEW_ACTIONS)
    has_cli_or_allowed = "allowed actions" in normalized or "choices sorted allowed actions" in normalized
    has_tests = "tests/test_review_actions.py" in diff and ("test_" in diff or "unsupported_approval" in diff)
    return has_actions and has_cli_or_allowed and has_tests


def _has_append_evidence(packet: AgentChangePacket) -> bool:
    evidence = packet.git_diff + "\n" + packet.validation_claimed
    return any(
        marker in evidence
        for marker in (
            "actions.append(action)",
            "append_review_action",
            "test_actions_append",
            "append audit history",
        )
    )


def _large_insertion_stat(stat: str, path: str) -> bool:
    for line in stat.splitlines():
        if path not in line and not (path == "README.md" and "README" in line):
            continue
        numbers = [int(match) for match in re.findall(r"\b(\d+)\b", line)]
        if numbers and max(numbers) >= 40:
            return True
    return False


def _canonical_section(heading: str | None) -> str | None:
    normalized = _normalize(heading or "")
    if not normalized:
        return None
    if "agent change packet" in normalized:
        return None
    return SECTION_ALIASES.get(normalized)


def _join_section(section_text: dict[str, list[str]], section: str) -> str:
    return "\n\n".join(part for part in section_text.get(section, []) if part).strip()


def _labeled_candidate_section_text(section_text: dict[str, list[str]]) -> str:
    parts = []
    for section, label in (
        ("Candidate Technical Decisions", "Technical decisions"),
        ("Candidate Risks", "Risks"),
        ("Candidate Open Questions", "Open questions"),
    ):
        text = _join_section(section_text, section)
        if text:
            parts.append(f"{label}:\n{text}")
    return "\n\n".join(parts)


def _packet_id(title: str, text: str) -> str:
    digest_source = title + "\n" + text
    return f"packet_{hashlib.sha1(digest_source.encode('utf-8')).hexdigest()[:12]}"


def _memory_id(memory_type: str, source_object_id: str, title: str, source_event_ids: list[str]) -> str:
    digest = hashlib.sha1("|".join([memory_type, source_object_id, title, ",".join(source_event_ids)]).encode("utf-8")).hexdigest()
    return f"mem_{digest[:12]}"


def _source_from_event(event: Any, quote: str, section: str) -> dict[str, Any]:
    provenance = _get(event, "provenance") or {}
    container = _get(event, "container") or {}
    actor = _get(event, "actor") or {}
    workspace = _get(event, "workspace") or {}
    return {
        "source_event_id": _event_id(event),
        "source_type": _get(event, "source_type"),
        "source_subtype": _get(event, "source_subtype"),
        "workspace": workspace.get("name") if isinstance(workspace, dict) else None,
        "document_id": provenance.get("document_id") or container.get("id"),
        "document_title": provenance.get("document_title") or container.get("name"),
        "section_heading": section,
        "actor": actor.get("name") or "Unknown",
        "actor_role": actor.get("role"),
        "quote": _clean_quote(quote),
        "locator": provenance.get("locator") or _get(event, "external_id"),
        "page": provenance.get("page"),
        "url": _get(event, "source_url"),
    }


def _document_title(events: list[Any]) -> str | None:
    for event in events:
        container = _get(event, "container") or {}
        if container.get("name"):
            return str(container["name"])
    return None


def _heading(event: Any) -> str:
    provenance = _get(event, "provenance") or {}
    context = _get(event, "context") or {}
    return str(provenance.get("section_heading") or context.get("section_heading") or "")


def _text(event: Any) -> str:
    content = _get(event, "content") or {}
    return str(content.get("text") or "")


def _event_id(event: Any) -> str:
    return str(_get(event, "event_id") or "")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _strip_code_ticks(text: str) -> str:
    return text.replace("`", "")


def _clean_sentence(text: str) -> str:
    clean = _normalize_spaces(text).strip(" -")
    if not clean:
        return ""
    return clean if clean.endswith((".", "?", "!")) else f"{clean}."


def _clean_quote(text: str) -> str:
    return _normalize_spaces(text)[:900]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(text).lower())).strip()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()
