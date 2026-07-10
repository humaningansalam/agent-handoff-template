from __future__ import annotations

from dataclasses import dataclass

"""Generated from ai/roles/*.yaml. Do not edit directly."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: str
    tools: tuple[str, ...]
    permission_mode: str = "default"


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name='maintenance-cartographer',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob'),
        permission_mode='plan',
    ),
    AgentSpec(
        name='maintenance-evaluator',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob', 'Bash'),
        permission_mode='plan',
    ),
    AgentSpec(
        name='maintenance-implementer',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob', 'Edit', 'MultiEdit', 'Write'),
        permission_mode='default',
    ),
    AgentSpec(
        name='maintenance-plan-critic',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob'),
        permission_mode='plan',
    ),
    AgentSpec(
        name='maintenance-planner',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob'),
        permission_mode='plan',
    ),
    AgentSpec(
        name='maintenance-skeptic',
        kind='maintenance-worker',
        tools=('Read', 'Grep', 'Glob', 'Bash'),
        permission_mode='plan',
    ),
)

AGENTS_BY_NAME: dict[str, AgentSpec] = {agent.name: agent for agent in AGENTS}
