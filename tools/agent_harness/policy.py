from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatch

from tools.agent_harness import paths as harness_paths


class SurfaceClass(str, Enum):
    LOW_RISK_PROSE = "low_risk_prose"
    INSTRUCTION_DOC = "instruction_doc"
    CONTRACT_DOC = "contract_doc"
    CRITICAL_HARNESS_DOC = "critical_harness_doc"
    HARNESS_CODE = "harness_code"
    HOOK_OR_PERMISSION = "hook_or_permission"
    HARNESS_TEST = "harness_test"
    NORMAL_CODE = "normal_code"
    FORBIDDEN_SURFACE = "forbidden_surface"


class WorkflowProfile(str, Enum):
    TINY_DOC = "TINY_DOC"
    STANDARD = "STANDARD"
    CRITICAL_HARNESS = "CRITICAL_HARNESS"


class VerificationMode(str, Enum):
    SEMANTIC = "semantic"
    MECHANICAL = "mechanical"


APPROVAL_HASH_PREFIX_LENGTH = 12


@dataclass(frozen=True)
class PolicyDecision:
    surfaces: tuple[str, ...]
    surface_classes: tuple[SurfaceClass, ...]
    profile: WorkflowProfile
    route: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    required_workers: tuple[str, ...]
    approval_required: bool
    host_verifier_allowed: bool
    verification_mode: VerificationMode
    reapproval_triggers: tuple[str, ...]
    reason: str


CRITICAL_HARNESS_PATTERNS = (
    ".claude/skills/maintenance-workflow/**",
    ".claude/skills/maintenance-workflow/SKILL.md",
    ".claude/agents/maintenance-*.md",
    ".claude/hooks/**",
    ".claude/settings.json",
    "tools/hooks/maintenance/**",
    "tools/agent_harness/**",
    "tools/registries/agent_registry.py",
    "tools/runtime/permission_policy.py",
    "tests/maintenance/**",
    "docs/MAINTENANCE_HARNESS_CONTRACT.md",
)

HOOK_OR_PERMISSION_PATTERNS = (
    ".claude/hooks/**",
    ".claude/settings.json",
    "tools/hooks/maintenance/**",
    "tools/runtime/permission_policy.py",
)

HARNESS_CODE_PATTERNS = (
    "tools/agent_harness/**",
    "tools/registries/agent_registry.py",
)

HARNESS_TEST_PATTERNS = ("tests/maintenance/**",)

CRITICAL_HARNESS_DOC_PATTERNS = (
    ".claude/skills/maintenance-workflow/**",
    ".claude/skills/maintenance-workflow/SKILL.md",
    ".claude/agents/maintenance-*.md",
    "docs/MAINTENANCE_HARNESS_CONTRACT.md",
)

INSTRUCTION_DOC_PATTERNS = (
    "CLAUDE.md",
    ".claude/rules/**",
    ".claude/skills/**",
    ".claude/agents/**",
)

CONTRACT_DOC_PATTERNS = (
    "docs/PRD.md",
    "docs/OPERATIONS_CONTRACT.md",
)

LOW_RISK_PROSE_PATTERNS = (
    "README.md",
    "templates/*.md",
    "templates/**/*.md",
    "templates/*.txt",
    "templates/**/*.txt",
    "docs/*.md",
    "docs/**/*.md",
    "docs/*.txt",
    "docs/**/*.txt",
)

FORBIDDEN_SURFACE_PATTERNS = (
    "repos/**",
)

REAPPROVAL_TRIGGERS = (
    "affected_surfaces_changed",
    "acceptance_criteria_identity_changed",
    "surface_class_changed",
    "profile_changed",
    "route_changed",
    "verification_mode_changed",
    "permission_semantics_changed",
)


