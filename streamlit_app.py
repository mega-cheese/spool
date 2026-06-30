from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import streamlit as st
except ImportError:  # Allows import in environments that have not installed UI deps yet.
    st = None

from run_scenarios import SCENARIO_SETS
from src import DEFAULT_LLM_MODEL
from src.schemas import CLAIM_CATEGORIES
from src.ui_helpers import (
    UiRunResult,
    latest_verification_summary,
    load_json_if_exists,
    run_extraction_to_ui_folder,
    save_uploaded_file,
)


def run_app() -> None:
    if st is None:
        raise RuntimeError("Streamlit is not installed. Run: python3 -m pip install -r requirements.txt")

    _load_dotenv_if_available()
    st.set_page_config(page_title="Spool", page_icon=None, layout="wide")

    controls = _sidebar_controls()
    st.title("Spool")
    st.caption("Verified company memory")

    if controls["run_clicked"]:
        _run_extraction(controls)

    result = st.session_state.get("ui_run_result")
    if not result:
        st.info("Choose a fixture or upload a local file, then run extraction.")
        return

    _render_run(result)


def _sidebar_controls() -> dict[str, Any]:
    with st.sidebar:
        st.title("Spool")
        st.caption("Verified company memory")

        scenario_set = st.selectbox("Scenario set", ["all", "docs", "review", "conflict", "meeting_minutes"])
        scenarios = SCENARIO_SETS[scenario_set]
        scenario = st.selectbox("Scenario", scenarios, index=_default_scenario_index(scenarios))

        llm_available = bool(os.getenv("OPENAI_API_KEY"))
        mode_options = ["heuristic", "llm"]
        mode = st.selectbox("Mode", mode_options, index=1 if llm_available else 0)
        if mode == "llm" and not llm_available:
            st.warning("LLM mode needs OPENAI_API_KEY in your environment or .env.")

        pipeline = st.selectbox("Pipeline", ["legacy", "candidates-shadow"], index=1)
        model = st.text_input("Model", value=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL), disabled=mode != "llm")

        uploaded_file = st.file_uploader("Optional upload", type=["json", "md", "txt", "docx", "pdf"])
        run_clicked = st.button("Run extraction", type="primary", width="stretch")

    return {
        "scenario_set": scenario_set,
        "scenario": scenario,
        "mode": mode,
        "pipeline": pipeline,
        "model": model,
        "uploaded_file": uploaded_file,
        "run_clicked": run_clicked,
    }


def _run_extraction(controls: dict[str, Any]) -> None:
    try:
        input_path = Path(controls["scenario"])
        uploaded_file = controls["uploaded_file"]
        if uploaded_file is not None:
            input_path = save_uploaded_file(uploaded_file.name, uploaded_file.getvalue())

        with st.spinner("Running extraction..."):
            result = run_extraction_to_ui_folder(
                input_path,
                mode=controls["mode"],
                pipeline=controls["pipeline"],
                model=controls["model"],
            )
        st.session_state.ui_run_result = result
        st.session_state.ui_scenario_set = controls["scenario_set"]
        st.session_state.ui_scenario_name = Path(controls["scenario"]).stem if uploaded_file is None else Path(input_path).stem
        st.success(f"Run complete: {result.run_dir}")
    except Exception as exc:  # Streamlit should show useful local debugging context.
        st.error("Extraction failed.")
        st.exception(exc)


