from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import DEFAULT_LLM_MODEL, EXTRACTOR_VERSION
from .candidate_shadow import filter_clean_decisions_by_claim_ownership, finalize_clean_decisions_with_state_resolution
from .conflict_detector import detect_conflicts, remove_conflicted_clean_decisions
from .heuristic_extractor import extract_decisions_from_events
from .input_loader import load_input_source
from .llm_extractor import extract_decisions_with_llm
from .memory_artifacts import build_memory_artifacts
from .meeting_minutes import (
    apply_meeting_minutes_clean_guard,
    build_clean_memory_payload,
    extract_meeting_memory,
    merge_meeting_decisions,
)
from .review_queue import build_review_queue, review_queue_to_markdown
from .schemas import DecisionRecord
from .slack_parser import group_events_by_thread
from .source_resolver import build_event_lookup
from .validators import ValidationIssue, finalize_clean_tradeoff_canonicalization, postprocess_decisions, validate_decisions


@dataclass
class UiRunResult:
    run_id: str
    run_dir: Path
    input_file: str
    mode: str
    pipeline: str
    output_paths: dict[str, str | None]


def run_extraction_to_ui_folder(
    input_file: str | Path,
    *,
    mode: str,
    pipeline: str,
    model: str | None = None,
    output_root: str | Path = "outputs/ui_runs",
    run_id: str | None = None,
    source_type: str = "auto",
    pretty: bool = True,
) -> UiRunResult:
    """Run the existing extraction pipeline and write a complete local UI run folder."""
    _load_dotenv_if_available()
    input_path = Path(input_file)
    selected_model = model or os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL)
    run_dir = _create_run_dir(Path(output_root), run_id or _generated_run_id(input_path))

    ingested = load_input_source(input_path, source_type)
    events = ingested.canonical_events
    event_lookup = build_event_lookup(events)
    grouped_threads = group_events_by_thread(events)

    if mode == "llm":
        decisions = extract_decisions_with_llm(grouped_threads, event_lookup, selected_model)
    else:
        decisions = extract_decisions_from_events(grouped_threads, event_lookup)
    meeting_memory = extract_meeting_memory(ingested.normalized_events, event_lookup, str(input_path), mode, pipeline)

    candidates = []
    state_resolution: dict[str, Any] | None = None
    raw_decisions: list[DecisionRecord] | None = None
    review_queue: dict[str, Any]
    conflicts: list[dict[str, Any]]

    if mode == "llm":
        processed = postprocess_decisions(decisions, event_lookup)
        raw_decisions = processed.raw_decisions
        clean_decisions = processed.clean_decisions
        merge_meeting_decisions(clean_decisions, raw_decisions, meeting_memory)
        meeting_guard_removed_count = apply_meeting_minutes_clean_guard(clean_decisions, meeting_memory)
        ownership_removed_count = 0
        final_guard_removed_count = 0
        final_tradeoff_change_count = 0
        if pipeline == "candidates-shadow":
            ownership_removed_count = filter_clean_decisions_by_claim_ownership(ingested.normalized_events, clean_decisions)
            candidates, state_resolution, final_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                ingested.normalized_events,
                clean_decisions,
            )
            final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(clean_decisions, state_resolution)
            if final_tradeoff_change_count:
                candidates, state_resolution, extra_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                    ingested.normalized_events,
                    clean_decisions,
                )
                final_guard_removed_count += extra_guard_removed_count
        else:
            final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(clean_decisions)
        conflicts = detect_conflicts(
            candidates=candidates,
            raw_decisions=raw_decisions,
            clean_decisions=clean_decisions,
            source_bundles=ingested.source_bundles,
            normalized_events=ingested.normalized_events,
            state_resolution=state_resolution,
        )
        conflict_removed_count = remove_conflicted_clean_decisions(clean_decisions, conflicts)
        if conflict_removed_count and pipeline == "candidates-shadow":
            candidates, state_resolution, _conflict_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                ingested.normalized_events,
                clean_decisions,
            )
        raw_metadata = _metadata(
            str(input_path),
            raw_decisions,
            mode,
            selected_model,
            processed.raw_issues,
            raw_validation_issues=processed.raw_issues,
            include_validation_details=True,
        )
        clean_metadata = _metadata(
            str(input_path),
            clean_decisions,
            mode,
            selected_model,
            processed.clean_issues,
            raw_validation_issues=processed.raw_issues,
            clean_validation_issues=processed.clean_issues,
            removed_claim_count=processed.removed_claim_count + ownership_removed_count + final_guard_removed_count + meeting_guard_removed_count,
            repair_count=processed.repair_count + final_tradeoff_change_count,
            include_validation_details=False,
        )
        review_queue = build_review_queue(
            clean_decisions,
            raw_decisions=raw_decisions,
            candidates=candidates,
            state_resolution=state_resolution,
            source_bundles=ingested.source_bundles,
            normalized_events=ingested.normalized_events,
            conflicts=conflicts,
            document_memory=meeting_memory.document_memory_debug,
            input_file=str(input_path),
            mode=mode,
            pipeline=pipeline,
            validation_issues=[*processed.raw_issues, *processed.clean_issues],
        )
    else:
        raw_decisions = copy.deepcopy(decisions)
        clean_decisions, issues = validate_decisions(copy.deepcopy(decisions), event_lookup)
        merge_meeting_decisions(clean_decisions, raw_decisions, meeting_memory)
        apply_meeting_minutes_clean_guard(clean_decisions, meeting_memory)
        if pipeline == "candidates-shadow":
            filter_clean_decisions_by_claim_ownership(ingested.normalized_events, clean_decisions)
            candidates, state_resolution, _removed_count = finalize_clean_decisions_with_state_resolution(
                ingested.normalized_events,
                clean_decisions,
            )
            final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(clean_decisions, state_resolution)
            if final_tradeoff_change_count:
                candidates, state_resolution, _extra_removed_count = finalize_clean_decisions_with_state_resolution(
                    ingested.normalized_events,
                    clean_decisions,
                )
        else:
            finalize_clean_tradeoff_canonicalization(clean_decisions)
        conflicts = detect_conflicts(
            candidates=candidates,
            raw_decisions=raw_decisions,
            clean_decisions=clean_decisions,
            source_bundles=ingested.source_bundles,
            normalized_events=ingested.normalized_events,
            state_resolution=state_resolution,
        )
        conflict_removed_count = remove_conflicted_clean_decisions(clean_decisions, conflicts)
        if conflict_removed_count and pipeline == "candidates-shadow":
            candidates, state_resolution, _conflict_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                ingested.normalized_events,
                clean_decisions,
            )
        raw_metadata = _metadata(str(input_path), raw_decisions, mode, None, [], include_validation_details=True)
        clean_metadata = _metadata(
            str(input_path),
            clean_decisions,
            mode,
            None,
            issues,
            clean_validation_issues=issues,
            include_validation_details=False,
        )
        review_queue = build_review_queue(
            clean_decisions,
            raw_decisions=raw_decisions,
            candidates=candidates,
            state_resolution=state_resolution,
            source_bundles=ingested.source_bundles,
            normalized_events=ingested.normalized_events,
            conflicts=conflicts,
            document_memory=meeting_memory.document_memory_debug,
            input_file=str(input_path),
            mode=mode,
            pipeline=pipeline,
            validation_issues=issues,
        )

    output_paths: dict[str, str | None] = {
        "decisions": str(run_dir / "decisions.json"),
        "raw_decisions": str(run_dir / "raw_decisions.json") if mode == "llm" else None,
        "normalized_events_debug": str(run_dir / "normalized_events_debug.json"),
        "source_bundles_debug": str(run_dir / "source_bundles_debug.json"),
        "candidates_debug": str(run_dir / "candidates_debug.json") if pipeline == "candidates-shadow" else None,
        "state_resolution_debug": str(run_dir / "state_resolution_debug.json") if pipeline == "candidates-shadow" else None,
        "conflicts_debug": str(run_dir / "conflicts_debug.json"),
        "meeting_metadata": str(run_dir / "meeting_metadata.json"),
        "clean_memory": str(run_dir / "clean_memory.json"),
        "action_items": str(run_dir / "action_items.json"),
        "open_questions": str(run_dir / "open_questions.json"),
        "document_memory_debug": str(run_dir / "document_memory_debug.json"),
        "block_classification_debug": str(run_dir / "block_classification_debug.json"),
        "review_queue": str(run_dir / "review_queue.json"),
        "review_queue_md": str(run_dir / "review_queue.md"),
        "candidate_memory": str(run_dir / "candidate_memory.json"),
        "approved_memory": str(run_dir / "approved_memory.json"),
        "rejected_memory": str(run_dir / "rejected_memory.json"),
        "review_actions": str(run_dir / "review_actions.json"),
        "memory_state_debug": str(run_dir / "memory_state_debug.json"),
        "run_metadata": str(run_dir / "run_metadata.json"),
    }
    decisions_payload = {"metadata": clean_metadata, "decisions": [decision.to_dict() for decision in clean_decisions]}
    clean_memory = build_clean_memory_payload(meeting_memory, review_queue)
    memory_artifacts = build_memory_artifacts(
        input_file=str(input_path),
        mode=mode,
        pipeline=pipeline,
        decisions_payload=decisions_payload,
        clean_memory=clean_memory,
        review_queue=review_queue,
        coverage_warnings=meeting_memory.block_classification_debug.get("coverage_audit_warnings", []),
        auto_approve=False,
    )

    _write_json(
        run_dir / "decisions.json",
        decisions_payload,
        pretty=pretty,
    )
    if mode == "llm" and raw_decisions is not None:
        _write_json(
            run_dir / "raw_decisions.json",
            {"metadata": raw_metadata, "decisions": [decision.to_dict(include_internal=True) for decision in raw_decisions]},
            pretty=pretty,
        )
    _write_json(run_dir / "normalized_events_debug.json", [event.to_dict() for event in ingested.normalized_events], pretty=pretty)
    _write_json(run_dir / "source_bundles_debug.json", [bundle.to_dict() for bundle in ingested.source_bundles], pretty=pretty)
    if pipeline == "candidates-shadow":
        _write_json(run_dir / "candidates_debug.json", [candidate.to_dict() for candidate in candidates], pretty=pretty)
        _write_json(run_dir / "state_resolution_debug.json", state_resolution or _empty_state_resolution(), pretty=pretty)
    _write_json(run_dir / "conflicts_debug.json", conflicts, pretty=pretty)
    _write_json(run_dir / "meeting_metadata.json", meeting_memory.meeting_metadata, pretty=pretty)
    _write_json(run_dir / "clean_memory.json", clean_memory, pretty=pretty)
    _write_json(run_dir / "action_items.json", meeting_memory.action_items, pretty=pretty)
    _write_json(run_dir / "open_questions.json", meeting_memory.open_questions, pretty=pretty)
    _write_json(run_dir / "document_memory_debug.json", meeting_memory.document_memory_debug, pretty=pretty)
    _write_json(run_dir / "block_classification_debug.json", meeting_memory.block_classification_debug, pretty=pretty)
    _write_json(run_dir / "review_queue.json", review_queue, pretty=pretty)
    (run_dir / "review_queue.md").write_text(review_queue_to_markdown(review_queue), encoding="utf-8")
    for filename, payload in memory_artifacts.items():
        _write_json(run_dir / filename, payload, pretty=pretty)

    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_dir.name,
            "input_file": str(input_path),
            "mode": mode,
            "pipeline": pipeline,
            "model": selected_model if mode == "llm" else None,
            "source_type": ingested.inferred_source_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": output_paths,
        },
        pretty=pretty,
    )
    return UiRunResult(
        run_id=run_dir.name,
        run_dir=run_dir,
        input_file=str(input_path),
        mode=mode,
        pipeline=pipeline,
        output_paths=output_paths,
    )


