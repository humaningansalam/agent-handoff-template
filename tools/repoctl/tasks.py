from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from .completion_catalogue import prepare_completion_sidecar_writes
from .discovery_outcomes import (
    add_verification_record as add_discovery_verification_record,
    add_workspace_artifact_verification_record,
    completion_outcome_projection,
    load_outcome_state,
    outcome_state_path,
    serialize_outcome_state,
    update_outcome_state,
    validate_completion_outcome,
)
from .io import LOCK_REL, RepoctlError, atomic_write, decode_schema_version
from .git import ChangedEntry, RepoGitState, StablePathState, normalize_repo_path, normalize_stable_path_state, repo_change_fingerprint_records, repo_changed_entries, repo_commit_range_entries, repo_git_head, repo_git_state, repo_git_status, repo_is_ancestor, repo_path_fingerprints, repo_path_stable_states, stable_path_state_digest, verify_legacy_change_terminal_states
from .graph_model import digest_data
from .markdown import append_section_entry, find_section, has_section, parse_frontmatter, parse_labeled_list_section, replace_frontmatter_line, replace_section
from .repositories import REPO_REQUIRED_TASK_AREAS, TASK_AREAS, RepoLayout, RepoSelectorStatus, RepoTarget, RepositoryIdentitySource, default_repo_target, repo_layout, resolve_repo_selector_path, resolve_task_repo_target
from .result_receipts import ResultAuthority as DiscoveryResultAuthority
from .result_receipts import ResultProducer as DiscoveryResultProducer
from .result_receipts import ResultSelection, parse_result_request, result_receipt_episode, verify_result_selections
from .settings import document_language, validate_document_language

LIVE = {"todo", "doing", "blocked"}
NON_LIVE = {"done", "canceled"}
STATUSES = LIVE | NON_LIVE
AREAS = TASK_AREAS
REPO_REQUIRED_AREAS = REPO_REQUIRED_TASK_AREAS
TASK_RE = re.compile(r"^(T-[0-9]{14}Z)--[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ID_RE = re.compile(r"^T-[0-9]{14}Z$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED = {"id", "title", "status", "owner", "created", "parent", "depends_on"}
TASK_STATE_SCHEMA_VERSION = 4
LEGACY_TASK_STATE_SCHEMA_VERSION = 3
COMPLETION_RECEIPT_SCHEMA_VERSION = 4
TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION = 3
LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION = 2
RESUME_BINDING_SCHEMA_VERSION = 4
LEGACY_RESUME_BINDING_SCHEMA_VERSIONS = {1, 2, 3}
ARCHIVE_LOCATOR_SCHEMA_VERSION = 1
HANDOFF_GENERATED_MARKER = "<!-- repoctl: generated-handoff -->"


class TaskHandoffStatus(StrEnum):
    CURRENT = "current"
    INACTIVE = "inactive"
    HISTORICAL = "historical"


class TaskResumeSelectionStatus(StrEnum):
    NO_LIVE = "no_live"
    SINGLE_LIVE = "single_live"
    AMBIGUOUS = "ambiguous"


class _CompletionEvidenceMode(StrEnum):
    NONE = "none"
    WORKING_TREE_DIFF = "working_tree_diff"
    COMMITTED_RANGE = "committed_range"


class BaselineOwnership(StrEnum):
    TASK = "task"
    PREEXISTING = "preexisting"


class RepositoryLineageStatus(StrEnum):
    BASELINE_MISSING = "baseline_missing"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNAVAILABLE = "unavailable"
    SAME_HEAD = "same_head"
    DESCENDANT = "descendant"
    REWRITTEN = "rewritten"


@dataclass(frozen=True)
class CompletionReceiptArtifact:
    receipt: dict[str, Any]
    receipt_path: str
    receipt_text: str
    receipt_sha256: str
    declared_path: str
    resolved_path: str
    content_sha256: str
    artifact_text: str


class CompletionReceiptArtifactResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    INVALID_IDENTITY = "invalid_identity"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    UNRESOLVABLE = "unresolvable"
    OUTSIDE_WORKSPACE = "outside_workspace"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class CompletionReceiptArtifactResolution:
    receipt_path: str
    declared_path: str
    status: CompletionReceiptArtifactResolutionStatus
    candidate_paths: tuple[str, ...]
    existing_paths: tuple[str, ...]
    receipt_sha256: str = ""
    resolved_path: str = ""
    content_sha256: str = ""
    artifact_bytes: bytes = b""
    artifact_text: str = ""

    def input_identity(self) -> dict[str, object]:
        return {
            "receipt_path": self.receipt_path,
            "declared_path": self.declared_path,
            "status": self.status.value,
            "candidate_paths": list(self.candidate_paths),
            "existing_paths": list(self.existing_paths),
            "receipt_sha256": self.receipt_sha256,
            "resolved_path": self.resolved_path,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class CompletionReceiptCollection:
    artifacts: tuple[CompletionReceiptArtifact, ...]
    problems: tuple[Problem, ...]
    resolutions: tuple[CompletionReceiptArtifactResolution, ...]

    @property
    def input_digest(self) -> str:
        return digest_data(
            {
                "artifacts": [
                    {
                        "receipt_path": artifact.receipt_path,
                        "receipt_sha256": artifact.receipt_sha256,
                        "declared_path": artifact.declared_path,
                        "resolved_path": artifact.resolved_path,
                        "content_sha256": artifact.content_sha256,
                    }
                    for artifact in self.artifacts
                ],
                "problems": [
                    {
                        "code": problem.code,
                        "path": problem.path or "",
                        "cause_code": problem.cause_code or "",
                    }
                    for problem in self.problems
                ],
                "resolutions": [resolution.input_identity() for resolution in self.resolutions],
            }
        )

    @property
    def candidate_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    path
                    for resolution in self.resolutions
                    for path in resolution.candidate_paths
                }
            )
        )

    @property
    def input_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.candidate_paths,
                    *(
                        resolution.receipt_path
                        for resolution in self.resolutions
                        if resolution.receipt_path
                    ),
                    *(problem.path for problem in self.problems if problem.path),
                }
            )
        )


