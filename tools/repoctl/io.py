from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RepoctlError(RuntimeError):
    """Base error for repoctl user-facing failures."""

    def __init__(self, message: str, *, code: str = "repoctl_error", path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


LOCK_REL = Path("docs/tasks/.repoctl.lock.d")


def find_workspace_root() -> Path:
    """Resolve the workspace root from this module's location, never cwd git state."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (
            (candidate / "AGENTS.md").is_file()
            and (candidate / "docs/BOARD.md").is_file()
            and (candidate / "docs/tasks").is_dir()
        ):
            return candidate
    raise RepoctlError("workspace root not found from repoctl module path")


@contextmanager
def repoctl_lock(root: Path, *, timeout: float = 30.0, interval: float = 0.2) -> Iterator[None]:
    lock_dir = root / LOCK_REL
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            lock_dir.mkdir()
            acquired = True
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RepoctlError(f"could not acquire lock after {timeout:.0f}s: {LOCK_REL}", code="stale_lock", path=str(LOCK_REL))
            time.sleep(interval)
    try:
        yield
    finally:
        if acquired:
            try:
                lock_dir.rmdir()
            except FileNotFoundError:
                pass


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise
