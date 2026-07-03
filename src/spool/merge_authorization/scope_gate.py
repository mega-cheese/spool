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
class ScopeClassification:
    path: str
    classification: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "classification": self.classification,
            "reason": self.reason,
        }


@dataclass
class ScopeReport:
    task_source: str
    approved_task: str
    assignment_status: str = "available"
    allowed_files: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    changed_files: list[FileChange] = field(default_factory=list)
    scope_classifications: list[ScopeClassification] = field(default_factory=list)
    supporting_changes: list[str] = field(default_factory=list)
    risky_expansions: list[str] = field(default_factory=list)
    scope_violations: list[str] = field(default_factory=list)
    forbidden_violations: list[str] = field(default_factory=list)
    scope_risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_source": self.task_source,
            "approved_task": self.approved_task,
            "assignment_status": self.assignment_status,
            "allowed_files": list(self.allowed_files),
            "forbidden_paths": list(self.forbidden_paths),
            "changed_files": [change.to_dict() for change in self.changed_files],
            "scope_classifications": [item.to_dict() for item in self.scope_classifications],
            "supporting_changes": list(self.supporting_changes),
            "risky_expansions": list(self.risky_expansions),
            "scope_violations": list(self.scope_violations),
            "forbidden_violations": list(self.forbidden_violations),
            "scope_risks": list(self.scope_risks),
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


def evaluate_scope(
    task_text: str,
    changed_files: list[FileChange],
    *,
    task_source: str = "task",
    assignment_status: str = "available",
) -> ScopeReport:
    allowed_files = _extract_allowed_files(task_text)
    forbidden_paths = _extract_forbidden_paths(task_text)
    changed_paths = [change.path for change in changed_files]
    scope_classifications = [_classify_path(path, allowed_files) for path in changed_paths]
    supporting_changes = [
        item.path for item in scope_classifications if item.classification == "supporting_change"
    ]
    risky_expansions = [
        item.path for item in scope_classifications if item.classification == "risky_expansion"
    ]
    scope_violations = [
        item.path
        for item in scope_classifications
        if allowed_files and item.classification == "outside_scope"
    ]
    forbidden_violations = [path for path in changed_paths if _matches_any_path(path, forbidden_paths)]
    scope_risks = []
    if assignment_status == "not_provided":
        scope_risks.append("Assignment evidence was not provided; scope authorization review is partial.")
    elif assignment_status == "empty":
        scope_risks.append("Assignment evidence was empty; scope authorization review is partial.")
    elif assignment_status == "missing":
        scope_risks.append("Assignment evidence was expected but missing; scope authorization review is partial.")
    if not allowed_files and _has_explicit_scope_language(task_text):
        scope_risks.append("Could not determine approved file scope from task.")
    if risky_expansions:
        scope_risks.append(
            f"Risky scope expansions need review: {', '.join(risky_expansions)}."
        )
    return ScopeReport(
        task_source=task_source,
        approved_task=_first_nonempty_line(task_text),
        assignment_status=assignment_status,
        allowed_files=allowed_files,
        forbidden_paths=forbidden_paths,
        changed_files=changed_files,
        scope_classifications=scope_classifications,
        supporting_changes=supporting_changes,
        risky_expansions=risky_expansions,
        scope_violations=scope_violations,
        forbidden_violations=forbidden_violations,
        scope_risks=scope_risks,
    )


def _extract_allowed_files(task_text: str) -> list[str]:
    lines = task_text.splitlines()
    allowed: list[str] = []
    in_allowed_block = False
    for line in lines:
        stripped = line.strip()
        if _is_allowed_scope_heading(stripped):
            in_allowed_block = True
            allowed.extend(_paths_from_text(stripped.split(":", 1)[1]))
            continue
        if in_allowed_block:
            if not stripped:
                in_allowed_block = False
                continue
            if re.match(r"^[A-Za-z][A-Za-z /_-]+:", stripped):
                in_allowed_block = False
                continue
            allowed.extend(_paths_from_text(_strip_list_marker(stripped)))
            if not _paths_from_text(stripped) and not re.match(r"^[-*]|\d+\.", stripped):
                in_allowed_block = False
    inline_match = re.search(r"\bonly\s+(?:modify|change|edit)\s+(.+?)(?:\.|\n|$)", task_text, re.IGNORECASE | re.DOTALL)
    if inline_match:
        allowed.extend(_paths_from_text(inline_match.group(1)))
    return _dedupe(allowed)


def _is_allowed_scope_heading(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:allowed\s+(?:file|files|path|paths|director(?:y|ies)|area|areas|scope|files\s*/\s*areas|files\s+and\s+areas)|only\s+modify)\s*:",
            text,
            re.IGNORECASE,
        )
    )


def _strip_list_marker(text: str) -> str:
    return re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", text).strip()


def _extract_forbidden_paths(task_text: str) -> list[str]:
    forbidden: list[str] = []
    for line in task_text.splitlines():
        stripped = line.strip()
        if re.search(r"\bdo not\s+(?:modify|edit|touch|change)\b|\bforbidden paths?\s*:", stripped, re.IGNORECASE):
            forbidden.extend(_paths_from_text(stripped))
    return _dedupe(forbidden)


def _paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for candidate in re.findall(r"`([^`]+)`", text):
        paths.extend(_path_candidates(candidate))
    unquoted = re.sub(r"`[^`]+`", " ", text)
    paths.extend(_path_candidates(unquoted))
    return _dedupe(paths)


def _path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    path_pattern = re.compile(
        r"(?<![\w./-])(?:\.?[A-Za-z0-9_-]+/)+(?:[A-Za-z0-9_.-]+/?)?"
        r"|(?<![\w./-])(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9][A-Za-z0-9_.-]*(?![\w/-])"
    )
    for match in path_pattern.finditer(text):
        candidate = match.group(0).strip("`'\" ,;:)")
        if candidate and not candidate.startswith(("-", "*")):
            candidates.append(candidate)
    return candidates


def _matches_any_path(path: str, candidates: list[str]) -> bool:
    return any(path == candidate or path.startswith(candidate.rstrip("/") + "/") for candidate in candidates)


def _classify_path(path: str, allowed_files: list[str]) -> ScopeClassification:
    if _matches_any_path(path, allowed_files):
        return ScopeClassification(
            path=path,
            classification="explicitly_allowed",
            reason="The changed file matches an explicit allowed file or directory.",
        )
    if _is_supporting_change(path):
        return ScopeClassification(
            path=path,
            classification="supporting_change",
            reason="The changed file is a deterministic low-risk supporting docs or tests path.",
        )
    if _is_risky_expansion(path):
        return ScopeClassification(
            path=path,
            classification="risky_expansion",
            reason="The changed file affects scripts, CI workflow, or dependency/project configuration and needs explicit review.",
        )
    return ScopeClassification(
        path=path,
        classification="outside_scope",
        reason="The changed file does not match explicit allowed scope or a recognized supporting/risky category.",
    )


def _is_supporting_change(path: str) -> bool:
    return path == "README.md" or path.startswith("docs/") or path.startswith("tests/")


def _is_risky_expansion(path: str) -> bool:
    return (
        path.startswith("scripts/")
        or path.startswith(".github/workflows/")
        or path in {"requirements.txt", "pyproject.toml", "package.json"}
    )


def _has_explicit_scope_language(task_text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:only|just|do not|don't|must not|scope|limited to|stay within|allowed files|allowed paths)\b",
            task_text,
            re.IGNORECASE,
        )
    )


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
