from __future__ import annotations

import re
from dataclasses import dataclass


ASSIGNMENT_HEADINGS = {"spool task", "task", "assignment"}
AGENT_SUMMARY_HEADINGS = {"agent summary", "summary", "implementation summary"}
TEST_EVIDENCE_HEADINGS = {"tests run", "test evidence", "testing"}
CANDIDATE_DECISION_HEADINGS = {"candidate decisions", "decisions"}


@dataclass
class PRBodySections:
    assignment_text: str = ""
    assignment_source: str | None = None
    agent_summary_text: str = ""
    agent_summary_source: str | None = None
    test_evidence_text: str = ""
    test_evidence_source: str | None = None
    candidate_decisions_text: str = ""
    candidate_decisions_source: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "assignment_text": self.assignment_text,
            "assignment_source": self.assignment_source,
            "agent_summary_text": self.agent_summary_text,
            "agent_summary_source": self.agent_summary_source,
            "test_evidence_text": self.test_evidence_text,
            "test_evidence_source": self.test_evidence_source,
            "candidate_decisions_text": self.candidate_decisions_text,
            "candidate_decisions_source": self.candidate_decisions_source,
        }


def parse_pr_body_sections(markdown: str) -> PRBodySections:
    sections = PRBodySections()
    for heading, body in _heading_sections(markdown):
        normalized = _normalize_heading(heading)
        if normalized in ASSIGNMENT_HEADINGS and not sections.assignment_source:
            sections.assignment_text = body
            sections.assignment_source = heading
        elif normalized in AGENT_SUMMARY_HEADINGS and not sections.agent_summary_source:
            sections.agent_summary_text = body
            sections.agent_summary_source = heading
        elif normalized in TEST_EVIDENCE_HEADINGS and not sections.test_evidence_source:
            sections.test_evidence_text = body
            sections.test_evidence_source = heading
        elif normalized in CANDIDATE_DECISION_HEADINGS and not sections.candidate_decisions_source:
            sections.candidate_decisions_text = body
            sections.candidate_decisions_source = heading
    return sections


def _heading_sections(markdown: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_heading is None:
            return
        result.append((current_heading, "\n".join(current_lines).strip()))

    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            current_heading = match.group(2).strip()
            current_lines = []
            continue
        if current_heading is not None:
            current_lines.append(line)
    flush()
    return result


def _normalize_heading(heading: str) -> str:
    return re.sub(r"\s+", " ", heading.strip().lower())
