from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


@contextmanager
def reject_directory_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    *directories: Path,
    allow_reads: Callable[[Path], bool] | None = None,
) -> Iterator[list[Path]]:
    """Reject every common cold-directory scan and record exact file reads."""

    roots = tuple(path.resolve(strict=False) for path in directories)
    reads: list[Path] = []
    original_glob = Path.glob
    original_rglob = Path.rglob
    original_iterdir = Path.iterdir
    original_scandir = os.scandir
    original_open = Path.open

    def under_cold_root(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(resolved == root or root in resolved.parents for root in roots)

    def reject_scan(path: Path, operation: str) -> None:
        if under_cold_root(path):
            raise AssertionError(f"cold directory enumeration via {operation}: {path}")

    def guarded_glob(path: Path, pattern: str):
        reject_scan(path, "glob")
        return original_glob(path, pattern)

    def guarded_rglob(path: Path, pattern: str):
        reject_scan(path, "rglob")
        return original_rglob(path, pattern)

    def guarded_iterdir(path: Path):
        reject_scan(path, "iterdir")
        return original_iterdir(path)

    def guarded_scandir(path: Any = "."):
        candidate = Path(path)
        reject_scan(candidate, "scandir")
        return original_scandir(path)

    def check_read(path: Path) -> None:
        if not under_cold_root(path):
            return
        resolved = path.resolve(strict=False)
        reads.append(resolved)
        if allow_reads is None or not allow_reads(resolved):
            raise AssertionError(f"unexpected cold artifact read: {path}")

    def guarded_open(path: Path, *args: Any, **kwargs: Any):
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if "r" in mode or "+" in mode:
            check_read(path)
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "glob", guarded_glob)
        scoped.setattr(Path, "rglob", guarded_rglob)
        scoped.setattr(Path, "iterdir", guarded_iterdir)
        scoped.setattr(os, "scandir", guarded_scandir)
        scoped.setattr(Path, "open", guarded_open)
        yield reads