def policy_for_surfaces(
    surfaces: tuple[str, ...] | list[str],
    *,
    severity: str = "P3",
    ambiguity: bool = False,
    verification_mode: VerificationMode | str = VerificationMode.SEMANTIC,
) -> PolicyDecision:
    normalized_surfaces = tuple(_normalize_surface(surface) for surface in surfaces if str(surface).strip())
    surface_classes = tuple(classify_surface(surface) for surface in normalized_surfaces)
    requested_mode = _normalize_verification_mode(verification_mode)
    if any(surface_class == SurfaceClass.FORBIDDEN_SURFACE for surface_class in surface_classes):
        mode = requested_mode
        return PolicyDecision(
            surfaces=normalized_surfaces,
            surface_classes=surface_classes,
            profile=WorkflowProfile.STANDARD,
            route=(),
            required_artifacts=("ops/agent-harness/current-run-state.json",),
            required_workers=(),
            approval_required=False,
            host_verifier_allowed=False,
            verification_mode=mode,
            reapproval_triggers=REAPPROVAL_TRIGGERS,
            reason=reason_for_policy(WorkflowProfile.STANDARD, surface_classes, severity=severity, ambiguity=ambiguity, verification_mode=mode) + "; forbidden surface",
        )
    profile = profile_for_surface_classes(surface_classes, severity=severity)
    mode = requested_mode
    if mode == VerificationMode.MECHANICAL and not mechanical_verification_allowed(normalized_surfaces, surface_classes):
        mode = VerificationMode.SEMANTIC
    route = route_for_profile(profile, ambiguity=ambiguity, verification_mode=mode)
    return PolicyDecision(
        surfaces=normalized_surfaces,
        surface_classes=surface_classes,
        profile=profile,
        route=route,
        required_artifacts=required_artifacts_for_route(route, host_verifier_allowed=profile == WorkflowProfile.TINY_DOC),
        required_workers=required_workers_for_route(route),
        approval_required=True,
        host_verifier_allowed=profile == WorkflowProfile.TINY_DOC,
        verification_mode=mode,
        reapproval_triggers=REAPPROVAL_TRIGGERS,
        reason=reason_for_policy(profile, surface_classes, severity=severity, ambiguity=ambiguity, verification_mode=mode),
    )


def plan_contract_payload(
    *,
    candidate_id: str,
    affected_surfaces: tuple[str, ...] | list[str],
    acceptance_criteria_ids: tuple[str, ...] | list[str],
    severity: str = "P3",
    ambiguity: bool = False,
    verification_mode: VerificationMode | str = VerificationMode.SEMANTIC,
) -> dict[str, object]:
    policy = policy_for_surfaces(affected_surfaces, severity=severity, ambiguity=ambiguity, verification_mode=verification_mode)
    return {
        "candidate_id": candidate_id.strip(),
        "affected_surfaces": list(policy.surfaces),
        "acceptance_criteria_ids": _normalize_unique(acceptance_criteria_ids),
        "surface_classes": [surface_class.value for surface_class in policy.surface_classes],
        "profile": policy.profile.value,
        "route": list(policy.route),
        "verification_mode": policy.verification_mode.value,
        "reapproval_triggers": list(policy.reapproval_triggers),
    }