def _render_run(result: UiRunResult) -> None:
    run_dir = Path(result.run_dir)
    decisions_payload = load_json_if_exists(run_dir / "decisions.json") or {}
    raw_payload = load_json_if_exists(run_dir / "raw_decisions.json")
    state_resolution = load_json_if_exists(run_dir / "state_resolution_debug.json")
    source_bundles = load_json_if_exists(run_dir / "source_bundles_debug.json")
    candidates = load_json_if_exists(run_dir / "candidates_debug.json")
    normalized_events = load_json_if_exists(run_dir / "normalized_events_debug.json")
    conflicts = load_json_if_exists(run_dir / "conflicts_debug.json")
    meeting_metadata = load_json_if_exists(run_dir / "meeting_metadata.json")
    clean_memory = load_json_if_exists(run_dir / "clean_memory.json")
    action_items = load_json_if_exists(run_dir / "action_items.json")
    open_questions = load_json_if_exists(run_dir / "open_questions.json")
    document_memory = load_json_if_exists(run_dir / "document_memory_debug.json")
    block_debug = load_json_if_exists(run_dir / "block_classification_debug.json")
    review_queue = load_json_if_exists(run_dir / "review_queue.json")

    st.caption(f"Run folder: `{run_dir}`")
    st.write(f"Input file: `{result.input_file}`")
    st.write(f"Mode: `{result.mode}`  Pipeline: `{result.pipeline}`")
    _render_metrics(decisions_payload, raw_payload, state_resolution, candidates, review_queue)

    tabs = st.tabs(
        [
            "Confirmed Memory" if _is_meeting_minutes(meeting_metadata, clean_memory) else "Confirmed Decisions",
            "Meeting Metadata",
            "Action Items",
            "Open Questions",
            "Review Queue",
            "Conflicts",
            "Excluded From Active Memory",
            "Claim Ownership",
            "Source Evidence",
            "Candidate Debug",
            "Verification Summary",
            "Raw JSON",
        ]
    )
    with tabs[0]:
        _render_clean_memory(decisions_payload, clean_memory=clean_memory)
    with tabs[1]:
        _render_meeting_metadata(meeting_metadata)
    with tabs[2]:
        _render_meeting_action_items(action_items)
    with tabs[3]:
        _render_open_questions(open_questions)
    with tabs[4]:
        _render_review_queue(review_queue)
    with tabs[5]:
        _render_conflicts(conflicts)
    with tabs[6]:
        _render_excluded_memory(state_resolution)
    with tabs[7]:
        _render_claim_ownership(state_resolution)
    with tabs[8]:
        _render_source_evidence(source_bundles)
    with tabs[9]:
        _render_candidates(candidates)
    with tabs[10]:
        _render_verification_summary()
    with tabs[11]:
        _render_raw_json(
            {
                "decisions.json": decisions_payload,
                "raw_decisions.json": raw_payload,
                "review_queue.json": review_queue,
                "conflicts_debug.json": conflicts,
                "meeting_metadata.json": meeting_metadata,
                "clean_memory.json": clean_memory,
                "action_items.json": action_items,
                "open_questions.json": open_questions,
                "document_memory_debug.json": document_memory,
                "block_classification_debug.json": block_debug,
                "state_resolution_debug.json": state_resolution,
                "source_bundles_debug.json": source_bundles,
                "candidates_debug.json": candidates,
                "normalized_events_debug.json": normalized_events,
            }
        )


def _render_metrics(
    decisions_payload: dict[str, Any],
    raw_payload: dict[str, Any] | None,
    state_resolution: dict[str, Any] | None,
    candidates: list[dict[str, Any]] | None,
    review_queue: dict[str, Any] | None,
) -> None:
    clean_count = len(decisions_payload.get("decisions", []))
    raw_count = len(raw_payload.get("decisions", [])) if isinstance(raw_payload, dict) else None
    candidate_count = len(candidates) if isinstance(candidates, list) else None
    excluded_count = len(state_resolution.get("excluded_from_active_memory", [])) if isinstance(state_resolution, dict) else None
    review_count = len(review_queue.get("items", [])) if isinstance(review_queue, dict) else None

    cols = st.columns(5)
    cols[0].metric("Clean decisions", clean_count)
    cols[1].metric("Raw decisions", raw_count if raw_count is not None else "Not available")
    cols[2].metric("Candidates", candidate_count if candidate_count is not None else "Not available")
    cols[3].metric("Excluded claims", excluded_count if excluded_count is not None else "Not available")
    cols[4].metric("Review items", review_count if review_count is not None else "Not available")


