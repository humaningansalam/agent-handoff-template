from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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


class RpcRoutineStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class RpcParamsStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class RpcRoutineReasonCode(StrEnum):
    FORMAL_UNAVAILABLE = "routine_formal_unavailable"
    ARGUMENT_MISSING = "routine_argument_missing"
    NOT_STATIC_STRING = "routine_not_static_string"


class RpcParamsReasonCode(StrEnum):
    FORMAL_UNAVAILABLE = "params_formal_unavailable"
    ARGUMENT_AMBIGUOUS = "params_argument_ambiguous"
    NOT_MAP_LITERAL = "params_not_map_literal"
    MAP_NOT_FULLY_STATIC = "params_map_not_fully_static"


class RpcInvocationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class RpcInvocationReasonCode(StrEnum):
    UNEXPECTED_ARGUMENT = "unexpected_argument"
    MISSING_REQUIRED_ARGUMENT = "missing_required_argument"
    DUPLICATE_ARGUMENT = "duplicate_argument"
    ARGUMENT_CONTRACT_MISMATCH = "argument_contract_mismatch"


class RpcSchemaStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class RpcSchemaReasonCode(StrEnum):
    SCHEMA_NOT_OBSERVED = "schema_not_observed"


@dataclass(frozen=True)
class RpcSchemaSelection:
    status: RpcSchemaStatus
    schema: str = ""
    reason_code: RpcSchemaReasonCode | None = None

    def __post_init__(self) -> None:
        if self.status is RpcSchemaStatus.KNOWN:
            if not self.schema or self.reason_code is not None:
                raise ValueError("known RPC schema selection requires a schema and no reason")
            return
        if self.schema or self.reason_code is not RpcSchemaReasonCode.SCHEMA_NOT_OBSERVED:
            raise ValueError("unknown RPC schema selection requires schema_not_observed")

    @classmethod
    def from_dict(cls, data: object) -> "RpcSchemaSelection | None":
        if not isinstance(data, dict):
            return None
        try:
            status = RpcSchemaStatus(str(data.get("status") or ""))
            reason_value = str(data.get("reason_code") or "")
            reason = RpcSchemaReasonCode(reason_value) if reason_value else None
            return cls(status=status, schema=str(data.get("schema") or ""), reason_code=reason)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, str]:
        data = {"status": self.status.value}
        if self.status is RpcSchemaStatus.KNOWN:
            data["schema"] = self.schema
        elif self.reason_code is not None:
            data["reason_code"] = self.reason_code.value
        return data


@dataclass(frozen=True)
class RpcInvocationContract:
    status: RpcInvocationStatus
    reason_code: RpcInvocationReasonCode | None = None
    unmatched_argument_count: int = 0
    missing_required_parameter_names: tuple[str, ...] = ()
    duplicate_parameter_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.unmatched_argument_count < 0:
            raise ValueError("unmatched argument count must not be negative")
        if tuple(sorted(set(self.missing_required_parameter_names))) != self.missing_required_parameter_names:
            raise ValueError("missing required parameter names must be sorted and unique")
        if tuple(sorted(set(self.duplicate_parameter_names))) != self.duplicate_parameter_names:
            raise ValueError("duplicate parameter names must be sorted and unique")
        defect_kinds = sum(
            (
                self.unmatched_argument_count > 0,
                bool(self.missing_required_parameter_names),
                bool(self.duplicate_parameter_names),
            )
        )
        if self.status is RpcInvocationStatus.VALID:
            if defect_kinds or self.reason_code is not None:
                raise ValueError("valid invocation contract must not contain defects")
            return
        if defect_kinds == 0:
            raise ValueError("invalid invocation contract requires a structured defect")
        if defect_kinds > 1:
            expected_reason = RpcInvocationReasonCode.ARGUMENT_CONTRACT_MISMATCH
        elif self.unmatched_argument_count:
            expected_reason = RpcInvocationReasonCode.UNEXPECTED_ARGUMENT
        elif self.missing_required_parameter_names:
            expected_reason = RpcInvocationReasonCode.MISSING_REQUIRED_ARGUMENT
        else:
            expected_reason = RpcInvocationReasonCode.DUPLICATE_ARGUMENT
        if self.reason_code is not expected_reason:
            raise ValueError("invocation contract reason does not match its structured defects")

    @classmethod
    def from_dict(cls, data: object) -> "RpcInvocationContract | None":
        if not isinstance(data, dict):
            return None
        raw_missing = data.get("missing_required_parameter_names")
        raw_duplicates = data.get("duplicate_parameter_names")
        if (
            not isinstance(raw_missing, list)
            or any(not isinstance(value, str) for value in raw_missing)
            or not isinstance(raw_duplicates, list)
            or any(not isinstance(value, str) for value in raw_duplicates)
        ):
            return None
        try:
            status = RpcInvocationStatus(str(data.get("status") or ""))
            reason_value = str(data.get("reason_code") or "")
            reason = RpcInvocationReasonCode(reason_value) if reason_value else None
            return cls(
                status=status,
                reason_code=reason,
                unmatched_argument_count=int(data["unmatched_argument_count"]),
                missing_required_parameter_names=tuple(raw_missing),
                duplicate_parameter_names=tuple(raw_duplicates),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status.value,
            "unmatched_argument_count": self.unmatched_argument_count,
            "missing_required_parameter_names": list(self.missing_required_parameter_names),
            "duplicate_parameter_names": list(self.duplicate_parameter_names),
        }
        if self.reason_code is not None:
            data["reason_code"] = self.reason_code.value
        return data


