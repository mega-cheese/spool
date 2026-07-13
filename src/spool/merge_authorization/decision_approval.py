from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decision_memory import DEFAULT_DECISION_MEMORY_PATH, ApprovedDecision, load_approved_decisions
from .memory_update_writer import _decisions_to_yaml, _existing_to_entries
from .merge_authorization_result import MergeAuthorizationResult
from .pr_comment import PRCommentError, load_report as load_trust_report


class DecisionApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateApprovalSelection:
    index: int
    candidate: dict[str, Any]

    @property
    def title(self) -> str:
        return str(self.candidate.get("title") or "").strip()


@dataclass(frozen=True)
class ApprovalEntry:
    id: str
    title: str
    decision_type: str
    quote: str
    source_candidate_id: str
    entry: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review and optionally approve candidate decisions from trust_report.json.")
    parser.add_argument("--report", required=True, help="Path to trust_report.json.")
    parser.add_argument(
        "--approve",
        action="append",
        default=[],
        help="1-based candidate decision index to approve, or `all`. Repeat as needed.",
    )
    parser.add_argument(
        "--repo-memory",
        default=str(DEFAULT_DECISION_MEMORY_PATH),
        help="Approved decision memory path. Defaults to .spool/approved_decisions.yml.",
    )
    parser.add_argument("--write", action="store_true", help="Actually write approved decisions to repo memory.")
    args = parser.parse_args(argv)

    try:
        result = _load_merge_result(args.report)
        candidates = list(result.candidate_decisions)
        if not candidates:
            print("No candidate decisions found in report.")
            return 0

        approvals = [str(item).strip() for item in args.approve if str(item).strip()]
        if not approvals:
            _print_candidate_list(candidates)
            print("")
            print("Re-run with --approve all for a dry run, or repeat --approve <index> to select decisions.")
            return 0

        selected = _select_candidates(candidates, approvals)
        entries = [_candidate_to_entry(item) for item in selected]

        if not args.write:
            print(f"Dry run: {len(entries)} decision(s) selected for approval.")
            print("Would add:")
            for entry in entries:
                print(f"- {entry.id}: {entry.title}.")
            print("")
            print(f"Re-run with --write to update {args.repo_memory}.")
            return 0

        summary = write_approved_decisions(entries, args.repo_memory)
        print(f"Approved memory updated: {summary['repo_memory_path']}")
        print(f"Added: {summary['added_count']}")
        print(f"Skipped duplicates: {summary['skipped_duplicates_count']}")
        return 0
    except DecisionApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def write_approved_decisions(entries: list[ApprovalEntry], repo_memory_path: str | Path) -> dict[str, Any]:
    destination = Path(repo_memory_path)
    existing_decisions = _load_existing_decisions(destination)
    existing_fingerprints = {_approved_decision_fingerprint(item) for item in existing_decisions}
    kept_entries = _existing_to_entries(existing_decisions)

    added_entries: list[dict[str, Any]] = []
    added_count = 0
    skipped_duplicates = 0
    for entry in entries:
        fingerprint = _entry_fingerprint(entry)
        if fingerprint in existing_fingerprints:
            skipped_duplicates += 1
            continue
        existing_fingerprints.add(fingerprint)
        added_entries.append(entry.entry)
        added_count += 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_decisions_to_yaml([*kept_entries, *added_entries]), encoding="utf-8")
    return {
        "repo_memory_path": str(destination),
        "added_count": added_count,
        "skipped_duplicates_count": skipped_duplicates,
    }


def _load_merge_result(report_path: str | Path) -> MergeAuthorizationResult:
    try:
        payload = load_trust_report(report_path)
    except PRCommentError as exc:
        raise DecisionApprovalError(str(exc)) from exc
    return MergeAuthorizationResult.from_dict(payload)


def _print_candidate_list(candidates: list[dict[str, Any]]) -> None:
    print("Candidate decisions in report:")
    for index, candidate in enumerate(candidates, start=1):
        title = str(candidate.get("title") or "").strip().rstrip(".")
        print(f"{index}. {title}.")