def _render_clean_memory(payload: dict[str, Any], clean_memory: dict[str, Any] | None = None) -> None:
    if _is_meeting_minutes(None, clean_memory):
        _render_confirmed_meeting_memory(clean_memory)
        return

    decisions = payload.get("decisions", [])
    if not decisions:
        st.info("No clean decisions for this run.")
        return

    for decision in decisions:
        with st.container(border=True):
            st.subheader(decision.get("decision") or "Untitled decision")
            cols = st.columns(6)
            cols[0].metric("Status", decision.get("status") or "Unknown")
            cols[1].metric("Owner", decision.get("owner") or "Unknown")
            cols[2].metric("Owner role", decision.get("owner_role") or "Unknown")
            cols[3].metric("Date", decision.get("date") or "None")
            cols[4].metric("Confidence", decision.get("confidence", "Unknown"))
            cols[5].metric("Effective date", decision.get("effective_date") or "None")
            if decision.get("revisit_trigger"):
                st.write(f"Revisit trigger: {decision['revisit_trigger']}")
            if decision.get("revisit_date"):
                st.write(f"Revisit date: {decision['revisit_date']}")

            for category in CLAIM_CATEGORIES:
                with st.expander(category.replace("_", " ").title(), expanded=bool(decision.get(category))):
                    _render_claim_list(decision.get(category, []))

            with st.expander("Deadlines", expanded=bool(decision.get("deadlines"))):
                _render_deadlines(decision.get("deadlines", []))
            with st.expander("Action Items", expanded=bool(decision.get("action_items"))):
                _render_action_items(decision.get("action_items", []))
            for label, field in (
                ("Decision Sources", "decision_sources"),
                ("Supporting Sources", "supporting_sources"),
                ("All Sources", "all_sources"),
            ):
                with st.expander(label, expanded=False):
                    _render_sources(decision.get(field, []))


def _render_confirmed_meeting_memory(clean_memory: dict[str, Any] | None) -> None:
    if clean_memory is None:
        st.info("clean_memory.json not found for this run.")
        return
    memory_items = clean_memory.get("confirmed_memory", [])
    if not memory_items:
        st.info("No confirmed memory for this run.")
        return
    for item in memory_items:
        with st.container(border=True):
            st.subheader(item.get("title") or item.get("summary") or "Untitled memory")
            st.caption(f"Type: `{_memory_type_label(item.get('type'))}` | Status: `{item.get('status') or 'unknown'}` | Confidence: {item.get('confidence') or 'unknown'}")
            details = []
            if item.get("date"):
                details.append(f"Date: {item['date']}")
            if item.get("due_date"):
                details.append(f"Due: {item['due_date']}")
            if item.get("owner_names"):
                details.append(f"Owner: {', '.join(item['owner_names'])}")
            if details:
                st.caption(" | ".join(details))
            if item.get("summary") and item.get("summary") != item.get("title"):
                st.write(item["summary"])
            for source in item.get("sources") or []:
                if source.get("quote"):
                    st.code(source["quote"])


def _is_meeting_minutes(meeting_metadata: dict[str, Any] | None, clean_memory: dict[str, Any] | None) -> bool:
    metadata = clean_memory.get("metadata", {}) if isinstance(clean_memory, dict) else {}
    return (
        (isinstance(meeting_metadata, dict) and meeting_metadata.get("classification") == "meeting_minutes")
        or metadata.get("classification") == "meeting_minutes"
    )


def _memory_type_label(memory_type: Any) -> str:
    return {
        "confirmed_decision": "decision",
        "deadline": "deadline",
        "policy_or_process": "policy/process",
        "change": "change",
    }.get(str(memory_type or ""), str(memory_type or "memory"))


