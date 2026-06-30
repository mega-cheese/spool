#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "did",
    "do",
    "for",
    "from",
    "have",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "our",
    "that",
    "the",
    "to",
    "we",
    "what",
    "when",
    "who",
    "why",
    "with",
}

CLAIM_CATEGORIES = ("reasoning", "objections", "assumptions", "risks", "tradeoffs", "timelines")


def main() -> None:
    parser = argparse.ArgumentParser(description="Query approved decision memory.")
    parser.add_argument("approved_decisions_json")
    parser.add_argument("question")
    args = parser.parse_args()
    print(answer_question(args.approved_decisions_json, args.question))


def answer_question(path: str | Path, question: str) -> str:
    payload = _load_payload(path)
    decisions = [decision for decision in payload.get("decisions", []) if decision.get("status") == "approved"]
    if not decisions:
        return "I do not have reliable approved memory for that."

    best = _best_match(decisions, question)
    if not best:
        return "I do not have reliable approved memory for that."

    claims = []
    for category in CLAIM_CATEGORIES:
        claims.extend(best.get(category, []))

    revisit_trigger = best.get("revisit_trigger")
    structured_context = _structured_context(best)
    if not claims and not revisit_trigger and not structured_context:
        return "I do not have reliable approved memory for that."

    lines = [f"Decision: {best['decision']}", "", "Answer:"]
    reasoning = [claim["claim"] for claim in best.get("reasoning", [])]
    if reasoning:
        lines.append("The decision was made because " + "; ".join(_lower_first(item) for item in reasoning) + ".")
    elif best.get("timelines"):
        lines.append("The approved record includes timing context: " + "; ".join(_lower_first(claim["claim"]) for claim in best.get("timelines", [])) + ".")
    elif structured_context:
        lines.append("The approved record includes structured context: " + "; ".join(structured_context) + ".")
    elif revisit_trigger:
        lines.append("The approved record says to revisit this when " + _lower_first(str(revisit_trigger)).rstrip(".") + ".")
    else:
        lines.append("The approved record has supporting context, but no reasoning claim was approved.")

    if revisit_trigger:
        lines.append("Revisit trigger: " + str(revisit_trigger))
    if best.get("effective_date"):
        lines.append("Effective date: " + str(best["effective_date"]))
    for deadline in best.get("deadlines", []):
        lines.append("Deadline: " + str(deadline.get("date")) + " - " + str(deadline.get("description")))
    for item in best.get("action_items", []):
        due = item.get("due_date") or item.get("due_text")
        suffix = f" ({due})" if due else ""
        lines.append("Action item: " + str(item.get("action")) + suffix)

    lines.extend(["", "Sources:"])
    for claim in claims[:5]:
        source = claim.get("source") or {}
        lines.append(f"- {claim['claim']} ({source.get('speaker', 'Unknown')}, {source.get('message_ts', 'unknown ts')}): {source.get('quote', '')}")
    for item in [*best.get("deadlines", []), *best.get("action_items", [])][:5]:
        source = item.get("source") or {}
        label = item.get("description") or item.get("action")
        if label and source:
            lines.append(f"- {label} ({source.get('speaker', 'Unknown')}, {source.get('message_ts', 'unknown ts')}): {source.get('quote', '')}")
    return "\n".join(lines)


def _load_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return {"decisions": payload}
    return payload


def _best_match(decisions: list[dict[str, Any]], question: str) -> dict[str, Any] | None:
    question_terms = _terms(question)
    scored = []
    for decision in decisions:
        haystack = " ".join(
            [
                decision.get("decision", ""),
                decision.get("owner") or "",
                decision.get("effective_date") or "",
                decision.get("revisit_trigger") or "",
                " ".join(decision.get("participants", [])),
                " ".join(claim.get("claim", "") for category in CLAIM_CATEGORIES for claim in decision.get(category, [])),
                " ".join(item.get("description", "") for item in decision.get("deadlines", [])),
                " ".join(item.get("date", "") for item in decision.get("deadlines", [])),
                " ".join(item.get("action", "") for item in decision.get("action_items", [])),
                " ".join(item.get("due_text", "") or item.get("due_date", "") or "" for item in decision.get("action_items", [])),
            ]
        )
        score = len(question_terms & _terms(haystack))
        if score > 0:
            scored.append((score, decision))
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", text.lower()) if term not in STOPWORDS and len(term) > 2}


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _structured_context(decision: dict[str, Any]) -> list[str]:
    context: list[str] = []
    if decision.get("effective_date"):
        context.append("effective date " + str(decision["effective_date"]))
    for deadline in decision.get("deadlines", []):
        context.append(str(deadline.get("description")) + " on " + str(deadline.get("date")))
    for item in decision.get("action_items", []):
        due = item.get("due_date") or item.get("due_text")
        context.append(str(item.get("action")) + (f" due {due}" if due else ""))
    return context


if __name__ == "__main__":
    main()