def plan_contract_hash(
    *,
    candidate_id: str,
    affected_surfaces: tuple[str, ...] | list[str],
    acceptance_criteria_ids: tuple[str, ...] | list[str],
    severity: str = "P3",
    ambiguity: bool = False,
    verification_mode: VerificationMode | str = VerificationMode.SEMANTIC,
) -> str:
    payload = plan_contract_payload(
        candidate_id=candidate_id,
        affected_surfaces=affected_surfaces,
        acceptance_criteria_ids=acceptance_criteria_ids,
        severity=severity,
        ambiguity=ambiguity,
        verification_mode=verification_mode,
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approval_phrase(candidate_id: str, contract_hash: str) -> str:
    return f"승인: {candidate_id} {contract_hash[:APPROVAL_HASH_PREFIX_LENGTH]}"


def classify_surface(surface: str) -> SurfaceClass:
    path = _normalize_surface(surface)
    if _matches_any(path, FORBIDDEN_SURFACE_PATTERNS):
        return SurfaceClass.FORBIDDEN_SURFACE
    if _matches_any(path, HOOK_OR_PERMISSION_PATTERNS):
        return SurfaceClass.HOOK_OR_PERMISSION
    if _matches_any(path, HARNESS_CODE_PATTERNS):
        return SurfaceClass.HARNESS_CODE
    if _matches_any(path, HARNESS_TEST_PATTERNS):
        return SurfaceClass.HARNESS_TEST
    if _matches_any(path, CRITICAL_HARNESS_DOC_PATTERNS):
        return SurfaceClass.CRITICAL_HARNESS_DOC
    if _matches_any(path, CONTRACT_DOC_PATTERNS):
        return SurfaceClass.CONTRACT_DOC
    if _matches_any(path, INSTRUCTION_DOC_PATTERNS):
        return SurfaceClass.INSTRUCTION_DOC
    if _matches_any(path, LOW_RISK_PROSE_PATTERNS):
        return SurfaceClass.LOW_RISK_PROSE
    return SurfaceClass.NORMAL_CODE


def mechanical_verification_allowed(surfaces: tuple[str, ...], surface_classes: tuple[SurfaceClass, ...]) -> bool:
    return surface_classes == (SurfaceClass.LOW_RISK_PROSE,) or surfaces == ("docs/MAINTENANCE_HARNESS_CONTRACT.md",)


def profile_for_surface_classes(surface_classes: tuple[SurfaceClass, ...], *, severity: str = "P3") -> WorkflowProfile:
    normalized_severity = severity.strip().upper() or "P3"
    if normalized_severity in {"P0", "P1"}:
        return WorkflowProfile.CRITICAL_HARNESS
    if any(surface_class in _CRITICAL_CLASSES for surface_class in surface_classes):
        return WorkflowProfile.CRITICAL_HARNESS
    if any(surface_class in _STANDARD_CLASSES for surface_class in surface_classes):
        return WorkflowProfile.STANDARD
    if surface_classes and all(surface_class == SurfaceClass.LOW_RISK_PROSE for surface_class in surface_classes):
        return WorkflowProfile.TINY_DOC
    return WorkflowProfile.STANDARD


def route_for_profile(
    profile: WorkflowProfile | str,
    *,
    ambiguity: bool = False,
    verification_mode: VerificationMode | str = VerificationMode.SEMANTIC,
) -> tuple[str, ...]:
    normalized_profile = profile if isinstance(profile, WorkflowProfile) else WorkflowProfile(str(profile))
    mode = _normalize_verification_mode(verification_mode)
    if normalized_profile == WorkflowProfile.TINY_DOC:
        return ("maintenance-planner", "maintenance-implementer", "host-verifier")
    if normalized_profile == WorkflowProfile.CRITICAL_HARNESS:
        if mode == VerificationMode.MECHANICAL:
            return (
                "maintenance-cartographer",
                "maintenance-planner",
                "maintenance-plan-critic",
                "maintenance-implementer",
                "maintenance-evaluator",
            )
        return (
            "maintenance-cartographer",
            "maintenance-planner",
            "maintenance-plan-critic",
            "maintenance-implementer",
            "maintenance-evaluator",
            "maintenance-skeptic",
        )
    if ambiguity:
        return (
            "maintenance-cartographer",
            "maintenance-planner",
            "maintenance-plan-critic",
            "maintenance-implementer",
            "maintenance-evaluator",
        )
    return (
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
    )


def required_workers_for_route(route: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(worker for worker in route if worker != "host-verifier")


def required_artifacts_for_route(route: tuple[str, ...], *, host_verifier_allowed: bool = False) -> tuple[str, ...]:
    artifact = harness_paths.ARTIFACT_ROOT / "evidence"
    paths = ["ops/agent-harness/current-run-state.json"]
    worker_artifacts = {
        "maintenance-cartographer": str(artifact / "cartography.json"),
        "maintenance-planner": str(artifact / "plan.json"),
        "maintenance-plan-critic": str(artifact / "plan-review.json"),
        "maintenance-implementer": str(artifact / "execution.json"),
        "maintenance-evaluator": str(artifact / "execution-review.json"),
        "maintenance-skeptic": str(artifact / "skeptic-review.json"),
        "host-verifier": str(artifact / "execution-review.json"),
    }
    for worker in route:
        path = worker_artifacts[worker]
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def reason_for_policy(
    profile: WorkflowProfile,
    surface_classes: tuple[SurfaceClass, ...],
    *,
    severity: str,
    ambiguity: bool,
    verification_mode: VerificationMode,
) -> str:
    classes = ",".join(surface_class.value for surface_class in surface_classes) or "unknown"
    return (
        f"profile={profile.value}; severity={severity.strip().upper() or 'P3'}; "
        f"ambiguity={ambiguity}; verification_mode={verification_mode.value}; surface_classes={classes}"
    )


def _normalize_verification_mode(mode: VerificationMode | str) -> VerificationMode:
    if isinstance(mode, VerificationMode):
        return mode
    return VerificationMode(str(mode).strip() or VerificationMode.SEMANTIC.value)


def _normalize_surface(surface: str) -> str:
    return str(surface).strip().lstrip("./")


def _normalize_unique(values: tuple[str, ...] | list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern.strip().lstrip("./")) for pattern in patterns)


_CRITICAL_CLASSES = {
    SurfaceClass.CRITICAL_HARNESS_DOC,
    SurfaceClass.HARNESS_CODE,
    SurfaceClass.HOOK_OR_PERMISSION,
    SurfaceClass.HARNESS_TEST,
    SurfaceClass.FORBIDDEN_SURFACE,
}

_STANDARD_CLASSES = {
    SurfaceClass.INSTRUCTION_DOC,
    SurfaceClass.CONTRACT_DOC,
    SurfaceClass.NORMAL_CODE,
}