def _render_claim_list(claims: list[dict[str, Any]]) -> None:
    if not claims:
        st.caption("None")
        return
    for claim in claims:
        with st.container(border=True):
            st.write(claim.get("claim", ""))
            st.caption(f"Support: {claim.get('support_level', 'unknown')} | Confidence: {claim.get('confidence', 'unknown')}")
            _render_source(claim.get("source"))


def _render_deadlines(deadlines: list[dict[str, Any]]) -> None:
    if not deadlines:
        st.caption("None")
        return
    for deadline in deadlines:
        with st.container(border=True):
            st.write(f"{deadline.get('date')}: {deadline.get('description')}")
            _render_source(deadline.get("source"))


def _render_action_items(action_items: list[dict[str, Any]]) -> None:
    if not action_items:
        st.caption("None")
        return
    for item in action_items:
        with st.container(border=True):
            due = item.get("due_date") or item.get("due_text") or "No due date"
            st.write(item.get("action", ""))
            st.caption(f"Owner: {item.get('owner') or 'Unassigned'} | Due: {due}")
            _render_source(item.get("source"))


def _render_sources(sources: list[dict[str, Any]]) -> None:
    if not sources:
        st.caption("None")
        return
    for source in sources:
        with st.container(border=True):
            _render_source(source)


def _render_source(source: dict[str, Any] | None) -> None:
    if not source:
        st.caption("No source attached.")
        return
    parts = [
        f"type={source.get('source_type')}",
        f"speaker={source.get('speaker') or 'Unknown'}",
        f"role={source.get('speaker_role') or 'Unknown'}",
        f"channel/document={source.get('channel') or source.get('channel_id') or 'Unknown'}",
        f"locator={source.get('message_ts')}",
    ]
    st.caption(" | ".join(parts))
    if source.get("url"):
        st.markdown(f"[Open source]({source['url']})")
    if source.get("quote"):
        st.code(source["quote"])


def _render_excluded_memory(state_resolution: dict[str, Any] | None) -> None:
    if state_resolution is None:
        st.info("state_resolution_debug.json not found for this run.")
        return
    excluded = state_resolution.get("excluded_from_active_memory", [])
    if not excluded:
        st.info("No excluded claims for this run.")
        return
    for item in excluded:
        with st.container(border=True):
            st.write(item.get("claim", ""))
            st.caption(
                f"Reason: {item.get('reason') or 'Unknown'} | "
                f"Target: {item.get('target_name') or 'Unknown'} | "
                f"Role: {item.get('target_role') or 'Unknown'}"
            )


def _render_review_queue(review_queue: dict[str, Any] | None) -> None:
    if review_queue is None:
        st.info("review_queue.json not found for this run.")
        return
    items = review_queue.get("items", [])
    if not items:
        st.info("No review items for this run.")
        return
    for item in items:
        severity = item.get("severity") or "unknown"
        if severity == "high":
            st.error(item.get("title") or "Decision needs human review")
        elif severity == "medium":
            st.warning(item.get("title") or "Decision needs human review")
        else:
            st.info(item.get("title") or "Decision needs human review")
        with st.container(border=True):
            st.write(f"Status: `{item.get('status')}` | Reason: `{item.get('reason')}` | Severity: `{severity}`")
            st.write(f"Candidate decision: {item.get('candidate_decision') or 'Unknown'}")
            st.write(item.get("summary") or "")
            _render_conflict_options(item.get("options") or [])
            st.write(f"Recommended action: {item.get('recommended_action') or 'Review this item.'}")
            st.caption(f"Related candidate IDs: {', '.join(item.get('related_candidate_ids') or []) or 'None'}")
            st.caption(f"Source event IDs: {', '.join(item.get('source_event_ids') or []) or 'None'}")
            sources = item.get("sources") or []
            if not sources:
                st.caption("Evidence unavailable or not yet linked.")
            for source in sources:
                actor = source.get("actor") or source.get("speaker") or "Unknown"
                st.caption(
                    f"{actor} | {source.get('source_type') or 'unknown source'} | "
                    f"{source.get('locator') or source.get('message_ts') or 'unknown locator'}"
                )
                if source.get("quote"):
                    st.code(source["quote"])


