from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry
from .graph_semantic_model import (
    PreciseCall,
    PreciseSymbol,
    ProviderFailure,
    RpcInvocationContract,
    RpcInvocationFact,
    RpcParamsReasonCode,
    RpcParamsStatus,
    RpcRoutineReasonCode,
    RpcRoutineStatus,
    RpcSchemaSelection,
    SemanticProviderResult,
    SourceAnchor,
)
from .io import atomic_write
from .repositories import RepoTarget


DART_PROVIDER_LANGUAGES = frozenset({"dart"})
PROVIDER = "dart_analyzer"
HELPER_PUBSPEC = """name: repoctl_dart_analyzer_helper
publish_to: none
environment:
  sdk: '>=3.6.0 <4.0.0'
dependencies:
  analyzer: 8.2.0
  path: '>=1.9.0 <2.0.0'
"""


def _dart_executable() -> tuple[str, str]:
    dart = shutil.which("dart")
    if dart:
        return dart, "path"
    flutter = shutil.which("flutter")
    if flutter:
        candidate = Path(flutter).resolve().with_name("dart")
        if candidate.is_file():
            return str(candidate), "flutter_sdk"
    return "", ""


def _dart_sdk_path(dart: str) -> str:
    executable = Path(dart).resolve()
    flutter_sdk = executable.parent.parent / "bin/cache/dart-sdk"
    if (flutter_sdk / "lib/core/core.dart").is_file():
        return str(flutter_sdk)
    sdk = executable.parent.parent
    return str(sdk) if (sdk / "lib/core/core.dart").is_file() else ""


