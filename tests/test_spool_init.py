from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.spool.merge_authorization.init_repo import SPOOL_AGENTS_MARKER, init_repo, main


class SpoolInitTest(unittest.TestCase):
    def test_init_creates_spool_setup_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary = init_repo(root)

            self.assertTrue((root / ".spool" / "config.yml").exists())
            self.assertTrue((root / ".spool" / "approved_decisions.yml").exists())
            self.assertTrue((root / ".spool" / "agent_instructions.md").exists())
            self.assertTrue((root / ".github" / "pull_request_template.md").exists())
            self.assertTrue((root / "AGENTS.md").exists())

        self.assertEqual(summary["created_count"], 5)
        self.assertEqual(summary["exists_count"], 0)
        self.assertEqual(summary["updated_count"], 0)

    def test_generated_pr_template_contains_required_spool_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            init_repo(root)
            template = (root / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

        for heading in ("## Spool Task", "## Agent Summary", "## Tests Run", "## Candidate Decisions"):
            self.assertIn(heading, template)

        self.assertIn(
            "List only durable product, architecture, security, API, policy, or process decisions "
            "that future PRs should respect.",
            template,
        )
        self.assertIn(
            "Do not list ordinary implementation details, tests, docs, refactors, bug fixes, or file additions.",
            template,
        )
        self.assertIn('If this PR introduces no new durable decisions, write "None."', template)

    def test_generated_agent_instructions_define_candidate_decisions_as_project_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            init_repo(root)
            instructions = (root / ".spool" / "agent_instructions.md").read_text(encoding="utf-8")

        self.assertIn("Candidate decisions are proposed project memory, not a change summary.", instructions)
        self.assertIn("Do not list normal implementation details as candidate decisions.", instructions)
        self.assertIn(
            "If the PR only implements an already-approved decision, write `None` or explain that no new durable decisions are introduced.",
            instructions,
        )

    def test_init_creates_or_appends_agents_md_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agents = root / "AGENTS.md"
            agents.write_text("# Existing Instructions\n\nKeep scope narrow.\n", encoding="utf-8")

            summary = init_repo(root)
            text = agents.read_text(encoding="utf-8")

        self.assertIn("# Existing Instructions", text)
        self.assertIn(SPOOL_AGENTS_MARKER, text)
        self.assertIn(".spool/agent_instructions.md", text)
        self.assertEqual(summary["updated"], ["AGENTS.md"])

    def test_init_does_not_overwrite_existing_pr_template_or_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pr_template = root / ".github" / "pull_request_template.md"
            config = root / ".spool" / "config.yml"
            pr_template.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            pr_template.write_text("custom template\n", encoding="utf-8")
            config.write_text("custom config\n", encoding="utf-8")

            summary = init_repo(root)

            self.assertEqual(pr_template.read_text(encoding="utf-8"), "custom template\n")
            self.assertEqual(config.read_text(encoding="utf-8"), "custom config\n")

        self.assertIn(".github/pull_request_template.md", summary["exists"])
        self.assertIn(".spool/config.yml", summary["exists"])

    def test_init_does_not_duplicate_agents_md_spool_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            init_repo(root)
            first = (root / "AGENTS.md").read_text(encoding="utf-8")
            summary = init_repo(root)
            second = (root / "AGENTS.md").read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(second.count(SPOOL_AGENTS_MARKER), 1)
        self.assertIn("AGENTS.md", summary["exists"])

    def test_main_prints_useful_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["--root", temp_dir])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("created:", output)
        self.assertIn(".spool/config.yml", output)
        self.assertIn(".github/pull_request_template.md", output)

    def test_flags_can_skip_agents_md_and_pr_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary = init_repo(root, include_agents_md=False, include_pr_template=False)

            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / ".github" / "pull_request_template.md").exists())
            self.assertTrue((root / ".spool" / "config.yml").exists())

        self.assertNotIn("AGENTS.md", summary["created"])
        self.assertNotIn(".github/pull_request_template.md", summary["created"])

    def test_repo_gitignore_ignores_local_spool_outputs_but_not_committed_spool_files(self) -> None:
        gitignore = Path(".gitignore").read_text(encoding="utf-8")
        lines = {line.strip() for line in gitignore.splitlines() if line.strip() and not line.strip().startswith("#")}

        self.assertIn("outputs/", lines)
        self.assertIn("dogfood/", lines)
        self.assertIn("examples.zip", lines)
        self.assertIn("examples/pr_trust_real/", lines)
        self.assertIn("__pycache__/", lines)
        self.assertIn("*.pyc", lines)
        self.assertIn(".spool/local/", lines)
        self.assertNotIn(".spool/", lines)


if __name__ == "__main__":
    unittest.main()
