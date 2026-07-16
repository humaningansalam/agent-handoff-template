from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from .repositories import RepoTarget, default_repo_target


ChangedEntry: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True)
class RepoGitState:
    available: bool
    reason: str = ""
    repo_id: str = ""
    repo_path: str = ""


def _target(root: Path, target: RepoTarget | None = None) -> RepoTarget | None:
    return target or default_repo_target(root)


def repo_git_state(root: Path, target: RepoTarget | None = None) -> RepoGitState:
    try:
        selected = _target(root, target)
    except Exception as exc:
        return RepoGitState(False, str(exc))
    if selected is None:
        return RepoGitState(False, "product repository directory is missing")
    repo = selected.root_path
    if not (repo / ".git").exists():
        return RepoGitState(False, f"{selected.display_path}/ is not an independent git repository", selected.id, selected.display_path)
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return RepoGitState(False, f"{selected.display_path}/ git metadata is not usable", selected.id, selected.display_path)
    try:
        top = Path(result.stdout.strip()).resolve()
        if top != repo.resolve():
            return RepoGitState(False, f"{selected.display_path}/ git resolves outside repository root", selected.id, selected.display_path)
    except OSError:
        return RepoGitState(False, f"{selected.display_path}/ git root cannot be resolved", selected.id, selected.display_path)
    return RepoGitState(True, repo_id=selected.id, repo_path=selected.display_path)


