from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .board import append_backlog_item, backlog_warnings, parse_board, read_backlog_items, remove_backlog_item, render_board, resolve_backlog_item, check_board
from .code_index import build_code_index
from .context import build_context_bundle
from .context_benchmark import compare_context_benchmarks, materialize_context_benchmark_corpus, run_context_benchmark
from .context_task_pack import build_task_context_pack, compare_task_context_pack_benchmarks, compare_task_context_packs, materialize_task_context_pack_benchmark_tasks, run_task_context_pack_benchmark
from .graph import build_graph, query_graph
from .graph_model import digest_data
from .io import RepoctlError, atomic_write, find_workspace_root, repoctl_lock
from .knowledge_candidates import approve_knowledge_candidate, build_knowledge_candidate, build_knowledge_candidate_from_pack, build_knowledge_candidate_from_receipt, check_all_knowledge_candidates, check_knowledge_candidate, check_knowledge_records, deprecate_knowledge_record, knowledge_status, list_knowledge_candidates, list_knowledge_events, query_knowledge_records, refresh_knowledge_candidate, refresh_stale_knowledge_candidates, reject_knowledge_candidate, show_knowledge_candidate, show_knowledge_event, show_knowledge_record
from .knowledge_render import render_knowledge
from .meta import check_meta, exclude_path, init_store, meta_inventory, meta_query, meta_status, meta_suggest, move_annotation, remove_annotation, set_annotation, show_annotation
from .markdown import find_section
from .repositories import RepoTarget, adopt_repositories, default_repo_target, repo_check_problems, repo_layout, require_repo_target
from .tasks import Problem, REPO_REQUIRED_AREAS, append_task_log, block_task, cancel_task, create_task_file, finish_task, load_tasks, live_tasks, repo_changes_since_task_start, resolve_task, start_task, update_task_discovery, validate_tasks, validate_verification_file
from .upgrade import apply_upgrade, plan_upgrade, write_plan


class RepoctlArgparseError(RuntimeError):
    pass


class RepoctlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RepoctlArgparseError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status:
            raise RepoctlArgparseError((message or "argument parsing failed").strip())
        super().exit(status, message)


def _json(data: Any) -> None:
    _complete_json_envelope(data)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _complete_json_envelope(data: Any) -> None:
    if isinstance(data, dict) and "ok" in data:
        data.setdefault("data", {})
        data.setdefault("warnings", [])
        data.setdefault("problems", [])
        data.setdefault("next_actions", _next_actions_for_problems([*data.get("problems", []), *data.get("warnings", [])], data=data.get("data", data)))


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


def _next_actions_for_problems(problems: list[Any], *, data: dict[str, Any] | None = None) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    task_id = str((data or {}).get("task_id") or "T-...")

    def add(label: str, *, command: str = "", path: str = "") -> None:
        action = {"label": label}
        if command:
            action["command"] = command
        if path:
            action["path"] = path
        key = (action.get("label", ""), action.get("command", ""), action.get("path", ""))
        if key not in seen:
            seen.add(key)
            actions.append(action)

    for problem in problems:
        code = _problem_code(problem)
        path = _problem_path(problem)
        if code == "missing_verification_file":
            add("Create verification evidence", command=f"cat > /tmp/{task_id}-verification.md")
            add("Retry finish", command=f"./scripts/repoctl task finish {task_id} --verification-file /tmp/{task_id}-verification.md --json")
            add("Reuse completed task Verification", command=f"./scripts/repoctl task finish {task_id} --use-task-verification --json")
        elif code == "verification_file_inside_repo":
            add("Move verification evidence outside repos/", command=f"cp {path or 'repos/...'} /tmp/{task_id}-verification.md")
        elif code in {"missing_discovery_evidence", "placeholder_discovery"}:
            add("Record task discovery evidence", command=f"./scripts/repoctl task discovery add {task_id} --query '<query>' --reviewed repos/<path> --chosen repos/<path> --json")
            add("Open Discovery section", path=path or f"docs/tasks/{task_id}.md")
        elif code in {"repo_git_unavailable", "repository_git_unavailable"}:
            add("Initialize repos/ as an independent git repository", command="git -C repos init")
        elif code == "repo_head_changed_since_start":
            add("Restart task baseline after reviewing repo HEAD change", command=f"./scripts/repoctl task start {task_id} --force-dirty --json")
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
        elif code in {"invalid_frontmatter", "missing_frontmatter", "invalid_status"}:
            add("Open and fix task frontmatter", path=path or f"docs/tasks/{task_id}.md")
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
            command="./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark-multirepo --require-fixture-corpus --require-no-cross-repo --require-no-forbidden --min-category-packed-recall multi-repo-isolation=1.0 --json",
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


