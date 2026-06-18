from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io import LOCK_REL, RepoctlError, atomic_write
from .git import ChangedEntry, RepoGitState, repo_change_fingerprints, repo_changed_entries, repo_diff_evidence, repo_git_head, repo_git_status
from .markdown import append_section_entry, find_section, parse_frontmatter, replace_frontmatter_line, replace_section
from .settings import document_language, validate_document_language

LIVE = {"todo", "doing", "blocked"}
NON_LIVE = {"done", "canceled"}
STATUSES = LIVE | NON_LIVE
AREAS = {"", "repo", "backend", "frontend", "infra", "docs", "ops", "mobile"}
REPO_REQUIRED_AREAS = {"repo", "backend", "frontend", "infra", "mobile"}
TASK_RE = re.compile(r"^(T-[0-9]{14}Z)--[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ID_RE = re.compile(r"^T-[0-9]{14}Z$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED = {"id", "title", "status", "owner", "created", "parent", "depends_on"}

TASK_DOC_COPY: dict[str, dict[str, Any]] = {
    "en": {
        "area_unspecified": "not specified",
        "task_created": "task created via repoctl task create.",
        "task_started": "task started.",
        "task_started_dirty": "task started with dirty repo state recorded.",
        "task_started_git_unavailable": "task started; repo dirty check unavailable ({reason}).",
        "task_finished": "task finished and verified.",
        "task_canceled": "task canceled with verification evidence.",
        "task_blocked": "task blocked with evidence.",
        "repo_head_at_start": "repo head at start",
        "verification_empty": "- Verification file was empty.",
        "gate_summary_title": "Repoctl gate summary:",
        "repo_git_present": "- repo git: present",
        "repo_git_unavailable": "- repo git: unavailable ({reason})",
        "meta_gate_passed": "- meta gate: passed ({changed_files} changed files checked)",
        "meta_gate_skipped": "- meta gate: skipped ({reason})",
        "meta_gate_status": "- meta gate: {status}",
        "repo_change_evidence": "Repo change evidence:",
        "done_handoff_next": "No further action; task is complete.",
        "canceled_handoff_next": "No further action; task is canceled.",
        "blocked_handoff_next": "Resolve the recorded blocker or update the task with new evidence.",
        "done_handoff_done": "Task remains archived or non-live according to repoctl check.",
        "canceled_handoff_done": "Task remains archived or non-live as canceled according to repoctl check.",
        "blocked_handoff_done": "The blocker is resolved or the task remains explicitly blocked with current evidence.",
        "work_area_primary": "Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.",
        "parent_goal": "Coordinate `{title}` by splitting the work into child tasks, keeping shared decisions current, and closing integration with verification evidence.",
        "parent_plan": [
            "Inspect the likely repo/docs surfaces and decide whether child tasks are truly needed.",
            "Create child tasks with explicit parent frontmatter for each independently verifiable surface.",
            "Keep shared decisions and child status summaries current while treating child frontmatter as authoritative.",
            "Finish only after all children are done/canceled and integration verification is recorded.",
        ],
        "parent_handoff_next": "Inspect the work area and define the first child task for `{title}` if coordination is still warranted.",
        "parent_handoff_done": "Child tasks, shared decisions, and integration criteria are current enough for another agent to continue.",
        "live_child_summary": "<!-- Child tasks are discovered from child frontmatter `parent`, not this list. -->",
        "non_live_child_summary": "<!-- Non-live child summaries may be added here after children exist. -->",
        "shared_decisions": "Record cross-child decisions here as they are made.",
        "integration_done": "All child tasks are done or canceled and integration verification is recorded.",
        "task_goal": "Deliver `{title}` as the smallest verified change, with exact touched files, validation evidence, and restartable handoff recorded before finish.",
        "task_plan": [
            "Inspect the task record, Board, and relevant repo/docs surfaces before editing.",
            "Identify the exact files to change and keep edits limited to that surface.",
            "Implement the smallest complete change that satisfies the goal.",
            "Run focused validation and `repoctl meta check --changed` when `repo/` files changed.",
            "Write a temporary verification file outside `repo/`, then finish with repoctl.",
        ],
        "task_handoff_next": "Start the task, inspect `{repo_hint}`, and replace this generated scope with the exact files and validation plan.",
        "task_handoff_done": "The task file names the real touched files, the change is verified, and repoctl finish records archive/Board state.",
        "in_scope": [
            "Identify and record the concrete files/docs that define this task.",
            "Make only the narrow changes needed for the stated goal.",
            "Keep `repo/.repometa` annotations valid for any changed `repo/` files required by metadata coverage policy.",
            "Keep Execution Log entries meaningful: creation, start, implementation decision, verification, blocker, or finish.",
            "Use a temporary verification file outside `repo/`; `repoctl task finish` stores the durable evidence in the task.",
        ],
        "root_in_scope": [
            "Identify and record the concrete workspace/docs files that define this task.",
            "Make only the narrow changes needed for the stated goal.",
            "Do not touch product files under `repo/` unless the task is intentionally converted into repo-scoped work.",
            "Keep Execution Log entries meaningful: creation, start, implementation decision, verification, blocker, or finish.",
            "Use a temporary verification file outside `repo/`; `repoctl task finish` stores the durable evidence in the task.",
        ],
        "out_of_scope": [
            "Unrelated refactors or cleanup.",
            "Branch, commit, PR, deploy, or release automation unless explicitly requested.",
        ],
        "verification_pending": "Pending.",
        "start_handoff_next": "Continue implementation for `{task_path}`.",
        "start_handoff_done": "The task names exact touched files, focused validation is recorded, `./scripts/repoctl meta check --changed` is clean for changed `repo/` files, and the task is finished.",
        "context_docs": "<!-- Add only the minimum context docs needed for this task, or leave empty. -->",
        "discovery": [
            "Candidate query: none yet",
            "Candidate files reviewed: none yet",
            "Chosen files: none yet",
        ],
    },
    "ko": {
        "area_unspecified": "지정되지 않음",
        "task_created": "repoctl task create로 작업을 생성함.",
        "task_started": "작업을 시작함.",
        "task_started_dirty": "작업을 시작했고, 더러운 repo 상태를 기록함.",
        "task_started_git_unavailable": "작업을 시작했으나 repo dirty 확인을 사용할 수 없음({reason}).",
        "task_finished": "작업을 검증하고 완료함.",
        "task_canceled": "검증 증거와 함께 작업을 취소함.",
        "task_blocked": "증거와 함께 작업을 blocked로 표시함.",
        "repo_head_at_start": "repo head at start",
        "verification_empty": "- 검증 파일이 비어 있음.",
        "gate_summary_title": "Repoctl 게이트 요약:",
        "repo_git_present": "- repo git: 있음",
        "repo_git_unavailable": "- repo git: 사용할 수 없음({reason})",
        "meta_gate_passed": "- meta gate: 통과({changed_files}개 변경 파일 확인)",
        "meta_gate_skipped": "- meta gate: 건너뜀({reason})",
        "meta_gate_status": "- meta gate: {status}",
        "repo_change_evidence": "Repo 변경 증거:",
        "done_handoff_next": "추가 작업 없음; 작업이 완료됨.",
        "canceled_handoff_next": "추가 작업 없음; 작업이 취소됨.",
        "blocked_handoff_next": "기록된 blocker를 해결하거나 새 증거로 작업을 업데이트한다.",
        "done_handoff_done": "repoctl check 기준으로 작업이 archive 또는 non-live 상태를 유지함.",
        "canceled_handoff_done": "repoctl check 기준으로 작업이 canceled archive 또는 non-live 상태를 유지함.",
        "blocked_handoff_done": "blocker가 해결되었거나 작업이 최신 증거와 함께 명시적으로 blocked 상태임.",
        "work_area_primary": "첫 구현 단계에서 정확한 repo, docs, workspace 파일을 확인한다. 제목만 보고 추측하지 않는다.",
        "parent_goal": "`{title}`를 조율한다. 필요한 child task로 나누고, 공유 결정을 최신으로 유지하며, 통합 검증 증거로 마무리한다.",
        "parent_plan": [
            "관련 repo/docs 표면을 확인하고 child task가 정말 필요한지 판단한다.",
            "독립적으로 검증 가능한 표면마다 명시적 parent frontmatter가 있는 child task를 만든다.",
            "child frontmatter를 권위 source로 두고 공유 결정과 child 상태 요약을 최신으로 유지한다.",
            "모든 child가 done/canceled가 되고 통합 검증이 기록된 뒤에만 완료한다.",
        ],
        "parent_handoff_next": "작업 영역을 확인하고, 조율이 여전히 필요하면 `{title}`의 첫 child task를 정의한다.",
        "parent_handoff_done": "child task, 공유 결정, 통합 기준이 다음 agent가 이어갈 만큼 최신 상태임.",
        "live_child_summary": "<!-- Child task는 이 목록이 아니라 child frontmatter `parent`에서 찾는다. -->",
        "non_live_child_summary": "<!-- Child가 생긴 뒤 non-live child 요약을 여기에 추가할 수 있다. -->",
        "shared_decisions": "작업 중 생긴 cross-child 결정을 여기에 기록한다.",
        "integration_done": "모든 child task가 done 또는 canceled이고 통합 검증이 기록됨.",
        "task_goal": "`{title}`를 가장 작은 검증 가능한 변경으로 완수한다. 정확한 변경 파일, 검증 증거, 재시작 가능한 handoff를 완료 전에 기록한다.",
        "task_plan": [
            "편집 전에 작업 기록, Board, 관련 repo/docs 표면을 확인한다.",
            "변경할 정확한 파일을 식별하고 편집 범위를 그 표면으로 제한한다.",
            "목표를 만족하는 가장 작은 완전한 변경을 구현한다.",
            "집중 검증을 실행하고, `repo/` 파일이 바뀌었으면 `repoctl meta check --changed`를 실행한다.",
            "`repo/` 밖 임시 검증 파일을 작성한 뒤 repoctl로 완료한다.",
        ],
        "task_handoff_next": "작업을 시작하고 `{repo_hint}`를 확인한 뒤, 이 생성된 범위를 정확한 파일과 검증 계획으로 교체한다.",
        "task_handoff_done": "작업 파일에 실제 변경 파일이 기록되고, 변경이 검증되며, repoctl finish가 archive/Board 상태를 기록함.",
        "in_scope": [
            "이 작업을 정의하는 구체적인 파일/docs를 식별하고 기록한다.",
            "명시된 목표에 필요한 좁은 변경만 수행한다.",
            "metadata coverage policy가 요구하는 변경 `repo/` 파일의 `repo/.repometa` annotation을 유효하게 유지한다.",
            "Execution Log에는 생성, 시작, 구현 결정, 검증, blocker, 완료처럼 의미 있는 항목만 남긴다.",
            "`repo/` 밖 임시 검증 파일을 사용한다. `repoctl task finish`가 영구 증거를 작업 파일에 저장한다.",
        ],
        "root_in_scope": [
            "이 작업을 정의하는 구체적인 workspace/docs 파일을 식별하고 기록한다.",
            "명시된 목표에 필요한 좁은 변경만 수행한다.",
            "작업을 의도적으로 repo-scoped로 전환하지 않는 한 `repo/` 제품 파일은 건드리지 않는다.",
            "Execution Log에는 생성, 시작, 구현 결정, 검증, blocker, 완료처럼 의미 있는 항목만 남긴다.",
            "`repo/` 밖 임시 검증 파일을 사용한다. `repoctl task finish`가 영구 증거를 작업 파일에 저장한다.",
        ],
        "out_of_scope": [
            "무관한 refactor 또는 cleanup.",
            "명시적으로 요청되지 않은 branch, commit, PR, deploy, release 자동화.",
        ],
        "verification_pending": "대기 중.",
        "start_handoff_next": "`{task_path}` 구현을 계속한다.",
        "start_handoff_done": "작업에 정확한 변경 파일이 기록되고, 집중 검증이 남아 있으며, 변경된 `repo/` 파일에 대해 `./scripts/repoctl meta check --changed`가 깨끗하고, 작업이 완료됨.",
        "context_docs": "<!-- 이 작업에 필요한 최소 context docs만 추가한다. 없으면 비워 둔다. -->",
        "discovery": [
            "Candidate query: none yet",
            "Candidate files reviewed: none yet",
            "Chosen files: none yet",
        ],
    },
}


def _copy(language: str) -> dict[str, Any]:
    return TASK_DOC_COPY.get(language, TASK_DOC_COPY["en"])


def _task_language(root: Path, task: Task) -> str:
    value = task.frontmatter.get("document_language")
    if isinstance(value, str) and value.strip():
        language = value.strip().lower()
        validate_document_language(language, source=f"{task.rel_path} document_language")
        return language
    return document_language(root)


def _bullet_lines(items: list[str]) -> str:
    return "".join(f"- {item}\n" for item in items)


@dataclass(frozen=True)
class Problem:
    severity: str
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path is not None:
            data["path"] = self.path
        return data


@dataclass(frozen=True)
class Task:
    path: Path
    rel_path: str
    frontmatter: dict[str, Any]
    body: str
    archived: bool = False

    @property
    def id(self) -> str:
        return str(self.frontmatter.get("id") or "")

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status") or "")

    @property
    def parent(self) -> str:
        return str(self.frontmatter.get("parent") or "")

    def to_list_dict(self) -> dict[str, Any]:
        depends_on = self.frontmatter.get("depends_on")
        if not isinstance(depends_on, list):
            depends_on = []
        return {
            "id": self.id,
            "path": self.rel_path,
            "status": self.status,
            "owner": str(self.frontmatter.get("owner") or "unassigned"),
            "repo_ref": str(self.frontmatter.get("repo_ref") or ""),
            "parent": self.parent,
            "depends_on": depends_on,
        }


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def load_task(path: Path, root: Path, *, archived: bool = False) -> Task:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    return Task(path=path, rel_path=_rel(root, path), frontmatter=frontmatter, body=body, archived=archived)


def load_tasks(root: Path) -> list[Task]:
    tasks: list[Task] = []
    for path in sorted((root / "docs/tasks").glob("T-*.md")):
        tasks.append(load_task(path, root, archived=False))
    archive_dir = root / "docs/archive/tasks"
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("T-*.md")):
            tasks.append(load_task(path, root, archived=True))
    return tasks


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def resolve_live_task(root: Path, task_id: str) -> Task:
    if not ID_RE.match(task_id):
        raise RepoctlError("invalid task id format; expected T-YYYYMMDDHHMMSSZ")
    matches = sorted((root / "docs/tasks").glob(f"{task_id}--*.md"))
    if not matches:
        raise RepoctlError(f"task not found: {task_id}")
    if len(matches) > 1:
        raise RepoctlError(f"ambiguous task id: {task_id}")
    return load_task(matches[0], root)