def repo_git_status(root: Path, target: RepoTarget | None = None) -> tuple[list[str], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], state
    assert selected is not None
    repo = selected.root_path
    result = subprocess.run(["git", "status", "--short"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return [line for line in result.stdout.splitlines() if line], state


def repo_git_head(root: Path, target: RepoTarget | None = None) -> tuple[str, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return "", state
    assert selected is not None
    repo = selected.root_path
    result = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return "<unborn>", state
    return result.stdout.strip(), state


def repo_is_ancestor(root: Path, *, ancestor: str, descendant: str = "HEAD", target: RepoTarget | None = None) -> tuple[bool, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return False, state
    assert selected is not None
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=selected.root_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True, state
    if result.returncode == 1:
        return False, state
    return False, RepoGitState(
        False,
        f"{selected.display_path}/ cannot compare repository history: {ancestor}..{descendant}",
        selected.id,
        selected.display_path,
    )


def normalize_repo_path(path: str | Path) -> str:
    raw = str(path)
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _git_lines(repo: Path, args: list[str]) -> list[str]:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _parse_name_status(line: str) -> list[ChangedEntry]:
    parts = line.split("\t")
    code = parts[0]
    change_type = code[0]
    if change_type in {"R", "C"} and len(parts) >= 3:
        path = normalize_repo_path(parts[2])
        old_path = normalize_repo_path(parts[1])
        return [("renamed" if change_type == "R" else "copied", path, old_path)] if path else []
    if len(parts) >= 2:
        mapping = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}
        path = normalize_repo_path(parts[1])
        return [(mapping.get(change_type, "modified"), path, "")] if path else []
    return []


def _parse_name_status_z(data: bytes) -> list[ChangedEntry]:
    tokens = [token.decode("utf-8", errors="surrogateescape") for token in data.split(b"\0") if token]
    entries: list[ChangedEntry] = []
    index = 0
    while index < len(tokens):
        code = tokens[index]
        index += 1
        if not code:
            continue
        change_type = code[0]
        if change_type in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            old_path = normalize_repo_path(tokens[index])
            path = normalize_repo_path(tokens[index + 1])
            index += 2
            if path:
                entries.append(("renamed" if change_type == "R" else "copied", path, old_path))
            continue
        if index >= len(tokens):
            break
        path = normalize_repo_path(tokens[index])
        index += 1
        if path:
            mapping = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}
            entries.append((mapping.get(change_type, "modified"), path, ""))
    return entries


def repo_commit_range_entries(root: Path, *, base: str, head: str = "HEAD", target: RepoTarget | None = None) -> tuple[list[ChangedEntry], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], state
    assert selected is not None
    repo = selected.root_path
    for revision in (base, head):
        result = subprocess.run(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0:
            return [], RepoGitState(False, f"{selected.display_path}/ cannot resolve commit: {revision}", selected.id, selected.display_path)
    result = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", f"{base}..{head}"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return [], RepoGitState(False, f"{selected.display_path}/ cannot diff commit range: {base}..{head}", selected.id, selected.display_path)
    data = result.stdout
    seen: set[ChangedEntry] = set()
    changes: list[ChangedEntry] = []
    for item in _parse_name_status_z(data):
        if item not in seen:
            seen.add(item)
            changes.append(item)
    return changes, state


def repo_changed_entries(root: Path, target: RepoTarget | None = None) -> tuple[list[ChangedEntry], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], state
    assert selected is not None
    repo = selected.root_path
    seen: set[ChangedEntry] = set()
    changes: list[ChangedEntry] = []
    for args in (["diff", "--name-status", "--find-renames"], ["diff", "--cached", "--name-status", "--find-renames"]):
        for line in _git_lines(repo, args):
            for item in _parse_name_status(line):
                if item not in seen:
                    seen.add(item)
                    changes.append(item)
    for line in _git_lines(repo, ["ls-files", "--others", "--exclude-standard"]):
        path = normalize_repo_path(line)
        if not path:
            continue
        item = ("untracked", path, "")
        if item not in seen:
            seen.add(item)
            changes.append(item)
    return changes, state


def repo_change_fingerprints(root: Path, entries: list[ChangedEntry], target: RepoTarget | None = None) -> tuple[dict[str, str], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    repo = selected.root_path
    fingerprints: dict[str, str] = {}
    for entry in entries:
        _change, path, old_path = entry
        digest = hashlib.sha256()
        for rel in [old_path, path]:
            if not rel:
                continue
            digest.update(rel.encode("utf-8"))
            for args in (["diff", "--binary", "--", rel], ["diff", "--cached", "--binary", "--", rel]):
                result = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
                digest.update(result.stdout)
            file_path = repo / rel
            if file_path.is_file():
                try:
                    digest.update(file_path.read_bytes())
                except OSError:
                    digest.update(b"<unreadable>")
            elif file_path.exists():
                digest.update(b"<non-file>")
            else:
                digest.update(b"<missing>")
        fingerprints[_changed_entry_key(entry)] = digest.hexdigest()
    return fingerprints, state


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_bytes(repo: Path, args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else b""


def _path_state_manifest(repo: Path, path: str) -> dict[str, Any]:
    normalized = normalize_repo_path(path)
    file_path = repo / normalized
    status_bytes = _git_bytes(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", normalized])
    index_bytes = _git_bytes(repo, ["ls-files", "--stage", "-z", "--", normalized])
    manifest: dict[str, Any] = {
        "path": normalized,
        "git_status_sha256": _sha256_bytes(status_bytes),
        "index_sha256": _sha256_bytes(index_bytes),
    }
    try:
        file_stat = os.lstat(file_path)
    except OSError:
        manifest.update({"kind": "missing", "mode": ""})
        return manifest
    manifest["mode"] = f"{stat.S_IMODE(file_stat.st_mode):04o}"
    if stat.S_ISLNK(file_stat.st_mode):
        manifest.update({"kind": "symlink", "symlink_target": os.readlink(file_path)})
    elif stat.S_ISREG(file_stat.st_mode):
        try:
            content = file_path.read_bytes()
        except OSError:
            content = b"<unreadable>"
        manifest.update({"kind": "file", "content_sha256": _sha256_bytes(content)})
    elif stat.S_ISDIR(file_stat.st_mode):
        manifest["kind"] = "directory"
    else:
        manifest["kind"] = "other"
    return manifest


def repo_path_fingerprints(root: Path, paths: list[str], target: RepoTarget | None = None) -> tuple[dict[str, str], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    fingerprints: dict[str, str] = {}
    for path in sorted({normalized for value in paths if (normalized := normalize_repo_path(value))}):
        manifest = _path_state_manifest(selected.root_path, path)
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprints[path] = _sha256_bytes(encoded)
    return fingerprints, state


def _git_index_records(repo: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for token in _git_bytes(repo, ["ls-files", "--stage", "-z"]).split(b"\0"):
        if not token or b"\t" not in token:
            continue
        raw_identity, raw_path = token.split(b"\t", 1)
        parts = raw_identity.decode("ascii", errors="ignore").split()
        path = normalize_repo_path(raw_path.decode("utf-8", errors="surrogateescape"))
        if len(parts) != 3 or not path or parts[2] != "0":
            continue
        records[path] = {"mode": parts[0], "object": parts[1]}
    return records


def _stat_probe(path: Path) -> dict[str, Any]:
    try:
        file_stat = os.lstat(path)
    except OSError:
        return {"kind": "missing"}
    probe: dict[str, Any] = {
        "mode": f"{stat.S_IMODE(file_stat.st_mode):04o}",
        "size": int(file_stat.st_size),
        "mtime_ns": int(file_stat.st_mtime_ns),
        "ctime_ns": int(file_stat.st_ctime_ns),
    }
    if stat.S_ISLNK(file_stat.st_mode):
        probe.update({"kind": "symlink", "target": os.readlink(path)})
    elif stat.S_ISREG(file_stat.st_mode):
        probe["kind"] = "file"
    else:
        probe["kind"] = "other"
    return probe


def repo_file_state_records(
    root: Path,
    *,
    paths: list[str],
    target: RepoTarget | None = None,
    previous: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], RepoGitState]:
    """Return content identities without rereading clean tracked files.

    Git object IDs identify clean tracked files. Dirty and untracked files use a
    stat probe to reuse the previous content digest when their working-tree
    state has not changed.
    """
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    repo = selected.root_path
    index_records = _git_index_records(repo)
    changes, change_state = repo_changed_entries(root, selected)
    if not change_state.available:
        return {}, change_state
    changes_by_path: dict[str, list[dict[str, str]]] = {}
    for change, path, old_path in changes:
        if path:
            item = {"change": change}
            if old_path:
                item["old_path"] = old_path
            changes_by_path.setdefault(path, []).append(item)
    previous = previous or {}
    records: dict[str, dict[str, Any]] = {}
    for path in sorted({normalized for value in paths if (normalized := normalize_repo_path(value))}):
        index = index_records.get(path, {})
        path_changes = sorted(changes_by_path.get(path, []), key=lambda item: (item.get("change", ""), item.get("old_path", "")))
        if index and not path_changes:
            records[path] = {
                "path": path,
                "source": "git_index",
                "mode": index.get("mode", ""),
                "object": index.get("object", ""),
            }
            continue

        probe = {
            "index": index,
            "changes": path_changes,
            "stat": _stat_probe(repo / path),
        }
        old = previous.get(path)
        if isinstance(old, dict) and old.get("probe") == probe and old.get("source") == "working_tree":
            records[path] = old
            continue

        record: dict[str, Any] = {
            "path": path,
            "source": "working_tree",
            "probe": probe,
        }
        kind = str(probe["stat"].get("kind") or "")
        if kind == "file":
            try:
                record["content_sha256"] = _sha256_bytes((repo / path).read_bytes())
            except OSError:
                record["content_sha256"] = _sha256_bytes(b"<unreadable>")
        elif kind == "symlink":
            record["symlink_target"] = str(probe["stat"].get("target") or "")
        records[path] = record
    return records, state


def _sorted_changed_entries(entries: list[ChangedEntry]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for change, path, old_path in sorted(set(entries), key=lambda item: (item[1], item[2], item[0])):
        item = {"change": change, "path": path}
        if old_path:
            item["old_path"] = old_path
        values.append(item)
    return values


def repo_evidence_fingerprint(
    root: Path,
    *,
    mode: str,
    start_head: str,
    observed_head: str,
    entries: list[ChangedEntry],
    ownership: dict[str, dict[str, Any]] | None = None,
    conflict_paths: list[str] | None = None,
    target: RepoTarget | None = None,
) -> tuple[dict[str, Any], str, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, "", state
    assert selected is not None
    repo = selected.root_path
    untracked_paths = [
        normalize_repo_path(token.decode("utf-8", errors="surrogateescape"))
        for token in _git_bytes(repo, ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0")
        if token
    ]
    untracked = [_path_state_manifest(repo, path) for path in sorted(path for path in untracked_paths if path)]
    ownership_items: list[dict[str, str]] = []
    for path, decision in sorted((ownership or {}).items()):
        ownership_items.append(
            {
                "path": path,
                "ownership": str(decision.get("ownership") or ""),
                "baseline_fingerprint": str(decision.get("baseline_fingerprint") or ""),
                "final_fingerprint": str(decision.get("final_fingerprint") or ""),
            }
        )
    manifest: dict[str, Any] = {
        "repo_id": selected.id,
        "repo_path": selected.display_path,
        "mode": mode,
        "start_head": start_head,
        "observed_head": observed_head,
        "changed_entries": _sorted_changed_entries(entries),
        "staged_binary_diff_sha256": _sha256_bytes(_git_bytes(repo, ["diff", "--cached", "--binary", "--full-index", "--no-ext-diff"])),
        "unstaged_binary_diff_sha256": _sha256_bytes(_git_bytes(repo, ["diff", "--binary", "--full-index", "--no-ext-diff"])),
        "untracked": untracked,
        "ownership": ownership_items,
        "conflict_paths": sorted(set(conflict_paths or [])),
    }
    if mode == "committed_range":
        manifest["committed_binary_diff_sha256"] = _sha256_bytes(
            _git_bytes(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", f"{start_head}..{observed_head}"])
        )
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return manifest, _sha256_bytes(encoded), state


def _changed_entry_key(entry: ChangedEntry) -> str:
    change, path, old_path = entry
    return "\0".join([change, path, old_path])
