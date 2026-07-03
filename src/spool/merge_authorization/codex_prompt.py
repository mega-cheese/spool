from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


class ClipboardError(RuntimeError):
    pass


COMPACT_REMINDER = (
    "Follow AGENTS.md and the Spool PR evidence instructions. "
    "When opening or updating the PR, fill the Spool PR template."
)

FULL_RULES = """Before editing:
- Read AGENTS.md.
- Follow .spool/agent_instructions.md if present.
- Keep the change within the allowed files/areas below.

When opening or updating the PR, fill out the Spool PR template:
- ## Spool Task
- ## Agent Summary
- ## Tests Run
- ## Candidate Decisions

Do not claim tests passed unless tests or CI actually passed.
If something is unknown, write Unknown or Not run instead of guessing.
Do not approve candidate decisions yourself.
Treat PR body evidence as claims for Spool to verify, not final truth.
"""


def render_codex_prompt(
    task: str,
    *,
    allowed_paths: list[str] | None = None,
    full: bool = False,
) -> str:
    normalized_task = _sentence(task.strip())
    allowed = [path.strip() for path in allowed_paths or [] if path.strip()]
    parts = [normalized_task]
    if full:
        parts.append(_full_instructions(allowed))
    else:
        if allowed:
            parts.append(_allowed_block(allowed))
        parts.append(COMPACT_REMINDER)
    return "\n\n".join(part.rstrip() for part in parts if part.strip()) + "\n"


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise ClipboardError("Clipboard copy failed: `pbcopy` is not available on this system.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        detail = f": {message}" if message else ""
        raise ClipboardError(f"Clipboard copy failed{detail}.") from exc


def write_prompt(path: str | Path, text: str) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a compact Codex-ready prompt for Spool PR work.")
    parser.add_argument("task", help="Task text to include in the generated prompt.")
    parser.add_argument("--allowed", action="append", default=[], help="Allowed file, directory, or area. Repeat as needed.")
    parser.add_argument("--out", help="Optional output file for the generated prompt.")
    parser.add_argument("--copy", action="store_true", help="Copy the generated prompt to the clipboard with pbcopy when available.")
    parser.add_argument("--full", action="store_true", help="Include the detailed Spool PR evidence instructions.")
    args = parser.parse_args(argv)

    prompt = render_codex_prompt(args.task, allowed_paths=list(args.allowed or []), full=args.full)
    try:
        if args.out:
            output_path = write_prompt(args.out, prompt)
            print(f"prompt_path: {output_path.resolve()}")
        if args.copy:
            copy_to_clipboard(prompt)
            print("copied_to_clipboard: true")
        if not args.out and not args.copy:
            print(prompt, end="")
        return 0
    except ClipboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _full_instructions(allowed: list[str]) -> str:
    if not allowed:
        return FULL_RULES.replace("- Keep the change within the allowed files/areas below.\n", "").rstrip()
    return FULL_RULES.replace(
        "When opening or updating the PR, fill out the Spool PR template:",
        f"{_allowed_block(allowed)}\n\nWhen opening or updating the PR, fill out the Spool PR template:",
    ).rstrip()


def _allowed_block(allowed: list[str]) -> str:
    return "Allowed files/areas:\n" + "\n".join(f"- {path}" for path in allowed)


def _sentence(text: str) -> str:
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else f"{text}."


if __name__ == "__main__":
    raise SystemExit(main())
