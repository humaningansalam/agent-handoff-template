from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from collections import Counter
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .io import RepoctlError


DEBUG_EVENTS_REL = Path("docs/tasks/.repoctl-state/debug/events.jsonl")
DEBUG_TRUNCATED_REL = DEBUG_EVENTS_REL.with_name("capture-truncated")
DEBUG_EVENT_SCHEMA = "repoctl.debug.event"
DEBUG_EVENT_SCHEMA_VERSION = 1
_MAX_ITEMS = 100
_MAX_EVENT_BYTES = 64 * 1024
_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
_TASK_ID_RE = re.compile(r"T-[0-9]{14}Z")
_REPO_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_OPTION_RE = re.compile(r"--[a-z0-9][a-z0-9-]{0,63}")
_COMMAND_RE = re.compile(r"(?:repoctl|[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*)")
_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_OUTPUT_OPTIONS = frozenset({"--json", "--full", "--explain", "--verbose", "--format"})
_CURRENT: ContextVar[dict[str, Any] | None] = ContextVar("repoctl_debug", default=None)


def begin_debug(raw_argv: list[str]) -> Token[dict[str, Any] | None]:
    return _CURRENT.set(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": "repoctl",
            "request": {"option_names": [], "argument_count": len(raw_argv)},
            "target": {},
            "problem_codes": set(),
            "warning_codes": set(),
        }
    )


def bind_debug_command(command: str, args: Any | None = None, *, raw_argv: list[str] | None = None) -> None:
    trace = _CURRENT.get()
    if trace is None:
        return
    trace["command"] = command
    if raw_argv is not None:
        trace["request"]["option_names"] = _option_names(raw_argv)
    if args is not None:
        _record_id(trace["target"], "repo_id", getattr(args, "repo_id", ""), _REPO_ID_RE)
        for name in ("task_id", "from_task", "task"):
            _record_id(trace["target"], "task_id", getattr(args, name, ""), _TASK_ID_RE)


def observe_problem(code: str) -> None:
    trace = _CURRENT.get()
    if trace is not None and _CODE_RE.fullmatch(code):
        trace["problem_codes"].add(code)


def observe_envelope(envelope: Any) -> None:
    trace = _CURRENT.get()
    if trace is None or not isinstance(envelope, dict):
        return
    for field in ("problems", "warnings"):
        destination = trace[f"{field[:-1]}_codes"]
        destination.update(
            code
            for item in envelope.get(field, [])
            if isinstance(item, dict)
            and isinstance((code := item.get("code")), str)
            and _CODE_RE.fullmatch(code)
        )
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    _record_id(trace["target"], "task_id", data.get("task_id"), _TASK_ID_RE)
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    _record_id(trace["target"], "task_id", task.get("id"), _TASK_ID_RE)
    _record_id(trace["target"], "repo_id", task.get("repo_id"), _REPO_ID_RE)
    repository = data.get("repository") if isinstance(data.get("repository"), dict) else {}
    _record_id(trace["target"], "repo_id", repository.get("id"), _REPO_ID_RE)


def observe_context(
    repo_id: str,
    bundle: Mapping[str, Any],
    *,
    source_completeness: Mapping[str, Any] | None = None,
) -> None:
    trace = _CURRENT.get()
    if trace is None:
        return
    _record_id(trace["target"], "repo_id", repo_id, _REPO_ID_RE)
    completeness = source_completeness or (
        bundle.get("completeness") if isinstance(bundle.get("completeness"), Mapping) else {}
    )
    project = completeness.get("project_knowledge") if isinstance(completeness.get("project_knowledge"), Mapping) else {}
    reviewed = project.get("reviewed_records") if isinstance(project.get("reviewed_records"), Mapping) else {}
    history = project.get("task_history") if isinstance(project.get("task_history"), Mapping) else {}
    anchor = completeness.get("graph_anchor") if isinstance(completeness.get("graph_anchor"), Mapping) else {}
    explicit_history = completeness.get("explicit_task_history") if isinstance(completeness.get("explicit_task_history"), Mapping) else {}
    history_status = str(explicit_history.get("status") or "disabled")
    trace["context_sources"] = {
        "graph": {
            "available": bool(completeness.get("graph_available")),
            "anchor_status": str(anchor.get("status") or "not_requested"),
        },
        "knowledge": {
            "consulted": bool(reviewed.get("queried")),
            "available": _optional_count(reviewed.get("available_record_count")),
            "returned": _optional_count(reviewed.get("result_count")),
        },
        "task_history": {
            "consulted": history_status != "disabled",
            "status": history_status,
            "returned": _optional_count(history.get("result_count")),
        },
    }


