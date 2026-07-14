from __future__ import annotations

import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry
from .graph_semantic_model import PreciseCall, PreciseSymbol, ProviderFailure, SemanticProviderResult, SourceAnchor
from .repositories import RepoTarget


CSHARP_PROVIDER_LANGUAGES = frozenset({"csharp"})
PROVIDER = "csharp_roslyn"


def _roslyn_candidates() -> list[tuple[Path, str]]:
    values: list[tuple[Path, str]] = []
    configured = os.environ.get("REPOCTL_ROSLYN_DIR", "").strip()
    if configured:
        values.append((Path(configured).expanduser(), "environment"))
    editor = os.environ.get("UNITY_EDITOR_PATH", "").strip()
    editor_roots = [Path(editor).expanduser()] if editor else []
    editor_roots.extend((Path.home() / "Unity/Hub/Editor").glob("*/Editor"))
    editor_roots.extend(Path("/opt/unity/Hub/Editor").glob("*/Editor"))
    for root in sorted(editor_roots, key=lambda path: path.as_posix(), reverse=True):
        values.extend(
            (
                (root / "Data/MonoBleedingEdge/lib/mono/msbuild/Current/bin/Roslyn", "unity_editor"),
                (root / "Data/MonoBleedingEdge/lib/mono/msbuild/15.0/bin/Roslyn", "unity_editor"),
                (root / "Data/Tools/Roslyn", "unity_editor"),
            )
        )
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in values:
        normalized = path.resolve(strict=False).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((path, source))
    return result


def _find_roslyn() -> tuple[Path | None, str]:
    required = ("csi.exe", "Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.CSharp.dll")
    for directory, source in _roslyn_candidates():
        if all((directory / name).is_file() for name in required):
            return directory.resolve(), source
    return None, ""


def _system_web_extensions() -> Path | None:
    candidates = (
        Path("/usr/lib/mono/4.5/System.Web.Extensions.dll"),
        Path("/usr/lib/mono/4.8-api/System.Web.Extensions.dll"),
        Path("/usr/lib/mono/4.7.2-api/System.Web.Extensions.dll"),
    )
    return next((path for path in candidates if path.is_file()), None)


def _text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == name and child.text:
            return child.text.strip()
    return ""


def _project_inputs(repo: Path, paths: tuple[str, ...]) -> list[dict[str, object]]:
    eligible = set(paths)
    assignments: dict[str, dict[str, object]] = {}
    projects: list[dict[str, object]] = []
    for project_path in sorted(repo.rglob("*.csproj")):
        if any(part in {"Library", "Temp", "Obj", "obj", "bin", "Build", "Builds"} for part in project_path.relative_to(repo).parts):
            continue
        try:
            root = ET.parse(project_path).getroot()
        except (OSError, ET.ParseError):
            continue
        compile_paths: list[str] = []
        references: list[str] = []
        defines = [value for value in _text(root, "DefineConstants").split(";") if value]
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "Compile":
                include = str(element.attrib.get("Include") or "").replace("\\", "/")
                candidate = (project_path.parent / include).resolve(strict=False)
                try:
                    relative = candidate.relative_to(repo.resolve()).as_posix()
                except ValueError:
                    continue
                if relative in eligible:
                    compile_paths.append(relative)
            elif tag == "HintPath" and element.text:
                candidate = Path(element.text.strip())
                if not candidate.is_absolute():
                    candidate = project_path.parent / candidate
                if candidate.is_file():
                    references.append(str(candidate.resolve()))
        if not compile_paths:
            continue
        project = {
            "key": project_path.relative_to(repo).as_posix(),
            "name": _text(root, "AssemblyName") or project_path.stem,
            "paths": [],
            "references": sorted(set(references)),
            "defines": sorted(set(defines)),
        }
        projects.append(project)
        for path in sorted(set(compile_paths)):
            assignments.setdefault(path, project)

    for path, project in assignments.items():
        cast_paths = project["paths"]
        if isinstance(cast_paths, list):
            cast_paths.append(path)
    unassigned = sorted(eligible - set(assignments))
    result = [project for project in projects if isinstance(project.get("paths"), list) and project["paths"]]
    if unassigned:
        result.append({"key": "<unassigned>", "name": "repoctl_unassigned", "paths": unassigned, "references": [], "defines": []})
    return result


