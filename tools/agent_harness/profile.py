from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowProfile:
    name: str
    trigger: str
    artifacts_root: str
    denied_surfaces: tuple[str, ...] = ()


MAINTENANCE_PROFILE = WorkflowProfile(
    name="maintenance",
    trigger="/maintenance-workflow",
    artifacts_root="ops/agent-harness",
    denied_surfaces=("projects/**", "live external mutation"),
)