def finish_debug(root: Path, token: Token[dict[str, Any] | None], *, exit_code: int, duration_ms: int) -> None:
    trace = _CURRENT.get()
    try:
        if trace is None or trace["command"] == "debug.summary":
            return
        event: dict[str, Any] = {
            "schema": DEBUG_EVENT_SCHEMA,
            "schema_version": DEBUG_EVENT_SCHEMA_VERSION,
            "timestamp": trace["timestamp"],
            "duration_ms": max(0, duration_ms),
            "command": trace["command"],
            "request": trace["request"],
            "target": trace["target"],
            "outcome": {
                "exit_code": exit_code,
                "ok": exit_code == 0,
                "problem_codes": sorted(trace["problem_codes"])[:_MAX_ITEMS],
                "warning_codes": sorted(trace["warning_codes"])[:_MAX_ITEMS],
            },
        }
        for field in ("context_sources",):
            if field in trace:
                event[field] = trace[field]
        append_debug_event(root, event)
    finally:
        _CURRENT.reset(token)


def debug_summary(root: Path) -> dict[str, Any]:
    events, invalid_count, incomplete = _read_events(root)
    events = [event for event in events if event["command"] != "debug.summary"]
    commands = _command_summary(events)
    problems = Counter(code for event in events for code in event["outcome"]["problem_codes"])
    warnings = Counter(code for event in events for code in event["outcome"]["warning_codes"])
    timestamps = [event["timestamp"] for event in events]
    return {
        "capture": {
            "event_count": len(events),
            "invalid_event_count": invalid_count,
            "incomplete": incomplete,
            "max_journal_bytes": _MAX_JOURNAL_BYTES,
            "first_timestamp": min(timestamps, default=None),
            "last_timestamp": max(timestamps, default=None),
        },
        "commands": commands,
        "context_sources": _context_source_summary(events),
        "outcomes": {
            "succeeded": sum(event["outcome"]["ok"] for event in events),
            "failed": sum(not event["outcome"]["ok"] for event in events),
            "problem_codes": dict(sorted(problems.items())),
            "warning_codes": dict(sorted(warnings.items())),
        },
    }


