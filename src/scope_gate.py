from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class FileChange:
    path: str
    change_type: str
    old_path: str | None = None
    new_path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "old_path": self.old_path,
            "new_path": self.new_path,
        }


@dataclass
class ScopeReport:
    task_source: str
    approved_task: str
    allowed_files: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    changed_files: list[FileChange] = field(default_factory=list)
    scope_violations: list[str] = field(default_factory=list)
    forbidden_violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_source": self.task_source,
            "approved_task": self.approved_task,
            "allowed_files": list(self.allowed_files),
            "forbidden_paths": list(self.forbidden_paths),
            "changed_files": [change.to_dict() for change in self.changed_files],
            "scope_violations": list(self.scope_violations),
            "forbidden_violations": list(self.forbidden_violations),
        }


def parse_changed_files(diff_text: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: dict[str, str | None] | None = None

    def flush() -> None:
        if not current:
            return
        old_path = current.get("old_path")
        new_path = current.get("new_path")
        change_type = current.get("change_type") or "modified"
        path = new_path if new_path and new_path != "/dev/null" else old_path
        if path:
            changes.append(FileChange(path=path, change_type=change_type, old_path=old_path, new_path=new_path))

    for line in diff_text.splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            flush()
            old_path, new_path = match.group(1), match.group(2)
            current = {"old_path": old_path, "new_path": new_path, "change_type": "modified"}
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current["change_type"] = "added"
        elif line.startswith("deleted file mode"):
            current["change_type"] = "deleted"
        elif line.startswith("rename from "):
            current["old_path"] = line.replace("rename from ", "", 1).strip()
            current["change_type"] = "renamed"
        elif line.startswith("rename to "):
            current["new_path"] = line.replace("rename to ", "", 1).strip()
            current["change_type"] = "renamed"
        elif line.startswith("--- /dev/null"):
            current["old_path"] = "/dev/null"
            current["change_type"] = "added"
        elif line.startswith("+++ /dev/null"):
            current["new_path"] = "/dev/null"
            current["change_type"] = "deleted"
    flush()
    return _dedupe_changes(changes)


def evaluate_scope(task_text: str, changed_files: list[FileChange], *, task_source: str = "task") -> ScopeReport:
    allowed_files = _extract_allowed_files(task_text)
    forbidden_paths = _extract_forbidden_paths(task_text)
    changed_paths = [change.path for change in changed_files]
    scope_violations = [path for path in changed_paths if allowed_files and path not in allowed_files]
    forbidden_violations = [path for path in changed_paths if _matches_any_path(path, forbidden_paths)]
    return ScopeReport(
        task_source=task_source,
        approved_task=_first_nonempty_line(task_text),
        allowed_files=allowed_files,
        forbidden_paths=forbidden_paths,
        changed_files=changed_files,
        scope_violations=scope_violations,
        forbidden_violations=forbidden_violations,
    )


def _extract_allowed_files(task_text: str) -> list[str]:
    lines = task_text.splitlines()
    allowed: list[str] = []
    in_allowed_block = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^(allowed files|allowed paths|only modify)\s*:", stripped, re.IGNORECASE):
            in_allowed_block = True
            allowed.extend(_paths_from_text(stripped))
            continue
        if in_allowed_block:
            if not stripped:
                in_allowed_block = False
                continue
            bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
            if bullet_match:
                allowed.extend(_paths_from_text(bullet_match.group(1)))
            elif re.match(r"^[A-Za-z].+:", stripped):
                in_allowed_block = False
    inline_match = re.search(r"\bonly\s+(?:modify|change|edit)\s+(.+?)(?:\.|\n|$)", task_text, re.IGNORECASE | re.DOTALL)
    if inline_match:
        allowed.extend(_paths_from_text(inline_match.group(1)))
    return _dedupe(allowed)


def _extract_forbidden_paths(task_text: str) -> list[str]:
    forbidden: list[str] = []
    for line in task_text.splitlines():
        stripped = line.strip()
        if re.search(r"\bdo not\s+(?:modify|edit|touch|change)\b|\bforbidden paths?\s*:", stripped, re.IGNORECASE):
            forbidden.extend(_paths_from_text(stripped))
    return _dedupe(forbidden)


def _paths_from_text(text: str) -> list[str]:
    paths = re.findall(r"`([^`]+\.[A-Za-z0-9]+)`", text)
    paths.extend(re.findall(r"\b(?:src|tests|app|docs|README)[A-Za-z0-9_./-]*\.[A-Za-z0-9]+\b", text))
    return [path.strip() for path in paths if path.strip()]


def _matches_any_path(path: str, candidates: list[str]) -> bool:
    return any(path == candidate or path.startswith(candidate.rstrip("/") + "/") for candidate in candidates)


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" #")
        if stripped and stripped.lower() not in {"task", "mandate", "request"}:
            return stripped
    return ""


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _dedupe_changes(changes: list[FileChange]) -> list[FileChange]:
    result: list[FileChange] = []
    seen: set[str] = set()
    for change in changes:
        if change.path in seen:
            continue
        seen.add(change.path)
        result.append(change)
    return result
