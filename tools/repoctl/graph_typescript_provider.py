from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry
from .graph_semantic_model import PreciseCall, PreciseSymbol, ProviderFailure, SemanticProviderResult, SourceAnchor
from .io import atomic_write
from .repositories import RepoTarget


TYPESCRIPT_PROVIDER_LANGUAGES = frozenset({"javascript", "typescript"})
PROVIDER = "typescript_compiler"
BUNDLED_TYPESCRIPT_VERSION = "5.9.3"


def _nearest_config(repo: Path, path: str) -> Path | None:
    current = (repo / path).parent
    while current == repo or repo in current.parents:
        for name in ("tsconfig.json", "jsconfig.json"):
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current == repo:
            break
        current = current.parent
    return None


def typescript_analysis_units(repo: Path, paths: set[str]) -> dict[str, str]:
    units: dict[str, str] = {}
    for path in sorted(paths):
        config = _nearest_config(repo, path)
        units[path] = config.relative_to(repo).as_posix() if config is not None else ""
    return units


def _compiler_candidates(repo: Path, entries: list[CodeIndexEntry]) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    configured = os.environ.get("REPOCTL_TYPESCRIPT_LIB", "").strip()
    if configured:
        candidates.append((Path(configured).expanduser(), "environment"))

    directories = {repo}
    for entry in entries:
        if entry.language not in TYPESCRIPT_PROVIDER_LANGUAGES:
            continue
        current = (repo / entry.path).parent
        while current == repo or repo in current.parents:
            directories.add(current)
            if current == repo:
                break
            current = current.parent
    for directory in sorted(directories, key=lambda path: (len(path.parts), path.as_posix()), reverse=True):
        candidates.append((directory / "node_modules/typescript/lib/typescript.js", "product_local"))

    tsc = shutil.which("tsc")
    if tsc:
        resolved = Path(tsc).resolve()
        candidates.extend(
            (
                (resolved.parent.parent / "lib/typescript.js", "path"),
                (resolved.parent.parent / "node_modules/typescript/lib/typescript.js", "path"),
            )
        )
    node = shutil.which("node")
    if node:
        resolved = Path(node).resolve()
        for parent in resolved.parents:
            candidates.append((parent / "lib/node_modules/typescript/lib/typescript.js", "node_global"))

    deduped: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        normalized = path.resolve(strict=False).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append((path, source))
    return deduped


def _find_compiler(repo: Path, entries: list[CodeIndexEntry]) -> tuple[Path | None, str]:
    for path, source in _compiler_candidates(repo, entries):
        if path.is_file():
            return path.resolve(), source
    return None, ""


def _bundled_compiler(root: Path) -> tuple[Path | None, str]:
    archive = Path(__file__).with_name("providers") / f"typescript-{BUNDLED_TYPESCRIPT_VERSION}.js.gz"
    try:
        compressed = archive.read_bytes()
        digest = hashlib.sha256(compressed).hexdigest()
    except OSError:
        return None, ""
    cache = root / ".repoctl-state/graph-tools/typescript" / BUNDLED_TYPESCRIPT_VERSION
    compiler = cache / "typescript.js"
    marker = cache / "archive.sha256"
    try:
        if compiler.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
            return compiler, "repoctl_bundled"
    except OSError:
        pass
    try:
        content = gzip.decompress(compressed)
        temporary = cache / f"typescript.tmp-{os.getpid()}.js"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(content)
        os.replace(temporary, compiler)
        atomic_write(marker, digest + "\n")
    except (OSError, gzip.BadGzipFile):
        return None, ""
    return compiler, "repoctl_bundled"


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
        language = str(raw.get("language") or "")
        if anchor is None or not provider_symbol_id or language not in TYPESCRIPT_PROVIDER_LANGUAGES:
            continue
        symbol = PreciseSymbol(
            path=anchor.path,
            provider=PROVIDER,
            provider_symbol_id=provider_symbol_id,
            language=language,
            kind=str(raw.get("kind") or "symbol"),
            name=str(raw.get("name") or ""),
            qualified_name=str(raw.get("qualified_name") or raw.get("name") or ""),
            anchor=anchor,
        )
        if symbol.name and provider_symbol_id not in symbol_ids:
            symbols.append(symbol)
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
        language = str(raw.get("language") or "")
        if anchor is None or caller not in symbol_ids or callee not in symbol_ids or language not in TYPESCRIPT_PROVIDER_LANGUAGES:
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
                language=language,
                scope=str(raw.get("scope") or "same_file"),
                anchor=anchor,
            )
        )
    return sorted(calls, key=lambda item: (item.caller_provider_symbol_id, item.callee_provider_symbol_id, item.anchor.start_line, item.anchor.start_col))