def resolve_task(root: Path, task_id: str) -> Task:
    if not ID_RE.match(task_id):
        raise RepoctlError("invalid task id format; expected T-YYYYMMDDHHMMSSZ")
    matches = sorted((root / "docs/tasks").glob(f"{task_id}--*.md")) + sorted((root / "docs/archive/tasks").glob(f"{task_id}--*.md"))
    if not matches:
        raise RepoctlError(f"task not found: {task_id}")
    if len(matches) > 1:
        raise RepoctlError(f"ambiguous task id: {task_id}")
    return load_task(matches[0], root)


def append_task_log(root: Path, task_id: str, message: str) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    if not message.strip():
        raise RepoctlError("task log message is required")
    text = task.path.read_text(encoding="utf-8")
    timestamp = utc_stamp()
    text = append_section_entry(text, "Execution Log", f"- {timestamp}: {message.strip()}")
    return {"task": task, "text": text, "timestamp": timestamp}


def _strip_ticks(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "`":
        return stripped[1:-1].strip()
    return stripped


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _strip_ticks(value).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _read_discovery_values(task: Task) -> dict[str, list[str]]:
    try:
        section = find_section(task.body, "Discovery")
    except RepoctlError:
        return {}
    body = task.body[section.body_start : section.end]
    fields: dict[str, list[str]] = {}
    current_key = ""
    for line in body.splitlines():
        match = re.match(r"^\s*-\s+(Candidate query|Candidate files reviewed|Chosen files|Notes):\s*(.*)$", line)
        if match:
            current_key = match.group(1)
            value = match.group(2).strip()
            fields.setdefault(current_key, [])
            if value:
                fields[current_key].append(value)
            continue
        if current_key and re.match(r"^\s{2,}-\s+", line):
            fields.setdefault(current_key, []).append(re.sub(r"^\s*-\s*", "", line).strip())
    return {key: _dedupe_preserve(values) for key, values in fields.items()}


def _format_discovery_scalar(value: str) -> str:
    return f"`{_strip_ticks(value)}`"


def _format_discovery_list(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"- {key}: none yet"]
    if len(values) == 1:
        return [f"- {key}: {_format_discovery_scalar(values[0])}"]
    lines = [f"- {key}:"]
    lines.extend(f"  - {_format_discovery_scalar(value)}" for value in values)
    return lines


def update_task_discovery(
    root: Path,
    task_id: str,
    *,
    query: str = "",
    reviewed: list[str] | None = None,
    chosen: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    reviewed = reviewed or []
    chosen = chosen or []
    if not any([query.strip(), reviewed, chosen, note.strip()]):
        raise RepoctlError("task discovery add requires --query, --reviewed, --chosen, or --note", code="missing_discovery_input", path=task.rel_path)

    fields = _read_discovery_values(task)
    placeholders = {"none", "none yet", "n/a", "na", "tbd", "todo", "pending", "-"}

    def without_placeholders(values: list[str]) -> list[str]:
        return [value for value in values if _strip_ticks(value).lower() not in placeholders]

    query_values = without_placeholders(fields.get("Candidate query", []))
    if query.strip():
        query_values = [_strip_ticks(query)]
    reviewed_values = _dedupe_preserve([*without_placeholders(fields.get("Candidate files reviewed", [])), *reviewed])
    chosen_values = _dedupe_preserve([*without_placeholders(fields.get("Chosen files", [])), *chosen])
    note_values = _dedupe_preserve([*without_placeholders(fields.get("Notes", [])), *([note] if note.strip() else [])])

    lines: list[str] = []
    lines.extend(_format_discovery_list("Candidate query", query_values))
    lines.extend(_format_discovery_list("Candidate files reviewed", reviewed_values))
    lines.extend(_format_discovery_list("Chosen files", chosen_values))
    if note_values:
        lines.extend(_format_discovery_list("Notes", note_values))
    current_text = task.path.read_text(encoding="utf-8")
    discovery_body = "\n".join(lines) + "\n"
    try:
        text = replace_section(current_text, "Discovery", discovery_body)
    except RepoctlError:
        if "## Execution Log" not in current_text:
            raise
        text = current_text.replace("## Execution Log", f"## Discovery\n\n{discovery_body}\n## Execution Log", 1)
    return {
        "task": task,
        "text": text,
        "discovery": {
            "candidate_query": query_values[0] if query_values else "",
            "candidate_files_reviewed": reviewed_values,
            "chosen_files": chosen_values,
            "notes": note_values,
        },
    }


def _dirty_entry(dirty: list[str], *, copy: dict[str, Any]) -> str:
    shown = dirty[:20]
    suffix = "\n  - ... truncated" if len(dirty) > 20 else ""
    lines = "\n".join(f"  - {line}" for line in shown)
    return f"- {utc_stamp()}: {copy['task_started_dirty']}\n{lines}{suffix}"


def _git_unavailable_entry(reason: str, *, copy: dict[str, Any]) -> str:
    return f"- {utc_stamp()}: {copy['task_started_git_unavailable'].format(reason=reason)}"


def _repo_head_entry(head: str, *, copy: dict[str, Any]) -> str:
    return f"- {copy['repo_head_at_start']}: `{head}`"


def _state_dir(root: Path) -> Path:
    return root / "docs/tasks/.repoctl-state"


def _baseline_path(root: Path, task_id: str) -> Path:
    return _state_dir(root) / f"{task_id}.json"


def _entry_to_dict(entry: ChangedEntry) -> dict[str, str]:
    change, path, old_path = entry
    data = {"change": change, "path": path}
    if old_path:
        data["old_path"] = old_path
    return data


def _entry_key(entry: ChangedEntry) -> tuple[str, str, str]:
    return entry


def _entry_fingerprint_key(entry: ChangedEntry) -> str:
    change, path, old_path = entry
    return "\0".join([change, path, old_path])


def _write_repo_baseline(root: Path, task: "Task", entries: list[ChangedEntry], git_state: RepoGitState) -> None:
    if not git_state.available:
        return
    fingerprints, _fingerprint_state = repo_change_fingerprints(root, entries)
    _state_dir(root).mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task.id,
        "created": utc_stamp(),
        "repo_changes": [_entry_to_dict(entry) for entry in entries],
        "repo_change_fingerprints": fingerprints,
    }
    atomic_write(_baseline_path(root, task.id), json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_repo_baseline(root: Path, task_id: str) -> dict[str, Any] | None:
    path = _baseline_path(root, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoctlError(f"task repo dirty baseline is unreadable: {path.relative_to(root).as_posix()}") from exc
    raw_entries = data.get("repo_changes", []) if isinstance(data, dict) else []
    if not isinstance(raw_entries, list):
        raise RepoctlError(f"task repo dirty baseline is invalid: {path.relative_to(root).as_posix()}")
    entries: list[ChangedEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise RepoctlError(f"task repo dirty baseline is invalid: {path.relative_to(root).as_posix()}")
        change = str(item.get("change") or "")
        path_value = str(item.get("path") or "")
        old_path = str(item.get("old_path") or "")
        if not change or not path_value:
            raise RepoctlError(f"task repo dirty baseline is invalid: {path.relative_to(root).as_posix()}")
        entries.append((change, path_value, old_path))
    raw_fingerprints = data.get("repo_change_fingerprints", {}) if isinstance(data, dict) else {}
    if raw_fingerprints and not isinstance(raw_fingerprints, dict):
        raise RepoctlError(f"task repo dirty baseline is invalid: {path.relative_to(root).as_posix()}")
    fingerprints = {str(key): str(value) for key, value in raw_fingerprints.items()} if isinstance(raw_fingerprints, dict) else {}
    return {"entries": entries, "fingerprints": fingerprints}


def repo_changes_since_task_start(root: Path, task_id: str) -> dict[str, Any]:
    current, git_state = repo_changed_entries(root)
    baseline = _read_repo_baseline(root, task_id) if git_state.available else None
    if baseline is None:
        return {"changes": current, "baseline_available": False, "baseline_count": 0, "current_count": len(current), "preexisting_count": 0, "baseline_conflicts": [], "repo_git": git_state}
    baseline_entries = baseline["entries"]
    baseline_fingerprints = baseline["fingerprints"]
    current_fingerprints, _fingerprint_state = repo_change_fingerprints(root, current)
    baseline_keys = {_entry_key(entry) for entry in baseline_entries}
    changes: list[ChangedEntry] = []
    baseline_conflicts: list[str] = []
    for entry in current:
        if _entry_key(entry) not in baseline_keys:
            changes.append(entry)
            continue
        key = _entry_fingerprint_key(entry)
        baseline_fingerprint = baseline_fingerprints.get(key)
        current_fingerprint = current_fingerprints.get(key)
        if not baseline_fingerprint:
            baseline_conflicts.append(entry[1])
            changes.append(entry)
        elif current_fingerprint != baseline_fingerprint:
            baseline_conflicts.append(entry[1])
            changes.append(entry)
    return {
        "changes": changes,
        "baseline_available": True,
        "baseline_count": len(baseline_entries),
        "current_count": len(current),
        "preexisting_count": max(0, len(current) - len(changes)),
        "baseline_conflicts": sorted(set(baseline_conflicts)),
        "repo_git": git_state,
    }


def start_task(root: Path, task_id: str, *, force_dirty: bool = False) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in {"todo", "blocked"} and not (task.status == "doing" and force_dirty):
        raise RepoctlError("task start requires status todo or blocked; use --force-dirty to refresh a doing task's repo evidence")
    dirty, git_state = repo_git_status(root)
    baseline_entries, _baseline_git_state = repo_changed_entries(root)
    if _repo_scoped_task(task) and not git_state.available:
        raise RepoctlError(f"repo-scoped task cannot start because {git_state.reason}; initialize repo/ as an independent git repository first")
    repo_scoped = _repo_scoped_task(task)
    if dirty and repo_scoped and not force_dirty:
        raise RepoctlError("repo/ is dirty; use --force-dirty to record dirty files and continue")

    text = task.path.read_text(encoding="utf-8")
    text = replace_frontmatter_line(text, "status", "doing")
    head, _head_state = repo_git_head(root)
    if dirty:
        entry = _dirty_entry(dirty, copy=copy)
    elif not git_state.available:
        entry = _git_unavailable_entry(git_state.reason, copy=copy)
    else:
        entry = f"- {utc_stamp()}: {copy['task_started']}"
    if git_state.available:
        entry = f"{entry}\n{_repo_head_entry(head, copy=copy)}"
        _write_repo_baseline(root, task, baseline_entries, git_state)
    text = append_section_entry(text, "Execution Log", entry)
    handoff = (
        f"- Next exact step: {copy['start_handoff_next'].format(task_path=task.rel_path)}\n"
        f"- First file to open: `{task.rel_path}`\n"
        "- First command to run: `./scripts/repoctl task list --json`\n"
        f"- Done when: {copy['start_handoff_done']}\n"
    )
    text = replace_section(text, "Handoff", handoff)
    warnings: list[Problem] = []
    if dirty and not repo_scoped and not force_dirty:
        warnings.append(Problem("warning", "root_task_repo_dirty_recorded", "root/workspace task started with existing repo/ dirty state recorded for baseline only", task.rel_path))
    return {"task": task, "text": text, "dirty": dirty, "repo_git": git_state, "warnings": warnings}


def _verification_gate_summary(meta_gate: dict[str, Any] | None, git_state: RepoGitState, *, copy: dict[str, Any]) -> str:
    lines = [copy["gate_summary_title"]]
    if git_state.available:
        lines.append(copy["repo_git_present"])
    else:
        lines.append(copy["repo_git_unavailable"].format(reason=git_state.reason))
    if meta_gate:
        status = str(meta_gate.get("status") or "unknown")
        if status == "passed":
            lines.append(copy["meta_gate_passed"].format(changed_files=meta_gate.get("changed_files", 0)))
        elif status == "skipped":
            lines.append(copy["meta_gate_skipped"].format(reason=meta_gate.get("reason", "unknown")))
        else:
            lines.append(copy["meta_gate_status"].format(status=status))
        summary = meta_gate.get("summary")
        if isinstance(summary, dict) and summary:
            lines.append(
                "- meta status: "
                f"total={summary.get('total', 0)} "
                f"required={summary.get('annotation_required', 0)} "
                f"annotated={summary.get('annotated', 0)} "
                f"excluded={summary.get('excluded', 0)} "
                f"indexed_only={summary.get('indexed_only', 0)}"
            )
        if "task_new_changes" in meta_gate:
            lines.append(
                "- repo changes: "
                f"task_new_changes={meta_gate.get('task_new_changes', 0)} "
                f"preexisting_dirty_files={meta_gate.get('preexisting_dirty_files', 0)}"
            )
    return "\n".join(lines) + "\n"


def _verification_body(verification: str, diff_evidence: str, *, meta_gate: dict[str, Any] | None, git_state: RepoGitState, copy: dict[str, Any]) -> tuple[str, bool]:
    truncated = False
    verification = verification.strip()
    if len(verification) > 4000:
        verification = verification[:4000].rstrip() + "\n... truncated"
        truncated = True
    body = verification or copy["verification_empty"]
    body += "\n\n" + _verification_gate_summary(meta_gate, git_state, copy=copy).rstrip("\n")
    if diff_evidence:
        body += f"\n\n{copy['repo_change_evidence']}\n\n```text\n" + diff_evidence + "\n```"
    return body + "\n", truncated


def _done_handoff(new_path: str, *, copy: dict[str, Any]) -> str:
    return (
        f"- Next exact step: {copy['done_handoff_next']}\n"
        f"- First file to open: `{new_path}`\n"
        "- First command to run: `./scripts/repoctl check --json`\n"
        f"- Done when: {copy['done_handoff_done']}\n"
    )


def _canceled_handoff(new_path: str, *, copy: dict[str, Any]) -> str:
    return (
        f"- Next exact step: {copy['canceled_handoff_next']}\n"
        f"- First file to open: `{new_path}`\n"
        "- First command to run: `./scripts/repoctl check --json`\n"
        f"- Done when: {copy['canceled_handoff_done']}\n"
    )


def _blocked_handoff(task_path: str, task_id: str, *, copy: dict[str, Any]) -> str:
    return (
        f"- Next exact step: {copy['blocked_handoff_next']}\n"
        f"- First file to open: `{task_path}`\n"
        f"- First command to run: `./scripts/repoctl task doctor {task_id} --json`\n"
        f"- Done when: {copy['blocked_handoff_done']}\n"
    )


def validate_verification_file(root: Path, verification_file: Path) -> None:
    resolved_verification = verification_file.resolve()
    repo_root = (root / "repo").resolve()
    try:
        resolved_verification.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise RepoctlError("verification file is an input artifact; keep it outside repo/ so finish records durable evidence in the task without creating repo metadata residue", code="verification_file_inside_repo", path=verification_file.as_posix())
    if not verification_file.is_file():
        raise RepoctlError(f"verification file cannot be read: {verification_file}", code="missing_verification_file", path=verification_file.as_posix())


def finish_task(root: Path, task_id: str, *, verification_file: Path, meta_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in LIVE:
        raise RepoctlError("task finish requires a live status")
    area = str(task.frontmatter.get("area") or "")
    repo_changed = bool(meta_gate and meta_gate.get("status") == "passed" and meta_gate.get("scope") == "changed")
    start_head = _repo_head_at_start(task)
    current_head, current_head_state = repo_git_head(root)
    if (repo_changed or area in REPO_REQUIRED_AREAS) and current_head_state.available and meta_gate and meta_gate.get("reason") != "no_repo_directory":
        if not start_head:
            raise RepoctlError("task cannot finish because repo head at start was not recorded; restart the task with repoctl task start")
        if current_head != start_head:
            raise RepoctlError("repo HEAD changed since task start; finish before committing repo/ changes so changed-file gates can validate the actual work", code="repo_head_changed_since_start", path=task.rel_path)
    if repo_changed and area not in REPO_REQUIRED_AREAS:
        raise RepoctlError("task that changes repo/ must set area to one of: repo, backend, frontend, infra, mobile; update task frontmatter to the accurate repo area instead of finishing as docs/ops")
    if repo_changed and not str(task.frontmatter.get("repo_ref") or ""):
        raise RepoctlError("task that changes repo/ must record repo_ref for handoff traceability; set repo_ref to the repo/ branch or worktree name before finishing")
    if repo_changed and not _discovery_recorded(task):
        raise RepoctlError("repo task must record candidate discovery before finish", code="placeholder_discovery", path=task.rel_path)
    if area in REPO_REQUIRED_AREAS and not (root / "repo").exists():
        raise RepoctlError("repo-scoped task cannot finish because repo/ is missing; initialize repo/ as the product repository or use area docs/ops for root-only work")
    finish_timestamp = utc_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=finish_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task finish would create non-monotonic Execution Log timestamps; {timestamp_problem}")
    validate_verification_file(root, verification_file)
    try:
        verification = verification_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoctlError(f"verification file cannot be read: {verification_file}") from exc
    if not verification.strip():
        raise RepoctlError("verification file must contain the commands run and their results")
    all_tasks = load_tasks(root)
    children = children_by_parent(all_tasks)
    live_children = [child for child in children.get(task.id, []) if child.status in LIVE]
    if live_children:
        raise RepoctlError("cannot finish parent task while live children remain")

    text = task.path.read_text(encoding="utf-8")
    diff_evidence, git_state = repo_diff_evidence(root)
    verification_body, truncated = _verification_body(verification, diff_evidence, meta_gate=meta_gate, git_state=git_state, copy=copy)
    text = replace_section(text, "Verification", verification_body)
    text = append_section_entry(text, "Execution Log", f"- {finish_timestamp}: {copy['task_finished']}")
    text = replace_frontmatter_line(text, "status", "done")

    is_child = bool(task.parent)
    is_parent = task.id in children
    old_path = task.rel_path
    archived = False
    new_path = old_path
    moves: list[tuple[Path, Path]] = []
    archive_texts: dict[Path, str] = {}
    if is_parent or not is_child:
        archived = True
        new_path = f"docs/archive/tasks/{task.path.name}"
        moves.append((task.path, root / new_path))
        if is_parent:
            for child in children.get(task.id, []):
                if not child.archived:
                    child_new_path = f"docs/archive/tasks/{child.path.name}"
                    target = root / child_new_path
                    child_text = child.path.read_text(encoding="utf-8")
                    child_text = append_section_entry(child_text, "Execution Log", f"- {utc_stamp()}: task archived with parent `{task.id}`.")
                    archive_texts[target] = replace_section(child_text, "Handoff", _done_handoff(child_new_path, copy=copy))
                    moves.append((child.path, target))
    text = replace_section(text, "Handoff", _done_handoff(new_path, copy=copy))
    if moves:
        archive_texts[root / new_path] = text
    return {
        "task": task,
        "text": text,
        "old_path": old_path,
        "new_path": new_path,
        "archived": archived,
        "moves": moves,
        "archive_texts": archive_texts,
        "truncated": truncated,
    }


def cancel_task(root: Path, task_id: str, *, verification_file: Path, meta_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in LIVE:
        raise RepoctlError("task cancel requires a live status")
    finish_timestamp = utc_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=finish_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task cancel would create non-monotonic Execution Log timestamps; {timestamp_problem}")
    validate_verification_file(root, verification_file)
    try:
        verification = verification_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoctlError(f"verification file cannot be read: {verification_file}") from exc
    if not verification.strip():
        raise RepoctlError("verification file must contain the cancellation reason and any verification evidence")
    all_tasks = load_tasks(root)
    children = children_by_parent(all_tasks)
    live_children = [child for child in children.get(task.id, []) if child.status in LIVE]
    if live_children:
        raise RepoctlError("cannot cancel parent task while live children remain")

    text = task.path.read_text(encoding="utf-8")
    diff_evidence, git_state = repo_diff_evidence(root)
    meta_gate = meta_gate or {"status": "skipped", "reason": "task_canceled"}
    verification_body, truncated = _verification_body(verification, diff_evidence, meta_gate=meta_gate, git_state=git_state, copy=copy)
    text = replace_section(text, "Verification", verification_body)
    text = append_section_entry(text, "Execution Log", f"- {finish_timestamp}: {copy['task_canceled']}")
    text = replace_frontmatter_line(text, "status", "canceled")

    is_child = bool(task.parent)
    is_parent = task.id in children
    old_path = task.rel_path
    archived = False
    new_path = old_path
    moves: list[tuple[Path, Path]] = []
    archive_texts: dict[Path, str] = {}
    if is_parent or not is_child:
        archived = True
        new_path = f"docs/archive/tasks/{task.path.name}"
        moves.append((task.path, root / new_path))
        if is_parent:
            for child in children.get(task.id, []):
                if not child.archived:
                    child_new_path = f"docs/archive/tasks/{child.path.name}"
                    target = root / child_new_path
                    child_text = child.path.read_text(encoding="utf-8")
                    child_text = append_section_entry(child_text, "Execution Log", f"- {utc_stamp()}: task archived with canceled parent `{task.id}`.")
                    archive_texts[target] = replace_section(child_text, "Handoff", _canceled_handoff(child_new_path, copy=copy))
                    moves.append((child.path, target))
    text = replace_section(text, "Handoff", _canceled_handoff(new_path, copy=copy))
    if moves:
        archive_texts[root / new_path] = text
    return {
        "task": task,
        "text": text,
        "old_path": old_path,
        "new_path": new_path,
        "archived": archived,
        "moves": moves,
        "archive_texts": archive_texts,
        "truncated": truncated,
    }


def block_task(root: Path, task_id: str, *, verification_file: Path) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in LIVE:
        raise RepoctlError("task block requires a live status")
    block_timestamp = utc_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=block_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task block would create non-monotonic Execution Log timestamps; {timestamp_problem}")
    validate_verification_file(root, verification_file)
    try:
        verification = verification_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoctlError(f"verification file cannot be read: {verification_file}") from exc
    if not verification.strip():
        raise RepoctlError("verification file must contain the blocker and current evidence")

    text = task.path.read_text(encoding="utf-8")
    diff_evidence, git_state = repo_diff_evidence(root)
    meta_gate = {"status": "skipped", "reason": "task_blocked"}
    verification_body, truncated = _verification_body(verification, diff_evidence, meta_gate=meta_gate, git_state=git_state, copy=copy)
    text = replace_section(text, "Verification", verification_body)
    text = append_section_entry(text, "Execution Log", f"- {block_timestamp}: {copy['task_blocked']}")
    text = replace_frontmatter_line(text, "status", "blocked")
    text = replace_section(text, "Handoff", _blocked_handoff(task.rel_path, task.id, copy=copy))
    return {
        "task": task,
        "text": text,
        "old_path": task.rel_path,
        "new_path": task.rel_path,
        "archived": False,
        "keep_board": True,
        "moves": [],
        "archive_texts": {},
        "truncated": truncated,
    }


def _escape_yaml_double(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validate_title(title: str) -> None:
    if "\n" in title or "\r" in title:
        raise RepoctlError("task title must be a single line")


def _slug_from_title(title: str) -> str:
    try:
        title.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RepoctlError("non-ASCII title requires explicit --slug") from exc
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        raise RepoctlError("title cannot be converted to a slug; pass --slug")
    return slug


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise RepoctlError("invalid slug; use lowercase kebab-case [a-z0-9-]")


def _validate_parent_id(parent: str) -> None:
    if parent and not ID_RE.match(parent):
        raise RepoctlError("invalid parent id format; expected T-YYYYMMDDHHMMSSZ")


def _validate_area(area: str) -> None:
    if area not in AREAS:
        raise RepoctlError("invalid area; use one of: repo, backend, frontend, infra, docs, ops, mobile. Put detailed product surfaces in the title, Work Area, or Discovery instead of --area", code="invalid_area")


def _validate_repo_ref(repo_ref: str) -> None:
    if repo_ref.strip() in {".", "./", "root", "workspace"}:
        raise RepoctlError("repo_ref must identify repo/ branch or worktree; omit --repo-ref for root workspace work", code="invalid_repo_ref")


def validate_repo_ref_area(area: str, repo_ref: str) -> None:
    if repo_ref and area not in REPO_REQUIRED_AREAS:
        raise RepoctlError("--repo-ref marks repo/ work, so --area must be one of repo, backend, frontend, infra, mobile; omit --repo-ref for root docs/ops work", code="repo_ref_non_repo_area")


def is_parent_task(task: Task) -> bool:
    if task.parent:
        return False
    try:
        find_section(task.body, "Live Child Tasks")
        find_section(task.body, "Non-Live Child Tasks")
    except RepoctlError:
        return False
    return True


def _repo_scoped_task(task: Task) -> bool:
    area = str(task.frontmatter.get("area") or "")
    return area in {"repo", "backend", "frontend", "infra", "mobile"}


def _has_backlog_origin(task: Task) -> bool:
    return "Backlog origin:" in task.body


def _discovery_recorded(task: Task) -> bool:
    try:
        section = find_section(task.body, "Discovery")
    except RepoctlError:
        return False
    body = task.body[section.body_start : section.end]
    fields: dict[str, str] = {}
    current_key = ""
    for line in body.splitlines():
        match = re.match(r"^\s*-\s+(Candidate query|Candidate files reviewed|Chosen files):\s*(.*)$", line)
        if match:
            current_key = match.group(1)
            fields[current_key] = match.group(2).strip()
            continue
        if current_key and re.match(r"^\s{2,}-\s+", line):
            fields[current_key] = (fields[current_key] + " " + line.strip()).strip()
    missing = [key for key in ("Candidate query", "Candidate files reviewed", "Chosen files") if not fields.get(key)]
    if missing:
        return False
    placeholders = {"none", "none yet", "n/a", "na", "tbd", "todo", "pending", "-"}
    normalized = {key: fields[key].strip().strip("`").strip().lower() for key in fields}
    if any(normalized[key] in placeholders for key in normalized):
        return False
    chosen = fields["Chosen files"]
    return bool(re.search(r"`repo/[^`]+`", chosen))


def _task_workspace_root(task: Task) -> Path:
    root = task.path
    for _part in Path(task.rel_path).parts:
        root = root.parent
    return root


def _context_doc_paths(task: Task) -> list[str]:
    try:
        section = find_section(task.body, "Context Docs")
    except RepoctlError:
        return []
    body = task.body[section.body_start : section.end]
    paths: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        candidates = re.findall(r"`([^`]+)`", stripped)
        if not candidates and stripped.startswith("-"):
            candidates = [stripped[1:].strip()]
        for candidate in candidates:
            candidate = candidate.strip().strip(".,;:")
            if candidate.startswith(("docs/", "repo/")) or candidate in {"AGENTS.md", "README.md", "CLAUDE.md"}:
                paths.append(candidate)
    return paths


def _repo_head_at_start(task: Task) -> str:
    try:
        section = find_section(task.body, "Execution Log")
    except RepoctlError:
        return ""
    body = task.body[section.body_start : section.end]
    match = re.search(r"^- repo head at start: `([^`]+)`", body, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _execution_log_timestamps(task: Task) -> list[str]:
    try:
        section = find_section(task.body, "Execution Log")
    except RepoctlError:
        return []
    body = task.body[section.body_start : section.end]
    timestamps: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s*-\s+(\d{8}T\d{6}Z):", line)
        if match:
            timestamps.append(match.group(1))
    return timestamps


def _execution_log_timestamp_entries(task: Task) -> list[tuple[int, str]]:
    try:
        section = find_section(task.body, "Execution Log")
    except RepoctlError:
        return []
    body = task.body[section.body_start : section.end]
    line_base = task.body[: section.body_start].count("\n") + 1
    entries: list[tuple[int, str]] = []
    for offset, line in enumerate(body.splitlines(), start=0):
        match = re.match(r"^\s*-\s+(\d{8}T\d{6}Z):", line)
        if match:
            entries.append((line_base + offset, match.group(1)))
    return entries


def _execution_log_timestamp_problem(task: Task, *, now: str | None = None) -> str:
    entries = _execution_log_timestamp_entries(task)
    now = now or utc_stamp()
    previous = ""
    for line, timestamp in entries:
        if previous and timestamp < previous:
            return f"Execution Log timestamp at line {line} is earlier than previous entry ({timestamp} < {previous})"
        if timestamp > now:
            return f"Execution Log timestamp at line {line} is in the future ({timestamp} > current UTC {now})"
        previous = timestamp
    return ""


def _replace_exact(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RepoctlError(f"template placeholder missing: {old}")
    return text.replace(old, new, 1)


def _apply_creation_defaults(
    text: str,
    *,
    title: str,
    task_id: str,
    task_type: str,
    rel_path: Path,
    created: str,
    area: str,
    repo_ref: str,
    parent: str,
    backlog_id: str = "",
    language: str = "en",
) -> str:
    copy = _copy(language)
    area_hint = area or copy["area_unspecified"]
    repo_scoped = bool(repo_ref or area in REPO_REQUIRED_AREAS)
    repo_hint = repo_ref or ("repo/" if repo_scoped else "root workspace")
    scope_line = f"- Repository/worktree: `{repo_hint}`\n" if repo_scoped else "- Workspace scope: root workspace (no `repo_ref`)\n"
    work_area = (
        f"- Task record: `{rel_path.as_posix()}`\n"
        f"{scope_line}"
        f"- Area hint: {area_hint}\n"
        f"- Primary surface: {copy['work_area_primary']}\n"
    )
    if task_type == "parent":
        goal = copy["parent_goal"].format(title=title) + "\n"
        plan = _bullet_lines(copy["parent_plan"])
        handoff = (
            f"- Next exact step: {copy['parent_handoff_next'].format(title=title)}\n"
            f"- First file to open: `{rel_path.as_posix()}`\n"
            "- First command to run: `./scripts/repoctl task list --json`\n"
            f"- Done when: {copy['parent_handoff_done']}\n"
        )
        text = replace_section(text, "Live Child Tasks", f"{copy['live_child_summary']}\n")
        text = replace_section(text, "Non-Live Child Tasks", f"{copy['non_live_child_summary']}\n")
        text = replace_section(text, "Shared Interfaces / Decisions", f"- {copy['shared_decisions']}\n")
        text = replace_section(text, "Integration Done When", f"- {copy['integration_done']}\n")
    else:
        goal = copy["task_goal"].format(title=title) + "\n"
        plan = _bullet_lines(copy["task_plan"])
        handoff = (
            f"- Next exact step: {copy['task_handoff_next'].format(repo_hint=repo_hint)}\n"
            f"- First file to open: `{rel_path.as_posix()}`\n"
            f"- First command to run: `./scripts/repoctl task start {task_id} --json`\n"
            f"- Done when: {copy['task_handoff_done']}\n"
        )
    if parent:
        work_area += f"- Parent task: `{parent}`\n"
    if backlog_id:
        work_area += f"- Backlog origin: `{backlog_id}`\n"
    text = replace_section(text, "Context Docs", f"{copy['context_docs']}\n")
    if task_type != "parent":
        text = replace_section(text, "Discovery", _bullet_lines(copy["discovery"]))
    text = replace_section(text, "Work Area", work_area)
    text = replace_section(text, "Goal", goal)
    text = replace_section(
        text,
        "In Scope",
        _bullet_lines(copy["in_scope"] if repo_scoped else copy["root_in_scope"]),
    )
    text = replace_section(
        text,
        "Out of Scope",
        _bullet_lines(copy["out_of_scope"]),
    )
    text = replace_section(
        text,
        "Plan",
        plan,
    )
    text = replace_section(text, "Execution Log", f"- {created}: {copy['task_created']}\n")
    text = replace_section(text, "Verification", f"- {copy['verification_pending']}\n")
    text = replace_section(text, "Handoff", handoff)
    return text


def create_task_file(
    root: Path,
    *,
    title: str,
    task_type: str = "task",
    slug: str | None = None,
    area: str = "",
    owner: str = "unassigned",
    parent: str = "",
    repo_ref: str = "",
    backlog_id: str = "",
) -> Task:
    if not (root / LOCK_REL).is_dir():
        raise RepoctlError(f"task creation requires repoctl lock: {LOCK_REL}")
    _validate_title(title)
    _validate_area(area)
    _validate_repo_ref(repo_ref)
    validate_repo_ref_area(area, repo_ref)
    if task_type not in {"task", "parent"}:
        raise RepoctlError("--type must be 'task' or 'parent'")
    if task_type == "parent" and parent:
        raise RepoctlError("parent tasks cannot have a parent id")
    _validate_parent_id(parent)
    if parent:
        parent_matches = [task for task in load_tasks(root) if not task.archived and task.id == parent]
        if not parent_matches:
            raise RepoctlError(f"parent task not found: {parent}")
        if parent_matches[0].status not in LIVE or not is_parent_task(parent_matches[0]):
            raise RepoctlError(f"parent task is not a live coordinating parent: {parent}")
    slug = slug or _slug_from_title(title)
    _validate_slug(slug)

    template_name = "PARENT_TEMPLATE.md" if task_type == "parent" else "TEMPLATE.md"
    template_path = root / "docs/tasks" / template_name
    if not template_path.is_file():
        raise RepoctlError(f"template missing: docs/tasks/{template_name}")

    for _attempt in range(20):
        now = datetime.now(UTC)
        ts_file = now.strftime("%Y%m%d%H%M%SZ")
        task_id = f"T-{ts_file}"
        rel_path = Path("docs/tasks") / f"{task_id}--{slug}.md"
        path = root / rel_path
        if list((root / "docs/tasks").glob(f"{task_id}--*.md")) or list((root / "docs/archive/tasks").glob(f"{task_id}--*.md")):
            time.sleep(1)
            continue
        if path.exists():
            time.sleep(1)
            continue

        text = template_path.read_text(encoding="utf-8")
        created = now.strftime("%Y%m%dT%H%M%SZ")
        title_yaml = _escape_yaml_double(title)
        text = _replace_exact(text, "id: T-YYYYMMDDHHMMSSZ", f"id: {task_id}")
        text = text.replace('title: "Replace with task title"', f'title: "{title_yaml}"', 1)
        text = text.replace('title: "Replace with parent task title"', f'title: "{title_yaml}"', 1)
        text = _replace_exact(text, 'owner: "unassigned"', f'owner: "{_escape_yaml_double(owner)}"')
        text = _replace_exact(text, "created: YYYYMMDDTHHMMSSZ", f"created: {created}")
        text = _replace_exact(text, 'repo_ref: ""', f'repo_ref: "{_escape_yaml_double(repo_ref)}"')
        text = _replace_exact(text, 'area: ""', f'area: "{_escape_yaml_double(area)}"')
        text = _replace_exact(text, 'parent: ""', f'parent: "{_escape_yaml_double(parent)}"')
        language = document_language(root)
        text = text.replace("depends_on: []\n", f'depends_on: []\ndocument_language: "{_escape_yaml_double(language)}"\n', 1)
        text = text.replace("# T-YYYYMMDDHHMMSSZ - Title", f"# {task_id} - {title}", 1)
        text = text.replace("# T-YYYYMMDDHHMMSSZ - Parent Title", f"# {task_id} - {title}", 1)
        text = _apply_creation_defaults(
            text,
            title=title,
            task_id=task_id,
            task_type=task_type,
            rel_path=rel_path,
            created=created,
            area=area,
            repo_ref=repo_ref,
            parent=parent,
            backlog_id=backlog_id,
            language=language,
        )
        atomic_write(path, text)
        return load_task(path, root)
    raise RepoctlError("failed to reserve unique task id after 20 retries")


def live_tasks(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if not task.archived and task.status in LIVE]


def children_by_parent(tasks: list[Task]) -> dict[str, list[Task]]:
    result: dict[str, list[Task]] = {}
    for task in tasks:
        if task.parent:
            result.setdefault(task.parent, []).append(task)
    return result


def validate_tasks(tasks: list[Task], *, include_archived_warnings: bool = False) -> list[Problem]:
    problems: list[Problem] = []
    ids = {task.id for task in tasks if task.id}
    children = children_by_parent(tasks)

    def append_warning(task: Task, code: str, message: str) -> None:
        if task.archived and not include_archived_warnings:
            return
        problems.append(Problem("warning", code, message, task.rel_path))

    for task in tasks:
        match = TASK_RE.match(task.path.name)
        if not match:
            problems.append(Problem("error", "invalid_filename", "task filename must be T-YYYYMMDDHHMMSSZ--slug.md", task.rel_path))
        elif task.id != match.group(1):
            problems.append(Problem("error", "id_filename_mismatch", "task id must match filename id", task.rel_path))
        missing = sorted(REQUIRED - set(task.frontmatter))
        if missing:
            problems.append(Problem("error", "missing_frontmatter", f"missing frontmatter fields: {', '.join(missing)}", task.rel_path))
        if task.status not in STATUSES:
            problems.append(Problem("error", "invalid_status", f"invalid status: {task.status}", task.rel_path))
        task_document_language = task.frontmatter.get("document_language")
        if task_document_language is not None:
            if not isinstance(task_document_language, str):
                problems.append(Problem("error", "invalid_document_language", "document_language must be a string", task.rel_path))
            else:
                try:
                    validate_document_language(task_document_language.strip().lower(), source="document_language")
                except RepoctlError as exc:
                    problems.append(Problem("error", "invalid_document_language", str(exc), task.rel_path))
        if task.parent and task.parent not in ids:
            problems.append(Problem("error", "missing_parent", f"parent task not found: {task.parent}", task.rel_path))
        if _repo_scoped_task(task) and not str(task.frontmatter.get("repo_ref") or ""):
            append_warning(task, "missing_repo_ref", "repo-scoped task should record repo_ref for handoff traceability")
        if _repo_scoped_task(task) and task.status in LIVE and not _discovery_recorded(task):
            append_warning(task, "missing_discovery_evidence", "repo-scoped task should record Candidate query, Candidate files reviewed, and Chosen files in Discovery")
        root = _task_workspace_root(task)
        for context_path in _context_doc_paths(task):
            if not (root / context_path).exists():
                append_warning(task, "missing_context_doc", f"Context Docs path does not exist: {context_path}")
        timestamp_problem = _execution_log_timestamp_problem(task)
        if timestamp_problem:
            append_warning(task, "execution_log_timestamp_order", timestamp_problem)
        depends_on = task.frontmatter.get("depends_on", [])
        if isinstance(depends_on, list):
            for item in depends_on:
                if not isinstance(item, str) or not ID_RE.match(item):
                    append_warning(task, "invalid_depends_on", f"depends_on item is not a task id: {item}")
        if task.archived and task.status in LIVE:
            problems.append(Problem("error", "archive_live_status", "archived task must not have live status", task.rel_path))
        if not task.archived and task.status in NON_LIVE:
            is_child = bool(task.parent)
            is_parent = is_parent_task(task)
            if not is_child and not is_parent:
                problems.append(Problem("error", "done_standalone_in_tasks", "standalone done/canceled task must be archived", task.rel_path))
            if is_child:
                parent = next((candidate for candidate in tasks if candidate.id == task.parent), None)
                if parent is not None and parent.archived:
                    problems.append(Problem("error", "non_live_child_of_archived_parent_in_tasks", "child of archived parent must be archived with parent", task.rel_path))
            if is_parent and not any(child.status in LIVE for child in children.get(task.id, [])):
                problems.append(Problem("error", "non_live_parent_in_tasks", "done/canceled parent with no live children must be archived", task.rel_path))
        if task.status in NON_LIVE:
            live_children = [child for child in children.get(task.id, []) if child.status in LIVE]
            if live_children:
                problems.append(Problem("error", "non_live_parent_has_live_child", "done/canceled parent has live child tasks", task.rel_path))
    return problems
