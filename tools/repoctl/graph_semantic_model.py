from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceAnchor:
    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


@dataclass(frozen=True)
class PreciseSymbol:
    path: str
    provider: str
    provider_symbol_id: str
    language: str
    kind: str
    name: str
    qualified_name: str
    anchor: SourceAnchor

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "provider_symbol_id": self.provider_symbol_id,
            "language": self.language,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "anchor": self.anchor.to_dict(),
        }


@dataclass(frozen=True)
class PreciseCall:
    path: str
    provider: str
    caller_provider_symbol_id: str
    callee_provider_symbol_id: str
    language: str
    scope: str
    anchor: SourceAnchor

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "caller_provider_symbol_id": self.caller_provider_symbol_id,
            "callee_provider_symbol_id": self.callee_provider_symbol_id,
            "language": self.language,
            "scope": self.scope,
            "anchor": self.anchor.to_dict(),
        }


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    capability: str
    code: str
    message: str
    paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "provider": self.provider,
            "capability": self.capability,
            "code": self.code,
            "message": self.message,
        }
        if self.paths:
            data["paths"] = list(self.paths)
        return data


@dataclass(frozen=True)
class CapabilityEvidence:
    evidence_level: str = "precise"
    coverage_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_level": self.evidence_level,
            "coverage_gaps": list(self.coverage_gaps),
        }


@dataclass(frozen=True)
class SemanticProviderResult:
    provider: str
    languages: tuple[str, ...]
    symbols: tuple[PreciseSymbol, ...] = ()
    calls: tuple[PreciseCall, ...] = ()
    symbol_analyzed_paths: tuple[str, ...] = ()
    call_analyzed_paths: tuple[str, ...] = ()
    symbol_failed_paths: tuple[str, ...] = ()
    call_failed_paths: tuple[str, ...] = ()
    failures: tuple[ProviderFailure, ...] = ()
    symbol_coverage: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    call_coverage: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    tool: dict[str, object] = field(default_factory=dict)

    def to_meta(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "languages": list(self.languages),
            "symbol_analyzed_paths": list(self.symbol_analyzed_paths),
            "call_analyzed_paths": list(self.call_analyzed_paths),
            "symbol_failed_paths": list(self.symbol_failed_paths),
            "call_failed_paths": list(self.call_failed_paths),
            "symbol_coverage": self.symbol_coverage.to_dict(),
            "call_coverage": self.call_coverage.to_dict(),
            "symbol_count": len(self.symbols),
            "call_count": len(self.calls),
            "failures": [failure.to_dict() for failure in self.failures],
            "tool": self.tool,
        }