def csharp_analysis_units(repo: Path, paths: set[str]) -> dict[str, str]:
    units: dict[str, str] = {}
    for project in _project_inputs(repo, tuple(sorted(paths))):
        key = str(project.get("key") or project.get("name") or "<unassigned>")
        for path in project.get("paths", []):
            units[str(path)] = key
    return units


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
                language="csharp",
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
                language="csharp",
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
        languages=("csharp",),
        symbol_failed_paths=paths,
        call_failed_paths=paths,
        failures=failures,
    )


def build_csharp_semantics(
    *,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    analysis_paths: set[str] | None = None,
) -> SemanticProviderResult:
    paths = tuple(sorted(entry.path for entry in entries if entry.language == "csharp"))
    if not paths:
        return SemanticProviderResult(provider=PROVIDER, languages=("csharp",))
    selected = tuple(sorted(set(paths) if analysis_paths is None else set(paths) & analysis_paths))
    if not selected:
        return SemanticProviderResult(provider=PROVIDER, languages=("csharp",))
    mono = shutil.which("mono")
    roslyn, roslyn_source = _find_roslyn()
    web_extensions = _system_web_extensions()
    if not mono or roslyn is None or web_extensions is None:
        return _unavailable(selected, code="csharp_provider_unavailable", message="Mono and the Roslyn compiler APIs are required")

    helper = Path(__file__).with_name("providers") / "csharp_semantics.csx"
    command = [
        mono,
        str(roslyn / "csi.exe"),
        f"/r:{roslyn / 'Microsoft.CodeAnalysis.dll'}",
        f"/r:{roslyn / 'Microsoft.CodeAnalysis.CSharp.dll'}",
        f"/r:{web_extensions}",
        str(helper),
    ]
    environment = dict(os.environ)
    environment["MONO_PATH"] = os.pathsep.join(filter(None, (str(roslyn), environment.get("MONO_PATH", ""))))
    try:
        completed = subprocess.run(
            command,
            input=json.dumps({"repo_root": str(target.root_path), "projects": _project_inputs(target.root_path, selected)}, separators=(",", ":")),
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
            cwd=target.root_path,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unavailable(selected, code="csharp_provider_failed", message=f"Roslyn provider failed: {exc}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        data = None
    if completed.returncode != 0 or not isinstance(data, dict) or data.get("ok") is not True:
        message = str(data.get("error") if isinstance(data, dict) else completed.stderr or "invalid provider output")
        return _unavailable(selected, code="csharp_provider_failed", message=f"Roslyn provider failed: {message}")

    eligible_paths = set(selected)
    analyzed = tuple(sorted({str(path) for path in data.get("analyzed_paths", []) if str(path) in eligible_paths}))
    failed = tuple(sorted(eligible_paths - set(analyzed)))
    symbols, symbol_ids = _symbols(data.get("symbols"), eligible_paths=eligible_paths)
    calls = _calls(data.get("calls"), eligible_paths=eligible_paths, symbol_ids=symbol_ids)
    failures: tuple[ProviderFailure, ...] = ()
    if failed:
        failures = (ProviderFailure(PROVIDER, "symbols,calls", "csharp_analysis_failed", "Roslyn analysis failed for one or more source files", failed),)
    return SemanticProviderResult(
        provider=PROVIDER,
        languages=("csharp",),
        symbols=tuple(symbols),
        calls=tuple(calls),
        symbol_analyzed_paths=analyzed,
        call_analyzed_paths=analyzed,
        symbol_failed_paths=failed,
        call_failed_paths=failed,
        failures=failures,
        tool={"kind": "roslyn_semantic_model", "source": roslyn_source},
    )
