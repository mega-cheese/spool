from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from extract_decisions import _archive_output_paths, _can_write_latest, _metadata, main
from src import DEFAULT_LLM_MODEL
from src.heuristic_extractor import extract_decisions_from_events
from src.validators import ValidationIssue


class OutputArchivingTest(unittest.TestCase):
    def test_clean_metadata_omits_legacy_validation_fields(self) -> None:
        metadata = _metadata(
            "data/pricing_change.json",
            [],
            "llm",
            "gpt-5.4-mini",
            [],
            raw_validation_issues=[ValidationIssue("dec_test", "Raw issue.")],
            clean_validation_issues=[],
            removed_claim_count=1,
            include_validation_details=False,
        )

        self.assertNotIn("validation_issue_count", metadata)
        self.assertNotIn("validation_issues", metadata)
        self.assertIn("raw_validation_issue_count", metadata)
        self.assertIn("clean_validation_issue_count", metadata)
        self.assertIn("removed_claim_count", metadata)

    def test_raw_metadata_excludes_clean_only_fields(self) -> None:
        metadata = _metadata(
            "data/pricing_change.json",
            [],
            "llm",
            DEFAULT_LLM_MODEL,
            [ValidationIssue("dec_test", "Raw issue.")],
            raw_validation_issues=[ValidationIssue("dec_test", "Raw issue.")],
            include_validation_details=True,
        )

        self.assertIn("validation_issue_count", metadata)
        self.assertIn("validation_issues", metadata)
        self.assertIn("raw_validation_issue_count", metadata)
        self.assertNotIn("clean_validation_issue_count", metadata)
        self.assertNotIn("removed_claim_count", metadata)
        self.assertNotIn("repair_count", metadata)
        self.assertNotIn("postprocessing_change_count", metadata)

    def test_clean_metadata_reflects_repairs_and_removals(self) -> None:
        metadata = _metadata(
            "data/pricing_change.json",
            [],
            "llm",
            DEFAULT_LLM_MODEL,
            [],
            raw_validation_issues=[],
            clean_validation_issues=[],
            removed_claim_count=2,
            repair_count=1,
            include_validation_details=False,
        )

        self.assertEqual(metadata["removed_claim_count"], 2)
        self.assertEqual(metadata["repair_count"], 1)
        self.assertEqual(metadata["postprocessing_change_count"], 3)

    def test_run_id_creates_clean_and_raw_archive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clean, raw = _archive_output_paths(
                str(Path(tmpdir) / "decisions.json"),
                str(Path(tmpdir) / "raw_decisions.json"),
                "1227",
                include_raw=True,
                input_json="data/vendor_selection.json",
            )

            self.assertTrue(clean.endswith("decisions_run_vendor_selection_1227.json"))
            self.assertTrue(raw and raw.endswith("raw_decisions_run_vendor_selection_1227.json"))

    def test_archive_suffix_includes_each_scenario_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for scenario in ("original_fake", "pricing_change", "mobile_app", "vendor_selection"):
                with self.subTest(scenario=scenario):
                    clean, raw = _archive_output_paths(
                        str(Path(tmpdir) / "decisions.json"),
                        str(Path(tmpdir) / "raw_decisions.json"),
                        "1227",
                        include_raw=True,
                        input_json=f"data/{scenario}.json",
                    )

                    self.assertTrue(clean.endswith(f"decisions_run_{scenario}_1227.json"))
                    self.assertTrue(raw and raw.endswith(f"raw_decisions_run_{scenario}_1227.json"))

    def test_archive_paths_do_not_overwrite_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "decisions_run_vendor_selection_1227.json").write_text("old clean", encoding="utf-8")
            Path(tmpdir, "raw_decisions_run_vendor_selection_1227.json").write_text("old raw", encoding="utf-8")

            clean, raw = _archive_output_paths(
                str(Path(tmpdir) / "decisions.json"),
                str(Path(tmpdir) / "raw_decisions.json"),
                "1227",
                include_raw=True,
                input_json="data/vendor_selection.json",
            )

            self.assertTrue(clean.endswith("decisions_run_vendor_selection_1227_2.json"))
            self.assertTrue(raw and raw.endswith("raw_decisions_run_vendor_selection_1227_2.json"))

    def test_no_overwrite_prevents_replacing_latest_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            latest = Path(tmpdir) / "decisions.json"
            latest.write_text("old", encoding="utf-8")

            self.assertFalse(_can_write_latest(str(latest), no_overwrite=True))
            self.assertTrue(_can_write_latest(str(latest), no_overwrite=False))

    def test_llm_mode_creates_archived_clean_and_raw_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            self._run_mocked_llm(output_path, "--run-id", "1227")

            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change_1227.json").exists())
            self.assertTrue(Path(tmpdir, "raw_decisions_run_pricing_change_1227.json").exists())
            self.assertTrue(Path(tmpdir, "decisions.json").exists())
            self.assertTrue(Path(tmpdir, "raw_decisions.json").exists())

    def test_default_llm_mode_writes_stable_scenario_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            self._run_mocked_llm(output_path)

            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change.json").exists())
            self.assertTrue(Path(tmpdir, "raw_decisions_run_pricing_change.json").exists())
            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change.review_queue.json").exists())
            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change.review_queue.md").exists())
            self.assertEqual(
                sorted(path.name for path in Path(tmpdir).glob("decisions_run_*.json") if ".review_queue" not in path.name),
                ["decisions_run_pricing_change.json"],
            )

    def test_default_llm_mode_overwrites_stable_scenario_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            stable_clean = Path(tmpdir, "decisions_run_pricing_change.json")
            stable_raw = Path(tmpdir, "raw_decisions_run_pricing_change.json")
            stable_clean.write_text("old clean", encoding="utf-8")
            stable_raw.write_text("old raw", encoding="utf-8")

            self._run_mocked_llm(output_path)

            self.assertNotEqual(stable_clean.read_text(encoding="utf-8"), "old clean")
            self.assertNotEqual(stable_raw.read_text(encoding="utf-8"), "old raw")
            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change.review_queue.json").exists())
            self.assertEqual(
                sorted(path.name for path in Path(tmpdir).glob("decisions_run_*.json") if ".review_queue" not in path.name),
                ["decisions_run_pricing_change.json"],
            )

    def test_llm_mode_repeated_run_id_does_not_overwrite_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            self._run_mocked_llm(output_path, "--run-id", "1227")
            first_archive = Path(tmpdir, "decisions_run_pricing_change_1227.json")
            first_archive.write_text("old archive", encoding="utf-8")

            self._run_mocked_llm(output_path, "--run-id", "1227")

            self.assertEqual(first_archive.read_text(encoding="utf-8"), "old archive")
            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change_1227_2.json").exists())
            self.assertTrue(Path(tmpdir, "raw_decisions_run_pricing_change_1227_2.json").exists())

    def test_no_overwrite_skips_existing_latest_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            raw_path = Path(tmpdir) / "raw_decisions.json"
            output_path.write_text("old clean", encoding="utf-8")
            raw_path.write_text("old raw", encoding="utf-8")

            self._run_mocked_llm(output_path, "--run-id", "1227", "--no-overwrite")

            self.assertEqual(output_path.read_text(encoding="utf-8"), "old clean")
            self.assertEqual(raw_path.read_text(encoding="utf-8"), "old raw")
            self.assertTrue(Path(tmpdir, "decisions_run_pricing_change_1227.json").exists())
            self.assertTrue(Path(tmpdir, "raw_decisions_run_pricing_change_1227.json").exists())

    def test_printed_output_paths_are_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            stdout = StringIO()
            self._run_mocked_llm(output_path, "--run-id", "1227", stdout=stdout)

            wrote_lines = [line for line in stdout.getvalue().splitlines() if line.startswith("Wrote /")]
            self.assertTrue(wrote_lines)
            self.assertTrue(all(Path(line.removeprefix("Wrote ")).is_absolute() for line in wrote_lines))

    def test_llm_candidates_shadow_writes_run_folder_with_clean_raw_and_debug_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            self._run_mocked_llm(output_path, "--pipeline", "candidates-shadow", "--run-id", "shadow_test")

            run_dir = Path(tmpdir) / "runs" / "shadow_test"
            self.assertTrue((run_dir / "decisions.json").exists())
            self.assertTrue((run_dir / "raw_decisions.json").exists())
            self.assertTrue((run_dir / "normalized_events_debug.json").exists())
            self.assertTrue((run_dir / "source_bundles_debug.json").exists())
            self.assertTrue((run_dir / "candidates_debug.json").exists())
            self.assertTrue((run_dir / "state_resolution_debug.json").exists())
            self.assertTrue((run_dir / "candidate_memory.json").exists())
            self.assertTrue((run_dir / "approved_memory.json").exists())
            self.assertTrue((run_dir / "rejected_memory.json").exists())
            self.assertTrue((run_dir / "review_actions.json").exists())
            self.assertTrue((run_dir / "memory_state_debug.json").exists())
            self.assertTrue((run_dir / "run_metadata.json").exists())
            self.assertFalse((Path(tmpdir) / "normalized_events_debug.json").exists())
            self.assertFalse((Path(tmpdir) / "decisions_run_pricing_change_shadow_test.json").exists())
            self.assertFalse((Path(tmpdir) / "raw_decisions_run_pricing_change_shadow_test.json").exists())

    def test_shared_model_default_is_used_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            captured = {}

            def fake_llm(grouped_threads, event_lookup, model, max_threads):
                captured["model"] = model
                return extract_decisions_from_events(grouped_threads, event_lookup, max_threads)

            argv = [
                "extract_decisions.py",
                "data/pricing_change.json",
                str(Path(tmpdir) / "decisions.json"),
                "--mode",
                "llm",
            ]
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("sys.argv", argv),
                patch("extract_decisions.extract_decisions_with_llm", side_effect=fake_llm),
                patch("sys.stdout", new_callable=StringIO),
            ):
                main()

            self.assertEqual(captured["model"], DEFAULT_LLM_MODEL)

    def test_cli_metadata_records_actual_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "decisions.json"
            argv = [
                "extract_decisions.py",
                "data/vendor_selection.json",
                str(output_path),
                "--mode",
                "heuristic",
            ]
            with (
                patch("sys.argv", argv),
                patch("sys.stdout", new_callable=StringIO),
            ):
                main()

            self.assertIn('"input_file": "data/vendor_selection.json"', output_path.read_text(encoding="utf-8"))

    def _run_mocked_llm(self, output_path: Path, *extra_args: str, stdout: StringIO | None = None) -> None:
        def fake_llm(grouped_threads, event_lookup, model, max_threads):
            return extract_decisions_from_events(grouped_threads, event_lookup, max_threads)

        argv = [
            "extract_decisions.py",
            "data/pricing_change.json",
            str(output_path),
            "--mode",
            "llm",
            *extra_args,
        ]
        with (
            patch("sys.argv", argv),
            patch("extract_decisions.extract_decisions_with_llm", side_effect=fake_llm),
            patch("sys.stdout", stdout if stdout is not None else StringIO()),
        ):
            main()


if __name__ == "__main__":
    unittest.main()
