from __future__ import annotations

from dataclasses import dataclass

"""Canonical agent tool and permission boundaries."""


@dataclass(frozen=True)
class AgentSpec:
    """Canonical maintenance worker tool and permission contract."""
    name: str
    kind: str
    tools: tuple[str, ...]
    permission_mode: str = "default"


READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
READ_ONLY_EVALUATION_TOOLS = ("Read", "Grep", "Glob", "Bash")
IMPLEMENTER_TOOLS = ("Read", "Grep", "Glob", "Edit", "MultiEdit", "Write")

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="maintenance-cartographer",
        kind="maintenance-worker",
        tools=READ_ONLY_TOOLS,
        permission_mode="plan",
    ),
    AgentSpec(
        name="maintenance-planner",
        kind="maintenance-worker",
        tools=READ_ONLY_TOOLS,
        permission_mode="plan",
    ),
    AgentSpec(
        name="maintenance-plan-critic",
        kind="maintenance-worker",
        tools=READ_ONLY_TOOLS,
        permission_mode="plan",
    ),
    AgentSpec(
        name="maintenance-implementer",
        kind="maintenance-worker",
        tools=IMPLEMENTER_TOOLS,
        permission_mode="default",
    ),
    AgentSpec(
        name="maintenance-evaluator",
        kind="maintenance-worker",
        tools=READ_ONLY_EVALUATION_TOOLS,
        permission_mode="plan",
    ),
    AgentSpec(
        name="maintenance-skeptic",
        kind="maintenance-worker",
        tools=READ_ONLY_EVALUATION_TOOLS,
        permission_mode="plan",
    ),
)

AGENTS_BY_NAME: dict[str, AgentSpec] = {agent.name: agent for agent in AGENTS}
