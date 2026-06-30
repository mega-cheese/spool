#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.memory_store import apply_action_to_store
from src.review_actions import create_review_action


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a review action to a Spool run folder.")
    parser.add_argument("--run-dir", required=True, help="Path to a run folder containing candidate_memory.json.")
    parser.add_argument("--memory-id", required=True)
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "approve",
            "reject",
            "edit",
            "mark_needs_review",
            "mark_resolved",
            "mark_unresolved",
            "mark_superseded",
            "mark_stale",
        ],
    )
    parser.add_argument("--reviewer", default="human")
    parser.add_argument("--reason")
    parser.add_argument("--patch", default="{}")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    try:
        patch = json.loads(args.patch)
    except json.JSONDecodeError as exc:
        print(f"Invalid --patch JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("--patch must be a JSON object.", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir)
    action = create_review_action(
        args.memory_id,
        args.action,
        reviewer=args.reviewer,
        reason=args.reason,
        patch=patch,
    )
    result = apply_action_to_store(run_dir, action, pretty=args.pretty)
    if result["errors"]:
        for error in result["errors"]:
            print(f"Error: {error.get('code')}: {error.get('message')}", file=sys.stderr)
        return 1
    updated = result.get("updated_memory") or {}
    print(f"memory_id: {args.memory_id}")
    print(f"action: {args.action}")
    print(f"review_state: {updated.get('review_state')}")
    print(f"status: {updated.get('status')}")
    print(f"approved_count: {len(result['approved_memory'])}")
    print(f"rejected_count: {len(result['rejected_memory'])}")
    print(f"action_count: {len(result['actions'])}")
    if result["warnings"]:
        print("warnings: " + ", ".join(str(warning.get("code")) for warning in result["warnings"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
