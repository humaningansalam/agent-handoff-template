from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath


class PathRole(StrEnum):
    SOURCE = "source"
    TEST = "test"
    WORKFLOW = "workflow"


TEST_DIRECTORIES = {"test", "tests", "__tests__"}
TEST_SUFFIXES = (
    "_test.py",
    ".test.js",
    ".test.jsx",
    ".test.mjs",
    ".test.mts",
    ".test.ts",
    ".test.tsx",
    "_test.mjs",
    "_test.mts",
    "_test.dart",
)

WORKFLOW_DIRECTORIES = {(".github", "workflows"), ("docs", "workflows")}


def classify_path_role(path: str, *, repository_path: str = "") -> PathRole:
    normalized = path.replace("\\", "/").strip("/").casefold()
    parts = PurePosixPath(normalized).parts
    repository_parts = PurePosixPath(repository_path.replace("\\", "/").strip("/").casefold()).parts
    if repository_parts and tuple(parts[: len(repository_parts)]) == repository_parts:
        parts = parts[len(repository_parts) :]
    if tuple(parts[:2]) in WORKFLOW_DIRECTORIES:
        return PathRole.WORKFLOW
    name = parts[-1] if parts else ""
    if any(part in TEST_DIRECTORIES for part in parts[:-1]) or name.startswith("test_") or name.endswith(TEST_SUFFIXES):
        return PathRole.TEST
    return PathRole.SOURCE


def is_test_path(path: str, *, repository_path: str = "") -> bool:
    return classify_path_role(path, repository_path=repository_path) == PathRole.TEST
