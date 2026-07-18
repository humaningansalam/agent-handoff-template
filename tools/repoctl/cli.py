from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any

from .board import append_backlog_item, backlog_warnings, parse_board, read_backlog_items, remove_backlog_item, render_board, resolve_backlog_item, check_board
from .code_index import build_code_index
from .context import build_context_bundle, compact_context_bundle, render_context_markdown
from .context_benchmark import compare_context_benchmarks, materialize_context_benchmark_corpus, run_context_benchmark
from .context_task_pack import build_task_context_pack, compact_task_context_pack, compare_task_context_pack_benchmarks, compare_task_context_packs, materialize_task_context_pack_benchmark_tasks, render_task_context_pack_markdown, run_task_context_pack_benchmark
from .git import repo_commit_range_entries, repo_evidence_fingerprint, repo_git_head, repo_is_ancestor
from .graph import query_graph
from .graph_model import digest_data
from .graph_store import compact_graph_freshness, graph_materialization_freshness, load_materialized_graph, materialize_graph
from .graph_structured_relations import STRUCTURED_EDGE_KIND
from .io import RepoctlError, atomic_write, find_workspace_root, repoctl_lock
from .knowledge_candidates import approve_knowledge_candidate, build_knowledge_candidate, build_knowledge_candidate_from_pack, build_knowledge_candidate_from_receipt, check_all_knowledge_candidates, check_knowledge_candidate, check_knowledge_records, deprecate_knowledge_record, knowledge_status, list_knowledge_candidates, list_knowledge_events, query_knowledge_records, refresh_knowledge_candidate, refresh_knowledge_record_candidate, refresh_stale_knowledge_candidates, reject_knowledge_candidate, show_knowledge_candidate, show_knowledge_event, show_knowledge_record
from .knowledge_render import render_knowledge
from .meta import check_meta, exclude_path, init_store, meta_inventory, meta_query, meta_status, meta_suggest, move_annotation, remove_annotation, set_annotation, show_annotation
from .markdown import find_section
from .repositories import RepoTarget, adopt_repositories, default_repo_target, repo_check_problems, repo_layout, require_repo_target
from .tasks import Problem, REPO_REQUIRED_AREAS, VerificationInput, append_task_log, block_task, cancel_task, collect_completion_receipts, committed_range_baseline_conflicts, create_task_file, discovery_recorded, discovery_scope_delta, finish_task, load_tasks, live_tasks, repo_changes_since_task_start, resolve_task, resolve_task_baseline_ownerships, start_task, task_baseline_ownership_evidence, task_repo_head_at_start, update_task_discovery, validate_live_task_states, validate_tasks, validate_verification_file
from .upgrade import apply_upgrade, plan_upgrade, upgrade_status, write_plan


class RepoctlArgparseError(RuntimeError):
    pass


