from __future__ import annotations

from typing import Any


def validate_state_checkpoint(state: dict[str, Any]) -> bool:
    from tools.agent_harness.harness import MaintenanceHarness

    MaintenanceHarness.validate_state_checkpoint(state)
    return True
