#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import DEFAULT_LLM_MODEL, EXTRACTOR_VERSION
from src.candidate_shadow import (
    build_shadow_candidates,
    build_state_resolution_debug,
    filter_clean_decisions_by_claim_ownership,
    finalize_clean_decisions_with_state_resolution,
)
from src.conflict_detector import detect_conflicts, remove_conflicted_clean_decisions
from src.heuristic_extractor import extract_decisions_from_events
from src.input_loader import load_input_source
from src.llm_extractor import extract_decisions_with_llm
from src.memory_artifacts import build_memory_artifacts
from src.meeting_minutes import (
    apply_meeting_minutes_clean_guard,
    build_clean_memory_payload,
    extract_meeting_memory,
    merge_meeting_decisions,
)
from src.output_writer import write_output
from src.review_queue import build_review_queue, write_review_queue, write_review_queue_sidecar
from src.slack_parser import group_events_by_thread
from src.source_resolver import build_event_lookup
from src.validators import (
    ValidationIssue,
    finalize_clean_tradeoff_canonicalization,
    mark_support_levels,
    postprocess_decisions,
    validate_decisions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract source-backed decision records from fake Slack JSON.")
    parser.add_argument("input_json", help="Path to fake Slack JSON.")
    parser.add_argument("output_json", help="Path to write extracted decision records.")
    parser.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL))
    parser.add_argument("--verify", action="store_true", help="Mark support levels with a local lexical verifier.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print output JSON.")
    parser.add_argument("--max-threads", type=int, default=None)
    parser.add_argument(
        "--include-review-claims",
        action="store_true",
        help="Keep unsupported, indirect, or needs-review claims in clean LLM output and count them as issues.",
    )
    parser.add_argument("--raw-output", help="Path to write raw LLM output before clean filtering.")
    parser.add_argument("--archive-run", action="store_true", help="Also write uniquely named archive output files.")
    parser.add_argument("--run-id", help="Short ID to use for archived output files.")
    parser.add_argument("--no-overwrite", action="store_true", help="Do not replace existing latest output files.")
    parser.add_argument(
        "--source-type",
        choices=["auto", "slack", "google_doc", "uploaded_document", "mixed"],
        default="auto",
        help="Input source type. Defaults to inferring from extension or JSON shape.",
    )
    parser.add_argument(
        "--pipeline",
        choices=["legacy", "candidates-shadow"],
        default="legacy",
        help="Run the existing pipeline, optionally emitting shadow candidate debug artifacts.",
    )
    parser.add_argument("--review-policy", choices=["conservative"], default="conservative")
    parser.add_argument("--auto-approve", choices=["true", "false"], default="false")
    args = parser.parse_args()

    _load_dotenv_if_available()

    ingested = load_input_source(args.input_json, args.source_type)
    events = ingested.canonical_events
    normalized_events = ingested.normalized_events
    source_bundles = ingested.source_bundles
    grouped_threads = group_events_by_thread(events)
    event_lookup = build_event_lookup(events)

    if args.mode == "heuristic":
        decisions = extract_decisions_from_events(grouped_threads, event_lookup, args.max_threads)
    else:
        decisions = extract_decisions_with_llm(grouped_threads, event_lookup, args.model, args.max_threads)

    if args.verify:
        decisions = mark_support_levels(decisions)
    meeting_memory = extract_meeting_memory(normalized_events, event_lookup, args.input_json, args.mode, args.pipeline)

    if args.mode == "llm":
        processed = postprocess_decisions(decisions, event_lookup, include_review_claims=args.include_review_claims)
        merge_meeting_decisions(processed.clean_decisions, processed.raw_decisions, meeting_memory)
        meeting_guard_removed_count = apply_meeting_minutes_clean_guard(processed.clean_decisions, meeting_memory)
        ownership_removed_count = 0
        final_guard_removed_count = 0
        final_tradeoff_change_count = 0
        _shadow_candidates = None
        _shadow_state_resolution = None
        if args.pipeline == "candidates-shadow":
            ownership_removed_count = filter_clean_decisions_by_claim_ownership(normalized_events, processed.clean_decisions)
            _shadow_candidates, _shadow_state_resolution, final_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                normalized_events,
                processed.clean_decisions,
            )
            final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(
                processed.clean_decisions,
                _shadow_state_resolution,
            )
            if final_tradeoff_change_count:
                _shadow_candidates, _shadow_state_resolution, extra_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                    normalized_events,
                    processed.clean_decisions,
                )
                final_guard_removed_count += extra_guard_removed_count
        else:
            final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(processed.clean_decisions)
        conflicts = detect_conflicts(
            candidates=_shadow_candidates,
            raw_decisions=processed.raw_decisions,
            clean_decisions=processed.clean_decisions,
            source_bundles=source_bundles,
            normalized_events=normalized_events,
            state_resolution=_shadow_state_resolution,
        )
        conflict_removed_count = remove_conflicted_clean_decisions(processed.clean_decisions, conflicts)
        if conflict_removed_count and args.pipeline == "candidates-shadow":
            _shadow_candidates, _shadow_state_resolution, _conflict_guard_removed_count = finalize_clean_decisions_with_state_resolution(
                normalized_events,
                processed.clean_decisions,
            )
        review_queue = build_review_queue(
            processed.clean_decisions,
            raw_decisions=processed.raw_decisions,
            candidates=_shadow_candidates,
            state_resolution=_shadow_state_resolution,
            normalized_events=normalized_events,
            source_bundles=source_bundles,
            conflicts=conflicts,
            document_memory=meeting_memory.document_memory_debug,
            input_file=args.input_json,
            mode=args.mode,
            pipeline=args.pipeline,
            validation_issues=[*processed.raw_issues, *processed.clean_issues],
        )
        raw_output = args.raw_output or _default_raw_output_path(args.output_json)
        archive_run = args.pipeline != "candidates-shadow" and (
            args.archive_run or args.no_overwrite or args.run_id is not None or args.mode == "llm"
        )
        default_stable_run = (
            args.pipeline != "candidates-shadow"
            and args.run_id is None
            and not args.archive_run
            and not args.no_overwrite
        )
        run_id = _run_id(args.run_id)
        raw_metadata = _metadata(
            args.input_json,
            processed.raw_decisions,
            args.mode,
            args.model,
            processed.raw_issues,
            raw_validation_issues=processed.raw_issues,
            include_validation_details=True,
        )
        clean_metadata = _metadata(
            args.input_json,
            processed.clean_decisions,
            args.mode,
            args.model,
            processed.clean_issues,
            raw_validation_issues=processed.raw_issues,
            clean_validation_issues=processed.clean_issues,
            removed_claim_count=processed.removed_claim_count + ownership_removed_count + final_guard_removed_count + meeting_guard_removed_count,
            repair_count=processed.repair_count + final_tradeoff_change_count,
            include_validation_details=False,
        )
        written_paths: list[str] = []
        if default_stable_run:
            stable_clean, stable_raw = _stable_output_paths(
                args.output_json,
                raw_output,
                include_raw=True,
                input_json=args.input_json,
            )
            write_output(stable_raw, raw_metadata, processed.raw_decisions, pretty=args.pretty, include_internal=True)
            write_output(stable_clean, clean_metadata, processed.clean_decisions, pretty=args.pretty)
            _write_review_queue_sidecars(review_queue, [stable_clean], pretty=args.pretty)
            written_paths.extend([stable_raw, stable_clean])
        elif archive_run:
            archive_clean, archive_raw = _archive_output_paths(
                args.output_json,
                raw_output,
                run_id,
                include_raw=True,
                input_json=args.input_json,
            )
            write_output(archive_raw, raw_metadata, processed.raw_decisions, pretty=args.pretty, include_internal=True)
            write_output(archive_clean, clean_metadata, processed.clean_decisions, pretty=args.pretty)
            _write_review_queue_sidecars(review_queue, [archive_clean], pretty=args.pretty)
            written_paths.extend([archive_raw, archive_clean])
        if _can_write_latest(raw_output, args.no_overwrite):
            write_output(raw_output, raw_metadata, processed.raw_decisions, pretty=args.pretty, include_internal=True)
            written_paths.append(raw_output)
        if _can_write_latest(args.output_json, args.no_overwrite):
            write_output(args.output_json, clean_metadata, processed.clean_decisions, pretty=args.pretty)
            _write_review_queue_sidecars(review_queue, [args.output_json], pretty=args.pretty)
            written_paths.append(args.output_json)
        shadow_run_dir = None
        if args.pipeline == "candidates-shadow":
            shadow_paths, shadow_run_dir = _write_candidates_shadow_run_artifacts(
                    args.output_json,
                    args.input_json,
                    run_id,
                    normalized_events,
                    source_bundles,
                    clean_metadata,
                    processed.clean_decisions,
                    raw_metadata=raw_metadata,
                    raw_decisions=processed.raw_decisions,
                    review_queue=review_queue,
                    conflicts=conflicts,
                    meeting_memory=meeting_memory,
                    auto_approve=args.auto_approve == "true",
                    pretty=args.pretty,
                )
            written_paths.extend(shadow_paths)
        print(f"Wrote {len(processed.raw_decisions)} raw and {len(processed.clean_decisions)} clean decision record(s).")
        for path in written_paths:
            print(f"Wrote {Path(path).resolve()}")
        if shadow_run_dir:
            print(f"Run folder {Path(shadow_run_dir).resolve()}")
        return

    decisions, issues = validate_decisions(decisions, event_lookup)
    raw_decisions = list(decisions)
    merge_meeting_decisions(decisions, raw_decisions, meeting_memory)
    apply_meeting_minutes_clean_guard(decisions, meeting_memory)
    if args.pipeline == "candidates-shadow":
        filter_clean_decisions_by_claim_ownership(normalized_events, decisions)
        _shadow_candidates, _shadow_state_resolution, _removed_count = finalize_clean_decisions_with_state_resolution(normalized_events, decisions)
        final_tradeoff_change_count = finalize_clean_tradeoff_canonicalization(decisions, _shadow_state_resolution)
        if final_tradeoff_change_count:
            finalize_clean_decisions_with_state_resolution(normalized_events, decisions)
    else:
        _shadow_candidates = None
        _shadow_state_resolution = None
        finalize_clean_tradeoff_canonicalization(decisions)
    conflicts = detect_conflicts(
        candidates=_shadow_candidates,
        raw_decisions=raw_decisions if "raw_decisions" in locals() else None,
        clean_decisions=decisions,
        source_bundles=source_bundles,
        normalized_events=normalized_events,
        state_resolution=_shadow_state_resolution,
    )
    conflict_removed_count = remove_conflicted_clean_decisions(decisions, conflicts)
    if conflict_removed_count and args.pipeline == "candidates-shadow":
        _shadow_candidates, _shadow_state_resolution, _conflict_guard_removed_count = finalize_clean_decisions_with_state_resolution(
            normalized_events,
            decisions,
        )
    review_queue = build_review_queue(
        decisions,
        raw_decisions=raw_decisions,
        candidates=_shadow_candidates,
        state_resolution=_shadow_state_resolution,
        normalized_events=normalized_events,
        source_bundles=source_bundles,
        conflicts=conflicts,
        document_memory=meeting_memory.document_memory_debug,
        input_file=args.input_json,
        mode=args.mode,
        pipeline=args.pipeline,
        validation_issues=issues,
    )
    metadata = _metadata(
        args.input_json,
        decisions,
        args.mode,
        None,
        issues,
        clean_validation_issues=issues,
        include_validation_details=False,
    )

    written_paths = []
    run_id = _run_id(args.run_id) if args.pipeline == "candidates-shadow" or args.archive_run or args.no_overwrite or args.run_id is not None else None
    archive_run = args.pipeline != "candidates-shadow" and (args.archive_run or args.no_overwrite or args.run_id is not None)
    if archive_run:
        archive_clean, _archive_raw = _archive_output_paths(
            args.output_json,
            None,
            run_id or _run_id(args.run_id),
            include_raw=False,
            input_json=args.input_json,
        )
        write_output(archive_clean, metadata, decisions, pretty=args.pretty)
        _write_review_queue_sidecars(review_queue, [archive_clean], pretty=args.pretty)
        written_paths.append(archive_clean)
    if _can_write_latest(args.output_json, args.no_overwrite):
        write_output(args.output_json, metadata, decisions, pretty=args.pretty)
        _write_review_queue_sidecars(review_queue, [args.output_json], pretty=args.pretty)
        written_paths.append(args.output_json)
    shadow_run_dir = None
    if args.pipeline == "candidates-shadow":
        assert run_id is not None
        shadow_paths, shadow_run_dir = _write_candidates_shadow_run_artifacts(
                args.output_json,
                args.input_json,
                run_id,
                normalized_events,
                source_bundles,
                metadata,
                decisions,
                review_queue=review_queue,
                conflicts=conflicts,
                meeting_memory=meeting_memory,
                auto_approve=args.auto_approve == "true",
                pretty=args.pretty,
            )
        written_paths.extend(shadow_paths)
    print(f"Wrote {len(decisions)} decision record(s).")
    for path in written_paths:
        print(f"Wrote {Path(path).resolve()}")
    if shadow_run_dir:
        print(f"Run folder {Path(shadow_run_dir).resolve()}")


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _metadata(
    input_file: str,
    decisions: list[Any],
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


def _default_raw_output_path(output_json: str) -> str:
    output_path = Path(output_json)
    if output_path.name == "decisions.json":
        return str(output_path.with_name("raw_decisions.json"))
    return str(output_path.with_name(f"raw_{output_path.name}"))


def _run_id(requested: str | None = None) -> str:
    if requested:
        sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", requested).strip("_")
        return sanitized or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    return datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")


def _archive_output_paths(
    clean_output: str,
    raw_output: str | None,
    run_id: str,
    include_raw: bool,
    input_json: str | None = None,
) -> tuple[str, str | None]:
    base_archive_suffix = _archive_suffix(run_id, input_json)
    suffix_number = 1
    while True:
        archive_suffix = base_archive_suffix if suffix_number == 1 else f"{base_archive_suffix}_{suffix_number}"
        clean_archive = _archive_path(clean_output, archive_suffix)
        raw_archive = _archive_path(raw_output, archive_suffix) if include_raw and raw_output else None
        candidate_paths = [Path(clean_archive)]
        if raw_archive:
            candidate_paths.append(Path(raw_archive))
        if all(not path.exists() for path in candidate_paths):
            return clean_archive, raw_archive
        suffix_number += 1


def _archive_path(path: str | None, archive_suffix: str) -> str:
    if path is None:
        raise ValueError("Archive path requires a base path.")
    output_path = Path(path)
    suffix = output_path.suffix or ".json"
    return str(output_path.with_name(f"{output_path.stem}_{archive_suffix}{suffix}"))


def _archive_suffix(run_id: str, input_json: str | None) -> str:
    if not input_json:
        return run_id
    scenario = _sanitize_filename_token(Path(input_json).stem)
    if not scenario:
        return run_id
    normalized_run_id = run_id[4:] if run_id.startswith("run_") else run_id
    return f"run_{scenario}_{normalized_run_id}"


def _stable_output_paths(
    clean_output: str,
    raw_output: str | None,
    include_raw: bool,
    input_json: str | None = None,
) -> tuple[str, str | None]:
    scenario = _sanitize_filename_token(Path(input_json or "").stem) or "output"
    suffix = f"run_{scenario}"
    clean_path = _archive_path(clean_output, suffix)
    raw_path = _archive_path(raw_output, suffix) if include_raw and raw_output else None
    return clean_path, raw_path


def _sanitize_filename_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def _can_write_latest(path: str, no_overwrite: bool) -> bool:
    return not (no_overwrite and Path(path).exists())


def _write_candidates_shadow_run_artifacts(
    output_json: str,
    input_json: str,
    run_id: str,
    normalized_events: list[Any],
    bundles: list[Any],
    clean_metadata: dict[str, Any],
    clean_decisions: list[Any],
    raw_metadata: dict[str, Any] | None = None,
    raw_decisions: list[Any] | None = None,
    review_queue: dict[str, Any] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    meeting_memory: Any | None = None,
    auto_approve: bool = False,
    pretty: bool = False,
) -> tuple[list[str], str]:
    run_dir = _run_output_dir(output_json, input_json, run_id)
    candidates = build_shadow_candidates(normalized_events, clean_decisions)
    state_resolution = build_state_resolution_debug(candidates)
    decisions_payload = {
        "metadata": clean_metadata,
        "decisions": [decision.to_dict() for decision in clean_decisions],
    }
    payloads: dict[str, Any] = {
        "decisions.json": decisions_payload,
        "normalized_events_debug.json": [event.to_dict() for event in normalized_events],
        "source_bundles_debug.json": [bundle.to_dict() for bundle in bundles],
        "candidates_debug.json": [candidate.to_dict() for candidate in candidates],
        "state_resolution_debug.json": state_resolution,
        "conflicts_debug.json": conflicts or [],
    }
    clean_memory = None
    if meeting_memory is not None:
        payloads["meeting_metadata.json"] = meeting_memory.meeting_metadata
        clean_memory = build_clean_memory_payload(meeting_memory, review_queue)
        payloads["clean_memory.json"] = clean_memory
        payloads["action_items.json"] = meeting_memory.action_items
        payloads["open_questions.json"] = meeting_memory.open_questions
        payloads["document_memory_debug.json"] = meeting_memory.document_memory_debug
        payloads["block_classification_debug.json"] = meeting_memory.block_classification_debug
    if raw_metadata is not None and raw_decisions is not None:
        payloads["raw_decisions.json"] = {
            "metadata": raw_metadata,
            "decisions": [decision.to_dict(include_internal=True) for decision in raw_decisions],
        }
    if review_queue is None:
        review_queue = build_review_queue(
            clean_decisions,
            raw_decisions=raw_decisions,
            candidates=candidates,
            state_resolution=state_resolution,
            source_bundles=bundles,
            normalized_events=normalized_events,
            conflicts=conflicts or [],
            document_memory=meeting_memory.document_memory_debug if meeting_memory is not None else None,
            input_file=input_json,
            mode=str(clean_metadata.get("mode") or ""),
            pipeline="candidates-shadow",
        )
    payloads["review_queue.json"] = review_queue
    memory_artifacts = build_memory_artifacts(
        input_file=input_json,
        mode=str(clean_metadata.get("mode") or ""),
        pipeline="candidates-shadow",
        decisions_payload=decisions_payload,
        clean_memory=clean_memory,
        review_queue=review_queue,
        coverage_warnings=(meeting_memory.block_classification_debug.get("coverage_audit_warnings", []) if meeting_memory is not None else []),
        auto_approve=auto_approve,
    )
    payloads.update(memory_artifacts)
    written_paths: list[str] = []
    artifact_paths: dict[str, str | None] = {}
    for filename, payload in payloads.items():
        path = run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(_to_jsonable(payload), handle, indent=2 if pretty else None, ensure_ascii=False)
            handle.write("\n")
        written_paths.append(str(path))
        artifact_paths[_artifact_key(filename)] = str(path)
    review_json_path, review_md_path = write_review_queue(review_queue, run_dir, pretty=pretty)
    if str(review_json_path) not in written_paths:
        written_paths.append(str(review_json_path))
    written_paths.append(str(review_md_path))
    artifact_paths["review_queue"] = str(review_json_path)
    artifact_paths["review_queue_md"] = str(review_md_path)
    artifact_paths.setdefault("raw_decisions", None)
    metadata_path = run_dir / "run_metadata.json"
    run_metadata = {
        "run_id": run_dir.name,
        "input_file": input_json,
        "pipeline": "candidates-shadow",
        "mode": clean_metadata.get("mode"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifact_paths,
    }
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(run_metadata, handle, indent=2 if pretty else None, ensure_ascii=False)
        handle.write("\n")
    written_paths.append(str(metadata_path))
    return written_paths, str(run_dir)


def _write_review_queue_sidecars(review_queue: dict[str, Any], output_paths: list[str], pretty: bool) -> None:
    for output_path in output_paths:
        write_review_queue_sidecar(review_queue, output_path, pretty=pretty)


def _run_output_dir(output_json: str, input_json: str, run_id: str) -> Path:
    output_dir = Path(output_json).parent / "runs"
    base_name = _sanitize_filename_token(run_id) or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    suffix_number = 1
    while True:
        folder_name = base_name if suffix_number == 1 else f"{base_name}_{suffix_number}"
        run_dir = output_dir / folder_name
        if not run_dir.exists():
            return run_dir
        suffix_number += 1


def _artifact_key(filename: str) -> str:
    return filename.removesuffix(".json")


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