def _render_meeting_metadata(meeting_metadata: dict[str, Any] | None) -> None:
    if meeting_metadata is None:
        st.info("meeting_metadata.json not found for this run.")
        return
    if meeting_metadata.get("classification") != "meeting_minutes":
        st.info(f"Document classification: {meeting_metadata.get('classification') or 'unknown'}")
        return
    cols = st.columns(4)
    cols[0].metric("Classification", meeting_metadata.get("classification") or "unknown")
    cols[1].metric("Meeting date", meeting_metadata.get("meeting_date") or "None")
    cols[2].metric("Location", meeting_metadata.get("location") or "None")
    cols[3].metric("Source events", meeting_metadata.get("source_event_count", 0))
    st.write("Present:", ", ".join(meeting_metadata.get("present") or []) or "None")
    st.write("Absent:", ", ".join(meeting_metadata.get("absent") or []) or "None")


def _render_meeting_action_items(action_items: dict[str, Any] | None) -> None:
    if action_items is None:
        st.info("action_items.json not found for this run.")
        return
    items = action_items.get("action_items", [])
    if not items:
        st.info("No meeting action items for this run.")
        return
    for item in items:
        with st.container(border=True):
            st.write(item.get("title") or item.get("summary") or "Untitled action item")
            st.caption(
                f"Owner: {', '.join(item.get('owner_names') or []) or 'Unassigned'} | "
                f"Due: {item.get('due_date') or item.get('date') or 'None'} | "
                f"Status: {item.get('status') or 'unknown'}"
            )
            for source in item.get("sources") or []:
                if source.get("quote"):
                    st.code(source["quote"])


def _render_open_questions(open_questions: dict[str, Any] | None) -> None:
    if open_questions is None:
        st.info("open_questions.json not found for this run.")
        return
    questions = open_questions.get("open_questions", [])
    if not questions:
        st.info("No open questions for this run.")
        return
    for question in questions:
        with st.container(border=True):
            st.write(question.get("title") or question.get("summary") or "Untitled open question")
            st.caption(f"Status: {question.get('status') or 'open'} | Confidence: {question.get('confidence') or 'unknown'}")
            for source in question.get("sources") or []:
                if source.get("quote"):
                    st.code(source["quote"])


def _render_conflicts(conflicts: list[dict[str, Any]] | None) -> None:
    if conflicts is None:
        st.info("conflicts_debug.json not found for this run.")
        return
    if not conflicts:
        st.info("No conflicts for this run.")
        return
    for conflict in conflicts:
        st.error(conflict.get("summary") or "Conflicting sources detected.")
        with st.container(border=True):
            st.write(f"Conflict area: {conflict.get('conflict_area') or 'Unknown'}")
            st.write(f"Severity: `{conflict.get('severity')}` | Status: `{conflict.get('status')}`")
            _render_conflict_options(conflict.get("options") or [])
            st.write(f"Recommended action: {conflict.get('recommended_action') or 'Human review required.'}")
            st.caption(f"Source event IDs: {', '.join(conflict.get('source_event_ids') or []) or 'None'}")
            for source in conflict.get("sources") or []:
                actor = source.get("actor") or source.get("speaker") or "Unknown"
                heading = source.get("section_heading")
                heading_text = f" | {heading}" if heading else ""
                st.caption(
                    f"{actor} | {source.get('source_type') or 'unknown source'}{heading_text} | "
                    f"{source.get('locator') or source.get('message_ts') or 'unknown locator'}"
                )
                if source.get("quote"):
                    st.code(source["quote"])


def _render_conflict_options(options: list[dict[str, Any]]) -> None:
    if not options:
        return
    st.write("Options")
    for option in options:
        source_type = str(option.get("source_type") or "source").replace("_", " ").title()
        heading = option.get("section_heading")
        label = f"{source_type} {heading} section" if heading else source_type
        st.caption(f"{option.get('option') or 'Unknown option'} - {label}")
        if option.get("quote"):
            st.code(option["quote"])


