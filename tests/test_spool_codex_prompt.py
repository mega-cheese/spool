from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src.spool.merge_authorization.codex_prompt import (
    ClipboardError,
    copy_to_clipboard,
    main,
    render_codex_prompt,
)


class SpoolCodexPromptTest(unittest.TestCase):
    def test_renders_compact_prompt_with_task_only(self) -> None:
        prompt = render_codex_prompt("Add linked GitHub issue ingestion")

        self.assertEqual(
            prompt,
            "Add linked GitHub issue ingestion.\n\n"
            "Follow AGENTS.md and the Spool PR evidence instructions. "
            "When opening or updating the PR, fill the Spool PR template.\n",
        )

    def test_renders_compact_prompt_with_repeated_allowed_paths(self) -> None:
        prompt = render_codex_prompt(
            "Add linked GitHub issue ingestion",
            allowed_paths=["src/spool/merge_authorization/", "scripts/", "tests/"],
        )

        self.assertIn("Allowed files/areas:\n", prompt)
        self.assertIn("- src/spool/merge_authorization/\n", prompt)
        self.assertIn("- scripts/\n", prompt)
        self.assertIn("- tests/\n", prompt)

    def test_compact_prompt_is_short_and_omits_full_boilerplate(self) -> None:
        prompt = render_codex_prompt("Add linked GitHub issue ingestion", allowed_paths=["src/"])

        self.assertLess(len(prompt.split()), 45)
        self.assertNotIn("Before editing:", prompt)
        self.assertNotIn("Do not approve candidate decisions yourself.", prompt)

    def test_renders_full_prompt_with_detailed_spool_rules(self) -> None:
        prompt = render_codex_prompt(
            "Add linked GitHub issue ingestion",
            allowed_paths=["src/spool/merge_authorization/", "scripts/", "tests/"],
            full=True,
        )

        self.assertIn("Before editing:", prompt)
        self.assertIn("- Read AGENTS.md.", prompt)
        self.assertIn("- Follow .spool/agent_instructions.md if present.", prompt)
        self.assertIn("Allowed files/areas:", prompt)
        self.assertIn("Do not claim tests passed unless tests or CI actually passed.", prompt)
        self.assertIn("Do not approve candidate decisions yourself.", prompt)
        self.assertIn("Candidate decisions are proposed project memory, not a change summary.", prompt)
        self.assertIn("Do not list normal implementation details as candidate decisions.", prompt)
        self.assertIn(
            "If the PR only implements an already-approved decision, write `None` or explain that no new durable decisions are introduced.",
            prompt,
        )

    def test_full_prompt_includes_all_spool_pr_section_names(self) -> None:
        prompt = render_codex_prompt("Add linked GitHub issue ingestion", full=True)

        for section in ("## Spool Task", "## Agent Summary", "## Tests Run", "## Candidate Decisions"):
            self.assertIn(section, prompt)
        self.assertNotIn("Allowed files/areas:", prompt)

    def test_default_command_prints_compact_prompt_to_stdout(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["Add linked GitHub issue ingestion"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Add linked GitHub issue ingestion.", output)
        self.assertIn("Follow AGENTS.md", output)
        self.assertNotIn("Before editing:", output)

    def test_out_writes_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / "spool_codex_prompt.txt"
            exit_code = main(["Add linked GitHub issue ingestion", "--out", str(out_path)])
            content = out_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("Add linked GitHub issue ingestion.", content)

    def test_copy_calls_clipboard_helper_with_prompt(self) -> None:
        with mock.patch("src.spool.merge_authorization.codex_prompt.copy_to_clipboard") as mocked_copy:
            exit_code = main(["Add linked GitHub issue ingestion", "--allowed", "src/", "--copy"])

        self.assertEqual(exit_code, 0)
        copied_prompt = mocked_copy.call_args.args[0]
        self.assertIn("Add linked GitHub issue ingestion.", copied_prompt)
        self.assertIn("- src/", copied_prompt)

    def test_clipboard_failure_surfaces_clear_error(self) -> None:
        stderr = io.StringIO()
        with mock.patch(
            "src.spool.merge_authorization.codex_prompt.copy_to_clipboard",
            side_effect=ClipboardError("Clipboard copy failed."),
        ), redirect_stderr(stderr):
            exit_code = main(["Add linked GitHub issue ingestion", "--copy"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Clipboard copy failed", stderr.getvalue())

    def test_copy_to_clipboard_uses_pbcopy_when_available(self) -> None:
        completed = subprocess.CompletedProcess(args=["pbcopy"], returncode=0, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=completed) as mocked_run:
            copy_to_clipboard("Prompt text")

        self.assertEqual(mocked_run.call_args.args[0], ["pbcopy"])
        self.assertEqual(mocked_run.call_args.kwargs["input"], "Prompt text")

    def test_generated_compact_prompt_tells_codex_to_follow_agents_md(self) -> None:
        prompt = render_codex_prompt("Implement a small change")

        self.assertIn("Follow AGENTS.md", prompt)

    def test_generated_prompt_does_not_run_codex_automatically(self) -> None:
        with mock.patch("subprocess.run") as mocked_run:
            main(["Implement a small change"])

        mocked_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
