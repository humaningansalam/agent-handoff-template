from __future__ import annotations

from pathlib import Path
from typing import Any


def final_report_block_reason(root: Path, payload: dict[str, Any]) -> str | None:
    """Reserved for non-maintenance final-report gates.

    The target template currently uses maintenance-specific final report enforcement in
    tools.hooks.maintenance.enforce_final_report.
    """
    return None
