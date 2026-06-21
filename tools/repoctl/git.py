from __future__ import annotations

import subprocess
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

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


def normalize_repo_path(path: str | Path) -> str:
    raw = str(path).strip().replace("\\", "/")
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


def _changed_entry_key(entry: ChangedEntry) -> str:
    change, path, old_path = entry
    return "\0".join([change, path, old_path])


def repo_diff_evidence(root: Path, target: RepoTarget | None = None) -> tuple[str, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return f"repo git unavailable: {state.reason}", state
    assert selected is not None
    repo = selected.root_path
    status_lines = [line for line in subprocess.run(["git", "status", "--short"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.splitlines() if line]
    diff = subprocess.run(["git", "diff", "--stat"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.rstrip("\n")
    cached = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.rstrip("\n")
    parts: list[str] = []
    if status_lines:
        tracked: list[str] = []
        untracked: list[str] = []
        generated: list[str] = []
        for line in status_lines:
            path = line[3:]
            if path.endswith("/") and path[:-1].endswith("__pycache__") or "__pycache__/" in path:
                generated.append(path)
            elif line.startswith("??"):
                untracked.append(path)
            else:
                tracked.append(line)
        summary: list[str] = []
        if tracked:
            summary.append("Tracked changes:\n" + "\n".join(f"- {line}" for line in tracked))
        if untracked:
            summary.append("Untracked files:\n" + "\n".join(f"- {path}" for path in untracked))
        if generated:
            summary.append("Generated residue:\n" + "\n".join(f"- {path}" for path in generated))
        parts.append("Repo status summary:\n" + "\n\n".join(summary))
    if diff:
        parts.append("git diff --stat:\n" + diff)
    if cached:
        parts.append("git diff --cached --stat:\n" + cached)
    return "\n\n".join(parts), state
