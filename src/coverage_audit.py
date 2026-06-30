from __future__ import annotations

from typing import Any


def audit_task_coverage(
    blocks: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    task_spans: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    actions_by_block: dict[str, list[dict[str, Any]]] = {}
    actions_by_span: dict[str, list[dict[str, Any]]] = {}
    for action in action_items:
        block_id = action.get("block_id")
        if block_id:
            actions_by_block.setdefault(str(block_id), []).append(action)
        span_id = action.get("span_id")
        if span_id:
            actions_by_span.setdefault(str(span_id), []).append(action)

    owner_task_line_count = 0
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        extracted = actions_by_block.get(block_id, [])
        likely_rows = block.get("likely_task_rows") or []
        if block.get("block_type") == "task_section" and not extracted:
            warnings.append(
                _warning(
                    "high",
                    "Action items may have been missed",
                    "Document contains an explicit task section but no action items were extracted from it.",
                    block,
                )
            )
        if block.get("block_type") == "task_table" and likely_rows:
            coverage = len(extracted) / len(likely_rows)
            if coverage < 0.5:
                warnings.append(
                    _warning(
                        "high",
                        "Task table may have been under-extracted",
                        "Document appears to contain a task table, but many likely task rows were not extracted.",
                        block,
                    )
                )
        owner_task_line_count += len(likely_rows)

    for span in task_spans or []:
        likely_rows = span.get("likely_task_rows") or []
        line_count = int(span.get("line_count") or len(span.get("lines") or []))
        if not likely_rows:
            if line_count == 0:
                continue
        extracted = actions_by_span.get(str(span.get("span_id") or ""), [])
        if not extracted:
            warnings.append(
                _warning_for_span(
                    "high",
                    "Explicit To Do section produced no action items",
                    "Document contains an explicit To Do span, but no action items were extracted from it.",
                    span,
                )
            )
        elif len(likely_rows) >= 3 and len(extracted) / len(likely_rows) < 0.7:
            warnings.append(
                _warning_for_span(
                    "high",
                    "Explicit To Do section may have been under-extracted",
                    "Document contains an explicit To Do span, but several likely task rows were not extracted.",
                    span,
                )
            )

    if owner_task_line_count >= 5 and len(action_items) < 3:
        first_block = next((block for block in blocks if block.get("likely_task_rows")), blocks[0] if blocks else {})
        warnings.append(
            _warning(
                "medium",
                "Owner-task lines may have been missed",
                "Document contains multiple owner-task patterns but few action items were extracted.",
                first_block,
            )
        )
    return _dedupe_warnings(warnings)


def build_block_classification_debug(
    blocks: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    coverage_warnings: list[dict[str, Any]],
    task_spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions_by_block: dict[str, list[str]] = {}
    actions_by_span: dict[str, list[str]] = {}
    for action in action_items:
        block_id = action.get("block_id")
        if block_id:
            actions_by_block.setdefault(str(block_id), []).append(str(action.get("object_id") or ""))
        span_id = action.get("span_id")
        if span_id:
            actions_by_span.setdefault(str(span_id), []).append(str(action.get("object_id") or ""))
    debug_blocks = []
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        debug_blocks.append(
            {
                **{key: value for key, value in block.items() if key != "source"},
                "extracted_action_item_ids": actions_by_block.get(block_id, []),
            }
        )
    return {
        "blocks": debug_blocks,
        "detected_task_spans": [_debug_span(span, actions_by_span) for span in task_spans or []],
        "coverage_audit_warnings": coverage_warnings,
    }


def _warning(severity: str, title: str, summary: str, block: dict[str, Any]) -> dict[str, Any]:
    source = dict(block.get("source") or {})
    if source:
        source["quote"] = block.get("text") or ""
    return {
        "reason": "extraction_warning",
        "severity": severity,
        "title": title,
        "summary": summary,
        "candidate_decision": title,
        "recommended_action": "Review block classification and task extraction output.",
        "source_event_ids": [block["source_event_id"]] if block.get("source_event_id") else [],
        "sources": [source] if source else [],
        "related_candidate_ids": [],
    }


def _warning_for_span(severity: str, title: str, summary: str, span: dict[str, Any]) -> dict[str, Any]:
    source = dict(span.get("source") or {})
    quote = " ".join(str(line.get("text") or line.get("body") or "") for line in span.get("lines", [])[:8])
    if source:
        source["quote"] = quote
    return {
        "reason": "extraction_warning",
        "severity": severity,
        "title": title,
        "summary": summary,
        "candidate_decision": title,
        "recommended_action": "Review explicit task-span extraction output.",
        "source_event_ids": list(span.get("source_event_ids") or []),
        "sources": [source] if source else [],
        "related_candidate_ids": [],
    }


def _debug_span(span: dict[str, Any], actions_by_span: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "span_id": span.get("span_id"),
        "heading": span.get("heading"),
        "source_event_ids": list(span.get("source_event_ids") or []),
        "block_ids": list(span.get("block_ids") or []),
        "start_line_index": span.get("start_line_index"),
        "end_line_index": span.get("end_line_index"),
        "line_count": span.get("line_count", len(span.get("lines") or [])),
        "quote": span.get("quote"),
        "likely_task_rows": list(span.get("likely_task_rows") or []),
        "extracted_action_item_ids": actions_by_span.get(str(span.get("span_id") or ""), []),
    }


def _dedupe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for warning in warnings:
        key = (warning.get("title"), tuple(warning.get("source_event_ids") or []))
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result