def _select_candidates(candidates: list[dict[str, Any]], approvals: list[str]) -> list[CandidateApprovalSelection]:
    if any(item.lower() == "all" for item in approvals):
        return [
            CandidateApprovalSelection(index=index, candidate=dict(candidate))
            for index, candidate in enumerate(candidates, start=1)
        ]

    max_index = len(candidates)
    selected_indexes: list[int] = []
    for raw in approvals:
        try:
            index = int(raw)
        except ValueError as exc:
            raise DecisionApprovalError(f"Invalid approval index: {raw}. Use a 1-based integer or `all`.") from exc
        if index < 1 or index > max_index:
            raise DecisionApprovalError(f"Invalid approval index: {index}. Report has {max_index} candidate decision(s).")
        if index not in selected_indexes:
            selected_indexes.append(index)
    return [CandidateApprovalSelection(index=index, candidate=dict(candidates[index - 1])) for index in selected_indexes]


def _candidate_to_entry(selection: CandidateApprovalSelection) -> ApprovalEntry:
    candidate = selection.candidate
    title = str(candidate.get("title") or "").strip()
    quote = str(candidate.get("quote") or title).strip()
    decision_type = str(candidate.get("decision_type") or candidate.get("type") or "decision").strip()
    source_candidate_id = str(candidate.get("id") or f"CAND-{selection.index:03d}")
    decision_id = _decision_id(title=title, decision_type=decision_type, quote=quote)
    entry = {
        "id": decision_id,
        "status": "approved",
        "title": title,
        "type": decision_type,
        "scope": _candidate_scope(candidate),
        "must": [quote] if quote else [],
        "must_not": [],
        "requires_approval_for": [],
        "approved_by": [],
        "evidence": [{"source_type": "candidate_decision", "quote": quote}] if quote else [],
        "source_candidate_id": source_candidate_id,
    }
    return ApprovalEntry(
        id=decision_id,
        title=title,
        decision_type=decision_type,
        quote=quote,
        source_candidate_id=source_candidate_id,
        entry=entry,
    )


def _load_existing_decisions(path: Path) -> list[ApprovedDecision]:
    memory = load_approved_decisions(path)
    if memory.status == "missing":
        return []
    if memory.status != "available":
        raise DecisionApprovalError(f"Existing approved decision memory could not be loaded: {memory.status}.")
    return list(memory.decisions)


def _decision_id(*, title: str, decision_type: str, quote: str) -> str:
    normalized = "\n".join(
        [
            decision_type.strip().lower(),
            " ".join(title.strip().lower().split()),
            " ".join(quote.strip().lower().split()),
        ]
    )
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:6]
    return f"decision_{digest}"


def _candidate_scope(candidate: dict[str, Any]) -> dict[str, list[str]]:
    scope = candidate.get("scope")
    if not isinstance(scope, dict):
        return {"applies_to_paths": [], "applies_to_concepts": []}
    return {
        "applies_to_paths": [str(item) for item in scope.get("applies_to_paths") or [] if str(item).strip()],
        "applies_to_concepts": [str(item) for item in scope.get("applies_to_concepts") or [] if str(item).strip()],
    }


def _entry_fingerprint(entry: ApprovalEntry) -> tuple[str, str, str]:
    return (
        entry.decision_type.strip().lower(),
        " ".join(entry.title.strip().lower().split()),
        " ".join(entry.quote.strip().lower().split()),
    )


def _approved_decision_fingerprint(decision: ApprovedDecision) -> tuple[str, str, str]:
    quote = ""
    if decision.evidence:
        quote = decision.evidence[0].quote
    elif decision.must:
        quote = decision.must[0]
    else:
        quote = decision.summary_text
    return (
        decision.type.strip().lower(),
        " ".join(decision.title.strip().lower().split()),
        " ".join(str(quote).strip().lower().split()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