def _release_candidate_gate_result(
    *,
    name: str,
    command: str,
    mutates_workspace: bool,
    data: dict[str, Any],
    problems: list[Problem],
    warnings: list[dict[str, str]] | None = None,
    summary: dict[str, Any] | None = None,
    cleanup: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "command": command,
        "ok": not _has_errors(problems),
        "mutates_workspace": mutates_workspace,
        "summary": summary or {},
        "cleanup": cleanup or [],
        "data_digest": digest_data(data) if data else "",
        "problems": _problem_dicts(problems),
        "warnings": warnings or [],
    }


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
            summary={"board_stale": bool(check_payload.get("board", {}).get("stale")) if isinstance(check_payload.get("board"), dict) else False},
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
                "candidate_total_count": int(candidate_data.get("candidate_total_count") or 0) if isinstance(candidate_data, dict) else 0,
                "candidate_checked_count": len(candidate_data.get("results", [])) if isinstance(candidate_data.get("results"), list) else 0,
                "candidate_error_count": len([problem for problem in candidate_problems if problem.severity == "error"]),
                "candidate_warning_count": len([problem for problem in candidate_problems if problem.severity == "warning"]),
            },
        )
    )

    context_fixture = root / "tests/fixtures/context-benchmark"
    if _repo_target_available(root, repo_id) and _fixture_has_repository(context_fixture, repo_id):
        context_materialize, context_materialize_problems = materialize_context_benchmark_corpus(root, fixture=context_fixture, repo_id=repo_id, force=False)
        gates.append(
            _release_candidate_gate_result(
                name="context_benchmark_materialize",
                command=f"./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark --repo-id {repo_id} --json",
                mutates_workspace=True,
                data=context_materialize,
                problems=context_materialize_problems,
                warnings=[{"code": "context_benchmark_materialize_mutates_workspace", "message": "benchmark materialize writes fixture corpus files into product repositories for controlled retrieval tests"}],
                summary=context_materialize.get("totals", {}) if context_materialize else {},
                cleanup=_context_materialize_cleanup_entries(root, context_materialize),
            )
        )
        if not _has_errors(context_materialize_problems):
            context_benchmark, context_benchmark_problems = run_context_benchmark(
                root,
                fixture=context_fixture,
                repo_id=repo_id,
                min_recall_at_5=0.85,
                require_source_integrity=True,
                require_fixture_corpus=True,
                require_no_forbidden=True,
            )
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

    pack_fixture = root / "tests/fixtures/context-pack-benchmark"
    if _repo_target_available(root, repo_id) and (pack_fixture / "cases.json").exists():
        if (pack_fixture / "tasks.json").exists():
            pack_materialize, pack_materialize_problems = materialize_task_context_pack_benchmark_tasks(root, fixture=pack_fixture, force=False)
            gates.append(
                _release_candidate_gate_result(
                    name="context_pack_benchmark_materialize",
                    command="./scripts/repoctl context pack-benchmark-materialize --fixture tests/fixtures/context-pack-benchmark --json",
                    mutates_workspace=True,
                    data=pack_materialize,
                    problems=pack_materialize_problems,
                    warnings=[{"code": "context_pack_benchmark_materialize_mutates_workspace", "message": "context pack benchmark materialize writes archived fixture tasks for controlled startup-pack tests"}],
                    summary=pack_materialize.get("totals", {}) if pack_materialize else {},
                    cleanup=_pack_materialize_cleanup_entries(root, pack_materialize),
                )
            )
        else:
            pack_materialize_problems = []
        if not _has_errors(pack_materialize_problems):
            target = require_repo_target(root, repo_id=repo_id)
            pack_benchmark, pack_benchmark_problems = run_task_context_pack_benchmark(root, target=target, fixture=pack_fixture, min_must_read_recall=1.0)
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

    multi_fixture = root / "tests/fixtures/context-benchmark-multirepo"
    if _has_configured_repositories(root, {"web", "api"}) and (multi_fixture / "corpus.json").exists():
        multi_materialize, multi_materialize_problems = materialize_context_benchmark_corpus(root, fixture=multi_fixture, repo_id="", force=False)
        gates.append(
            _release_candidate_gate_result(
                name="context_benchmark_multirepo_materialize",
                command="./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark-multirepo --json",
                mutates_workspace=True,
                data=multi_materialize,
                problems=multi_materialize_problems,
                warnings=[{"code": "context_benchmark_materialize_mutates_workspace", "message": "benchmark materialize writes fixture corpus files into product repositories for controlled retrieval tests"}],
                summary=multi_materialize.get("totals", {}) if multi_materialize else {},
                cleanup=_context_materialize_cleanup_entries(root, multi_materialize),
            )
        )
        if not _has_errors(multi_materialize_problems):
            multi_benchmark, multi_benchmark_problems = run_context_benchmark(
                root,
                fixture=multi_fixture,
                min_category_packed_recall={"multi-repo-isolation": 1.0},
                require_fixture_corpus=True,
                require_no_cross_repo=True,
                require_no_forbidden=True,
            )
            gates.append(
                _release_candidate_gate_result(
                    name="context_benchmark_multirepo_isolation",
                    command="./scripts/repoctl context benchmark --fixture tests/fixtures/context-benchmark-multirepo --require-fixture-corpus --require-no-cross-repo --require-no-forbidden --min-category-packed-recall multi-repo-isolation=1.0 --json",
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
    baseline = _read_field_gate_artifact(baseline_path, problems, label="baseline")
    candidate = _read_field_gate_artifact(candidate_path, problems, label="candidate")
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


def _cleanup_field_gate_run(root: Path, *, artifact_path: Path) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    artifact = _read_field_gate_artifact(artifact_path, problems, label="cleanup", allow_failed=True)
    if not artifact:
        return {}, problems
    cleanup_entries = _field_gate_cleanup_entries(artifact)
    removed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for entry in cleanup_entries:
        kind = str(entry.get("kind") or "")
        rel = str(entry.get("path") or "")
        expected_digest = str(entry.get("content_sha256") or "")
        stop_rel = str(entry.get("stop_at") or "")
        if kind != "created_file" or not rel or not expected_digest.startswith("sha256:") or not stop_rel:
            problems.append(Problem("error", "field_gate_cleanup_entry_invalid", "field gate cleanup entry is invalid", rel or artifact_path.as_posix()))
            continue
        path = root / rel
        stop_at = root / stop_rel
        try:
            path.resolve().relative_to(root.resolve())
            stop_at.resolve().relative_to(root.resolve())
        except ValueError:
            problems.append(Problem("error", "field_gate_cleanup_path_outside_workspace", "field gate cleanup path must stay inside workspace", rel))
            continue
        if not path.exists():
            skipped.append({"path": rel, "reason": "missing"})
            continue
        if not path.is_file():
            problems.append(Problem("error", "field_gate_cleanup_not_file", "field gate cleanup path is not a file", rel))
            continue
        actual_digest = _file_digest(path)
        if actual_digest != expected_digest:
            problems.append(Problem("error", "field_gate_cleanup_digest_mismatch", "field gate cleanup file digest no longer matches artifact", rel))
            continue
        path.unlink()
        removed.append({"path": rel, "content_sha256": expected_digest})
        _remove_empty_parents(path.parent, stop_at=stop_at, root=root)
    data = {
        "schema": "repoctl.field_gate.cleanup",
        "schema_version": 1,
        "artifact": _field_gate_identity(artifact_path, artifact),
        "cleanup_entry_count": len(cleanup_entries),
        "removed_count": len(removed),
        "skipped_count": len(skipped),
        "removed": removed,
        "skipped": skipped,
    }
    data["cleanup_digest"] = digest_data(data)
    return data, problems


def _field_gate_cleanup_entries(data: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    gates = data.get("gates") if isinstance(data.get("gates"), list) else []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        cleanup = gate.get("cleanup") if isinstance(gate.get("cleanup"), list) else []
        for entry in cleanup:
            if not isinstance(entry, dict):
                continue
            key = (str(entry.get("kind") or ""), str(entry.get("path") or ""), str(entry.get("content_sha256") or ""), str(entry.get("stop_at") or ""))
            if key in seen:
                continue
            seen.add(key)
            entries.append({str(k): str(v) for k, v in entry.items() if isinstance(k, str)})
    return entries


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


def _discovery_guidance_actions(task_id: str, *, repo_path: str = "repos") -> list[dict[str, str]]:
    candidate = f"{repo_path.rstrip('/')}/<path>"
    return [
        {
            "label": "Record structured Discovery evidence",
            "command": f"./scripts/repoctl task discovery add {task_id} --query '<query>' --reviewed {candidate} --chosen {candidate} --json",
        },
        {
            "label": "Check finish readiness",
            "command": f"./scripts/repoctl task doctor {task_id} --json",
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


def _check_payload(root: Path, *, include_archived_warnings: bool = False) -> tuple[dict[str, Any], list[Problem], list[str]]:
    tasks = load_tasks(root)
    board_path = root / "docs/BOARD.md"
    board_text = board_path.read_text(encoding="utf-8")
    board_paths = parse_board(board_text)
    problems = validate_tasks(tasks, include_archived_warnings=include_archived_warnings) + check_board(root, board_paths, tasks, board_text)
    live_paths = [task.rel_path for task in live_tasks(tasks)]
    payload = {
        "ok": not _has_errors(problems),
        "data": {
            "field_gates": {
                "release_candidate": _release_candidate_field_gates(root),
            },
        },
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _warnings(problems),
        "board": {
            "stale": set(board_paths) != set(live_paths),
            "missing": sorted(set(live_paths) - set(board_paths)),
            "extra": sorted(set(board_paths) - set(live_paths)),
        },
    }
    return payload, problems, live_paths


def cmd_check(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    payload, problems, live_paths = _check_payload(root, include_archived_warnings=args.include_archived_warnings)
    if args.fix_board:
        with repoctl_lock(root):
            _locked_payload, _locked_problems, live_paths = _check_payload(root, include_archived_warnings=args.include_archived_warnings)
            board_path = root / "docs/BOARD.md"
            board_text = board_path.read_text(encoding="utf-8")
            fixed = render_board(board_text, live_paths)
            if fixed != board_text:
                atomic_write(board_path, fixed)
        payload, problems, _ = _check_payload(root, include_archived_warnings=args.include_archived_warnings)
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
        if payload["board"]["stale"]:
            print("BOARD is stale. Run: repoctl check --fix-board")
    return 1 if _has_errors(problems) else 0


def cmd_field_gate_run(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    if args.gate != "release-candidate":
        raise RepoctlError(f"unsupported field gate: {args.gate}")
    data = _run_release_candidate_field_gates(root, repo_id=args.repo_id)
    problems = [
        Problem("error", "field_gate_failed", f"field gate failed: {gate.get('name', '')}")
        for gate in data.get("gates", [])
        if not gate.get("ok")
    ]
    payload = {
        "ok": not problems,
        "command": "field-gate run",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "field_gate_runner_mutates_workspace",
                "message": "release-candidate field gate materializes controlled benchmark fixtures before running read-only gates",
            }
        ],
    }
    if args.output:
        output, output_problem = _workspace_output_path(root, args.output, code="field_gate_output_outside_workspace")
        if output_problem is not None:
            problems.append(output_problem)
            payload["ok"] = False
            payload["problems"] = [problem.to_dict() for problem in problems]
        else:
            data["artifact"] = {
                "path": output.relative_to(root).as_posix(),
                "run_digest": data.get("run_digest", ""),
            }
            _complete_json_envelope(payload)
            atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
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


def cmd_field_gate_cleanup(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    data, problems = _cleanup_field_gate_run(root, artifact_path=Path(args.artifact))
    payload = {
        "ok": not _has_errors(problems),
        "command": "field-gate cleanup",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"field gate cleanup removed={data.get('removed_count', 0) if data else 0} skipped={data.get('skipped_count', 0) if data else 0}")
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
        "tasks": [task.to_list_dict() for task in sorted(live_tasks(tasks), key=lambda task: task.rel_path)],
        "board": {
            "stale": set(board_paths) != set(live_paths),
            "missing": sorted(set(live_paths) - set(board_paths)),
            "extra": sorted(set(board_paths) - set(live_paths)),
        },
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _warnings(problems),
    }
    if args.json:
        _json(payload)
    else:
        for task in payload["tasks"]:
            print(f"{task['id']} {task['status']} {task['path']}")
        if payload["board"]["stale"]:
            print("BOARD is stale. Run: repoctl check --fix-board")
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    task = resolve_task(root, args.task_id)
    delta = repo_changes_since_task_start(root, args.task_id) if task.status in {"todo", "doing", "blocked"} else None
    repo_changes = _repo_change_summary(delta) if delta else None
    payload = {"ok": True, "command": "task.show", "data": {"task": task.to_list_dict(), "path": task.rel_path, "frontmatter": task.frontmatter, "body": task.body, "repo_changes": repo_changes}, "task": task.to_list_dict(), "path": task.rel_path, "frontmatter": task.frontmatter, "body": task.body, "repo_changes": repo_changes, "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(task.path.read_text(encoding="utf-8"))
    return 0


def cmd_task_log_append(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        result = append_task_log(root, args.task_id, args.message)
        atomic_write(result["task"].path, result["text"])
    payload = {"ok": True, "command": "task log append", "task_id": args.task_id, "timestamp": result["timestamp"], "problems": [], "warnings": []}
    if args.json:
        _json(payload)
    else:
        print(f"Logged: {args.task_id} {result['timestamp']}")
    return 0


def cmd_task_discovery_add(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    with repoctl_lock(root):
        result = update_task_discovery(root, args.task_id, query=args.query or "", reviewed=args.reviewed or [], chosen=args.chosen or [], note=args.note or "")
        atomic_write(result["task"].path, result["text"])
    payload = {
        "ok": True,
        "command": "task.discovery.add",
        "data": {
            "task_id": args.task_id,
            "path": result["task"].rel_path,
            "discovery": result["discovery"],
        },
        "problems": [],
        "warnings": [],
        "next_actions": [
            {
                "label": "Check finish readiness",
                "command": f"./scripts/repoctl task doctor {args.task_id} --json",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Updated Discovery: {args.task_id}")
    return 0


def _task_doctor_payload(root: Path, task_id: str) -> dict[str, Any]:
    task = resolve_task(root, task_id)
    all_tasks = load_tasks(root)
    task_problems = [problem for problem in validate_tasks(all_tasks, include_archived_warnings=True) if problem.path == task.rel_path]
    target = _repo_target_for_task_command(root, task)
    delta = repo_changes_since_task_start(root, task_id)
    changed_files, meta_status_problems, meta = meta_status(root, changed=True, changes=delta["changes"], target=target)
    meta_problems = check_meta(root, changed=True, changes=delta["changes"], target=target) if changed_files and not _has_errors(meta_status_problems) else []
    verification_path = Path(f"/tmp/{task_id}-verification.md")
    doctor_problems: list[Problem] = []
    if not verification_path.is_file():
        doctor_problems.append(Problem("warning", "missing_verification_file", f"expected verification file is not present: {verification_path}"))
    combined = [*task_problems, *meta_status_problems, *meta_problems, *doctor_problems]
    blockers = [problem.code for problem in combined if problem.severity == "error"]
    advisory = [problem.code for problem in combined if problem.severity == "warning"]
    finish_ready = task.status in {"doing", "todo", "blocked"} and not blockers and not advisory
    data = {
        "task_id": task.id,
        "status": task.status,
        "path": task.rel_path,
        "finish_ready": finish_ready,
        "blocked_by": blockers,
        "advisory": advisory,
        "repo_changes": {
            **_repo_change_summary(delta),
        },
        "repository": meta.get("repository", {}) if isinstance(meta, dict) else {},
        "verification_file": verification_path.as_posix(),
    }
    payload = {
        "ok": not blockers,
        "command": "task.doctor",
        "data": data,
        "problems": [problem.to_dict() for problem in combined if problem.severity == "error"],
        "warnings": [problem.to_dict() for problem in combined if problem.severity == "warning"],
    }
    return payload


def _repo_change_summary(delta: dict[str, Any]) -> dict[str, Any]:
    task_new_files = [entry[1] for entry in delta.get("changes", [])]
    return {
        "task_new": len(task_new_files),
        "task_new_files": task_new_files[:20],
        "task_new_files_truncated": len(task_new_files) > 20,
        "preexisting_dirty": delta.get("preexisting_count", 0),
        "baseline_available": bool(delta.get("baseline_available")),
        "baseline_conflicts": delta.get("baseline_conflicts", []),
        "repo_git_available": bool(delta.get("repo_git") and delta["repo_git"].available),
        "repo_git_reason": str(delta.get("repo_git").reason) if delta.get("repo_git") and not delta["repo_git"].available else "",
    }


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
    payload = _task_doctor_payload(root, args.task_id)
    if args.json:
        _json(payload)
    else:
        data = payload["data"]
        print(f"repoctl task doctor: {data['task_id']} status={data['status']} finish_ready={data['finish_ready']}")
        for problem in payload["problems"] + payload["warnings"]:
            print(f"- {problem['code']}: {problem['message']}")
    return 1 if _has_errors([Problem(problem["severity"], problem["code"], problem["message"], problem.get("path")) for problem in payload["problems"]]) else 0


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
        try:
            target = _repo_target_for_task_command(root, task)
            if target is not None:
                repo_path = target.display_path
        except RepoctlError:
            pass
        next_actions = _discovery_guidance_actions(task.id, repo_path=repo_path)
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
        "task_id": task.id,
        "path": task.rel_path,
        "status": status,
        "backlog_id": args.backlog_id or "",
        "backlog_removed": bool(args.backlog_id),
        "started": bool(start_result),
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
    with repoctl_lock(root):
        result = start_task(root, args.task_id, force_dirty=args.force_dirty)
        atomic_write(result["task"].path, result["text"])
    delta = repo_changes_since_task_start(root, args.task_id)
    data = {"task_id": args.task_id, "status": "doing", "dirty": result["dirty"], "repo_changes": _repo_change_summary(delta)}
    next_actions: list[dict[str, str]] = []
    if _repo_scoped_frontmatter(result["task"]):
        repo_path = "repos"
        try:
            target = _repo_target_for_task_command(root, result["task"])
            if target is not None:
                repo_path = target.display_path
        except RepoctlError:
            pass
        next_actions = _discovery_guidance_actions(args.task_id, repo_path=repo_path)
    payload = {"ok": True, "command": "task.start", "data": data, **data, "problems": [], "warnings": [problem.to_dict() for problem in result.get("warnings", [])], "next_actions": next_actions}
    if args.json:
        _json(payload)
    else:
        print(f"Started: {args.task_id}")
        if next_actions:
            print(f"Next: {next_actions[0]['command']}")
    return 0


def _task_verification_file(root: Path, task_id: str, *, suffix: str) -> Path:
    task = resolve_task(root, task_id)
    text = task.path.read_text(encoding="utf-8")
    section = find_section(text, "Verification")
    body = text[section.body_start : section.end].strip()
    if not body or body in {"- pending", "- 대기 중."}:
        raise RepoctlError("task Verification section is empty; use --verification-file or fill ## Verification before --use-task-verification", code="missing_verification_file", path=task.rel_path)
    path = Path("/tmp") / f"{task_id}-{suffix}.md"
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _verification_file_arg(root: Path, task_id: str, *, verification_file: str | None, use_task_verification: bool, suffix: str, command: str) -> Path:
    if verification_file and use_task_verification:
        raise RepoctlError(f"{command} accepts either --verification-file or --use-task-verification, not both")
    if use_task_verification:
        return _task_verification_file(root, task_id, suffix=suffix)
    if not verification_file:
        raise RepoctlError(
            f"task {command} requires external verification evidence outside repos/. "
            f"Create /tmp/{task_id}-{suffix}.md and retry with --verification-file, "
            "or use --use-task-verification only when ## Verification already contains final manager-run evidence.",
            code="missing_verification_file",
        )
    return Path(verification_file)


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


def _finish_meta_gate(root: Path, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    task = resolve_task(root, task_id)
    target = _repo_target_for_task_command(root, task)
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
    delta = repo_changes_since_task_start(root, task_id)
    task_changes = delta["changes"]
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
    return meta_gate, delta


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
    verification_file = _verification_file_arg(root, args.task_id, verification_file=args.verification_file, use_task_verification=args.use_task_verification, suffix="verification", command="finish")
    validate_verification_file(root, verification_file)
    with repoctl_lock(root):
        meta_gate, delta = _finish_meta_gate(root, args.task_id)
        result = finish_task(root, args.task_id, verification_file=verification_file, meta_gate=meta_gate, repo_delta=delta)
        _write_task_result(root, result)
    data = {
        "task_id": args.task_id,
        "status": "done",
        "old_path": result["old_path"],
        "new_path": result["new_path"],
        "archived": result["archived"],
        "truncated": result["truncated"],
        "meta_gate": meta_gate,
        "completion_receipt": result["receipt_path"].relative_to(root).as_posix(),
    }
    payload = {
        "ok": True,
        "command": "task.finish",
        "data": data,
        **data,
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Finished: {args.task_id}")
        if result["archived"]:
            print(f"Archived: {result['new_path']}")
    return 0


def cmd_task_cancel(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    verification_file = _verification_file_arg(root, args.task_id, verification_file=args.verification_file, use_task_verification=args.use_task_verification, suffix="cancel", command="cancel")
    validate_verification_file(root, verification_file)
    with repoctl_lock(root):
        cancel_gate = _cancel_dirty_gate(root, args.task_id, allow_dirty_cancel=args.allow_dirty_cancel)
        result = cancel_task(root, args.task_id, verification_file=verification_file, meta_gate=cancel_gate)
        _write_task_result(root, result)
    data = {
        "task_id": args.task_id,
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
        **data,
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Canceled: {args.task_id}")
        if result["archived"]:
            print(f"Archived: {result['new_path']}")
    return 0


def cmd_task_block(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    verification_file = _verification_file_arg(root, args.task_id, verification_file=args.verification_file, use_task_verification=args.use_task_verification, suffix="blocker", command="block")
    validate_verification_file(root, verification_file)
    with repoctl_lock(root):
        result = block_task(root, args.task_id, verification_file=verification_file)
        _write_task_result(root, result)
    payload = {
        "ok": True,
        "task_id": args.task_id,
        "status": "blocked",
        "path": result["new_path"],
        "truncated": result["truncated"],
        "problems": [],
        "warnings": [],
    }
    if args.json:
        _json(payload)
    else:
        print(f"Blocked: {args.task_id}")
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
    text = args.text or args.text_arg or ""
    candidates, problems, meta = meta_suggest(root, text=text, limit=args.limit, target=target)
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
    snapshot, problems, meta = build_graph(root, target=target)
    payload = {
        "ok": snapshot is not None and not _has_errors(problems),
        "command": "graph build",
        "data": {"snapshot": snapshot.to_dict() if snapshot is not None else None, **meta},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "graph_not_authoritative",
                "message": "graph build is a read-only derived snapshot; source authorities remain repo registry, code index, and .repometa",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        if snapshot is not None:
            print(f"graph snapshot {snapshot.snapshot_digest} repository={target.id} nodes={len(snapshot.nodes)} edges={len(snapshot.edges)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_graph_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    snapshot, build_problems, meta = build_graph(root, target=target)
    if snapshot is None or _has_errors(build_problems):
        payload = {
            "ok": False,
            "command": "graph query",
            "data": {"result": None, **meta},
            "problems": [problem.to_dict() for problem in build_problems],
            "warnings": [],
        }
        if args.json:
            _json(payload)
        else:
            for problem in build_problems:
                print(problem.message)
        return 1 if _has_errors(build_problems) else 0

    result, query_problems = query_graph(snapshot, file=args.file or "", topic=args.topic or "", import_ref=args.import_ref or "")
    payload = {
        "ok": result is not None and not _has_errors(query_problems),
        "command": "graph query",
        "data": {"result": result, "repository": target.to_dict(), "snapshot_digest": snapshot.snapshot_digest},
        "problems": [problem.to_dict() for problem in query_problems],
        "warnings": [
            {
                "code": "graph_not_authoritative",
                "message": "graph query uses a read-only derived snapshot; inspect source files before changing task scope",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        if result is not None:
            print(f"graph query {result['query']} nodes={len(result['nodes'])} edges={len(result['edges'])}")
        for problem in query_problems:
            print(problem.message)
    return 1 if _has_errors(query_problems) else 0


def cmd_context_query(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    target = require_repo_target(root, repo_id=args.repo_id)
    bundle, problems, meta = build_context_bundle(root, target=target, query=args.query, budget_tokens=args.budget_tokens, explain=args.explain)
    payload = {
        "ok": bundle is not None and not _has_errors(problems),
        "command": "context query",
        "data": {"bundle": bundle.to_dict() if bundle is not None else None, **meta},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
            {
                "code": "context_not_authoritative",
                "message": "context query returns a read-only evidence bundle; source authorities remain repo registry, source documents, Graph, .repometa, and task receipts",
            }
        ],
    }
    if args.json:
        _json(payload)
    else:
        if bundle is not None:
            print(f"context bundle {bundle.bundle_digest} repository={target.id} packed={len(bundle.packed_context)} candidates={len(bundle.candidates)}")
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
    packed_category_gates, packed_category_gate_problems = _parse_category_recall_gates(args.min_category_packed_recall or [])
    data, problems = run_context_benchmark(
        root,
        fixture=fixture,
        repo_id=args.repo_id or "",
        budget_tokens=args.budget_tokens,
        min_recall_at_5=args.min_recall_at_5,
        min_precision_at_5=args.min_precision_at_5,
        min_knowledge_recall_at_5=args.min_knowledge_recall_at_5,
        min_category_recall_at_5=category_gates,
        min_category_knowledge_recall_at_5=knowledge_category_gates,
        min_category_graph_edge_recall=edge_category_gates,
        min_category_packed_recall=packed_category_gates,
        require_source_integrity=args.require_source_integrity,
        require_knowledge_source_current=args.require_knowledge_source_current,
        require_no_forbidden=args.require_no_forbidden,
        require_no_cross_repo=args.require_no_cross_repo,
        require_fixture_corpus=args.require_fixture_corpus,
    )
    problems = [*category_gate_problems, *knowledge_category_gate_problems, *edge_category_gate_problems, *packed_category_gate_problems, *problems]
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
    payload = {
        "ok": not _has_errors(problems),
        "command": "context pack",
        "data": {**data, **meta},
        "problems": [problem.to_dict() for problem in problems],
        "warnings": data.get("warnings", []),
    }
    if args.output and not _has_errors(problems):
        output, output_problem = _workspace_output_path(root, args.output, code="context_pack_output_outside_workspace")
        if output_problem is not None:
            problems.append(output_problem)
            payload["ok"] = False
            payload["problems"] = [problem.to_dict() for problem in problems]
        else:
            if data:
                payload["data"]["artifact"] = {
                    "path": output.relative_to(root).as_posix(),
                    "pack_digest": data.get("pack_digest", ""),
                }
            _complete_json_envelope(payload)
            atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if args.json:
        _json(payload)
    else:
        groups = data.get("groups", {})
        print(f"context pack task={data.get('task', {}).get('id', args.task)} must_read={len(groups.get('must_read', []))} maybe={len(groups.get('maybe_relevant', []))} verification={len(groups.get('verification_hints', []))}")
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
        max_reviewed_knowledge_drop=args.max_reviewed_knowledge_drop,
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
        reviewed = deltas.get("reviewed_knowledge", {}).get("delta", 0)
        print(f"context pack compare must_read_delta={must_read} reviewed_knowledge_delta={reviewed}")
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
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [
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
    source_modes = [bool(args.source), bool(args.from_receipt), bool(args.from_pack)]
    if sum(1 for enabled in source_modes if enabled) != 1:
        data: dict[str, Any] = {}
        problems = [Problem("error", "knowledge_candidate_source_required", "provide exactly one of --source, --from-receipt, or --from-pack")]
    elif args.from_receipt:
        data, problems = build_knowledge_candidate_from_receipt(root, task_id=args.from_receipt, repo_id=args.repo_id, kind=args.kind)
    elif args.from_pack:
        data, problems = build_knowledge_candidate_from_pack(root, pack=Path(args.from_pack), repo_id=args.repo_id, kind=args.kind)
    else:
        data, problems = build_knowledge_candidate(root, source=Path(args.source), repo_id=args.repo_id, kind=args.kind)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge candidate build",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
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
        candidate = data.get("candidate", {}) if data else {}
        print(f"knowledge candidate {candidate.get('id', '')} path={data.get('path', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


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
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge candidate show",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
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
        candidate = data.get("candidate", {}) if data else {}
        print(f"knowledge candidate {candidate.get('id', args.candidate_id)}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


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
        data, problems = refresh_stale_knowledge_candidates(root, repo_id=args.repo_id)
    elif args.candidate_id:
        data, problems = refresh_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id)
    else:
        data = {}
        problems = [Problem("error", "knowledge_candidate_refresh_target_required", "provide a candidate id or --all-stale")]
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
        else:
            candidate = data.get("candidate", {}) if data else {}
            print(f"knowledge candidate refresh old={args.candidate_id} new={candidate.get('id', '')}")
        for problem in problems:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_approve(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    data, problems = approve_knowledge_candidate(root, repo_id=args.repo_id, candidate_id=args.candidate_id, supersedes=args.supersedes)
    record = data.get("record", {}) if data else {}
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge approve",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": _knowledge_approval_warnings(record),
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
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge query",
        "data": data,
        "problems": [problem.to_dict() for problem in problems],
        "warnings": [problem.to_dict() for problem in warnings],
    }
    if args.json:
        _json(payload)
    else:
        print(f"knowledge query repo_id={args.repo_id} results={data.get('result_count', 0)}")
        for problem in [*problems, *warnings]:
            print(problem.message)
    return 1 if _has_errors(problems) else 0


def cmd_knowledge_render(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    require_repo_target(root, repo_id=args.repo_id)
    output = Path(args.output) if args.output else _default_knowledge_render_output(args.repo_id)
    data, problems = render_knowledge(root, repo_id=args.repo_id, output=output, check=args.check)
    payload = {
        "ok": not _has_errors(problems),
        "command": "knowledge render",
        "data": data,
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


def _default_knowledge_render_output(repo_id: str) -> Path:
    if repo_id == "main":
        return Path("docs/knowledge/generated")
    return Path("docs/knowledge/generated") / repo_id


def cmd_upgrade_plan(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    data = plan_upgrade(root, source=args.source)
    problems = [{"severity": "error", **conflict} for conflict in data.get("conflicts", [])]
    payload = {
        "ok": not problems,
        "command": "upgrade.plan",
        "data": data,
        "problems": problems,
        "warnings": [],
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
    root = find_workspace_root()
    data = apply_upgrade(root, plan_file=args.plan_file)
    payload = {
        "ok": True,
        "command": "upgrade.apply",
        "data": data,
        "problems": [],
        "warnings": [],
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
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    field_gate = sub.add_parser("field-gate")
    field_gate_sub = field_gate.add_subparsers(dest="field_gate_command", required=True, parser_class=RepoctlArgumentParser)
    field_gate_run = field_gate_sub.add_parser("run")
    field_gate_run.add_argument("gate", choices=["release-candidate"])
    field_gate_run.add_argument("--repo-id", default="main")
    field_gate_run.add_argument("--output")
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
    field_gate_cleanup = field_gate_sub.add_parser("cleanup")
    field_gate_cleanup.add_argument("--artifact", required=True)
    field_gate_cleanup.add_argument("--json", action="store_true")
    field_gate_cleanup.set_defaults(func=cmd_field_gate_cleanup)

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
    task_show.add_argument("--json", action="store_true")
    task_show.set_defaults(func=cmd_task_show)
    task_doctor = task_sub.add_parser("doctor")
    task_doctor.add_argument("task_id")
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
    task_discovery_add.add_argument("--note", help="short rationale for the chosen scope")
    task_discovery_add.add_argument("--json", action="store_true")
    task_discovery_add.set_defaults(func=cmd_task_discovery_add)
    task_start = task_sub.add_parser("start")
    task_start.add_argument("task_id")
    task_start.add_argument("--force-dirty", action="store_true")
    task_start.add_argument("--json", action="store_true")
    task_start.set_defaults(func=cmd_task_start)
    task_finish = task_sub.add_parser("finish")
    task_finish.add_argument("task_id")
    task_finish.add_argument("--verification-file")
    task_finish.add_argument("--use-task-verification", action="store_true", help="use the current ## Verification section as the finish evidence")
    task_finish.add_argument("--json", action="store_true")
    task_finish.set_defaults(func=cmd_task_finish)
    task_block = task_sub.add_parser("block")
    task_block.add_argument("task_id")
    task_block.add_argument("--verification-file")
    task_block.add_argument("--use-task-verification", action="store_true", help="use the current ## Verification section as blocker evidence")
    task_block.add_argument("--json", action="store_true")
    task_block.set_defaults(func=cmd_task_block)
    task_cancel = task_sub.add_parser("cancel")
    task_cancel.add_argument("task_id")
    task_cancel.add_argument("--verification-file")
    task_cancel.add_argument("--use-task-verification", action="store_true", help="use the current ## Verification section as cancellation evidence")
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
    meta_suggest_cmd.add_argument("text_arg", nargs="?")
    meta_suggest_cmd.add_argument("--repo-id")
    meta_suggest_cmd.add_argument("--text")
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
    graph_build.add_argument("--json", action="store_true")
    graph_build.set_defaults(func=cmd_graph_build)
    graph_query = graph_sub.add_parser("query")
    graph_query.add_argument("--repo-id")
    graph_query.add_argument("--file", default="")
    graph_query.add_argument("--topic", default="")
    graph_query.add_argument("--import", dest="import_ref", default="")
    graph_query.add_argument("--json", action="store_true")
    graph_query.set_defaults(func=cmd_graph_query)

    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_command", required=True, parser_class=RepoctlArgumentParser)
    context_query = context_sub.add_parser("query")
    context_query.add_argument("query")
    context_query.add_argument("--repo-id")
    context_query.add_argument("--budget-tokens", type=int, default=3000)
    context_query.add_argument("--explain", action="store_true")
    context_query.add_argument("--json", action="store_true")
    context_query.set_defaults(func=cmd_context_query)
    context_benchmark = context_sub.add_parser("benchmark")
    context_benchmark.add_argument("--fixture", default="tests/fixtures/context-benchmark")
    context_benchmark.add_argument("--repo-id")
    context_benchmark.add_argument("--budget-tokens", type=int, default=3000)
    context_benchmark.add_argument("--min-recall-at-5", type=float)
    context_benchmark.add_argument("--min-precision-at-5", type=float)
    context_benchmark.add_argument("--min-knowledge-recall-at-5", type=float)
    context_benchmark.add_argument("--min-category-recall-at-5", action="append", default=[])
    context_benchmark.add_argument("--min-category-knowledge-recall-at-5", action="append", default=[])
    context_benchmark.add_argument("--min-category-graph-edge-recall", action="append", default=[])
    context_benchmark.add_argument("--min-category-packed-recall", action="append", default=[])
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
    context_pack.add_argument("--budget-tokens", type=int, default=5000)
    context_pack.add_argument("--explain", action="store_true")
    context_pack.add_argument("--output")
    context_pack.add_argument("--json", action="store_true")
    context_pack.set_defaults(func=cmd_context_pack)
    context_pack_compare = context_sub.add_parser("pack-compare")
    context_pack_compare.add_argument("--baseline", required=True)
    context_pack_compare.add_argument("--candidate", required=True)
    context_pack_compare.add_argument("--max-must-read-drop", type=int)
    context_pack_compare.add_argument("--max-reviewed-knowledge-drop", type=int)
    context_pack_compare.add_argument("--require-warning-stability", action="store_true")
    context_pack_compare.add_argument("--json", action="store_true")
    context_pack_compare.set_defaults(func=cmd_context_pack_compare)
    context_pack_benchmark = context_sub.add_parser("pack-benchmark")
    context_pack_benchmark.add_argument("--fixture", default="tests/fixtures/context-pack-benchmark")
    context_pack_benchmark.add_argument("--repo-id", required=True)
    context_pack_benchmark.add_argument("--budget-tokens", type=int, default=5000)
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
    knowledge_candidate_build.add_argument("--repo-id", required=True)
    knowledge_candidate_build.add_argument("--kind", choices=sorted(["decision", "failure_mode", "invariant"]), default="decision")
    knowledge_candidate_build.add_argument("--json", action="store_true")
    knowledge_candidate_build.set_defaults(func=cmd_knowledge_candidate_build)
    knowledge_candidate_list = knowledge_candidate_sub.add_parser("list")
    knowledge_candidate_list.add_argument("--repo-id", required=True)
    knowledge_candidate_list.add_argument("--with-checks", action="store_true")
    knowledge_candidate_list.add_argument("--json", action="store_true")
    knowledge_candidate_list.set_defaults(func=cmd_knowledge_candidate_list)
    knowledge_candidate_show = knowledge_candidate_sub.add_parser("show")
    knowledge_candidate_show.add_argument("candidate_id")
    knowledge_candidate_show.add_argument("--repo-id", required=True)
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
    knowledge_query.add_argument("--json", action="store_true")
    knowledge_query.set_defaults(func=cmd_knowledge_query)
    knowledge_render = knowledge_sub.add_parser("render")
    knowledge_render.add_argument("--repo-id", required=True)
    knowledge_render.add_argument("--output")
    knowledge_render.add_argument("--check", action="store_true")
    knowledge_render.add_argument("--json", action="store_true")
    knowledge_render.set_defaults(func=cmd_knowledge_render)

    upgrade = sub.add_parser("upgrade")
    upgrade_sub = upgrade.add_subparsers(dest="upgrade_command", required=True, parser_class=RepoctlArgumentParser)
    upgrade_plan = upgrade_sub.add_parser("plan")
    upgrade_plan.add_argument("--from", dest="source", required=True, help="repoctl release checkout or extracted artifact directory")
    upgrade_plan.add_argument("--output", help="optional path for a plan artifact; omitted keeps the command read-only")
    upgrade_plan.add_argument("--json", action="store_true")
    upgrade_plan.set_defaults(func=cmd_upgrade_plan)
    upgrade_apply = upgrade_sub.add_parser("apply")
    upgrade_apply.add_argument("--plan-file", required=True)
    upgrade_apply.add_argument("--json", action="store_true")
    upgrade_apply.set_defaults(func=cmd_upgrade_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
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
        if getattr(args, "json", False):
            problem = {"severity": "error", "code": error.code, "message": str(error)}
            if error.path:
                problem["path"] = error.path
            _json({"ok": False, "command": _command_name(args), "data": {"task_id": getattr(args, "task_id", "")}, "problems": [problem], "warnings": []})
        else:
            print(f"repoctl: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        message = str(error)
        if getattr(args, "json", False):
            _json({"ok": False, "command": _command_name(args), "data": {}, "problems": [{"severity": "error", "code": "io_error", "message": message}], "warnings": []})
        else:
            print(f"repoctl: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