def load_json_if_exists(path: str | Path | None) -> Any | None:
    if not path:
        return None
    json_path = Path(path)
    if not json_path.exists():
        return None
    with json_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_uploaded_file(filename: str, data: bytes, upload_root: str | Path = "outputs/ui_uploads") -> Path:
    upload_dir = Path(upload_root)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_token(Path(filename).stem) or "uploaded_input"
    suffix = Path(filename).suffix.lower()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = upload_dir / f"{safe_name}_{timestamp}{suffix}"
    with path.open("wb") as handle:
        handle.write(data)
    return path


def latest_verification_summary(scenario_set: str, scenario_name: str, output_root: str | Path = "outputs/runs") -> Path | None:
    root = Path(output_root)
    if not root.exists():
        return None
    for run_dir in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True):
        manifest_path = run_dir / "batch_manifest.json"
        summary_path = run_dir / "verification_summary.md"
        if not manifest_path.exists() or not summary_path.exists():
            continue
        try:
            manifest = load_json_if_exists(manifest_path) or {}
        except json.JSONDecodeError:
            continue
        if manifest.get("scenario_set") == scenario_set and scenario_name in set(manifest.get("scenario_names", [])):
            return summary_path
    return None


def _metadata(
    input_file: str,
    decisions: list[DecisionRecord],
    mode: str,
    model: str | None,
    validation_issues: list[ValidationIssue],
    raw_validation_issues: list[ValidationIssue] | None = None,
    clean_validation_issues: list[ValidationIssue] | None = None,
    removed_claim_count: int | None = None,
    repair_count: int | None = None,
    include_validation_details: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "input_file": input_file,
        "decision_count": len(decisions),
        "extractor_version": EXTRACTOR_VERSION,
        "mode": mode,
        "model": model,
        "verified": len(validation_issues) == 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_validation_details:
        metadata["validation_issue_count"] = len(validation_issues)
        metadata["validation_issues"] = [issue.to_dict() for issue in validation_issues]
    if raw_validation_issues is not None:
        metadata["raw_validation_issue_count"] = len(raw_validation_issues)
    if clean_validation_issues is not None:
        metadata["clean_validation_issue_count"] = len(clean_validation_issues)
    if removed_claim_count is not None:
        metadata["removed_claim_count"] = removed_claim_count
    if repair_count is not None:
        metadata["repair_count"] = repair_count
        metadata["postprocessing_change_count"] = repair_count + (removed_claim_count or 0)
    return metadata


def _write_json(path: Path, payload: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")


def _create_run_dir(output_root: Path, run_id: str) -> Path:
    safe_run_id = _safe_token(run_id) or _generated_run_id(Path("input"))
    output_root.mkdir(parents=True, exist_ok=True)
    candidate = output_root / safe_run_id
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    suffix = 2
    while True:
        suffixed = output_root / f"{safe_run_id}_{suffix}"
        if not suffixed.exists():
            suffixed.mkdir(parents=True)
            return suffixed
        suffix += 1


def _generated_run_id(input_path: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = _safe_token(input_path.stem) or "input"
    return f"ui_{stem}_{timestamp}"


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value).strip("_")


def _empty_state_resolution() -> dict[str, Any]:
    return {
        "resolver_version": "shadow-0.1",
        "candidate_count": 0,
        "states": {},
        "claim_ownership": [],
        "excluded_from_active_memory": [],
        "duplicate_pairs": [],
    }


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
