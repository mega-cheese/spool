from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent_change_packet import (
    build_agent_change_artifact_payloads,
    audit_agent_claims,
    detect_agent_change_packet,
    extract_agent_claims,
    parse_agent_change_packet,
)
from src.input_loader import load_input_source
from src.meeting_minutes import build_clean_memory_payload, classify_document, extract_meeting_memory
from src.memory_artifacts import build_memory_artifacts
from src.review_queue import build_review_queue
from src.ui_helpers import load_json_if_exists, run_extraction_to_ui_folder


FIXTURE = "data/agent_change_packet_v081.md"


class AgentChangePacketTest(unittest.TestCase):
    def test_detector_classifies_agent_packet_before_meeting_minutes(self) -> None:
        ingested = load_input_source(FIXTURE)

        self.assertTrue(detect_agent_change_packet(ingested.normalized_events))
        self.assertEqual(classify_document(ingested.normalized_events), "agent_change_packet")

    def test_parser_finds_expected_sections_and_preserves_git_diff(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)

        self.assertEqual(packet.title, "Agent Change Packet: Spool v0.8.1 Review Actions")
        for section in (
            "Original Request",
            "Agent Implementation Summary",
            "Claimed Capabilities",
            "Validation Claimed by Agent",
            "Git Stat",
            "Git Diff",
        ):
            self.assertIn(section, packet.sections_found)
        self.assertIn("new file mode 100644", packet.git_diff)
        self.assertIn("diff --git a/src/review_actions.py", packet.git_diff)

    def test_fenced_diff_headings_do_not_split_agent_packet_sections(self) -> None:
        markdown = (
            "# Agent Change Packet: Fence Safety\n\n"
            "## Original Request\n\nAdd review actions.\n\n"
            "## Agent Implementation Summary\n\n- Added `review_memory.py`.\n\n"
            "## Claimed Capabilities\n\n- Appends to review_actions.json.\n\n"
            "## Validation Claimed by Agent\n\nPASS, 212 tests\n\n"
            "## Existing Product Rules / Approved Context\n\n- Raw extraction is not trusted memory.\n\n"
            "## Git Stat\n\nreview_memory.py | 50 +++++\n\n"
            "## Git Diff\n\n"
            "```diff\n"
            "diff --git a/README.md b/README.md\n"
            "+## Review Actions\n"
            " ```bash\n"
            " venv/bin/python review_memory.py --action approve\n"
            " ## Batch Scenario Verification\n"
            " ```\n"
            "+## Batch Scenario Verification\n"
            "diff --git a/review_memory.py b/review_memory.py\n"
            "new file mode 100755\n"
            "+parser.add_argument(\"--action\")\n"
            "```\n\n"
            "## Questions for Spool Audit\n\n- Did scope stay tight?\n\n"
            "## Expected Useful Spool Output\n\n- A concise audit.\n"
        )

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(markdown)
            temp_path = handle.name

        try:
            ingested = load_input_source(temp_path)
            packet = parse_agent_change_packet(ingested.normalized_events)
            artifacts = build_agent_change_artifact_payloads(
                temp_path,
                packet,
                extract_agent_claims(packet),
                audit_agent_claims(packet, extract_agent_claims(packet)),
            )

            self.assertEqual(classify_document(ingested.normalized_events), "agent_change_packet")
            self.assertIn("+## Review Actions", packet.git_diff)
            self.assertIn("+## Batch Scenario Verification", packet.git_diff)
            self.assertNotIn("Review Actions", packet.sections_found)
            self.assertNotIn("Batch Scenario Verification", packet.sections_found)
            self.assertGreater(artifacts["agent_change_packet_debug.json"]["raw_section_lengths"]["git_diff"], 170)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_scope_audit_separates_requested_features_from_non_goals(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)
        report = audit_agent_claims(packet, extract_agent_claims(packet))
        scope = report.scope_audit

        self.assertIn("requested_features", scope)
        self.assertIn("explicit_non_goals", scope)
        self.assertIn("implemented_features", scope)
        self.assertIn("review action model", scope["requested_features"])
        self.assertIn("review action CLI", scope["requested_features"])
        self.assertIn("candidate/approved/rejected/review_actions artifact updates", scope["requested_features"])
        self.assertIn("browser extension", scope["explicit_non_goals"])
        self.assertIn("database", scope["explicit_non_goals"])
        self.assertNotIn("browser extension", scope["requested_features"])
        self.assertNotEqual(scope["scope_verdict"], "partially_out_of_scope")

    def test_scope_audit_ignores_negative_or_metadata_mentions_of_non_goals(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)
        packet.git_diff = (
            "commit abc\n"
            "Author: Owen Qu <owen@example.com>\n"
            "+Use review_memory.py without Streamlit or a database.\n"
            "+Store review actions in JSON artifacts rather than a database.\n"
        )

        report = audit_agent_claims(packet, extract_agent_claims(packet))

        self.assertEqual(report.scope_audit["possible_scope_issues"], [])
        self.assertNotEqual(report.scope_audit["scope_verdict"], "partially_out_of_scope")

    def test_claim_extraction_and_deterministic_evidence_matching(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)
        claims = extract_agent_claims(packet)
        report = audit_agent_claims(packet, claims)
        verdicts = {claim.claim: claim.verdict for claim in report.claims}

        self.assertEqual(verdicts["The implementation added src/review_actions.py."], "supported")
        self.assertEqual(verdicts["The implementation added review_memory.py."], "supported")
        self.assertEqual(
            verdicts["The CLI supports approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale."],
            "supported",
        )
        self.assertEqual(verdicts["review_actions.json is append-only."], "supported")
        self.assertEqual(verdicts["Prevents silent approval of unsupported or contradicted memory."], "supported")
        self.assertEqual(verdicts["Unit tests passed with 212 tests."], "weakly_supported")
        self.assertEqual(verdicts["Manual smoke passed in Streamlit."], "weakly_supported")
        self.assertEqual(verdicts["JSON artifact writes may not be atomic."], "needs_review")

    def test_claim_extraction_omits_standalone_tokens_and_groups_nested_capabilities(self) -> None:
        markdown = (
            "# Agent Change Packet: Claim Quality\n\n"
            "## Original Request\n\nImplement review actions.\n\n"
            "## Agent Implementation Summary\n\nChanged:\n- `src/review_actions.py`\n- `review_memory.py`\n\n"
            "## Claimed Capabilities\n\n"
            "The agent claims that v0.8.1 now supports:\n\n"
            "1. Applying review actions:\n"
            "   - approve\n"
            "   - reject\n"
            "   - edit\n"
            "   - mark_needs_review\n"
            "   - mark_resolved\n"
            "   - mark_unresolved\n"
            "   - mark_superseded\n"
            "   - mark_stale\n\n"
            "2. Updating review-state artifacts:\n"
            "   - `candidate_memory.json`\n"
            "   - `approved_memory.json`\n"
            "   - `rejected_memory.json`\n"
            "   - `memory_state_debug.json`\n\n"
            "3. Appending to:\n"
            "   - `review_actions.json`\n\n"
            "## Validation Claimed by Agent\n\nPASS, 212 tests\n\n"
            "## Existing Product Rules / Approved Context\n\n- Raw extraction is not trusted memory.\n\n"
            "## Git Stat\n\nsrc/review_actions.py | 100 +++++\nreview_memory.py | 20 ++\n\n"
            "## Git Diff\n\n```diff\n"
            "diff --git a/src/review_actions.py b/src/review_actions.py\n"
            "new file mode 100644\n"
            "+ALLOWED_ACTIONS = {\"approve\", \"reject\", \"edit\", \"mark_needs_review\", \"mark_resolved\", \"mark_unresolved\", \"mark_superseded\", \"mark_stale\"}\n"
            "+def append_review_action(path, action):\n+    actions.append(action)\n"
            "diff --git a/review_memory.py b/review_memory.py\n"
            "new file mode 100755\n"
            "+parser.add_argument(\"--action\", choices=sorted(ALLOWED_ACTIONS))\n"
            "```\n\n"
            "## Questions for Spool Audit\n\n- Did this work?\n\n"
            "## Expected Useful Spool Output\n\n- Useful claims.\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(markdown)
            temp_path = handle.name

        try:
            packet = parse_agent_change_packet(load_input_source(temp_path).normalized_events)
            report = audit_agent_claims(packet, extract_agent_claims(packet))
            claim_texts = [claim.claim for claim in report.claims]

            self.assertIn("The implementation added src/review_actions.py.", claim_texts)
            self.assertIn("The implementation added review_memory.py.", claim_texts)
            self.assertIn(
                "The CLI supports approve, reject, edit, mark_needs_review, mark_resolved, mark_unresolved, mark_superseded, and mark_stale.",
                claim_texts,
            )
            self.assertIn(
                "Review actions update candidate_memory.json, approved_memory.json, rejected_memory.json, and memory_state_debug.json.",
                claim_texts,
            )
            self.assertIn("review_actions.json is append-only.", claim_texts)
            for bad_claim in (
                "src/review_actions.py.",
                "review_memory.py.",
                "approve.",
                "reject.",
                "candidate_memory.json.",
                "mark_resolved.",
            ):
                self.assertNotIn(bad_claim, claim_texts)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_validation_pass_count_and_manual_smoke_lines_are_weakly_supported(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)
        packet.validation_claimed = (
            "venv/bin/python -m unittest discover -s tests: PASS, 212 tests\n\n"
            "Manual smoke test claimed:\n\n"
            "Created a fresh Meeting #12 run.\n"
            "Approved one commitment into approved_memory.json.\n"
        )

        report = audit_agent_claims(packet, extract_agent_claims(packet))
        verdicts = {claim.claim: claim.verdict for claim in report.claims}

        self.assertEqual(verdicts["Unit tests passed with 212 tests."], "weakly_supported")
        self.assertEqual(verdicts["Created a fresh Meeting #12 run."], "weakly_supported")
        self.assertEqual(verdicts["Approved one commitment into approved_memory.json."], "weakly_supported")

    def test_agent_packet_extraction_builds_candidate_memory_without_task_table_warning(self) -> None:
        ingested = load_input_source(FIXTURE)
        event_lookup = {event.external_id: event for event in ingested.normalized_events}
        extraction = extract_meeting_memory(ingested.normalized_events, event_lookup, FIXTURE, "heuristic", "candidates-shadow")
        review_queue = build_review_queue(
            [],
            normalized_events=ingested.normalized_events,
            source_bundles=ingested.source_bundles,
            document_memory=extraction.document_memory_debug,
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
        )
        clean_memory = build_clean_memory_payload(extraction, review_queue)
        memory_artifacts = build_memory_artifacts(
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
            decisions_payload={"metadata": {"decision_count": 0}, "decisions": []},
            clean_memory=clean_memory,
            review_queue=review_queue,
            coverage_warnings=extraction.block_classification_debug.get("coverage_audit_warnings", []),
            extra_candidates=extraction.extra_candidate_memory,
        )

        self.assertEqual(extraction.classification, "agent_change_packet")
        self.assertGreater(len(extraction.agent_claims["claims"]), 5)
        self.assertGreater(len(extraction.agent_audit_report["technical_decisions"]), 0)
        self.assertGreater(len(extraction.agent_audit_report["risks"]), 0)
        self.assertGreater(len(extraction.agent_audit_report["open_questions"]), 0)
        self.assertFalse(extraction.block_classification_debug.get("coverage_audit_warnings"))
        self.assertFalse(any("Task table" in item.get("summary", "") for item in review_queue["items"]))

        candidate_memory = memory_artifacts["candidate_memory.json"]["candidates"]
        types = {item["type"] for item in candidate_memory}
        self.assertIn("decision", types)
        self.assertIn("risk", types)
        self.assertIn("open_question", types)
        self.assertIn("context", types)
        self.assertLess(len(candidate_memory), 30)
        self.assertTrue(all(item["review_state"] != "approved" for item in candidate_memory))
        self.assertTrue(all(item["title"].strip() and item["summary"].strip() for item in candidate_memory))
        self.assertTrue(all(item["quote"].strip() != "---" for item in candidate_memory))
        self.assertTrue(all(item["source_event_ids"] and item["sources"] and item["quote"] for item in candidate_memory))

    def test_agent_packet_candidates_do_not_auto_approve(self) -> None:
        ingested = load_input_source(FIXTURE)
        extraction = extract_meeting_memory(ingested.normalized_events, {}, FIXTURE, "heuristic", "candidates-shadow")
        memory_artifacts = build_memory_artifacts(
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
            decisions_payload={"metadata": {"decision_count": 0}, "decisions": []},
            clean_memory=build_clean_memory_payload(extraction),
            review_queue={"items": []},
            coverage_warnings=[],
            extra_candidates=extraction.extra_candidate_memory,
            auto_approve=True,
        )

        approved = memory_artifacts["approved_memory.json"]

        self.assertEqual(approved["metadata"]["approved_count"], 0)
        self.assertFalse(
            any(item["review_state"] == "approved" for item in memory_artifacts["candidate_memory.json"]["candidates"])
        )

    def test_open_questions_keep_source_evidence_and_rejected_memory_stays_empty(self) -> None:
        ingested = load_input_source(FIXTURE)
        extraction = extract_meeting_memory(ingested.normalized_events, {}, FIXTURE, "heuristic", "candidates-shadow")
        review_queue = build_review_queue(
            [],
            normalized_events=ingested.normalized_events,
            source_bundles=ingested.source_bundles,
            document_memory=extraction.document_memory_debug,
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
        )
        clean_memory = build_clean_memory_payload(extraction, review_queue)
        memory_artifacts = build_memory_artifacts(
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
            decisions_payload={"metadata": {"decision_count": 0}, "decisions": []},
            clean_memory=clean_memory,
            review_queue=review_queue,
            coverage_warnings=[],
            extra_candidates=extraction.extra_candidate_memory,
        )

        self.assertEqual(memory_artifacts["rejected_memory.json"]["metadata"]["rejected_count"], 0)
        for question in extraction.open_questions["open_questions"]:
            self.assertTrue(question["source_event_ids"])
            self.assertTrue(question["sources"])
            self.assertTrue(question["quote"])
            self.assertEqual(question["metadata"]["packet_type"], "agent_change_packet")

    def test_review_queue_groups_validation_evidence_and_uses_agent_reason_codes(self) -> None:
        ingested = load_input_source(FIXTURE)
        extraction = extract_meeting_memory(ingested.normalized_events, {}, FIXTURE, "heuristic", "candidates-shadow")
        review_queue = build_review_queue(
            [],
            normalized_events=ingested.normalized_events,
            source_bundles=ingested.source_bundles,
            document_memory=extraction.document_memory_debug,
            input_file=FIXTURE,
            mode="heuristic",
            pipeline="candidates-shadow",
        )
        reasons = [item["reason"] for item in review_queue["items"]]
        summaries = [item["summary"] for item in review_queue["items"]]

        self.assertLessEqual(sum(reason == "weak_validation_evidence" for reason in reasons), 1)
        self.assertIn("weak_validation_evidence", reasons)
        self.assertTrue(set(reasons) <= {"unsupported_agent_claim", "weak_validation_evidence", "possible_scope_drift", "missing_source_evidence"})
        self.assertTrue(any("Validation claims are self-reported" in summary for summary in summaries))
        self.assertFalse(any(reason == "extraction_warning" for reason in reasons))

    def test_markdown_report_is_concise_and_omits_useless_standalone_claims(self) -> None:
        packet = parse_agent_change_packet(load_input_source(FIXTURE).normalized_events)
        claims = extract_agent_claims(packet)
        report = audit_agent_claims(packet, claims)
        markdown = build_agent_change_artifact_payloads(FIXTURE, packet, claims, report)["agent_audit_report.md"]

        for heading in (
            "## Overall Verdict",
            "## Scope Audit",
            "## Key Supported Claims",
            "## Weak Claims / Missing Evidence",
            "## Unsupported Claims",
            "## Technical Decisions",
            "## Risks",
            "## Open Questions",
            "## Reviewer Checklist",
        ):
            self.assertIn(heading, markdown)
        for bad in ("- approve.", "- reject.", "- candidate_memory.json.", "- resolved."):
            self.assertNotIn(bad, markdown)
        self.assertIn('Claim: "The implementation added src/review_actions.py."', markdown)
        self.assertIn("Evidence:", markdown)

    def test_candidate_subheadings_extract_risks_and_open_questions(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(
                "# Agent Change Packet: Separate Candidate Sections\n\n"
                "## Original Request\n\nKeep review actions append-only.\n\n"
                "## Agent Implementation Summary\n\n- Added `src/review_actions.py`.\n\n"
                "## Claimed Capabilities\n\n- Appends to review_actions.json.\n\n"
                "## Validation Claimed by Agent\n\nRan 1 tests in 0.01s\nOK\n\n"
                "## Existing Product Rules / Approved Context\n\n- Raw extraction is not trusted memory.\n\n"
                "## Git Stat\n\nsrc/review_actions.py | 10 +++++\n\n"
                "## Git Diff\n\n```diff\ndiff --git a/src/review_actions.py b/src/review_actions.py\nnew file mode 100644\n+def append_review_action(path, action):\n+    actions.append(action)\n```\n\n"
                "## Expected Useful Spool Output\n\n"
                "### Candidate Technical Decisions\n\n- Store review actions in JSON artifacts rather than a database.\n\n"
                "### Candidate Risks\n\n- JSON artifact writes may not be atomic.\n\n"
                "### Candidate Open Questions\n\n- Should weakly_supported memory be approvable?\n"
            )
            temp_path = handle.name

        try:
            extraction = extract_meeting_memory(load_input_source(temp_path).normalized_events, {}, temp_path, "heuristic", "candidates-shadow")
            types = {item.type for item in extraction.extra_candidate_memory or []}

            self.assertIn("decision", types)
            self.assertIn("risk", types)
            self.assertIn("open_question", types)
            self.assertGreater(extraction.open_questions["metadata"]["open_question_count"], 0)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_ui_helper_writes_agent_packet_artifacts_and_metadata_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_extraction_to_ui_folder(
                FIXTURE,
                mode="heuristic",
                pipeline="candidates-shadow",
                output_root=Path(temp_dir),
                run_id="agent_packet_ui",
            )

            run_dir = result.run_dir
            for filename in (
                "agent_change_packet_debug.json",
                "agent_claims.json",
                "agent_audit_report.json",
                "agent_audit_report.md",
            ):
                self.assertTrue((run_dir / filename).exists())

            meeting_metadata = load_json_if_exists(run_dir / "meeting_metadata.json")
            candidate_memory = load_json_if_exists(run_dir / "candidate_memory.json")
            approved_memory = load_json_if_exists(run_dir / "approved_memory.json")
            review_actions = load_json_if_exists(run_dir / "review_actions.json")
            run_metadata = load_json_if_exists(run_dir / "run_metadata.json")

            self.assertEqual(meeting_metadata["classification"], "agent_change_packet")
            self.assertGreater(candidate_memory["metadata"]["candidate_count"], 3)
            self.assertEqual(approved_memory["metadata"]["approved_count"], 0)
            self.assertEqual(review_actions["metadata"]["action_count"], 0)
            for key in (
                "agent_change_packet_debug",
                "agent_claims",
                "agent_audit_report",
                "agent_audit_report_md",
            ):
                self.assertEqual(run_metadata["artifacts"][key], result.output_paths[key])
                self.assertTrue(Path(run_metadata["artifacts"][key]).exists())

            report = json.loads((run_dir / "agent_audit_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["metadata"]["classification"], "agent_change_packet")
            self.assertTrue(report["review_checklist"])


if __name__ == "__main__":
    unittest.main()