class GraphQueryIntent(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    TASK = "task"
    GENERIC = "generic"


class ResultScope(StrEnum):
    TASK = "task"
    WORKSPACE_CONTROL_PLANE = "workspace_control_plane"


class ProductReadiness(StrEnum):
    NOT_EVALUATED = "not_evaluated"


class FieldGateApplicability(StrEnum):
    REPOCTL_RELEASE_CANDIDATE = "repoctl_release_candidate"


class NextActionKind(StrEnum):
    BASELINE_OWNERSHIP_RESOLUTION = "baseline_ownership_resolution"
    TASK_SCOPE_REVIEW = "task_scope_review"


class BaselineOwnership(StrEnum):
    TASK = "task"
    PREEXISTING = "preexisting"


class TaskScopeResolution(StrEnum):
    ADD_TO_CHOSEN = "add_to_chosen"
    REVERT_CHANGE = "revert_change"
    MOVE_TO_FOLLOW_UP = "move_to_follow_up"


COMPACT_PATH_LIMIT = 20


class RepoctlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RepoctlArgparseError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status:
            raise RepoctlArgparseError((message or "argument parsing failed").strip())
        super().exit(status, message)


def _json(data: Any, *, compact: bool = False) -> None:
    _complete_json_envelope(data)
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _complete_json_envelope(data: Any) -> None:
    if isinstance(data, dict) and "ok" in data:
        envelope_keys = {"ok", "command", "data", "warnings", "problems", "next_actions"}
        unknown_keys = sorted(set(data) - envelope_keys)
        if unknown_keys:
            raise RepoctlError(
                f"JSON envelope contains command-specific top-level fields: {', '.join(unknown_keys)}",
                code="invalid_json_envelope",
            )
        if not str(data.get("command") or ""):
            raise RepoctlError("JSON envelope is missing command identity", code="invalid_json_envelope")
        if not isinstance(data.get("data"), dict):
            raise RepoctlError("JSON envelope data must be an object", code="invalid_json_envelope")
        data.setdefault("warnings", [])
        data.setdefault("problems", [])
        data.setdefault("next_actions", _next_actions_for_problems([*data.get("problems", []), *data.get("warnings", [])], data=data.get("data", data)))


def _workspace_root_or_cwd() -> Path:
    try:
        return find_workspace_root()
    except Exception:
        return Path.cwd()


def _version_data(root: Path) -> dict[str, Any]:
    pyproject_version = _read_pyproject_version(root / "pyproject.toml")
    manifest_version = ""
    manifest_path = root / "repoctl-upgrade-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                manifest_version = str(manifest.get("version") or "")
        except (OSError, json.JSONDecodeError):
            manifest_version = ""
    return {
        "version": pyproject_version or manifest_version or "unknown",
        "pyproject_version": pyproject_version,
        "manifest_version": manifest_version,
        "workspace_root": root.as_posix(),
        "manifest_path": "repoctl-upgrade-manifest.json" if manifest_path.exists() else "",
    }


def _read_pyproject_version(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_project = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("[") and stripped.endswith("]"):
            return ""
        if in_project and stripped.startswith("version"):
            _key, _sep, value = stripped.partition("=")
            return value.strip().strip('"')
    return ""


def _wants_json_output(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False)) or str(getattr(args, "format", "")) == "json"


def _workspace_output_path(root: Path, output: str, *, code: str) -> tuple[Path | None, Problem | None]:
    path = Path(output)
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None, Problem("error", code, "output artifact must stay inside the workspace", output)
    return path, None


def _problem_code(problem: Any) -> str:
    if isinstance(problem, Problem):
        return problem.code
    if isinstance(problem, dict):
        return str(problem.get("code") or "")
    return ""


def _problem_path(problem: Any) -> str:
    if isinstance(problem, Problem):
        return problem.path or ""
    if isinstance(problem, dict):
        return str(problem.get("path") or "")
    return ""


def _mapping_at(data: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    current: Any = data or {}
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _next_actions_for_problems(problems: list[Any], *, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    task_id = str((data or {}).get("task_id") or "T-...")

    def add(
        label: str,
        *,
        command: str = "",
        path: str = "",
        kind: NextActionKind | None = None,
        source: str = "",
        choices: list[StrEnum] | None = None,
        targets: list[str] | None = None,
    ) -> None:
        action: dict[str, Any] = {"label": label}
        if command:
            action["command"] = command
        if path:
            action["path"] = path
        if kind is not None:
            action["kind"] = kind
        if source:
            action["source"] = source
        if choices:
            action["choices"] = list(choices)
        if targets:
            action["targets"] = list(targets)
        key = json.dumps(action, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            actions.append(action)

    for problem in problems:
        code = _problem_code(problem)
        path = _problem_path(problem)
        if code == "missing_verification_file":
            add("Complete task Verification", path=path or f"docs/tasks/{task_id}.md")
            add("Retry finish", command=f"./scripts/repoctl task finish {task_id} --json")
            add("Use an external verification artifact", command=f"./scripts/repoctl task finish {task_id} --verification-file /tmp/{task_id}-verification.md --json")
        elif code == "verification_file_inside_repo":
            add("Move verification evidence outside repos/", command=f"cp {path or 'repos/...'} /tmp/{task_id}-verification.md")
        elif code in {"missing_discovery_evidence", "placeholder_discovery"}:
            add("Record task discovery evidence", command=f"./scripts/repoctl task discovery add {task_id} --query '<query>' --reviewed repos/<path> --chosen repos/<path> --json")
            add("Open Discovery section", path=path or f"docs/tasks/{task_id}.md")
        elif code in {"actual_changes_outside_chosen", "task_chosen_scope_drift"}:
            scope = _mapping_at(data, "repo_changes", "scope")
            unchosen = _string_list(scope.get("unchosen_actual_paths"))
            source = "data.repo_changes.scope.unchosen_actual_paths" if unchosen else ""
            add(
                "Review repository changes outside the active Chosen scope",
                command=f"./scripts/repoctl task discovery add {task_id} --reviewed <approved-task-path> --chosen <approved-task-path> --json",
                kind=NextActionKind.TASK_SCOPE_REVIEW,
                source=source,
                choices=[
                    TaskScopeResolution.ADD_TO_CHOSEN,
                    TaskScopeResolution.REVERT_CHANGE,
                    TaskScopeResolution.MOVE_TO_FOLLOW_UP,
                ],
                targets=unchosen,
            )
            add("Inspect task repo changes", command=f"./scripts/repoctl task show {task_id} --summary --json")
        elif code in {"repo_git_unavailable", "repository_git_unavailable"}:
            add("Initialize repos/ as an independent git repository", command="git -C repos init")
        elif code == "repo_head_changed_since_start":
            add("Preflight committed range", command=f"./scripts/repoctl task doctor {task_id} --use-committed-diff --json")
            add("Finish using recorded start-to-HEAD diff", command=f"./scripts/repoctl task finish {task_id} --use-committed-diff --json")
        elif code == "baseline_conflict":
            repo_changes = _mapping_at(data, "repo_changes")
            structured_conflicts = _string_list(repo_changes.get("baseline_conflicts"))
            conflicts = list(structured_conflicts)
            if not conflicts and path:
                conflicts = [path]
            resolution_args = " ".join(
                f"--resolution {shlex.quote(f'{conflict}=<task|preexisting>')}"
                for conflict in conflicts
            ) or "--resolution '<path>=<task|preexisting>'"
            add(
                "Preview baseline ownership resolutions",
                command=f"./scripts/repoctl task baseline resolve {task_id} {resolution_args} --preview --json",
                kind=NextActionKind.BASELINE_OWNERSHIP_RESOLUTION,
                source="data.repo_changes.baseline_conflicts" if structured_conflicts else "",
                choices=[BaselineOwnership.TASK, BaselineOwnership.PREEXISTING],
                targets=conflicts,
            )
            add("Inspect task repo changes", command=f"./scripts/repoctl task show {task_id} --summary --json")
        elif code == "repo_history_rewritten":
            add("Inspect repository history", command="git -C repos log --oneline --decorate -20")
            add("Create a new task with a fresh baseline", command="./scripts/repoctl task create --slug <slug> --area repo --repo-id main <title> --start --json")
        elif code == "repo_changes_on_cancel":
            add("Revert or finish repos/ changes before canceling", command="git -C repos status --short")
            add("Explicitly cancel with dirty repo evidence", command=f"./scripts/repoctl task cancel {task_id} --verification-file /tmp/{task_id}-cancel.md --allow-dirty-cancel --json")
        elif code == "annotation_required":
            repository = data.get("repository") if isinstance(data, dict) else None
            repo_path = str(repository.get("path") or "") if isinstance(repository, dict) else ""
            repo_id = str(repository.get("id") or "") if isinstance(repository, dict) else ""
            if repo_path and path.startswith(f"{repo_path}/"):
                rel = path[len(repo_path) + 1 :]
            else:
                rel = path[6:] if path.startswith("repos/") else path
            selector = f" --repo-id {repo_id}" if repo_id and repo_id != "main" else ""
            add("Add required metadata annotation", command=f"./scripts/repoctl meta set {rel or '<path>'}{selector} --role <role> --purpose <purpose> --topic <topic> --json")
        elif code == "move_candidate":
            add("Repair metadata path explicitly", command="./scripts/repoctl meta move <old-path> <new-path> --json")
        elif code == "inline_meta_residue":
            add("Move inline metadata into .repometa", command="./scripts/repoctl meta set <path> --role <role> --purpose <purpose> --topic <topic> --json", path=path)
            add("Remove inline @meta/frontmatter metadata from the source file", path=path)
        elif code in {"invalid_frontmatter", "missing_frontmatter", "invalid_status"}:
            add("Open and fix task frontmatter", path=path or f"docs/tasks/{task_id}.md")
        elif code == "task_not_found":
            add("List live tasks", command="./scripts/repoctl task list --json")
            add("Open Board task registry", path="docs/BOARD.md")
        elif code == "repository_not_found":
            add("Inspect configured repositories", command="./scripts/repoctl repo list --json")
            add("Adopt detected product repositories", command="./scripts/repoctl repo adopt --all --json")
        elif code in {"repository_selector_required", "repository_identity_unbound"}:
            add("Inspect repository identities", command="./scripts/repoctl repo list --json")
            add("Pass an explicit repo id", command="./scripts/repoctl <command> --repo-id <id> --json")
        elif code in {"invalid_task_id", "invalid_task_id_format"}:
            add("Use task id format T-YYYYMMDDHHMMSSZ", command="./scripts/repoctl task list --json")
        elif code == "invalid_area":
            add("Use a broad area enum and keep detailed surface in task text", command="./scripts/repoctl task create --area frontend --slug <slug> \"<title>\" --json")
        elif code == "invalid_repo_ref":
            add("When no product repo is selected, omit --repo-ref", command="./scripts/repoctl task create --area docs --slug <slug> \"<title>\" --json")
            add("For repo work, use stable repo_id", command="./scripts/repoctl task create --area repo --repo-id <id> --slug <slug> \"<title>\" --json")
        elif code == "repo_ref_non_repo_area":
            add("Use a repo-scoped area and stable repo_id for repos/ work", command="./scripts/repoctl task create --area repo --repo-id <id> --slug <slug> \"<title>\" --json")
            add("Omit --repo-ref when no product repo is selected", command="./scripts/repoctl task create --area docs --slug <slug> \"<title>\" --json")
        elif code == "metadata_coverage_empty":
            add("Configure sparse metadata coverage", command="./scripts/repoctl meta set <path> --role <role> --purpose <purpose> --topic <topic> --json")
        elif code == "board_missing_live_task":
            add("Repair Board registry", command="./scripts/repoctl check --fix-board --json")
        elif code == "stale_lock":
            add("Inspect repoctl lock before removing it", path=path or "docs/tasks/.repoctl.lock.d")
        elif code in {"missing_upgrade_manifest", "invalid_upgrade_source"}:
            add("Choose a repoctl release checkout or extracted artifact", command="./scripts/repoctl upgrade plan --from /path/to/agent-handoff-template --json")
        elif code == "missing_upgrade_plan":
            add("Create an upgrade plan first", command="./scripts/repoctl upgrade plan --from /path/to/agent-handoff-template --output /tmp/repoctl-upgrade-plan.json --json")
        elif code in {"upgrade_plan_stale", "upgrade_plan_workspace_mismatch"}:
            add("Regenerate the upgrade plan", command="./scripts/repoctl upgrade plan --from /path/to/agent-handoff-template --output /tmp/repoctl-upgrade-plan.json --json")
        elif code == "upgrade_plan_has_conflicts":
            add("Inspect plan conflicts before applying", path=path or "/tmp/repoctl-upgrade-plan.json")
        elif code in {"context_benchmark_corpus_file_missing", "context_benchmark_corpus_file_digest_drift"}:
            add("Apply the declared benchmark corpus before running this gate", path="tests/fixtures/context-benchmark/corpus.json")
        elif code in {
            "graph_materialization_incomplete",
            "graph_materialization_invalid",
            "graph_materialization_repository_mismatch",
            "graph_materialization_schema_mismatch",
            "graph_materialization_unavailable",
            "evidence_index_missing",
            "evidence_index_unavailable",
            "evidence_index_schema_invalid",
            "evidence_index_query_failed",
            "evidence_index_input_mismatch",
            "evidence_index_snapshot_mismatch",
        }:
            repository = data.get("repository") if isinstance(data, dict) and isinstance(data.get("repository"), dict) else {}
            repo_id = str(repository.get("id") or "<id>")
            add(
                "Rebuild the materialized Graph and evidence index",
                command=f"./scripts/repoctl graph build --repo-id {repo_id} --rebuild --json",
            )
        elif code == "knowledge_candidate_receipt_invalid":
            add("Inspect the completion receipt", path=path or f"docs/tasks/.repoctl-state/completions/{task_id}.json")
            add("Rebuild the candidate after fixing receipt provenance", command=f"./scripts/repoctl knowledge candidate suggest --from-task {task_id} --repo-id <id> --kind <kind> --claim '<reusable claim>' --dry-run --json")
        elif code == "knowledge_candidate_claim_required":
            add("State the reusable decision, invariant, or failure mode explicitly", command=f"./scripts/repoctl knowledge candidate suggest --from-task {task_id} --repo-id <id> --kind <kind> --claim '<reusable claim>' --dry-run --json")
        elif code == "knowledge_records_empty":
            add("Build a reviewable candidate from a source document", command="./scripts/repoctl knowledge candidate build --source docs/contracts/<source>.md --repo-id <id> --json")
            add("Preview task-derived candidate without approving it", command=f"./scripts/repoctl knowledge candidate suggest --from-task {task_id} --repo-id <id> --kind <kind> --claim '<reusable claim>' --dry-run --json")
    return actions


def _release_candidate_field_gates(root: Path, *, repo_id: str = "main") -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []

    def add(label: str, *, command: str, mutates_workspace: bool, requires: list[str] | None = None) -> None:
        gates.append(
            {
                "label": label,
                "command": command,
                "mutates_workspace": mutates_workspace,
                "requires": requires or [],
            }
        )

    if _repo_target_available(root, repo_id) and _fixture_has_repository(root / "tests/fixtures/context-benchmark", repo_id):
        add(
            "Materialize context benchmark corpus",
            command=f"./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark --repo-id {repo_id} --json",
            mutates_workspace=True,
            requires=["tests/fixtures/context-benchmark/corpus.json"],
        )
        add(
            "Run context benchmark gate",
            command=f"./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark --repo-id {repo_id} --min-recall-at-5 0.85 --require-source-integrity --require-fixture-corpus --require-no-forbidden --json",
            mutates_workspace=False,
            requires=["tests/fixtures/context-benchmark/questions.jsonl", "tests/fixtures/context-benchmark/expected-sources.json"],
        )
    if _repo_target_available(root, repo_id) and (root / "tests/fixtures/context-pack-benchmark/cases.json").exists():
        if (root / "tests/fixtures/context-pack-benchmark/tasks.json").exists():
            add(
                "Materialize context pack benchmark tasks",
                command="./scripts/repoctl context pack-benchmark-materialize --fixture tests/fixtures/context-pack-benchmark --json",
                mutates_workspace=True,
                requires=["tests/fixtures/context-pack-benchmark/tasks.json"],
            )
        add(
            "Run context pack benchmark gate",
            command=f"./scripts/repoctl context pack-benchmark --fixture tests/fixtures/context-pack-benchmark --repo-id {repo_id} --min-must-read-recall 1.0 --json",
            mutates_workspace=False,
            requires=["tests/fixtures/context-pack-benchmark/cases.json"],
        )
    if _has_configured_repositories(root, {"web", "api"}) and (root / "tests/fixtures/context-benchmark-multirepo/corpus.json").exists():
        add(
            "Materialize multi-repo context benchmark corpus",
            command="./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark-multirepo --json",
            mutates_workspace=True,
            requires=["tests/fixtures/context-benchmark-multirepo/corpus.json"],
        )
        add(
            "Run multi-repo isolation benchmark gate",
            command="./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark-multirepo --require-fixture-corpus --require-no-cross-repo --require-no-forbidden --min-category-visible-recall multi-repo-isolation=1.0 --json",
            mutates_workspace=False,
            requires=["tests/fixtures/context-benchmark-multirepo/questions.jsonl", "tests/fixtures/context-benchmark-multirepo/expected-sources.json"],
        )
    knowledge_records = root / "docs/knowledge/records"
    if knowledge_records.exists() and any(knowledge_records.glob("K-*.json")):
        add(
            "Check rendered knowledge pages",
            command=f"./scripts/repoctl knowledge render --repo-id {repo_id} --check --json",
            mutates_workspace=False,
            requires=["docs/knowledge/records"],
        )
    return gates


def _has_configured_repositories(root: Path, repo_ids: set[str]) -> bool:
    try:
        layout = repo_layout(root)
    except (OSError, RepoctlError):
        return False
    if not layout.registry_ready:
        return False
    configured = {target.id for target in layout.targets}
    return repo_ids.issubset(configured)


def _repo_target_available(root: Path, repo_id: str) -> bool:
    try:
        require_repo_target(root, repo_id=repo_id)
    except (OSError, RepoctlError):
        return False
    return True


def _fixture_has_repository(fixture: Path, repo_id: str) -> bool:
    corpus_path = fixture / "corpus.json"
    if not corpus_path.is_file():
        return False
    try:
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    repositories = payload.get("repositories") if isinstance(payload, dict) else None
    return isinstance(repositories, dict) and repo_id in repositories


def _problem_dicts(problems: list[Problem]) -> list[dict[str, str]]:
    return [problem.to_dict() for problem in problems]


def _problem_code_counts(problems: list[Problem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for problem in problems:
        counts[problem.code] = counts.get(problem.code, 0) + 1
    return dict(sorted(counts.items()))


def _item_code_counts(items: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(items, list):
        return counts
    for item in items:
        code = _problem_code(item)
        if code:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _problems_from_dicts(items: list[dict[str, str]]) -> list[Problem]:
    return [
        Problem(
            str(item.get("severity") or "error"),
            str(item.get("code") or "repoctl_error"),
            str(item.get("message") or ""),
            str(item.get("path")) if item.get("path") is not None else None,
        )
        for item in items
    ]


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _cleanup_entry(root: Path, path: Path, *, stop_at: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        root_resolved = root.resolve()
        path.resolve().relative_to(root_resolved)
        stop_at.resolve().relative_to(root_resolved)
        rel = path.relative_to(root).as_posix()
        stop_rel = stop_at.relative_to(root).as_posix()
    except ValueError:
        return None
    return {
        "kind": "created_file",
        "path": rel,
        "content_sha256": _file_digest(path),
        "stop_at": stop_rel,
    }


def _context_materialize_cleanup_entries(root: Path, data: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    repositories = data.get("repositories") if isinstance(data.get("repositories"), dict) else {}
    for repo_id, result in sorted(repositories.items()):
        if not isinstance(result, dict):
            continue
        try:
            target = require_repo_target(root, repo_id=str(repo_id))
        except RepoctlError:
            continue
        created = result.get("created") if isinstance(result.get("created"), list) else []
        for rel in created:
            if not isinstance(rel, str) or not rel:
                continue
            entry = _cleanup_entry(root, target.root_path / rel, stop_at=target.root_path)
            if entry is not None:
                entries.append(entry)
    return entries


def _pack_materialize_cleanup_entries(root: Path, data: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    created = data.get("created") if isinstance(data.get("created"), list) else []
    stop_at = root / "docs/archive/tasks"
    for rel in created:
        if not isinstance(rel, str) or not rel:
            continue
        entry = _cleanup_entry(root, root / rel, stop_at=stop_at)
        if entry is not None:
            entries.append(entry)
    return entries


def _temporary_benchmark_metadata(root: Path, *, repo_id: str, enabled: bool) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not enabled:
        return {}, []
    target = require_repo_target(root, repo_id=repo_id)
    metadata_root = target.root_path / ".repometa"
    if metadata_root.exists() or any(path.name != ".git" for path in target.root_path.iterdir()):
        return {}, []

    data = init_store(root, target=target)
    entries: list[dict[str, str]] = []
    for rel in data.get("created", []):
        if not isinstance(rel, str) or not rel:
            continue
        entry = _cleanup_entry(root, root / rel, stop_at=target.root_path)
        if entry is not None:
            entries.append(entry)
    return data, entries


def _cleanup_materialized_entries(root: Path, entries: list[dict[str, str]]) -> tuple[dict[str, Any], list[Problem]]:
    removed: list[str] = []
    problems: list[Problem] = []
    for entry in entries:
        rel = str(entry.get("path") or "")
        expected_digest = str(entry.get("content_sha256") or "")
        stop_rel = str(entry.get("stop_at") or "")
        path = root / rel
        stop_at = root / stop_rel
        if not rel or not expected_digest or not stop_rel:
            problems.append(Problem("error", "field_gate_materialized_entry_invalid", "field gate materialized entry is invalid", rel))
            continue
        if not path.is_file():
            continue
        if _file_digest(path) != expected_digest:
            problems.append(Problem("error", "field_gate_materialized_file_changed", "field gate materialized file changed before automatic removal", rel))
            continue
        path.unlink()
        removed.append(rel)
        _remove_empty_parents(path.parent, stop_at=stop_at, root=root)
    return {
        "materialized_count": len(entries),
        "removed_count": len(removed),
        "removed": removed,
    }, problems


def _release_candidate_gate_result(
    *,
    name: str,
    command: str,
    mutates_workspace: bool,
    data: dict[str, Any],
    problems: list[Problem],
    warnings: list[dict[str, str]] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "command": command,
        "ok": not _has_errors(problems),
        "mutates_workspace": mutates_workspace,
        "summary": summary or {},
        "data_digest": digest_data(data) if data else "",
        "problems": _problem_dicts(problems),
        "warnings": warnings or [],
    }


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _compact_field_gate_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, value in summary.items():
        if _is_json_scalar(value):
            compact[str(key)] = value
        elif isinstance(value, dict) and all(_is_json_scalar(child) for child in value.values()):
            compact[str(key)] = value
    return compact


def _compact_release_candidate_data(data: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: data[key]
        for key in (
            "schema",
            "schema_version",
            "repo_id",
            "scope",
            "applicability",
            "product_readiness",
            "gate_count",
            "passed_count",
            "failed_count",
            "run_digest",
            "artifact",
        )
        if key in data
    }
    compact_gates: list[dict[str, Any]] = []
    for gate in data.get("gates", []):
        if not isinstance(gate, dict):
            continue
        problems = gate.get("problems") if isinstance(gate.get("problems"), list) else []
        warnings = gate.get("warnings") if isinstance(gate.get("warnings"), list) else []
        summary = gate.get("summary") if isinstance(gate.get("summary"), dict) else {}
        compact_summary = _compact_field_gate_summary(summary)
        compact_gates.append(
            {
                "name": str(gate.get("name") or ""),
                "ok": bool(gate.get("ok")),
                "mutates_workspace": bool(gate.get("mutates_workspace")),
                "summary": compact_summary,
                "problem_count": len(problems),
                "warning_count": len(warnings),
                "problem_codes": _item_code_counts(problems),
                "warning_codes": _item_code_counts(warnings),
                "details_omitted": compact_summary != summary
                or any(key in gate for key in ("command", "data_digest")),
            }
        )
    compact["gates"] = compact_gates
    return compact


def _isolated_benchmark_workspace(root: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="repoctl-field-gate-")
    isolated = Path(temporary.name) / "workspace"
    shutil.copytree(
        root,
        isolated,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "repos", ".repoctl-state", ".venv", "__pycache__", "generated"),
    )
    for rel in ("docs/tasks", "docs/archive/tasks", "docs/knowledge/records", "docs/knowledge/events", "docs/knowledge/generated"):
        path = isolated / rel
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    for target in repo_layout(root).targets:
        repo = isolated / target.display_path
        repo.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "init", "-q"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            temporary.cleanup()
            raise RepoctlError(
                f"cannot initialize isolated benchmark repository: {result.stderr.strip()}",
                code="field_gate_benchmark_workspace_unavailable",
                path=target.display_path,
            )
        source_metadata = target.root_path / ".repometa"
        if source_metadata.is_dir():
            shutil.copytree(source_metadata, repo / ".repometa", dirs_exist_ok=True)
    return temporary, isolated


def _run_release_candidate_field_gates(root: Path, *, repo_id: str) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    check_payload, check_problems, _live_paths = _check_payload(root)
    gates.append(
        _release_candidate_gate_result(
            name="workspace_check",
            command="./scripts/repoctl check --json",
            mutates_workspace=False,
            data=check_payload,
            problems=check_problems,
            warnings=check_payload.get("warnings", []) if isinstance(check_payload.get("warnings"), list) else [],
            summary={"board_stale": bool(check_payload.get("data", {}).get("board", {}).get("stale")) if isinstance(check_payload.get("data"), dict) else False},
        )
    )
    if _has_errors(check_problems):
        return _release_candidate_payload(repo_id=repo_id, gates=gates)

    try:
        layout = repo_layout(root)
        repo_problems = _problems_from_dicts(repo_check_problems(layout))
        repo_data = layout.to_dict()
    except RepoctlError as exc:
        repo_problems = [Problem("error", exc.code, str(exc), exc.path)]
        repo_data = {}
    gates.append(
        _release_candidate_gate_result(
            name="repository_check",
            command="./scripts/repoctl repo check --json",
            mutates_workspace=False,
            data=repo_data,
            problems=repo_problems,
            summary={
                "registry_ready": bool(repo_data.get("registry_ready")) if repo_data else False,
                "target_count": len(repo_data.get("targets", [])) if isinstance(repo_data.get("targets"), list) else 0,
                "candidate_count": len(repo_data.get("candidates", [])) if isinstance(repo_data.get("candidates"), list) else 0,
            },
        )
    )
    if _has_errors(repo_problems):
        return _release_candidate_payload(repo_id=repo_id, gates=gates)

    knowledge_data, knowledge_problems = check_knowledge_records(root, repo_id=repo_id)
    candidate_data, candidate_problems = check_all_knowledge_candidates(root, repo_id=repo_id, pending_only=True)
    knowledge_data["candidate_checks"] = candidate_data
    knowledge_gate_problems = [
        *knowledge_problems,
        *[problem for problem in candidate_problems if problem.severity == "error"],
    ]
    knowledge_gate_warnings = [problem for problem in candidate_problems if problem.severity == "warning"]
    gates.append(
        _release_candidate_gate_result(
            name="knowledge_check",
            command=f"./scripts/repoctl knowledge check --repo-id {repo_id} --include-candidates --json",
            mutates_workspace=False,
            data=knowledge_data,
            problems=knowledge_gate_problems,
            warnings=_problem_dicts(knowledge_gate_warnings),
            summary={
                "record_count": int(knowledge_data.get("record_count") or 0),
                "event_count": int(knowledge_data.get("event_count") or 0),
                "record_error_count": len([problem for problem in knowledge_problems if problem.severity == "error"]),
                "record_problem_codes": _problem_code_counts([problem for problem in knowledge_problems if problem.severity == "error"]),
                "candidate_total_count": int(candidate_data.get("candidate_total_count") or 0) if isinstance(candidate_data, dict) else 0,
                "candidate_checked_count": len(candidate_data.get("results", [])) if isinstance(candidate_data.get("results"), list) else 0,
                "candidate_error_count": len([problem for problem in candidate_problems if problem.severity == "error"]),
                "candidate_warning_count": len([problem for problem in candidate_problems if problem.severity == "warning"]),
                "candidate_problem_codes": _problem_code_counts([problem for problem in candidate_problems if problem.severity == "error"]),
                "candidate_warning_codes": _problem_code_counts([problem for problem in candidate_problems if problem.severity == "warning"]),
            },
        )
    )

    benchmark_temporary, benchmark_root = _isolated_benchmark_workspace(root)
    context_fixture = benchmark_root / "tests/fixtures/context-benchmark"
    context_enabled = _repo_target_available(benchmark_root, repo_id) and _fixture_has_repository(context_fixture, repo_id)
    pack_fixture = benchmark_root / "tests/fixtures/context-pack-benchmark"
    pack_enabled = _repo_target_available(benchmark_root, repo_id) and (pack_fixture / "cases.json").exists()
    benchmark_metadata, benchmark_metadata_entries = _temporary_benchmark_metadata(
        benchmark_root,
        repo_id=repo_id,
        enabled=context_enabled or pack_enabled,
    )
    benchmark_metadata_cleanup: dict[str, Any] = {}
    benchmark_metadata_cleanup_problems: list[Problem] = []

    def cleanup_benchmark_metadata() -> None:
        nonlocal benchmark_metadata_cleanup, benchmark_metadata_cleanup_problems, benchmark_metadata_entries
        if not benchmark_metadata_entries:
            return
        benchmark_metadata_cleanup, benchmark_metadata_cleanup_problems = _cleanup_materialized_entries(benchmark_root, benchmark_metadata_entries)
        benchmark_metadata_entries = []

    if context_enabled:
        try:
            context_materialize, context_materialize_problems = materialize_context_benchmark_corpus(benchmark_root, fixture=context_fixture, repo_id=repo_id, force=False)
            context_cleanup_entries = _context_materialize_cleanup_entries(benchmark_root, context_materialize)
            context_cleanup: dict[str, Any] = {}
            context_benchmark: dict[str, Any] = {}
            context_benchmark_problems: list[Problem] = []
            try:
                if not _has_errors(context_materialize_problems):
                    context_benchmark, context_benchmark_problems = run_context_benchmark(
                        benchmark_root,
                        fixture=context_fixture,
                        repo_id=repo_id,
                        min_recall_at_5=0.85,
                        require_source_integrity=True,
                        require_fixture_corpus=True,
                        require_no_forbidden=True,
                    )
            finally:
                context_cleanup, context_cleanup_problems = _cleanup_materialized_entries(benchmark_root, context_cleanup_entries)
                context_materialize_problems.extend(context_cleanup_problems)
            gates.append(
                _release_candidate_gate_result(
                    name="context_benchmark_materialize",
                    command=f"./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark --repo-id {repo_id} --json",
                    mutates_workspace=False,
                    data=context_materialize,
                    problems=context_materialize_problems,
                    warnings=[{"code": "context_benchmark_isolated_workspace", "message": "benchmark corpus was materialized only inside an isolated temporary workspace"}],
                    summary={**(context_materialize.get("totals", {}) if context_materialize else {}), "auto_cleanup": context_cleanup},
                )
            )
            if not _has_errors(context_materialize_problems):
                gates.append(
                    _release_candidate_gate_result(
                        name="context_benchmark",
                        command=f"./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark --repo-id {repo_id} --min-recall-at-5 0.85 --require-source-integrity --require-fixture-corpus --require-no-forbidden --json",
                        mutates_workspace=False,
                        data=context_benchmark,
                        problems=context_benchmark_problems,
                        warnings=[{"code": "context_benchmark_retrieval_only", "message": "context benchmark measures retrieval quality only; it does not validate generated answers"}],
                        summary={
                            "question_count": context_benchmark.get("question_count", 0),
                            **(context_benchmark.get("summary", {}) if isinstance(context_benchmark.get("summary"), dict) else {}),
                        },
                    )
                )
        except BaseException:
            cleanup_benchmark_metadata()
            raise

    if pack_enabled:
        try:
            if (pack_fixture / "tasks.json").exists():
                pack_materialize, pack_materialize_problems = materialize_task_context_pack_benchmark_tasks(benchmark_root, fixture=pack_fixture, force=False)
                pack_cleanup_entries = _pack_materialize_cleanup_entries(benchmark_root, pack_materialize)
                pack_cleanup: dict[str, Any] = {}
                pack_benchmark: dict[str, Any] = {}
                pack_benchmark_problems: list[Problem] = []
                try:
                    if not _has_errors(pack_materialize_problems):
                        target = require_repo_target(benchmark_root, repo_id=repo_id)
                        pack_benchmark, pack_benchmark_problems = run_task_context_pack_benchmark(benchmark_root, target=target, fixture=pack_fixture, min_must_read_recall=1.0)
                finally:
                    pack_cleanup, pack_cleanup_problems = _cleanup_materialized_entries(benchmark_root, pack_cleanup_entries)
                    pack_materialize_problems.extend(pack_cleanup_problems)
                gates.append(
                    _release_candidate_gate_result(
                        name="context_pack_benchmark_materialize",
                        command="./scripts/repoctl context pack-benchmark-materialize --fixture tests/fixtures/context-pack-benchmark --json",
                        mutates_workspace=False,
                        data=pack_materialize,
                        problems=pack_materialize_problems,
                        warnings=[{"code": "context_pack_benchmark_isolated_workspace", "message": "benchmark tasks were materialized only inside an isolated temporary workspace"}],
                        summary={**(pack_materialize.get("totals", {}) if pack_materialize else {}), "auto_cleanup": pack_cleanup},
                    )
                )
            else:
                pack_materialize_problems = []
                pack_benchmark = {}
                pack_benchmark_problems = []
            if not _has_errors(pack_materialize_problems):
                gates.append(
                    _release_candidate_gate_result(
                        name="context_pack_benchmark",
                        command=f"./scripts/repoctl context pack-benchmark --fixture tests/fixtures/context-pack-benchmark --repo-id {repo_id} --min-must-read-recall 1.0 --json",
                        mutates_workspace=False,
                        data=pack_benchmark,
                        problems=pack_benchmark_problems,
                        warnings=[{"code": "context_pack_benchmark_retrieval_only", "message": "context pack benchmark measures source pack recall only; it does not validate generated answers or task scope"}],
                        summary={
                            "case_count": pack_benchmark.get("case_count", 0),
                            **(pack_benchmark.get("summary", {}) if isinstance(pack_benchmark.get("summary"), dict) else {}),
                        },
                    )
                )
        finally:
            cleanup_benchmark_metadata()
    else:
        cleanup_benchmark_metadata()

    if benchmark_metadata:
        materialize_gate = next(
            (gate for gate in gates if gate.get("name") in {"context_benchmark_materialize", "context_pack_benchmark_materialize"}),
            None,
        )
        if materialize_gate is not None:
            summary = materialize_gate.get("summary") if isinstance(materialize_gate.get("summary"), dict) else {}
            summary["temporary_repometa"] = {
                "created_count": int(benchmark_metadata.get("created_count") or 0),
                "auto_cleanup": benchmark_metadata_cleanup,
            }
            materialize_gate["summary"] = summary
            if benchmark_metadata_cleanup_problems:
                materialize_gate["problems"].extend(_problem_dicts(benchmark_metadata_cleanup_problems))
                materialize_gate["ok"] = False

    multi_fixture = benchmark_root / "tests/fixtures/context-benchmark-multirepo"
    if _has_configured_repositories(benchmark_root, {"web", "api"}) and (multi_fixture / "corpus.json").exists():
        multi_materialize, multi_materialize_problems = materialize_context_benchmark_corpus(benchmark_root, fixture=multi_fixture, repo_id="", force=False)
        multi_cleanup_entries = _context_materialize_cleanup_entries(benchmark_root, multi_materialize)
        multi_cleanup: dict[str, Any] = {}
        multi_benchmark: dict[str, Any] = {}
        multi_benchmark_problems: list[Problem] = []
        try:
            if not _has_errors(multi_materialize_problems):
                multi_benchmark, multi_benchmark_problems = run_context_benchmark(
                    benchmark_root,
                    fixture=multi_fixture,
                    min_category_visible_recall={"multi-repo-isolation": 1.0},
                    require_fixture_corpus=True,
                    require_no_cross_repo=True,
                    require_no_forbidden=True,
                )
        finally:
            multi_cleanup, multi_cleanup_problems = _cleanup_materialized_entries(benchmark_root, multi_cleanup_entries)
            multi_materialize_problems.extend(multi_cleanup_problems)
        gates.append(
            _release_candidate_gate_result(
                name="context_benchmark_multirepo_materialize",
                command="./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark-multirepo --json",
                mutates_workspace=False,
                data=multi_materialize,
                problems=multi_materialize_problems,
                warnings=[{"code": "context_benchmark_isolated_workspace", "message": "benchmark corpus was materialized only inside an isolated temporary workspace"}],
                summary={**(multi_materialize.get("totals", {}) if multi_materialize else {}), "auto_cleanup": multi_cleanup},
            )
        )
        if not _has_errors(multi_materialize_problems):
            gates.append(
                _release_candidate_gate_result(
                    name="context_benchmark_multirepo_isolation",
                    command="./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark-multirepo --require-fixture-corpus --require-no-cross-repo --require-no-forbidden --min-category-visible-recall multi-repo-isolation=1.0 --json",
                    mutates_workspace=False,
                    data=multi_benchmark,
                    problems=multi_benchmark_problems,
                    warnings=[{"code": "context_benchmark_retrieval_only", "message": "context benchmark measures retrieval quality only; it does not validate generated answers"}],
                    summary={
                        "question_count": multi_benchmark.get("question_count", 0),
                        **(multi_benchmark.get("summary", {}) if isinstance(multi_benchmark.get("summary"), dict) else {}),
                    },
                )
            )

    benchmark_temporary.cleanup()

    knowledge_records = root / "docs/knowledge/records"
    if knowledge_records.exists() and any(knowledge_records.glob("K-*.json")):
        render_output = Path("docs/knowledge/generated")
        render_data, render_problems = render_knowledge(root, repo_id=repo_id, output=render_output, check=True)
        gates.append(
            _release_candidate_gate_result(
                name="knowledge_render_check",
                command=f"./scripts/repoctl knowledge render --repo-id {repo_id} --check --json",
                mutates_workspace=False,
                data=render_data,
                problems=render_problems,
                warnings=[{"code": "knowledge_render_not_authoritative", "message": "rendered knowledge pages are generated views and must not be ingested as source authority"}],
                summary=render_data.get("check", {}) if isinstance(render_data.get("check"), dict) else {},
            )
        )

    return _release_candidate_payload(repo_id=repo_id, gates=gates)


def _release_candidate_payload(*, repo_id: str, gates: list[dict[str, Any]]) -> dict[str, Any]:
    error_count = sum(1 for gate in gates if not gate.get("ok"))
    data = {
        "schema": "repoctl.field_gate.release_candidate",
        "schema_version": 1,
        "repo_id": repo_id,
        "scope": ResultScope.WORKSPACE_CONTROL_PLANE,
        "applicability": FieldGateApplicability.REPOCTL_RELEASE_CANDIDATE,
        "product_readiness": ProductReadiness.NOT_EVALUATED,
        "gate_count": len(gates),
        "passed_count": len(gates) - error_count,
        "failed_count": error_count,
        "gates": gates,
    }
    data["run_digest"] = digest_data(data)
    return data


def _read_field_gate_artifact(path: Path, problems: list[Problem], *, label: str, allow_failed: bool = False) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "field_gate_artifact_missing", f"{label} field gate artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "field_gate_artifact_invalid_json", f"{label} field gate artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "field_gate_artifact_invalid", f"{label} field gate artifact must be an object", path.as_posix()))
        return {}
    if str(payload.get("command") or "") == "field-gate run" and payload.get("ok") is False and not allow_failed:
        problems.append(Problem("error", "field_gate_artifact_failed", f"{label} field gate artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "field-gate run" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "field_gate_artifact_missing_data", f"{label} field gate artifact is missing data", path.as_posix()))
        return {}
    if str(data.get("schema") or "") != "repoctl.field_gate.release_candidate":
        problems.append(Problem("error", "field_gate_artifact_wrong_schema", f"{label} artifact is not a release-candidate field gate run", path.as_posix()))
        return {}
    gates = data.get("gates")
    if not isinstance(gates, list) or not all(isinstance(gate, dict) for gate in gates):
        problems.append(Problem("error", "field_gate_artifact_invalid_data", f"{label} field gate artifact is missing gates", path.as_posix()))
        return {}
    expected_digest = str(data.get("run_digest") or "")
    actual_digest = digest_data({key: value for key, value in data.items() if key not in {"run_digest", "artifact"}})
    if expected_digest != actual_digest:
        problems.append(Problem("error", "field_gate_artifact_digest_mismatch", f"{label} field gate artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _compare_field_gate_runs(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_failed_count_increase: int | None = None,
    require_same_gates: bool = False,
    require_no_gate_regressions: bool = False,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_field_gate_artifact(baseline_path, problems, label="baseline", allow_failed=True)
    candidate = _read_field_gate_artifact(candidate_path, problems, label="candidate", allow_failed=True)
    if not baseline or not candidate:
        return {}, problems
    baseline_gates = _field_gates_by_name(baseline)
    candidate_gates = _field_gates_by_name(candidate)
    missing_gates = sorted(set(baseline_gates) - set(candidate_gates))
    new_gates = sorted(set(candidate_gates) - set(baseline_gates))
    gate_deltas = []
    for name in sorted(set(baseline_gates) | set(candidate_gates)):
        baseline_gate = baseline_gates.get(name, {})
        candidate_gate = candidate_gates.get(name, {})
        baseline_ok = bool(baseline_gate.get("ok")) if baseline_gate else None
        candidate_ok = bool(candidate_gate.get("ok")) if candidate_gate else None
        gate_deltas.append(
            {
                "name": name,
                "present_in_baseline": bool(baseline_gate),
                "present_in_candidate": bool(candidate_gate),
                "ok": {"baseline": baseline_ok, "candidate": candidate_ok, "regressed": baseline_ok is True and candidate_ok is False},
                "summary_deltas": _summary_deltas(
                    baseline_gate.get("summary", {}) if isinstance(baseline_gate.get("summary"), dict) else {},
                    candidate_gate.get("summary", {}) if isinstance(candidate_gate.get("summary"), dict) else {},
                ),
                "problem_count": {
                    "baseline": len(baseline_gate.get("problems", [])) if isinstance(baseline_gate.get("problems"), list) else 0,
                    "candidate": len(candidate_gate.get("problems", [])) if isinstance(candidate_gate.get("problems"), list) else 0,
                },
            }
        )
    failed_delta = int(candidate.get("failed_count") or 0) - int(baseline.get("failed_count") or 0)
    if max_failed_count_increase is not None and failed_delta > max_failed_count_increase:
        problems.append(Problem("error", "field_gate_failed_count_regressed", "candidate field gate failed_count increased more than allowed"))
    if require_same_gates and (missing_gates or new_gates):
        problems.append(Problem("error", "field_gate_gate_set_changed", "candidate field gate set differs from baseline"))
    if require_no_gate_regressions:
        for delta in gate_deltas:
            if delta["ok"]["regressed"]:
                problems.append(Problem("error", "field_gate_gate_regressed", f"field gate regressed from ok to failed: {delta['name']}"))
    data = {
        "schema": "repoctl.field_gate.compare",
        "schema_version": 1,
        "baseline": _field_gate_identity(baseline_path, baseline),
        "candidate": _field_gate_identity(candidate_path, candidate),
        "failed_count_delta": {"baseline": int(baseline.get("failed_count") or 0), "candidate": int(candidate.get("failed_count") or 0), "delta": failed_delta},
        "missing_gates": missing_gates,
        "new_gates": new_gates,
        "gate_deltas": gate_deltas,
        "gates": {
            "max_failed_count_increase": max_failed_count_increase,
            "require_same_gates": require_same_gates,
            "require_no_gate_regressions": require_no_gate_regressions,
        },
    }
    data["compare_digest"] = digest_data(data)
    return data, problems


def _remove_empty_parents(path: Path, *, stop_at: Path, root: Path) -> None:
    try:
        stop = stop_at.resolve()
        current = path.resolve()
        root_resolved = root.resolve()
        current.relative_to(root_resolved)
        stop.relative_to(root_resolved)
    except ValueError:
        return
    while current != stop and current != root_resolved:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _field_gates_by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gates: dict[str, dict[str, Any]] = {}
    for gate in data.get("gates", []):
        if not isinstance(gate, dict):
            continue
        name = str(gate.get("name") or "")
        if name:
            gates[name] = gate
    return gates


def _field_gate_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "run_digest": str(data.get("run_digest") or ""),
        "repo_id": str(data.get("repo_id") or ""),
        "gate_count": int(data.get("gate_count") or 0),
        "failed_count": int(data.get("failed_count") or 0),
    }


def _summary_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, dict[str, float]]:
    baseline_values = _flatten_numeric_summary(baseline)
    candidate_values = _flatten_numeric_summary(candidate)
    deltas: dict[str, dict[str, float]] = {}
    for key in sorted(set(baseline_values) | set(candidate_values)):
        baseline_value = baseline_values.get(key)
        candidate_value = candidate_values.get(key)
        if baseline_value is None or candidate_value is None:
            continue
        deltas[key] = {
            "baseline": round(baseline_value, 6),
            "candidate": round(candidate_value, 6),
            "delta": round(candidate_value - baseline_value, 6),
        }
    return deltas


def _flatten_numeric_summary(value: Any, *, prefix: str = "") -> dict[str, float]:
    results: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            results.update(_flatten_numeric_summary(child, prefix=child_prefix))
    elif isinstance(value, bool):
        results[prefix] = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        results[prefix] = float(value)
    return results


def _repo_scoped_frontmatter(task: Any) -> bool:
    area = str(task.frontmatter.get("area") or "")
    return bool(str(task.frontmatter.get("repo_id") or "").strip()) or area in REPO_REQUIRED_AREAS


def _discovery_guidance_actions(task_id: str, *, repo_id: str = "main", repo_path: str = "repos") -> list[dict[str, str]]:
    candidate = f"{repo_path.rstrip('/')}/<path>"
    return [
        {
            "label": "Record the candidate query",
            "command": f"./scripts/repoctl task discovery add {task_id} --query '<query>' --json",
        },
        {
            "label": "Find likely product files",
            "command": f"./scripts/repoctl context query '<query>' --repo-id {repo_id} --json",
        },
        {
            "label": "Record inspected and chosen files",
            "command": f"./scripts/repoctl task discovery add {task_id} --reviewed {candidate} --chosen {candidate} --json",
        },
    ]


def _has_errors(problems: list[Problem]) -> bool:
    return any(problem.severity == "error" for problem in problems)


def _warnings(problems: list[Problem]) -> list[dict[str, str]]:
    return [problem.to_dict() for problem in problems if problem.severity == "warning"]


def _repo_target_from_args(root: Path, args: argparse.Namespace) -> RepoTarget | None:
    repo_id = getattr(args, "repo_id", None)
    if repo_id:
        return require_repo_target(root, repo_id=repo_id)
    return default_repo_target(root)


def _command_name(args: argparse.Namespace) -> str:
    parts = [str(getattr(args, name)) for name in ("command", "field_gate_command", "repo_command", "task_command", "task_log_command", "task_discovery_command", "backlog_command", "meta_command", "index_command", "graph_command", "context_command", "knowledge_command", "knowledge_candidate_command", "knowledge_event_command", "upgrade_command") if getattr(args, name, None)]
    return ".".join(parts) if parts else "repoctl"


def _error_data(args: argparse.Namespace) -> dict[str, Any]:
    data: dict[str, Any] = {}
    task_id = str(getattr(args, "task_id", "") or getattr(args, "task", "") or "")
    repo_id = str(getattr(args, "repo_id", "") or "")
    if task_id:
        data["task_id"] = task_id
    if repo_id:
        data["repo_id"] = repo_id
    return data


def _check_payload(root: Path, *, include_archived_warnings: bool = False, full: bool = False) -> tuple[dict[str, Any], list[Problem], list[str]]:
    tasks = load_tasks(root)
    board_path = root / "docs/BOARD.md"
    board_text = board_path.read_text(encoding="utf-8")
    board_paths = parse_board(board_text)
    _receipts, receipt_problems = collect_completion_receipts(root)
    problems = validate_tasks(tasks, include_archived_warnings=include_archived_warnings) + validate_live_task_states(root, tasks) + check_board(root, board_paths, tasks, board_text) + receipt_problems + _generated_adapter_problems(root)
    live_paths = [task.rel_path for task in live_tasks(tasks)]
    release_gates = _release_candidate_field_gates(root)
    release_gate_data: dict[str, Any] = {
        "scope": ResultScope.WORKSPACE_CONTROL_PLANE,
        "applicability": FieldGateApplicability.REPOCTL_RELEASE_CANDIDATE,
        "product_readiness": ProductReadiness.NOT_EVALUATED,
        "details_included": full,
        "gate_count": len(release_gates),
        "mutating_gate_count": sum(1 for gate in release_gates if gate.get("mutates_workspace")),
        "run_command": "./scripts/repoctl field-gate run release-candidate --repo-id main --json",
    }
    if full:
        release_gate_data["gates"] = release_gates
    payload = {
        "ok": not _has_errors(problems),
        "command": "check",
        "data": {
            "field_gates": {
                "release_candidate": release_gate_data,
            },
            "board": {
                "stale": set(board_paths) != set(live_paths),
                "missing": sorted(set(live_paths) - set(board_paths)),
                "extra": sorted(set(board_paths) - set(live_paths)),
            },
        },
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _warnings(problems),
    }
    return payload, problems, live_paths


def _generated_adapter_problems(root: Path) -> list[Problem]:
    manifest_path = root / "ai/generated-manifest.json"
    rel_manifest = "ai/generated-manifest.json"
    if not manifest_path.is_file():
        contract_paths = (
            root / "ai/roles",
            root / ".agents/skills/maintenance-workflow/SKILL.md",
            root / "tools/render_agent_adapters.py",
        )
        generated_outputs = (
            *root.glob(".claude/agents/maintenance-*.md"),
            *root.glob(".codex/agents/maintenance-*.toml"),
        )
        if not any(path.exists() for path in contract_paths) and not generated_outputs:
            return []
        return [Problem("error", "generated_adapter_manifest_invalid", "generated adapter manifest is missing", rel_manifest)]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Problem("error", "generated_adapter_manifest_invalid", f"generated adapter manifest is unreadable: {exc}", rel_manifest)]
    if not isinstance(data, dict) or data.get("schema") != "repoctl.generated-adapters":
        return [Problem("error", "generated_adapter_manifest_invalid", "generated adapter manifest has invalid schema", rel_manifest)]
    problems: list[Problem] = []
    expected_outputs: set[str] = set()
    for group in ("sources", "outputs"):
        entries = data.get(group)
        if not isinstance(entries, list):
            problems.append(Problem("error", "generated_adapter_manifest_invalid", f"generated adapter manifest {group} must be a list", rel_manifest))
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                problems.append(Problem("error", "generated_adapter_manifest_invalid", f"generated adapter manifest {group} entry is invalid", rel_manifest))
                continue
            rel = str(entry.get("path") or "")
            expected = str(entry.get("sha256") or "")
            normalized = Path(rel)
            if not rel or normalized.is_absolute() or ".." in normalized.parts or not expected.startswith("sha256:"):
                problems.append(Problem("error", "generated_adapter_manifest_invalid", f"generated adapter manifest path or digest is invalid: {rel}", rel_manifest))
                continue
            path = root / rel
            if group == "outputs":
                expected_outputs.add(rel)
            try:
                actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                actual = ""
            if actual != expected:
                problems.append(Problem("error", "generated_adapter_drift", f"generated adapter {group[:-1]} digest does not match manifest", rel))
    for pattern in (".claude/agents/maintenance-*.md", ".codex/agents/maintenance-*.toml"):
        for path in root.glob(pattern):
            rel = path.relative_to(root).as_posix()
            if rel not in expected_outputs:
                problems.append(Problem("error", "generated_adapter_orphan", "generated adapter is not declared by the manifest", rel))
    return problems


def cmd_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    payload, problems, live_paths = _check_payload(root, include_archived_warnings=args.include_archived_warnings, full=args.full)
    if args.fix_board:
        with repoctl_lock(root):
            _locked_payload, _locked_problems, live_paths = _check_payload(root, include_archived_warnings=args.include_archived_warnings, full=args.full)
            board_path = root / "docs/BOARD.md"
            board_text = board_path.read_text(encoding="utf-8")
            fixed = render_board(board_text, live_paths)
            if fixed != board_text:
                atomic_write(board_path, fixed)
        payload, problems, _ = _check_payload(root, include_archived_warnings=args.include_archived_warnings, full=args.full)
    if args.json:
        _json(payload)
    else:
        if payload["ok"]:
            print("repoctl check: ok")
        else:
            print("repoctl check: problems found")
            for problem in problems:
                location = f" {problem.path}" if problem.path else ""
                print(f"[{problem.severity}] {problem.code}{location}: {problem.message}")
        if payload["data"]["board"]["stale"]:
            print("BOARD is stale. Run: repoctl check --fix-board")
    return 1 if _has_errors(problems) else 0


def cmd_field_gate_run(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    if args.gate != "release-candidate":
        raise RepoctlError(f"unsupported field gate: {args.gate}")
    output: Path | None = None
    if args.output:
        output, output_problem = _workspace_output_path(root, args.output, code="field_gate_output_outside_workspace")
        if output_problem is not None:
            payload = {
                "ok": False,
                "command": "field-gate run",
                "data": {},
                "problems": [output_problem.to_dict()],
                "warnings": [],
            }
            if args.json:
                _json(payload)
            else:
                print(output_problem.message)
            return 1
    data = _run_release_candidate_field_gates(root, repo_id=args.repo_id)
    problems = [
        Problem("error", "field_gate_failed", f"field gate failed: {gate.get('name', '')}")
        for gate in data.get("gates", [])
        if not gate.get("ok")
    ]
    warnings = [
        {
            "code": "field_gate_benchmarks_isolated",
            "message": "release-candidate benchmarks run in an isolated temporary workspace and do not materialize fixtures in product repositories",
        }
    ]
    full_payload = {
        "ok": not problems,
        "command": "field-gate run",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if output is not None:
        data["artifact"] = {
            "path": output.relative_to(root).as_posix(),
            "run_digest": data.get("run_digest", ""),
        }
        _complete_json_envelope(full_payload)
        atomic_write(output, json.dumps(full_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    payload = {
        "ok": not problems,
        "command": "field-gate run",
        "data": data if args.full else _compact_release_candidate_data(data),
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if args.json:
        _json(payload)
    else:
        print(f"field gate {args.gate} passed={data.get('passed_count', 0)} failed={data.get('failed_count', 0)} digest={data.get('run_digest', '')}")
        for gate in data.get("gates", []):
            status = "ok" if gate.get("ok") else "failed"
            print(f"[{status}] {gate.get('name', '')}")
    return 1 if problems else 0


def cmd_field_gate_compare(args: argparse.Namespace) -> int:
    data, problems = _compare_field_gate_runs(
        baseline_path=Path(args.baseline),
        candidate_path=Path(args.candidate),
        max_failed_count_increase=args.max_failed_count_increase,
        require_same_gates=args.require_same_gates,
        require_no_gate_regressions=args.require_no_gate_regressions,
    )
    payload = {
        "ok": not _has_errors(problems),
        "command": "field-gate compare",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        delta = data.get("failed_count_delta", {}) if data else {}
        print(f"field gate compare failed_count_delta={delta.get('delta', 0)} missing={len(data.get('missing_gates', [])) if data else 0} new={len(data.get('new_gates', [])) if data else 0}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_repo_list(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    layout = repo_layout(root)
    payload = {"ok": True, "command": "repo.list", "data": layout.to_dict(), "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        for target in layout.targets:
            print(f"{target.id} {target.display_path} {target.identity_source}")
        for candidate in layout.candidates:
            print(f"{candidate.display_path} suggested_id={candidate.suggested_id} {candidate.identity_status}")
    return 0


def cmd_repo_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, args.repo_id)
    payload = {"ok": True, "command": "repo.show", "data": {"repository": target.to_dict()}, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"{target.id} {target.display_path} {target.identity_source}")
    return 0


def cmd_repo_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    layout = repo_layout(root)
    problems = repo_check_problems(layout)
    payload = {"ok": not problems, "command": "repo.check", "data": layout.to_dict(), "problems": problems, "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"repoctl repo check: {layout.placement} ({len(layout.targets)} repositories)")
        for problem in problems:
            print(f"[{problem['severity']}] {problem['code']}: {problem['message']}")
    return 1 if problems else 0


def cmd_repo_adopt(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        layout = adopt_repositories(root, all_candidates=args.all, path=args.path or "", repo_id=args.repo_id or "")
    payload = {"ok": True, "command": "repo.adopt", "data": layout.to_dict(), "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print("repoctl repo adopt: ok")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    tasks = load_tasks(root)
    board_text = (root / "docs/BOARD.md").read_text(encoding="utf-8")
    board_paths = parse_board(board_text)
    live_paths = [task.rel_path for task in live_tasks(tasks)]
    problems = validate_tasks(tasks) + check_board(root, board_paths, tasks, board_text)
    payload = {
        "ok": not _has_errors(problems),
        "command": "task.list",
        "data": {
            "tasks": [task.to_list_dict() for task in sorted(live_tasks(tasks), key=lambda task: task.rel_path)],
            "board": {
                "stale": set(board_paths) != set(live_paths),
                "missing": sorted(set(live_paths) - set(board_paths)),
                "extra": sorted(set(board_paths) - set(live_paths)),
            },
        },
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _warnings(problems),
    }
    if args.json:
        _json(payload)
    else:
        for task in payload["data"]["tasks"]:
            print(f"{task['id']} {task['status']} {task['path']}")
        if payload["data"]["board"]["stale"]:
            print("BOARD is stale. Run: repoctl check --fix-board")
    return 0


def _task_scope_drift_warning(root: Path, task: Any, delta: dict[str, Any]) -> dict[str, Any] | None:
    try:
        target = _repo_target_for_task_command(root, task)
    except RepoctlError:
        return None
    if target is None or not _repo_scoped_frontmatter(task) or not discovery_recorded(task, target):
        return None
    scope = discovery_scope_delta(task, target, list(delta.get("changes") or []))
    delta["scope"] = scope
    if not scope["unchosen_actual_paths"] and not scope["unused_chosen_paths"]:
        return None
    return {
        "severity": "warning",
        "code": "task_chosen_scope_drift",
        "message": "current repository changes and active Chosen files differ; this is advisory until task finish",
        "path": task.rel_path,
    }


def _task_baseline_conflict_warning(task: Any, delta: dict[str, Any]) -> dict[str, Any] | None:
    conflicts = _string_list(delta.get("baseline_conflicts"))
    if not conflicts:
        return None
    return {
        "severity": "warning",
        "code": "baseline_conflict",
        "message": "task changes overlap paths that were dirty at task start; resolve ownership before finish",
        "path": conflicts[0],
    }


def cmd_task_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task = resolve_task(root, args.task_id)
    delta = repo_changes_since_task_start(root, task.id) if task.status in {"todo", "doing", "blocked"} else None
    warnings: list[dict[str, Any]] = []
    if delta:
        baseline_warning = _task_baseline_conflict_warning(task, delta)
        if baseline_warning is not None:
            warnings.append(baseline_warning)
        scope_warning = _task_scope_drift_warning(root, task, delta)
        if scope_warning is not None:
            warnings.append(scope_warning)
    full_repo_changes = _repo_change_summary(delta, compact=False) if delta else None
    repo_changes = _repo_change_summary(delta, compact=bool(args.summary or args.section)) if delta else None
    summary = {"task": task.to_list_dict(), "path": task.rel_path, "repo_changes": repo_changes}
    if args.section:
        text = task.path.read_text(encoding="utf-8")
        section = find_section(text, args.section)
        body = text[section.body_start : section.end].strip()
        payload = {
            "ok": True,
            "command": "task.show",
            "data": {**summary, "section": {"name": args.section, "body": body}},
            "problems": [],
            "warnings": warnings,
        }
    elif args.summary:
        payload = {"ok": True, "command": "task.show", "data": summary, "problems": [], "warnings": warnings}
    else:
        payload = {
            "ok": True,
            "command": "task.show",
            "data": {**summary, "frontmatter": task.frontmatter, "body": task.body},
            "problems": [],
            "warnings": warnings,
        }
    action_data = {**summary, "task_id": task.id, "repo_changes": full_repo_changes}
    payload["next_actions"] = _next_actions_for_problems(warnings, data=action_data)
    if args.json:
        _json(payload)
    elif args.section:
        print(f"## {args.section}\n\n{payload['data']['section']['body']}".rstrip())
    elif args.summary:
        print(f"{task.id} {task.status} {task.rel_path}")
    else:
        print(task.path.read_text(encoding="utf-8"))
    return 0


def cmd_task_log_append(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        result = append_task_log(root, args.task_id, args.message)
        atomic_write(result["task"].path, result["text"])
    task_id = result["task"].id
    payload = {
        "ok": True,
        "command": "task.log.append",
        "data": {"task_id": task_id, "timestamp": result["timestamp"]},
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Logged: {task_id} {result['timestamp']}")
    return 0


def cmd_task_discovery_add(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        result = update_task_discovery(
            root,
            args.task_id,
            query=args.query or "",
            reviewed=args.reviewed or [],
            chosen=args.chosen or [],
            replace_chosen=args.replace_chosen or [],
            reason=args.reason or "",
            note=args.note or "",
        )
        atomic_write(result["task"].path, result["text"])
    next_actions: list[dict[str, str]] = []
    task_id = result["task"].id
    chosen_files = result["discovery"]["chosen_files"]
    try:
        target = _repo_target_for_task_command(root, result["task"])
    except RepoctlError:
        target = None
    if args.query and target is not None:
        next_actions.append(
            {
                "label": "Find likely product files",
                "command": f"./scripts/repoctl context query {shlex.quote(args.query)} --repo-id {target.id} --json",
            }
        )
    if chosen_files:
        next_actions.append(
            {
                "label": "Check finish readiness",
                "command": f"./scripts/repoctl task doctor {task_id} --json",
            }
        )
    payload = {
        "ok": True,
        "command": "task.discovery.add",
        "data": {
            "task_id": task_id,
            "path": result["task"].rel_path,
            "update": result["update"],
            "totals": result["totals"],
            **({"discovery": result["discovery"]} if args.full else {}),
        },
        "problems": [],
        "warnings": [],
        "next_actions": next_actions,
    }
    if args.json:
        _json(payload)
    else:
        print(f"Updated Discovery: {task_id}")
    return 0


def cmd_task_baseline_resolve(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    resolutions: list[tuple[str, str]] = []
    if args.path:
        if not args.ownership:
            raise RepoctlError("--ownership is required with --path", code="missing_baseline_ownership")
        resolutions.extend((path, args.ownership) for path in args.path)
    for raw in args.resolution or []:
        path, separator, ownership = raw.rpartition("=")
        if not separator or not path.strip() or ownership not in {"task", "preexisting"}:
            raise RepoctlError(
                "--resolution must use PATH=task or PATH=preexisting",
                code="invalid_baseline_resolution",
                path=raw,
            )
        resolutions.append((path, ownership))
    if not resolutions:
        raise RepoctlError(
            "baseline resolve requires --path with --ownership or one or more --resolution PATH=OWNERSHIP values",
            code="missing_baseline_resolution",
        )
    with repoctl_lock(root):
        result = resolve_task_baseline_ownerships(
            root,
            args.task_id,
            resolutions=resolutions,
            apply=not args.preview,
        )
    data = {
        "task_id": result["task"].id,
        "applied": result["applied"],
        "resolutions": result["resolutions"],
    }
    payload = {"ok": True, "command": "task.baseline.resolve", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        action = "Previewed" if args.preview else "Resolved"
        print(f"{action} baseline ownership for {len(result['resolutions'])} path(s)")
    return 0


def _task_doctor_payload(root: Path, task_id: str, *, use_committed_diff: bool = False) -> dict[str, Any]:
    task = resolve_task(root, task_id)
    all_tasks = load_tasks(root)
    task_problems = [problem for problem in validate_tasks(all_tasks, include_archived_warnings=True) if problem.path == task.rel_path]
    doctor_problems: list[Problem] = []
    verification: VerificationInput | None = None
    verification_ready = True
    try:
        verification = _task_verification_input(root, task_id)
    except RepoctlError as exc:
        verification_ready = False
        doctor_problems.append(Problem("warning", exc.code or "missing_verification_file", str(exc), exc.path or task.rel_path))
    target: RepoTarget | None = None
    repository: dict[str, Any] = {}
    delta_preparation_failed = False
    try:
        target = _repo_target_for_task_command(root, task)
        repository = target.to_dict() if target is not None else {}
        delta = (
            _task_finish_repo_delta(root, task, target, use_committed_diff=use_committed_diff)
            if target is not None
            else repo_changes_since_task_start(root, task.id)
        )
    except RepoctlError as exc:
        delta_preparation_failed = True
        doctor_problems.append(Problem("error", exc.code or "repoctl_error", str(exc), exc.path or task.rel_path))
        delta = {
            "changes": [],
            "baseline_available": False,
            "preexisting_count": 0,
            "baseline_conflicts": [],
        }
    scope_warning = None if delta_preparation_failed else _task_scope_drift_warning(root, task, delta)
    if not delta_preparation_failed:
        try:
            if verification is None:
                _meta_gate, delta = _finish_meta_gate(
                    root,
                    task.id,
                    use_committed_diff=use_committed_diff,
                    prepared_delta=delta,
                )
            else:
                _meta_gate, delta, _result = _prepare_task_finish(
                    root,
                    task.id,
                    verification=verification,
                    use_committed_diff=use_committed_diff,
                    prepared_delta=delta,
                )
        except RepoctlError as exc:
            discovery_is_already_advisory = verification is None and exc.code == "placeholder_discovery" and any(
                problem.code == "missing_discovery_evidence" for problem in task_problems
            )
            scope_is_already_advisory = exc.code == "actual_changes_outside_chosen" and scope_warning is not None
            if not discovery_is_already_advisory and not scope_is_already_advisory:
                doctor_problems.append(Problem("error", exc.code or "repoctl_error", str(exc), exc.path or task.rel_path))
    combined = [*task_problems, *doctor_problems]
    blockers = [problem.code for problem in combined if problem.severity == "error"]
    advisory = [
        *[problem.code for problem in combined if problem.severity == "warning"],
        *([str(scope_warning["code"])] if scope_warning is not None else []),
    ]
    finish_ready = task.status in {"doing", "todo", "blocked"} and verification_ready and not blockers
    full_repo_changes = _repo_change_summary(delta, compact=False)
    data = {
        "task_id": task.id,
        "status": task.status,
        "path": task.rel_path,
        "finish_ready": finish_ready,
        "blocked_by": blockers,
        "advisory": advisory,
        "repo_changes": _repo_change_summary(delta),
        "repository": repository,
        "evidence_mode": "committed_range" if use_committed_diff else "working_tree_diff",
        "verification": {
            "default_source": "task_section",
            "task_section_complete": verification_ready,
        },
    }
    payload = {
        "ok": not blockers,
        "command": "task.doctor",
        "data": data,
        "problems": [problem.to_dict() for problem in combined if problem.severity == "error"],
        "warnings": [
            *[problem.to_dict() for problem in combined if problem.severity == "warning"],
            *([scope_warning] if scope_warning is not None else []),
        ],
    }
    action_data = {**data, "repo_changes": full_repo_changes}
    payload["next_actions"] = _next_actions_for_problems(
        [*payload["problems"], *payload["warnings"]],
        data=action_data,
    )
    return payload


def _project_string_collection(values: Any, *, compact: bool) -> tuple[list[str], int, bool]:
    items = _string_list(values)
    visible = items[:COMPACT_PATH_LIMIT] if compact else items
    return visible, len(items), compact and len(items) > COMPACT_PATH_LIMIT


def _scope_summary(scope: Any, *, compact: bool) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("actual_paths", "chosen_paths", "unchosen_actual_paths", "unused_chosen_paths"):
        visible, count, truncated = _project_string_collection(scope.get(key), compact=compact)
        summary[key] = visible
        summary[f"{key}_count"] = count
        summary[f"{key}_truncated"] = truncated
    return summary


def _repo_change_summary(delta: dict[str, Any], *, compact: bool = True) -> dict[str, Any]:
    task_new_files = [entry[1] for entry in delta.get("changes", [])]
    visible_task_new, _task_new_count, task_new_truncated = _project_string_collection(task_new_files, compact=compact)
    visible_conflicts, conflict_count, conflicts_truncated = _project_string_collection(delta.get("baseline_conflicts"), compact=compact)
    summary = {
        "task_new": len(task_new_files),
        "task_new_files": visible_task_new,
        "task_new_files_truncated": task_new_truncated,
        "preexisting_dirty": delta.get("preexisting_count", 0),
        "baseline_available": bool(delta.get("baseline_available")),
        "baseline_conflicts": visible_conflicts,
        "baseline_conflict_count": conflict_count,
        "baseline_conflicts_truncated": conflicts_truncated,
        "repo_git_available": bool(delta.get("repo_git") and delta["repo_git"].available),
        "repo_git_reason": str(delta.get("repo_git").reason) if delta.get("repo_git") and not delta["repo_git"].available else "",
    }
    if isinstance(delta.get("scope"), dict):
        summary["scope"] = _scope_summary(delta["scope"], compact=compact)
    return summary


def _metadata_coverage_warnings(meta: dict[str, Any]) -> list[dict[str, str]]:
    summary = meta.get("summary", {}) if isinstance(meta, dict) else {}
    if not isinstance(summary, dict):
        return []
    if summary.get("indexed_only", 0) and not summary.get("annotated", 0) and not summary.get("annotation_required", 0):
        return [
            {
                "severity": "warning",
                "code": "metadata_coverage_empty",
                "message": "metadata policy has no required or recorded annotations; meta query/suggest are weak discovery hints until sparse coverage is configured",
            }
        ]
    return []


def cmd_task_doctor(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    payload = _task_doctor_payload(root, args.task_id, use_committed_diff=args.use_committed_diff)
    if args.json:
        _json(payload)
    else:
        data = payload["data"]
        print(f"repoctl task doctor: {data['task_id']} status={data['status']} finish_ready={data['finish_ready']}")
        for problem in payload["problems"] + payload["warnings"]:
            print(f"- {problem['code']}: {problem['message']}")
    return 1 if payload["problems"] else 0


def cmd_task_create(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task: Any | None = None
    original_board_text = ""
    with repoctl_lock(root):
        board_path = root / "docs/BOARD.md"
        original_board_text = board_path.read_text(encoding="utf-8")
        try:
            board_text = original_board_text
            board_paths = parse_board(board_text)
            title = args.title
            area = args.area or ""
            repo_ref = args.repo_ref or ""
            repo_id = args.repo_id or ""
            if not title:
                raise RepoctlError("task title is required")
            if args.backlog_id:
                resolve_backlog_item(board_text, args.backlog_id)
                if not args.slug:
                    raise RepoctlError("Backlog promotion requires explicit --slug", code="missing_slug")
                if not area:
                    raise RepoctlError("Backlog promotion requires explicit --area", code="missing_area")
            task = create_task_file(
                root,
                title=title,
                task_type=args.type,
                slug=args.slug,
                area=area,
                owner=args.owner,
                parent=args.parent or "",
                repo_ref=repo_ref,
                repo_id=repo_id,
                backlog_id=args.backlog_id or "",
                follow_up_of=args.follow_up_of or "",
            )
            if args.backlog_id:
                board_text, _removed = remove_backlog_item(board_text, args.backlog_id)
                board_paths = parse_board(board_text)
            if task.rel_path not in board_paths:
                board_paths.append(task.rel_path)
                fixed = render_board(board_text, board_paths)
                atomic_write(board_path, fixed)
            start_result = None
            if args.start:
                start_result = start_task(root, task.id, force_dirty=args.force_dirty)
                atomic_write(start_result["task"].path, start_result["text"])
        except Exception:
            if task is not None and task.path.exists() and task.path.is_file():
                task.path.unlink()
            if original_board_text:
                atomic_write(board_path, original_board_text)
            raise
    status = "doing" if start_result else task.status
    next_actions: list[dict[str, str]] = []
    if _repo_scoped_frontmatter(task):
        repo_path = "repos"
        repo_id = str(task.frontmatter.get("repo_id") or "main")
        try:
            target = _repo_target_for_task_command(root, task)
            if target is not None:
                repo_path = target.display_path
                repo_id = target.id
        except RepoctlError:
            pass
        next_actions = _discovery_guidance_actions(task.id, repo_id=repo_id, repo_path=repo_path)
    payload = {
        "ok": True,
        "command": "task.create",
        "data": {
            "task_id": task.id,
            "path": task.rel_path,
            "status": status,
            "backlog_id": args.backlog_id or "",
            "backlog_removed": bool(args.backlog_id),
            "started": bool(start_result),
            "repo_changes": _repo_change_summary(repo_changes_since_task_start(root, task.id)) if start_result else None,
        },
        "problems": [],
        "warnings": [problem.to_dict() for problem in (start_result or {}).get("warnings", [])],
        "next_actions": next_actions,
    }
    if args.json:
        _json(payload)
    elif args.print_id:
        print(task.id)
    else:
        print(f"Created: {task.rel_path}")
        print(f"Task ID: {task.id}")
        if start_result:
            print(f"Started: {task.id}")
        if next_actions:
            print(f"Next: {next_actions[0]['command']}")
    return 0


def cmd_backlog_list(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    board_text = (root / "docs/BOARD.md").read_text(encoding="utf-8")
    items = read_backlog_items(board_text)
    payload = {"ok": True, "command": "backlog list", "data": {"items": [item.to_dict() for item in items]}, "problems": [], "warnings": backlog_warnings(items)}
    if args.json:
        _json(payload)
    else:
        if not items:
            print("Backlog is empty.")
        for item in items:
            print(f"{item.id} {item.title}")
            print(item.raw)
            print()
    return 0


def cmd_backlog_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    board_text = (root / "docs/BOARD.md").read_text(encoding="utf-8")
    item = resolve_backlog_item(board_text, args.backlog_id)
    payload = {"ok": True, "command": "backlog show", "data": {"item": item.to_dict()}, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(item.raw)
    return 0


def cmd_backlog_add(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    title = (args.title or "").strip()
    if not title:
        raise RepoctlError("backlog title is required")
    if "\n" in title or "\r" in title:
        raise RepoctlError("backlog title must be a single line", code="invalid_title")
    body = ""
    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise RepoctlError(f"body file cannot be read: {args.body_file}") from exc
    with repoctl_lock(root):
        board_path = root / "docs/BOARD.md"
        board_text = board_path.read_text(encoding="utf-8")
        updated = append_backlog_item(board_text, title, body)
        atomic_write(board_path, updated)
    items = read_backlog_items(updated)
    item = items[-1]
    payload = {"ok": True, "command": "backlog add", "data": {"item": item.to_dict()}, "problems": [], "warnings": backlog_warnings(items)}
    if args.json:
        _json(payload)
    else:
        print(f"Added: {item.id}")
    return 0


def cmd_backlog_remove(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        board_path = root / "docs/BOARD.md"
        board_text = board_path.read_text(encoding="utf-8")
        updated, item = remove_backlog_item(board_text, args.backlog_id)
        atomic_write(board_path, updated)
    payload = {"ok": True, "command": "backlog remove", "data": {"removed": item.to_dict()}, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Removed: {item.id}")
    return 0


def cmd_task_start(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task_id = resolve_task(root, args.task_id).id
    with repoctl_lock(root):
        result = start_task(root, task_id, force_dirty=args.force_dirty)
        atomic_write(result["task"].path, result["text"])
    delta = repo_changes_since_task_start(root, task_id)
    visible_dirty, dirty_count, dirty_truncated = _project_string_collection(result["dirty"], compact=True)
    data = {
        "task_id": task_id,
        "status": "doing",
        "dirty": visible_dirty,
        "dirty_count": dirty_count,
        "dirty_truncated": dirty_truncated,
        "repo_changes": _repo_change_summary(delta),
    }
    next_actions: list[dict[str, str]] = []
    if _repo_scoped_frontmatter(result["task"]):
        repo_path = "repos"
        repo_id = str(result["task"].frontmatter.get("repo_id") or "main")
        try:
            target = _repo_target_for_task_command(root, result["task"])
            if target is not None:
                repo_path = target.display_path
                repo_id = target.id
        except RepoctlError:
            pass
        next_actions = _discovery_guidance_actions(task_id, repo_id=repo_id, repo_path=repo_path)
    payload = {"ok": True, "command": "task.start", "data": data, "problems": [], "warnings": [problem.to_dict() for problem in result.get("warnings", [])], "next_actions": next_actions}
    if args.json:
        _json(payload)
    else:
        print(f"Started: {task_id}")
        if next_actions:
            print(f"Next: {next_actions[0]['command']}")
    return 0


def _task_verification_input(root: Path, task_id: str) -> VerificationInput:
    task = resolve_task(root, task_id)
    text = task.path.read_text(encoding="utf-8")
    section = find_section(text, "Verification")
    body = text[section.body_start : section.end].strip()
    normalized = body.casefold().strip()
    placeholders = {"- pending", "- pending.", "- 대기 중", "- 대기 중.", "pending", "pending.", "대기 중", "대기 중."}
    if not body or normalized in placeholders:
        raise RepoctlError("task Verification section is incomplete; record commands, evidence, and results or use --verification-file", code="missing_verification_file", path=task.rel_path)
    source_text = body + "\n"
    return VerificationInput(
        source="task_section",
        text=source_text,
        source_sha256="sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        source_path=task.rel_path,
    )


def _verification_input_arg(root: Path, task_id: str, *, verification_file: str | None, command: str) -> VerificationInput:
    if verification_file:
        path = Path(verification_file)
        validate_verification_file(root, path)
        try:
            source_bytes = path.read_bytes()
            source_text = source_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RepoctlError(f"verification file cannot be read as UTF-8: {path}", code="missing_verification_file", path=path.as_posix()) from exc
        return VerificationInput(
            source="external_file",
            text=source_text,
            source_sha256="sha256:" + hashlib.sha256(source_bytes).hexdigest(),
            source_path=path.as_posix(),
        )
    try:
        return _task_verification_input(root, task_id)
    except RepoctlError:
        raise RepoctlError(
            f"task {command} requires --verification-file or a completed ## Verification section",
            code="missing_verification_file",
            path=resolve_task(root, task_id).rel_path,
        )


def _repo_target_for_task_command(root: Path, task: Any) -> RepoTarget | None:
    repo_id = str(task.frontmatter.get("repo_id") or "").strip()
    area = str(task.frontmatter.get("area") or "")
    if repo_id:
        return require_repo_target(root, repo_id=repo_id)
    if area in REPO_REQUIRED_AREAS:
        return default_repo_target(root)
    layout = repo_layout(root)
    if not layout.registry_ready:
        return None
    return layout.targets[0] if len(layout.targets) == 1 else None


def _task_finish_repo_delta(
    root: Path,
    task: Any,
    target: RepoTarget,
    *,
    use_committed_diff: bool,
) -> dict[str, Any]:
    if _repo_scoped_frontmatter(task) and not use_committed_diff:
        start_head = task_repo_head_at_start(root, task.id)
        current_head, head_state = repo_git_head(root, target)
        if start_head and head_state.available and current_head != start_head:
            raise RepoctlError(
                "repo HEAD changed since task start; use --use-committed-diff only when the recorded start HEAD is an ancestor of the current HEAD",
                code="repo_head_changed_since_start",
                path=task.rel_path,
            )
    delta = repo_changes_since_task_start(root, task.id)
    if not use_committed_diff:
        delta["ownership"] = task_baseline_ownership_evidence(root, task.id)
        return delta

    pending_task_changes = delta.get("changes") or []
    if pending_task_changes:
        changed = ", ".join(str(entry[1]) for entry in pending_task_changes[:8])
        suffix = "" if len(pending_task_changes) <= 8 else f", ... +{len(pending_task_changes) - 8} more"
        raise RepoctlError(
            f"committed diff finish requires no uncommitted task changes after the commit: {changed}{suffix}",
            code="repo_dirty_after_committed_diff",
            path=str(pending_task_changes[0][1]),
        )
    start_head = task_repo_head_at_start(root, task.id)
    if not start_head:
        raise RepoctlError("task cannot finish because repo head at start was not recorded; restart the task with repoctl task start", code="repo_head_missing_at_start", path=task.rel_path)
    current_head, head_state = repo_git_head(root, target)
    if not head_state.available:
        raise RepoctlError(f"committed diff finish cannot read repository HEAD: {head_state.reason}", code="repo_git_unavailable", path=head_state.repo_path or target.display_path)
    is_ancestor, ancestry_state = repo_is_ancestor(root, ancestor=start_head, descendant=current_head, target=target)
    if not ancestry_state.available:
        raise RepoctlError(f"committed diff finish cannot compare repository history: {ancestry_state.reason}", code="repo_commit_range_unavailable", path=ancestry_state.repo_path or target.display_path)
    if not is_ancestor:
        raise RepoctlError(
            "committed diff finish requires the recorded start HEAD to be an ancestor of the observed HEAD",
            code="repo_history_rewritten",
            path=target.display_path,
        )
    committed_changes, range_state = repo_commit_range_entries(root, base=start_head, target=target)
    if not range_state.available:
        raise RepoctlError(f"committed diff finish cannot read task commit range: {range_state.reason}", code="repo_commit_range_unavailable", path=range_state.repo_path or target.display_path)
    committed_conflicts, ownership_evidence = committed_range_baseline_conflicts(root, task.id, committed_changes)
    return {
        "changes": committed_changes,
        "baseline_available": True,
        "baseline_count": int(delta.get("baseline_count") or 0),
        "current_count": int(delta.get("current_count") or 0),
        "preexisting_count": int(delta.get("preexisting_count") or 0),
        "baseline_conflicts": sorted(set([*list(delta.get("baseline_conflicts") or []), *committed_conflicts])),
        "initial_dirty_paths": list(delta.get("initial_dirty_paths") or []),
        "ownership": ownership_evidence or task_baseline_ownership_evidence(root, task.id),
        "repo_git": range_state,
        "committed_range": {"base": start_head, "head": current_head},
    }


def _finish_meta_gate(
    root: Path,
    task_id: str,
    *,
    use_committed_diff: bool = False,
    prepared_delta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = resolve_task(root, task_id)
    target = _repo_target_for_task_command(root, task)
    if use_committed_diff and target is None:
        raise RepoctlError("committed diff finish requires an explicit product repository target", code="repository_selector_required", path=task.rel_path)
    if target is None:
        delta = repo_changes_since_task_start(root, task_id)
        if delta.get("changes"):
            first_changed = str(delta["changes"][0][1])
            changed = ", ".join(str(entry[1]) for entry in delta["changes"][:8])
            suffix = "" if len(delta["changes"]) <= 8 else f", ... +{len(delta['changes']) - 8} more"
            raise RepoctlError(
                f"task has product repository changes without repo_id: {changed}{suffix}; create a repo-scoped child task with repo_id for product work",
                code="repository_selector_required",
                path=first_changed,
            )
        layout = repo_layout(root)
        blocking = [problem for problem in layout.problems if problem.get("code") != "repository_identity_unbound"]
        if blocking:
            first = blocking[0]
            raise RepoctlError(
                f"{first.get('code', 'repository_topology_invalid')} {first.get('path', '')}: {first.get('message', 'product repository registry is not ready')}",
                code=first.get("code") or "repository_topology_invalid",
                path=first.get("path") or "",
            )
        reason = "no_repo_directory" if not (root / "repos").exists() else "root_workspace_no_repo_target"
        return {"status": "skipped", "reason": reason}, {
            "changes": [],
            "baseline_available": False,
            "preexisting_count": 0,
            "baseline_conflicts": [],
        }
    delta = prepared_delta if prepared_delta is not None else _task_finish_repo_delta(
        root,
        task,
        target,
        use_committed_diff=use_committed_diff,
    )
    if delta.get("baseline_conflicts"):
        conflicts = ", ".join(str(path) for path in list(delta["baseline_conflicts"])[:8])
        suffix = "" if len(delta["baseline_conflicts"]) <= 8 else f", ... +{len(delta['baseline_conflicts']) - 8} more"
        raise RepoctlError(
            f"task changes overlap paths that were dirty at task start: {conflicts}{suffix}; resolve each path with repoctl task baseline resolve",
            code="baseline_conflict",
            path=str(delta["baseline_conflicts"][0]),
        )
    task_changes = delta["changes"]
    if task_changes and _repo_scoped_frontmatter(task):
        if not discovery_recorded(task, target):
            raise RepoctlError("repo task must record candidate discovery before finish", code="placeholder_discovery", path=task.rel_path)
        scope_delta = discovery_scope_delta(task, target, task_changes)
        delta["scope"] = scope_delta
        if scope_delta["unchosen_actual_paths"]:
            missing = ", ".join(scope_delta["unchosen_actual_paths"][:8])
            suffix = "" if len(scope_delta["unchosen_actual_paths"]) <= 8 else f", ... +{len(scope_delta['unchosen_actual_paths']) - 8} more"
            raise RepoctlError(
                f"actual repository changes are outside the active Chosen files set: {missing}{suffix}",
                code="actual_changes_outside_chosen",
                path=scope_delta["unchosen_actual_paths"][0],
            )
    changed_files, status_problems, meta_summary = meta_status(root, changed=True, changes=task_changes, target=target)
    repo_exists = bool(target and target.root_path.exists()) or (root / "repos").exists()
    meta_gate = {"status": "skipped", "reason": "no_repo_directory" if not repo_exists else "no_repo_changes"}
    status_errors = [problem for problem in status_problems if problem.severity == "error"]
    if status_errors:
        first = status_errors[0]
        location = f" {first.path}" if first.path else ""
        raise RepoctlError(f"repo meta changed-file check failed: {first.code}{location}: {first.message}", code=first.code, path=first.path)
    if changed_files:
        meta_problems = check_meta(root, changed=True, changes=task_changes, target=target)
        meta_errors = [problem for problem in meta_problems if problem.severity == "error"]
        if meta_errors:
            first = meta_errors[0]
            location = f" {first.path}" if first.path else ""
            raise RepoctlError(f"repo meta changed-file check failed: {first.code}{location}: {first.message}", code=first.code, path=first.path)
        meta_gate = {
            "status": "passed",
            "scope": "changed",
            "changed_files": len(changed_files),
            "baseline_available": delta["baseline_available"],
            "preexisting_dirty_files": delta["preexisting_count"],
            "baseline_conflicts": delta.get("baseline_conflicts", []),
            "summary": meta_summary.get("summary", {}),
        }
    elif delta["current_count"] and delta["baseline_available"]:
        meta_gate = {
            "status": "skipped",
            "reason": "no_task_repo_changes",
            "baseline_available": True,
            "preexisting_dirty_files": delta["preexisting_count"],
            "summary": meta_summary.get("summary", {}),
        }
    start_head = task_repo_head_at_start(root, task_id)
    observed_head, observed_state = repo_git_head(root, target)
    should_record_repo_evidence = _repo_scoped_frontmatter(task) or bool(delta.get("changes"))
    if observed_state.available and should_record_repo_evidence:
        mode = "committed_range" if use_committed_diff else "working_tree_diff"
        manifest, fingerprint, fingerprint_state = repo_evidence_fingerprint(
            root,
            mode=mode,
            start_head=start_head,
            observed_head=observed_head,
            entries=list(delta.get("changes") or []),
            ownership=delta.get("ownership") if isinstance(delta.get("ownership"), dict) else {},
            conflict_paths=list(delta.get("baseline_conflicts") or []),
            target=target,
        )
        if not fingerprint_state.available:
            raise RepoctlError(f"cannot fingerprint repository evidence: {fingerprint_state.reason}", code="repo_git_unavailable", path=fingerprint_state.repo_path or target.display_path)
        delta["evidence_mode"] = mode
        delta["evidence_manifest"] = manifest
        delta["diff_fingerprint_sha256"] = fingerprint
    return meta_gate, delta


def _prepare_task_finish(
    root: Path,
    task_id: str,
    *,
    verification: VerificationInput,
    use_committed_diff: bool = False,
    prepared_delta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    meta_gate, delta = _finish_meta_gate(
        root,
        task_id,
        use_committed_diff=use_committed_diff,
        prepared_delta=prepared_delta,
    )
    result = finish_task(
        root,
        task_id,
        verification=verification,
        meta_gate=meta_gate,
        repo_delta=delta,
        allow_head_changed=use_committed_diff,
    )
    return meta_gate, delta, result


def _finish_summary(meta_gate: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    repo_git = delta.get("repo_git")
    task_new_files = [str(entry[1]) for entry in delta.get("changes", [])]
    visible_task_new, _task_new_count, task_new_truncated = _project_string_collection(task_new_files, compact=True)
    visible_conflicts, conflict_count, conflicts_truncated = _project_string_collection(delta.get("baseline_conflicts"), compact=True)
    unused_chosen = (delta.get("scope") or {}).get("unused_chosen_paths") if isinstance(delta.get("scope"), dict) else []
    visible_unused, unused_count, unused_truncated = _project_string_collection(unused_chosen, compact=True)
    summary = {
        "meta_gate_status": str(meta_gate.get("status") or "unknown"),
        "meta_gate_reason": str(meta_gate.get("reason") or ""),
        "task_new_changes": len(task_new_files),
        "task_new_files": visible_task_new,
        "task_new_files_truncated": task_new_truncated,
        "current_dirty_files": int(delta.get("current_count") or 0),
        "preexisting_dirty_files": int(delta.get("preexisting_count") or 0),
        "baseline_available": bool(delta.get("baseline_available")),
        "baseline_conflicts": visible_conflicts,
        "baseline_conflict_count": conflict_count,
        "baseline_conflicts_truncated": conflicts_truncated,
        "unused_chosen_paths": visible_unused,
        "unused_chosen_path_count": unused_count,
        "unused_chosen_paths_truncated": unused_truncated,
        "repo_git_available": bool(repo_git and repo_git.available),
        "repo_git_reason": str(repo_git.reason) if repo_git and not repo_git.available else "",
        "attention_required": bool(delta.get("baseline_conflicts")) or str(meta_gate.get("status") or "") not in {"passed", "skipped"},
    }
    committed_range = delta.get("committed_range")
    if isinstance(committed_range, dict) and committed_range:
        summary["committed_range"] = committed_range
    return summary


def _cancel_dirty_gate(root: Path, task_id: str, *, allow_dirty_cancel: bool) -> dict[str, Any]:
    delta = repo_changes_since_task_start(root, task_id)
    if delta["changes"] and not allow_dirty_cancel:
        changed = ", ".join(entry[1] for entry in delta["changes"][:5])
        suffix = " ..." if len(delta["changes"]) > 5 else ""
        raise RepoctlError(
            f"task cancel would leave repos/ changes outside a finished metadata gate: {changed}{suffix}; revert them, finish the task, or pass --allow-dirty-cancel with explicit cancellation evidence",
            code="repo_changes_on_cancel",
            path=f"docs/tasks/{task_id}.md",
        )
    return {
        "status": "skipped",
        "reason": "task_canceled" if allow_dirty_cancel or not delta["changes"] else "repo_changes_on_cancel",
        "baseline_available": delta["baseline_available"],
        "preexisting_dirty_files": delta["preexisting_count"],
        "task_new_changes": len(delta["changes"]),
    }


def _write_task_result(root: Path, result: dict[str, Any]) -> None:
    written_archives: list[Path] = []
    original_task_text = ""
    task_written = False
    original_sources: dict[Path, str] = {}

    def restore_removed_sources() -> None:
        for source, text in original_sources.items():
            if not source.exists():
                atomic_write(source, text)

    def remove_written_archives() -> None:
        for target in written_archives:
            if target.exists() and target.is_file():
                target.unlink()

    if result["archived"]:
        try:
            for _source, target in result["moves"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                archive_text = result["archive_texts"].get(target)
                if archive_text is None:
                    raise RepoctlError(f"archive text missing for {target.relative_to(root).as_posix()}")
                atomic_write(target, archive_text)
                written_archives.append(target)
        except Exception:
            remove_written_archives()
            raise
    else:
        original_task_text = result["task"].path.read_text(encoding="utf-8")
        atomic_write(result["task"].path, result["text"])
        task_written = True
    receipt_writes = result.get("receipt_writes") or []
    if not receipt_writes and result.get("receipt_path") is not None and result.get("receipt_text"):
        receipt_writes = [(result["receipt_path"], str(result["receipt_text"]))]
    original_receipts: dict[Path, str | None] = {}

    def restore_receipts() -> None:
        for receipt_path, original_text in original_receipts.items():
            if original_text is None:
                if receipt_path.exists() and receipt_path.is_file():
                    receipt_path.unlink()
            else:
                atomic_write(receipt_path, original_text)

    if receipt_writes:
        try:
            for receipt_path, receipt_text in receipt_writes:
                if receipt_path not in original_receipts:
                    original_receipts[receipt_path] = receipt_path.read_text(encoding="utf-8") if receipt_path.is_file() else None
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(receipt_path, str(receipt_text))
        except Exception:
            restore_receipts()
            remove_written_archives()
            if task_written:
                atomic_write(result["task"].path, original_task_text)
            raise
    if result["archived"]:
        try:
            for source, _target in result["moves"]:
                if source.exists():
                    original_sources[source] = source.read_text(encoding="utf-8")
                    source.unlink()
        except Exception:
            restore_removed_sources()
            restore_receipts()
            remove_written_archives()
            raise
    board_path = root / "docs/BOARD.md"
    board_text = board_path.read_text(encoding="utf-8")
    remove_paths = set() if result.get("keep_board") else {result["old_path"]}
    for source, _target in result["moves"]:
        try:
            remove_paths.add(source.relative_to(root).as_posix())
        except ValueError:
            pass
    kept = [path for path in parse_board(board_text) if path not in remove_paths]
    try:
        atomic_write(board_path, render_board(board_text, kept))
    except Exception:
        restore_removed_sources()
        restore_receipts()
        remove_written_archives()
        if task_written:
            atomic_write(result["task"].path, original_task_text)
        raise


def cmd_task_finish(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task_id = resolve_task(root, args.task_id).id
    verification = _verification_input_arg(root, task_id, verification_file=args.verification_file, command="finish")
    with repoctl_lock(root):
        meta_gate, delta, result = _prepare_task_finish(
            root,
            task_id,
            verification=verification,
            use_committed_diff=args.use_committed_diff,
        )
        _write_task_result(root, result)
    finish_summary = _finish_summary(meta_gate, delta)
    data = {
        "task_id": task_id,
        "status": "done",
        "closure_scope": ResultScope.TASK,
        "product_readiness": ProductReadiness.NOT_EVALUATED,
        "old_path": result["old_path"],
        "new_path": result["new_path"],
        "archived": result["archived"],
        "truncated": result["truncated"],
        "meta_gate": meta_gate,
        "finish_summary": finish_summary,
        "completion_receipt": result["receipt_path"].relative_to(root).as_posix(),
    }
    receipt = result.get("receipt") if isinstance(result.get("receipt"), dict) else {}
    repo_id = str(receipt.get("repo_id") or "")
    next_actions = []
    if repo_id:
        next_actions.append(
            {
                "label": "Preview a Knowledge candidate only if this task produced a reusable decision, invariant, or failure mode",
                "command": f"./scripts/repoctl knowledge candidate suggest --from-task {task_id} --repo-id {repo_id} --kind <kind> --claim '<reusable claim>' --dry-run --json",
            }
        )
    payload = {
        "ok": True,
        "command": "task.finish",
        "data": data,
        "problems": [],
        "warnings": [],
        "next_actions": next_actions,
    }
    if args.json:
        _json(payload)
    else:
        print(f"Finished: {task_id}")
        print(
            "Repo changes: "
            f"task_new={finish_summary['task_new_changes']} "
            f"preexisting_dirty={finish_summary['preexisting_dirty_files']} "
            f"current_dirty={finish_summary['current_dirty_files']} "
            f"meta_gate={finish_summary['meta_gate_status']}"
        )
        if result["archived"]:
            print(f"Archived: {result['new_path']}")
    return 0


def cmd_task_cancel(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task_id = resolve_task(root, args.task_id).id
    verification = _verification_input_arg(root, task_id, verification_file=args.verification_file, command="cancel")
    with repoctl_lock(root):
        cancel_gate = _cancel_dirty_gate(root, task_id, allow_dirty_cancel=args.allow_dirty_cancel)
        result = cancel_task(root, task_id, verification=verification)
        _write_task_result(root, result)
    data = {
        "task_id": task_id,
        "status": "canceled",
        "old_path": result["old_path"],
        "new_path": result["new_path"],
        "archived": result["archived"],
        "truncated": result["truncated"],
        "cancel_gate": cancel_gate,
    }
    payload = {
        "ok": True,
        "command": "task.cancel",
        "data": data,
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Canceled: {task_id}")
        if result["archived"]:
            print(f"Archived: {result['new_path']}")
    return 0


def cmd_task_block(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task_id = resolve_task(root, args.task_id).id
    verification = _verification_input_arg(root, task_id, verification_file=args.verification_file, command="block")
    with repoctl_lock(root):
        result = block_task(root, task_id, verification=verification)
        _write_task_result(root, result)
    payload = {
        "ok": True,
        "command": "task.block",
        "data": {
            "task_id": task_id,
            "status": "blocked",
            "path": result["new_path"],
            "truncated": result["truncated"],
        },
        "problems": [],
        "warnings": [],
        "next_actions": [
            {
                "label": "Check task readiness after resolving the blocker",
                "command": f"./scripts/repoctl task doctor {task_id} --json",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Blocked: {task_id}")
    return 0


def cmd_meta_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    problems = check_meta(root, changed=args.changed, target=target)
    data = {"scope": "changed" if args.changed else "all"}
    if target is not None:
        data["repository"] = target.to_dict()
    payload = {
        "ok": not _has_errors(problems),
        "command": "meta check --changed" if args.changed else "meta check",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        if payload["ok"]:
            print("repoctl meta check: ok")
        else:
            print("repoctl meta check: problems found")
            for problem in problems:
                location = f" {problem.path}" if problem.path else ""
                print(f"[{problem.severity}] {problem.code}{location}: {problem.message}")
    return 1 if _has_errors(problems) else 0


def cmd_meta_init(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    with repoctl_lock(root):
        data = init_store(root, target=target)
    payload = {"ok": True, "command": "meta init", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"repoctl meta init: {data['created_count']} files created")
    return 0


def cmd_meta_status(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    files, problems, meta = meta_status(root, changed=args.changed, target=target)
    visible_files = files
    if not args.include_excluded:
        visible_files = [file for file in visible_files if file.classification != "excluded"]
    data: dict[str, Any] = {**meta}
    if args.verbose or args.include_excluded:
        data["files"] = [file.to_dict() for file in visible_files]
    warnings = _metadata_coverage_warnings(meta)
    payload = {
        "ok": not _has_errors(problems),
        "command": "meta status",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if args.json:
        _json(payload)
    else:
        if not visible_files:
            print("No eligible repo files found." if not args.changed else "No eligible changed repo files found.")
        summary = meta.get("summary", {})
        if summary:
            print(
                "repoctl meta status: "
                f"total={summary.get('total', 0)} "
                f"required={summary.get('annotation_required', 0)} "
                f"annotated={summary.get('annotated', 0)} "
                f"excluded={summary.get('excluded', 0)} "
                f"indexed_only={summary.get('indexed_only', 0)}"
            )
        for file in visible_files:
            marker = "required" if file.annotation_required else "optional"
            present = "present" if file.annotation_present else "missing"
            print(f"{file.path} area={file.area or '-'} topics={','.join(file.default_topics) or '-'} annotation={marker}/{present}")
    return 1 if _has_errors(problems) else 0


def cmd_meta_inventory(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    files, problems, meta = meta_inventory(root, changed=False, target=target)
    warnings = _metadata_coverage_warnings(meta)
    payload = {
        "ok": not _has_errors(problems),
        "command": "meta inventory",
        "data": {**meta, "files": [file.to_dict() for file in files]},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if args.json:
        _json(payload)
    else:
        summary = meta.get("summary", {})
        print(f"repoctl meta inventory: {summary.get('total', 0)} files")
        for key in ("excluded", "annotated", "annotation_required", "indexed_only", "excluded_override", "orphan_annotation", "move_candidate"):
            if summary.get(key):
                print(f"{key}: {summary[key]}")
    return 1 if _has_errors(problems) else 0


def _read_optional_file(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RepoctlError(f"file cannot be read: {path}") from exc


def cmd_meta_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    data = show_annotation(root, args.path, target=target)
    payload = {"ok": True, "command": "meta show", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_meta_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    candidates, problems, meta = meta_query(root, role=args.role or "", topics=args.topic or [], area=args.area or "", effects=args.declared_effect or [], limit=args.limit, target=target)
    warnings = _metadata_coverage_warnings(meta)
    payload = {
        "ok": not _has_errors(problems),
        "command": "meta query",
        "data": {**meta, "candidates": [candidate.to_dict() for candidate in candidates]},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if args.json:
        _json(payload)
    else:
        for candidate in candidates:
            print(f"{candidate.score:03d} {candidate.path} [{', '.join(candidate.signals)}]")
    return 1 if _has_errors(problems) else 0


def cmd_meta_suggest(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    candidates, problems, meta = meta_suggest(root, text=args.text, limit=args.limit, target=target)
    warning = {
        "code": "suggestion_not_authoritative",
        "message": "meta suggest returns candidate files only; inspect files before creating or changing task scope",
    }
    payload = {
        "ok": not _has_errors(problems),
        "command": "meta suggest",
        "data": {**meta, "candidates": [candidate.to_dict() for candidate in candidates]},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [warning, *_metadata_coverage_warnings(meta)],
    }
    if args.json:
        _json(payload)
    else:
        print(warning["message"])
        for candidate in candidates:
            print(f"{candidate.score:03d} {candidate.path} [{', '.join(candidate.signals)}]")
    return 1 if _has_errors(problems) else 0


def cmd_index_code(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    entries, problems, meta = build_code_index(root, changed=args.changed, limit=args.limit, target=target)
    warning = {
        "code": "index_not_authoritative",
        "message": "index code is read-only technical fact extraction; inspect files before changing task scope",
    }
    warnings = [warning]
    summary = meta.get("summary", {})
    if summary.get("truncated"):
        warnings.append(
            {
                "code": "index_truncated",
                "message": f"index code returned {summary.get('returned', 0)} of {summary.get('total', 0)} files; rerun with a higher --limit for complete output",
            }
        )
    payload = {
        "ok": not _has_errors(problems),
        "command": "index code",
        "data": {**meta, "files": [entry.to_dict() for entry in entries]},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": warnings,
    }
    if args.json:
        _json(payload)
    else:
        print(warning["message"])
        for entry in entries:
            print(f"{entry.path} language={entry.language} symbols={','.join(entry.symbols) or '-'} deps={','.join(entry.deps) or '-'}")
    return 1 if _has_errors(problems) else 0


def cmd_graph_build(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    with repoctl_lock(root):
        snapshot, problems, meta = materialize_graph(root, target=target, rebuild=args.rebuild)
    summary = _graph_snapshot_summary(snapshot) if snapshot is not None else None
    data = {
        "repository": target.to_dict(),
        "summary": summary,
        "materialization": _compact_graph_materialization(meta.get("materialization", {})),
    }
    if args.full:
        data.update(meta)
        data["snapshot"] = snapshot.to_dict() if snapshot is not None else None
    payload = {
        "ok": snapshot is not None and not _has_errors(problems),
        "command": "graph build",
        "data": data,
        "problems": [problem.to_dict() for problem in problems if problem.severity == "error"],
        "warnings": [
            *[problem.to_dict() for problem in problems if problem.severity == "warning"],
            {
                "code": "graph_not_authoritative",
                "message": "graph build materializes a derived index under .repoctl-state; source authorities remain product files, task receipts, and .repometa",
            }
        ],
    }
    if args.json:
        _json(payload, compact=not args.full)
    else:
        if snapshot is not None:
            print(f"graph snapshot {snapshot.snapshot_digest} repository={target.id} nodes={len(snapshot.nodes)} edges={len(snapshot.edges)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _graph_snapshot_summary(snapshot: Any) -> dict[str, Any]:
    node_counts: dict[str, int] = {}
    edge_counts: dict[str, int] = {}
    for node in snapshot.nodes:
        node_counts[node.kind] = node_counts.get(node.kind, 0) + 1
    for edge in snapshot.edges:
        edge_counts[edge.kind] = edge_counts.get(edge.kind, 0) + 1
    return {
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.snapshot_digest,
        "schema_version": snapshot.schema_version,
        "source_count": len(snapshot.sources),
        "node_count": len(snapshot.nodes),
        "edge_count": len(snapshot.edges),
        "node_counts": dict(sorted(node_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "completeness": _compact_graph_completeness(snapshot.completeness),
        "index": _graph_index_summary(snapshot),
    }


def _graph_index_summary(snapshot: Any) -> dict[str, Any]:
    entries = [
        node.facts.get("index")
        for node in snapshot.nodes
        if node.kind == "file" and isinstance(node.facts.get("index"), dict)
    ]
    languages = {
        str(entry.get("language") or "")
        for entry in entries
        if str(entry.get("language") or "")
    }
    return {
        "total": len(entries),
        "ok": sum(1 for entry in entries if entry.get("parse_status") == "ok"),
        "skipped": sum(1 for entry in entries if entry.get("parse_status") == "skipped"),
        "parse_error": sum(1 for entry in entries if entry.get("parse_status") == "parse_error"),
        "languages": {
            language: sum(1 for entry in entries if entry.get("language") == language)
            for language in sorted(languages)
        },
    }


def _compact_graph_materialization(materialization: Any) -> dict[str, Any]:
    if not isinstance(materialization, dict):
        return {}
    updated_paths = materialization.get("updated_paths") if isinstance(materialization.get("updated_paths"), dict) else {}
    semantic_paths = {
        str(path)
        for paths in updated_paths.values()
        if isinstance(paths, list)
        for path in paths
        if str(path)
    }
    code_index = materialization.get("code_index") if isinstance(materialization.get("code_index"), dict) else {}
    evidence = materialization.get("evidence") if isinstance(materialization.get("evidence"), dict) else {}
    return {
        "status": materialization.get("status", ""),
        "input_digest": materialization.get("input_digest", ""),
        "reused_provider_count": len(materialization.get("reused_providers", [])) if isinstance(materialization.get("reused_providers"), list) else 0,
        "updated_provider_count": len(materialization.get("updated_providers", [])) if isinstance(materialization.get("updated_providers"), list) else 0,
        "semantic_updated_path_count": len(semantic_paths),
        "code_index": {
            "full_reindex": bool(code_index.get("full_reindex")),
            "changed_path_count": len(code_index.get("changed_paths", [])) if isinstance(code_index.get("changed_paths"), list) else 0,
        },
        "evidence": {
            "status": evidence.get("status", ""),
            "updated_path_count": len(evidence.get("updated_paths", [])) if isinstance(evidence.get("updated_paths"), list) else 0,
        },
    }


def cmd_graph_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    snapshot, build_problems, meta = load_materialized_graph(root, target=target)
    if snapshot is None or _has_errors(build_problems):
        payload = {
            "ok": False,
            "command": "graph query",
            "data": {"result": None, **meta},
            "problems": [problem.to_dict() for problem in build_problems],
            "warnings": [],
        }
        if args.json:
            _json(payload, compact=not args.full)
        else:
            for problem in build_problems:
                print(problem.message)
        return 1 if _has_errors(build_problems) else 0

    freshness, freshness_problems = graph_materialization_freshness(root, target=target, snapshot=snapshot)

    result, query_problems = query_graph(
        snapshot,
        file=args.file or "",
        topic=args.topic or "",
        import_ref=args.import_ref or "",
        symbol=args.symbol or "",
        callers_of=args.callers_of or "",
        callees_of=args.callees_of or "",
        impact_file=args.impact_file or "",
        impact_symbol=args.impact_symbol or "",
        task=args.task or "",
        artifact=args.artifact or "",
        in_file=args.in_file or "",
        depth=args.depth,
    )
    query_status = str((result or {}).get("query_status") or "unavailable")
    outcome_ok = query_status in {"found", "not_found"}
    result_data = result if args.full or result is None else _compact_graph_query_result(result, freshness=freshness)
    completeness = result.get("completeness", snapshot.completeness) if result is not None else snapshot.completeness
    result_warnings = result.get("warnings", []) if isinstance(result, dict) and isinstance(result.get("warnings"), list) else []
    freshness_data = freshness if args.full else compact_graph_freshness(freshness)
    stale_warning: dict[str, Any] = {
        "code": "graph_snapshot_stale",
        "message": "materialized Graph does not match current source or workspace evidence; run repoctl graph build before relying on changed relations",
    }
    if args.full:
        stale_warning["changed_path_count"] = int(freshness.get("changed_path_count") or 0)
        stale_warning["changed_root_path_count"] = int(freshness.get("changed_root_path_count") or 0)
        stale_warning["changed_paths"] = freshness.get("changed_paths", [])
        stale_warning["changed_root_paths"] = freshness.get("changed_root_paths", [])
    payload = {
        "ok": result is not None and outcome_ok and not _has_errors(query_problems),
        "command": "graph query",
        "data": {
            "result": result_data,
            "query_status": query_status,
            "completeness": completeness if args.full else _compact_graph_completeness(completeness),
            "repository": target.to_dict(),
            "snapshot_digest": snapshot.snapshot_digest,
            "freshness": freshness_data,
        },
        "problems": [problem.to_dict() for problem in query_problems],
        "warnings": [
            *[problem.to_dict() for problem in build_problems if problem.severity == "warning"],
            *[problem.to_dict() for problem in freshness_problems],
            *(result_warnings if args.full else _compact_graph_query_warnings(completeness)),
            *(
                [
                    stale_warning
                ]
                if freshness.get("status") == "stale"
                else []
            ),
            {
                "code": "graph_not_authoritative",
                "message": "graph query reads the materialized derived index; inspect source files before changing task scope",
            }
        ],
    }
    if args.json:
        _json(payload, compact=not args.full)
    else:
        if result is not None:
            print(f"graph query {result['query']} matches={len(result.get('matches', []))} paths={len(result.get('paths', []))} nodes={len(result['nodes'])} edges={len(result['edges'])}")
            for match in result.get("matches", [])[:10]:
                label = match.get("qualified_name") or match.get("path") or match.get("raw_import") or match.get("topic") or match.get("id")
                location = match.get("path") or ""
                print(f"match {label} {location}".rstrip())
            for path in result.get("paths", [])[:20]:
                source = path.get("from", {})
                target_node = path.get("to", {})
                source_label = source.get("qualified_name") or source.get("path") or source.get("id")
                target_label = target_node.get("qualified_name") or target_node.get("path") or target_node.get("id")
                print(f"path {source_label} --{path.get('edge')}--> {target_label} ({path.get('reason')})")
        for problem in query_problems:
            print(problem.message)
    return 1 if _has_errors(query_problems) or query_status in {"unsupported", "unavailable"} else 0


def _compact_graph_query_result(result: dict[str, Any], *, freshness: dict[str, Any] | None = None) -> dict[str, Any]:
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    paths = result.get("paths") if isinstance(result.get("paths"), list) else []
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    continuations = result.get("continuations") if isinstance(result.get("continuations"), list) else []
    node_summaries = {
        str(node.get("id") or ""): _compact_graph_node(node, include_id=False)
        for node in nodes
        if isinstance(node, dict) and str(node.get("id") or "")
    }
    query_intent = _graph_query_intent(result.get("query"))
    displayed_matches = [match for match in matches[:3] if isinstance(match, dict)]
    match_keys = {
        key
        for match in displayed_matches
        if (key := _graph_summary_selector_key(match)) is not None
    }
    continuation_by_key = _compact_graph_continuation_index(continuations)
    ordered_paths = _dedupe_compact_graph_paths(
        sorted(
            [path for path in paths if isinstance(path, dict)],
            key=lambda path: _compact_graph_path_key(path, query_intent=query_intent),
        )
    )
    selected_paths, selected_continuation_keys = _select_compact_graph_paths(
        ordered_paths,
        continuation_by_key=continuation_by_key,
        match_keys=match_keys,
        path_limit=3,
        continuation_limit=3,
    )
    completeness = result.get("completeness") if isinstance(result.get("completeness"), dict) else {}
    displayed_paths = [_compact_graph_path(path, completeness=completeness, freshness=freshness or {}) for path in selected_paths]
    relation_edges = [
        edge
        for edge in edges
        if isinstance(edge, dict) and str(edge.get("kind") or "") not in {"DEFINES", "ANCHORS"}
    ]
    relation_edges.sort(key=lambda edge: _compact_graph_relation_key(edge, query_intent=query_intent))
    relation_edges = _dedupe_compact_graph_edges(relation_edges)
    selected_relations: list[dict[str, Any]] = []
    if not displayed_paths:
        selected_relations, selected_continuation_keys = _select_compact_graph_relations(
            relation_edges,
            node_summaries=node_summaries,
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
            relation_limit=3,
            continuation_limit=3,
        )
    for key in sorted(match_keys):
        if len(selected_continuation_keys) >= 3:
            break
        if key in continuation_by_key and key not in selected_continuation_keys:
            selected_continuation_keys.append(key)
    displayed_continuations = _compact_graph_continuations(
        selected_continuation_keys,
        continuation_by_key=continuation_by_key,
    )
    displayed_relations = [
        _compact_graph_relation(edge, node_summaries, completeness=completeness, freshness=freshness or {})
        for edge in selected_relations
    ]
    compact = {
        "query": result.get("query", {}),
        "query_status": result.get("query_status", "unavailable"),
        "matches": displayed_matches[:3],
        "candidates": [candidate for candidate in candidates[:3] if isinstance(candidate, dict)],
        "paths": displayed_paths,
        "continuations": displayed_continuations,
    }
    if not compact["paths"]:
        compact["relations"] = displayed_relations
    return compact


_GRAPH_QUERY_INTENT_BY_TYPE = {
    "file": GraphQueryIntent.FILE,
    "impact_file": GraphQueryIntent.FILE,
    "symbol": GraphQueryIntent.SYMBOL,
    "callers_of": GraphQueryIntent.SYMBOL,
    "callees_of": GraphQueryIntent.SYMBOL,
    "impact_symbol": GraphQueryIntent.SYMBOL,
    "task": GraphQueryIntent.TASK,
    "artifact": GraphQueryIntent.TASK,
}

_GRAPH_RELATION_ORDER = {
    GraphQueryIntent.FILE: (
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "KNOWLEDGE_APPLIES_TO",
        "TASK_CHANGED_FILE",
        "TASK_VERIFIED_BY",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_DERIVED_FROM_TASK",
        "CALLS",
        "TASK_RECORDED_CHANGE",
        "CHANGE_AFFECTED_FILE",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "HAS_TOPIC",
        "CONTAINS",
    ),
    GraphQueryIntent.SYMBOL: (
        "CALLS",
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "KNOWLEDGE_APPLIES_TO",
        "TASK_CHANGED_FILE",
        "TASK_VERIFIED_BY",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_DERIVED_FROM_TASK",
        "TASK_RECORDED_CHANGE",
        "CHANGE_AFFECTED_FILE",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "HAS_TOPIC",
        "CONTAINS",
    ),
    GraphQueryIntent.TASK: (
        "TASK_VERIFIED_BY",
        "TASK_CHANGED_FILE",
        "TASK_RECORDED_CHANGE",
        "CHANGE_AFFECTED_FILE",
        "KNOWLEDGE_DERIVED_FROM_TASK",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_APPLIES_TO",
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "CALLS",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "HAS_TOPIC",
        "CONTAINS",
    ),
    GraphQueryIntent.GENERIC: (
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        STRUCTURED_EDGE_KIND,
        "HAS_TOPIC",
        "CONTAINS",
        "TESTS_FILE",
        "IMPORTS_FILE",
        "CALLS",
        "KNOWLEDGE_APPLIES_TO",
        "TASK_VERIFIED_BY",
        "TASK_CHANGED_FILE",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_DERIVED_FROM_TASK",
        "TASK_RECORDED_CHANGE",
        "CHANGE_AFFECTED_FILE",
    ),
}


def _graph_query_intent(query: Any) -> GraphQueryIntent:
    if not isinstance(query, dict):
        return GraphQueryIntent.GENERIC
    return _GRAPH_QUERY_INTENT_BY_TYPE.get(str(query.get("type") or ""), GraphQueryIntent.GENERIC)


def _graph_relation_priority(kind: str, *, query_intent: GraphQueryIntent) -> int:
    order = _GRAPH_RELATION_ORDER[query_intent]
    try:
        return order.index(kind)
    except ValueError:
        return len(order)


def _compact_graph_path_key(path: dict[str, Any], *, query_intent: GraphQueryIntent) -> tuple[int, str, str, str]:
    edge = str(path.get("edge") or "")
    return (
        _graph_relation_priority(edge, query_intent=query_intent),
        edge,
        str(path.get("from") or ""),
        str(path.get("to") or ""),
    )


def _compact_graph_continuation_index(continuations: list[Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in continuations:
        if not isinstance(item, dict):
            continue
        key = _graph_selector_key(item.get("selector"))
        if key is not None and key not in by_key:
            by_key[key] = item
    return by_key


def _graph_relation_endpoints(item: dict[str, Any]) -> tuple[str, str]:
    def identity(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("id") or _graph_summary_selector_key(value) or "")
        return str(value or "")

    return identity(item.get("from")), identity(item.get("to"))


def _dedupe_compact_graph_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_test_endpoints = {
        _graph_relation_endpoints(path)
        for path in paths
        if str(path.get("edge") or "") == "TESTS_FILE"
    }
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        edge = str(path.get("edge") or "")
        endpoints = _graph_relation_endpoints(path)
        if edge == "IMPORTS_FILE" and endpoints in direct_test_endpoints:
            continue
        key = (edge, *endpoints)
        if key not in seen:
            seen.add(key)
            selected.append(path)
    return selected


def _dedupe_compact_graph_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_test_endpoints = {
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in edges
        if str(edge.get("kind") or "") == "TESTS_FILE"
    }
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        kind = str(edge.get("kind") or "")
        endpoints = (str(edge.get("from") or ""), str(edge.get("to") or ""))
        if kind == "IMPORTS_FILE" and endpoints in direct_test_endpoints:
            continue
        key = (kind, *endpoints)
        if key not in seen:
            seen.add(key)
            selected.append(edge)
    return selected


def _relation_continuation_keys(
    endpoints: list[Any],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
) -> list[tuple[str, str, str]] | None:
    keys: list[tuple[str, str, str]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        key = _graph_summary_selector_key(endpoint)
        if key is None:
            return None
        if key in match_keys:
            continue
        if key not in continuation_by_key:
            return None
        if key not in keys:
            keys.append(key)
    return keys


def _select_compact_graph_paths(
    paths: list[dict[str, Any]],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
    path_limit: int,
    continuation_limit: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    selected_paths: list[dict[str, Any]] = []
    selected_keys: list[tuple[str, str, str]] = []
    for path in paths:
        keys = _relation_continuation_keys(
            [path.get("from"), path.get("to")],
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
        )
        if keys is None:
            continue
        new_keys = [key for key in keys if key not in selected_keys]
        if len(selected_keys) + len(new_keys) > continuation_limit:
            continue
        selected_paths.append(path)
        selected_keys.extend(new_keys)
        if len(selected_paths) >= path_limit:
            break
    return selected_paths, selected_keys


def _select_compact_graph_relations(
    edges: list[dict[str, Any]],
    *,
    node_summaries: dict[str, dict[str, Any]],
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
    relation_limit: int,
    continuation_limit: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    selected_edges: list[dict[str, Any]] = []
    selected_keys: list[tuple[str, str, str]] = []
    for edge in edges:
        endpoints = [
            node_summaries.get(str(edge.get("from") or ""), {}),
            node_summaries.get(str(edge.get("to") or ""), {}),
        ]
        keys = _relation_continuation_keys(
            endpoints,
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
        )
        if keys is None:
            continue
        new_keys = [key for key in keys if key not in selected_keys]
        if len(selected_keys) + len(new_keys) > continuation_limit:
            continue
        selected_edges.append(edge)
        selected_keys.extend(new_keys)
        if len(selected_edges) >= relation_limit:
            break
    return selected_edges, selected_keys


def _compact_graph_continuations(
    keys: list[tuple[str, str, str]],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "selector": continuation_by_key[key].get("selector", {}),
            "query_types": continuation_by_key[key].get("query_types", []),
            "actions": continuation_by_key[key].get("actions", []),
        }
        for key in keys
    ]


def _compact_graph_relation_key(edge: dict[str, Any], *, query_intent: GraphQueryIntent) -> tuple[int, str, str, str]:
    kind = str(edge.get("kind") or "")
    return (
        _graph_relation_priority(kind, query_intent=query_intent),
        kind,
        str(edge.get("from") or ""),
        str(edge.get("to") or ""),
    )


def _graph_selector_key(selector: Any) -> tuple[str, str, str] | None:
    if not isinstance(selector, dict):
        return None
    kind = str(selector.get("kind") or "")
    value = str(selector.get("value") or "")
    if not kind or not value:
        return None
    return kind, value, str(selector.get("in_file") or "")


def _graph_summary_selector_key(summary: dict[str, Any]) -> tuple[str, str, str] | None:
    kind = str(summary.get("kind") or "")
    if kind == "file":
        selector = {"kind": "file", "value": summary.get("path")}
    elif kind == "symbol":
        selector = {
            "kind": "symbol",
            "value": summary.get("qualified_name") or summary.get("name"),
            "in_file": summary.get("path"),
        }
    elif kind == "import_ref":
        selector = {"kind": "import", "value": summary.get("raw_import")}
    elif kind == "topic":
        selector = {"kind": "topic", "value": summary.get("topic")}
    elif kind in {"task", "change_event"}:
        selector = {"kind": "task", "value": summary.get("task_id")}
    elif kind == "artifact":
        selector = {"kind": "document", "value": summary.get("path")}
    elif kind == "document":
        selector = {"kind": "document", "value": summary.get("path")}
    elif kind == "knowledge":
        selector = {"kind": "knowledge_record", "value": summary.get("record_id")}
    else:
        return None
    return _graph_selector_key(selector)


def _compact_graph_node(node: dict[str, Any], *, include_id: bool = True) -> dict[str, Any]:
    summary: dict[str, Any] = {"kind": node.get("kind", "")}
    if include_id:
        summary["id"] = node.get("id", "")
    identity = node.get("identity") if isinstance(node.get("identity"), dict) else {}
    facts = node.get("facts") if isinstance(node.get("facts"), dict) else {}
    provider = facts.get("provider") if isinstance(facts.get("provider"), dict) else {}
    for key, value in (
        ("path", identity.get("path")),
        ("task_id", identity.get("task_id")),
        ("raw_import", identity.get("raw_import")),
        ("topic", identity.get("topic")),
        ("name", provider.get("name")),
        ("qualified_name", provider.get("qualified_name")),
        ("symbol_kind", provider.get("kind")),
        ("record_id", identity.get("record_id")),
        ("status", (facts.get("record") or {}).get("status") if isinstance(facts.get("record"), dict) else None),
        ("knowledge_kind", (facts.get("record") or {}).get("kind") if isinstance(facts.get("record"), dict) else None),
    ):
        if value not in (None, ""):
            summary[key] = value
    return summary


def _compact_graph_path(path: dict[str, Any], *, completeness: dict[str, Any], freshness: dict[str, Any]) -> dict[str, Any]:
    compact = dict(path)
    for key in ("from", "to"):
        endpoint = compact.get(key)
        if isinstance(endpoint, dict):
            compact[key] = {name: value for name, value in endpoint.items() if name != "id"}
    source = compact.get("source") if isinstance(compact.get("source"), dict) else {}
    compact["evidence"] = _compact_relation_evidence(
        edge=str(compact.get("edge") or ""),
        assertion=str(source.get("assertion") or ""),
        provider=str(source.get("provider") or ""),
        facts=source.get("facts") if isinstance(source.get("facts"), dict) else {},
        endpoints=[compact.get("from"), compact.get("to")],
        completeness=completeness,
        freshness=freshness,
    )
    compact.pop("source", None)
    return compact


def _compact_graph_relation(
    edge: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    *,
    completeness: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    source_node = nodes.get(str(edge.get("from") or ""), {"id": edge.get("from", "")})
    target_node = nodes.get(str(edge.get("to") or ""), {"id": edge.get("to", "")})
    relation: dict[str, Any] = {
        "from": source_node,
        "edge": edge.get("kind", ""),
        "to": target_node,
        "evidence": _compact_relation_evidence(
            edge=str(edge.get("kind") or ""),
            assertion=str(edge.get("assertion") or ""),
            provider=str(edge.get("source") or ""),
            facts=edge.get("facts") if isinstance(edge.get("facts"), dict) else {},
            endpoints=[source_node, target_node],
            completeness=completeness,
            freshness=freshness,
        ),
    }
    if isinstance(edge.get("facts"), dict) and edge["facts"]:
        relation["facts"] = edge["facts"]
    return relation


def _compact_relation_evidence(
    *,
    edge: str,
    assertion: str,
    provider: str,
    facts: dict[str, Any],
    endpoints: list[Any],
    completeness: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    capability_by_edge = {
        "CALLS": "calls",
        "DECLARES_IMPORT": "imports",
        "IMPORTS_FILE": "imports",
        "RESOLVES_TO": "imports",
        "TESTS_FILE": "imports",
        STRUCTURED_EDGE_KIND: "structured_relations",
        "TASK_RECORDED_CHANGE": "task_history",
        "CHANGE_AFFECTED_FILE": "task_history",
        "TASK_CHANGED_FILE": "task_history",
        "TASK_VERIFIED_BY": "task_history",
        "KNOWLEDGE_APPLIES_TO": "knowledge",
        "KNOWLEDGE_SOURCED_FROM": "knowledge",
        "KNOWLEDGE_DERIVED_FROM_TASK": "knowledge",
    }
    capabilities = completeness.get("capabilities") if isinstance(completeness.get("capabilities"), dict) else {}
    confidence = str(facts.get("confidence") or "unknown")
    changed_paths = {str(path) for path in freshness.get("changed_paths", []) if str(path)}
    changed_root_paths = {str(path) for path in freshness.get("changed_root_paths", []) if str(path)}
    endpoint_paths = {
        str(endpoint.get("path") or "")
        for endpoint in endpoints
        if isinstance(endpoint, dict) and str(endpoint.get("path") or "")
    }
    freshness_status = "stale" if endpoint_paths & changed_paths else "current"
    root_evidence_edges = {
        "HAS_TOPIC",
        "TASK_RECORDED_CHANGE",
        "CHANGE_AFFECTED_FILE",
        "TASK_CHANGED_FILE",
        "TASK_VERIFIED_BY",
        "KNOWLEDGE_APPLIES_TO",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_DERIVED_FROM_TASK",
    }
    if endpoint_paths & changed_root_paths or (freshness.get("root_evidence_changed") and edge in root_evidence_edges):
        freshness_status = "stale"
    recorded_freshness = str(facts.get("freshness") or "")
    if recorded_freshness == "stale":
        freshness_status = "stale"
    elif recorded_freshness in {"current", "reviewed"} and freshness_status != "stale":
        freshness_status = "current"
    if str(freshness.get("status") or "") not in {"current", "stale"}:
        freshness_status = "stale" if recorded_freshness == "stale" else "unknown"
    return {
        "type": str(facts.get("evidence_type") or edge.casefold()),
        "assertion": assertion,
        "provider": provider,
        "confidence": confidence,
        "completeness": str(capabilities.get(capability_by_edge.get(edge, "file_inventory")) or completeness.get("status") or "partial"),
        "freshness": freshness_status,
    }


def _compact_graph_completeness(completeness: Any) -> dict[str, Any]:
    if not isinstance(completeness, dict):
        return {}
    return {"status": str(completeness.get("status") or "partial")}


def _compact_graph_query_warnings(completeness: Any) -> list[dict[str, str]]:
    if not isinstance(completeness, dict):
        return []
    warnings: list[dict[str, str]] = []
    capabilities = completeness.get("capabilities") if isinstance(completeness.get("capabilities"), dict) else {}
    if not completeness.get("code_facts_complete", True):
        warnings.append({"code": "graph_code_relations_partial", "message": "some code relations are unavailable from the current materialization"})
    if str(capabilities.get("task_history") or "complete") != "complete":
        warnings.append({"code": "graph_task_history_partial", "message": "some task-history relations are unavailable from the current materialization"})
    if any(str(capabilities.get(name) or "complete") != "complete" for name in ("imports", "symbols", "calls")):
        warnings.append({"code": "graph_semantic_relations_partial", "message": "some semantic relations are unavailable from the current materialization"})
    if str(capabilities.get("structured_relations") or "complete") != "complete":
        warnings.append({"code": "graph_structured_relations_partial", "message": "some structured file relations are unavailable from the current materialization"})
    if str(capabilities.get("knowledge") or "complete") != "complete":
        warnings.append({"code": "graph_knowledge_partial", "message": "some reviewed-Knowledge relations are unavailable from the current materialization"})
    return warnings


def cmd_context_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    bundle, problems, meta = build_context_bundle(root, target=target, query=args.query, explain=args.explain, mode=args.mode or "")
    bundle_data = None
    if bundle is not None:
        bundle_data = bundle.to_dict() if args.full else compact_context_bundle(bundle)
    data = {"bundle": bundle_data, "repository": target.to_dict()}
    if args.full or args.explain:
        data.update(meta)
    payload = {
        "ok": bundle is not None and not _has_errors(problems),
        "command": "context query",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "context_not_authoritative",
                "message": "context query returns a read-only evidence bundle; source authorities remain repo registry, source documents, .repometa, task receipts, and reviewed Knowledge records",
            }
        ],
    }
    output_format = "json" if args.json else args.format
    if output_format == "json":
        _json(payload, compact=not args.full and not args.explain)
    elif output_format == "markdown":
        if bundle is not None:
            print(render_context_markdown(bundle), end="")
        for problem in problems:
            print(problem.message)
    else:
        if bundle is not None:
            print(f"context bundle {bundle.bundle_digest} repository={target.id} evidence={len(bundle.evidence)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_benchmark(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    fixture = Path(args.fixture)
    if not fixture.is_absolute():
        fixture = root / fixture
    category_gates, category_gate_problems = _parse_category_recall_gates(args.min_category_recall_at_5 or [])
    knowledge_category_gates, knowledge_category_gate_problems = _parse_category_recall_gates(args.min_category_knowledge_recall_at_5 or [])
    edge_category_gates, edge_category_gate_problems = _parse_category_recall_gates(args.min_category_graph_edge_recall or [])
    visible_category_gates, visible_category_gate_problems = _parse_category_recall_gates(args.min_category_visible_recall or [])
    data, problems = run_context_benchmark(
        root,
        fixture=fixture,
        repo_id=args.repo_id or "",
        min_recall_at_5=args.min_recall_at_5,
        min_precision_at_5=args.min_precision_at_5,
        min_knowledge_recall_at_5=args.min_knowledge_recall_at_5,
        min_category_recall_at_5=category_gates,
        min_category_knowledge_recall_at_5=knowledge_category_gates,
        min_category_graph_edge_recall=edge_category_gates,
        min_category_visible_recall=visible_category_gates,
        require_source_integrity=args.require_source_integrity,
        require_knowledge_source_current=args.require_knowledge_source_current,
        require_no_forbidden=args.require_no_forbidden,
        require_no_cross_repo=args.require_no_cross_repo,
        require_fixture_corpus=args.require_fixture_corpus,
    )
    problems = [*category_gate_problems, *knowledge_category_gate_problems, *edge_category_gate_problems, *visible_category_gate_problems, *problems]
    payload = {
        "ok": not _has_errors(problems),
        "command": "context benchmark",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "context_benchmark_retrieval_only",
                "message": "context benchmark measures retrieval quality only; it does not validate generated answers",
            }
        ],
    }
    if args.output:
        output, output_problem = _workspace_output_path(root, args.output, code="context_benchmark_output_outside_workspace")
        if output_problem is not None:
            problems.append(output_problem)
            payload["ok"] = False
            payload["problems"] = [problem.to_dict() for problem in problems]
        else:
            if data:
                data["artifact"] = {
                    "path": output.relative_to(root).as_posix(),
                    "benchmark_digest": data.get("benchmark_digest", ""),
                }
            _complete_json_envelope(payload)
            atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if args.json:
        _json(payload)
    else:
        summary = data.get("summary", {}) if data else {}
        print(f"context benchmark questions={data.get('question_count', 0)} recall@5={summary.get('mean_recall_at_5', 0)} precision@5={summary.get('mean_precision_at_5', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_benchmark_materialize(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    fixture = Path(args.fixture)
    if not fixture.is_absolute():
        fixture = root / fixture
    data, problems = materialize_context_benchmark_corpus(root, fixture=fixture, repo_id=args.repo_id or "", force=args.force)
    payload = {
        "ok": not _has_errors(problems),
        "command": "context benchmark-materialize",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "context_benchmark_materialize_mutates_workspace",
                "message": "benchmark materialize writes fixture corpus files into product repositories for controlled retrieval tests",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        totals = data.get("totals", {}) if data else {}
        print(f"context benchmark-materialize created={totals.get('created', 0)} unchanged={totals.get('unchanged', 0)} overwritten={totals.get('overwritten', 0)} conflicts={totals.get('conflict', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _parse_category_recall_gates(values: list[str]) -> tuple[dict[str, float], list[Problem]]:
    gates: dict[str, float] = {}
    problems: list[Problem] = []
    for value in values:
        category, separator, raw_threshold = value.partition("=")
        category = category.strip()
        raw_threshold = raw_threshold.strip()
        if not separator or not category or not raw_threshold:
            problems.append(Problem("error", "context_benchmark_category_gate_invalid", "category recall gate must use category=threshold", value))
            continue
        try:
            threshold = float(raw_threshold)
        except ValueError:
            problems.append(Problem("error", "context_benchmark_category_gate_invalid", "category recall gate threshold must be numeric", value))
            continue
        if threshold < 0 or threshold > 1:
            problems.append(Problem("error", "context_benchmark_category_gate_invalid", "category recall gate threshold must be between 0 and 1", value))
            continue
        gates[category] = threshold
    return gates, problems


def cmd_context_benchmark_compare(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    baseline = Path(args.baseline)
    candidate = Path(args.candidate)
    if not baseline.is_absolute():
        baseline = root / baseline
    if not candidate.is_absolute():
        candidate = root / candidate
    data, problems = compare_context_benchmarks(
        root=root,
        baseline_path=baseline,
        candidate_path=candidate,
        max_recall_at_5_drop=args.max_recall_at_5_drop,
        max_precision_at_5_drop=args.max_precision_at_5_drop,
        max_knowledge_recall_at_5_drop=args.max_knowledge_recall_at_5_drop,
        max_question_recall_at_5_drop=args.max_question_recall_at_5_drop,
        require_current_sources=args.require_current_sources,
    )
    payload = {
        "ok": not _has_errors(problems),
        "command": "context benchmark-compare",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        deltas = data.get("metric_deltas", {}) if data else {}
        recall = deltas.get("mean_recall_at_5", {}).get("delta", 0)
        precision = deltas.get("mean_precision_at_5", {}).get("delta", 0)
        print(f"context benchmark compare recall@5_delta={recall} precision@5_delta={precision}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_pack(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    data, problems, meta = build_task_context_pack(root, target=target, task_id=args.task, budget_tokens=args.budget_tokens, explain=args.explain)
    payload_data = {**data, **meta} if args.full else {**compact_task_context_pack(data), **meta}
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack",
        "data": payload_data,
        "problems": [problem.to_dict() for problem in problems if problem.severity == "error"],
        "warnings": [*data.get("warnings", []), *[problem.to_dict() for problem in problems if problem.severity == "warning"]],
    }
    output_format = "json" if args.json else args.format
    written_output = ""
    if args.output and not _has_errors(problems):
        output, output_problem = _workspace_output_path(root, args.output, code="context_pack_output_outside_workspace")
        if output_problem is not None:
            problems.append(output_problem)
            payload["ok"] = False
            payload["problems"] = [problem.to_dict() for problem in problems if problem.severity == "error"]
        else:
            written_output = output.relative_to(root).as_posix()
            if data and output_format == "json":
                payload["data"]["artifact"] = {
                    "path": output.relative_to(root).as_posix(),
                    "pack_digest": payload["data"].get("pack_digest", ""),
                }
            _complete_json_envelope(payload)
            if output_format == "markdown":
                atomic_write(output, render_task_context_pack_markdown(data))
            else:
                atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if output_format == "json":
        _json(payload)
    elif output_format == "markdown":
        if written_output:
            print(f"context pack written: {written_output}")
        elif data and not args.output:
            print(render_task_context_pack_markdown(data), end="")
        for problem in problems:
            print(problem.message)
    else:
        groups = data.get("groups", {})
        print(f"context pack task={data.get('task', {}).get('id', args.task)} must_read={len(groups.get('must_read', []))} likely_change={len(groups.get('likely_change', []))} impact={len(groups.get('impact', []))} verification={len(groups.get('verification', []))}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_pack_compare(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    baseline = Path(args.baseline)
    candidate = Path(args.candidate)
    if not baseline.is_absolute():
        baseline = root / baseline
    if not candidate.is_absolute():
        candidate = root / candidate
    data, problems = compare_task_context_packs(
        baseline_path=baseline,
        candidate_path=candidate,
        max_must_read_drop=args.max_must_read_drop,
        require_warning_stability=args.require_warning_stability,
    )
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack-compare",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        deltas = data.get("count_deltas", {}) if data else {}
        must_read = deltas.get("must_read", {}).get("delta", 0)
        print(f"context pack compare must_read_delta={must_read}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_pack_benchmark(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    fixture = Path(args.fixture)
    if not fixture.is_absolute():
        fixture = root / fixture
    data, problems = run_task_context_pack_benchmark(
        root,
        target=target,
        fixture=fixture,
        budget_tokens=args.budget_tokens,
        explain=args.explain,
        min_must_read_recall=args.min_must_read_recall,
    )
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack-benchmark",
        "data": data,
        "problems": [problem.to_dict() for problem in problems if problem.severity == "error"],
        "warnings": [
            *[problem.to_dict() for problem in problems if problem.severity == "warning"],
            {
                "code": "context_pack_benchmark_retrieval_only",
                "message": "context pack benchmark measures source pack recall only; it does not validate generated answers or task scope",
            }
        ],
    }
    if args.output:
        output, output_problem = _workspace_output_path(root, args.output, code="context_pack_benchmark_output_outside_workspace")
        if output_problem is not None:
            problems.append(output_problem)
            payload["ok"] = False
            payload["problems"] = [problem.to_dict() for problem in problems]
        else:
            if data:
                data["artifact"] = {
                    "path": output.relative_to(root).as_posix(),
                    "benchmark_digest": data.get("benchmark_digest", ""),
                }
            _complete_json_envelope(payload)
            atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if args.json:
        _json(payload)
    else:
        summary = data.get("summary", {}) if data else {}
        print(f"context pack benchmark cases={data.get('case_count', 0) if data else 0} must_read_recall={summary.get('mean_must_read_recall', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_pack_benchmark_materialize(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    fixture = Path(args.fixture)
    if not fixture.is_absolute():
        fixture = root / fixture
    data, problems = materialize_task_context_pack_benchmark_tasks(root, fixture=fixture, force=args.force)
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack-benchmark-materialize",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "context_pack_benchmark_materialize_mutates_workspace",
                "message": "context pack benchmark materialize writes archived fixture tasks for controlled startup-pack tests",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        totals = data.get("totals", {}) if data else {}
        print(f"context pack benchmark tasks materialized created={totals.get('created', 0)} unchanged={totals.get('unchanged', 0)} conflicts={totals.get('conflict', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_context_pack_benchmark_compare(args: argparse.Namespace) -> int:
    baseline = Path(args.baseline)
    candidate = Path(args.candidate)
    data, problems = compare_task_context_pack_benchmarks(
        baseline_path=baseline,
        candidate_path=candidate,
        max_mean_must_read_recall_drop=args.max_mean_must_read_recall_drop,
    )
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack-benchmark-compare",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        deltas = data.get("metric_deltas", {}) if data else {}
        recall = deltas.get("mean_must_read_recall", {}).get("delta", 0)
        print(f"context pack benchmark compare mean_must_read_recall_delta={recall}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_candidate_build(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    claim, claim_problems = _knowledge_candidate_claim_input(root, args)
    from_task = getattr(args, "from_task", "")
    source_modes = [bool(args.source), bool(args.from_receipt), bool(args.from_pack), bool(from_task)]
    if claim_problems:
        data = {}
        problems = claim_problems
    elif sum(1 for enabled in source_modes if enabled) != 1:
        data: dict[str, Any] = {}
        problems = [Problem("error", "knowledge_candidate_source_required", "provide exactly one of --source, --from-receipt, --from-pack, or --from-task")]
    elif from_task:
        data, problems = build_knowledge_candidate_from_receipt(
            root,
            task_id=from_task,
            repo_id=args.repo_id,
            kind=args.kind,
            write=not getattr(args, "dry_run", False),
            claim=claim,
        )
    elif args.from_receipt:
        data, problems = build_knowledge_candidate_from_receipt(root, task_id=args.from_receipt, repo_id=args.repo_id, kind=args.kind, claim=claim)
    elif args.from_pack:
        data, problems = build_knowledge_candidate_from_pack(root, pack=Path(args.from_pack), repo_id=args.repo_id, kind=args.kind, claim=claim)
    else:
        data, problems = build_knowledge_candidate(root, source=Path(args.source), repo_id=args.repo_id, kind=args.kind, claim=claim)
    response_data = data
    if getattr(args, "knowledge_candidate_command", "") == "suggest" and not getattr(args, "full", False):
        response_data = _compact_knowledge_candidate_data(data)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge candidate suggest" if getattr(args, "knowledge_candidate_command", "") == "suggest" else "knowledge candidate build",
        "data": response_data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "knowledge_candidate_not_authoritative",
                "message": "knowledge candidates are review inputs only; they are not canonical knowledge records",
            }
        ],
        "next_actions": (
            _knowledge_candidate_next_actions(
                data,
                repo_id=args.repo_id,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
            if not _has_errors(problems)
            else _next_actions_for_problems(problems, data={"task_id": from_task or args.from_receipt or "T-..."})
        ),
    }
    if args.json:
        _json(payload)
    else:
        candidate = data.get("candidate", {}) if data else {}
        print(f"knowledge candidate {candidate.get('id', '')} path={data.get('path', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _knowledge_candidate_next_actions(data: dict[str, Any], *, repo_id: str, dry_run: bool) -> list[dict[str, str]]:
    candidate = data.get("candidate") if isinstance(data.get("candidate"), dict) else {}
    candidate_id = str(candidate.get("id") or "")
    if not candidate_id or dry_run:
        return []
    return [
        {
            "label": "Review the candidate with source-currentness checks",
            "command": f"./scripts/repoctl knowledge candidate show {candidate_id} --repo-id {repo_id} --format markdown",
        },
        {
            "label": "Run the candidate contract check",
            "command": f"./scripts/repoctl knowledge candidate check {candidate_id} --repo-id {repo_id} --json",
        },
        {
            "label": "Approve only after reviewing the claim and sources",
            "command": f"./scripts/repoctl knowledge approve {candidate_id} --repo-id {repo_id} --reviewed-by <label> --note-file <review-note.md> --json",
        },
    ]


def _knowledge_candidate_claim_input(root: Path, args: argparse.Namespace) -> tuple[str, list[Problem]]:
    claim = str(getattr(args, "claim", "") or "").strip()
    claim_file = str(getattr(args, "claim_file", "") or "").strip()
    if not claim_file:
        return claim, []
    path = Path(claim_file)
    resolved = path if path.is_absolute() else root / path
    if not resolved.is_file():
        return "", [Problem("error", "knowledge_candidate_claim_file_missing", "knowledge candidate claim file is missing", claim_file)]
    try:
        claim = resolved.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        return "", [Problem("error", "knowledge_candidate_claim_file_unreadable", str(exc), claim_file)]
    if not claim:
        return "", [Problem("error", "knowledge_candidate_claim_file_empty", "knowledge candidate claim file is empty", claim_file)]
    return claim, []


def _compact_knowledge_candidate_data(data: dict[str, Any]) -> dict[str, Any]:
    candidate = data.get("candidate") if isinstance(data.get("candidate"), dict) else None
    if candidate is None:
        return data
    compact_candidate = dict(candidate)
    compact_candidate.pop("summary", None)
    derived = compact_candidate.get("derived_from") if isinstance(compact_candidate.get("derived_from"), dict) else {}
    compact_candidate["derived_from"] = _compact_knowledge_derived_from(derived)
    return {**data, "candidate": compact_candidate}


def _compact_knowledge_derived_from(derived: dict[str, Any]) -> dict[str, Any]:
    compact_derived = dict(derived)
    compact_derived.pop("related_symbols", None)
    compact_derived.pop("related_symbol_warnings", None)
    return compact_derived


def _compact_knowledge_approval_data(data: dict[str, Any]) -> dict[str, Any]:
    record = data.get("record") if isinstance(data.get("record"), dict) else None
    if record is None:
        return data
    compact_record = dict(record)
    compact_record.pop("summary", None)
    created_from = compact_record.get("created_from") if isinstance(compact_record.get("created_from"), dict) else {}
    compact_created_from = dict(created_from)
    candidate_derived = compact_created_from.get("candidate_derived_from")
    if isinstance(candidate_derived, dict):
        compact_created_from["candidate_derived_from"] = _compact_knowledge_derived_from(candidate_derived)
    compact_record["created_from"] = compact_created_from
    return {**data, "record": compact_record}


def cmd_knowledge_candidate_list(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data = list_knowledge_candidates(root, repo_id=args.repo_id, with_checks=args.with_checks)
    payload = {
        "ok": True,
        "command": "knowledge candidate list",
        "data": data,
        "problems": [],
        "warnings": [
            {
                "code": "knowledge_candidate_not_authoritative",
                "message": "knowledge candidates are review inputs only; they are not canonical knowledge records",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge candidates repo_id={args.repo_id} count={len(data.get('candidates', []))}")
    return 0


def cmd_knowledge_status(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data = knowledge_status(root, repo_id=args.repo_id)
    payload = {
        "ok": True,
        "command": "knowledge status",
        "data": data,
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge status repo_id={args.repo_id} candidates={data['candidate_count']} records={data['record_count']} events={data['event_count']}")
    return 0


def cmd_knowledge_event_list(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data = list_knowledge_events(root, repo_id=args.repo_id, event_type=args.type, candidate_id=args.candidate_id, record_id=args.record_id)
    payload = {
        "ok": True,
        "command": "knowledge event list",
        "data": data,
        "problems": [],
        "warnings": [
            {
                "code": "knowledge_events_are_append_only",
                "message": "knowledge events are append-only lifecycle evidence",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge events repo_id={args.repo_id} count={data.get('event_count', 0)}")
    return 0


def cmd_knowledge_event_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = show_knowledge_event(root, repo_id=args.repo_id, event_id=args.event_id)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge event show",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "knowledge_events_are_append_only",
                "message": "knowledge events are append-only lifecycle evidence",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        event = data.get("event", {}) if data else {}
        print(f"knowledge event {event.get('id', args.event_id)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_candidate_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = show_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id)
    check_data: dict[str, Any] = {}
    check_problems: list[Problem] = []
    if not problems:
        check_data, check_problems = check_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id)
    candidate = data.get("candidate") if isinstance(data.get("candidate"), dict) else {}
    candidate_id = str(candidate.get("id") or args.candidate_id)
    review_actions = []
    if data and not _has_errors([*problems, *check_problems]):
        review_actions = [
            {
                "label": "Approve after reviewing the claim and sources",
                "command": f"./scripts/repoctl knowledge approve {candidate_id} --repo-id {args.repo_id} --reviewed-by <label> --note-file <review-note.md> --json",
            },
            {
                "label": "Reject if the claim is not reusable or source-grounded",
                "command": f"./scripts/repoctl knowledge reject {candidate_id} --repo-id {args.repo_id} --reason-file <reason.md> --json",
            },
        ]
    payload = {
        "ok": not _has_errors([*problems, *check_problems]),
        "command": "knowledge candidate show",
        "data": {**data, "review_summary": check_data} if data else data,
        "problems": [problem.to_dict() for problem in [*problems, *check_problems] if problem.severity == "error"],
        "warnings": [
            {
                "code": "knowledge_candidate_not_authoritative",
                "message": "knowledge candidates are review inputs only; they are not canonical knowledge records",
            }
        ]
        + [problem.to_dict() for problem in check_problems if problem.severity == "warning"],
        "next_actions": review_actions,
    }
    if args.json:
        _json(payload)
    else:
        if args.format == "markdown":
            print(_render_knowledge_candidate_review_markdown(data, check_data, repo_id=args.repo_id))
        else:
            candidate = data.get("candidate", {}) if data else {}
            print(f"knowledge candidate {candidate.get('id', args.candidate_id)}")
            if data:
                print(f"kind={candidate.get('kind', '')} claim={candidate.get('claim', '')}")
        for problem in [*problems, *check_problems]:
            print(problem.message)
    return 1 if _has_errors([*problems, *check_problems]) else 0


def _render_knowledge_candidate_review_markdown(data: dict[str, Any], check_data: dict[str, Any], *, repo_id: str) -> str:
    candidate = data.get("candidate", {}) if isinstance(data.get("candidate"), dict) else {}
    lines = [
        f"# Knowledge Candidate Review: {candidate.get('id', '')}",
        "",
        "## Candidate",
        "",
        f"- Repo: `{repo_id}`",
        f"- Kind: `{candidate.get('kind', '')}`",
        f"- Title: {candidate.get('title', '')}",
        f"- Claim: {candidate.get('claim', '')}",
        f"- Authoritative: `{str(candidate.get('authoritative', ''))}`",
        "",
        "## Summary",
        "",
        str(candidate.get("summary") or "").strip() or "_No summary._",
        "",
        "## Origin",
        "",
    ]
    derived = candidate.get("derived_from") if isinstance(candidate.get("derived_from"), dict) else {}
    if derived:
        lines.append(f"- Kind: `{derived.get('kind', '')}`")
        for key in ("task_id", "repo_id", "verification_artifact", "path", "pack_digest", "record_id", "record_digest"):
            if derived.get(key):
                lines.append(f"- {key}: `{derived.get(key)}`")
        changed_files = derived.get("changed_files") if isinstance(derived.get("changed_files"), list) else []
        if changed_files:
            lines.append("- Changed files: " + ", ".join(f"`{item}`" for item in changed_files))
    else:
        lines.append("- Kind: `authority_document`")
    lines.extend(["", "## Source Refs", ""])
    for ref in candidate.get("source_refs", []) if isinstance(candidate.get("source_refs"), list) else []:
        if not isinstance(ref, dict):
            continue
        lines.append(
            f"- `{ref.get('path', '')}` section `{ref.get('section', '')}` kind `{ref.get('kind', '')}` digest `{ref.get('content_sha256', '')}`"
        )
    lines.extend(["", "## Source Currentness", ""])
    for status in _candidate_source_statuses_for_review(check_data, candidate):
        lines.append(
            f"- `{status.get('path', '')}` exists=`{status.get('exists', False)}` digest_matches=`{status.get('digest_matches', False)}`"
        )
    lines.extend(["", "## Related Current Records", ""])
    related = check_data.get("related_records") if isinstance(check_data.get("related_records"), list) else []
    if related:
        for item in related:
            lines.append(f"- `{item.get('record_id', '')}` status `{item.get('status', '')}` relation `{item.get('relation', '')}`")
    else:
        lines.append("- None found.")
    lines.extend(["", "## Review Check", ""])
    lines.append(f"- Passed: `{check_data.get('passed', False)}`")
    for problem in check_data.get("problems", []) if isinstance(check_data.get("problems"), list) else []:
        lines.append(f"- Error `{problem.get('code', '')}`: {problem.get('message', '')}")
    for warning in check_data.get("warnings", []) if isinstance(check_data.get("warnings"), list) else []:
        lines.append(f"- Warning `{warning.get('code', '')}`: {warning.get('message', '')}")
    lines.extend(
        [
            "",
            "## Next Commands",
            "",
            f"- Approve: `./scripts/repoctl knowledge approve {candidate.get('id', '')} --repo-id {repo_id} --reviewed-by <label> --note-file /tmp/review-note.md --json`",
            f"- Reject: `./scripts/repoctl knowledge reject {candidate.get('id', '')} --repo-id {repo_id} --reason-file /tmp/reject-reason.md --json`",
            f"- Supersede: `./scripts/repoctl knowledge approve {candidate.get('id', '')} --repo-id {repo_id} --supersedes K-... --reviewed-by <label> --note-file /tmp/review-note.md --json`",
        ]
    )
    return "\n".join(lines)


def _candidate_source_statuses_for_review(check_data: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = check_data.get("source_ref_statuses")
    if isinstance(statuses, list):
        return [status for status in statuses if isinstance(status, dict)]
    return [
        {
            "path": ref.get("path", ""),
            "exists": "",
            "digest_matches": "",
        }
        for ref in candidate.get("source_refs", [])
        if isinstance(ref, dict)
    ]


def cmd_knowledge_candidate_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    if args.all:
        data, problems = check_all_knowledge_candidates(root, repo_id=args.repo_id, pending_only=not args.all_states)
    elif args.candidate_id:
        data, problems = check_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id)
    else:
        data = {}
        problems = [Problem("error", "knowledge_candidate_check_target_required", "provide a candidate id or --all")]
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge candidate check",
        "data": data,
        "problems": [problem.to_dict() for problem in problems if problem.severity == "error"],
        "warnings": [problem.to_dict() for problem in problems if problem.severity == "warning"],
    }
    if args.json:
        _json(payload)
    else:
        if args.all:
            print(f"knowledge candidate check repo_id={args.repo_id} candidates={data.get('candidate_count', 0)} errors={data.get('error_count', 0)} warnings={data.get('warning_count', 0)}")
        else:
            print(f"knowledge candidate check candidate={args.candidate_id} passed={data.get('passed', False)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_candidate_refresh(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    if args.all_stale:
        data, problems = refresh_stale_knowledge_candidates(root, repo_id=args.repo_id, include_records=args.include_records)
    elif args.record_id:
        data, problems = refresh_knowledge_record_candidate(root, repo_id=args.repo_id, record_id=args.record_id)
    elif args.candidate_id:
        data, problems = refresh_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id)
    else:
        data = {}
        problems = [Problem("error", "knowledge_candidate_refresh_target_required", "provide a candidate id, --record-id, or --all-stale")]
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge candidate refresh",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "knowledge_candidate_refresh_creates_new_candidate",
                "message": "refresh creates a new candidate and leaves the original candidate unchanged",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        if args.all_stale:
            print(f"knowledge candidate refresh repo_id={args.repo_id} refreshed={data.get('refreshed_count', 0)} skipped={data.get('skipped_count', 0)}")
        elif args.record_id:
            candidate = data.get("candidate", {}) if data else {}
            print(f"knowledge candidate refresh record={args.record_id} new={candidate.get('id', '')}")
        else:
            candidate = data.get("candidate", {}) if data else {}
            print(f"knowledge candidate refresh old={args.candidate_id} new={candidate.get('id', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_approve(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    review_note = ""
    note_problems: list[Problem] = []
    if args.note_file:
        note_path = Path(args.note_file)
        if not note_path.is_absolute():
            note_path = root / note_path
        if not note_path.is_file():
            note_problems.append(Problem("error", "knowledge_approve_note_missing", "approval note file is missing", note_path.as_posix()))
        else:
            review_note = note_path.read_text(encoding="utf-8").strip()
            if not review_note:
                note_problems.append(Problem("error", "knowledge_approve_note_empty", "approval note file is empty", note_path.as_posix()))
    if note_problems:
        data, problems = {}, note_problems
    else:
        data, problems = approve_knowledge_candidate(
            root,
            repo_id=args.repo_id,
            candidate_id=args.candidate_id,
            supersedes=args.supersedes,
            reviewed_by=args.reviewed_by,
            review_note=review_note,
        )
    record = data.get("record", {}) if data else {}
    response_data = data if getattr(args, "full", False) else _compact_knowledge_approval_data(data)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge approve",
        "data": response_data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _knowledge_approval_warnings(record),
        "next_actions": [
            {
                "label": "Refresh the non-authoritative llmwiki view",
                "command": f"./scripts/repoctl knowledge render --repo-id {args.repo_id} --json",
            }
        ]
        if data and not _has_errors(problems)
        else [],
    }
    if args.json:
        _json(payload)
    else:
        record = data.get("record", {}) if data else {}
        print(f"knowledge record {record.get('id', '')} path={data.get('record_path', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _knowledge_approval_warnings(record: dict[str, Any]) -> list[dict[str, str]]:
    created_from = record.get("created_from") if isinstance(record.get("created_from"), dict) else {}
    candidate_check = created_from.get("candidate_check") if isinstance(created_from.get("candidate_check"), dict) else {}
    warning_codes = candidate_check.get("warning_codes") if isinstance(candidate_check.get("warning_codes"), list) else []
    return [
        {
            "severity": "warning",
            "code": str(code),
            "message": "candidate was approved with a non-blocking warning",
        }
        for code in warning_codes
        if str(code)
    ]


def cmd_knowledge_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = show_knowledge_record(root, record_id=args.record_id, repo_id=args.repo_id)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge show",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        record = data.get("record", {}) if data else {}
        print(f"knowledge record {record.get('id', args.record_id)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_reject(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = reject_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id, reason_file=Path(args.reason_file))
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge reject",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        event = data.get("event", {}) if data else {}
        print(f"knowledge reject event={event.get('id', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_deprecate(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = deprecate_knowledge_record(root, repo_id=args.repo_id, record_id=args.record_id, reason_file=Path(args.reason_file))
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge deprecate",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "knowledge_deprecation_is_append_only",
                "message": "deprecation writes a lifecycle event and does not edit the record body",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        event = data.get("event", {}) if data else {}
        print(f"knowledge deprecate event={event.get('id', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = check_knowledge_records(root, repo_id=args.repo_id)
    warnings: list[Problem] = []
    if args.include_candidates:
        candidate_data, candidate_problems = check_all_knowledge_candidates(root, repo_id=args.repo_id, pending_only=True)
        data["candidate_checks"] = candidate_data
        problems.extend(problem for problem in candidate_problems if problem.severity == "error")
        warnings.extend(problem for problem in candidate_problems if problem.severity == "warning")
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge check",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [problem.to_dict() for problem in warnings],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge check repo_id={args.repo_id} records={data.get('record_count', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    include_stale = args.include_stale or args.include_history
    include_superseded = args.include_superseded or args.include_history
    include_deprecated = args.include_deprecated or args.include_history
    data, problems, warnings = query_knowledge_records(root, repo_id=args.repo_id, query=args.query, include_stale=include_stale, include_superseded=include_superseded, include_deprecated=include_deprecated, limit=args.limit, explain=args.explain)
    if int(data.get("available_record_count") or 0) == 0:
        warnings.append(
            Problem(
                "warning",
                "knowledge_records_empty",
                "no reviewed knowledge records exist for this repo; this is normal before candidates are explicitly approved",
                args.repo_id,
            )
        )
    response_data = data if args.full else _compact_knowledge_query_data(data)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge query",
        "data": response_data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [problem.to_dict() for problem in warnings],
    }
    if args.json:
        _json(payload, compact=not args.full and not args.explain)
    else:
        print(f"knowledge query repo_id={args.repo_id} results={data.get('result_count', 0)}")
        for problem in [*problems, *warnings]:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _compact_knowledge_query_data(data: dict[str, Any]) -> dict[str, Any]:
    compact = dict(data)
    results = data.get("results") if isinstance(data.get("results"), list) else []
    compact_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        compact_item = dict(item)
        record = item.get("record") if isinstance(item.get("record"), dict) else None
        if record is not None:
            compact_record = dict(record)
            compact_record.pop("summary", None)
            compact_item["record"] = compact_record
        compact_results.append(compact_item)
    compact["results"] = compact_results
    return compact


def cmd_knowledge_render(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    output = Path(args.output) if args.output else _default_knowledge_render_output(args.repo_id)
    data, problems = render_knowledge(root, repo_id=args.repo_id, output=output, check=args.check)
    response_data = data if args.full else _compact_knowledge_render_data(data)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge render",
        "data": response_data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "knowledge_render_not_authoritative",
                "message": "rendered knowledge pages are generated views and must not be ingested as source authority",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge render output={data.get('output', '')} records={data.get('record_count', 0)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def _compact_knowledge_render_data(data: dict[str, Any]) -> dict[str, Any]:
    rendered = data.get("rendered") if isinstance(data.get("rendered"), list) else []
    removed = data.get("removed") if isinstance(data.get("removed"), list) else []
    compact = {key: value for key, value in data.items() if key not in {"rendered", "removed"}}
    counts = {
        "records": 0,
        "file_targets": 0,
        "symbol_targets": 0,
        "other": 0,
    }
    for item in rendered:
        path = str(item.get("path") or "") if isinstance(item, dict) else ""
        if "/records/" in path:
            counts["records"] += 1
        elif "/targets/files/" in path:
            counts["file_targets"] += 1
        elif "/targets/symbols/" in path:
            counts["symbol_targets"] += 1
        else:
            counts["other"] += 1
    compact["page_counts"] = {"total": len(rendered), **counts}
    if "removed" in data:
        compact["removed_count"] = len(removed)
    return compact


def _default_knowledge_render_output(repo_id: str) -> Path:
    if repo_id == "main":
        return Path("docs/knowledge/generated")
    return Path("docs/knowledge/generated") / repo_id


def cmd_upgrade_status(args: argparse.Namespace) -> int:
    root = _workspace_root_or_cwd()
    status_data, problems = upgrade_status(root)
    data = {
        **_version_data(root),
        **status_data,
        "next_command": "./scripts/repoctl upgrade plan --from /path/to/agent-handoff-template --json",
    }
    payload = {
        "ok": not problems,
        "command": "upgrade status",
        "data": data,
        "problems": problems,
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"repoctl upgrade status version={data['version']} status={data['status']} receipts={data['receipt_count']}")
        if data.get("latest"):
            print(f"latest backup: {data['latest']['backup']['availability']}")
        print(data["next_command"])
    return 1 if problems else 0


def cmd_upgrade_plan(args: argparse.Namespace) -> int:
    root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else find_workspace_root()
    data = plan_upgrade(root, source=args.source)
    problems = [{"severity": "error", **conflict} for conflict in data.get("conflicts", [])]
    payload = {
        "ok": not problems,
        "command": "upgrade.plan",
        "data": data,
        "problems": problems,
        "warnings": list(data.get("warnings", [])),
    }
    if args.output:
        write_plan(Path(args.output).expanduser(), data)
        payload["data"]["plan_file"] = str(Path(args.output).expanduser())
    if args.json:
        _json(payload)
    else:
        print(f"repoctl upgrade plan: {len(data['operations'])} change(s), {len(data['conflicts'])} conflict(s)")
        for operation in data["operations"]:
            print(f"{operation['action']} {operation['path']}")
        for conflict in data["conflicts"]:
            print(f"conflict {conflict['code']} {conflict['path']}")
        if args.output:
            print(f"plan written: {args.output}")
    return 1 if problems else 0


def cmd_upgrade_apply(args: argparse.Namespace) -> int:
    root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else find_workspace_root()
    data = apply_upgrade(root, plan_file=args.plan_file)
    payload = {
        "ok": True,
        "command": "upgrade.apply",
        "data": data,
        "problems": [],
        "warnings": list(data.get("warnings", [])),
    }
    if args.json:
        _json(payload)
    else:
        print(f"repoctl upgrade apply: {len(data['applied'])} change(s) applied")
        print(f"receipt: {data['receipt_path']}")
    return 0


def cmd_meta_set(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    purpose = args.purpose or _read_optional_file(args.purpose_file)
    if not purpose:
        raise RepoctlError("--purpose or --purpose-file is required")
    caution: list[str] = []
    caution_text = _read_optional_file(args.caution_file)
    if caution_text:
        caution.append(caution_text)
    caution.extend(args.caution or [])
    with repoctl_lock(root):
        data = set_annotation(
            root,
            args.path,
            role=args.role,
            purpose=purpose,
            topics=args.topic,
            declared_effects=args.declared_effect or [],
            caution=caution,
            reviewed_by=args.reviewed_by,
            target=target,
        )
    payload = {"ok": True, "command": "meta set", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Set annotation: {data['path']}")
    return 0


def cmd_meta_remove(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    with repoctl_lock(root):
        data = remove_annotation(root, args.path, target=target)
    payload = {"ok": True, "command": "meta remove", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Removed annotation: {data['path']}")
    return 0


def cmd_meta_move(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    with repoctl_lock(root):
        data = move_annotation(root, args.old_path, args.new_path, target=target)
    payload = {"ok": True, "command": "meta move", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Moved annotation: {data['old_path']} -> {data['new_path']}")
    return 0


def cmd_meta_exclude(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = _repo_target_from_args(root, args)
    with repoctl_lock(root):
        data = exclude_path(root, args.path, reason=args.reason, excluded_by=args.excluded_by, target=target)
    payload = {"ok": True, "command": "meta exclude", "data": data, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Excluded from annotation coverage: {data['path']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = RepoctlArgumentParser(prog="repoctl")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=RepoctlArgumentParser)

    check = sub.add_parser("check")
    check.add_argument("--fix-board", action="store_true")
    check.add_argument("--include-archived-warnings", action="store_true")
    check.add_argument("--full", action="store_true", help="include detailed release field-gate commands")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    field_gate = sub.add_parser("field-gate")
    field_gate_sub = field_gate.add_subparsers(dest="field_gate_command", required=True, parser_class=RepoctlArgumentParser)
    field_gate_run = field_gate_sub.add_parser("run")
    field_gate_run.add_argument("gate", choices=["release-candidate"])
    field_gate_run.add_argument("--repo-id", default="main")
    field_gate_run.add_argument("--output")
    field_gate_run.add_argument("--full", action="store_true", help="include child gate commands and full diagnostic summaries")
    field_gate_run.add_argument("--json", action="store_true")
    field_gate_run.set_defaults(func=cmd_field_gate_run)
    field_gate_compare = field_gate_sub.add_parser("compare")
    field_gate_compare.add_argument("--baseline", required=True)
    field_gate_compare.add_argument("--candidate", required=True)
    field_gate_compare.add_argument("--max-failed-count-increase", type=int)
    field_gate_compare.add_argument("--require-same-gates", action="store_true")
    field_gate_compare.add_argument("--require-no-gate-regressions", action="store_true")
    field_gate_compare.add_argument("--json", action="store_true")
    field_gate_compare.set_defaults(func=cmd_field_gate_compare)
    repo = sub.add_parser("repo")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True, parser_class=RepoctlArgumentParser)
    repo_list = repo_sub.add_parser("list")
    repo_list.add_argument("--json", action="store_true")
    repo_list.set_defaults(func=cmd_repo_list)
    repo_show = repo_sub.add_parser("show")
    repo_show.add_argument("repo_id")
    repo_show.add_argument("--json", action="store_true")
    repo_show.set_defaults(func=cmd_repo_show)
    repo_check = repo_sub.add_parser("check")
    repo_check.add_argument("--json", action="store_true")
    repo_check.set_defaults(func=cmd_repo_check)
    repo_adopt = repo_sub.add_parser("adopt")
    repo_adopt.add_argument("path", nargs="?")
    repo_adopt.add_argument("--id", dest="repo_id", default="")
    repo_adopt.add_argument("--all", action="store_true")
    repo_adopt.add_argument("--json", action="store_true")
    repo_adopt.set_defaults(func=cmd_repo_adopt)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True, parser_class=RepoctlArgumentParser)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("--type", choices=["task", "parent"], default="task")
    task_create.add_argument("--slug")
    task_create.add_argument("--area", default="", help="broad area: repo, backend, frontend, infra, docs, ops, mobile")
    task_create.add_argument("--owner", default="unassigned")
    task_create.add_argument("--parent", default="")
    task_create.add_argument("--repo-ref", default="", help="advisory repos/ branch or worktree hint; never selects a repository")
    task_create.add_argument("--repo-id", default="", help="stable product repository id for repo-scoped work; defaults to main in single-repo workspaces")
    task_create.add_argument("--backlog-id")
    task_create.add_argument("--follow-up-of", default="", help="create a new task linked to a completed task; completed tasks are immutable")
    task_create.add_argument("--start", action="store_true")
    task_create.add_argument("--force-dirty", action="store_true", help="with --start, record an existing dirty repos/ baseline instead of blocking repo-scoped work")
    task_create.add_argument("--print-id", action="store_true", help="print only the created task id in non-JSON mode")
    task_create.add_argument("--json", action="store_true")
    task_create.add_argument("title", nargs="?")
    task_create.set_defaults(func=cmd_task_create)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--json", action="store_true")
    task_list.set_defaults(func=cmd_task_list)
    task_show = task_sub.add_parser("show")
    task_show.add_argument("task_id")
    task_show.add_argument("--summary", action="store_true", help="omit task body and frontmatter from output")
    task_show.add_argument("--section", choices=["Discovery", "Verification", "Handoff", "Closure"], help="return only one canonical task section")
    task_show.add_argument("--json", action="store_true")
    task_show.set_defaults(func=cmd_task_show)
    task_doctor = task_sub.add_parser("doctor")
    task_doctor.add_argument("task_id")
    task_doctor.add_argument("--use-committed-diff", action="store_true", help="preflight the recorded task-start HEAD through current HEAD")
    task_doctor.add_argument("--json", action="store_true")
    task_doctor.set_defaults(func=cmd_task_doctor)
    task_log = task_sub.add_parser("log")
    task_log_sub = task_log.add_subparsers(dest="task_log_command", required=True, parser_class=RepoctlArgumentParser)
    task_log_append = task_log_sub.add_parser("append")
    task_log_append.add_argument("task_id")
    task_log_append.add_argument("message")
    task_log_append.add_argument("--json", action="store_true")
    task_log_append.set_defaults(func=cmd_task_log_append)
    task_discovery = task_sub.add_parser("discovery")
    task_discovery_sub = task_discovery.add_subparsers(dest="task_discovery_command", required=True, parser_class=RepoctlArgumentParser)
    task_discovery_add = task_discovery_sub.add_parser("add")
    task_discovery_add.add_argument("task_id")
    task_discovery_add.add_argument("--query", help="candidate search/query command or phrase")
    task_discovery_add.add_argument("--reviewed", action="append", default=[], help="repos/path inspected during discovery; repeat for multiple files")
    task_discovery_add.add_argument("--chosen", action="append", default=[], help="repos/path selected for task scope; repeat for multiple files")
    task_discovery_add.add_argument("--replace-chosen", action="append", default=[], help="replace the active chosen-file set; repeat for multiple files")
    task_discovery_add.add_argument("--reason", help="required rationale when replacing the active chosen-file set")
    task_discovery_add.add_argument("--note", help="short rationale for the chosen scope")
    task_discovery_add.add_argument("--full", action="store_true", help="include the full cumulative Discovery state")
    task_discovery_add.add_argument("--json", action="store_true")
    task_discovery_add.set_defaults(func=cmd_task_discovery_add)
    task_baseline = task_sub.add_parser("baseline")
    task_baseline_sub = task_baseline.add_subparsers(dest="task_baseline_command", required=True, parser_class=RepoctlArgumentParser)
    task_baseline_resolve = task_baseline_sub.add_parser("resolve")
    task_baseline_resolve.add_argument("task_id")
    task_baseline_resolve.add_argument("--path", action="append", default=[], help="baseline path to resolve; repeat for paths sharing --ownership")
    task_baseline_resolve.add_argument("--ownership", choices=["task", "preexisting"])
    task_baseline_resolve.add_argument("--resolution", action="append", default=[], help="mixed atomic resolution in PATH=task or PATH=preexisting form; repeat as needed")
    task_baseline_resolve.add_argument("--preview", action="store_true", help="validate and show every resolution without writing task state")
    task_baseline_resolve.add_argument("--json", action="store_true")
    task_baseline_resolve.set_defaults(func=cmd_task_baseline_resolve)
    task_start = task_sub.add_parser("start")
    task_start.add_argument("task_id")
    task_start.add_argument("--force-dirty", action="store_true")
    task_start.add_argument("--json", action="store_true")
    task_start.set_defaults(func=cmd_task_start)
    task_finish = task_sub.add_parser("finish")
    task_finish.add_argument("task_id")
    task_finish.add_argument("--verification-file")
    task_finish.add_argument("--use-committed-diff", action="store_true", help="validate recorded task-start HEAD through current HEAD when product changes were committed before finish")
    task_finish.add_argument("--json", action="store_true")
    task_finish.set_defaults(func=cmd_task_finish)
    task_block = task_sub.add_parser("block")
    task_block.add_argument("task_id")
    task_block.add_argument("--verification-file")
    task_block.add_argument("--json", action="store_true")
    task_block.set_defaults(func=cmd_task_block)
    task_cancel = task_sub.add_parser("cancel")
    task_cancel.add_argument("task_id")
    task_cancel.add_argument("--verification-file")
    task_cancel.add_argument("--allow-dirty-cancel", action="store_true", help="archive cancellation even when task-scoped repos/ changes remain, recording them as explicit evidence")
    task_cancel.add_argument("--json", action="store_true")
    task_cancel.set_defaults(func=cmd_task_cancel)

    backlog = sub.add_parser("backlog")
    backlog_sub = backlog.add_subparsers(dest="backlog_command", required=True, parser_class=RepoctlArgumentParser)
    backlog_add = backlog_sub.add_parser("add")
    backlog_add.add_argument("title")
    backlog_add.add_argument("--body-file")
    backlog_add.add_argument("--json", action="store_true")
    backlog_add.set_defaults(func=cmd_backlog_add)
    backlog_list = backlog_sub.add_parser("list")
    backlog_list.add_argument("--json", action="store_true")
    backlog_list.set_defaults(func=cmd_backlog_list)
    backlog_show = backlog_sub.add_parser("show")
    backlog_show.add_argument("backlog_id")
    backlog_show.add_argument("--json", action="store_true")
    backlog_show.set_defaults(func=cmd_backlog_show)
    backlog_remove = backlog_sub.add_parser("remove")
    backlog_remove.add_argument("backlog_id")
    backlog_remove.add_argument("--json", action="store_true")
    backlog_remove.set_defaults(func=cmd_backlog_remove)

    meta = sub.add_parser("meta")
    meta_sub = meta.add_subparsers(dest="meta_command", required=True, parser_class=RepoctlArgumentParser)
    meta_init = meta_sub.add_parser("init")
    meta_init.add_argument("--repo-id")
    meta_init.add_argument("--json", action="store_true")
    meta_init.set_defaults(func=cmd_meta_init)
    meta_check = meta_sub.add_parser("check")
    meta_check.add_argument("--repo-id")
    meta_check.add_argument("--changed", action="store_true")
    meta_check.add_argument("--json", action="store_true")
    meta_check.set_defaults(func=cmd_meta_check)
    meta_status_cmd = meta_sub.add_parser("status")
    meta_status_cmd.add_argument("--repo-id")
    meta_status_cmd.add_argument("--changed", action="store_true")
    meta_status_cmd.add_argument("--verbose", action="store_true")
    meta_status_cmd.add_argument("--include-excluded", action="store_true")
    meta_status_cmd.add_argument("--json", action="store_true")
    meta_status_cmd.set_defaults(func=cmd_meta_status)
    meta_inventory_cmd = meta_sub.add_parser("inventory")
    meta_inventory_cmd.add_argument("--repo-id")
    meta_inventory_cmd.add_argument("--json", action="store_true")
    meta_inventory_cmd.set_defaults(func=cmd_meta_inventory)
    meta_show = meta_sub.add_parser("show")
    meta_show.add_argument("path")
    meta_show.add_argument("--repo-id")
    meta_show.add_argument("--json", action="store_true")
    meta_show.set_defaults(func=cmd_meta_show)
    meta_query_cmd = meta_sub.add_parser("query")
    meta_query_cmd.add_argument("--repo-id")
    meta_query_cmd.add_argument("--role", default="")
    meta_query_cmd.add_argument("--topic", action="append")
    meta_query_cmd.add_argument("--area", default="")
    meta_query_cmd.add_argument("--declared-effect", action="append")
    meta_query_cmd.add_argument("--limit", type=int, default=50)
    meta_query_cmd.add_argument("--json", action="store_true")
    meta_query_cmd.set_defaults(func=cmd_meta_query)
    meta_suggest_cmd = meta_sub.add_parser("suggest")
    meta_suggest_cmd.add_argument("--repo-id")
    meta_suggest_cmd.add_argument("--text", required=True)
    meta_suggest_cmd.add_argument("--limit", type=int, default=20)
    meta_suggest_cmd.add_argument("--json", action="store_true")
    meta_suggest_cmd.set_defaults(func=cmd_meta_suggest)
    meta_set = meta_sub.add_parser("set")
    meta_set.add_argument("path")
    meta_set.add_argument("--repo-id")
    meta_set.add_argument("--role", required=True)
    meta_set.add_argument("--purpose")
    meta_set.add_argument("--purpose-file")
    meta_set.add_argument("--topic", action="append", required=True)
    meta_set.add_argument("--declared-effect", action="append")
    meta_set.add_argument("--caution", action="append")
    meta_set.add_argument("--caution-file")
    meta_set.add_argument("--reviewed-by", default="agent")
    meta_set.add_argument("--json", action="store_true")
    meta_set.set_defaults(func=cmd_meta_set)
    meta_remove = meta_sub.add_parser("remove")
    meta_remove.add_argument("path")
    meta_remove.add_argument("--repo-id")
    meta_remove.add_argument("--json", action="store_true")
    meta_remove.set_defaults(func=cmd_meta_remove)
    meta_move = meta_sub.add_parser("move")
    meta_move.add_argument("old_path")
    meta_move.add_argument("new_path")
    meta_move.add_argument("--repo-id")
    meta_move.add_argument("--json", action="store_true")
    meta_move.set_defaults(func=cmd_meta_move)
    meta_exclude = meta_sub.add_parser("exclude")
    meta_exclude.add_argument("path")
    meta_exclude.add_argument("--repo-id")
    meta_exclude.add_argument("--reason", required=True)
    meta_exclude.add_argument("--excluded-by", default="agent")
    meta_exclude.add_argument("--json", action="store_true")
    meta_exclude.set_defaults(func=cmd_meta_exclude)

    index = sub.add_parser("index")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_code = index_sub.add_parser("code")
    index_code.add_argument("--repo-id")
    index_code.add_argument("--changed", action="store_true")
    index_code.add_argument("--limit", type=int, default=200)
    index_code.add_argument("--json", action="store_true")
    index_code.set_defaults(func=cmd_index_code)

    graph = sub.add_parser("graph")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True, parser_class=RepoctlArgumentParser)
    graph_build = graph_sub.add_parser("build")
    graph_build.add_argument("--repo-id")
    graph_build.add_argument("--rebuild", action="store_true", help="discard reusable provider materialization and rebuild all provider results")
    graph_build.add_argument("--full", action="store_true", help="include full nodes/edges snapshot; default JSON is compact summary")
    graph_build.add_argument("--json", action="store_true")
    graph_build.set_defaults(func=cmd_graph_build)
    graph_query = graph_sub.add_parser("query")
    graph_query.add_argument("--repo-id")
    graph_query.add_argument("--file", default="")
    graph_query.add_argument("--topic", default="")
    graph_query.add_argument("--import", dest="import_ref", default="")
    graph_query.add_argument("--symbol", default="")
    graph_query.add_argument("--callers-of", dest="callers_of", default="")
    graph_query.add_argument("--callees-of", dest="callees_of", default="")
    graph_query.add_argument("--impact-file", dest="impact_file", default="")
    graph_query.add_argument("--impact-symbol", dest="impact_symbol", default="")
    graph_query.add_argument("--task", default="")
    graph_query.add_argument("--artifact", default="")
    graph_query.add_argument("--in-file", dest="in_file", default="")
    graph_query.add_argument("--depth", type=int, default=1)
    graph_query.add_argument("--full", action="store_true", help="include raw graph nodes, edges, and full provider diagnostics")
    graph_query.add_argument("--json", action="store_true")
    graph_query.set_defaults(func=cmd_graph_query)

    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_command", required=True, parser_class=RepoctlArgumentParser)
    context_query = context_sub.add_parser("query")
    context_query.add_argument("query")
    context_query.add_argument("--repo-id")
    context_query.add_argument("--mode", default="")
    context_query.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    context_query.add_argument("--explain", action="store_true")
    context_query.add_argument("--full", action="store_true", help="include full evidence and source diagnostics in JSON output")
    context_query.add_argument("--json", action="store_true")
    context_query.set_defaults(func=cmd_context_query)
    context_benchmark = context_sub.add_parser("benchmark")
    context_benchmark.add_argument("--fixture", default="tests/fixtures/context-benchmark")
    context_benchmark.add_argument("--repo-id")
    context_benchmark.add_argument("--min-recall-at-5", type=float)
    context_benchmark.add_argument("--min-precision-at-5", type=float)
    context_benchmark.add_argument("--min-knowledge-recall-at-5", type=float)
    context_benchmark.add_argument("--min-category-recall-at-5", action="append", default=[])
    context_benchmark.add_argument("--min-category-knowledge-recall-at-5", action="append", default=[])
    context_benchmark.add_argument("--min-category-graph-edge-recall", action="append", default=[])
    context_benchmark.add_argument("--min-category-visible-recall", action="append", default=[])
    context_benchmark.add_argument("--require-source-integrity", action="store_true")
    context_benchmark.add_argument("--require-knowledge-source-current", action="store_true")
    context_benchmark.add_argument("--require-no-forbidden", action="store_true")
    context_benchmark.add_argument("--require-no-cross-repo", action="store_true")
    context_benchmark.add_argument("--require-fixture-corpus", action="store_true")
    context_benchmark.add_argument("--output")
    context_benchmark.add_argument("--json", action="store_true")
    context_benchmark.set_defaults(func=cmd_context_benchmark)
    context_benchmark_materialize = context_sub.add_parser("benchmark-materialize")
    context_benchmark_materialize.add_argument("--fixture", default="tests/fixtures/context-benchmark")
    context_benchmark_materialize.add_argument("--repo-id")
    context_benchmark_materialize.add_argument("--force", action="store_true")
    context_benchmark_materialize.add_argument("--json", action="store_true")
    context_benchmark_materialize.set_defaults(func=cmd_context_benchmark_materialize)
    context_benchmark_compare = context_sub.add_parser("benchmark-compare")
    context_benchmark_compare.add_argument("--baseline", required=True)
    context_benchmark_compare.add_argument("--candidate", required=True)
    context_benchmark_compare.add_argument("--max-recall-at-5-drop", type=float)
    context_benchmark_compare.add_argument("--max-precision-at-5-drop", type=float)
    context_benchmark_compare.add_argument("--max-knowledge-recall-at-5-drop", type=float)
    context_benchmark_compare.add_argument("--max-question-recall-at-5-drop", type=float)
    context_benchmark_compare.add_argument("--require-current-sources", action="store_true")
    context_benchmark_compare.add_argument("--json", action="store_true")
    context_benchmark_compare.set_defaults(func=cmd_context_benchmark_compare)
    context_pack = context_sub.add_parser("pack")
    context_pack.add_argument("--task", required=True)
    context_pack.add_argument("--repo-id", required=True)
    context_pack.add_argument("--budget-tokens", type=int, default=1500)
    context_pack.add_argument("--explain", action="store_true")
    context_pack.add_argument("--output")
    context_pack.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    context_pack.add_argument("--full", action="store_true", help="include raw bundle/candidates in JSON output")
    context_pack.add_argument("--json", action="store_true")
    context_pack.set_defaults(func=cmd_context_pack)
    context_pack_compare = context_sub.add_parser("pack-compare")
    context_pack_compare.add_argument("--baseline", required=True)
    context_pack_compare.add_argument("--candidate", required=True)
    context_pack_compare.add_argument("--max-must-read-drop", type=int)
    context_pack_compare.add_argument("--require-warning-stability", action="store_true")
    context_pack_compare.add_argument("--json", action="store_true")
    context_pack_compare.set_defaults(func=cmd_context_pack_compare)
    context_pack_benchmark = context_sub.add_parser("pack-benchmark")
    context_pack_benchmark.add_argument("--fixture", default="tests/fixtures/context-pack-benchmark")
    context_pack_benchmark.add_argument("--repo-id", required=True)
    context_pack_benchmark.add_argument("--budget-tokens", type=int, default=1500)
    context_pack_benchmark.add_argument("--explain", action="store_true")
    context_pack_benchmark.add_argument("--min-must-read-recall", type=float)
    context_pack_benchmark.add_argument("--output")
    context_pack_benchmark.add_argument("--json", action="store_true")
    context_pack_benchmark.set_defaults(func=cmd_context_pack_benchmark)
    context_pack_benchmark_materialize = context_sub.add_parser("pack-benchmark-materialize")
    context_pack_benchmark_materialize.add_argument("--fixture", default="tests/fixtures/context-pack-benchmark")
    context_pack_benchmark_materialize.add_argument("--force", action="store_true")
    context_pack_benchmark_materialize.add_argument("--json", action="store_true")
    context_pack_benchmark_materialize.set_defaults(func=cmd_context_pack_benchmark_materialize)
    context_pack_benchmark_compare = context_sub.add_parser("pack-benchmark-compare")
    context_pack_benchmark_compare.add_argument("--baseline", required=True)
    context_pack_benchmark_compare.add_argument("--candidate", required=True)
    context_pack_benchmark_compare.add_argument("--max-mean-must-read-recall-drop", type=float)
    context_pack_benchmark_compare.add_argument("--json", action="store_true")
    context_pack_benchmark_compare.set_defaults(func=cmd_context_pack_benchmark_compare)

    knowledge = sub.add_parser("knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True, parser_class=RepoctlArgumentParser)
    knowledge_candidate = knowledge_sub.add_parser("candidate")
    knowledge_candidate_sub = knowledge_candidate.add_subparsers(dest="knowledge_candidate_command", required=True, parser_class=RepoctlArgumentParser)
    knowledge_candidate_build = knowledge_candidate_sub.add_parser("build")
    knowledge_candidate_build.add_argument("--source")
    knowledge_candidate_build.add_argument("--from-receipt")
    knowledge_candidate_build.add_argument("--from-pack")
    knowledge_candidate_build.add_argument("--from-task", dest="from_task")
    knowledge_candidate_build.add_argument("--repo-id", required=True)
    knowledge_candidate_build.add_argument("--kind", choices=sorted(["decision", "failure_mode", "invariant"]), default="decision")
    knowledge_candidate_build_claim = knowledge_candidate_build.add_mutually_exclusive_group()
    knowledge_candidate_build_claim.add_argument("--claim", default="")
    knowledge_candidate_build_claim.add_argument("--claim-file", default="")
    knowledge_candidate_build.add_argument("--json", action="store_true")
    knowledge_candidate_build.set_defaults(func=cmd_knowledge_candidate_build, knowledge_candidate_command="build")
    knowledge_candidate_suggest = knowledge_candidate_sub.add_parser("suggest")
    knowledge_candidate_suggest.add_argument("--from-task", dest="from_task", required=True)
    knowledge_candidate_suggest.add_argument("--repo-id", required=True)
    knowledge_candidate_suggest.add_argument("--kind", choices=sorted(["decision", "failure_mode", "invariant"]), default="decision")
    knowledge_candidate_suggest.add_argument("--dry-run", action="store_true")
    knowledge_candidate_suggest_claim = knowledge_candidate_suggest.add_mutually_exclusive_group()
    knowledge_candidate_suggest_claim.add_argument("--claim", default="")
    knowledge_candidate_suggest_claim.add_argument("--claim-file", default="")
    knowledge_candidate_suggest.add_argument("--full", action="store_true", help="include the full candidate summary")
    knowledge_candidate_suggest.add_argument("--json", action="store_true")
    knowledge_candidate_suggest.set_defaults(func=cmd_knowledge_candidate_build, source="", from_receipt="", from_pack="", knowledge_candidate_command="suggest")
    knowledge_candidate_list = knowledge_candidate_sub.add_parser("list")
    knowledge_candidate_list.add_argument("--repo-id", required=True)
    knowledge_candidate_list.add_argument("--with-checks", action="store_true")
    knowledge_candidate_list.add_argument("--json", action="store_true")
    knowledge_candidate_list.set_defaults(func=cmd_knowledge_candidate_list)
    knowledge_candidate_show = knowledge_candidate_sub.add_parser("show")
    knowledge_candidate_show.add_argument("candidate_id")
    knowledge_candidate_show.add_argument("--repo-id", required=True)
    knowledge_candidate_show.add_argument("--format", choices=["text", "markdown"], default="text")
    knowledge_candidate_show.add_argument("--json", action="store_true")
    knowledge_candidate_show.set_defaults(func=cmd_knowledge_candidate_show)
    knowledge_candidate_check = knowledge_candidate_sub.add_parser("check")
    knowledge_candidate_check.add_argument("candidate_id", nargs="?")
    knowledge_candidate_check.add_argument("--all", action="store_true")
    knowledge_candidate_check.add_argument("--all-states", action="store_true")
    knowledge_candidate_check.add_argument("--repo-id", required=True)
    knowledge_candidate_check.add_argument("--json", action="store_true")
    knowledge_candidate_check.set_defaults(func=cmd_knowledge_candidate_check)
    knowledge_candidate_refresh = knowledge_candidate_sub.add_parser("refresh")
    knowledge_candidate_refresh.add_argument("candidate_id", nargs="?")
    knowledge_candidate_refresh.add_argument("--all-stale", action="store_true")
    knowledge_candidate_refresh.add_argument("--include-records", action="store_true")
    knowledge_candidate_refresh.add_argument("--record-id")
    knowledge_candidate_refresh.add_argument("--repo-id", required=True)
    knowledge_candidate_refresh.add_argument("--json", action="store_true")
    knowledge_candidate_refresh.set_defaults(func=cmd_knowledge_candidate_refresh)
    knowledge_status_parser = knowledge_sub.add_parser("status")
    knowledge_status_parser.add_argument("--repo-id", required=True)
    knowledge_status_parser.add_argument("--json", action="store_true")
    knowledge_status_parser.set_defaults(func=cmd_knowledge_status)
    knowledge_event = knowledge_sub.add_parser("event")
    knowledge_event_sub = knowledge_event.add_subparsers(dest="knowledge_event_command", required=True, parser_class=RepoctlArgumentParser)
    knowledge_event_list = knowledge_event_sub.add_parser("list")
    knowledge_event_list.add_argument("--repo-id", required=True)
    knowledge_event_list.add_argument("--type", default="")
    knowledge_event_list.add_argument("--candidate-id", default="")
    knowledge_event_list.add_argument("--record-id", default="")
    knowledge_event_list.add_argument("--json", action="store_true")
    knowledge_event_list.set_defaults(func=cmd_knowledge_event_list)
    knowledge_event_show = knowledge_event_sub.add_parser("show")
    knowledge_event_show.add_argument("event_id")
    knowledge_event_show.add_argument("--repo-id", required=True)
    knowledge_event_show.add_argument("--json", action="store_true")
    knowledge_event_show.set_defaults(func=cmd_knowledge_event_show)
    knowledge_approve = knowledge_sub.add_parser("approve")
    knowledge_approve.add_argument("candidate_id")
    knowledge_approve.add_argument("--repo-id", required=True)
    knowledge_approve.add_argument("--supersedes", action="append", default=[])
    knowledge_approve.add_argument("--reviewed-by", default="human")
    knowledge_approve.add_argument("--note-file")
    knowledge_approve.add_argument("--full", action="store_true", help="include the full record summary")
    knowledge_approve.add_argument("--json", action="store_true")
    knowledge_approve.set_defaults(func=cmd_knowledge_approve)
    knowledge_show = knowledge_sub.add_parser("show")
    knowledge_show.add_argument("record_id")
    knowledge_show.add_argument("--repo-id", required=True)
    knowledge_show.add_argument("--json", action="store_true")
    knowledge_show.set_defaults(func=cmd_knowledge_show)
    knowledge_reject = knowledge_sub.add_parser("reject")
    knowledge_reject.add_argument("candidate_id")
    knowledge_reject.add_argument("--repo-id", required=True)
    knowledge_reject.add_argument("--reason-file", required=True)
    knowledge_reject.add_argument("--json", action="store_true")
    knowledge_reject.set_defaults(func=cmd_knowledge_reject)
    knowledge_deprecate = knowledge_sub.add_parser("deprecate")
    knowledge_deprecate.add_argument("record_id")
    knowledge_deprecate.add_argument("--repo-id", required=True)
    knowledge_deprecate.add_argument("--reason-file", required=True)
    knowledge_deprecate.add_argument("--json", action="store_true")
    knowledge_deprecate.set_defaults(func=cmd_knowledge_deprecate)
    knowledge_check = knowledge_sub.add_parser("check")
    knowledge_check.add_argument("--repo-id", required=True)
    knowledge_check.add_argument("--include-candidates", action="store_true")
    knowledge_check.add_argument("--json", action="store_true")
    knowledge_check.set_defaults(func=cmd_knowledge_check)
    knowledge_query = knowledge_sub.add_parser("query")
    knowledge_query.add_argument("query")
    knowledge_query.add_argument("--repo-id", required=True)
    knowledge_query.add_argument("--include-stale", action="store_true")
    knowledge_query.add_argument("--include-superseded", action="store_true")
    knowledge_query.add_argument("--include-deprecated", action="store_true")
    knowledge_query.add_argument("--include-history", action="store_true")
    knowledge_query.add_argument("--explain", action="store_true")
    knowledge_query.add_argument("--limit", type=int, default=10)
    knowledge_query.add_argument("--full", action="store_true", help="include full reviewed record summaries")
    knowledge_query.add_argument("--json", action="store_true")
    knowledge_query.set_defaults(func=cmd_knowledge_query)
    knowledge_render = knowledge_sub.add_parser("render")
    knowledge_render.add_argument("--repo-id", required=True)
    knowledge_render.add_argument("--output")
    knowledge_render.add_argument("--check", action="store_true")
    knowledge_render.add_argument("--full", action="store_true", help="include per-page digests and source bundles in JSON output")
    knowledge_render.add_argument("--json", action="store_true")
    knowledge_render.set_defaults(func=cmd_knowledge_render)

    upgrade = sub.add_parser("upgrade")
    upgrade_sub = upgrade.add_subparsers(dest="upgrade_command", required=True, parser_class=RepoctlArgumentParser)
    upgrade_status_parser = upgrade_sub.add_parser("status")
    upgrade_status_parser.add_argument("--json", action="store_true")
    upgrade_status_parser.set_defaults(func=cmd_upgrade_status)
    upgrade_plan = upgrade_sub.add_parser("plan")
    upgrade_plan.add_argument("--workspace-root", help="workspace to upgrade; defaults to the current workspace")
    upgrade_plan.add_argument("--from", dest="source", required=True, help="repoctl release checkout or extracted artifact directory")
    upgrade_plan.add_argument("--output", help="optional path for a plan artifact; omitted keeps the command read-only")
    upgrade_plan.add_argument("--json", action="store_true")
    upgrade_plan.set_defaults(func=cmd_upgrade_plan)
    upgrade_apply = upgrade_sub.add_parser("apply")
    upgrade_apply.add_argument("--workspace-root", help="workspace to upgrade; defaults to the current workspace")
    upgrade_apply.add_argument("--plan-file", required=True)
    upgrade_apply.add_argument("--json", action="store_true")
    upgrade_apply.set_defaults(func=cmd_upgrade_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv in (["--version"], ["version"], ["version", "--json"]):
        data = _version_data(_workspace_root_or_cwd())
        if "--json" in raw_argv:
            _json({"ok": True, "command": "version", "data": data, "problems": [], "warnings": []})
        else:
            print(data["version"])
        return 0
    try:
        args = parser.parse_args(raw_argv)
    except RepoctlArgparseError as error:
        if "--json" in raw_argv:
            _json({"ok": False, "command": raw_argv[0] if raw_argv else "repoctl", "data": {}, "problems": [{"severity": "error", "code": "argparse_error", "message": str(error)}], "warnings": []})
        else:
            print(f"repoctl: {error}", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except RepoctlError as error:
        if _wants_json_output(args):
            problem = {"severity": "error", "code": error.code, "message": str(error)}
            if error.path:
                problem["path"] = error.path
            _json({"ok": False, "command": _command_name(args), "data": _error_data(args), "problems": [problem], "warnings": []})
        else:
            print(f"repoctl: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        message = str(error)
        if _wants_json_output(args):
            _json({"ok": False, "command": _command_name(args), "data": {}, "problems": [{"severity": "error", "code": "io_error", "message": message}], "warnings": []})
        else:
            print(f"repoctl: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