@dataclass(frozen=True)
class RpcInvocationFact:
    fact_id: str
    repository_id: str
    path: str
    provider: str
    language: str
    content_sha256: str
    start_offset: int
    end_offset: int
    resolved_callee_identity: str
    receiver_type: str
    invocation: RpcInvocationContract
    schema_selection: RpcSchemaSelection
    routine_status: RpcRoutineStatus
    routine: str
    routine_reason_code: RpcRoutineReasonCode | None
    params_status: RpcParamsStatus
    param_names: tuple[str, ...]
    params_reason_code: RpcParamsReasonCode | None
    syntactic_argument_count: int
    anchor: SourceAnchor

    def __post_init__(self) -> None:
        if tuple(sorted(set(self.param_names))) != self.param_names:
            raise ValueError("RPC parameter names must be sorted and unique")
        if self.routine_status is RpcRoutineStatus.KNOWN:
            if self.routine_reason_code is not None:
                raise ValueError("known RPC routine evidence must not contain a reason")
        elif self.routine or self.routine_reason_code is None:
            raise ValueError("unknown RPC routine evidence requires a reason and no value")
        allowed_params_reasons = {
            RpcParamsStatus.COMPLETE: {None},
            RpcParamsStatus.PARTIAL: {RpcParamsReasonCode.MAP_NOT_FULLY_STATIC},
            RpcParamsStatus.UNKNOWN: {
                RpcParamsReasonCode.FORMAL_UNAVAILABLE,
                RpcParamsReasonCode.ARGUMENT_AMBIGUOUS,
                RpcParamsReasonCode.NOT_MAP_LITERAL,
            },
        }
        if self.params_reason_code not in allowed_params_reasons[self.params_status]:
            raise ValueError("RPC parameter evidence status and reason are inconsistent")
        if self.params_status is RpcParamsStatus.UNKNOWN and self.param_names:
            raise ValueError("unknown RPC parameter evidence must not contain known names")

    def to_dict(self) -> dict[str, object]:
        routine: dict[str, object] = {"status": self.routine_status.value}
        if self.routine_status == RpcRoutineStatus.KNOWN:
            routine["value"] = self.routine
        elif self.routine_reason_code is not None:
            routine["reason_code"] = self.routine_reason_code.value
        params: dict[str, object] = {
            "status": self.params_status.value,
            "known_names": list(self.param_names),
        }
        if self.params_reason_code is not None:
            params["reason_code"] = self.params_reason_code.value
        return {
            "fact_id": self.fact_id,
            "repository_id": self.repository_id,
            "path": self.path,
            "provider": self.provider,
            "language": self.language,
            "content_sha256": self.content_sha256,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "resolved_callee_identity": self.resolved_callee_identity,
            "receiver_type": self.receiver_type,
            "invocation": self.invocation.to_dict(),
            "schema_selection": self.schema_selection.to_dict(),
            "routine": routine,
            "params": params,
            "syntactic_argument_count": self.syntactic_argument_count,
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
    rpc_invocations: tuple[RpcInvocationFact, ...] = ()
    symbol_analyzed_paths: tuple[str, ...] = ()
    call_analyzed_paths: tuple[str, ...] = ()
    rpc_analyzed_paths: tuple[str, ...] = ()
    symbol_failed_paths: tuple[str, ...] = ()
    call_failed_paths: tuple[str, ...] = ()
    rpc_failed_paths: tuple[str, ...] = ()
    failures: tuple[ProviderFailure, ...] = ()
    symbol_coverage: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    call_coverage: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    rpc_coverage: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    tool: dict[str, object] = field(default_factory=dict)

    def to_meta(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "languages": list(self.languages),
            "symbol_analyzed_paths": list(self.symbol_analyzed_paths),
            "call_analyzed_paths": list(self.call_analyzed_paths),
            "rpc_analyzed_paths": list(self.rpc_analyzed_paths),
            "symbol_failed_paths": list(self.symbol_failed_paths),
            "call_failed_paths": list(self.call_failed_paths),
            "rpc_failed_paths": list(self.rpc_failed_paths),
            "symbol_coverage": self.symbol_coverage.to_dict(),
            "call_coverage": self.call_coverage.to_dict(),
            "rpc_coverage": self.rpc_coverage.to_dict(),
            "symbol_count": len(self.symbols),
            "call_count": len(self.calls),
            "rpc_invocation_count": len(self.rpc_invocations),
            "failures": [failure.to_dict() for failure in self.failures],
            "tool": self.tool,
        }