def _unavailable(paths: tuple[str, ...], *, code: str, message: str) -> SemanticProviderResult:
    failures: tuple[ProviderFailure, ...] = ()
    if paths:
        failures = (ProviderFailure(PROVIDER, "symbols,calls", code, message, paths),)
    return SemanticProviderResult(
        provider=PROVIDER,
        languages=tuple(sorted(TYPESCRIPT_PROVIDER_LANGUAGES)),
        symbol_failed_paths=paths,
        call_failed_paths=paths,
        failures=failures,
    )


def build_typescript_semantics(
    *,
    root: Path,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    analysis_paths: set[str] | None = None,
) -> SemanticProviderResult:
    relevant = [entry for entry in entries if entry.language in TYPESCRIPT_PROVIDER_LANGUAGES]
    paths = tuple(sorted(entry.path for entry in relevant))
    if not paths:
        return SemanticProviderResult(provider=PROVIDER, languages=tuple(sorted(TYPESCRIPT_PROVIDER_LANGUAGES)))
    selected = tuple(sorted(set(paths) if analysis_paths is None else set(paths) & analysis_paths))
    if not selected:
        return SemanticProviderResult(provider=PROVIDER, languages=tuple(sorted(TYPESCRIPT_PROVIDER_LANGUAGES)))

    node = shutil.which("node")
    compiler, compiler_source = _find_compiler(target.root_path, relevant)
    if compiler is None:
        compiler, compiler_source = _bundled_compiler(root)
    if not node or compiler is None:
        missing = "Node.js" if not node else "the TypeScript compiler"
        return _unavailable(selected, code="typescript_provider_unavailable", message=f"{missing} is not available")

    helper = Path(__file__).with_name("providers") / "typescript_semantics.js"
    try:
        completed = subprocess.run(
            [node, str(helper), str(compiler)],
            input=json.dumps(
                {
                    "repo_root": str(target.root_path),
                    "paths": list(paths),
                    "analysis_paths": list(selected),
                },
                separators=(",", ":"),
            ),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
            cwd=target.root_path,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unavailable(selected, code="typescript_provider_failed", message=f"TypeScript compiler provider failed: {exc}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        data = None
    if completed.returncode != 0 or not isinstance(data, dict) or data.get("ok") is not True:
        message = str(data.get("error") if isinstance(data, dict) else completed.stderr or "invalid provider output")
        return _unavailable(selected, code="typescript_provider_failed", message=f"TypeScript compiler provider failed: {message}")

    eligible_paths = set(paths)
    selected_paths = set(selected)
    analyzed = tuple(sorted({str(path) for path in data.get("analyzed_paths", []) if str(path) in selected_paths}))
    failed = tuple(sorted(selected_paths - set(analyzed)))
    symbols, symbol_ids = _symbols(data.get("symbols"), eligible_paths=eligible_paths)
    calls = _calls(data.get("calls"), eligible_paths=eligible_paths, symbol_ids=symbol_ids)
    failures: tuple[ProviderFailure, ...] = ()
    if failed:
        failures = (
            ProviderFailure(
                PROVIDER,
                "symbols,calls",
                "typescript_analysis_failed",
                "TypeScript compiler analysis failed for one or more source files",
                failed,
            ),
        )
    return SemanticProviderResult(
        provider=PROVIDER,
        languages=tuple(sorted(TYPESCRIPT_PROVIDER_LANGUAGES)),
        symbols=tuple(symbols),
        calls=tuple(calls),
        symbol_analyzed_paths=analyzed,
        call_analyzed_paths=analyzed,
        symbol_failed_paths=failed,
        call_failed_paths=failed,
        failures=failures,
        tool={
            "kind": "typescript_compiler_api",
            "version": str(data.get("compiler_version") or ""),
            "source": compiler_source,
        },
    )
