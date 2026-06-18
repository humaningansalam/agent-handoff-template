from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

UNSAFE_PARSE_ERROR = "UNSAFE_PARSE_ERROR"


def relativize_tool_path(root: Path, value: object) -> str:
    raw = str(value or "")
    if not raw:
        return raw
    path = Path(raw)
    if not path.is_absolute():
        return raw
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return raw


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return [UNSAFE_PARSE_ERROR]


def strip_cd_prefix(parts: list[str]) -> list[str]:
    if len(parts) >= 3 and parts[0] == "cd" and parts[2] == "&&":
        return parts[3:]
    return parts


def normalize_tool_input(root: Path, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tool_input)
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        for key in ("file_path", "path"):
            if key in normalized:
                normalized[key] = relativize_tool_path(root, normalized[key])
    if tool_name == "Bash" and isinstance(normalized.get("command"), str):
        parts = strip_cd_prefix(split_command(normalized["command"]))
        if parts == [UNSAFE_PARSE_ERROR]:
            normalized["command"] = UNSAFE_PARSE_ERROR
            return normalized
        normalized["command"] = " ".join(shlex.quote(part) for part in parts)
    return normalized


def split_bash_command(tool_input: dict[str, Any]) -> list[str]:
    command = str(tool_input.get("command") or "")
    if not command:
        return [UNSAFE_PARSE_ERROR]
    return strip_cd_prefix(split_command(command))