def _command_summary(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    commands: dict[str, dict[str, int]] = {}
    failed_shapes: set[tuple[Any, ...]] = set()
    for event in events:
        command = event["command"]
        stats = commands.setdefault(command, {"called": 0, "succeeded": 0, "failed": 0, "total_duration_ms": 0, "max_duration_ms": 0, "later_same_shape_success_after_failure": 0})
        ok, duration = event["outcome"]["ok"], event["duration_ms"]
        stats["called"] += 1
        stats["succeeded" if ok else "failed"] += 1
        stats["total_duration_ms"] += duration
        stats["max_duration_ms"] = max(stats["max_duration_ms"], duration)
        if command == "task.resume":
            if "task_resume_ambiguous" in event["outcome"]["problem_codes"]:
                stats["resume_ambiguous"] = stats.get("resume_ambiguous", 0) + 1
            elif ok and event["target"].get("task_id"):
                stats["resume_selected"] = stats.get("resume_selected", 0) + 1
            elif ok:
                stats["resume_no_live"] = stats.get("resume_no_live", 0) + 1
        shape = _request_shape(event)
        if ok and shape in failed_shapes:
            stats["later_same_shape_success_after_failure"] += 1
            failed_shapes.remove(shape)
        elif not ok:
            failed_shapes.add(shape)
    for stats in commands.values():
        stats["mean_duration_ms"] = round(stats["total_duration_ms"] / stats["called"])
    return dict(sorted(commands.items()))


def _context_source_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    graph: dict[str, Any] = {"queries": 0, "available": 0, "anchor_statuses": Counter()}
    knowledge = {"consulted": 0, "queries_with_results": 0, "returned": 0}
    history: dict[str, Any] = {"consulted": 0, "statuses": Counter(), "queries_with_results": 0, "returned": 0}
    for event in events:
        sources = event.get("context_sources")
        if not isinstance(sources, dict):
            continue
        graph_source = sources["graph"]
        graph["queries"] += 1
        graph["available"] += int(graph_source["available"])
        graph["anchor_statuses"][graph_source["anchor_status"]] += 1
        for name, summary in (("knowledge", knowledge), ("task_history", history)):
            source = sources[name]
            summary["consulted"] += int(source["consulted"])
            returned = source["returned"] or 0
            summary["queries_with_results"] += int(returned > 0)
            summary["returned"] += returned
        history["statuses"][sources["task_history"]["status"]] += 1
    graph["anchor_statuses"] = dict(sorted(graph["anchor_statuses"].items()))
    history["statuses"] = dict(sorted(history["statuses"].items()))
    return {"graph": graph, "knowledge": knowledge, "task_history": history}


def append_debug_event(root: Path, event: Mapping[str, Any]) -> None:
    line = (json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(line) > _MAX_EVENT_BYTES:
        return
    _debug_directory(root, create=True)
    descriptor = _open_no_follow(root / DEBUG_EVENTS_REL, os.O_RDWR | os.O_CREAT | os.O_APPEND)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RepoctlError("debug journal must be a regular file", code="unsafe_debug_path", path=DEBUG_EVENTS_REL.as_posix())
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if os.fstat(descriptor).st_size + len(line) > _MAX_JOURNAL_BYTES:
            os.close(_open_no_follow(root / DEBUG_TRUNCATED_REL, os.O_WRONLY | os.O_CREAT))
            os.ftruncate(descriptor, 0)
        view = memoryview(line)
        while view:
            view = view[os.write(descriptor, view) :]
    finally:
        os.close(descriptor)


def _read_events(root: Path) -> tuple[list[dict[str, Any]], int, bool]:
    if _debug_directory(root, create=False) is None:
        return [], 0, False
    marker = root / DEBUG_TRUNCATED_REL
    incomplete = _safe_file(marker, required=False)
    path = root / DEBUG_EVENTS_REL
    if not path.exists():
        return [], 0, incomplete
    _safe_file(path)
    events: list[dict[str, Any]] = []
    invalid = 0
    descriptor = _open_no_follow(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            if os.fstat(descriptor).st_size > _MAX_JOURNAL_BYTES:
                handle.seek(-_MAX_JOURNAL_BYTES, os.SEEK_END)
                handle.readline()
                incomplete = True
            for line in handle:
                try:
                    event = json.loads(line) if len(line) <= _MAX_EVENT_BYTES else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    event = None
                if _valid_event(event):
                    events.append(event)
                else:
                    invalid += 1
    finally:
        os.close(descriptor)
    return events, invalid, incomplete


def _debug_directory(root: Path, *, create: bool) -> Path | None:
    current = root
    for part in DEBUG_EVENTS_REL.parent.parts:
        current /= part
        if current.is_symlink():
            raise RepoctlError("debug path must not contain symlinks", code="unsafe_debug_path", path=DEBUG_EVENTS_REL.as_posix())
        if create:
            current.mkdir(exist_ok=True)
        if not current.exists():
            return None
        if not current.is_dir():
            raise RepoctlError("debug path parent must be a directory", code="unsafe_debug_path", path=DEBUG_EVENTS_REL.as_posix())
    return current


def _safe_file(path: Path, *, required: bool = True) -> bool:
    if not path.exists():
        if required:
            raise RepoctlError("debug file is missing", code="unsafe_debug_path", path=path.name)
        return False
    if path.is_symlink() or not path.is_file():
        raise RepoctlError("debug file must be regular", code="unsafe_debug_path", path=path.name)
    return True


def _open_no_follow(path: Path, flags: int) -> int:
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)


def _valid_event(value: Any) -> bool:
    try:
        return bool(
            isinstance(value, dict)
            and value.get("schema") == DEBUG_EVENT_SCHEMA
            and value.get("schema_version") == DEBUG_EVENT_SCHEMA_VERSION
            and isinstance(value.get("timestamp"), str)
            and type(value.get("duration_ms")) is int
            and value["duration_ms"] >= 0
            and isinstance(value.get("command"), str)
            and _COMMAND_RE.fullmatch(value["command"])
            and _valid_request(value.get("request"))
            and _valid_target(value.get("target"))
            and _valid_outcome(value.get("outcome"))
            and _valid_sources(value.get("context_sources"))
        )
    except (KeyError, TypeError):
        return False


def _valid_request(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("option_names"), list)
        and value["option_names"] == sorted(set(value["option_names"]))
        and len(value["option_names"]) <= _MAX_ITEMS
        and all(isinstance(name, str) and _OPTION_RE.fullmatch(name) for name in value["option_names"])
        and type(value.get("argument_count")) is int
        and value["argument_count"] >= 0
    )


def _valid_target(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and ("repo_id" not in value or isinstance(value["repo_id"], str) and _REPO_ID_RE.fullmatch(value["repo_id"]))
        and ("task_id" not in value or isinstance(value["task_id"], str) and _TASK_ID_RE.fullmatch(value["task_id"]))
    )


def _valid_outcome(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and type(value.get("exit_code")) is int
        and isinstance(value.get("ok"), bool)
        and value["ok"] == (value["exit_code"] == 0)
        and all(
            isinstance(codes, list)
            and len(codes) <= _MAX_ITEMS
            and all(isinstance(code, str) and _CODE_RE.fullmatch(code) for code in codes)
            for codes in (value.get("problem_codes"), value.get("warning_codes"))
        )
    )


def _valid_sources(value: Any) -> bool:
    if value is None:
        return True
    try:
        graph, knowledge, history = value["graph"], value["knowledge"], value["task_history"]
        optional = (knowledge["available"], knowledge["returned"], history["returned"])
        return bool(
            isinstance(graph["available"], bool)
            and graph["anchor_status"] in {"not_requested", "resolved", "ambiguous", "unresolved"}
            and isinstance(knowledge["consulted"], bool)
            and isinstance(history["consulted"], bool)
            and history["status"] in {"disabled", "not_applicable", "available", "partial", "unavailable"}
            and all(count is None or type(count) is int and count >= 0 for count in optional)
        )
    except (KeyError, TypeError):
        return False


def _request_shape(event: Mapping[str, Any]) -> tuple[Any, ...]:
    target, request = event["target"], event["request"]
    return (
        event["command"],
        target.get("repo_id", ""),
        target.get("task_id", ""),
        request["argument_count"],
        tuple(name for name in request["option_names"] if name not in _OUTPUT_OPTIONS),
    )


def _option_names(raw_argv: list[str]) -> list[str]:
    names: set[str] = set()
    for value in raw_argv:
        if value == "--":
            break
        name = value.split("=", 1)[0]
        if value.startswith("--") and _OPTION_RE.fullmatch(name):
            names.add(name)
    return sorted(names)[:_MAX_ITEMS]


def _record_id(target: dict[str, str], key: str, value: Any, pattern: re.Pattern[str]) -> None:
    if isinstance(value, str) and pattern.fullmatch(value):
        target[key] = value


def _optional_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None
