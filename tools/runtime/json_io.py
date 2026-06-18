from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import fcntl


class JsonIoError(RuntimeError):
    pass


def _ensure_no_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise JsonIoError(f"{label} is a symlink: {path}")


def _resolve_existing_root(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise JsonIoError(f"root is a symlink: {root}")
    if not root.exists():
        raise JsonIoError(f"root missing: {root}")
    if not root.is_dir():
        raise JsonIoError(f"root is not a directory: {root}")
    return root.resolve()


def _ensure_parent_dir(path: Path) -> None:
    parent = path.parent
    _ensure_no_symlink(parent, "parent")

    existing = parent
    missing: list[Path] = []
    while not existing.exists():
        missing.append(existing)
        next_existing = existing.parent
        if next_existing == existing:
            raise JsonIoError(f"parent missing: {parent}")
        existing = next_existing

    _ensure_no_symlink(existing, "parent")
    if not existing.is_dir():
        raise JsonIoError(f"parent is not a directory: {existing}")

    current = existing
    for part in reversed(missing):
        current = current / part.name
        _ensure_no_symlink(current, "parent")
        current.mkdir(exist_ok=True)
        _ensure_no_symlink(current, "parent")

    _ensure_no_symlink(parent, "parent")


def _prepare_atomic_target(path: Path) -> Path:
    path = Path(path)
    _ensure_no_symlink(path, "target")
    _ensure_parent_dir(path)
    _ensure_no_symlink(path.parent, "parent")

    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink():
            raise JsonIoError(f"tmp is a symlink: {tmp}")
        tmp.unlink()
    return tmp


def _reject_dotdot(path: Path) -> None:
    if ".." in Path(path).parts:
        raise JsonIoError(f"path escapes root: {path}")


def _resolve_root_bound_path(path: Path, root_resolved: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else root_resolved / path


def _check_under_root(path: Path, root: Path) -> Path:
    root_resolved = _resolve_existing_root(root)
    path = _resolve_root_bound_path(path, root_resolved)
    _reject_dotdot(path)
    _ensure_no_symlink(path, "target")

    try:
        relative_parent = path.parent.relative_to(root_resolved)
    except ValueError as e:
        raise JsonIoError(f"path escapes root: {path}") from e

    current = root_resolved
    for part in relative_parent.parts:
        if part in {"", ".", ".."}:
            raise JsonIoError(f"path escapes root: {path}")
        current = current / part
        _ensure_no_symlink(current, "parent")
        if current.exists() and not current.resolve().is_relative_to(root_resolved):
            raise JsonIoError(f"path escapes root: {path}")

    if path.exists():
        resolved = path.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise JsonIoError(f"path escapes root: {path}")

    return path


def read_json_object(path: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    if path.is_symlink():
        raise JsonIoError(f"{path} is a symlink")
    if not path.is_file():
        if missing_ok:
            return {}
        raise JsonIoError(f"{path} missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise JsonIoError(f"{path} is invalid JSON") from e
    if not isinstance(data, dict):
        raise JsonIoError(f"{path} must contain a JSON object")
    return data


def read_json_array(path: Path, *, missing_ok: bool = True) -> list[Any]:
    if path.is_symlink():
        raise JsonIoError(f"{path} is a symlink")
    if not path.is_file():
        if missing_ok:
            return []
        raise JsonIoError(f"{path} missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise JsonIoError(f"{path} is invalid JSON") from e
    if not isinstance(data, list):
        raise JsonIoError(f"{path} must contain a JSON array")
    return data


def read_json_strict(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink():
        raise JsonIoError(f"{path} is a symlink")
    if not path.exists():
        raise JsonIoError(f"{path} missing")
    if not path.is_file():
        raise JsonIoError(f"{path} is not a file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise JsonIoError(f"{path} is invalid JSON") from e
    if not isinstance(data, dict):
        raise JsonIoError(f"{path} must contain a JSON object")
    return data


def read_json_optional(path: Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    return read_json_strict(path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    tmp = _prepare_atomic_target(path)
    try:
        tmp.write_bytes(data)
        _ensure_no_symlink(path, "target")
        tmp.replace(path)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise


def write_text_atomic(path: Path, content: str) -> None:
    write_bytes_atomic(path, content.encode("utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    write_text_atomic(path, content)


def write_text_atomic_under_root(path: Path, content: str, root: Path) -> None:
    target = _check_under_root(path, root)
    write_text_atomic(target, content)


def write_json_atomic_under_root(path: Path, data: Any, root: Path) -> None:
    target = _check_under_root(path, root)
    write_json_atomic(target, data)


def append_jsonl_atomic_under_root(path: Path, event: dict[str, Any], root: Path, *, max_bytes: int = 512 * 1024) -> None:
    target = _check_under_root(path, root)
    lock_path = _check_under_root(target.with_name(target.name + ".lock"), root)
    _ensure_parent_dir(lock_path)
    _ensure_no_symlink(lock_path, "lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        existing = ""
        if target.exists():
            _ensure_no_symlink(target, "target")
            if not target.is_file():
                raise JsonIoError(f"target is not a file: {target}")
            existing = target.read_text(encoding="utf-8")
            if len(existing.encode("utf-8")) > max_bytes:
                existing = existing[-max_bytes // 2 :]
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        write_text_atomic_under_root(target, existing + line, root)
