from __future__ import annotations

from pathlib import Path

from .code_index import CodeIndexEntry
from .graph_code_provider import (
    PYTHON_PROVIDER_INPUT_VERSION,
    PYTHON_PROVIDER_LANGUAGES,
    PythonCallableExport,
    build_python_semantics,
)
from .graph_import_resolver import ImportResolution
from .graph_semantic_model import CapabilityEvidence, ProviderFailure, SemanticProviderResult
from .repositories import RepoTarget


SEMANTIC_PROVIDER_LANGUAGES = frozenset({"python", "javascript", "typescript", "dart", "csharp"})
PROVIDER_LANGUAGES = {
    "python_ast": frozenset({"python"}),
    "typescript_compiler": frozenset({"javascript", "typescript"}),
    "dart_analyzer": frozenset({"dart"}),
    "csharp_roslyn": frozenset({"csharp"}),
}
PROVIDER_INPUT_VERSIONS = {
    "python_ast": PYTHON_PROVIDER_INPUT_VERSION,
    "typescript_compiler": 2,
    "dart_analyzer": 3,
    "csharp_roslyn": 2,
}


def _python_result(
    root: Path,
    *,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    import_resolutions: list[ImportResolution],
    analysis_paths: set[str] | None = None,
    previous: SemanticProviderResult | None = None,
) -> SemanticProviderResult:
    symbols, calls, meta = build_python_semantics(
        root,
        target=target,
        entries=entries,
        import_resolutions=import_resolutions,
        analysis_paths=analysis_paths,
        known_symbols=previous.symbols if previous is not None else (),
        known_exported_callables=tuple(
            PythonCallableExport(
                path=str(value.get("path") or ""),
                name=str(value.get("name") or ""),
                provider_symbol_id=str(value.get("provider_symbol_id") or ""),
            )
            for value in (previous.tool.get("exported_callables", []) if previous is not None else [])
            if isinstance(value, dict)
            and str(value.get("path") or "")
            and str(value.get("name") or "")
            and str(value.get("provider_symbol_id") or "")
        ),
    )
    analyzed_paths = tuple(sorted(str(path) for path in meta.get("analyzed_paths", [])))
    failed_paths = tuple(sorted(str(path) for path in meta.get("failed_paths", [])))
    failures: tuple[ProviderFailure, ...] = ()
    if failed_paths:
        failures = (
            ProviderFailure(
                provider="python_ast",
                capability="symbols,calls",
                code="python_analysis_failed",
                message="Python AST analysis failed for one or more source files",
                paths=failed_paths,
            ),
        )
    return SemanticProviderResult(
        provider="python_ast",
        languages=tuple(sorted(PYTHON_PROVIDER_LANGUAGES)),
        symbols=tuple(symbols),
        calls=tuple(calls),
        symbol_analyzed_paths=analyzed_paths,
        call_analyzed_paths=analyzed_paths,
        symbol_failed_paths=failed_paths,
        call_failed_paths=failed_paths,
        failures=failures,
        call_coverage=CapabilityEvidence(
            evidence_level="conservative",
            coverage_gaps=("python_dynamic_call_targets_are_not_exhaustive",),
        ),
        tool={
            "kind": "python_stdlib_ast",
            "exported_callables": list(meta.get("exported_callables", [])),
        },
    )


def build_semantic_provider(
    provider: str,
    root: Path,
    *,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    import_resolutions: list[ImportResolution],
    analysis_paths: set[str] | None = None,
    previous: SemanticProviderResult | None = None,
) -> SemanticProviderResult:
    if provider == "python_ast":
        return _python_result(
            root,
            target=target,
            entries=entries,
            import_resolutions=import_resolutions,
            analysis_paths=analysis_paths,
            previous=previous,
        )

    # External providers stay lazy so normal repoctl commands do not import
    # toolchain-specific modules.
    if provider == "typescript_compiler":
        from .graph_typescript_provider import build_typescript_semantics

        return build_typescript_semantics(root=root, target=target, entries=entries, analysis_paths=analysis_paths)
    if provider == "dart_analyzer":
        from .graph_dart_provider import build_dart_semantics

        return build_dart_semantics(root=root, target=target, entries=entries, analysis_paths=analysis_paths)
    if provider == "csharp_roslyn":
        from .graph_csharp_provider import build_csharp_semantics

        return build_csharp_semantics(target=target, entries=entries, analysis_paths=analysis_paths)
    raise ValueError(f"unknown semantic provider: {provider}")


def build_semantic_providers(
    root: Path,
    *,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    import_resolutions: list[ImportResolution],
    cached_results: list[SemanticProviderResult] | None = None,
) -> list[SemanticProviderResult]:
    cached = {result.provider: result for result in cached_results or []}
    return [
        cached.get(provider)
        or build_semantic_provider(
            provider,
            root,
            target=target,
            entries=entries,
            import_resolutions=import_resolutions,
        )
        for provider in PROVIDER_LANGUAGES
    ]