def _helper_package(root: Path, dart: str) -> tuple[Path | None, str]:
    package_root = root / ".repoctl-state/graph-tools/dart-analyzer"
    pubspec = package_root / "pubspec.yaml"
    try:
        current = pubspec.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != HELPER_PUBSPEC:
        atomic_write(pubspec, HELPER_PUBSPEC)
    package_config = package_root / ".dart_tool/package_config.json"
    if package_config.is_file() and current == HELPER_PUBSPEC:
        return package_config, ""
    try:
        completed = subprocess.run(
            [dart, "pub", "get", "--offline"],
            cwd=package_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if completed.returncode != 0 or not package_config.is_file():
        return None, (completed.stderr or completed.stdout or "offline analyzer package resolution failed").strip()
    return package_config, ""


def _helper_executable(root: Path, dart: str) -> tuple[Path | None, str]:
    package_config, error = _helper_package(root, dart)
    if package_config is None:
        return None, error
    source = Path(__file__).with_name("providers") / "dart_semantics.dart"
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        return None, str(exc)
    digest = hashlib.sha256(HELPER_PUBSPEC.encode("utf-8") + source_bytes).hexdigest()
    package_root = package_config.parent.parent
    executable = package_root / "dart_semantics"
    digest_path = package_root / "dart_semantics.sha256"
    try:
        if executable.is_file() and digest_path.read_text(encoding="utf-8").strip() == digest:
            return executable, ""
    except OSError:
        pass
    temporary = package_root / f"dart_semantics.tmp-{os.getpid()}"
    try:
        completed = subprocess.run(
            [dart, "compile", "exe", f"--packages={package_config}", "-o", str(temporary), str(source)],
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if completed.returncode != 0 or not temporary.is_file():
        return None, (completed.stderr or completed.stdout or "Dart analyzer helper compilation failed").strip()
    os.replace(temporary, executable)
    atomic_write(digest_path, digest + "\n")
    return executable, ""


def _anchor(data: Any, *, eligible_paths: set[str]) -> SourceAnchor | None:
    if not isinstance(data, dict):
        return None
    path = str(data.get("path") or "")
    if path not in eligible_paths:
        return None
    try:
        values = [int(data[key]) for key in ("start_line", "start_col", "end_line", "end_col")]
    except (KeyError, TypeError, ValueError):
        return None
    if values[0] < 1 or values[1] < 0 or values[2] < values[0] or values[3] < 0:
        return None
    return SourceAnchor(path, *values)


def _symbols(data: Any, *, eligible_paths: set[str]) -> tuple[list[PreciseSymbol], set[str]]:
    symbols: list[PreciseSymbol] = []
    symbol_ids: set[str] = set()
    if not isinstance(data, list):
        return symbols, symbol_ids
    for raw in data:
        if not isinstance(raw, dict):
            continue
        anchor = _anchor(raw.get("anchor"), eligible_paths=eligible_paths)
        provider_symbol_id = str(raw.get("provider_symbol_id") or "")
        name = str(raw.get("name") or "")
        if anchor is None or not provider_symbol_id or not name or provider_symbol_id in symbol_ids:
            continue
        symbols.append(
            PreciseSymbol(
                path=anchor.path,
                provider=PROVIDER,
                provider_symbol_id=provider_symbol_id,
                language="dart",
                kind=str(raw.get("kind") or "symbol"),
                name=name,
                qualified_name=str(raw.get("qualified_name") or name),
                anchor=anchor,
            )
        )
        symbol_ids.add(provider_symbol_id)
    return sorted(symbols, key=lambda item: item.provider_symbol_id), symbol_ids


def _calls(data: Any, *, eligible_paths: set[str], symbol_ids: set[str]) -> list[PreciseCall]:
    calls: list[PreciseCall] = []
    seen: set[tuple[str, str, int, int]] = set()
    if not isinstance(data, list):
        return calls
    for raw in data:
        if not isinstance(raw, dict):
            continue
        anchor = _anchor(raw.get("anchor"), eligible_paths=eligible_paths)
        caller = str(raw.get("caller_provider_symbol_id") or "")
        callee = str(raw.get("callee_provider_symbol_id") or "")
        if anchor is None or caller not in symbol_ids or callee not in symbol_ids:
            continue
        key = (caller, callee, anchor.start_line, anchor.start_col)
        if key in seen:
            continue
        seen.add(key)
        calls.append(
            PreciseCall(
                path=anchor.path,
                provider=PROVIDER,
                caller_provider_symbol_id=caller,
                callee_provider_symbol_id=callee,
                language="dart",
                scope=str(raw.get("scope") or "same_file"),
                anchor=anchor,
            )
        )
    return sorted(calls, key=lambda item: (item.caller_provider_symbol_id, item.callee_provider_symbol_id, item.anchor.start_line, item.anchor.start_col))


def _rpc_invocations(
    data: Any,
    *,
    repo_id: str,
    repo: Path,
    selected_paths: set[str],
) -> tuple[list[RpcInvocationFact] | None, str]:
    if not isinstance(data, list):
        return None, "rpc_invocations must be a list"
    content_identities: dict[str, str] = {}
    facts: list[RpcInvocationFact] = []
    seen_ids: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict):
            return None, "rpc invocation must be an object"
        anchor = _anchor(raw.get("anchor"), eligible_paths=selected_paths)
        path = str(raw.get("path") or "")
        if anchor is None or path != anchor.path or path not in selected_paths:
            return None, "rpc invocation path or anchor is outside the selected analysis set"
        try:
            start_offset = int(raw["start_offset"])
            end_offset = int(raw["end_offset"])
            syntactic_argument_count = int(raw["syntactic_argument_count"])
        except (KeyError, TypeError, ValueError):
            return None, "rpc invocation offsets and argument count must be integers"
        if start_offset < 0 or end_offset <= start_offset or syntactic_argument_count < 0:
            return None, "rpc invocation offsets or argument count are invalid"
        resolved_callee_identity = str(raw.get("resolved_callee_identity") or "")
        if not resolved_callee_identity:
            return None, "rpc invocation is missing resolved callee identity"
        routine = raw.get("routine")
        params = raw.get("params")
        invocation = RpcInvocationContract.from_dict(raw.get("invocation"))
        schema_selection = RpcSchemaSelection.from_dict(raw.get("schema_selection"))
        if not isinstance(routine, dict) or not isinstance(params, dict) or invocation is None or schema_selection is None:
            return None, "rpc invocation contract, schema, routine, and params evidence must be valid objects"
        try:
            routine_status = RpcRoutineStatus(str(routine.get("status") or ""))
            params_status = RpcParamsStatus(str(params.get("status") or ""))
        except ValueError:
            return None, "rpc invocation evidence status is invalid"
        raw_routine_value = routine.get("value")
        raw_routine_reason = routine.get("reason_code")
        if routine_status == RpcRoutineStatus.KNOWN:
            if "value" not in routine or not isinstance(raw_routine_value, str) or raw_routine_reason not in (None, ""):
                return None, "rpc invocation routine evidence is incomplete"
            routine_value = raw_routine_value
            routine_reason = None
        else:
            if not isinstance(raw_routine_reason, str) or not raw_routine_reason or raw_routine_value not in (None, ""):
                return None, "rpc invocation routine evidence is incomplete"
            routine_value = ""
            try:
                routine_reason = RpcRoutineReasonCode(raw_routine_reason)
            except ValueError:
                return None, "rpc invocation routine reason code is invalid"
        raw_names = params.get("known_names")
        if not isinstance(raw_names, list) or any(not isinstance(value, str) for value in raw_names):
            return None, "rpc invocation parameter names are invalid"
        param_names = tuple(sorted(set(raw_names)))
        if len(param_names) != len(raw_names):
            return None, "rpc invocation parameter names contain duplicates"
        raw_params_reason = str(params.get("reason_code") or "")
        if params_status == RpcParamsStatus.COMPLETE:
            if raw_params_reason:
                return None, "complete rpc parameter evidence must not contain a reason code"
            params_reason = None
        else:
            try:
                params_reason = RpcParamsReasonCode(raw_params_reason)
            except ValueError:
                return None, "non-complete rpc parameter evidence requires a valid reason code"
        try:
            content_sha256 = content_identities[path]
        except KeyError:
            try:
                content_sha256 = "sha256:" + hashlib.sha256((repo / path).read_bytes()).hexdigest()
            except OSError as exc:
                return None, f"rpc invocation source could not be read: {exc}"
            content_identities[path] = content_sha256
        identity = json.dumps(
            {
                "repository_id": repo_id,
                "path": path,
                "content_sha256": content_sha256,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "resolved_callee_identity": resolved_callee_identity,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fact_id = "rpc:" + hashlib.sha256(identity).hexdigest()
        if fact_id in seen_ids:
            return None, "rpc invocation facts contain a duplicate identity"
        seen_ids.add(fact_id)
        try:
            fact = RpcInvocationFact(
                fact_id=fact_id,
                repository_id=repo_id,
                path=path,
                provider=PROVIDER,
                language="dart",
                content_sha256=content_sha256,
                start_offset=start_offset,
                end_offset=end_offset,
                resolved_callee_identity=resolved_callee_identity,
                receiver_type=str(raw.get("receiver_type") or ""),
                invocation=invocation,
                schema_selection=schema_selection,
                routine_status=routine_status,
                routine=routine_value,
                routine_reason_code=routine_reason,
                params_status=params_status,
                param_names=param_names,
                params_reason_code=params_reason,
                syntactic_argument_count=syntactic_argument_count,
                anchor=anchor,
            )
        except ValueError as exc:
            return None, f"rpc invocation evidence is incoherent: {exc}"
        facts.append(fact)
    return sorted(facts, key=lambda item: item.fact_id), ""


def _unavailable(paths: tuple[str, ...], *, code: str, message: str) -> SemanticProviderResult:
    failures: tuple[ProviderFailure, ...] = ()
    if paths:
        failures = (ProviderFailure(PROVIDER, "symbols,calls,rpc", code, message, paths),)
    return SemanticProviderResult(
        provider=PROVIDER,
        languages=("dart",),
        symbol_failed_paths=paths,
        call_failed_paths=paths,
        rpc_failed_paths=paths,
        failures=failures,
    )


def build_dart_semantics(
    *,
    root: Path,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    analysis_paths: set[str] | None = None,
) -> SemanticProviderResult:
    paths = tuple(sorted(entry.path for entry in entries if entry.language == "dart"))
    if not paths:
        return SemanticProviderResult(provider=PROVIDER, languages=("dart",))
    selected = tuple(sorted(set(paths) if analysis_paths is None else set(paths) & analysis_paths))
    if not selected:
        return SemanticProviderResult(provider=PROVIDER, languages=("dart",))
    dart, dart_source = _dart_executable()
    if not dart:
        return _unavailable(selected, code="dart_provider_unavailable", message="the Dart SDK is not available")
    helper, helper_error = _helper_executable(root, dart)
    if helper is None:
        return _unavailable(selected, code="dart_provider_unavailable", message=f"package:analyzer helper is unavailable offline: {helper_error}")

    try:
        completed = subprocess.run(
            [str(helper)],
            input=json.dumps(
                {
                    "repo_root": str(target.root_path),
                    "paths": list(paths),
                    "analysis_paths": list(selected),
                    "sdk_path": _dart_sdk_path(dart),
                },
                separators=(",", ":"),
            ),
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
            cwd=target.root_path,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unavailable(selected, code="dart_provider_failed", message=f"Dart analyzer provider failed: {exc}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        data = None
    if completed.returncode != 0 or not isinstance(data, dict) or data.get("ok") is not True:
        message = str(data.get("error") if isinstance(data, dict) else completed.stderr or "invalid provider output")
        return _unavailable(selected, code="dart_provider_failed", message=f"Dart analyzer provider failed: {message}")

    eligible_paths = set(paths)
    selected_paths = set(selected)
    analyzed = tuple(sorted({str(path) for path in data.get("analyzed_paths", []) if str(path) in selected_paths}))
    failed = tuple(sorted(selected_paths - set(analyzed)))
    rpc_analyzed = tuple(sorted({str(path) for path in data.get("rpc_analyzed_paths", []) if str(path) in selected_paths}))
    rpc_failed = tuple(sorted({str(path) for path in data.get("rpc_failed_paths", []) if str(path) in selected_paths}))
    if set(rpc_analyzed) & set(rpc_failed) or set(rpc_analyzed) | set(rpc_failed) != selected_paths:
        return _unavailable(selected, code="dart_provider_invalid_output", message="Dart analyzer RPC coverage did not account for every selected path")
    symbols, symbol_ids = _symbols(data.get("symbols"), eligible_paths=eligible_paths)
    calls = _calls(data.get("calls"), eligible_paths=eligible_paths, symbol_ids=symbol_ids)
    rpc_invocations, rpc_error = _rpc_invocations(
        data.get("rpc_invocations"),
        repo_id=target.id,
        repo=target.root_path,
        selected_paths=selected_paths,
    )
    if rpc_invocations is None:
        return _unavailable(selected, code="dart_provider_invalid_output", message=f"Dart analyzer RPC output is invalid: {rpc_error}")
    failure_items: list[ProviderFailure] = []
    if failed:
        failure_items.append(ProviderFailure(PROVIDER, "symbols,calls", "dart_analysis_failed", "Dart analyzer failed for one or more source files", failed))
    if rpc_failed:
        failure_items.append(
            ProviderFailure(
                PROVIDER,
                "rpc",
                "dart_rpc_enumeration_incomplete",
                "Dart analyzer could not classify every rpc invocation in one or more source files",
                rpc_failed,
            )
        )
    return SemanticProviderResult(
        provider=PROVIDER,
        languages=("dart",),
        symbols=tuple(symbols),
        calls=tuple(calls),
        rpc_invocations=tuple(rpc_invocations),
        symbol_analyzed_paths=analyzed,
        call_analyzed_paths=analyzed,
        rpc_analyzed_paths=rpc_analyzed,
        symbol_failed_paths=failed,
        call_failed_paths=failed,
        rpc_failed_paths=rpc_failed,
        failures=tuple(failure_items),
        tool={"kind": "dart_package_analyzer_aot", "source": dart_source},
    )
