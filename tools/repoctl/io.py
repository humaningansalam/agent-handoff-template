from __future__ import annotations

import json
import os
import socket
import time
import uuid
from collections.abc import Collection
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class RepoctlError(RuntimeError):
    """Base error for repoctl user-facing failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "repoctl_error",
        path: str | None = None,
        cause_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.cause_code = cause_code


LOCK_REL = Path("docs/tasks/.repoctl.lock.d")
LOCK_OWNER = "owner.json"


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
            _write_lock_owner(lock_dir)
            acquired = True
            break
        except FileExistsError:
            _recover_dead_lock(lock_dir)
            if time.monotonic() >= deadline:
                raise RepoctlError(f"could not acquire lock after {timeout:.0f}s: {LOCK_REL}", code="stale_lock", path=str(LOCK_REL))
            time.sleep(interval)
    try:
        yield
    finally:
        if acquired:
            try:
                owner = lock_dir / LOCK_OWNER
                if owner.exists() and owner.is_file():
                    owner.unlink()
                lock_dir.rmdir()
            except FileNotFoundError:
                pass


def _write_lock_owner(lock_dir: Path) -> None:
    payload = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (lock_dir / LOCK_OWNER).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _recover_dead_lock(lock_dir: Path) -> None:
    owner_path = lock_dir / LOCK_OWNER
    try:
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(owner, dict):
        return
    if str(owner.get("hostname") or "") != socket.gethostname():
        return
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return
    if _pid_alive(pid):
        return
    try:
        owner_path.unlink()
        lock_dir.rmdir()
    except OSError:
        return


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    _reject_symlinked_parent_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = write_temporary_text(path, text)
    try:
        os.replace(tmp, path)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise


def _reject_symlinked_parent_chain(path: Path) -> None:
    """Never let a state write cross an existing symlinked path component."""

    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise RepoctlError(
                "write path crosses a symlinked parent",
                code="unsafe_write_path",
                path=path.as_posix(),
            )
        current = current.parent


def write_temporary_text(path: Path, text: str, *, suffix: str = ".tmp") -> Path:
    """Create one durable, collision-free sibling file for an atomic publish step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}{suffix}")
        try:
            handle = temporary.open("x", encoding="utf-8", newline="")
        except FileExistsError:
            continue
        break
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return temporary


def decode_schema_version(value: object, *, supported: Collection[int]) -> int:
    """Return an exact JSON integer schema version without numeric coercion."""
    if type(value) is not int or value not in supported:
        raise ValueError("schema version is not a supported exact integer")
    return value
