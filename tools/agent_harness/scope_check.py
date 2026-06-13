from __future__ import annotations

from fnmatch import fnmatch


def changed_files_within_scope(changed_files: list[str] | tuple[str, ...], approved_surfaces: list[str] | tuple[str, ...]) -> bool:
    if not changed_files or not approved_surfaces:
        return False
    return all(any(path == surface or fnmatch(path, surface) for surface in approved_surfaces) for path in changed_files)
