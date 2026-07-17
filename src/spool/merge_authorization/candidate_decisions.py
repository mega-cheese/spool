from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CandidateDecision:
    id: str
    title: str
    decision_type: str
    source: str
    quote: str
    reason: str
    recommended_action: str = "approve_or_reject"
    status: str = "candidate"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "decision_type": self.decision_type,
            "source": self.source,
            "quote": self.quote,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
            "status": self.status,
        }


def extract_candidate_decisions(text: str, *, source: str = "agent_summary") -> list[CandidateDecision]:
    candidates: list[CandidateDecision] = []
    for line in _candidate_lines(text):
        if _is_low_value_line(line):
            continue
        if not _is_candidate_decision(line):
            continue
        candidates.append(
            CandidateDecision(
                id=f"CAND-{len(candidates) + 1:03d}",
                title=_candidate_title(line),
                decision_type=_decision_type(line),
                source=source,
                quote=line,
                reason=(
                    "The PR appears to propose a project-level product, architecture, security, API, "
                    "policy, or process choice that future PRs should respect."
                ),
            )
        )
    return _dedupe_candidates(candidates)


def _candidate_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^[-*]\s+", "", raw_line.strip())
        if not line or line.startswith("#"):
            continue
        result.append(line if line.endswith((".", "?", "!")) else f"{line}.")
    return result


def _is_candidate_decision(line: str) -> bool:
    lower = line.lower()
    future_signal = bool(
        re.search(
            r"\b(going forward|from now on|default behavior|must always|should never|must never|must not|all future|policy|requires approval|should be allowed|should now default|should automatically)\b",
            lower,
        )
    )
    if future_signal:
        return True
    if _has_sensitive_concept(lower) and re.search(r"\b(should|must|default|allowed|policy)\b", lower):
        return True
    return False


def _is_low_value_line(line: str) -> bool:
    lower = line.lower()
    if re.search(r"\b(remains? unchanged|is unchanged|are unchanged|still\s+\w+\s+(?:the\s+)?current)\b", lower):
        return True
    if re.search(r"\btests?\s+should\s+pass\b", lower):
        return True
    if re.search(r"\bshould\s+(?:return|handle|accept|raise|parse|render|call|update)\b", lower):
        return True
    if re.search(r"\b(added|updated|changed|modified)\s+`?[\w./-]+\.(?:py|md|json|ts|tsx)`?", lower):
        return True
    return False


def _candidate_title(line: str) -> str:
    title = re.sub(r"^\s*(going forward|from now on),?\s*", "", line, flags=re.IGNORECASE).strip()
    title = title.rstrip(".")
    return title[:1].upper() + title[1:] if title else line.rstrip(".")


def _decision_type(line: str) -> str:
    lower = line.lower()
    if re.search(r"\b(auth|security|secret|secrets)\b", lower):
        return "security"
    if re.search(r"\b(privacy)\b", lower):
        return "privacy"
    if re.search(r"\b(data|schema|telemetry)\b", lower):
        return "data"
    if re.search(r"\b(architecture|config|env)\b", lower):
        return "architecture"
    if re.search(r"\b(billing|payment|models?|upload|playground|ui)\b", lower):
        return "product_behavior"
    if re.search(r"\b(review actions?|append|overwrite|implementation|constraint)\b", lower):
        return "implementation_constraint"
    if re.search(r"\b(policy|approval|process)\b", lower):
        return "process_policy"
    return "unknown"


def _has_sensitive_concept(lower: str) -> bool:
    return bool(re.search(r"\b(auth|billing|payment|schema|security|privacy|telemetry|config|env|secrets)\b", lower))


def _dedupe_candidates(candidates: list[CandidateDecision]) -> list[CandidateDecision]:
    result: list[CandidateDecision] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", candidate.title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    for index, candidate in enumerate(result, start=1):
        candidate.id = f"CAND-{index:03d}"
    return result