def _render_claim_ownership(state_resolution: dict[str, Any] | None) -> None:
    if state_resolution is None:
        st.info("state_resolution_debug.json not found for this run.")
        return
    ownership = state_resolution.get("claim_ownership", [])
    if not ownership:
        st.info("No claim ownership records for this run.")
        return
    st.dataframe(ownership, width="stretch", hide_index=True)


def _render_source_evidence(source_bundles: list[dict[str, Any]] | None) -> None:
    if source_bundles is None:
        st.info("source_bundles_debug.json not found for this run.")
        return
    if not source_bundles:
        st.info("No source bundles for this run.")
        return
    for bundle in source_bundles:
        title = bundle.get("title") or bundle.get("bundle_id") or "Untitled source bundle"
        with st.expander(title, expanded=True):
            st.caption(
                f"Bundle type: {bundle.get('bundle_type')} | "
                f"Source types: {', '.join(bundle.get('source_types', []))} | "
                f"Root id: {bundle.get('root_id')} | "
                f"Time range: {bundle.get('time_range', {})}"
            )
            for event in bundle.get("events", []):
                actor = event.get("actor", {})
                with st.container(border=True):
                    st.write(f"{actor.get('name') or 'Unknown'} · {actor.get('role') or 'Unknown role'}")
                    st.caption(
                        f"{event.get('created_at') or 'No timestamp'} | "
                        f"{event.get('source_type')} / {event.get('source_subtype')}"
                    )
                    text = (event.get("content") or {}).get("text")
                    quote = (event.get("provenance") or {}).get("quote")
                    if text:
                        st.write(text)
                    if quote and quote != text:
                        st.code(quote)
                    if event.get("source_url"):
                        st.markdown(f"[Open source]({event['source_url']})")


def _render_candidates(candidates: list[dict[str, Any]] | None) -> None:
    if candidates is None:
        st.info("candidates_debug.json not found for this run.")
        return
    if not candidates:
        st.info("No candidates for this run.")
        return
    for candidate in candidates:
        label = candidate.get("proposed_action") or candidate.get("candidate_id") or "Candidate"
        with st.expander(label, expanded=False):
            st.write(f"State: `{candidate.get('state')}`")
            st.write(f"Confidence: `{candidate.get('confidence')}`")
            st.write(f"Normalized action: `{candidate.get('normalized_action')}`")
            st.write("Source event ids:", candidate.get("source_event_ids", []))
            st.write("Decision event ids:", candidate.get("decision_event_ids", []))
            st.write("Approval event ids:", candidate.get("approval_event_ids", []))
            claims = candidate.get("claims", [])
            if claims:
                st.dataframe(claims, width="stretch", hide_index=True)
            else:
                st.caption("No candidate claims.")


def _render_verification_summary() -> None:
    scenario_set = st.session_state.get("ui_scenario_set")
    scenario_name = st.session_state.get("ui_scenario_name")
    if not scenario_set or not scenario_name:
        st.info("No selected scenario metadata for this run.")
        return
    summary_path = latest_verification_summary(scenario_set, scenario_name)
    if summary_path is None:
        st.info("No verification_summary.md found for this scenario in outputs/runs.")
        return
    st.caption(f"Verification summary: `{summary_path}`")
    st.markdown(summary_path.read_text(encoding="utf-8"))


def _render_raw_json(payloads: dict[str, Any | None]) -> None:
    for name, payload in payloads.items():
        with st.expander(name, expanded=False):
            if payload is None:
                st.info(f"{name} not found for this run.")
            else:
                st.json(payload)


def _default_scenario_index(scenarios: list[str]) -> int:
    preferred = "data/mixed_slack_doc_vendor_selection.json"
    return scenarios.index(preferred) if preferred in scenarios else 0


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


if __name__ == "__main__":
    run_app()