@dataclass(frozen=True)
class DiscoveryResultSelection:
    producer: DiscoveryResultProducer
    result_id: str
    authority: DiscoveryResultAuthority
    ref: str
    episode_id: str = ""
    request: dict[str, Any] | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.result_id, str) or not isinstance(self.ref, str) or not isinstance(self.episode_id, str):
            raise ValueError("selected result identity fields must be strings")
        if type(self.schema_version) is not int:
            raise ValueError("selected result evidence schema version must be an integer")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.result_id):
            raise ValueError("selected result identity must be a sha256 digest")
        if not self.ref.strip() or self.ref != self.ref.strip():
            raise ValueError("selected result reference must be a non-empty canonical value")
        if self.schema_version == 1:
            if self.episode_id or self.request is not None:
                raise ValueError("legacy selected result evidence must not invent request ownership")
            return
        if self.schema_version != 2:
            raise ValueError("selected result evidence has an unsupported schema version")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.episode_id):
            raise ValueError("selected result episode identity must be a sha256 digest")
        if self.request is None or parse_result_request(self.producer, self.request).to_dict() != self.request:
            raise ValueError("selected result request must be canonical structured data")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "producer": self.producer.value,
            "result_id": self.result_id,
            "authority": self.authority.value,
            "ref": self.ref,
        }
        if self.schema_version == 1:
            return data
        return {
            "schema_version": self.schema_version,
            **data,
            "episode_id": self.episode_id,
            "request": self.request,
        }

    def to_text(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True).replace("`", "\\u0060")

    @classmethod
    def from_text(cls, value: str) -> "DiscoveryResultSelection":
        try:
            data = json.loads(_strip_ticks(value))
            if not isinstance(data, dict):
                raise ValueError
            legacy = set(data) == {"producer", "result_id", "authority", "ref"}
            current = set(data) == {"schema_version", "producer", "result_id", "episode_id", "request", "authority", "ref"}
            if not legacy and not current:
                raise ValueError
            return cls(
                producer=DiscoveryResultProducer(data["producer"]),
                result_id=data["result_id"],
                authority=DiscoveryResultAuthority(data["authority"]),
                ref=data["ref"],
                episode_id=data.get("episode_id", ""),
                request=data.get("request") if isinstance(data.get("request"), dict) else None,
                schema_version=1 if legacy else data["schema_version"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RepoctlError(
                "Selected result evidence must be canonical structured data produced by repoctl",
                code="invalid_discovery_result_evidence",
            ) from exc


class _CompletionEvidenceAttribution(StrEnum):
    NONE = "none"
    TASK_WORKING_TREE = "task_working_tree"
    RANGE_OBSERVED = "range_observed"


_COMPLETION_ATTRIBUTION_BY_MODE = {
    _CompletionEvidenceMode.NONE: _CompletionEvidenceAttribution.NONE,
    _CompletionEvidenceMode.WORKING_TREE_DIFF: _CompletionEvidenceAttribution.TASK_WORKING_TREE,
    _CompletionEvidenceMode.COMMITTED_RANGE: _CompletionEvidenceAttribution.RANGE_OBSERVED,
}

TASK_DOC_COPY: dict[str, dict[str, Any]] = {
    "en": {
        "area_unspecified": "not specified",
        "task_created": "task created via repoctl task create.",
        "task_started": "task started.",
        "task_started_dirty": "task started with dirty repo state recorded.",
        "task_started_git_unavailable": "task started; repo dirty check unavailable ({reason}).",
        "task_finished": "task finished and verified.",
        "task_canceled": "task canceled: {reason}",
        "task_blocked": "task blocked: {reason}",
        "repo_head_at_start": "repo head at start",
        "closure_done": "Implementation and verification completed.",
        "closure_canceled": "Task canceled with recorded evidence.",
        "git_delivery_outside": "Not managed by repoctl.",
        "done_handoff_next": "No further action; task is complete.",
        "canceled_handoff_next": "No further action; task is canceled.",
        "blocked_handoff_next": "Resolve the recorded blocker or update the task with new evidence.",
        "done_handoff_done": "Task remains archived or non-live according to repoctl check.",
        "canceled_handoff_done": "Task remains archived or non-live as canceled according to repoctl check.",
        "blocked_handoff_done": "The blocker is resolved or the task remains explicitly blocked with current evidence.",
        "work_area_primary": "Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.",
        "parent_goal": "Coordinate `{title}` by splitting the work into child tasks, keeping shared decisions current, and closing integration with verification evidence.",
        "parent_plan": [
            "Inspect the likely repos/docs surfaces and decide whether child tasks are truly needed.",
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
        "task_handoff_next": "Start the task, inspect `{repo_hint}`, and record Discovery before editing.",
        "task_handoff_done": "Chosen files match the actual changes, Verification is complete, and repoctl finish records the result.",
        "task_scope": [
            "Change only the files recorded in Discovery for this goal.",
            "Keep `repos/.repometa` annotations valid when coverage requires them for changed product files.",
            "Run focused validation and `repoctl meta check --changed` before finish.",
            "Commit, push, PR, deploy, and release state are not managed by repoctl.",
        ],
        "root_scope": [
            "Change only the workspace/docs files needed for this goal.",
            "Do not touch product files under `repos/` unless this becomes a repo-scoped task.",
            "Commit, push, PR, deploy, and release state are not managed by repoctl.",
        ],
        "in_scope": [
            "Identify and record the concrete files/docs that define this task.",
            "Make only the narrow changes needed for the stated goal.",
            "Keep `repos/.repometa` annotations valid for any changed `repos/` files required by metadata coverage policy.",
            "Keep Execution Log entries meaningful: creation, start, implementation decision, verification, blocker, or finish.",
            "Record commands and results in `## Verification`; use an external file only when an existing artifact is the evidence source.",
        ],
        "root_in_scope": [
            "Identify and record the concrete workspace/docs files that define this task.",
            "Make only the narrow changes needed for the stated goal.",
            "Do not touch product files under `repos/` unless the task is intentionally converted into repo-scoped work.",
            "Keep Execution Log entries meaningful: creation, start, implementation decision, verification, blocker, or finish.",
            "Record commands and results in `## Verification`; use an external file only when an existing artifact is the evidence source.",
        ],
        "out_of_scope": [
            "Unrelated refactors or cleanup.",
            "Branch, commit, PR, deploy, or release automation unless explicitly requested.",
        ],
        "verification_pending": "Pending.",
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
        "task_canceled": "작업을 취소함: {reason}",
        "task_blocked": "작업을 blocked로 표시함: {reason}",
        "repo_head_at_start": "repo head at start",
        "closure_done": "구현과 검증을 완료함.",
        "closure_canceled": "기록된 증거와 함께 작업을 취소함.",
        "git_delivery_outside": "repoctl 관리 범위가 아님.",
        "done_handoff_next": "추가 작업 없음; 작업이 완료됨.",
        "canceled_handoff_next": "추가 작업 없음; 작업이 취소됨.",
        "blocked_handoff_next": "기록된 blocker를 해결하거나 새 증거로 작업을 업데이트한다.",
        "done_handoff_done": "repoctl check 기준으로 작업이 archive 또는 non-live 상태를 유지함.",
        "canceled_handoff_done": "repoctl check 기준으로 작업이 canceled archive 또는 non-live 상태를 유지함.",
        "blocked_handoff_done": "blocker가 해결되었거나 작업이 최신 증거와 함께 명시적으로 blocked 상태임.",
        "work_area_primary": "첫 구현 단계에서 정확한 repo, docs, workspace 파일을 확인한다. 제목만 보고 추측하지 않는다.",
        "parent_goal": "`{title}`를 조율한다. 필요한 child task로 나누고, 공유 결정을 최신으로 유지하며, 통합 검증 증거로 마무리한다.",
        "parent_plan": [
            "관련 repos/docs 표면을 확인하고 child task가 정말 필요한지 판단한다.",
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
        "task_handoff_next": "작업을 시작하고 `{repo_hint}`를 확인한 뒤 편집 전에 Discovery를 기록한다.",
        "task_handoff_done": "Chosen files와 실제 변경이 일치하고 Verification이 완료되며 repoctl finish가 결과를 기록함.",
        "task_scope": [
            "이 목표를 위해 Discovery에 기록한 파일만 변경한다.",
            "변경한 제품 파일에 필요한 `repos/.repometa` annotation을 유효하게 유지한다.",
            "완료 전에 집중 검증과 `repoctl meta check --changed`를 실행한다.",
            "commit, push, PR, deploy, release 상태는 repoctl이 관리하지 않는다.",
        ],
        "root_scope": [
            "이 목표에 필요한 workspace/docs 파일만 변경한다.",
            "repo-scoped task로 전환하지 않는 한 `repos/` 제품 파일을 편집하지 않는다.",
            "commit, push, PR, deploy, release 상태는 repoctl이 관리하지 않는다.",
        ],
        "in_scope": [
            "이 작업을 정의하는 구체적인 파일/docs를 식별하고 기록한다.",
            "명시된 목표에 필요한 좁은 변경만 수행한다.",
            "metadata coverage policy가 요구하는 변경 `repos/` 파일의 `repos/.repometa` annotation을 유효하게 유지한다.",
            "Execution Log에는 생성, 시작, 구현 결정, 검증, blocker, 완료처럼 의미 있는 항목만 남긴다.",
            "명령과 결과를 `## Verification`에 기록한다. 이미 존재하는 외부 artifact가 증거 원본일 때만 외부 파일을 사용한다.",
        ],
        "root_in_scope": [
            "이 작업을 정의하는 구체적인 workspace/docs 파일을 식별하고 기록한다.",
            "명시된 목표에 필요한 좁은 변경만 수행한다.",
            "작업을 의도적으로 repo-scoped로 전환하지 않는 한 `repos/` 제품 파일은 건드리지 않는다.",
            "Execution Log에는 생성, 시작, 구현 결정, 검증, blocker, 완료처럼 의미 있는 항목만 남긴다.",
            "명령과 결과를 `## Verification`에 기록한다. 이미 존재하는 외부 artifact가 증거 원본일 때만 외부 파일을 사용한다.",
        ],
        "out_of_scope": [
            "무관한 refactor 또는 cleanup.",
            "명시적으로 요청되지 않은 branch, commit, PR, deploy, release 자동화.",
        ],
        "verification_pending": "대기 중.",
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


def _render_created_handoff(
    *,
    copy: dict[str, Any],
    task_type: str,
    title: str,
    task_id: str,
    task_path: str,
    repo_hint: str,
) -> str:
    if task_type == "parent":
        return (
            f"{HANDOFF_GENERATED_MARKER}\n"
            f"- Next exact step: {copy['parent_handoff_next'].format(title=title)}\n"
            f"- First file to open: `{task_path}`\n"
            "- First command to run: `./scripts/repoctl task list --json`\n"
            f"- Done when: {copy['parent_handoff_done']}\n"
        )
    return (
        f"{HANDOFF_GENERATED_MARKER}\n"
        f"- Next exact step: {copy['task_handoff_next'].format(repo_hint=repo_hint)}\n"
        f"- First file to open: `{task_path}`\n"
        f"- First command to run: `./scripts/repoctl task start {task_id} --json`\n"
        f"- Done when: {copy['task_handoff_done']}\n"
    )


@dataclass(frozen=True)
class Problem:
    severity: str
    code: str
    message: str
    path: str | None = None
    cause_code: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path is not None:
            data["path"] = self.path
        if self.cause_code is not None:
            data["cause_code"] = self.cause_code
        return data


@dataclass(frozen=True)
class RepositoryTransitionObservation:
    """One read-only Git transition observation with non-owning change segments."""

    repo_id: str
    repo_path: str
    historical_git_toplevel: str
    start_head: str
    current_head: str
    lineage: RepositoryLineageStatus
    committed_changes: tuple[ChangedEntry, ...]
    worktree_changes: tuple[ChangedEntry, ...]
    repo_git: RepoGitState
    problems: tuple[Problem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_identity": {
                "repo_id": self.repo_id,
                "repo_path": self.repo_path,
            },
            "historical_locator": {
                "git_toplevel": self.historical_git_toplevel,
            },
            "heads": {
                "start": self.start_head,
                "current": self.current_head,
            },
            "lineage": self.lineage.value,
            "segments": {
                "committed": [_entry_to_dict(entry) for entry in self.committed_changes],
                "worktree": [_entry_to_dict(entry) for entry in self.worktree_changes],
            },
            "git": {
                "available": self.repo_git.available,
                "reason": self.repo_git.reason,
                "problem_code": self.repo_git.problem_code,
            },
            "problems": [problem.to_dict() for problem in self.problems],
        }


@dataclass(frozen=True)
class _DescendantPathClaim:
    task_id: str
    receipt_path: str
    repo_id: str
    path: str
    effect: str
    basis: tuple[str, ...]
    before: StablePathState
    after: StablePathState
    started_at: str
    completed_at: str

    @property
    def before_digest(self) -> str:
        return stable_path_state_digest(self.before)

    @property
    def after_digest(self) -> str:
        return stable_path_state_digest(self.after)


@dataclass(frozen=True)
class _DescendantAttributionResult:
    remaining: tuple[ChangedEntry, ...]
    attributed: tuple[dict[str, Any], ...]
    problems: tuple[Problem, ...]


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
            "repo_id": str(self.frontmatter.get("repo_id") or ""),
            "parent": self.parent,
            "follow_up_of": str(self.frontmatter.get("follow_up_of") or ""),
            "depends_on": depends_on,
        }


@dataclass(frozen=True)
class TaskResumeSelection:
    status: TaskResumeSelectionStatus
    live_task_count: int
    task: Task | None
    candidates: tuple[Task, ...]


@dataclass(frozen=True)
class VerificationInput:
    source: str
    text: str
    source_sha256: str
    source_path: str = ""


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def load_task(path: Path, root: Path, *, archived: bool = False) -> Task:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    return Task(path=path, rel_path=_rel(root, path), frontmatter=frontmatter, body=body, archived=archived)


def load_tasks(root: Path, *, include_archived: bool = False) -> list[Task]:
    tasks: list[Task] = []
    for path in sorted((root / "docs/tasks").glob("T-*.md")):
        tasks.append(load_task(path, root, archived=False))
    archive_dir = root / "docs/archive/tasks"
    if include_archived and archive_dir.exists():
        for path in sorted(archive_dir.glob("T-*.md")):
            tasks.append(load_task(path, root, archived=True))
    elif archive_dir.exists():
        # Follow-up authority is transitive. Resolve only archived identities
        # reachable from current tasks so ordinary checks remain independent of
        # total archive size without dropping a predecessor's own ancestry.
        pending = sorted({
            str(task.frontmatter.get("follow_up_of") or "")
            for task in tasks
            if ID_RE.fullmatch(str(task.frontmatter.get("follow_up_of") or ""))
        })
        requested = set(pending)
        loaded_ids = {task.id for task in tasks}
        for task_id in pending:
            if task_id in loaded_ids:
                continue
            path = _task_archive_locator(root, task_id)
            if path is not None:
                archived_task = load_task(path, root, archived=True)
                tasks.append(archived_task)
                loaded_ids.add(archived_task.id)
                predecessor = str(archived_task.frontmatter.get("follow_up_of") or "")
                if ID_RE.fullmatch(predecessor) and predecessor not in loaded_ids and predecessor not in requested:
                    requested.add(predecessor)
                    pending.append(predecessor)
    return tasks


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def normalize_task_id(task_id: str) -> str:
    candidate = str(task_id).strip().replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    name = candidate.rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    match = re.fullmatch(r"(T-[0-9]{14}Z)(?:--[a-z0-9]+(?:-[a-z0-9]+)*)?", name)
    if match:
        return match.group(1)
    raise RepoctlError("invalid task id format; expected T-YYYYMMDDHHMMSSZ", code="invalid_task_id")


def resolve_live_task(root: Path, task_id: str) -> Task:
    task_id = normalize_task_id(task_id)
    matches = sorted((root / "docs/tasks").glob(f"{task_id}--*.md"))
    if not matches:
        raise RepoctlError(f"task not found: {task_id}", code="task_not_found")
    if len(matches) > 1:
        raise RepoctlError(f"ambiguous task id: {task_id}")
    return load_task(matches[0], root)


def resolve_task(root: Path, task_id: str) -> Task:
    task_id = normalize_task_id(task_id)
    matches = sorted((root / "docs/tasks").glob(f"{task_id}--*.md"))
    if len(matches) > 1:
        raise RepoctlError(f"ambiguous task id: {task_id}")
    if matches:
        return load_task(matches[0], root)
    archived = _task_archive_locator(root, task_id)
    if archived is None:
        raise RepoctlError(f"task not found: {task_id}", code="task_not_found")
    return load_task(archived, root, archived=True)


def append_task_log(root: Path, task_id: str, message: str) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    if task.status not in LIVE:
        raise RepoctlError("done or canceled tasks are immutable; create a follow-up task", code="task_not_live", path=task.rel_path)
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


def _normalize_discovery_path(value: str) -> str:
    raw = _strip_ticks(value)
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "\\" in raw:
        return ""
    normalized = normalize_repo_path(raw)
    return normalized if normalized.startswith("repos/") else ""


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _strip_ticks(value).strip()
        if not normalized:
            continue
        key = normalized
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


DISCOVERY_PLACEHOLDERS = frozenset({"none", "none yet", "n/a", "na", "tbd", "todo", "pending", "-"})


def _explicit_discovery_values(values: list[str]) -> list[str]:
    """Discard only a field's sole unquoted template placeholder."""

    if len(values) == 1:
        raw = values[0].strip()
        backticked = len(raw) >= 2 and raw[0] == raw[-1] == "`"
        if not backticked and raw.casefold() in DISCOVERY_PLACEHOLDERS:
            return []
    return _dedupe_preserve(values)


def task_discovery_values(task: Task) -> dict[str, list[str]]:
    try:
        fields = parse_labeled_list_section(
            task.body,
            "Discovery",
            ("Candidate query", "Candidate files reviewed", "Chosen files", "Selected result evidence", "Notes"),
        )
    except RepoctlError:
        return {}
    return {key: _explicit_discovery_values(values) for key, values in fields.items()}


def task_discovery_result_selections(task: Task) -> list[DiscoveryResultSelection]:
    return [
        DiscoveryResultSelection.from_text(value)
        for value in task_discovery_values(task).get("Selected result evidence", [])
    ]


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
    excluded: list[str] | None = None,
    chosen: list[str] | None = None,
    replace_chosen: list[str] | None = None,
    reason: str = "",
    note: str = "",
    result_producer: str = "",
    result_id: str = "",
    result_authority: str = "",
    result_refs: list[str] | None = None,
) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    if task.status not in LIVE:
        raise RepoctlError("done or canceled tasks are immutable; create a follow-up task", code="task_not_live", path=task.rel_path)
    reviewed = reviewed or []
    excluded = excluded or []
    chosen = chosen or []
    replace_chosen = replace_chosen or []
    result_refs = result_refs or []
    if chosen and replace_chosen:
        raise RepoctlError("task discovery add accepts --chosen or --replace-chosen, not both", code="ambiguous_chosen_update", path=task.rel_path)
    if replace_chosen and not reason.strip():
        raise RepoctlError("--replace-chosen requires --reason", code="missing_scope_change_reason", path=task.rel_path)
    result_fields_present = [bool(result_producer), bool(result_id), bool(result_authority), bool(result_refs)]
    if any(result_fields_present) and not all(result_fields_present):
        raise RepoctlError(
            "selected result evidence requires --result-producer, --result-id, --result-authority, and --result-ref",
            code="incomplete_discovery_result_evidence",
            path=task.rel_path,
        )
    if not any([query.strip(), reviewed, excluded, chosen, replace_chosen, note.strip(), result_refs]):
        raise RepoctlError("task discovery add requires scope evidence, a note, or selected result evidence", code="missing_discovery_input", path=task.rel_path)

    fields = task_discovery_values(task)
    previous_queries = fields.get("Candidate query", [])
    previous_reviewed = fields.get("Candidate files reviewed", [])
    previous_chosen = fields.get("Chosen files", [])
    previous_notes = fields.get("Notes", [])
    previous_result_selections = [
        DiscoveryResultSelection.from_text(value)
        for value in fields.get("Selected result evidence", [])
    ]

    incoming_result_selections: list[DiscoveryResultSelection] = []
    receipt_request_episode_id = ""
    receipt_seed_query = ""
    receipt_request: dict[str, Any] | None = None
    incoming_producer: DiscoveryResultProducer | None = None
    requested_selections: list[ResultSelection] = []
    target = _target_for_task(root, task)
    _require_task_start_scope_alignment(root, task, target=target)
    if result_refs:
        if target is None:
            raise RepoctlError(
                "selected result evidence requires a repository-scoped task",
                code="discovery_result_repository_required",
                path=task.rel_path,
            )
        try:
            incoming_producer = DiscoveryResultProducer(result_producer)
            authority = DiscoveryResultAuthority(result_authority)
            requested_selections = [ResultSelection(authority, ref) for ref in result_refs]
            receipt = verify_result_selections(
                root,
                target=target,
                producer=incoming_producer,
                result_id=result_id,
                selections=requested_selections,
            )
            episode = result_receipt_episode(receipt)
            receipt_request_episode_id = episode.id
            receipt_seed_query = episode.seed_query
            receipt_request = receipt["request"]
        except (RepoctlError, ValueError) as exc:
            if isinstance(exc, RepoctlError):
                raise
            raise RepoctlError(str(exc), code="invalid_discovery_result_evidence", path=task.rel_path) from exc

    previous_episode_ids = {
        selection.episode_id
        for selection in previous_result_selections
        if selection.episode_id
    }
    if len(previous_episode_ids) > 1:
        raise RepoctlError(
            "current Discovery result evidence belongs to more than one episode",
            code="discovery_result_episode_conflict",
            path=task.rel_path,
        )
    active_episode_id = next(iter(previous_episode_ids), "")
    active_context_owner = any(
        selection.producer == DiscoveryResultProducer.CONTEXT
        and selection.episode_id == active_episode_id
        for selection in previous_result_selections
    )
    explicit_query = _strip_ticks(query).strip()
    active_query = previous_queries[-1] if previous_queries else ""
    adopts_context_owner = False
    if incoming_producer == DiscoveryResultProducer.CONTEXT:
        if explicit_query and explicit_query != receipt_seed_query:
            raise RepoctlError(
                "selected Context result request does not match the supplied Discovery query",
                code="discovery_result_episode_mismatch",
                path=task.rel_path,
            )
        incoming_query = receipt_seed_query
        incoming_episode_id = receipt_request_episode_id
        if active_episode_id and active_context_owner:
            starts_new_episode = active_episode_id != incoming_episode_id
        elif active_query:
            starts_new_episode = active_query != incoming_query
            adopts_context_owner = not starts_new_episode and bool(active_episode_id)
        else:
            starts_new_episode = False
    elif incoming_producer == DiscoveryResultProducer.GRAPH:
        incoming_query = explicit_query or active_query or receipt_seed_query
        if explicit_query and active_query and explicit_query != active_query:
            incoming_episode_id = digest_data({"kind": "task_discovery_query", "query": explicit_query})
            starts_new_episode = True
        elif active_episode_id:
            incoming_episode_id = active_episode_id
            starts_new_episode = False
        elif active_query:
            incoming_episode_id = digest_data({"kind": "task_discovery_query", "query": active_query})
            starts_new_episode = False
        else:
            incoming_episode_id = receipt_request_episode_id
            starts_new_episode = False
    else:
        incoming_episode_id = ""
        incoming_query = explicit_query
        starts_new_episode = bool(incoming_query and incoming_query != active_query)
    if incoming_producer is not None:
        incoming_result_selections = [
            DiscoveryResultSelection(
                producer=incoming_producer,
                result_id=result_id,
                episode_id=incoming_episode_id,
                request=receipt_request,
                authority=selection.authority,
                ref=selection.ref,
            )
            for selection in requested_selections
        ]
    query_values = [incoming_query or active_query] if incoming_query or active_query else []
    episode_reviewed = [] if starts_new_episode else previous_reviewed
    episode_notes = [] if starts_new_episode else previous_notes
    if starts_new_episode:
        episode_result_selections = []
    elif adopts_context_owner:
        episode_result_selections = [
            DiscoveryResultSelection(
                producer=selection.producer,
                result_id=selection.result_id,
                episode_id=incoming_episode_id if selection.schema_version == 2 else "",
                request=selection.request,
                schema_version=selection.schema_version,
                authority=selection.authority,
                ref=selection.ref,
            )
            for selection in previous_result_selections
        ]
    else:
        episode_result_selections = previous_result_selections
    reviewed_values = _dedupe_preserve([*episode_reviewed, *reviewed, *excluded])
    chosen_values = _dedupe_preserve(replace_chosen) if replace_chosen else _dedupe_preserve([*previous_chosen, *chosen])
    note_values = _dedupe_preserve([*episode_notes, *([note] if note.strip() else [])])
    result_selection_by_text = {
        selection.to_text(): selection
        for selection in [*episode_result_selections, *incoming_result_selections]
    }
    episode_result_selection_texts = {
        selection.to_text() for selection in episode_result_selections
    }
    result_selections = [result_selection_by_text[key] for key in sorted(result_selection_by_text)]
    if target is not None:
        for label, values in (("reviewed", reviewed_values), ("excluded", excluded), ("chosen", chosen_values)):
            normalized_paths = [_normalize_discovery_path(value) for value in values]
            invalid_paths = [value for value, normalized in zip(values, normalized_paths, strict=True) if not normalized]
            if invalid_paths:
                escaping_paths = [value for value in invalid_paths if ".." in Path(_strip_ticks(value)).parts]
                if escaping_paths:
                    raise RepoctlError(
                        f"{label} discovery files must stay under selected repository {target.id} ({target.display_path}): {', '.join(escaping_paths)}",
                        code="discovery_outside_selected_repository",
                        path=escaping_paths[0],
                    )
                raise RepoctlError(
                    f"{label} discovery files must be workspace-relative product paths: {', '.join(invalid_paths)}",
                    code="invalid_discovery_path",
                    path=invalid_paths[0],
                )
            outside = _discovery_paths_outside_target(normalized_paths, target)
            if outside:
                raise RepoctlError(
                    f"{label} discovery files must stay under selected repository {target.id} ({target.display_path}): {', '.join(outside)}",
                    code="discovery_outside_selected_repository",
                    path=outside[0],
                )
        directory_paths = [
            path
            for path in _dedupe_preserve([*reviewed_values, *chosen_values])
            if (normalized := _normalize_discovery_path(path)) and (root / normalized).is_dir()
        ]
        if directory_paths:
            raise RepoctlError(
                f"discovery entries must be files, not directories: {', '.join(directory_paths)}",
                code="discovery_path_is_directory",
                path=directory_paths[0],
            )

    lines: list[str] = []
    lines.extend(_format_discovery_list("Candidate query", query_values))
    lines.extend(_format_discovery_list("Candidate files reviewed", reviewed_values))
    lines.extend(_format_discovery_list("Chosen files", chosen_values))
    if result_selections:
        lines.extend(_format_discovery_list("Selected result evidence", [selection.to_text() for selection in result_selections]))
    if note_values:
        lines.extend(_format_discovery_list("Notes", note_values))
    current_text = task.path.read_text(encoding="utf-8")
    discovery_body = "\n".join(lines) + "\n"
    try:
        text = replace_section(current_text, "Discovery", discovery_body)
    except RepoctlError as exc:
        if exc.code != "missing_section":
            raise
        execution_log = find_section(current_text, "Execution Log")
        text = current_text[: execution_log.start] + f"## Discovery\n\n{discovery_body}\n" + current_text[execution_log.start :]
    if replace_chosen:
        removed = sorted(set(previous_chosen) - set(chosen_values))
        added = sorted(set(chosen_values) - set(previous_chosen))
        text = append_section_entry(
            text,
            "Execution Log",
            f"- {utc_stamp()}: scope changed: removed {', '.join(removed) or 'none'}; added {', '.join(added) or 'none'}; reason={reason.strip()}",
        )
    outcome_state = update_outcome_state(
        root,
        task_id=task.id,
        target=target,
        query=query_values[-1] if query_values else "",
        episode_id=incoming_episode_id or active_episode_id,
        starts_new_episode=starts_new_episode,
        reviewed_paths=reviewed_values,
        excluded_paths=excluded,
        chosen_paths=chosen_values,
        result_receipt=receipt if result_refs else None,
        result_selections=requested_selections,
    )
    return {
        "task": task,
        "text": text,
        "discovery": {
            "candidate_query": query_values[-1] if query_values else "",
            "candidate_query_history": query_values,
            "candidate_files_reviewed": reviewed_values,
            "chosen_files": chosen_values,
            "excluded_files": [
                str(item.get("identity", {}).get("path") or "")
                for item in (outcome_state.get("active_episode") or {}).get("excluded", [])
            ],
            "notes": note_values,
            "selected_result_evidence": [selection.to_dict() for selection in result_selections],
        },
        "update": {
            "candidate_queries": {
                "episode_changed": starts_new_episode,
                "added": [incoming_query] if starts_new_episode else [],
                "already_present": [incoming_query] if incoming_query and incoming_query == active_query else [],
            },
            "reviewed_files": {
                "added": sorted(set(reviewed_values) - set(episode_reviewed)),
                "removed": sorted(set(previous_reviewed) - set(reviewed_values)),
                "already_present": sorted(set(reviewed) & set(episode_reviewed)),
            },
            "chosen_files": {
                "mode": "replace" if replace_chosen else "append" if chosen else "unchanged",
                "added": sorted(set(chosen_values) - set(previous_chosen)),
                "removed": sorted(set(previous_chosen) - set(chosen_values)),
                "already_present": sorted(set(replace_chosen or chosen) & set(previous_chosen)),
            },
            "notes": {
                "added": [value for value in note_values if value not in episode_notes],
                "removed": [value for value in previous_notes if value not in note_values],
                "already_present": [note] if note.strip() and note in episode_notes else [],
            },
            "selected_result_evidence": {
                "added": [
                    selection.to_dict()
                    for selection in result_selections
                    if selection.to_text() not in episode_result_selection_texts
                ],
                "removed": [
                    selection.to_dict()
                    for selection in previous_result_selections
                    if selection.to_text() not in result_selection_by_text
                ],
                "already_present": [
                    selection.to_dict()
                    for selection in incoming_result_selections
                    if selection.to_text() in episode_result_selection_texts
                ],
            },
        },
        "totals": {
            "candidate_query_count": len(query_values),
            "reviewed_file_count": len(reviewed_values),
            "chosen_file_count": len(chosen_values),
            "excluded_file_count": len((outcome_state.get("active_episode") or {}).get("excluded", [])),
            "note_count": len(note_values),
            "selected_result_evidence_count": len(result_selections),
        },
        "state_writes": [
            (outcome_state_path(root, task.id), serialize_outcome_state(outcome_state)),
        ],
    }


def _require_task_start_scope_alignment(
    root: Path,
    task: Task,
    *,
    target: RepoTarget | None,
    require_current_start: bool = False,
) -> dict[str, Any] | None:
    baseline = _read_repo_baseline(root, task.id)
    if baseline is None:
        if not require_current_start and task.status == "todo":
            return None
        raise RepoctlError(
            "task mutation requires current task-start transition evidence",
            code="transition_evidence_incomplete",
            path=task.rel_path,
        )

    started_at = baseline.get("started_at") if isinstance(baseline, dict) else None
    workspace_start = isinstance(baseline.get("repositories"), list)
    scope_matches = (
        workspace_start
        and target is None
        and baseline.get("repo_id") == ""
        and baseline.get("repo_path") == ""
    ) or (
        not workspace_start
        and target is not None
        and baseline.get("repo_id") == target.id
        and baseline.get("repo_path") == target.display_path
    )
    if (
        not scope_matches
        or task.status == "todo"
        or baseline.get("state_version") != TASK_STATE_SCHEMA_VERSION
        or not isinstance(started_at, str)
        or not _valid_event_stamp(started_at)
    ):
        raise RepoctlError(
            "task repository scope does not match its immutable start transition; create a follow-up with a fresh baseline",
            code="transition_evidence_incomplete",
            path=task.rel_path,
        )
    return baseline


def record_task_verification_outcome(
    root: Path,
    task_id: str,
    *,
    status: str,
    evidence_ref: str,
    subject_refs: list[str],
    claim_ids: list[str] | None = None,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    if task.status not in LIVE:
        raise RepoctlError("done or canceled tasks are immutable; create a follow-up task", code="task_not_live", path=task.rel_path)
    artifacts = artifact_refs or []
    if artifacts and _repo_scoped_task(task):
        raise RepoctlError(
            "--artifact is only valid for root-only workspace tasks; use --subject for product repository files",
            code="workspace_verification_artifact_invalid",
            path=task.rel_path,
        )
    if artifacts:
        _require_task_start_scope_alignment(root, task, target=None, require_current_start=True)
        # The start baseline is monotonic for a task: blocked-task restart
        # preserves this exact state and no command refreshes it. Therefore a
        # successful add is already bound to the same generation finish later
        # validates, without inventing a second mutable lineage owner.
        state = add_workspace_artifact_verification_record(
            root,
            task_id=task.id,
            status=status,
            evidence_ref=evidence_ref,
            artifact_refs=artifacts,
            subject_refs=subject_refs,
            claim_ids=claim_ids or [],
        )
    else:
        target = _target_for_task(root, task)
        _require_task_start_scope_alignment(root, task, target=target, require_current_start=True)
        state = add_discovery_verification_record(
            root,
            task_id=task.id,
            target=target,
            status=status,
            evidence_ref=evidence_ref,
            subject_refs=subject_refs,
            claim_ids=claim_ids or [],
        )
    return {
        "task": task,
        "state": state,
        "state_writes": [(outcome_state_path(root, task.id), serialize_outcome_state(state))],
    }


def task_decomposition_evidence(root: Path, task_id: str) -> dict[str, int]:
    """Return structural task-growth facts without assigning task meaning."""

    task = resolve_task(root, task_id)
    state = load_outcome_state(root, task.id)
    if state is None:
        return {
            "chosen_subject_count": 0,
            "discovery_episode_count": 0,
            "prior_discovery_episode_count": 0,
            "structured_verification_record_count": 0,
        }
    active = state.get("active_episode")
    active_has_evidence = bool(
        isinstance(active, dict)
        and (
            active.get("citations")
            or active.get("reviewed")
            or active.get("excluded")
        )
    )
    prior_count = len(state.get("prior_episodes") or [])
    return {
        "chosen_subject_count": len(state.get("active_chosen") or []),
        "discovery_episode_count": prior_count + int(active_has_evidence),
        "prior_discovery_episode_count": prior_count,
        "structured_verification_record_count": len(state.get("verification_records") or []),
    }


def discovery_scope_delta(
    task: Task,
    target: RepoTarget,
    changes: list[ChangedEntry],
    *,
    observed_committed_changes: list[ChangedEntry] | None = None,
) -> dict[str, list[str]]:
    chosen, _invalid = _task_chosen_path_projection(task, target=target)
    actual = set(_entry_mutation_paths(changes))
    observed_committed = set(_entry_mutation_paths(observed_committed_changes or []))
    return {
        "actual_paths": sorted(actual),
        "observed_committed_paths": sorted(observed_committed),
        "chosen_paths": sorted(chosen),
        "unchosen_actual_paths": sorted(actual - chosen),
        "unused_chosen_paths": sorted(chosen - actual - observed_committed),
    }


def _task_chosen_path_projection(
    task: Task,
    *,
    target: RepoTarget | None,
) -> tuple[set[str], list[str]]:
    """Return canonical Task Chosen paths and every explicit invalid value."""

    chosen: set[str] = set()
    invalid: list[str] = []
    target_prefix = f"{target.display_path.rstrip('/')}/" if target is not None else ""
    for value in task_discovery_values(task).get("Chosen files", []):
        raw = _strip_ticks(value).strip()
        path = PurePosixPath(raw)
        if (
            not raw
            or "\\" in raw
            or path.is_absolute()
            or not path.parts
            or str(path) != raw
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            invalid.append(raw)
            continue
        if target is not None:
            if raw == target.display_path or (raw.startswith("repos/") and not raw.startswith(target_prefix)):
                invalid.append(raw)
                continue
            if raw.startswith(target_prefix):
                raw = raw[len(target_prefix) :]
        normalized = normalize_repo_path(raw)
        if not normalized:
            invalid.append(value)
            continue
        chosen.add(normalized)
    return chosen, sorted(set(invalid))


def task_discovery_outcome_alignment(
    root: Path,
    task: Task,
    *,
    target: RepoTarget | None,
) -> dict[str, Any]:
    """Compare the human Chosen projection with its machine-owned outcome."""

    state = load_outcome_state(root, task.id)
    if state is None:
        return {
            "status": "not_recorded",
            "reason_codes": [],
            "task_chosen_paths": [],
            "outcome_chosen_paths": [],
            "task_only_paths": [],
            "outcome_only_paths": [],
            "invalid_task_chosen_values": [],
            "invalid_outcome_subject_ids": [],
        }

    expected_repository = target.to_dict() if target is not None else None
    reason_codes: list[str] = []
    if state.get("repository") != expected_repository:
        reason_codes.append("discovery_outcome_repository_mismatch")

    task_paths, invalid_task_values = _task_chosen_path_projection(task, target=target)

    outcome_paths: set[str] = set()
    invalid_subject_ids: list[str] = []
    for subject in state.get("active_chosen", []):
        identity = subject.get("identity") if isinstance(subject, dict) else None
        raw_path = identity.get("path") if isinstance(identity, dict) else None
        normalized = normalize_repo_path(raw_path) if isinstance(raw_path, str) else ""
        if not isinstance(subject, dict) or subject.get("kind") != "file" or not normalized:
            invalid_subject_ids.append(str(subject.get("subject_id") or "") if isinstance(subject, dict) else "")
            continue
        outcome_paths.add(normalized)

    task_only = sorted(task_paths - outcome_paths)
    outcome_only = sorted(outcome_paths - task_paths)
    if invalid_task_values:
        reason_codes.append("discovery_task_chosen_invalid")
    if invalid_subject_ids:
        reason_codes.append("discovery_outcome_chosen_invalid")
    if task_only or outcome_only:
        reason_codes.append("discovery_outcome_chosen_mismatch")
    return {
        "status": "mismatch" if reason_codes else "aligned",
        "reason_codes": reason_codes,
        "task_chosen_paths": sorted(task_paths),
        "outcome_chosen_paths": sorted(outcome_paths),
        "task_only_paths": task_only,
        "outcome_only_paths": outcome_only,
        "invalid_task_chosen_values": invalid_task_values,
        "invalid_outcome_subject_ids": sorted(set(invalid_subject_ids)),
    }


def task_discovery_outcome_alignment_problem(
    root: Path,
    task: Task,
    *,
    target: RepoTarget | None,
) -> Problem | None:
    alignment = task_discovery_outcome_alignment(root, task, target=target)
    reason_codes = list(alignment["reason_codes"])
    if not reason_codes:
        return None
    code = reason_codes[0]
    if code == "discovery_outcome_repository_mismatch":
        message = "task Discovery outcome repository identity does not match the task's current selected repository"
    elif code == "discovery_task_chosen_invalid":
        message = (
            "task Discovery contains explicit Chosen values that are not canonical workspace-relative paths "
            f"(invalid={len(alignment['invalid_task_chosen_values'])})"
        )
    elif code == "discovery_outcome_chosen_invalid":
        message = "task Discovery outcome contains a non-file or invalid active Chosen subject"
    else:
        message = (
            "task Discovery Chosen projection does not match machine outcome active_chosen "
            f"(task_only={len(alignment['task_only_paths'])}, outcome_only={len(alignment['outcome_only_paths'])}); "
            "reconcile scope through repoctl task discovery add before completion"
        )
    return Problem("error", code, message, task.rel_path)


def _dirty_entry(dirty: list[str], *, copy: dict[str, Any], baseline_ref: str) -> str:
    return (
        f"- {utc_stamp()}: {copy['task_started_dirty']} "
        f"(`dirty_count={len(dirty)}`; machine baseline: `{baseline_ref}`)"
    )


def _git_unavailable_entry(reason: str, *, copy: dict[str, Any]) -> str:
    return f"- {utc_stamp()}: {copy['task_started_git_unavailable'].format(reason=reason)}"


def _repo_head_entry(head: str, *, copy: dict[str, Any]) -> str:
    return f"- {copy['repo_head_at_start']}: `{head}`"


def _state_dir(root: Path) -> Path:
    return root / "docs/tasks/.repoctl-state"


def _baseline_path(root: Path, task_id: str) -> Path:
    return _state_dir(root) / f"{task_id}.json"


def _completion_receipt_path(root: Path, task_id: str) -> Path:
    return _state_dir(root) / "completions" / f"{task_id}.json"


def archive_locator_path(root: Path, task_id: str) -> Path:
    if not isinstance(task_id, str) or ID_RE.fullmatch(task_id) is None:
        raise ValueError("invalid archive locator identity")
    return _state_dir(root) / "archive" / f"{task_id}.json"


def archive_locator_text(task_id: str, task_path: str) -> str:
    if (
        not isinstance(task_id, str)
        or not isinstance(task_path, str)
        or ID_RE.fullmatch(task_id) is None
        or not task_path.startswith("docs/archive/tasks/")
        or not _valid_receipt_task_path(task_path, task_id=task_id)
    ):
        raise ValueError("invalid archive locator identity")
    return json.dumps(
        {
            "schema": "repoctl.task.archive",
            "schema_version": ARCHIVE_LOCATOR_SCHEMA_VERSION,
            "task_id": task_id,
            "task_path": task_path,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def parse_archive_locator(text: str, *, task_id: str) -> str:
    locator = json.loads(text)
    task_path = str(locator.get("task_path") or "") if isinstance(locator, dict) else ""
    if (
        not isinstance(locator, dict)
        or set(locator) != {"schema", "schema_version", "task_id", "task_path"}
        or locator.get("schema") != "repoctl.task.archive"
        or type(locator.get("schema_version")) is not int
        or locator["schema_version"] != ARCHIVE_LOCATOR_SCHEMA_VERSION
        or locator.get("task_id") != task_id
        or not task_path.startswith("docs/archive/tasks/")
        or not _valid_receipt_task_path(task_path, task_id=task_id)
    ):
        raise ValueError("invalid archive locator identity")
    return task_path


def _contained_regular_file(root: Path, path: Path, directory: Path) -> bool:
    """Accept a file only when every workspace-local component is non-symlinked."""

    try:
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        root_resolved = root.resolve(strict=True)
        directory_resolved = directory.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
        resolved.relative_to(directory_resolved)
    except (OSError, ValueError, RuntimeError):
        return False
    return path.is_file()


def validate_workspace_write_path(root: Path, path: Path, *, boundary: Path) -> None:
    """Reject writes whose existing parent chain escapes or traverses a symlink."""

    try:
        relative = path.relative_to(root)
        boundary.relative_to(root)
        path.relative_to(boundary)
        if path.is_symlink():
            raise ValueError
        root_resolved = root.resolve(strict=True)
        boundary_resolved = boundary.resolve(strict=False)
        boundary_resolved.relative_to(root_resolved)
        current = root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError
            if current.exists():
                current.resolve(strict=True).relative_to(root_resolved)
        parent_resolved = path.parent.resolve(strict=False)
        parent_resolved.relative_to(root_resolved)
        parent_resolved.relative_to(boundary_resolved)
    except (OSError, ValueError, RuntimeError) as exc:
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        raise RepoctlError(
            "workspace state write path escapes its canonical directory or crosses a symlink",
            code="unsafe_workspace_write_path",
            path=rel,
        ) from exc


def _task_archive_locator(root: Path, task_id: str) -> Path | None:
    """Resolve one archived task through fixed per-task machine state."""

    if ID_RE.fullmatch(task_id) is None:
        return None
    task_path = ""
    locator_path = archive_locator_path(root, task_id)
    if locator_path.exists() or locator_path.is_symlink():
        if not _contained_regular_file(root, locator_path, _state_dir(root) / "archive"):
            return None
        try:
            task_path = parse_archive_locator(locator_path.read_text(encoding="utf-8"), task_id=task_id)
        except (OSError, UnicodeDecodeError, ValueError):
            return None
    else:
        receipt_path = _completion_receipt_path(root, task_id)
        if not _contained_regular_file(root, receipt_path, _state_dir(root) / "completions"):
            return None
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(receipt, dict) or receipt.get("task_id") != task_id:
            return None
        try:
            _completion_receipt_repo_id(receipt_path, root, receipt)
            if (
                receipt.get("status") != "done"
                or not isinstance(receipt.get("content_sha256"), str)
                or not _valid_sha256(str(receipt.get("content_sha256") or ""))
                or not isinstance(receipt.get("changed_entries"), list)
                or not isinstance(receipt.get("repo_evidence"), dict)
                or not isinstance(receipt.get("verification"), dict)
            ):
                return None
        except RepoctlError:
            return None
        task_path = completion_receipt_task_path(receipt)
    if not _valid_receipt_task_path(task_path, task_id=task_id) or not task_path.startswith("docs/archive/tasks/"):
        return None
    path = root / task_path
    if not _contained_regular_file(root, path, root / "docs/archive/tasks"):
        return None
    try:
        task = load_task(path, root, archived=True)
    except (OSError, UnicodeError, RepoctlError):
        return None
    if task.id != task_id or task.status not in NON_LIVE:
        return None
    return path


def archive_locator_writes(root: Path, moves: list[tuple[Path, Path]], tasks: list[Task]) -> list[tuple[Path, str]]:
    """Bind each archive move to one immutable, fixed-path lookup record."""

    tasks_by_path = {task.path: task for task in tasks}
    writes: list[tuple[Path, str]] = []
    for source, target in moves:
        validate_workspace_write_path(root, target, boundary=root / "docs/archive/tasks")
        if target.exists() or target.is_symlink():
            raise RepoctlError(
                "archive task target already exists and will not be overwritten",
                code="archive_task_conflict",
                path=target.relative_to(root).as_posix(),
            )
        task = tasks_by_path.get(source)
        if task is None:
            raise RepoctlError(
                "archive move has no source task identity",
                code="archive_locator_source_missing",
                path=source.relative_to(root).as_posix(),
            )
        task_path = target.relative_to(root).as_posix()
        locator_path = archive_locator_path(root, task.id)
        validate_workspace_write_path(root, locator_path, boundary=_state_dir(root) / "archive")
        locator_text = archive_locator_text(task.id, task_path)
        if locator_path.exists() or locator_path.is_symlink():
            try:
                existing = locator_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise RepoctlError(
                    "archive locator is unreadable",
                    code="archive_locator_conflict",
                    path=locator_path.relative_to(root).as_posix(),
                ) from exc
            if locator_path.is_symlink() or existing != locator_text:
                raise RepoctlError(
                    "archive locator conflicts with the requested archive move",
                    code="archive_locator_conflict",
                    path=locator_path.relative_to(root).as_posix(),
                )
        writes.append((locator_path, locator_text))
    return writes


def _completion_receipt_path_problem(root: Path, path: Path) -> Problem | None:
    rel = path.relative_to(root).as_posix()
    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return Problem("error", "invalid_completion_receipt", f"task completion receipt path is unresolvable: {rel}", rel)
    if root_resolved not in (path_resolved, *path_resolved.parents):
        return Problem("error", "invalid_completion_receipt", f"task completion receipt escapes workspace: {rel}", rel)
    return None


def _resume_binding_path(root: Path, task_id: str) -> Path:
    return _state_dir(root) / "resume" / f"{task_id}.json"


def _entry_to_dict(entry: ChangedEntry) -> dict[str, str]:
    change, path, old_path = entry
    data = {"change": change, "path": path}
    if old_path:
        data["old_path"] = old_path
    return data


def _entry_mutation_paths(entries: list[ChangedEntry]) -> list[str]:
    return sorted({path for entry in entries for path, _role, _change in _entry_transition_ports(entry)})


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _valid_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _valid_utc_stamp(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value))


def _valid_legacy_completion_stamp(value: str) -> bool:
    if _valid_utc_stamp(value):
        return True
    if not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == UTC


def _utc_event_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _valid_event_stamp(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _normalized_task_section_body(task: Task, section_name: str) -> str | None:
    try:
        section = find_section(task.body, section_name)
    except RepoctlError:
        return None
    body = task.body[section.body_start : section.end].strip()
    return body + "\n" if body else ""


def task_handoff_body(task: Task) -> str | None:
    return _normalized_task_section_body(task, "Handoff")


def task_handoff_is_generated_template(task: Task) -> bool:
    return HANDOFF_GENERATED_MARKER in (task_handoff_body(task) or "")


def _task_contract_digest(task: Task) -> str:
    text = task.path.read_text(encoding="utf-8")
    for section_name in ("Discovery", "Execution Log", "Verification", "Handoff"):
        if has_section(text, section_name):
            text = replace_section(text, section_name, f"<!-- repoctl resume component: {section_name} -->\n")
    return digest_data({"task_contract": text})


def _repo_state_projection(state: RepoGitState) -> dict[str, Any]:
    return {
        "available": state.available,
        "reason": state.reason,
        "repo_id": state.repo_id,
        "repo_path": state.repo_path,
    }


def _resume_observation_unavailable(task: Task, state: RepoGitState) -> RepoctlError:
    return RepoctlError(
        f"task resume repository observation is unavailable: {state.reason or 'unknown repository state'}",
        code="task_resume_observation_unavailable",
        path=state.repo_path or task.rel_path,
    )


def _repository_resume_projection(
    root: Path,
    task: Task,
    *,
    layout: RepoLayout | None = None,
) -> dict[str, Any]:
    layout = layout or repo_layout(root)
    target = _target_for_task(root, task, layout=layout)
    if target is not None:
        delta = repo_changes_since_task_start(root, task.id, layout=layout)
        head, head_state = repo_git_head(root, target)
        records, fingerprint_state = repo_change_fingerprint_records(
            root,
            list(delta.get("changes") or []),
            target,
        )
        if not head_state.available:
            raise _resume_observation_unavailable(task, head_state)
        if not fingerprint_state.available:
            raise _resume_observation_unavailable(task, fingerprint_state)
        child_changes = [
            item
            for item in delta.get("child_attributed_changes", [])
            if isinstance(item, dict)
        ]
        return {
            "scope": "selected_repository",
            "repository": {"id": target.id, "path": target.display_path},
            "head": head,
            "head_state": _repo_state_projection(head_state),
            "baseline_available": bool(delta.get("baseline_available")),
            "change_records": records,
            "observed_committed_changes": [
                _entry_to_dict(entry)
                for entry in delta.get("observed_committed_changes", [])
            ],
            "baseline_conflicts": sorted(str(value) for value in delta.get("baseline_conflicts", []) if str(value)),
            "child_attributed_changes": sorted(
                child_changes,
                key=lambda item: json.dumps(item, ensure_ascii=True, sort_keys=True),
            ),
        }

    surfaces = _root_task_product_surfaces(root, layout=layout)
    if not surfaces:
        return {"scope": "not_applicable", "repositories": []}
    repositories: list[dict[str, Any]] = []
    for surface in surfaces:
        entries, entries_state = repo_changed_entries(root, surface)
        records, fingerprint_state = repo_change_fingerprint_records(root, entries, surface)
        head, head_state = repo_git_head(root, surface)
        for state in (entries_state, fingerprint_state, head_state):
            if not state.available:
                raise _resume_observation_unavailable(task, state)
        repositories.append(
            {
                "id": surface.id,
                "path": surface.display_path,
                "head": head,
                "head_state": _repo_state_projection(head_state),
                "change_records": records,
            }
        )
    repositories.sort(key=lambda item: (str(item["path"]), str(item["id"])))
    return {"scope": "workspace_repositories", "repositories": repositories}


def _child_completion_receipt_projection(root: Path, child: Task) -> dict[str, Any]:
    path = _completion_receipt_path(root, child.id)
    rel = path.relative_to(root).as_posix()
    present = path.exists() or path.is_symlink()
    if not present:
        return {
            "path": rel,
            "present": False,
            "content_sha256": "",
            "resolution": {"status": "missing_receipt"},
        }
    if path_problem := _completion_receipt_path_problem(root, path):
        return {
            "path": rel,
            "present": True,
            "content_sha256": "",
            "resolution": {
                "status": "invalid_receipt_path",
                "code": path_problem.code,
            },
        }
    try:
        receipt_bytes = path.read_bytes()
    except OSError:
        return {
            "path": rel,
            "present": True,
            "content_sha256": "",
            "resolution": {"status": "unreadable_receipt"},
        }
    receipt_sha256 = _sha256_bytes(receipt_bytes)
    try:
        data = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "path": rel,
            "present": True,
            "content_sha256": receipt_sha256,
            "resolution": {"status": "unreadable_receipt"},
        }
    declared_path = str(data.get("task_path_at_completion") or "") if isinstance(data, dict) else ""
    resolution = _resolve_receipt_artifact(
        root,
        child.id,
        declared_path,
        receipt_path=rel,
        receipt_sha256=receipt_sha256,
    )
    return {
        "path": rel,
        "present": True,
        "content_sha256": receipt_sha256,
        "resolution": resolution.input_identity(),
    }


def _direct_child_lifecycle_projection(root: Path, task: Task) -> list[dict[str, Any]]:
    children = [
        candidate
        for candidate in load_tasks(root, include_archived=False)
        if candidate.parent == task.id
    ]
    return [
        {
            "id": child.id,
            "status": child.status,
            "path": child.rel_path,
            "completion_receipt": _child_completion_receipt_projection(root, child),
        }
        for child in sorted(children, key=lambda candidate: (candidate.id, candidate.rel_path))
    ]


def task_resume_input_digests(
    root: Path,
    task: Task,
    *,
    layout: RepoLayout | None = None,
) -> dict[str, str]:
    outcome_state = load_outcome_state(root, task.id)
    section_digests = {
        "discovery": digest_data(
            {
                "section": _normalized_task_section_body(task, "Discovery"),
                "outcome_state_digest": str((outcome_state or {}).get("state_digest") or ""),
            }
        ),
        "execution_log": digest_data({"section": _normalized_task_section_body(task, "Execution Log")}),
        "verification": digest_data({"section": _normalized_task_section_body(task, "Verification")}),
    }
    return {
        "task_contract": _task_contract_digest(task),
        **section_digests,
        "repository": digest_data(_repository_resume_projection(root, task, layout=layout)),
        "direct_children": digest_data(_direct_child_lifecycle_projection(root, task)),
    }


def _validate_resume_binding_data(path: Path, task_id: str, data: dict[str, Any]) -> None:
    rel = path.as_posix()
    expected_keys = {
        "schema",
        "schema_version",
        "task_id",
        "handoff_digest",
        "input_digests",
        "context_pack",
    }
    if set(data) != expected_keys:
        raise RepoctlError("task resume binding has invalid fields", code="task_resume_binding_invalid", path=rel)
    schema_version = data.get("schema_version")
    if (
        data.get("schema") != "repoctl.task.resume_binding"
        or type(schema_version) is not int
        or schema_version not in LEGACY_RESUME_BINDING_SCHEMA_VERSIONS | {RESUME_BINDING_SCHEMA_VERSION}
    ):
        raise RepoctlError("task resume binding has invalid schema", code="task_resume_binding_invalid", path=rel)
    if str(data.get("task_id") or "") != task_id:
        raise RepoctlError("task resume binding task id does not match", code="task_resume_binding_invalid", path=rel)
    if not _valid_sha256(str(data.get("handoff_digest") or "")):
        raise RepoctlError("task resume binding has invalid Handoff digest", code="task_resume_binding_invalid", path=rel)
    input_digests = data.get("input_digests")
    expected_input_keys = {"task_contract", "discovery", "execution_log", "verification", "repository"}
    if schema_version != 1:
        expected_input_keys.add("direct_children")
    if not isinstance(input_digests, dict) or set(input_digests) != expected_input_keys:
        raise RepoctlError("task resume binding has invalid input digests", code="task_resume_binding_invalid", path=rel)
    if any(not _valid_sha256(str(value or "")) for value in input_digests.values()):
        raise RepoctlError("task resume binding has invalid input digest", code="task_resume_binding_invalid", path=rel)
    context_pack = data.get("context_pack")
    if context_pack is None:
        return
    if not isinstance(context_pack, dict) or set(context_pack) != {"path", "artifact_sha256", "input_digest"}:
        raise RepoctlError("task resume binding has invalid Context Pack binding", code="task_resume_binding_invalid", path=rel)
    pack_path = str(context_pack.get("path") or "")
    candidate = Path(pack_path)
    if (
        not pack_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in pack_path
        or normalize_repo_path(pack_path) != pack_path
    ):
        raise RepoctlError("task resume binding has invalid Context Pack path", code="task_resume_binding_invalid", path=rel)
    for key in ("artifact_sha256", "input_digest"):
        if not _valid_sha256(str(context_pack.get(key) or "")):
            raise RepoctlError("task resume binding has invalid Context Pack digest", code="task_resume_binding_invalid", path=rel)


def load_task_resume_binding(root: Path, task_id: str) -> dict[str, Any] | None:
    normalized_task_id = normalize_task_id(task_id)
    path = _resume_binding_path(root, normalized_task_id)
    if not path.exists() and not path.is_symlink():
        return None
    if not _contained_regular_file(root, path, _state_dir(root) / "resume"):
        raise RepoctlError(
            "task resume binding must be a contained non-symlinked regular file",
            code="task_resume_binding_invalid",
            path=path.relative_to(root).as_posix(),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoctlError("task resume binding is unreadable", code="task_resume_binding_invalid", path=path.relative_to(root).as_posix()) from exc
    if not isinstance(data, dict):
        raise RepoctlError("task resume binding must be an object", code="task_resume_binding_invalid", path=path.relative_to(root).as_posix())
    _validate_resume_binding_data(path.relative_to(root), normalized_task_id, data)
    return data


def bind_task_handoff(
    root: Path,
    task_id: str,
    *,
    context_pack: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not (root / LOCK_REL).is_dir():
        raise RepoctlError(f"task Handoff binding requires repoctl lock: {LOCK_REL}", code="task_lock_required", path=LOCK_REL.as_posix())
    task = resolve_live_task(root, task_id)
    if task.status not in LIVE:
        raise RepoctlError("only a live task Handoff can be bound", code="task_not_live", path=task.rel_path)
    if task_handoff_is_generated_template(task):
        raise RepoctlError(
            "replace the repoctl-generated Handoff with reviewed task-specific restart instructions before binding",
            code="task_handoff_generated_template",
            path=task.rel_path,
        )
    handoff_problems = _live_handoff_problems(task, root)
    if handoff_problems:
        problem = handoff_problems[0]
        raise RepoctlError(problem.message, code=problem.code, path=problem.path)
    handoff = task_handoff_body(task)
    if handoff is None:
        raise RepoctlError("live task must contain a Handoff section", code="missing_handoff", path=task.rel_path)
    if context_pack is not None:
        candidate_binding = {
            "path": str(context_pack.get("path") or ""),
            "artifact_sha256": str(context_pack.get("artifact_sha256") or ""),
            "input_digest": str(context_pack.get("input_digest") or ""),
        }
    else:
        candidate_binding = None
    data: dict[str, Any] = {
        "schema": "repoctl.task.resume_binding",
        "schema_version": RESUME_BINDING_SCHEMA_VERSION,
        "task_id": task.id,
        "handoff_digest": digest_data({"handoff": handoff}),
        "input_digests": task_resume_input_digests(root, task),
        "context_pack": candidate_binding,
    }
    path = _resume_binding_path(root, task.id)
    _validate_resume_binding_data(path.relative_to(root), task.id, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "task_id": task.id,
        "receipt_path": path.relative_to(root).as_posix(),
        "current_revision": digest_data(data["input_digests"]),
        "context_pack": candidate_binding,
    }


def task_handoff_observation(
    root: Path,
    task: Task,
    *,
    binding: dict[str, Any] | None,
    layout: RepoLayout | None = None,
    repository_observation_available: bool = True,
) -> dict[str, Any]:
    handoff = task_handoff_body(task)
    if task.archived or task.status in NON_LIVE:
        return {
            "status": TaskHandoffStatus.HISTORICAL.value,
            "active": False,
            "body": handoff or "",
            "reason_codes": [],
            "receipt_ref": "",
            "bound_revision": "",
            "current_revision": "",
            "changed_inputs": [],
        }
    handoff_problems = _live_handoff_problems(task, root)
    if handoff_problems:
        return {
            "status": TaskHandoffStatus.INACTIVE.value,
            "active": False,
            "body": handoff or "",
            "reason_codes": [problem.code for problem in handoff_problems],
            "receipt_ref": "",
            "bound_revision": "",
            "current_revision": "",
            "changed_inputs": ["handoff"],
        }
    if binding is None:
        current_revision = ""
        if repository_observation_available:
            try:
                current_digests = task_resume_input_digests(root, task, layout=layout)
                current_revision = digest_data(current_digests)
            except RepoctlError:
                pass
        return {
            "status": TaskHandoffStatus.INACTIVE.value,
            "active": False,
            "body": handoff,
            "reason_codes": ["handoff_unbound"],
            "receipt_ref": "",
            "bound_revision": "",
            "current_revision": current_revision,
            "changed_inputs": [],
        }
    if binding["schema_version"] != RESUME_BINDING_SCHEMA_VERSION:
        return {
            "status": TaskHandoffStatus.INACTIVE.value,
            "active": False,
            "body": handoff,
            "reason_codes": ["handoff_binding_legacy"],
            "receipt_ref": _resume_binding_path(root, task.id).relative_to(root).as_posix(),
            "bound_revision": digest_data(binding["input_digests"]),
            "current_revision": "",
            "changed_inputs": ["binding"],
        }
    bound_digests = binding["input_digests"]
    if not repository_observation_available:
        return {
            "status": TaskHandoffStatus.INACTIVE.value,
            "active": False,
            "body": handoff,
            "reason_codes": ["resume_observation_unavailable"],
            "receipt_ref": _resume_binding_path(root, task.id).relative_to(root).as_posix(),
            "bound_revision": digest_data(bound_digests),
            "current_revision": "",
            "changed_inputs": [],
        }
    try:
        current_digests = task_resume_input_digests(root, task, layout=layout)
    except RepoctlError:
        return {
            "status": TaskHandoffStatus.INACTIVE.value,
            "active": False,
            "body": handoff,
            "reason_codes": ["resume_observation_unavailable"],
            "receipt_ref": _resume_binding_path(root, task.id).relative_to(root).as_posix(),
            "bound_revision": digest_data(bound_digests),
            "current_revision": "",
            "changed_inputs": [],
        }
    changed_inputs = [
        key
        for key in ("task_contract", "discovery", "execution_log", "verification", "repository", "direct_children")
        if str(bound_digests.get(key) or "") != str(current_digests.get(key) or "")
    ]
    reason_codes = [f"{key}_changed" for key in changed_inputs]
    if str(binding.get("handoff_digest") or "") != digest_data({"handoff": handoff}):
        changed_inputs.insert(0, "handoff")
        reason_codes.insert(0, "handoff_changed")
    status = TaskHandoffStatus.INACTIVE if reason_codes else TaskHandoffStatus.CURRENT
    return {
        "status": status.value,
        "active": status == TaskHandoffStatus.CURRENT,
        "body": handoff,
        "reason_codes": reason_codes,
        "receipt_ref": _resume_binding_path(root, task.id).relative_to(root).as_posix(),
        "bound_revision": digest_data(bound_digests),
        "current_revision": digest_data(current_digests),
        "changed_inputs": changed_inputs,
    }


def _valid_receipt_task_path(value: str, *, task_id: str = "", allow_empty: bool = False) -> bool:
    if not value:
        return allow_empty
    normalized = value.strip().replace("\\", "/")
    if normalized != value or normalized.startswith("/") or "/../" in f"/{normalized}/":
        return False
    prefix = "docs/archive/tasks/" if normalized.startswith("docs/archive/tasks/") else "docs/tasks/" if normalized.startswith("docs/tasks/") else ""
    if not prefix:
        return False
    filename = normalized.removeprefix(prefix)
    if "/" in filename or not filename.endswith(".md"):
        return False
    match = TASK_RE.match(filename)
    return bool(match and (not task_id or match.group(1) == task_id))


def completion_receipt_task_path(receipt: dict[str, Any]) -> str:
    task_id = str(receipt.get("task_id") or "")
    value = str(receipt.get("task_path_at_completion") or "")
    return value if ID_RE.match(task_id) and _valid_receipt_task_path(value, task_id=task_id) else ""


def _completion_receipt_artifact_candidates(
    root: Path,
    task_id: str,
    task_path: str,
    *,
    audit_history: bool = False,
) -> list[Path]:
    if not ID_RE.match(task_id) or not _valid_receipt_task_path(task_path, task_id=task_id):
        return []
    declared = root / task_path
    archived = _task_archive_locator(root, task_id)
    candidates = [declared, *([archived] if archived is not None and archived != declared else [])]
    if audit_history:
        candidates.extend(sorted((root / "docs/tasks").glob(f"{task_id}--*.md")))
        candidates.extend(sorted((root / "docs/archive/tasks").glob(f"{task_id}--*.md")))
    return list(dict.fromkeys(candidates))


def _resolve_receipt_artifact(
    root: Path,
    task_id: str,
    value: str,
    *,
    receipt_path: str = "",
    receipt_sha256: str = "",
    audit_history: bool = False,
) -> CompletionReceiptArtifactResolution:
    candidates = _completion_receipt_artifact_candidates(
        root,
        task_id,
        value,
        audit_history=audit_history,
    )
    candidate_paths = tuple(candidate.relative_to(root).as_posix() for candidate in candidates)
    if not candidates:
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.INVALID_IDENTITY,
            (),
            (),
            receipt_sha256,
        )
    existing = [candidate for candidate in candidates if candidate.is_file()]
    existing_paths = tuple(candidate.relative_to(root).as_posix() for candidate in existing)
    if not existing:
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.MISSING,
            candidate_paths,
            (),
            receipt_sha256,
        )
    if len(existing) != 1:
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.AMBIGUOUS,
            candidate_paths,
            existing_paths,
            receipt_sha256,
        )
    path = existing[0]
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError, RuntimeError):
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.UNRESOLVABLE,
            candidate_paths,
            existing_paths,
            receipt_sha256,
        )
    if root_resolved not in (resolved, *resolved.parents):
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.OUTSIDE_WORKSPACE,
            candidate_paths,
            existing_paths,
            receipt_sha256,
        )
    try:
        artifact_bytes = path.read_bytes()
        artifact_text = artifact_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return CompletionReceiptArtifactResolution(
            receipt_path,
            value,
            CompletionReceiptArtifactResolutionStatus.UNREADABLE,
            candidate_paths,
            existing_paths,
            receipt_sha256,
        )
    return CompletionReceiptArtifactResolution(
        receipt_path,
        value,
        CompletionReceiptArtifactResolutionStatus.RESOLVED,
        candidate_paths,
        existing_paths,
        receipt_sha256,
        resolved_path=path.relative_to(root).as_posix(),
        content_sha256=_sha256_bytes(artifact_bytes),
        artifact_bytes=artifact_bytes,
        artifact_text=artifact_text,
    )


def _completion_receipt_artifact_resolution_error(
    resolution: CompletionReceiptArtifactResolution,
) -> RepoctlError:
    messages = {
        CompletionReceiptArtifactResolutionStatus.INVALID_IDENTITY: "does not match task_id",
        CompletionReceiptArtifactResolutionStatus.MISSING: "is missing",
        CompletionReceiptArtifactResolutionStatus.AMBIGUOUS: "is ambiguous",
        CompletionReceiptArtifactResolutionStatus.UNRESOLVABLE: "cannot be resolved",
        CompletionReceiptArtifactResolutionStatus.OUTSIDE_WORKSPACE: "escapes workspace",
        CompletionReceiptArtifactResolutionStatus.UNREADABLE: "is unreadable",
    }
    return RepoctlError(
        f"task completion receipt artifact {messages[resolution.status]}: {resolution.declared_path}",
        code="invalid_completion_receipt",
        path=resolution.declared_path,
    )


def _completion_receipt_schema_version(data: dict[str, Any], *, rel: str) -> int:
    if data.get("schema") != "repoctl.task.completion":
        raise RepoctlError(f"task completion receipt has invalid schema: {rel}", code="invalid_completion_receipt", path=rel)
    try:
        return decode_schema_version(
            data.get("schema_version"),
            supported=(
                LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION,
                TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION,
                COMPLETION_RECEIPT_SCHEMA_VERSION,
            ),
        )
    except ValueError as exc:
        raise RepoctlError(f"task completion receipt has invalid schema: {rel}", code="invalid_completion_receipt", path=rel) from exc


def _completion_receipt_repo_id(path: Path, root: Path, data: dict[str, Any]) -> str:
    rel = path.relative_to(root).as_posix()
    _completion_receipt_schema_version(data, rel=rel)
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id) or path.stem != task_id:
        raise RepoctlError(f"task completion receipt task_id does not match filename: {rel}", code="invalid_completion_receipt", path=rel)
    raw_repo_id = data.get("repo_id")
    if "repo_id" not in data or not isinstance(raw_repo_id, str):
        raise RepoctlError(f"task completion receipt has invalid repo_id: {rel}", code="invalid_completion_receipt", path=rel)
    repo_id = raw_repo_id
    if repo_id and not re.fullmatch(r"[a-z][a-z0-9_-]*", repo_id):
        raise RepoctlError(f"task completion receipt has invalid repo_id: {rel}", code="invalid_completion_receipt", path=rel)
    return repo_id


def _receipt_changed_entry(item: Any, *, rel: str, label: str = "changed entry") -> ChangedEntry:
    if not isinstance(item, dict):
        raise RepoctlError(f"task completion receipt has invalid {label}: {rel}", code="invalid_completion_receipt", path=rel)
    if set(item) not in ({"change", "path"}, {"change", "path", "old_path"}):
        raise RepoctlError(f"task completion receipt has invalid {label}: {rel}", code="invalid_completion_receipt", path=rel)
    if any(not isinstance(value, str) for value in item.values()):
        raise RepoctlError(f"task completion receipt has invalid {label}: {rel}", code="invalid_completion_receipt", path=rel)
    change = item["change"]
    path_value = item["path"]
    old_path = item.get("old_path", "")
    if (
        change not in {"added", "modified", "deleted", "renamed", "copied", "untracked"}
        or not path_value
        or normalize_repo_path(path_value) != path_value
    ):
        raise RepoctlError(f"task completion receipt has invalid {label}: {rel}", code="invalid_completion_receipt", path=rel)
    if old_path and normalize_repo_path(old_path) != old_path:
        raise RepoctlError(f"task completion receipt has invalid {label} old_path: {rel}", code="invalid_completion_receipt", path=rel)
    if (change in {"renamed", "copied"}) != bool(old_path):
        raise RepoctlError(f"task completion receipt has invalid {label} old_path: {rel}", code="invalid_completion_receipt", path=rel)
    return change, path_value, old_path


def _entry_transition_ports(entry: ChangedEntry) -> tuple[tuple[str, str, str], ...]:
    change, path, old_path = entry
    if change == "renamed":
        return ((old_path, "source", change), (path, "destination", change))
    if change == "copied":
        return ((path, "destination", change),)
    return ((path, "path", change),)


def _receipt_path_transition(item: Any, *, rel: str) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != {"path", "effect", "basis", "before", "after"}:
        raise RepoctlError(f"task completion receipt has invalid path transition: {rel}", code="invalid_completion_receipt", path=rel)
    path = item.get("path")
    effect = item.get("effect")
    raw_basis = item.get("basis")
    basis = raw_basis if isinstance(raw_basis, list) and all(isinstance(value, str) for value in raw_basis) else []
    before = normalize_stable_path_state(item.get("before"))
    after = normalize_stable_path_state(item.get("after"))
    if (
        not isinstance(path, str)
        or not isinstance(effect, str)
        or normalize_repo_path(path) != path
        or effect not in {"write", "remove"}
        or not basis
        or basis != sorted(set(basis))
        or not set(basis) <= {"observed_change", "baseline_ownership"}
        or before is None
        or after is None
        or (effect == "remove") != (after.get("kind") == "missing")
        or stable_path_state_digest(before) == stable_path_state_digest(after)
    ):
        raise RepoctlError(f"task completion receipt has invalid path transition: {rel}", code="invalid_completion_receipt", path=rel)
    return {"path": path, "effect": effect, "basis": basis, "before": before, "after": after}


def _receipt_baseline_ownership(
    repo_evidence: dict[str, Any],
    *,
    rel: str,
    schema_version: int,
) -> dict[str, dict[str, str]]:
    raw_ownership = repo_evidence.get("ownership")
    if schema_version == LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION and "ownership" not in repo_evidence:
        return {}
    try:
        return _canonical_baseline_ownership(
            raw_ownership,
            include_fingerprints=True,
            allowed_paths=None,
        )
    except ValueError as exc:
        raise RepoctlError(f"task completion receipt has invalid baseline ownership: {rel}", code="invalid_completion_receipt", path=rel) from exc


def _validate_receipt_path_transitions(
    *,
    rel: str,
    repo_evidence: dict[str, Any],
    entries: list[ChangedEntry],
    ownership: dict[str, dict[str, str]],
) -> None:
    raw_transitions = repo_evidence.get("path_transitions")
    if not isinstance(raw_transitions, list):
        raise RepoctlError(f"task completion receipt has invalid path transitions: {rel}", code="invalid_completion_receipt", path=rel)
    transitions = [_receipt_path_transition(item, rel=rel) for item in raw_transitions]
    transition_paths = [item["path"] for item in transitions]
    changed_paths = set(_entry_mutation_paths(entries))
    task_owned_paths = {
        path
        for path, decision in ownership.items()
        if decision["ownership"] == BaselineOwnership.TASK.value
    }
    if len(set(transition_paths)) != len(transition_paths) or not changed_paths <= set(transition_paths):
        raise RepoctlError(
            f"task completion receipt path transitions do not cover changed entries exactly: {rel}",
            code="invalid_completion_receipt",
            path=rel,
        )
    for transition in transitions:
        path = transition["path"]
        expected_basis = sorted(
            {
                *( ["observed_change"] if path in changed_paths else [] ),
                *( ["baseline_ownership"] if path in task_owned_paths else [] ),
            }
        )
        if not expected_basis or transition["basis"] != expected_basis:
            raise RepoctlError(
                f"task completion receipt path transition basis is invalid: {rel}",
                code="invalid_completion_receipt",
                path=rel,
            )


def _validate_receipt_fingerprint_manifest(
    *,
    rel: str,
    data: dict[str, Any],
    repo_evidence: dict[str, Any],
    entries: list[ChangedEntry],
) -> None:
    manifest = repo_evidence.get("fingerprint_manifest")
    if manifest in (None, {}):
        return
    if not isinstance(manifest, dict):
        raise RepoctlError(f"task completion receipt has invalid fingerprint manifest: {rel}", code="invalid_completion_receipt", path=rel)
    mode = str(repo_evidence.get("mode") or "")
    repo_id = str(data.get("repo_id") or "")
    repo_path = str(manifest.get("repo_path") or "")
    if (
        mode == "none"
        or str(manifest.get("mode") or "") != mode
        or str(manifest.get("repo_id") or "") != repo_id
        or not repo_path
        or normalize_repo_path(repo_path) != repo_path
        or not (repo_path == "repos" or repo_path.startswith("repos/"))
        or str(manifest.get("start_head") or "") != str(repo_evidence.get("start_head") or "")
        or str(manifest.get("observed_head") or "") != str(repo_evidence.get("observed_head") or "")
    ):
        raise RepoctlError(f"task completion receipt fingerprint manifest does not match repo evidence: {rel}", code="invalid_completion_receipt", path=rel)

    manifest_entries_raw = manifest.get("changed_entries")
    if not isinstance(manifest_entries_raw, list):
        raise RepoctlError(f"task completion receipt has invalid fingerprint manifest entries: {rel}", code="invalid_completion_receipt", path=rel)
    manifest_entries = [_receipt_changed_entry(item, rel=rel, label="fingerprint manifest entry") for item in manifest_entries_raw]
    if len(set(manifest_entries)) != len(manifest_entries) or sorted(manifest_entries) != sorted(entries):
        raise RepoctlError(f"task completion receipt fingerprint manifest entries do not match changed_entries: {rel}", code="invalid_completion_receipt", path=rel)

    raw_fingerprints = manifest.get("entry_fingerprints")
    if raw_fingerprints is not None:
        if not isinstance(raw_fingerprints, list):
            raise RepoctlError(f"task completion receipt has invalid entry fingerprints: {rel}", code="invalid_completion_receipt", path=rel)
        fingerprint_entries: list[ChangedEntry] = []
        for item in raw_fingerprints:
            if not isinstance(item, dict) or set(item) - {"change", "path", "old_path", "fingerprint_sha256"}:
                raise RepoctlError(f"task completion receipt has invalid entry fingerprint: {rel}", code="invalid_completion_receipt", path=rel)
            entry = _receipt_changed_entry(
                {key: item[key] for key in ("change", "path", "old_path") if key in item},
                rel=rel,
                label="entry fingerprint",
            )
            if not isinstance(item.get("fingerprint_sha256"), str) or not _valid_sha256(item["fingerprint_sha256"]):
                raise RepoctlError(f"task completion receipt has invalid entry fingerprint digest: {rel}", code="invalid_completion_receipt", path=rel)
            fingerprint_entries.append(entry)
        if len(set(fingerprint_entries)) != len(fingerprint_entries) or sorted(fingerprint_entries) != sorted(entries):
            raise RepoctlError(f"task completion receipt entry fingerprints do not cover changed_entries exactly: {rel}", code="invalid_completion_receipt", path=rel)

    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if _sha256_text(encoded) != str(repo_evidence.get("diff_fingerprint_sha256") or ""):
        raise RepoctlError(f"task completion receipt fingerprint manifest hash does not match repo evidence: {rel}", code="invalid_completion_receipt", path=rel)


def _completion_evidence_pair(
    repo_evidence: dict[str, Any],
    *,
    rel: str,
) -> tuple[_CompletionEvidenceMode, _CompletionEvidenceAttribution]:
    try:
        mode = _CompletionEvidenceMode(str(repo_evidence.get("mode") or ""))
    except ValueError as exc:
        raise RepoctlError(f"task completion receipt has invalid repo evidence mode: {rel}", code="invalid_completion_receipt", path=rel) from exc
    try:
        attribution = _CompletionEvidenceAttribution(str(repo_evidence.get("attribution") or ""))
    except ValueError as exc:
        raise RepoctlError(f"task completion receipt has invalid repo evidence attribution: {rel}", code="invalid_completion_receipt", path=rel) from exc
    if _COMPLETION_ATTRIBUTION_BY_MODE[mode] is not attribution:
        raise RepoctlError(f"task completion receipt has incoherent repo evidence: {rel}", code="invalid_completion_receipt", path=rel)
    return mode, attribution


def _validate_completion_receipt(
    path: Path,
    root: Path,
    data: dict[str, Any],
    *,
    receipt_text: str = "",
    artifact_resolution: CompletionReceiptArtifactResolution | None = None,
) -> CompletionReceiptArtifact:
    rel = path.relative_to(root).as_posix()
    task_id = data.get("task_id")
    _completion_receipt_repo_id(path, root, data)
    if data.get("status") != "done":
        raise RepoctlError(f"task completion receipt has invalid status: {rel}", code="invalid_completion_receipt", path=rel)
    schema_version = _completion_receipt_schema_version(data, rel=rel)
    completed_at = data.get("completed_at")
    if not isinstance(task_id, str):
        raise RepoctlError(f"task completion receipt has invalid identity: {rel}", code="invalid_completion_receipt", path=rel)
    if schema_version >= TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION:
        valid_completion_stamp = isinstance(completed_at, str) and _valid_utc_stamp(completed_at)
    else:
        valid_completion_stamp = "completed_at" not in data or (
            isinstance(completed_at, str) and _valid_legacy_completion_stamp(completed_at)
        )
    if not valid_completion_stamp:
        raise RepoctlError(f"task completion receipt has invalid completion timestamp: {rel}", code="invalid_completion_receipt", path=rel)
    task_path = data.get("task_path_at_completion")
    if not isinstance(task_path, str):
        raise RepoctlError(f"task completion receipt has invalid task path: {rel}", code="invalid_completion_receipt", path=rel)
    if not _valid_receipt_task_path(task_path, task_id=task_id):
        raise RepoctlError(f"task completion receipt has invalid task path: {rel}", code="invalid_completion_receipt", path=rel)
    content_sha256 = data.get("content_sha256")
    if not isinstance(content_sha256, str) or not _valid_sha256(content_sha256):
        raise RepoctlError(f"task completion receipt has invalid content hash: {rel}", code="invalid_completion_receipt", path=rel)
    verification = data.get("verification")
    if not isinstance(verification, dict):
        raise RepoctlError(f"task completion receipt has invalid verification: {rel}", code="invalid_completion_receipt", path=rel)
    if verification.get("source") not in {"external_file", "task_section"}:
        raise RepoctlError(f"task completion receipt has invalid verification source: {rel}", code="invalid_completion_receipt", path=rel)
    for key in ("source_sha256", "normalized_sha256", "stored_sha256"):
        if not isinstance(verification.get(key), str) or not _valid_sha256(verification[key]):
            raise RepoctlError(f"task completion receipt has invalid verification hash: {rel}", code="invalid_completion_receipt", path=rel)
    if not isinstance(verification.get("truncated"), bool):
        raise RepoctlError(f"task completion receipt has invalid verification truncation flag: {rel}", code="invalid_completion_receipt", path=rel)
    resolution = artifact_resolution or _resolve_receipt_artifact(root, task_id, task_path, receipt_path=rel)
    if resolution.status is not CompletionReceiptArtifactResolutionStatus.RESOLVED:
        raise _completion_receipt_artifact_resolution_error(resolution)
    artifact_bytes = resolution.artifact_bytes
    artifact_text = resolution.artifact_text
    if _sha256_bytes(artifact_bytes) != content_sha256:
        raise RepoctlError(f"task completion receipt hash does not match artifact: {rel}", code="invalid_completion_receipt", path=rel)
    repo_evidence = data.get("repo_evidence")
    if not isinstance(repo_evidence, dict):
        raise RepoctlError(f"task completion receipt has invalid repo evidence: {rel}", code="invalid_completion_receipt", path=rel)
    mode, _attribution = _completion_evidence_pair(repo_evidence, rel=rel)
    ownership = _receipt_baseline_ownership(
        repo_evidence,
        rel=rel,
        schema_version=schema_version,
    )
    fingerprint = str(repo_evidence.get("diff_fingerprint_sha256") or "")
    if mode is not _CompletionEvidenceMode.NONE and not _valid_sha256(fingerprint):
        raise RepoctlError(f"task completion receipt has invalid repo evidence fingerprint: {rel}", code="invalid_completion_receipt", path=rel)
    raw_entries = data.get("changed_entries")
    if not isinstance(raw_entries, list):
        raise RepoctlError(f"task completion receipt has invalid changed_entries: {rel}", code="invalid_completion_receipt", path=rel)
    entries = [_receipt_changed_entry(item, rel=rel) for item in raw_entries]
    if len(set(entries)) != len(entries):
        raise RepoctlError(f"task completion receipt has duplicate changed_entries: {rel}", code="invalid_completion_receipt", path=rel)
    _validate_receipt_fingerprint_manifest(rel=rel, data=data, repo_evidence=repo_evidence, entries=entries)
    if schema_version >= TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION:
        started_at = data.get("started_at")
        completed_event_at = data.get("completed_event_at")
        if not isinstance(started_at, str) or not _valid_event_stamp(started_at):
            raise RepoctlError(f"task completion receipt has invalid start timestamp: {rel}", code="invalid_completion_receipt", path=rel)
        if not isinstance(completed_event_at, str) or not _valid_event_stamp(completed_event_at):
            raise RepoctlError(f"task completion receipt has invalid completion event timestamp: {rel}", code="invalid_completion_receipt", path=rel)
        if started_at > completed_event_at:
            raise RepoctlError(f"task completion receipt has invalid task interval: {rel}", code="invalid_completion_receipt", path=rel)
        _validate_receipt_path_transitions(
            rel=rel,
            repo_evidence=repo_evidence,
            entries=entries,
            ownership=ownership,
        )
    outcome = data.get("discovery_outcome")
    if schema_version == COMPLETION_RECEIPT_SCHEMA_VERSION:
        try:
            validate_completion_outcome(outcome)
        except ValueError as exc:
            raise RepoctlError(
                f"task completion receipt has invalid Discovery outcome: {rel}",
                code="invalid_completion_receipt",
                path=rel,
            ) from exc
    elif outcome is not None:
        raise RepoctlError(
            f"legacy task completion receipt must not invent Discovery outcome facts: {rel}",
            code="invalid_completion_receipt",
            path=rel,
        )
    return CompletionReceiptArtifact(
        receipt=data,
        receipt_path=rel,
        receipt_text=receipt_text,
        receipt_sha256=_sha256_text(receipt_text),
        declared_path=task_path,
        resolved_path=resolution.resolved_path,
        content_sha256=content_sha256,
        artifact_text=artifact_text,
    )


def collect_completion_receipt_collection(
    root: Path,
    *,
    repo_id: str | None = None,
) -> CompletionReceiptCollection:
    directory = _state_dir(root) / "completions"
    if directory_problem := _completion_receipt_path_problem(root, directory):
        return CompletionReceiptCollection((), (directory_problem,), ())
    if not directory.exists() and not directory.is_symlink():
        return CompletionReceiptCollection((), (), ())
    if not directory.is_dir():
        rel = directory.relative_to(root).as_posix()
        return CompletionReceiptCollection(
            (),
            (
                Problem(
                    "error",
                    "invalid_completion_receipt",
                    f"task completion receipt directory is not a directory: {rel}",
                    rel,
                ),
            ),
            (),
        )
    artifacts: list[CompletionReceiptArtifact] = []
    problems: list[Problem] = []
    resolutions: list[CompletionReceiptArtifactResolution] = []
    for path in sorted(directory.glob("T-*.json")):
        rel = path.relative_to(root).as_posix()
        if path_problem := _completion_receipt_path_problem(root, path):
            problems.append(path_problem)
            continue
        try:
            receipt_bytes = path.read_bytes()
            receipt_text = receipt_bytes.decode("utf-8")
            data = json.loads(receipt_text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            problems.append(Problem("error", "invalid_completion_receipt", f"task completion receipt is unreadable: {rel}", rel))
            continue
        if not isinstance(data, dict):
            problems.append(Problem("error", "invalid_completion_receipt", f"task completion receipt has invalid schema: {rel}", rel))
            continue
        try:
            receipt_repo_id = _completion_receipt_repo_id(path, root, data)
        except RepoctlError as exc:
            problems.append(Problem("error", exc.code or "invalid_completion_receipt", str(exc), exc.path or rel))
            continue
        if repo_id is not None and receipt_repo_id != repo_id:
            continue
        task_id = str(data.get("task_id") or "")
        task_path = str(data.get("task_path_at_completion") or "")
        resolution = _resolve_receipt_artifact(
            root,
            task_id,
            task_path,
            receipt_path=rel,
            receipt_sha256=_sha256_bytes(receipt_bytes),
            audit_history=True,
        )
        resolutions.append(resolution)
        try:
            artifact = _validate_completion_receipt(
                path,
                root,
                data,
                receipt_text=receipt_text,
                artifact_resolution=resolution,
            )
        except RepoctlError as exc:
            problems.append(Problem("error", exc.code or "invalid_completion_receipt", str(exc), exc.path or rel))
            continue
        artifacts.append(artifact)
    return CompletionReceiptCollection(tuple(artifacts), tuple(problems), tuple(resolutions))


def completion_receipt_authority_exists(
    root: Path,
    *,
    repo_id: str | None = None,
) -> bool:
    """Check fixed receipt authority without resolving archived task artifacts."""

    directory = _state_dir(root) / "completions"
    if not directory.is_dir():
        return False
    for path in directory.glob("T-*.json"):
        if repo_id is None:
            return True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and _completion_receipt_repo_id(path, root, data) == repo_id:
                return True
        except (OSError, json.JSONDecodeError, RepoctlError):
            return True
    return False


def collect_completion_receipt_artifacts(
    root: Path,
    *,
    repo_id: str | None = None,
) -> tuple[list[CompletionReceiptArtifact], list[Problem]]:
    collection = collect_completion_receipt_collection(root, repo_id=repo_id)
    return list(collection.artifacts), list(collection.problems)


def _completion_receipt_data_for_task(
    root: Path,
    *,
    task_id: str,
    repo_id: str | None = None,
) -> tuple[Path | None, dict[str, Any] | None, str, list[Problem]]:
    try:
        normalized_task_id = normalize_task_id(task_id)
    except RepoctlError as exc:
        return None, None, "", [Problem("error", exc.code or "invalid_completion_receipt", str(exc), exc.path or task_id)]
    if not ID_RE.fullmatch(normalized_task_id):
        return None, None, "", [Problem("error", "invalid_completion_receipt", "completion receipt task id is invalid", task_id)]
    path = _completion_receipt_path(root, normalized_task_id)
    rel = path.relative_to(root).as_posix()
    if path_problem := _completion_receipt_path_problem(root, path):
        return None, None, "", [path_problem]
    if not path.is_file():
        return None, None, "", []
    try:
        receipt_bytes = path.read_bytes()
        receipt_text = receipt_bytes.decode("utf-8")
        data = json.loads(receipt_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "", [Problem("error", "invalid_completion_receipt", f"task completion receipt is unreadable: {rel}", rel)]
    if not isinstance(data, dict):
        return None, None, "", [Problem("error", "invalid_completion_receipt", f"task completion receipt has invalid schema: {rel}", rel)]
    try:
        receipt_repo_id = _completion_receipt_repo_id(path, root, data)
        if repo_id is not None and receipt_repo_id != repo_id:
            raise RepoctlError(
                f"task completion receipt repo_id does not match requested repository: {rel}",
                code="invalid_completion_receipt",
                path=rel,
            )
    except RepoctlError as exc:
        return None, None, "", [Problem("error", exc.code or "invalid_completion_receipt", str(exc), exc.path or rel)]
    return path, data, receipt_text, []


def completion_receipt_artifact_for_task(
    root: Path,
    *,
    task_id: str,
    repo_id: str | None = None,
    audit_history: bool = False,
) -> tuple[CompletionReceiptArtifact | None, list[Problem]]:
    """Return one validated receipt with its unique current task artifact identity."""
    path, receipt, receipt_text, problems = _completion_receipt_data_for_task(root, task_id=task_id, repo_id=repo_id)
    if path is None or receipt is None or problems:
        return None, problems
    try:
        resolution = _resolve_receipt_artifact(
            root,
            str(receipt.get("task_id") or ""),
            str(receipt.get("task_path_at_completion") or ""),
            receipt_path=path.relative_to(root).as_posix(),
            receipt_sha256=_sha256_text(receipt_text),
            audit_history=audit_history,
        )
        return _validate_completion_receipt(
            path,
            root,
            receipt,
            receipt_text=receipt_text,
            artifact_resolution=resolution,
        ), []
    except RepoctlError as exc:
        rel = path.relative_to(root).as_posix()
        return None, [Problem("error", exc.code or "invalid_completion_receipt", str(exc), exc.path or rel)]


def _target_for_task(root: Path, task: "Task", *, layout: RepoLayout | None = None) -> RepoTarget | None:
    repo_id = str(task.frontmatter.get("repo_id") or "").strip()
    return resolve_task_repo_target(
        root,
        repo_id=repo_id,
        repo_scoped=_repo_scoped_task(task),
        layout=layout,
        task_path=task.rel_path,
    )


def _root_task_product_surfaces(root: Path, *, layout: RepoLayout | None = None) -> tuple[RepoTarget, ...]:
    layout = layout or repo_layout(root)
    targets = list(layout.targets)
    target_paths = {target.display_path for target in targets}
    for candidate in layout.candidates:
        if candidate.display_path in target_paths:
            continue
        targets.append(RepoTarget("", candidate.root_path, candidate.display_path, "unbound"))
    return tuple(targets)


def _no_product_repo_state() -> RepoGitState:
    return RepoGitState(False, "task has no product repository target")


def _write_task_state(root: Path, task_id: str, payload: dict[str, Any]) -> None:
    path = _baseline_path(root, task_id)
    existing: dict[str, Any] | None = None
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RepoctlError(f"task state is unreadable: {path.relative_to(root).as_posix()}", code="task_state_unreadable", path=path.relative_to(root).as_posix()) from exc
        if not isinstance(loaded, dict):
            raise RepoctlError(f"task state has invalid schema: {path.relative_to(root).as_posix()}", code="task_state_invalid", path=path.relative_to(root).as_posix())
        existing = loaded
    if existing is not None and existing.get("initial") != payload.get("initial"):
        raise RepoctlError("initial task baseline is immutable", code="initial_baseline_mutation", path=path.relative_to(root).as_posix())
    _state_dir(root).mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_task_state(root: Path, task_id: str) -> dict[str, Any] | None:
    path = _baseline_path(root, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoctlError(f"task state is unreadable: {path.relative_to(root).as_posix()}", code="task_state_unreadable", path=path.relative_to(root).as_posix()) from exc
    if not isinstance(data, dict):
        raise RepoctlError(f"task state has invalid schema: {path.relative_to(root).as_posix()}", code="task_state_invalid", path=path.relative_to(root).as_posix())
    try:
        schema_version = decode_schema_version(
            data.get("schema_version"),
            supported=(LEGACY_TASK_STATE_SCHEMA_VERSION, TASK_STATE_SCHEMA_VERSION),
        )
    except ValueError as exc:
        raise RepoctlError(
            "task state schema is unsupported; the initial baseline will not be inferred",
            code="task_state_schema_unsupported",
            path=path.relative_to(root).as_posix(),
        ) from exc
    if data.get("schema") != "repoctl.task.state":
        raise RepoctlError(
            "task state schema is unsupported; the initial baseline will not be inferred",
            code="task_state_schema_unsupported",
            path=path.relative_to(root).as_posix(),
        )
    if data.get("task_id") != task_id or not isinstance(data.get("initial"), dict) or not isinstance(data.get("ownership", {}), dict):
        raise RepoctlError(f"task state has invalid schema: {path.relative_to(root).as_posix()}", code="task_state_invalid", path=path.relative_to(root).as_posix())
    data["schema_version"] = schema_version
    return data


def _stable_state_map(value: Any, *, paths: set[str], rel: str) -> dict[str, StablePathState]:
    if not isinstance(value, dict) or set(value) != paths:
        raise RepoctlError(f"task repo stable baseline is invalid: {rel}", code="task_state_invalid", path=rel)
    states: dict[str, StablePathState] = {}
    for raw_path, raw_state in value.items():
        if not isinstance(raw_path, str):
            raise RepoctlError(f"task repo stable baseline is invalid: {rel}", code="task_state_invalid", path=rel)
        path = raw_path
        state = normalize_stable_path_state(raw_state)
        if normalize_repo_path(path) != path or state is None:
            raise RepoctlError(f"task repo stable baseline is invalid: {rel}", code="task_state_invalid", path=rel)
        states[path] = state
    return states


def _stable_fingerprints(states: dict[str, StablePathState]) -> dict[str, str]:
    return {path: stable_path_state_digest(state) for path, state in states.items()}


def _canonical_baseline_ownership(
    value: Any,
    *,
    include_fingerprints: bool,
    allowed_paths: set[str] | None,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ValueError("baseline ownership must be an object")
    expected_fields = {"ownership", "decided_at"}
    if include_fingerprints:
        expected_fields.update({"baseline_fingerprint", "final_fingerprint"})
    decisions: dict[str, dict[str, str]] = {}
    for path, raw_decision in value.items():
        if (
            not isinstance(path, str)
            or normalize_repo_path(path) != path
            or (allowed_paths is not None and path not in allowed_paths)
            or not isinstance(raw_decision, dict)
            or set(raw_decision) != expected_fields
        ):
            raise ValueError("baseline ownership decision has an invalid schema")
        raw_owner = raw_decision.get("ownership")
        decided_at = raw_decision.get("decided_at")
        try:
            owner = BaselineOwnership(raw_owner)
        except (TypeError, ValueError) as exc:
            raise ValueError("baseline ownership has an invalid owner") from exc
        if not isinstance(decided_at, str) or not _valid_utc_stamp(decided_at):
            raise ValueError("baseline ownership has an invalid decision timestamp")
        decision = {"ownership": owner.value, "decided_at": decided_at}
        if include_fingerprints:
            baseline_fingerprint = raw_decision.get("baseline_fingerprint")
            final_fingerprint = raw_decision.get("final_fingerprint")
            if (
                not isinstance(baseline_fingerprint, str)
                or not isinstance(final_fingerprint, str)
                or not _valid_sha256(baseline_fingerprint)
                or not _valid_sha256(final_fingerprint)
            ):
                raise ValueError("legacy baseline ownership has an invalid fingerprint")
            decision.update(
                {
                    "baseline_fingerprint": baseline_fingerprint,
                    "final_fingerprint": final_fingerprint,
                }
            )
        decisions[path] = decision
    return decisions


def _decode_task_baseline_ownership(
    root: Path,
    state_path: Path,
    value: Any,
    *,
    state_version: int,
    allowed_paths: set[str],
) -> dict[str, dict[str, str]]:
    try:
        return _canonical_baseline_ownership(
            value,
            include_fingerprints=state_version == LEGACY_TASK_STATE_SCHEMA_VERSION,
            allowed_paths=allowed_paths,
        )
    except ValueError as exc:
        raise _invalid_task_baseline(root, state_path) from exc


def _write_repo_baseline(root: Path, task: "Task", entries: list[ChangedEntry], git_state: RepoGitState, target: RepoTarget) -> None:
    if not git_state.available:
        return
    baseline_paths = _entry_mutation_paths(entries)
    path_states, stable_state = repo_path_stable_states(root, baseline_paths, target)
    if not stable_state.available:
        raise RepoctlError(
            f"cannot record stable task baseline: {stable_state.reason}",
            code="repo_git_unavailable",
            path=stable_state.repo_path or "repos",
        )
    path_fingerprints = _stable_fingerprints(path_states)
    git_toplevel = ""
    if target is not None:
        try:
            git_toplevel = target.root_path.resolve().as_posix()
        except OSError:
            git_toplevel = target.root_path.as_posix()
    head, _head_state = repo_git_head(root, target)
    payload = {
        "schema": "repoctl.task.state",
        "schema_version": TASK_STATE_SCHEMA_VERSION,
        "task_id": task.id,
        "initial": {
            "created": utc_stamp(),
            "started_at": _utc_event_stamp(),
            "repo_id": git_state.repo_id,
            "repo_path": git_state.repo_path,
            "git_toplevel": git_toplevel,
            "identity_source": target.identity_source.value,
            "start_head": head,
            "dirty_entries": [_entry_to_dict(entry) for entry in entries],
            "dirty_path_fingerprints": path_fingerprints,
            "dirty_path_states": path_states,
        },
        "ownership": {},
    }
    _write_task_state(root, task.id, payload)


def _repo_baseline_record(root: Path, target: RepoTarget) -> dict[str, Any] | None:
    entries, git_state = repo_changed_entries(root, target)
    if not git_state.available:
        raise RepoctlError(
            f"cannot record task repository baseline: {git_state.reason}",
            code=git_state.problem_code or "repo_git_unavailable",
            path=git_state.repo_path or target.display_path,
        )
    baseline_paths = _entry_mutation_paths(entries)
    path_states, stable_state = repo_path_stable_states(root, baseline_paths, target)
    if not stable_state.available:
        raise RepoctlError(
            f"cannot record stable task baseline: {stable_state.reason}",
            code="repo_git_unavailable",
            path=stable_state.repo_path or target.display_path,
        )
    path_fingerprints = _stable_fingerprints(path_states)
    try:
        git_toplevel = target.root_path.resolve().as_posix()
    except OSError:
        git_toplevel = target.root_path.as_posix()
    head, _head_state = repo_git_head(root, target)
    return {
        "repo_id": target.id,
        "repo_path": target.display_path,
        "git_toplevel": git_toplevel,
        "identity_source": target.identity_source.value,
        "start_head": head,
        "dirty_entries": [_entry_to_dict(entry) for entry in entries],
        "dirty_path_fingerprints": path_fingerprints,
        "dirty_path_states": path_states,
    }


def _write_product_repo_baselines(root: Path, task: "Task", targets: tuple[RepoTarget, ...]) -> None:
    records = [record for target in targets if (record := _repo_baseline_record(root, target)) is not None]
    payload = {
        "schema": "repoctl.task.state",
        "schema_version": TASK_STATE_SCHEMA_VERSION,
        "task_id": task.id,
        "initial": {
            "created": utc_stamp(),
            "started_at": _utc_event_stamp(),
            "repositories": records,
        },
        "ownership": {},
    }
    _write_task_state(root, task.id, payload)


def _invalid_task_baseline(root: Path, state_path: Path, message: str = "task repo baseline is invalid") -> RepoctlError:
    rel = state_path.relative_to(root).as_posix()
    return RepoctlError(f"{message}: {rel}", code="task_state_invalid", path=rel)


def _decode_repo_baseline_record(
    root: Path,
    state_path: Path,
    value: Any,
    *,
    state_version: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_task_baseline(root, state_path)
    required = {
        "repo_id",
        "repo_path",
        "git_toplevel",
        "start_head",
        "dirty_entries",
        "dirty_path_fingerprints",
    }
    if state_version == TASK_STATE_SCHEMA_VERSION:
        required.update({"dirty_path_states", "identity_source"})
    if set(value) != required:
        raise _invalid_task_baseline(root, state_path)
    repo_id = value.get("repo_id")
    repo_path = value.get("repo_path")
    git_toplevel = value.get("git_toplevel")
    start_head = value.get("start_head")
    raw_identity_source = value.get("identity_source")
    if state_version == TASK_STATE_SCHEMA_VERSION:
        try:
            identity_source = RepositoryIdentitySource(raw_identity_source)
        except (TypeError, ValueError) as exc:
            raise _invalid_task_baseline(root, state_path) from exc
    else:
        identity_source = RepositoryIdentitySource.UNBOUND if repo_id == "" else RepositoryIdentitySource.PINNED
    if (
        not all(isinstance(item, str) for item in (repo_id, repo_path, git_toplevel, start_head))
        or (identity_source is RepositoryIdentitySource.UNBOUND) != (repo_id == "")
        or (bool(repo_id) and not re.fullmatch(r"[a-z][a-z0-9_-]*", repo_id))
        or normalize_repo_path(repo_path) != repo_path
        or not (repo_path == "repos" or repo_path.startswith("repos/"))
        or not git_toplevel
        or not Path(git_toplevel).is_absolute()
        or not (start_head == "<unborn>" or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", start_head))
    ):
        raise _invalid_task_baseline(root, state_path)
    entries = _parse_baseline_entries(value.get("dirty_entries"), state_path, root)
    paths = set(_entry_mutation_paths(entries))
    raw_fingerprints = value.get("dirty_path_fingerprints")
    if not isinstance(raw_fingerprints, dict) or set(raw_fingerprints) != paths:
        raise _invalid_task_baseline(root, state_path)
    if any(not isinstance(path, str) or not isinstance(digest, str) for path, digest in raw_fingerprints.items()):
        raise _invalid_task_baseline(root, state_path)
    fingerprints = dict(raw_fingerprints)
    if any(normalize_repo_path(path) != path or not _valid_sha256(digest) for path, digest in fingerprints.items()):
        raise _invalid_task_baseline(root, state_path)
    path_states: dict[str, StablePathState] = {}
    if state_version == TASK_STATE_SCHEMA_VERSION:
        path_states = _stable_state_map(
            value.get("dirty_path_states"),
            paths=paths,
            rel=state_path.relative_to(root).as_posix(),
        )
        if fingerprints != _stable_fingerprints(path_states):
            raise _invalid_task_baseline(root, state_path, "task repo stable baseline digest is invalid")
    return {
        "repo_id": repo_id,
        "repo_path": repo_path,
        "git_toplevel": git_toplevel,
        "identity_source": identity_source.value,
        "start_head": start_head,
        "dirty_entries": [_entry_to_dict(entry) for entry in entries],
        "dirty_path_fingerprints": fingerprints,
        **({"dirty_path_states": path_states} if state_version == TASK_STATE_SCHEMA_VERSION else {}),
        "state_version": state_version,
    }


def _read_repo_baseline(root: Path, task_id: str) -> dict[str, Any] | None:
    data = _read_task_state(root, task_id)
    if data is None:
        return None
    path = _baseline_path(root, task_id)
    initial = data["initial"]
    state_version = data["schema_version"]
    common_initial = {"created", *( ["started_at"] if state_version == TASK_STATE_SCHEMA_VERSION else [] )}
    created_at = initial.get("created")
    started_at = initial.get("started_at", "")
    if not isinstance(created_at, str) or not isinstance(started_at, str):
        raise _invalid_task_baseline(root, path)
    if not _valid_utc_stamp(created_at) or (
        state_version == TASK_STATE_SCHEMA_VERSION and not _valid_event_stamp(started_at)
    ):
        raise _invalid_task_baseline(root, path)
    if "repositories" in initial:
        if set(initial) != {*common_initial, "repositories"}:
            raise _invalid_task_baseline(root, path)
        raw_repositories = initial.get("repositories")
        if not isinstance(raw_repositories, list):
            raise _invalid_task_baseline(root, path)
        repositories = [
            _decode_repo_baseline_record(root, path, item, state_version=state_version)
            for item in raw_repositories
        ]
        identities = [(record["repo_id"], record["repo_path"]) for record in repositories]
        if len(set(identities)) != len(identities):
            raise _invalid_task_baseline(root, path)
        ownership = _decode_task_baseline_ownership(
            root,
            path,
            data.get("ownership"),
            state_version=state_version,
            allowed_paths=set(),
        )
        return {
            "repositories": repositories,
            "entries": [],
            "path_fingerprints": {},
            "path_states": {},
            "repo_id": "",
            "repo_path": "",
            "git_toplevel": "",
            "ownership": ownership,
            "state_version": state_version,
            "created_at": created_at,
            "started_at": started_at,
        }
    record_fields = {
        key: value
        for key, value in initial.items()
        if key not in common_initial
    }
    if set(initial) != {*common_initial, *record_fields}:
        raise _invalid_task_baseline(root, path)
    record = _decode_repo_baseline_record(root, path, record_fields, state_version=state_version)
    entries = _parse_baseline_entries(record["dirty_entries"], path, root)
    fingerprints = dict(record["dirty_path_fingerprints"])
    path_states = dict(record.get("dirty_path_states") or {})
    ownership = _decode_task_baseline_ownership(
        root,
        path,
        data.get("ownership"),
        state_version=state_version,
        allowed_paths=set(fingerprints),
    )
    return {
        "entries": entries,
        "path_fingerprints": fingerprints,
        "path_states": path_states,
        "repo_id": record["repo_id"],
        "repo_path": record["repo_path"],
        "git_toplevel": record["git_toplevel"],
        "identity_source": record["identity_source"],
        "head": record["start_head"],
        "ownership": ownership,
        "state_version": state_version,
        "created_at": created_at,
        "started_at": started_at,
    }


def _root_baseline_repository_records(baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    if baseline is None:
        return []
    records = baseline.get("repositories")
    if isinstance(records, list) and records:
        return records
    repo_path = str(baseline.get("repo_path") or "")
    if not repo_path:
        return []
    return [
        {
            "repo_id": str(baseline.get("repo_id") or ""),
            "repo_path": repo_path,
            "git_toplevel": str(baseline.get("git_toplevel") or ""),
            "identity_source": str(baseline.get("identity_source") or ""),
            "start_head": str(baseline.get("head") or ""),
            "dirty_entries": [_entry_to_dict(entry) for entry in baseline.get("entries", [])],
            "dirty_path_fingerprints": dict(baseline.get("path_fingerprints") or {}),
            **(
                {"dirty_path_states": dict(baseline.get("path_states") or {})}
                if baseline.get("state_version") == TASK_STATE_SCHEMA_VERSION
                else {}
            ),
            "state_version": baseline.get("state_version"),
        }
    ]


def _current_baseline_fingerprints(
    root: Path,
    *,
    baseline: dict[str, Any],
    paths: list[str],
    target: RepoTarget,
) -> tuple[dict[str, str], RepoGitState]:
    if baseline.get("state_version") == TASK_STATE_SCHEMA_VERSION:
        states, state = repo_path_stable_states(root, paths, target)
        return _stable_fingerprints(states), state
    return repo_path_fingerprints(root, paths, target)


def _build_task_path_transitions(
    root: Path,
    task: Task,
    *,
    target: RepoTarget,
    entries: list[ChangedEntry],
    mode: _CompletionEvidenceMode,
    observed_head: str,
) -> tuple[list[dict[str, Any]] | None, str]:
    state = _read_task_state(root, task.id)
    baseline = _read_repo_baseline(root, task.id)
    if state is None or baseline is None:
        return None, ""
    initial = state.get("initial") if isinstance(state.get("initial"), dict) else {}
    started_at = str(initial.get("started_at") or "")
    if not _valid_event_stamp(started_at):
        return None, ""
    changed_paths = set(_entry_mutation_paths(entries))
    ownership = baseline.get("ownership") if isinstance(baseline.get("ownership"), dict) else {}
    task_owned_paths = {
        path
        for path, decision in ownership.items()
        if decision["ownership"] == BaselineOwnership.TASK.value
    }
    paths = sorted(
        changed_paths
        if mode is _CompletionEvidenceMode.COMMITTED_RANGE
        else changed_paths | task_owned_paths
    )
    if not paths:
        return [], started_at
    start_head = str(baseline.get("head") or "")
    if not start_head:
        return None, started_at
    initial_dirty_paths = set(_entry_mutation_paths(list(baseline.get("entries") or [])))
    path_states: dict[str, StablePathState] = {}
    if mode is not _CompletionEvidenceMode.COMMITTED_RANGE:
        initial_states = baseline.get("path_states") if isinstance(baseline.get("path_states"), dict) else {}
        path_states = {
            path: state_value
            for path, state_value in initial_states.items()
            if path in initial_dirty_paths
        }
        missing_dirty_evidence = initial_dirty_paths & set(paths) - set(path_states)
        if missing_dirty_evidence:
            return None, started_at
    revision_paths = (
        paths
        if mode is _CompletionEvidenceMode.COMMITTED_RANGE
        else sorted(set(paths) - initial_dirty_paths)
    )
    if revision_paths:
        revision_states, revision_state = repo_path_stable_states(
            root,
            revision_paths,
            target,
            revision=start_head,
        )
        if not revision_state.available:
            raise RepoctlError(
                f"cannot read stable task-start state: {revision_state.reason}",
                code="root_evidence_incomplete",
                path=revision_state.repo_path or target.display_path,
            )
        path_states.update(revision_states)
    final_revision = observed_head if mode is _CompletionEvidenceMode.COMMITTED_RANGE else None
    final_states, final_state = repo_path_stable_states(
        root,
        paths,
        target,
        revision=final_revision,
    )
    if not final_state.available:
        raise RepoctlError(
            f"cannot read stable task terminal state: {final_state.reason}",
            code="terminal_evidence_incomplete",
            path=final_state.repo_path or target.display_path,
        )
    transitions: list[dict[str, Any]] = []
    for path in paths:
        before = path_states[path]
        after = final_states[path]
        if stable_path_state_digest(before) == stable_path_state_digest(after):
            if path in changed_paths:
                raise RepoctlError(
                    "repository change has no stable before/after transition",
                    code="stable_path_transition_noop",
                    path=path,
                )
            continue
        basis = sorted(
            {
                *(["observed_change"] if path in changed_paths else []),
                *(["baseline_ownership"] if path in task_owned_paths else []),
            }
        )
        transitions.append(
            {
                "path": path,
                "effect": "remove" if after.get("kind") == "missing" else "write",
                "basis": basis,
                "before": before,
                "after": after,
            }
        )
    return sorted(transitions, key=lambda item: item["path"]), started_at


def _parse_baseline_entries(raw_entries: Any, state_path: Path, root: Path) -> list[ChangedEntry]:
    if not isinstance(raw_entries, list):
        raise _invalid_task_baseline(root, state_path)
    entries: list[ChangedEntry] = []
    for item in raw_entries:
        try:
            entries.append(_receipt_changed_entry(item, rel=state_path.relative_to(root).as_posix(), label="baseline entry"))
        except RepoctlError as exc:
            raise _invalid_task_baseline(root, state_path) from exc
    if len(set(entries)) != len(entries):
        raise _invalid_task_baseline(root, state_path)
    return entries


def resolve_task_baseline_ownerships(
    root: Path,
    task_id: str,
    *,
    resolutions: list[tuple[str, str]],
    apply: bool = True,
) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    if task.status not in LIVE:
        raise RepoctlError("baseline ownership can only be resolved for a live task", code="task_not_live", path=task.rel_path)
    target = _target_for_task(root, task)
    if target is None:
        raise RepoctlError("baseline ownership requires an explicit product repository target", code="repository_selector_required", path=task.rel_path)
    state = _read_task_state(root, task.id)
    baseline = _read_repo_baseline(root, task.id)
    if state is None or baseline is None:
        raise RepoctlError("task has no initial repo baseline", code="repo_head_missing_at_start", path=task.rel_path)
    baseline_fingerprints = baseline.get("path_fingerprints") if isinstance(baseline.get("path_fingerprints"), dict) else {}
    known_paths = {str(path) for path in baseline_fingerprints}
    normalized_resolutions: dict[str, str] = {}
    for path, ownership in resolutions:
        if ownership not in {"task", "preexisting"}:
            raise RepoctlError("baseline ownership must be task or preexisting", code="invalid_baseline_ownership", path=path)
        resolution = resolve_repo_selector_path(
            path,
            repository_path=target.display_path,
            known_paths=known_paths,
        )
        if resolution.status == RepoSelectorStatus.INVALID:
            raise RepoctlError("baseline ownership path must be repo-relative", code="invalid_baseline_path", path=path)
        if resolution.status == RepoSelectorStatus.NOT_FOUND:
            raise RepoctlError(
                "baseline ownership path was not dirty at task start",
                code="baseline_path_not_initially_dirty",
                path=resolution.path or path,
            )
        if resolution.status == RepoSelectorStatus.AMBIGUOUS:
            raise RepoctlError(
                "baseline ownership path matches both repo-relative and workspace-relative identities",
                code="ambiguous_baseline_path",
                path=path,
            )
        normalized = resolution.path
        previous = normalized_resolutions.get(normalized)
        if previous and previous != ownership:
            raise RepoctlError("one baseline path cannot receive conflicting ownership decisions", code="conflicting_baseline_resolution", path=normalized)
        normalized_resolutions[normalized] = ownership
    if not normalized_resolutions:
        raise RepoctlError("at least one baseline ownership resolution is required", code="missing_baseline_resolution", path=task.rel_path)
    current_fingerprints, git_state = _current_baseline_fingerprints(
        root,
        baseline=baseline,
        paths=list(normalized_resolutions),
        target=target,
    )
    if not git_state.available:
        raise RepoctlError(f"cannot inspect baseline ownership path: {git_state.reason}", code="repo_git_unavailable", path=git_state.repo_path or target.display_path)
    decided_at = utc_stamp()
    resolved: list[dict[str, str]] = []
    for normalized, ownership in sorted(normalized_resolutions.items()):
        baseline_fingerprint = str(baseline_fingerprints.get(normalized) or "")
        final_fingerprint = str(current_fingerprints.get(normalized) or "")
        if ownership == "preexisting" and final_fingerprint != baseline_fingerprint:
            raise RepoctlError(
                "preexisting ownership requires the path to be restored exactly to its initial dirty state",
                code="baseline_not_restored",
                path=normalized,
            )
        resolved.append(
            {
                "path": normalized,
                "ownership": ownership,
                "baseline_fingerprint": baseline_fingerprint,
                "final_fingerprint": final_fingerprint,
            }
        )
    if apply:
        updated = json.loads(json.dumps(state))
        ownership_state = updated.setdefault("ownership", {})
        for item in resolved:
            decision = {
                "ownership": item["ownership"],
                "decided_at": decided_at,
            }
            if baseline.get("state_version") != TASK_STATE_SCHEMA_VERSION:
                decision.update(
                    {
                        "baseline_fingerprint": item["baseline_fingerprint"],
                        "final_fingerprint": item["final_fingerprint"],
                    }
                )
            ownership_state[item["path"]] = decision
        _write_task_state(root, task.id, updated)
    return {
        "task": task,
        "applied": apply,
        "resolutions": resolved,
    }


def committed_range_baseline_conflicts(root: Path, task_id: str, entries: list[ChangedEntry]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    task = resolve_task(root, task_id)
    target = _target_for_task(root, task)
    baseline = _read_repo_baseline(root, task.id)
    if target is None or baseline is None:
        return [], {}
    baseline_fingerprints = baseline.get("path_fingerprints") if isinstance(baseline.get("path_fingerprints"), dict) else {}
    ownership = baseline.get("ownership") if isinstance(baseline.get("ownership"), dict) else {}
    overlap = sorted(set(_entry_mutation_paths(entries)) & set(baseline_fingerprints))
    current_fingerprints, state = _current_baseline_fingerprints(
        root,
        baseline=baseline,
        paths=overlap,
        target=target,
    )
    if not state.available:
        raise RepoctlError(
            f"cannot inspect committed-range baseline paths: {state.reason}",
            code=state.problem_code or "repo_git_unavailable",
            path=state.repo_path or target.display_path,
        )
    conflicts: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    for path in overlap:
        decision = ownership.get(path, {})
        owner = decision.get("ownership", "")
        baseline_fingerprint = str(baseline_fingerprints.get(path) or "")
        final_fingerprint = str(current_fingerprints.get(path) or "")
        evidence[path] = {
            "ownership": owner,
            "decided_at": decision.get("decided_at", ""),
            "baseline_fingerprint": baseline_fingerprint,
            "final_fingerprint": final_fingerprint,
        }
        if owner == "task":
            continue
        if owner == "preexisting" and final_fingerprint == baseline_fingerprint:
            continue
        conflicts.append(path)
    return conflicts, evidence


def task_baseline_ownership_evidence(root: Path, task_id: str) -> dict[str, dict[str, Any]]:
    task = resolve_task(root, task_id)
    target = _target_for_task(root, task)
    baseline = _read_repo_baseline(root, task.id)
    if target is None or baseline is None:
        return {}
    ownership = baseline.get("ownership") if isinstance(baseline.get("ownership"), dict) else {}
    baseline_fingerprints = (
        baseline.get("path_fingerprints")
        if isinstance(baseline.get("path_fingerprints"), dict)
        else {}
    )
    paths = sorted(ownership)
    current_fingerprints, _state = _current_baseline_fingerprints(
        root,
        baseline=baseline,
        paths=paths,
        target=target,
    )
    evidence: dict[str, dict[str, Any]] = {}
    for path in paths:
        decision = ownership[path]
        evidence[path] = {
            "ownership": decision["ownership"],
            "decided_at": decision["decided_at"],
            "baseline_fingerprint": str(baseline_fingerprints.get(path) or ""),
            "final_fingerprint": str(current_fingerprints.get(path) or ""),
        }
    return evidence


def _workspace_receipt_has_repository_claim(receipt: dict[str, Any], *, rel: str) -> bool:
    repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {}
    mode, attribution = _completion_evidence_pair(repo_evidence, rel=rel)
    manifest = repo_evidence.get("fingerprint_manifest")
    return bool(
        mode is not _CompletionEvidenceMode.NONE
        or attribution is not _CompletionEvidenceAttribution.NONE
        or receipt.get("changed_entries")
        or manifest not in (None, {})
        or str(repo_evidence.get("diff_fingerprint_sha256") or "")
    )


def _done_descendant_completion_receipts(
    root: Path,
    task: Task,
    *,
    layout: RepoLayout,
) -> tuple[list[tuple[Task, dict[str, Any]]], list[Problem]]:
    if _repo_scoped_task(task):
        return [], []
    children = children_by_parent(load_tasks(root, include_archived=False))
    stack = list(children.get(task.id, []))
    seen = {task.id}
    done: list[tuple[Task, dict[str, Any]]] = []
    problems: list[Problem] = []
    while stack:
        child = stack.pop()
        if child.id in seen:
            problems.append(
                Problem(
                    "error",
                    "child_completion_task_duplicate",
                    "parent task contains a duplicate or cyclic descendant identity",
                    child.rel_path,
                )
            )
            continue
        seen.add(child.id)
        stack.extend(children.get(child.id, []))
        if child.status != "done":
            continue
        receipt_path = _completion_receipt_path(root, child.id)
        receipt_rel = receipt_path.relative_to(root).as_posix()
        if path_problem := _completion_receipt_path_problem(root, receipt_path):
            problems.append(
                Problem(
                    "error",
                    "child_completion_receipt_invalid",
                    f"done child task completion receipt is invalid: {path_problem.message}",
                    receipt_rel,
                    path_problem.code,
                )
            )
            continue
        if not receipt_path.is_file():
            # Legacy/manual done children can remain under docs/tasks without a
            # machine receipt. They contribute no repository ownership evidence;
            # parent completion validates only receipts that actually exist.
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            problems.append(
                Problem(
                    "error",
                    "child_completion_receipt_invalid",
                    "done child task completion receipt is unreadable",
                    receipt_rel,
                )
            )
            continue
        if not isinstance(receipt, dict):
            problems.append(
                Problem(
                    "error",
                    "child_completion_receipt_invalid",
                    "done child task completion receipt is not an object",
                    receipt_rel,
                )
            )
            continue
        try:
            _validate_completion_receipt(receipt_path, root, receipt)
        except RepoctlError as exc:
            problems.append(
                Problem(
                    "error",
                    "child_completion_receipt_invalid",
                    f"done child task completion receipt is invalid: {exc}",
                    receipt_rel,
                    exc.code or "invalid_completion_receipt",
                )
            )
            continue

        child_repo_scoped = _repo_scoped_task(child)
        try:
            expected_target = _target_for_task(root, child, layout=layout)
        except RepoctlError as exc:
            problems.append(
                Problem(
                    "error",
                    "child_completion_receipt_wrong_repository",
                    "done child task repository identity cannot be resolved",
                    child.rel_path,
                    exc.code or "repository_selector_invalid",
                )
            )
            continue
        receipt_repo_id = str(receipt.get("repo_id") or "")
        repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {}
        manifest = repo_evidence.get("fingerprint_manifest") if isinstance(repo_evidence.get("fingerprint_manifest"), dict) else {}
        if child_repo_scoped:
            if expected_target is None or receipt_repo_id != expected_target.id:
                problems.append(
                    Problem(
                        "error",
                        "child_completion_receipt_wrong_repository",
                        "done child task completion receipt belongs to the wrong repository",
                        receipt_rel,
                    )
                )
                continue
            if manifest and (
                str(manifest.get("repo_id") or "") != expected_target.id
                or str(manifest.get("repo_path") or "") != expected_target.display_path
            ):
                problems.append(
                    Problem(
                        "error",
                        "child_completion_receipt_wrong_repository",
                        "done child task completion receipt repository manifest does not match the selected repository",
                        receipt_rel,
                    )
                )
                continue
        else:
            has_repository_claim = _workspace_receipt_has_repository_claim(receipt, rel=receipt_rel)
            if has_repository_claim or receipt_repo_id:
                problems.append(
                    Problem(
                        "error",
                        "child_completion_receipt_wrong_repository",
                        "workspace child task completion receipt must not claim product repository ownership",
                        receipt_rel,
                    )
                )
                continue
        done.append((child, receipt))
    unique_problems = {
        (problem.code, problem.path or "", problem.cause_code or ""): problem
        for problem in problems
    }
    return (
        sorted(done, key=lambda item: item[0].id),
        [unique_problems[key] for key in sorted(unique_problems)],
    )


def _legacy_event_interval(started_at: str, completed_at: str) -> tuple[str, str] | None:
    if not _valid_utc_stamp(started_at) or not _valid_legacy_completion_stamp(completed_at):
        return None
    try:
        start = datetime.strptime(started_at, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC, microsecond=0)
        end = (
            datetime.strptime(completed_at, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC, microsecond=999999)
            if _valid_utc_stamp(completed_at)
            else datetime.fromisoformat(completed_at.removesuffix("Z") + "+00:00")
        )
    except ValueError:
        return None
    if start > end:
        return None
    return (
        start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        end.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )


def _legacy_transition_problem(path: str, message: str) -> Problem:
    return Problem(
        "error",
        "transition_evidence_incomplete",
        message,
        path,
        "legacy_completion_receipt_v2",
    )


def _legacy_descendant_path_claims(
    root: Path,
    *,
    child: Task,
    receipt: dict[str, Any],
    receipt_path: str,
    entries: list[ChangedEntry],
    target: RepoTarget,
) -> tuple[list[_DescendantPathClaim], list[Problem], set[str]]:
    """Project a v2 receipt only while its recorded terminal state is still provable."""
    repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {}
    mode, attribution = _completion_evidence_pair(repo_evidence, rel=receipt_path)
    if not (
        mode is _CompletionEvidenceMode.WORKING_TREE_DIFF
        and attribution is _CompletionEvidenceAttribution.TASK_WORKING_TREE
    ):
        return [], [], set()

    raw_ownership = _receipt_baseline_ownership(
        repo_evidence,
        rel=receipt_path,
        schema_version=LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION,
    )
    owned_paths = {
        path
        for path, decision in raw_ownership.items()
        if decision["ownership"] == BaselineOwnership.TASK.value
    }
    mutation_paths = set(_entry_mutation_paths(entries))
    evidence_paths = mutation_paths | owned_paths
    if not evidence_paths:
        return [], [], set()

    def incomplete(paths: set[str], message: str) -> tuple[list[_DescendantPathClaim], list[Problem], set[str]]:
        return [], [_legacy_transition_problem(path, message) for path in sorted(paths)], evidence_paths

    try:
        baseline = _read_repo_baseline(root, child.id)
    except RepoctlError:
        return incomplete(evidence_paths, "legacy child task state is invalid or unreadable")
    if baseline is None or baseline.get("state_version") != LEGACY_TASK_STATE_SCHEMA_VERSION:
        return incomplete(evidence_paths, "legacy child receipt has no matching legacy task-start state")

    manifest = repo_evidence.get("fingerprint_manifest") if isinstance(repo_evidence.get("fingerprint_manifest"), dict) else {}
    start_head = str(repo_evidence.get("start_head") or "")
    observed_head = str(repo_evidence.get("observed_head") or "")
    baseline_head = str(baseline.get("head") or "")
    current_head, current_head_state = repo_git_head(root, target)
    if _repository_target_identity_problem(target=target, baseline=baseline) is not None:
        return incomplete(evidence_paths, "legacy child task-start repository identity does not match the selected repository")
    identity_matches = (
        str(receipt.get("repo_id") or "") == target.id
        and str(manifest.get("repo_id") or "") == target.id
        and str(manifest.get("repo_path") or "") == target.display_path
        and baseline_head == start_head == observed_head
        and str(manifest.get("start_head") or "") == start_head
        and str(manifest.get("observed_head") or "") == observed_head
        and (start_head == "<unborn>" or bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", start_head)))
        and current_head_state.available
        and current_head == observed_head
    )
    if not identity_matches:
        return incomplete(evidence_paths, "legacy child receipt and task-start repository identities do not match")

    interval = _legacy_event_interval(
        str(baseline.get("created_at") or ""),
        str(receipt.get("completed_at") or ""),
    )
    if interval is None:
        return incomplete(evidence_paths, "legacy child receipt has no valid conservative execution interval")

    dirty_paths = set(_entry_mutation_paths(list(baseline.get("entries") or [])))
    unprovable_paths = (mutation_paths & dirty_paths) | owned_paths
    problems = [
        _legacy_transition_problem(path, "legacy child started with this path already dirty, so its before-state cannot be reconstructed")
        for path in sorted(unprovable_paths)
    ]
    candidate_paths = mutation_paths - unprovable_paths
    if not candidate_paths:
        return [], problems, evidence_paths

    terminal_verification, terminal_state = verify_legacy_change_terminal_states(
        root,
        entries=entries,
        candidate_paths=candidate_paths,
        manifest=manifest,
        target=target,
    )
    if not terminal_state.available:
        return incomplete(candidate_paths, "legacy child terminal Git evidence cannot be observed")
    after_states = dict(terminal_verification.states)
    drifted_paths = set(terminal_verification.unverified_paths)
    if terminal_verification.problem is not None:
        drifted_paths.update(candidate_paths)

    confirmed_head, confirmed_head_state = repo_git_head(root, target)
    if not confirmed_head_state.available or confirmed_head != observed_head:
        problems.extend(
            _legacy_transition_problem(
                path,
                "legacy child repository identity changed while terminal evidence was observed",
            )
            for path in sorted(candidate_paths)
        )
        return [], problems, evidence_paths

    problems.extend(
        _legacy_transition_problem(path, "legacy child has no safe terminal-state commitment matching the current path")
        for path in sorted(drifted_paths)
    )
    claim_paths = set(after_states) - drifted_paths
    if not claim_paths:
        return [], problems, evidence_paths

    before_states, before_state = repo_path_stable_states(
        root,
        sorted(claim_paths),
        target,
        revision=start_head,
    )
    if not before_state.available:
        problems.extend(
            _legacy_transition_problem(path, "legacy child stable before path state cannot be observed")
            for path in sorted(claim_paths)
        )
        return [], problems, evidence_paths

    started_at, completed_at = interval
    claims: list[_DescendantPathClaim] = []
    for path in sorted(claim_paths):
        before = before_states[path]
        after = after_states[path]
        if stable_path_state_digest(before) == stable_path_state_digest(after):
            problems.append(
                _legacy_transition_problem(path, "legacy child receipt names a change without a stable path transition")
            )
            continue
        claims.append(
            _DescendantPathClaim(
                task_id=child.id,
                receipt_path=receipt_path,
                repo_id=target.id,
                path=path,
                effect="remove" if after.get("kind") == "missing" else "write",
                basis=("observed_change",),
                before=before,
                after=after,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
    return claims, problems, evidence_paths


def _descendant_claims_by_path(
    *,
    root: Path,
    descendant_receipts: list[tuple[Task, dict[str, Any]]],
    target: RepoTarget,
) -> tuple[dict[str, list[_DescendantPathClaim]], list[Problem], set[str]]:
    claims: dict[str, list[_DescendantPathClaim]] = {}
    problems: list[Problem] = []
    evidence_paths: set[str] = set()
    for child, receipt in descendant_receipts:
        if str(receipt.get("repo_id") or "") != target.id:
            continue
        receipt_path = f"docs/tasks/.repoctl-state/completions/{child.id}.json"
        entries = [_receipt_changed_entry(item, rel=receipt_path) for item in receipt.get("changed_entries", [])]
        mutation_paths = set(_entry_mutation_paths(entries))
        evidence_paths.update(mutation_paths)
        schema_version = receipt.get("schema_version")
        if schema_version == LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION:
            legacy_claims, legacy_problems, legacy_paths = _legacy_descendant_path_claims(
                root,
                child=child,
                receipt=receipt,
                receipt_path=receipt_path,
                entries=entries,
                target=target,
            )
            evidence_paths.update(legacy_paths)
            problems.extend(legacy_problems)
            for claim in legacy_claims:
                claims.setdefault(claim.path, []).append(claim)
            continue
        repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {}
        mode, attribution = _completion_evidence_pair(repo_evidence, rel=receipt_path)
        if not (
            mode is _CompletionEvidenceMode.WORKING_TREE_DIFF
            and attribution is _CompletionEvidenceAttribution.TASK_WORKING_TREE
        ):
            continue
        for raw_transition in repo_evidence.get("path_transitions", []):
            transition = _receipt_path_transition(raw_transition, rel=receipt_path)
            claim = _DescendantPathClaim(
                task_id=child.id,
                receipt_path=receipt_path,
                repo_id=target.id,
                path=transition["path"],
                effect=transition["effect"],
                basis=tuple(transition["basis"]),
                before=transition["before"],
                after=transition["after"],
                started_at=str(receipt.get("started_at") or ""),
                completed_at=str(receipt.get("completed_event_at") or ""),
            )
            claims.setdefault(claim.path, []).append(claim)
    return claims, problems, evidence_paths


def _repository_target_identity_problem(
    *,
    target: RepoTarget,
    baseline: dict[str, Any],
) -> Problem | None:
    expected_repo_path = str(baseline.get("repo_path") or "")
    expected_repo_id = str(baseline.get("repo_id") or "")
    if (
        expected_repo_path != target.display_path
        or (expected_repo_id and expected_repo_id != target.id)
    ):
        return Problem(
            "error",
            "repo_target_changed_since_start",
            "task baseline and current logical repository identity do not match",
            target.display_path,
        )
    return None


def _repository_transition_observation(
    root: Path,
    *,
    target: RepoTarget,
    baseline: dict[str, Any] | None,
    worktree_changes: list[ChangedEntry],
    repo_git: RepoGitState | None = None,
) -> RepositoryTransitionObservation:
    expected_repo_id = str((baseline or {}).get("repo_id") or target.id)
    expected_repo_path = str((baseline or {}).get("repo_path") or target.display_path)
    historical_git_toplevel = str((baseline or {}).get("git_toplevel") or "")
    start_head = str((baseline or {}).get("head") or (baseline or {}).get("start_head") or "")
    current_head = ""
    committed_changes: tuple[ChangedEntry, ...] = ()
    problems: list[Problem] = []
    state = repo_git or repo_git_state(root, target)

    if baseline is None:
        if state.available:
            current_head, state = repo_git_head(root, target)
        return RepositoryTransitionObservation(
            repo_id=expected_repo_id,
            repo_path=expected_repo_path,
            historical_git_toplevel=historical_git_toplevel,
            start_head="",
            current_head=current_head,
            lineage=RepositoryLineageStatus.BASELINE_MISSING,
            committed_changes=(),
            worktree_changes=tuple(sorted(set(worktree_changes))),
            repo_git=state,
        )

    if identity_problem := _repository_target_identity_problem(target=target, baseline=baseline):
        problems.append(identity_problem)
        return RepositoryTransitionObservation(
            repo_id=expected_repo_id,
            repo_path=expected_repo_path,
            historical_git_toplevel=historical_git_toplevel,
            start_head=start_head,
            current_head="",
            lineage=RepositoryLineageStatus.IDENTITY_MISMATCH,
            committed_changes=(),
            worktree_changes=tuple(sorted(set(worktree_changes))),
            repo_git=state,
            problems=tuple(problems),
        )

    if not state.available:
        problems.append(
            Problem(
                "error",
                state.problem_code or "repo_git_unavailable",
                f"cannot observe repository transition: {state.reason}",
                state.repo_path or target.display_path,
            )
        )
        return RepositoryTransitionObservation(
            repo_id=expected_repo_id,
            repo_path=expected_repo_path,
            historical_git_toplevel=historical_git_toplevel,
            start_head=start_head,
            current_head="",
            lineage=RepositoryLineageStatus.UNAVAILABLE,
            committed_changes=(),
            worktree_changes=tuple(sorted(set(worktree_changes))),
            repo_git=state,
            problems=tuple(problems),
        )

    current_head, head_state = repo_git_head(root, target)
    state = head_state
    if not head_state.available:
        problems.append(
            Problem(
                "error",
                head_state.problem_code or "repo_commit_range_unavailable",
                f"cannot observe current repository HEAD: {head_state.reason}",
                head_state.repo_path or target.display_path,
            )
        )
        lineage = RepositoryLineageStatus.UNAVAILABLE
    elif not start_head:
        problems.append(
            Problem(
                "error",
                "repo_commit_range_unavailable",
                "task baseline has no recorded repository start HEAD",
                target.display_path,
            )
        )
        lineage = RepositoryLineageStatus.UNAVAILABLE
    elif current_head == start_head:
        lineage = RepositoryLineageStatus.SAME_HEAD
    else:
        is_ancestor, ancestry_state = repo_is_ancestor(
            root,
            ancestor=start_head,
            descendant=current_head,
            target=target,
        )
        if not ancestry_state.available:
            problems.append(
                Problem(
                    "error",
                    ancestry_state.problem_code or "repo_commit_range_unavailable",
                    f"cannot compare repository transition history: {ancestry_state.reason}",
                    ancestry_state.repo_path or target.display_path,
                )
            )
            lineage = RepositoryLineageStatus.UNAVAILABLE
        elif not is_ancestor:
            problems.append(
                Problem(
                    "error",
                    "repo_history_rewritten",
                    "current repository history is not descended from the task start HEAD",
                    target.display_path,
                )
            )
            lineage = RepositoryLineageStatus.REWRITTEN
        else:
            lineage = RepositoryLineageStatus.DESCENDANT
            committed, range_state = repo_commit_range_entries(
                root,
                base=start_head,
                head=current_head,
                target=target,
            )
            if not range_state.available:
                problems.append(
                    Problem(
                        "error",
                        range_state.problem_code or "repo_commit_range_unavailable",
                        f"cannot observe committed repository transition: {range_state.reason}",
                        range_state.repo_path or target.display_path,
                    )
                )
            else:
                committed_changes = tuple(sorted(set(committed)))

    return RepositoryTransitionObservation(
        repo_id=expected_repo_id,
        repo_path=expected_repo_path,
        historical_git_toplevel=historical_git_toplevel,
        start_head=start_head,
        current_head=current_head,
        lineage=lineage,
        committed_changes=committed_changes,
        worktree_changes=tuple(sorted(set(worktree_changes))),
        repo_git=state,
        problems=tuple(problems),
    )


def _repository_baseline_problem(
    root: Path,
    *,
    target: RepoTarget,
    baseline: dict[str, Any],
) -> Problem | None:
    observation = _repository_transition_observation(
        root,
        target=target,
        baseline=baseline,
        worktree_changes=[],
    )
    return observation.problems[0] if observation.problems else None


def _parent_path_states(
    root: Path,
    *,
    target: RepoTarget,
    baseline: dict[str, Any] | None,
    paths: set[str],
) -> tuple[dict[str, StablePathState], dict[str, StablePathState], list[Problem]]:
    if not paths:
        return {}, {}, []
    if baseline is None:
        return {}, {}, [
            Problem("error", "root_evidence_incomplete", "parent task has no recorded repository root state", path)
            for path in sorted(paths)
        ]
    problems: list[Problem] = []
    baseline_problem = _repository_baseline_problem(root, target=target, baseline=baseline)
    if baseline_problem is not None:
        return {}, {}, [
            Problem(
                baseline_problem.severity,
                baseline_problem.code,
                baseline_problem.message,
                path,
                baseline_problem.cause_code,
            )
            for path in sorted(paths)
        ]
    start_head = str(baseline.get("head") or baseline.get("start_head") or "")
    state_path = _baseline_path(root, "baseline")
    raw_entries = baseline.get("entries") if "entries" in baseline else baseline.get("dirty_entries", [])
    entries = _parse_baseline_entries(raw_entries, state_path, root)
    dirty_paths = set(_entry_mutation_paths(entries))
    raw_states = baseline.get("path_states")
    if raw_states is None:
        raw_states = baseline.get("dirty_path_states")
    stable_dirty: dict[str, StablePathState] = {}
    if baseline.get("state_version") == TASK_STATE_SCHEMA_VERSION:
        try:
            stable_dirty = _stable_state_map(
                raw_states,
                paths=dirty_paths,
                rel=str(baseline.get("repo_path") or target.display_path),
            )
        except RepoctlError:
            stable_dirty = {}
    missing_dirty = paths & dirty_paths - set(stable_dirty)
    problems.extend(
        Problem("error", "root_evidence_incomplete", "parent dirty overlay lacks stable path evidence", path)
        for path in sorted(missing_dirty)
    )
    root_states = {path: stable_dirty[path] for path in paths & set(stable_dirty)}
    revision_paths = sorted(paths - dirty_paths)
    if revision_paths:
        revision_states, revision_state = repo_path_stable_states(root, revision_paths, target, revision=start_head)
        if not revision_state.available:
            problems.extend(
                Problem("error", "root_evidence_incomplete", "parent start tree cannot provide stable path evidence", path)
                for path in revision_paths
            )
        else:
            root_states.update(revision_states)
    terminal_states, terminal_state = repo_path_stable_states(root, sorted(paths), target)
    if not terminal_state.available:
        terminal_states = {}
        problems.extend(
            Problem("error", "terminal_evidence_incomplete", "current worktree cannot provide stable path evidence", path)
            for path in sorted(paths)
        )
    return root_states, terminal_states, problems


def _evaluate_path_lineage(
    path: str,
    claims: list[_DescendantPathClaim],
    *,
    root_state: StablePathState,
    terminal_state: StablePathState,
) -> tuple[list[str], Problem | None]:
    remaining = list(claims)
    for claim in remaining:
        if (
            not _valid_event_stamp(claim.started_at)
            or not _valid_event_stamp(claim.completed_at)
            or claim.started_at > claim.completed_at
        ):
            return [], Problem("error", "transition_evidence_incomplete", "child transition lacks a valid execution interval", path)
    current_digest = stable_path_state_digest(root_state)
    ordered: list[_DescendantPathClaim] = []
    while remaining:
        compatible = [claim for claim in remaining if claim.before_digest == current_digest]
        if not compatible:
            return [], Problem("error", "transition_lineage_gap", "child path states do not form one chain from the parent path state", path)
        if len(compatible) > 1:
            earliest_completion = min(claim.completed_at for claim in compatible)
            compatible = [claim for claim in compatible if claim.completed_at == earliest_completion]
            if len(compatible) != 1:
                return [], Problem("error", "transition_order_ambiguous", "child path states and completion events do not determine one transition order", path)
        selected = compatible[0]
        if ordered and selected.completed_at < ordered[-1].completed_at:
            return [], Problem(
                "error",
                "transition_order_ambiguous",
                "child completion observations contradict the selected path transition order",
                path,
            )
        ordered.append(selected)
        remaining.remove(selected)
        current_digest = selected.after_digest
    if current_digest != stable_path_state_digest(terminal_state):
        return [], Problem("error", "terminal_evidence_drift", "current path state does not match the terminal child transition", path)
    return [claim.task_id for claim in ordered], None


def _attribute_descendant_changes(
    root: Path,
    *,
    target: RepoTarget,
    entries: list[ChangedEntry],
    descendant_receipts: list[tuple[Task, dict[str, Any]]],
    baseline: dict[str, Any] | None,
) -> _DescendantAttributionResult:
    if not descendant_receipts:
        return _DescendantAttributionResult(tuple(entries), (), ())
    try:
        claims_by_path, problems, evidence_paths = _descendant_claims_by_path(
            root=root,
            descendant_receipts=descendant_receipts,
            target=target,
        )
    except RepoctlError as exc:
        problem = Problem("error", exc.code or "child_completion_evidence_invalid", str(exc), exc.path or target.display_path)
        return _DescendantAttributionResult(tuple(entries), (), (problem,))
    observed_paths = set(_entry_mutation_paths(entries))
    lineage_paths = set(claims_by_path)
    repository_problem_paths = observed_paths | lineage_paths | evidence_paths
    root_states, terminal_states, observation_problems = _parent_path_states(
        root,
        target=target,
        baseline=baseline,
        paths=observed_paths | lineage_paths,
    )
    problems.extend(observation_problems)
    valid_path_tasks: dict[str, list[str]] = {}
    problem_paths = {problem.path for problem in problems if problem.path}
    for path, claims in sorted(claims_by_path.items()):
        if path in problem_paths or path not in root_states or path not in terminal_states:
            continue
        task_ids, problem = _evaluate_path_lineage(
            path,
            claims,
            root_state=root_states[path],
            terminal_state=terminal_states[path],
        )
        if problem is not None:
            problems.append(problem)
            problem_paths.add(path)
        else:
            valid_path_tasks[path] = task_ids
    attributed: list[dict[str, Any]] = []
    attributed_entries: set[ChangedEntry] = set()
    for entry in entries:
        mutation_paths = set(_entry_mutation_paths([entry]))
        if not mutation_paths or not mutation_paths <= set(valid_path_tasks):
            continue
        change, path, old_path = entry
        task_ids = sorted({task_id for mutation_path in mutation_paths for task_id in valid_path_tasks[mutation_path]})
        record: dict[str, Any] = {
            "task_ids": task_ids,
            "repo_id": target.id,
            "change": change,
            "path": path,
            "path_state_sha256": stable_path_state_digest(terminal_states[path]),
        }
        if old_path:
            record["old_path"] = old_path
        attributed.append(record)
        attributed_entries.add(entry)
    remaining = [entry for entry in entries if entry not in attributed_entries]
    public_problems = [
        Problem(
            problem.severity,
            problem.code,
            problem.message,
            f"{target.display_path}/{problem.path}" if problem.path in repository_problem_paths else problem.path,
            problem.cause_code,
        )
        for problem in problems
    ]
    unique_problems = {
        (problem.code, problem.path or "", problem.cause_code or ""): problem
        for problem in public_problems
    }
    return _DescendantAttributionResult(
        tuple(remaining),
        tuple(attributed),
        tuple(unique_problems[key] for key in sorted(unique_problems)),
    )


def _baseline_conflicting_paths(
    *,
    baseline_paths: set[str],
    baseline_fingerprints: dict[str, Any],
    current_fingerprints: dict[str, str],
    ownership: dict[str, Any] | None = None,
) -> set[str]:
    conflicts: set[str] = set()
    ownership = ownership or {}
    for path in baseline_paths:
        baseline_fingerprint = str(baseline_fingerprints.get(path) or "")
        current_fingerprint = str(current_fingerprints.get(path) or "")
        if baseline_fingerprint and current_fingerprint == baseline_fingerprint:
            continue
        decision = ownership.get(path, {})
        if decision.get("ownership") == BaselineOwnership.TASK.value:
            continue
        conflicts.add(path)
    return conflicts


def _complete_repo_delta(
    delta: dict[str, Any],
    *,
    integrity_problems: list[Problem],
) -> dict[str, Any]:
    unique_problems = {
        (problem.code, problem.path or "", problem.cause_code or ""): problem
        for problem in integrity_problems
    }
    ordered_problems = tuple(unique_problems[key] for key in sorted(unique_problems))
    observed_committed_changes = list(delta.get("observed_committed_changes") or [])
    return {
        **delta,
        "observed_committed_changes": observed_committed_changes,
        "observed_committed_count": len(observed_committed_changes),
        "integrity_problems": ordered_problems,
    }


def _require_no_integrity_problems(delta: dict[str, Any] | None) -> None:
    problems = [
        problem
        for problem in (delta or {}).get("integrity_problems", ())
        if isinstance(problem, Problem) and problem.severity == "error"
    ]
    if not problems:
        return
    problem = sorted(
        problems,
        key=lambda item: (item.code, item.path or "", item.cause_code or ""),
    )[0]
    raise RepoctlError(
        problem.message,
        code=problem.code,
        path=problem.path or "",
        cause_code=problem.cause_code,
    )


def repo_changes_since_task_start(
    root: Path,
    task_id: str,
    *,
    layout: RepoLayout | None = None,
) -> dict[str, Any]:
    task = resolve_task(root, task_id)
    task_id = task.id
    layout = layout or repo_layout(root)
    target = _target_for_task(root, task, layout=layout)
    integrity_problems: list[Problem] = []
    descendant_receipts, receipt_problems = _done_descendant_completion_receipts(root, task, layout=layout)
    integrity_problems.extend(receipt_problems)

    def attribute(
        selected: RepoTarget,
        selected_entries: list[ChangedEntry],
        selected_baseline: dict[str, Any] | None,
    ) -> tuple[list[ChangedEntry], list[dict[str, Any]]]:
        result = _attribute_descendant_changes(
            root,
            target=selected,
            entries=selected_entries,
            descendant_receipts=descendant_receipts,
            baseline=selected_baseline,
        )
        integrity_problems.extend(result.problems)
        return list(result.remaining), list(result.attributed)

    def complete(delta: dict[str, Any]) -> dict[str, Any]:
        return _complete_repo_delta(
            delta,
            integrity_problems=integrity_problems,
        )

    if target is None:
        baseline = _read_repo_baseline(root, task_id)
        baseline_records = _root_baseline_repository_records(baseline)
        if baseline_records:
            changes: list[ChangedEntry] = []
            attributed_changes: list[dict[str, str]] = []
            observed_committed_changes: list[ChangedEntry] = []
            current_task_change_count = 0
            baseline_count = 0
            current_count = 0
            baseline_conflicts: list[str] = []
            current_targets = _root_task_product_surfaces(root, layout=layout)
            matched_current_paths: set[str] = set()
            for record in baseline_records:
                repo_id = str(record.get("repo_id") or "")
                repo_path = str(record.get("repo_path") or "")
                state_path = _baseline_path(root, task_id)
                baseline_entries = _parse_baseline_entries(record.get("dirty_entries", []), state_path, root)
                baseline_count += len(baseline_entries)
                matched = next(
                    (
                        candidate
                        for candidate in current_targets
                        if candidate.display_path == repo_path and (not repo_id or candidate.id == repo_id)
                    ),
                    None,
                )
                if matched is None:
                    if repo_path:
                        changes.append(("deleted", repo_path, ""))
                        baseline_conflicts.extend(
                            f"{repo_path}/{path}"
                            for path in _entry_mutation_paths(baseline_entries)
                        )
                    continue
                matched_current_paths.add(matched.display_path)
                current, current_state = repo_changed_entries(root, matched)
                if not current_state.available:
                    integrity_problems.append(
                        Problem(
                            "error",
                            current_state.problem_code or "repo_git_unavailable",
                            f"cannot observe current repository changes: {current_state.reason}",
                            current_state.repo_path or matched.display_path,
                        )
                    )
                    current = []
                baseline_fingerprints = record.get("dirty_path_fingerprints", {})
                if not isinstance(baseline_fingerprints, dict):
                    raise RepoctlError(f"task repo dirty baseline is invalid: {state_path.relative_to(root).as_posix()}")
                current_count += len(current)
                baseline_paths = set(_entry_mutation_paths(baseline_entries))
                current_paths = set(_entry_mutation_paths(current))
                selected_baseline = {
                    **record,
                    "state_version": baseline.get("state_version"),
                }
                observation = _repository_transition_observation(
                    root,
                    target=matched,
                    baseline=selected_baseline,
                    worktree_changes=current,
                    repo_git=current_state,
                )
                integrity_problems.extend(observation.problems)
                observed_committed_changes.extend(
                    (
                        entry[0],
                        f"{repo_path}/{entry[1]}",
                        f"{repo_path}/{entry[2]}" if entry[2] else "",
                    )
                    for entry in observation.committed_changes
                )
                current_fingerprints, _fingerprint_state = _current_baseline_fingerprints(
                    root,
                    baseline=selected_baseline,
                    paths=sorted(baseline_paths | current_paths),
                    target=matched,
                )
                if not _fingerprint_state.available:
                    integrity_problems.append(
                        Problem(
                            "error",
                            _fingerprint_state.problem_code or "repo_git_unavailable",
                            f"cannot observe current stable path state: {_fingerprint_state.reason}",
                            _fingerprint_state.repo_path or matched.display_path,
                        )
                    )
                repo_changes: list[ChangedEntry] = []
                for entry in current:
                    paths = set(_entry_mutation_paths([entry]))
                    overlap = paths & baseline_paths
                    if not overlap:
                        repo_changes.append(entry)
                        continue
                    unchanged = paths <= baseline_paths and all(
                        str(baseline_fingerprints.get(path) or "") and current_fingerprints.get(path) == str(baseline_fingerprints.get(path) or "")
                        for path in overlap
                    )
                    if unchanged:
                        continue
                    repo_changes.append(entry)
                repo_conflicts = _baseline_conflicting_paths(
                    baseline_paths=baseline_paths,
                    baseline_fingerprints=baseline_fingerprints,
                    current_fingerprints=current_fingerprints,
                )
                current_task_change_count += len(repo_changes)
                remaining, attributed = attribute(matched, repo_changes, selected_baseline)
                attributed_changes.extend(attributed)
                remaining_paths = set(_entry_mutation_paths(remaining))
                baseline_only_conflicts = repo_conflicts - current_paths
                entry_conflicts = repo_conflicts & current_paths & remaining_paths
                baseline_conflicts.extend(
                    f"{repo_path}/{path}"
                    for path in sorted(baseline_only_conflicts | entry_conflicts)
                )
                changes.extend(
                    (entry[0], f"{repo_path}/{entry[1]}", f"{repo_path}/{entry[2]}" if entry[2] else "")
                    for entry in remaining
                )
            for current_only in current_targets:
                if current_only.display_path in matched_current_paths:
                    continue
                current, current_state = repo_changed_entries(root, current_only)
                if not current_state.available:
                    integrity_problems.append(
                        Problem(
                            "error",
                            current_state.problem_code or "repo_git_unavailable",
                            f"cannot observe current repository changes: {current_state.reason}",
                            current_state.repo_path or current_only.display_path,
                        )
                    )
                    current = []
                current_count += len(current)
                current_task_change_count += len(current)
                remaining, attributed = attribute(current_only, current, None)
                attributed_changes.extend(attributed)
                changes.extend(
                    (
                        entry[0],
                        f"{current_only.display_path}/{entry[1]}",
                        f"{current_only.display_path}/{entry[2]}" if entry[2] else "",
                    )
                    for entry in remaining
                )
            git_state = RepoGitState(True, repo_id="", repo_path="repos")
            return complete({
                "changes": changes,
                "baseline_available": True,
                "baseline_count": baseline_count,
                "current_count": current_count,
                "preexisting_count": max(0, current_count - current_task_change_count),
                "baseline_conflicts": sorted(set(baseline_conflicts)),
                "initial_dirty_paths": sorted(
                    f"{str(record.get('repo_path') or '')}/{path}".strip("/")
                    for record in baseline_records
                    for path in _entry_mutation_paths(_parse_baseline_entries(record.get("dirty_entries", []), _baseline_path(root, task_id), root))
                ),
                "ownership": dict(baseline.get("ownership") or {}),
                "child_attributed_changes": attributed_changes,
                "child_attributed_count": len(attributed_changes),
                "observed_committed_changes": sorted(set(observed_committed_changes)),
                "repo_git": git_state,
            })
        git_state = _no_product_repo_state()
        changes: list[ChangedEntry] = []
        attributed_changes: list[dict[str, str]] = []
        current_count = 0
        for product_target in _root_task_product_surfaces(root, layout=layout):
            current, target_state = repo_changed_entries(root, product_target)
            if not target_state.available:
                integrity_problems.append(
                    Problem(
                        "error",
                        target_state.problem_code or "repo_git_unavailable",
                        f"cannot observe current repository changes: {target_state.reason}",
                        target_state.repo_path or product_target.display_path,
                    )
                )
                current = []
            current_count += len(current)
            remaining, attributed = attribute(product_target, current, None)
            attributed_changes.extend(attributed)
            changes.extend(
                (entry[0], f"{product_target.display_path}/{entry[1]}", f"{product_target.display_path}/{entry[2]}" if entry[2] else "")
                for entry in remaining
            )
        if current_count:
            git_state = RepoGitState(True, repo_id="", repo_path="repos")
        return complete({
            "changes": changes,
            "baseline_available": False,
            "baseline_count": 0,
            "current_count": current_count,
            "preexisting_count": 0,
            "baseline_conflicts": [],
            "initial_dirty_paths": [],
            "ownership": {},
            "child_attributed_changes": attributed_changes,
            "child_attributed_count": len(attributed_changes),
            "repo_git": git_state,
        })
    current, git_state = repo_changed_entries(root, target)
    if not git_state.available:
        integrity_problems.append(
            Problem(
                "error",
                git_state.problem_code or "repo_git_unavailable",
                f"cannot observe current repository changes: {git_state.reason}",
                git_state.repo_path or target.display_path,
            )
        )
    baseline = _read_repo_baseline(root, task_id)
    if baseline is None:
        changes, attributed_changes = attribute(target, current, None)
        observation = _repository_transition_observation(
            root,
            target=target,
            baseline=None,
            worktree_changes=changes,
            repo_git=git_state,
        )
        integrity_problems.extend(observation.problems)
        return complete({
            "changes": changes,
            "observed_committed_changes": list(observation.committed_changes),
            "repository_observation": observation.to_dict(),
            "baseline_available": False,
            "baseline_count": 0,
            "current_count": len(current),
            "preexisting_count": 0,
            "baseline_conflicts": [],
            "initial_dirty_paths": [],
            "ownership": {},
            "child_attributed_changes": attributed_changes,
            "child_attributed_count": len(attributed_changes),
            "repo_git": observation.repo_git,
        })
    baseline_entries = baseline["entries"]
    baseline_fingerprints = baseline["path_fingerprints"]
    baseline_paths = set(_entry_mutation_paths(baseline_entries))
    current_paths = set(_entry_mutation_paths(current))
    current_fingerprints, fingerprint_state = _current_baseline_fingerprints(
        root,
        baseline=baseline,
        paths=sorted(baseline_paths | current_paths),
        target=target,
    )
    if not fingerprint_state.available:
        integrity_problems.append(
            Problem(
                "error",
                fingerprint_state.problem_code or "repo_git_unavailable",
                f"cannot observe current stable path state: {fingerprint_state.reason}",
                fingerprint_state.repo_path or target.display_path,
            )
        )
    ownership = baseline.get("ownership") if isinstance(baseline.get("ownership"), dict) else {}
    changes: list[ChangedEntry] = []
    for entry in current:
        paths = set(_entry_mutation_paths([entry]))
        overlap = paths & baseline_paths
        if not overlap:
            changes.append(entry)
            continue
        unchanged = paths <= baseline_paths and all(
            baseline_fingerprints.get(path) and current_fingerprints.get(path) == baseline_fingerprints.get(path)
            for path in overlap
        )
        if unchanged:
            continue
        changes.append(entry)
    baseline_conflicts = _baseline_conflicting_paths(
        baseline_paths=baseline_paths,
        baseline_fingerprints=baseline_fingerprints,
        current_fingerprints=current_fingerprints,
        ownership=ownership,
    )
    raw_changes = list(changes)
    changes, attributed_changes = attribute(target, raw_changes, baseline)
    observation = _repository_transition_observation(
        root,
        target=target,
        baseline=baseline,
        worktree_changes=changes,
        repo_git=git_state,
    )
    integrity_problems.extend(observation.problems)
    remaining_paths = set(_entry_mutation_paths(changes))
    baseline_only_conflicts = baseline_conflicts - current_paths
    entry_conflicts = baseline_conflicts & current_paths & remaining_paths
    return complete({
        "changes": changes,
        "observed_committed_changes": list(observation.committed_changes),
        "repository_observation": observation.to_dict(),
        "baseline_available": True,
        "baseline_count": len(baseline_entries),
        "current_count": len(current),
        "preexisting_count": max(0, len(current) - len(raw_changes)),
        "baseline_conflicts": sorted(baseline_only_conflicts | entry_conflicts),
        "initial_dirty_paths": sorted(baseline_paths),
        "ownership": ownership,
        "baseline_path_fingerprints": baseline_fingerprints,
        "current_path_fingerprints": current_fingerprints,
        "child_attributed_changes": attributed_changes,
        "child_attributed_count": len(attributed_changes),
        "repo_git": observation.repo_git,
    })


def start_task(root: Path, task_id: str, *, force_dirty: bool = False) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in {"todo", "blocked"}:
        raise RepoctlError("task start requires status todo or blocked; an active task baseline cannot be refreshed", code="task_already_started", path=task.rel_path)
    generated_handoff = task_handoff_is_generated_template(task)
    if task.status == "todo" and not generated_handoff:
        handoff_problems = _live_handoff_problems(task, root)
        if handoff_problems:
            problem = handoff_problems[0]
            raise RepoctlError(problem.message, code=problem.code, path=problem.path)
    if task.status == "blocked":
        handoff_problems = _live_handoff_problems(task, root)
        if handoff_problems:
            problem = handoff_problems[0]
            raise RepoctlError(problem.message, code=problem.code, path=problem.path)
    existing_state = _read_task_state(root, task.id)
    target = _target_for_task(root, task)
    repo_scoped = _repo_scoped_task(task)
    if target is None:
        product_targets = _root_task_product_surfaces(root)
        dirty = []
        for product_target in product_targets:
            lines, _state = repo_git_status(root, product_target)
            dirty.extend(f"{product_target.display_path}: {line}" for line in lines)
        git_state = RepoGitState(bool(product_targets), repo_id="", repo_path="repos") if product_targets else _no_product_repo_state()
        baseline_entries = []
    else:
        dirty, git_state = repo_git_status(root, target)
        baseline_entries, baseline_git_state = repo_changed_entries(root, target)
        if not baseline_git_state.available:
            raise RepoctlError(
                f"repo-scoped task cannot start because {baseline_git_state.reason}",
                code=baseline_git_state.problem_code or "repo_git_unavailable",
                path=baseline_git_state.repo_path or target.display_path,
            )
    if _repo_scoped_task(task) and not git_state.available:
        raise RepoctlError(f"repo-scoped task cannot start because {git_state.reason}; initialize repos/ as an independent git repository first", code="repo_git_unavailable", path=git_state.repo_path or "repos")
    if task.status == "blocked" and repo_scoped and existing_state is None:
        raise RepoctlError("blocked repo task has no initial baseline; create a new task instead of inferring the missing start state", code="repo_head_missing_at_start", path=task.rel_path)
    if dirty and repo_scoped and task.status == "todo" and existing_state is None and not force_dirty:
        raise RepoctlError("repos/ is dirty; use --force-dirty to record dirty files and continue", code="repo_dirty", path=git_state.repo_path or "repos")

    text = task.path.read_text(encoding="utf-8")
    text = replace_frontmatter_line(text, "status", "doing")
    head, _head_state = repo_git_head(root, target) if target is not None else ("", _no_product_repo_state())
    if dirty:
        entry = _dirty_entry(
            dirty,
            copy=copy,
            baseline_ref=_baseline_path(root, task.id).relative_to(root).as_posix(),
        )
    elif not git_state.available:
        entry = _git_unavailable_entry(git_state.reason, copy=copy)
    else:
        entry = f"- {utc_stamp()}: {copy['task_started']}"
    if existing_state is None:
        if target is None:
            _write_product_repo_baselines(root, task, _root_task_product_surfaces(root))
        elif git_state.available:
            entry = f"{entry}\n{_repo_head_entry(head, copy=copy)}"
            _write_repo_baseline(root, task, baseline_entries, git_state, target)
    text = append_section_entry(text, "Execution Log", entry)
    warnings: list[Problem] = []
    if dirty and not repo_scoped and not force_dirty:
        warnings.append(Problem("warning", "root_task_repo_dirty_recorded", "task started with existing repos/ dirty state recorded for baseline only", task.rel_path))
    return {
        "task": task,
        "text": text,
        "dirty": dirty,
        "repo_git": git_state,
        "warnings": warnings,
    }


def _verification_body(verification: VerificationInput) -> tuple[str, dict[str, Any]]:
    if verification.source == "external_file":
        normalized_body = _normalize_verification_artifact(verification.text)
        normalization = "strip_verification_heading_and_normalize_final_newline"
    elif verification.source == "task_section":
        normalized_body = verification.text.rstrip()
        normalization = "normalize_final_newline"
    else:
        raise RepoctlError("verification source must be external_file or task_section", code="invalid_verification_source")
    if not normalized_body.strip():
        raise RepoctlError("verification evidence must contain commands and results", code="empty_verification_file", path=verification.source_path)
    normalized = normalized_body.rstrip() + "\n"
    stored = normalized
    metadata = {
        "source": verification.source,
        "source_path": verification.source_path,
        "source_sha256": verification.source_sha256,
        "normalization": normalization,
        "normalized_sha256": _sha256_text(normalized),
        "stored_sha256": _sha256_text(stored),
        "truncated": False,
    }
    return stored, metadata


def _normalize_verification_artifact(verification: str) -> str:
    lines = verification.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+Verification(?:\s+for\b.*)?\s*$", lines[0].strip(), re.IGNORECASE):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _finalize_handoff(text: str, *, status: str, new_path: str, receipt_path: str, evidence_mode: str, copy: dict[str, Any]) -> str:
    if has_section(text, "Last Active Handoff") or has_section(text, "Closure"):
        raise RepoctlError("task already contains completion-only sections", code="duplicate_closure_section")
    section = find_section(text, "Handoff")
    handoff_body = text[section.body_start : section.end].strip()
    result = copy["closure_done"] if status == "done" else copy["closure_canceled"]
    receipt_value = f"`{receipt_path}`" if receipt_path else "none"
    closure = (
        "## Last Active Handoff\n\n"
        f"{handoff_body}\n\n"
        "## Closure\n\n"
        f"- Task result: {result}\n"
        f"- Task record at completion: `{new_path}`\n"
        f"- Repo evidence mode: `{evidence_mode}`\n"
        f"- Completion receipt: {receipt_value}\n"
        f"- Git delivery: {copy['git_delivery_outside']}\n"
    )
    suffix = text[section.end :]
    if suffix and not closure.endswith("\n\n"):
        closure += "\n"
    return text[: section.start] + closure + suffix


def validate_verification_file(root: Path, verification_file: Path) -> None:
    resolved_verification = verification_file.resolve()
    layout = repo_layout(root)
    product_roots = [target.root_path for target in layout.targets]
    seen_roots = {path.resolve() for path in product_roots}
    for candidate in layout.candidates:
        resolved_candidate = candidate.root_path.resolve()
        if resolved_candidate not in seen_roots:
            product_roots.append(candidate.root_path)
            seen_roots.add(resolved_candidate)
    if not product_roots:
        product_roots = [path for path in (root / "repos",) if path.exists()]
    for product_root in product_roots:
        try:
            resolved_verification.relative_to(product_root.resolve())
        except (OSError, ValueError):
            continue
        rel = product_root.relative_to(root).as_posix() if product_root.is_relative_to(root) else product_root.as_posix()
        raise RepoctlError(f"verification file is an input artifact; keep it outside {rel}/ so finish records durable evidence in the task without creating product metadata residue", code="verification_file_inside_repo", path=verification_file.as_posix())
    if not verification_file.is_file():
        raise RepoctlError(f"verification file cannot be read: {verification_file}", code="missing_verification_file", path=verification_file.as_posix())


def finish_task(root: Path, task_id: str, *, verification: VerificationInput, meta_gate: dict[str, Any] | None = None, repo_delta: dict[str, Any] | None = None, allow_head_changed: bool = False) -> dict[str, Any]:
    """Validate finish and build its write set without mutating the workspace."""
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in LIVE:
        raise RepoctlError("task finish requires a live status")
    _require_no_integrity_problems(repo_delta)
    receipt_path = _completion_receipt_path(root, task.id)
    if path_problem := _completion_receipt_path_problem(root, receipt_path):
        raise RepoctlError(path_problem.message, code=path_problem.code, path=path_problem.path)
    if receipt_path.exists():
        raise RepoctlError("task completion receipt already exists and will not be overwritten", code="completion_receipt_exists", path=receipt_path.relative_to(root).as_posix())
    repo_scoped = _repo_scoped_task(task)
    area = str(task.frontmatter.get("area") or "")
    target = _target_for_task(root, task)
    alignment_problem = task_discovery_outcome_alignment_problem(root, task, target=target)
    if alignment_problem is not None:
        raise RepoctlError(
            alignment_problem.message,
            code=alignment_problem.code,
            path=alignment_problem.path,
        )
    discovery_outcome = completion_outcome_projection(root, task.id)
    if discovery_outcome is not None:
        try:
            validate_completion_outcome(discovery_outcome)
        except ValueError as exc:
            raise RepoctlError(
                f"task Discovery completion outcome is invalid: {exc}",
                code="discovery_completion_outcome_invalid",
                path=task.rel_path,
            ) from exc
    _assert_repo_baseline_matches(root, task, target)
    repo_changed = bool(meta_gate and meta_gate.get("status") == "passed" and meta_gate.get("scope") == "changed")
    start_head = _repo_head_from_state(root, task)
    if target is None:
        current_head, current_head_state = "", _no_product_repo_state()
    else:
        current_head, current_head_state = repo_git_head(root, target)
    if (repo_changed or repo_scoped) and current_head_state.available and meta_gate and meta_gate.get("reason") != "no_repo_directory":
        if not start_head:
            raise RepoctlError("task cannot finish because repo head at start was not recorded; restart the task with repoctl task start", code="repo_head_missing_at_start", path=task.rel_path)
        if current_head != start_head and not allow_head_changed:
            raise RepoctlError("repo HEAD changed since task start; finish before committing repos/ changes so changed-file gates can validate the actual work", code="repo_head_changed_since_start", path=task.rel_path)
        if current_head != start_head and allow_head_changed and not (repo_delta or {}).get("committed_range"):
            raise RepoctlError("task finish with changed repo HEAD requires committed diff evidence from the recorded task start head", code="committed_diff_required", path=task.rel_path)
    if repo_changed and area not in REPO_REQUIRED_AREAS and not str(task.frontmatter.get("repo_id") or ""):
        raise RepoctlError("task that changes repos/ must set area to one of: repo, backend, frontend, infra, mobile or set repo_id for the selected product repository", code="repository_selector_required", path=task.rel_path)
    if repo_changed and not discovery_recorded(task, target):
        raise RepoctlError("repo task must record candidate discovery before finish", code="placeholder_discovery", path=task.rel_path)
    if repo_scoped and target is None:
        raise RepoctlError("repo-scoped task cannot finish because product repository is missing; initialize repos/ as the product repository or use area docs/ops for root-only work", code="repository_not_found", path=task.rel_path)
    finish_timestamp = utc_stamp()
    finish_event_timestamp = _utc_event_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=finish_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task finish would create non-monotonic Execution Log timestamps; {timestamp_problem}", code="execution_log_timestamp_order", path=task.rel_path)
    if not verification.text.strip():
        raise RepoctlError("verification evidence must contain the commands run and their results", code="empty_verification_file", path=verification.source_path or task.rel_path)
    all_tasks = load_tasks(root, include_archived=False)
    children = children_by_parent(all_tasks)
    live_children = [child for child in children.get(task.id, []) if child.status in LIVE]
    if live_children:
        raise RepoctlError("cannot finish parent task while live children remain", code="live_children_block_finish", path=task.rel_path)

    text = task.path.read_text(encoding="utf-8")
    verification_body, verification_metadata = _verification_body(verification)
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
    receipt_writes: list[tuple[Path, str]] = []
    if is_parent or not is_child:
        archived = True
        new_path = f"docs/archive/tasks/{task.path.name}"
        moves.append((task.path, root / new_path))
        if is_parent:
            for child in children.get(task.id, []):
                if not child.archived:
                    child_new_path = f"docs/archive/tasks/{child.path.name}"
                    child_archive_target = root / child_new_path
                    child_text = child.path.read_text(encoding="utf-8")
                    archive_texts[child_archive_target] = child_text
                    moves.append((child.path, child_archive_target))
    try:
        evidence_mode = _CompletionEvidenceMode(str((repo_delta or {}).get("evidence_mode") or "none"))
    except ValueError as exc:
        raise RepoctlError("task finish produced an invalid repository evidence mode", code="invalid_repo_evidence_mode") from exc
    evidence_attribution = _COMPLETION_ATTRIBUTION_BY_MODE[evidence_mode]
    state = _read_task_state(root, task.id)
    state_version = (state or {}).get("schema_version")
    initial = (state or {}).get("initial") if isinstance((state or {}).get("initial"), dict) else {}
    started_at = str(initial.get("started_at") or "")
    path_transitions: list[dict[str, Any]] | None = [] if target is None and state_version == TASK_STATE_SCHEMA_VERSION else None
    if target is not None and state_version == TASK_STATE_SCHEMA_VERSION:
        path_transitions, started_at = _build_task_path_transitions(
            root,
            task,
            target=target,
            entries=list((repo_delta or {}).get("changes") or []),
            mode=evidence_mode,
            observed_head=current_head,
        )
        if path_transitions is None:
            raise RepoctlError(
                "current task state cannot produce stable completion transitions",
                code="transition_evidence_incomplete",
                path=task.rel_path,
            )
    if discovery_outcome is not None and (
        path_transitions is None or not _valid_event_stamp(started_at)
    ):
        raise RepoctlError(
            "task Discovery outcome cannot be frozen without current task-start transition evidence; start a todo task first or use a follow-up with a fresh baseline",
            code="transition_evidence_incomplete",
            path=task.rel_path,
        )
    receipt_schema_version = (
        COMPLETION_RECEIPT_SCHEMA_VERSION
        if discovery_outcome is not None
        else TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION
        if path_transitions is not None and _valid_event_stamp(started_at)
        else LEGACY_COMPLETION_RECEIPT_SCHEMA_VERSION
    )
    receipt_rel = receipt_path.relative_to(root).as_posix()
    text = _finalize_handoff(text, status="done", new_path=new_path, receipt_path=receipt_rel, evidence_mode=evidence_mode.value, copy=copy)
    if moves:
        archive_texts[root / new_path] = text
    archive_writes = archive_locator_writes(root, moves, all_tasks)
    changed_entries = [_entry_to_dict(entry) for entry in (repo_delta or {}).get("changes", [])]
    repo_evidence = {
        "mode": evidence_mode.value,
        "attribution": evidence_attribution.value,
        "start_head": start_head,
        "observed_head": current_head,
        "git_available": bool(current_head_state.available),
        "diff_fingerprint_sha256": str((repo_delta or {}).get("diff_fingerprint_sha256") or ""),
        "fingerprint_manifest": (repo_delta or {}).get("evidence_manifest") or {},
        "ownership": (repo_delta or {}).get("ownership") or {},
        "meta_gate": meta_gate or {},
        "delta": {
            "changed_count": len(changed_entries),
            "current_count": int((repo_delta or {}).get("current_count") or 0),
            "baseline_available": bool((repo_delta or {}).get("baseline_available")),
            "baseline_count": int((repo_delta or {}).get("baseline_count") or 0),
            "preexisting_count": int((repo_delta or {}).get("preexisting_count") or 0),
            "baseline_conflicts": list((repo_delta or {}).get("baseline_conflicts") or []),
            "scope": (repo_delta or {}).get("scope") or {},
        },
    }
    if receipt_schema_version >= TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION:
        repo_evidence["path_transitions"] = path_transitions
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": receipt_schema_version,
        "task_id": task.id,
        "repo_id": target.id if target is not None and _repo_scoped_task(task) else "",
        "status": "done",
        "completed_at": finish_timestamp,
        "task_path_at_completion": new_path,
        "content_sha256": _sha256_text(text),
        "changed_entries": changed_entries,
        "repo_evidence": repo_evidence,
        "verification": verification_metadata,
    }
    if receipt_schema_version >= TRANSITION_COMPLETION_RECEIPT_SCHEMA_VERSION:
        receipt["started_at"] = started_at
        receipt["completed_event_at"] = finish_event_timestamp
    if discovery_outcome is not None:
        receipt["discovery_outcome"] = discovery_outcome
    receipt_text = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt_writes.append((receipt_path, receipt_text))
    catalogue_writes = prepare_completion_sidecar_writes(
        root,
        receipt=receipt,
        receipt_path=receipt_rel,
        receipt_text=receipt_text,
        artifact_path=new_path,
        artifact_text=text,
    )
    receipt_writes.extend(catalogue_writes.writes)
    return {
        "task": task,
        "text": text,
        "old_path": old_path,
        "new_path": new_path,
        "archived": archived,
        "moves": moves,
        "archive_texts": archive_texts,
        "truncated": bool(verification_metadata["truncated"]),
        "receipt_path": receipt_path,
        "receipt_text": receipt_text,
        "receipt_writes": receipt_writes,
        "archive_writes": archive_writes,
        "receipt": receipt,
    }


def cancel_task(root: Path, task_id: str, *, reason: str, residue_paths: list[str], baseline_conflicts: list[str]) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status not in LIVE:
        raise RepoctlError("task cancel requires a live status")
    finish_timestamp = utc_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=finish_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task cancel would create non-monotonic Execution Log timestamps; {timestamp_problem}")
    all_tasks = load_tasks(root, include_archived=False)
    children = children_by_parent(all_tasks)
    live_children = [child for child in children.get(task.id, []) if child.status in LIVE]
    if live_children:
        raise RepoctlError("cannot cancel parent task while live children remain")

    text = task.path.read_text(encoding="utf-8")
    transition_entry = copy["task_canceled"].format(reason=reason) + "".join(
        f"; {key}={json.dumps(paths, ensure_ascii=False)}"
        for key, paths in (("residue_paths", residue_paths), ("baseline_conflicts", baseline_conflicts))
        if paths
    )
    text = append_section_entry(text, "Execution Log", f"- {finish_timestamp}: {transition_entry}")
    text = replace_frontmatter_line(text, "status", "canceled")

    is_child = bool(task.parent)
    is_parent = task.id in children
    old_path = task.rel_path
    archived = False
    new_path = old_path
    moves: list[tuple[Path, Path]] = []
    archive_texts: dict[Path, str] = {}
    receipt_writes: list[tuple[Path, str]] = []
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
                    archive_texts[target] = child_text
                    moves.append((child.path, target))
    text = _finalize_handoff(text, status="canceled", new_path=new_path, receipt_path="", evidence_mode="none", copy=copy)
    if moves:
        archive_texts[root / new_path] = text
    archive_writes = archive_locator_writes(root, moves, all_tasks)
    return {
        "task": task,
        "text": text,
        "old_path": old_path,
        "new_path": new_path,
        "archived": archived,
        "moves": moves,
        "archive_texts": archive_texts,
        "receipt_writes": receipt_writes,
        "archive_writes": archive_writes,
    }


def block_task(root: Path, task_id: str, *, reason: str) -> dict[str, Any]:
    task = resolve_live_task(root, task_id)
    copy = _copy(_task_language(root, task))
    if task.status == "blocked":
        raise RepoctlError("task block requires status todo or doing", code="task_already_blocked", path=task.rel_path)
    if task.status not in LIVE:
        raise RepoctlError("task block requires a live status")
    block_timestamp = utc_stamp()
    timestamp_problem = _execution_log_timestamp_problem(task, now=block_timestamp)
    if timestamp_problem:
        raise RepoctlError(f"task block would create non-monotonic Execution Log timestamps; {timestamp_problem}")
    text = task.path.read_text(encoding="utf-8")
    text = append_section_entry(text, "Execution Log", f"- {block_timestamp}: {copy['task_blocked'].format(reason=reason)}")
    text = replace_frontmatter_line(text, "status", "blocked")
    return {
        "task": task,
        "text": text,
        "old_path": task.rel_path,
        "new_path": task.rel_path,
        "archived": False,
        "keep_board": True,
        "moves": [],
        "archive_texts": {},
    }


def _escape_yaml_double(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validate_title(title: str) -> None:
    if "\n" in title or "\r" in title:
        raise RepoctlError("task title must be a single line", code="invalid_title")


def _slug_from_title(title: str) -> str:
    try:
        title.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RepoctlError("non-ASCII title requires explicit --slug", code="missing_slug") from exc
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        raise RepoctlError("title cannot be converted to a slug; pass --slug", code="missing_slug")
    return slug


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise RepoctlError("invalid slug; use lowercase kebab-case [a-z0-9-]", code="invalid_slug")


def _validate_parent_id(parent: str) -> None:
    if parent and not ID_RE.match(parent):
        raise RepoctlError("invalid parent id format; expected T-YYYYMMDDHHMMSSZ", code="invalid_parent_id")


def _validate_area(area: str) -> None:
    if area not in AREAS:
        raise RepoctlError("invalid area; use one of: repo, backend, frontend, infra, docs, ops, mobile. Put detailed product surfaces in the title, Work Area, or Discovery instead of --area", code="invalid_area")


def _validate_repo_ref(repo_ref: str) -> None:
    if repo_ref.strip() in {".", "./", "root", "workspace"}:
        raise RepoctlError("repo_ref is only an advisory repos/ branch or worktree hint; omit --repo-ref when no product repo is selected", code="invalid_repo_ref")


def validate_repo_ref_area(area: str, repo_ref: str) -> None:
    if repo_ref and area not in REPO_REQUIRED_AREAS:
        raise RepoctlError("--repo-ref is only valid with repos/ work, so --area must be one of repo, backend, frontend, infra, mobile", code="repo_ref_non_repo_area")


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
    return bool(str(task.frontmatter.get("repo_id") or "").strip()) or area in {"repo", "backend", "frontend", "infra", "mobile"}


def _repo_discovery_paths(values: list[str]) -> list[str]:
    return [normalized for value in values if (normalized := _normalize_discovery_path(value))]


def _discovery_paths_outside_target(values: list[str], target: RepoTarget) -> list[str]:
    paths = _repo_discovery_paths(values)
    if not paths:
        return []
    prefix = f"{target.display_path.rstrip('/')}/"
    invalid: list[str] = []
    for path in paths:
        normalized = normalize_repo_path(path)
        if not normalized or not normalized.startswith(prefix):
            invalid.append(path)
    return invalid


def discovery_recorded(task: Task, target: RepoTarget | None = None) -> bool:
    fields = task_discovery_values(task)
    required: dict[str, list[str]] = {}
    for key in ("Candidate query", "Candidate files reviewed", "Chosen files"):
        values = fields.get(key, [])
        if not values:
            return False
        required[key] = values
    reviewed_paths = _repo_discovery_paths(required["Candidate files reviewed"])
    chosen_paths = _repo_discovery_paths(required["Chosen files"])
    if len(reviewed_paths) != len(required["Candidate files reviewed"]) or len(chosen_paths) != len(required["Chosen files"]):
        return False
    if target is not None and (
        _discovery_paths_outside_target(reviewed_paths, target) or _discovery_paths_outside_target(chosen_paths, target)
    ):
        return False
    return True


def _task_workspace_root(task: Task) -> Path:
    root = task.path
    for _part in Path(task.rel_path).parts:
        root = root.parent
    return root


def _live_handoff_problems(task: Task, root: Path) -> list[Problem]:
    labels = ("Next exact step", "First file to open", "First command to run", "Done when")
    try:
        fields = parse_labeled_list_section(
            task.body,
            "Handoff",
            labels,
        )
    except RepoctlError:
        return [Problem("error", "missing_handoff", "live task must contain a Handoff section", task.rel_path)]
    invalid_fields = [
        label
        for label in labels
        if len(fields.get(label, [])) != 1 or not _strip_ticks(fields[label][0]).strip()
    ]
    if invalid_fields:
        return [
            Problem(
                "error",
                "invalid_handoff_structure",
                "live Handoff must contain exactly one non-empty value for each canonical field: " + ", ".join(labels),
                task.rel_path,
            )
        ]
    first_file_values = fields.get("First file to open", [])
    value = _strip_ticks(first_file_values[0])
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        return [Problem("error", "invalid_handoff_first_file", "Handoff First file must be a workspace-relative path", task.rel_path)]
    candidate = root / path
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve()
    except OSError:
        return [Problem("error", "invalid_handoff_first_file", "Handoff First file cannot be resolved", task.rel_path)]
    if root_resolved not in (candidate_resolved, *candidate_resolved.parents):
        return [Problem("error", "handoff_first_file_outside_workspace", "Handoff First file resolves outside the workspace", task.rel_path)]
    if not candidate.is_file():
        return [Problem("error", "handoff_first_file_missing", f"Handoff First file does not exist: {value}", task.rel_path)]
    return []


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
            if candidate.startswith(("docs/", "repos/")) or candidate in {"AGENTS.md", "README.md", "CLAUDE.md"}:
                paths.append(candidate)
    return paths


def _repo_head_from_state(root: Path, task: Task) -> str:
    baseline = _read_repo_baseline(root, task.id)
    if baseline is None:
        return ""
    return str(baseline.get("head") or "")


def task_repo_head_at_start(root: Path, task_id: str) -> str:
    task = resolve_task(root, task_id)
    return _repo_head_from_state(root, task)


def _assert_repo_baseline_matches(root: Path, task: Task, target: RepoTarget | None) -> None:
    baseline = _read_repo_baseline(root, task.id)
    if baseline is None or not baseline.get("repo_id"):
        return
    matched_target = target
    if matched_target is None:
        matched_target = next(
            (
                candidate
                for candidate in _root_task_product_surfaces(root)
                if candidate.id == baseline.get("repo_id") and candidate.display_path == baseline.get("repo_path")
            ),
            None,
        )
    if matched_target is None:
        raise RepoctlError("task started with a product repository baseline, but that repository is no longer present", code="repo_target_changed_since_start", path=task.rel_path)
    if problem := _repository_target_identity_problem(target=matched_target, baseline=baseline):
        raise RepoctlError(problem.message, code=problem.code, path=task.rel_path)


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
    repo_id: str,
    parent: str,
    backlog_id: str = "",
    language: str = "en",
) -> str:
    copy = _copy(language)
    area_hint = area or copy["area_unspecified"]
    repo_scoped = bool(repo_id or area in REPO_REQUIRED_AREAS)
    repo_hint = repo_id or ("main" if repo_scoped else "none")
    scope_line = f"- Repository: `{repo_hint}`\n" if repo_scoped else "- Product repository: none selected\n"
    work_area = (
        f"- Task record: `{rel_path.as_posix()}`\n"
        f"{scope_line}"
        f"- Area hint: {area_hint}\n"
        f"- Primary surface: {copy['work_area_primary']}\n"
    )
    if repo_ref:
        work_area += f"- Repo ref hint: `{repo_ref}`\n"
    if task_type == "parent":
        goal = copy["parent_goal"].format(title=title) + "\n"
        plan = _bullet_lines(copy["parent_plan"])
        handoff = _render_created_handoff(
            copy=copy,
            task_type="parent",
            title=title,
            task_id=task_id,
            task_path=rel_path.as_posix(),
            repo_hint=repo_hint,
        )
        text = replace_section(text, "Live Child Tasks", f"{copy['live_child_summary']}\n")
        text = replace_section(text, "Non-Live Child Tasks", f"{copy['non_live_child_summary']}\n")
        text = replace_section(text, "Shared Interfaces / Decisions", f"- {copy['shared_decisions']}\n")
        text = replace_section(text, "Integration Done When", f"- {copy['integration_done']}\n")
    else:
        goal = copy["task_goal"].format(title=title) + "\n"
        scope = _bullet_lines(copy["task_scope"] if repo_scoped else copy["root_scope"])
        handoff = _render_created_handoff(
            copy=copy,
            task_type="task",
            title=title,
            task_id=task_id,
            task_path=rel_path.as_posix(),
            repo_hint=repo_hint,
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
    if task_type == "parent":
        text = replace_section(text, "In Scope", _bullet_lines(copy["in_scope"] if repo_scoped else copy["root_in_scope"]))
        text = replace_section(text, "Out of Scope", _bullet_lines(copy["out_of_scope"]))
        text = replace_section(text, "Plan", plan)
    else:
        text = replace_section(text, "Scope", scope)
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
    repo_id: str = "",
    backlog_id: str = "",
    follow_up_of: str = "",
) -> Task:
    if not (root / LOCK_REL).is_dir():
        raise RepoctlError(f"task creation requires repoctl lock: {LOCK_REL}", code="task_lock_required", path=LOCK_REL.as_posix())
    _validate_title(title)
    _validate_area(area)
    _validate_repo_ref(repo_ref)
    if task_type not in {"task", "parent"}:
        raise RepoctlError("--type must be 'task' or 'parent'")
    if task_type == "parent" and (repo_id or repo_ref or area in REPO_REQUIRED_AREAS):
        raise RepoctlError(
            "parent tasks are root coordination only; create repo-scoped child tasks for product work",
            code="parent_repo_scope_forbidden",
        )
    validate_repo_ref_area(area, repo_ref)
    if repo_id and not re.match(r"^[a-z][a-z0-9_-]*$", repo_id):
        raise RepoctlError("invalid repo_id; use lowercase [a-z0-9_-] starting with a letter", code="invalid_repo_id")
    if not repo_id and area in REPO_REQUIRED_AREAS:
        target = default_repo_target(root)
        if target is not None:
            repo_id = target.id
        else:
            raise RepoctlError("product task requires a selected product repository; initialize repos/ or pass --repo-id for configured repositories", code="repository_selector_required")
    if repo_id:
        layout = repo_layout(root)
        if not layout.registry_ready:
            raise RepoctlError("repository identities are unbound; run repoctl repo adopt before mutating product repositories", code="repository_identity_unbound")
        if not any(target.id == repo_id for target in layout.targets):
            raise RepoctlError(f"repository not found: {repo_id}", code="repository_not_found")
    if task_type == "parent" and parent:
        raise RepoctlError("parent tasks cannot have a parent id", code="parent_cannot_have_parent")
    _validate_parent_id(parent)
    _validate_parent_id(follow_up_of)
    if parent:
        try:
            parent_task = resolve_live_task(root, parent)
        except RepoctlError as exc:
            if exc.code != "task_not_found":
                raise
            raise RepoctlError(f"parent task not found: {parent}", code="parent_task_not_found")
        if parent_task.status not in LIVE or not is_parent_task(parent_task):
            raise RepoctlError(f"parent task is not a live coordinating parent: {parent}", code="parent_target_not_coordinator")
    if follow_up_of:
        previous = resolve_task(root, follow_up_of)
        if previous.status not in NON_LIVE:
            raise RepoctlError("--follow-up-of requires a done or canceled task", code="follow_up_task_still_live", path=previous.rel_path)
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
        if (
            list((root / "docs/tasks").glob(f"{task_id}--*.md"))
            or archive_locator_path(root, task_id).exists()
        ):
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
        text = _replace_exact(text, 'repo_id: ""', f'repo_id: "{_escape_yaml_double(repo_id)}"')
        text = _replace_exact(text, 'area: ""', f'area: "{_escape_yaml_double(area)}"')
        text = _replace_exact(text, 'parent: ""', f'parent: "{_escape_yaml_double(parent)}"')
        language = document_language(root)
        follow_up_line = f'follow_up_of: "{_escape_yaml_double(follow_up_of)}"\n' if follow_up_of else ""
        text = text.replace("depends_on: []\n", f'depends_on: []\n{follow_up_line}document_language: "{_escape_yaml_double(language)}"\n', 1)
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
            repo_id=repo_id,
            parent=parent,
            backlog_id=backlog_id,
            language=language,
        )
        if follow_up_of:
            text = append_section_entry(text, "Work Area", f"- Follow-up of: `{follow_up_of}`")
        atomic_write(path, text)
        return load_task(path, root)
    raise RepoctlError("failed to reserve unique task id after 20 retries")


def live_tasks(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if not task.archived and task.status in LIVE]


def select_task_for_resume(tasks: list[Task]) -> TaskResumeSelection:
    candidates = tuple(sorted(live_tasks(tasks), key=lambda task: task.rel_path))
    if not candidates:
        return TaskResumeSelection(TaskResumeSelectionStatus.NO_LIVE, 0, None, ())
    if len(candidates) == 1:
        return TaskResumeSelection(TaskResumeSelectionStatus.SINGLE_LIVE, 1, candidates[0], ())
    return TaskResumeSelection(TaskResumeSelectionStatus.AMBIGUOUS, len(candidates), None, candidates)


def children_by_parent(tasks: list[Task]) -> dict[str, list[Task]]:
    result: dict[str, list[Task]] = {}
    for task in tasks:
        if task.parent:
            result.setdefault(task.parent, []).append(task)
    return result


def validate_live_task_states(root: Path, tasks: list[Task]) -> list[Problem]:
    problems: list[Problem] = []
    try:
        layout = repo_layout(root)
    except RepoctlError:
        layout = None
    for task in live_tasks(tasks):
        path = _baseline_path(root, task.id)
        if path.is_file():
            try:
                _read_task_state(root, task.id)
            except RepoctlError as exc:
                problems.append(Problem("error", exc.code or "task_state_invalid", str(exc), exc.path or path.relative_to(root).as_posix()))
        resume_path = _resume_binding_path(root, task.id)
        if resume_path.is_file():
            try:
                load_task_resume_binding(root, task.id)
            except RepoctlError as exc:
                problems.append(
                    Problem(
                        "error",
                        exc.code or "task_resume_binding_invalid",
                        str(exc),
                        exc.path or resume_path.relative_to(root).as_posix(),
                    )
                )
        if layout is not None:
            try:
                target = _target_for_task(root, task, layout=layout)
                alignment_problem = task_discovery_outcome_alignment_problem(root, task, target=target)
            except RepoctlError as exc:
                problems.append(
                    Problem(
                        "error",
                        exc.code or "discovery_outcome_state_invalid",
                        str(exc),
                        exc.path or task.rel_path,
                    )
                )
            else:
                if alignment_problem is not None:
                    problems.append(alignment_problem)
    return problems


def validate_tasks(tasks: list[Task], *, include_archived_warnings: bool = False) -> list[Problem]:
    problems: list[Problem] = []
    ids = {task.id for task in tasks if task.id}
    children = children_by_parent(tasks)

    def append_warning(task: Task, code: str, message: str, path: str | None = None) -> None:
        if task.archived and not include_archived_warnings:
            return
        problems.append(Problem("warning", code, message, path or task.rel_path))

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
        follow_up_of = str(task.frontmatter.get("follow_up_of") or "")
        if follow_up_of:
            if not ID_RE.match(follow_up_of):
                problems.append(Problem("error", "invalid_follow_up", "follow_up_of must be a task id", task.rel_path))
            elif follow_up_of not in ids:
                problems.append(Problem("error", "missing_follow_up_task", f"follow_up_of task not found: {follow_up_of}", task.rel_path))
            else:
                previous = next((candidate for candidate in tasks if candidate.id == follow_up_of), None)
                if previous is not None and previous.status in LIVE:
                    problems.append(Problem("error", "follow_up_task_still_live", "follow_up_of must reference a done or canceled task", task.rel_path))
        repo_id = task.frontmatter.get("repo_id")
        if repo_id not in (None, "") and (not isinstance(repo_id, str) or not re.match(r"^[a-z][a-z0-9_-]*$", repo_id)):
            problems.append(Problem("error", "invalid_repo_id", "repo_id must be lowercase [a-z0-9_-] starting with a letter", task.rel_path))
        if _repo_scoped_task(task) and task.status in LIVE and not discovery_recorded(task):
            append_warning(
                task,
                "missing_discovery_evidence",
                "repo-scoped task needs structured Discovery fields: Candidate query, Candidate files reviewed, and Chosen files. Prefer `repoctl task discovery add`; free-form prose is not enough.",
            )
        try:
            task_discovery_result_selections(task)
        except RepoctlError as exc:
            problems.append(Problem("error", exc.code, str(exc), task.rel_path))
        root = _task_workspace_root(task)
        if task.status in LIVE and not task.archived:
            problems.extend(_live_handoff_problems(task, root))
            if task_handoff_is_generated_template(task):
                append_warning(
                    task,
                    "task_handoff_generated_template",
                    "replace the repoctl-generated Handoff with reviewed task-specific restart instructions before binding",
                )
        for context_path in _context_doc_paths(task):
            if not (root / context_path).exists():
                append_warning(task, "missing_context_doc", f"Context Docs path does not exist: {context_path}", context_path)
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
