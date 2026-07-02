from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_DECISION_MEMORY_PATH = Path(".spool/approved_decisions.yml")
DEFAULT_LEGACY_MEMORY_PATH = Path(".spool/approved_repo_memory.json")


@dataclass
class DecisionEvidence:
    source_type: str = ""
    quote: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"source_type": self.source_type, "quote": self.quote}


@dataclass
class DecisionScope:
    applies_to_paths: list[str] = field(default_factory=list)
    applies_to_concepts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "applies_to_paths": list(self.applies_to_paths),
            "applies_to_concepts": list(self.applies_to_concepts),
        }


@dataclass
class ApprovedDecision:
    id: str
    status: str
    title: str
    type: str = "decision"
    scope: DecisionScope = field(default_factory=DecisionScope)
    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    requires_approval_for: list[str] = field(default_factory=list)
    approved_by: list[str] = field(default_factory=list)
    evidence: list[DecisionEvidence] = field(default_factory=list)
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def summary_text(self) -> str:
        parts = [self.title, *self.must, *self.must_not, *self.requires_approval_for]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "type": self.type,
            "scope": self.scope.to_dict(),
            "must": list(self.must),
            "must_not": list(self.must_not),
            "requires_approval_for": list(self.requires_approval_for),
            "approved_by": list(self.approved_by),
            "evidence": [item.to_dict() for item in self.evidence],
            "created_at": self.created_at,
        }


@dataclass
class DecisionMemory:
    source: str | None
    status: str
    decisions: list[ApprovedDecision] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_report_dict(self, *, matched_decisions: list[str] | None = None, conflicts: list[str] | None = None) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "approved_decisions_count": len(self.decisions),
            "matched_decisions": list(matched_decisions or []),
            "conflicts": list(conflicts or []),
            "notes": list(self.notes),
        }


def resolve_decision_memory_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    if DEFAULT_DECISION_MEMORY_PATH.exists():
        return DEFAULT_DECISION_MEMORY_PATH
    return DEFAULT_LEGACY_MEMORY_PATH


def load_approved_decisions(path: str | Path) -> DecisionMemory:
    memory_path = Path(path)
    if not memory_path.exists():
        return DecisionMemory(
            source=None,
            status="missing",
            notes=["No approved decision memory was provided."],
        )

    try:
        if memory_path.suffix.lower() == ".json":
            decisions = _load_legacy_json(memory_path)
        else:
            decisions = _load_yaml_decisions(memory_path)
    except ValueError as exc:
        return DecisionMemory(source=str(memory_path), status="malformed", notes=[str(exc)])
    except Exception as exc:
        return DecisionMemory(source=str(memory_path), status="malformed", notes=[f"Could not parse approved decision memory: {exc}"])

    return DecisionMemory(source=str(memory_path), status="available", decisions=decisions)


def _load_legacy_json(path: Path) -> list[ApprovedDecision]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_items = list(payload.get("approved_memory") or payload.get("memories") or [])
    elif isinstance(payload, list):
        raw_items = list(payload)
    else:
        raise ValueError("Legacy approved repo memory must be a list or object.")

    decisions: list[ApprovedDecision] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("decision") or "").strip()
        summary = str(item.get("summary") or title).strip()
        if not title and not summary:
            continue
        decisions.append(
            ApprovedDecision(
                id=str(item.get("memory_id") or item.get("id") or f"repo_memory_{index}"),
                status=str(item.get("status") or "approved"),
                title=title or summary,
                type=str(item.get("type") or "decision"),
                must=[summary] if summary else [],
                evidence=[DecisionEvidence(source_type="legacy_repo_memory", quote=title or summary)],
                raw=dict(item),
            )
        )
    return decisions


def _load_yaml_decisions(path: Path) -> list[ApprovedDecision]:
    parsed = _parse_supported_yaml(path.read_text(encoding="utf-8"))
    raw_decisions = parsed.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("Approved decision memory must contain a decisions list.")

    decisions: list[ApprovedDecision] = []
    errors: list[str] = []
    for index, item in enumerate(raw_decisions, start=1):
        if not isinstance(item, dict):
            errors.append(f"Decision #{index} is not an object.")
            continue
        decision_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not decision_id or not title:
            errors.append(f"Decision #{index} must include id and title.")
            continue
        scope_payload = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        evidence_payload = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        decisions.append(
            ApprovedDecision(
                id=decision_id,
                status=str(item.get("status") or "approved"),
                title=title,
                type=str(item.get("type") or "decision"),
                scope=DecisionScope(
                    applies_to_paths=_as_string_list(scope_payload.get("applies_to_paths")),
                    applies_to_concepts=_as_string_list(scope_payload.get("applies_to_concepts")),
                ),
                must=_as_string_list(item.get("must")),
                must_not=_as_string_list(item.get("must_not")),
                requires_approval_for=_as_string_list(item.get("requires_approval_for")),
                approved_by=_as_string_list(item.get("approved_by")),
                evidence=[
                    DecisionEvidence(
                        source_type=str(ev.get("source_type") or ""),
                        quote=str(ev.get("quote") or ""),
                    )
                    for ev in evidence_payload
                    if isinstance(ev, dict)
                ],
                created_at=str(item.get("created_at")) if item.get("created_at") is not None else None,
                raw=dict(item),
            )
        )

    if errors:
        raise ValueError(" ".join(errors))
    return decisions


def _parse_supported_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    active_list: tuple[dict[str, Any], str] | None = None
    active_evidence: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0 and stripped == "decisions: []":
            result["decisions"] = decisions
            current = None
            active_list = None
            active_evidence = None
            continue
        if indent == 0 and stripped == "decisions:":
            result["decisions"] = decisions
            current = None
            active_list = None
            active_evidence = None
            continue
        if stripped.startswith("- ") and indent == 2:
            current = {}
            decisions.append(current)
            active_list = None
            active_evidence = None
            key, value = _split_key_value(stripped[2:])
            if key:
                current[key] = value
            continue
        if current is None:
            continue
        if indent == 4:
            key, value = _split_key_value(stripped)
            active_evidence = None
            if value is None:
                if key == "scope":
                    current[key] = {}
                    active_list = None
                else:
                    current[key] = []
                    active_list = (current, key)
            else:
                current[key] = value
                active_list = None
            continue
        if indent == 6 and stripped.startswith("- ") and active_list:
            container, key = active_list
            if key == "evidence":
                ev_key, ev_value = _split_key_value(stripped[2:])
                active_evidence = {}
                container[key].append(active_evidence)
                if ev_key:
                    active_evidence[ev_key] = ev_value
            else:
                container[key].append(stripped[2:].strip())
            continue
        if indent == 6 and isinstance(current.get("scope"), dict):
            key, value = _split_key_value(stripped)
            if value is None:
                current["scope"][key] = []
                active_list = (current["scope"], key)
            else:
                current["scope"][key] = value
            continue
        if indent == 8 and stripped.startswith("- ") and active_list:
            container, key = active_list
            container[key].append(stripped[2:].strip())
            continue
        if indent == 8 and active_evidence is not None:
            key, value = _split_key_value(stripped)
            if key:
                active_evidence[key] = value

    return result


def _split_key_value(text: str) -> tuple[str, str | None]:
    if ":" not in text:
        return text.strip(), None
    key, value = text.split(":", 1)
    clean_value = value.strip()
    return key.strip(), clean_value if clean_value else None


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    clean = str(value).strip()
    if clean == "[]":
        return []
    return [clean] if clean else []
