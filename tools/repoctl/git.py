from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from .repositories import RepoTarget, default_repo_target


ChangedEntry: TypeAlias = tuple[str, str, str]
StablePathState: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class RepoGitState:
    available: bool
    reason: str = ""
    repo_id: str = ""
    repo_path: str = ""
    problem_code: str = ""


class GitNameStatusProblem(StrEnum):
    MALFORMED = "git_name_status_malformed"
    INVALID_PATH = "git_name_status_invalid_path"
    UNMERGED = "unmerged_path"
    UNSUPPORTED = "git_change_type_unsupported"


class ChangeFingerprintProjection(StrEnum):
    CURRENT = "current"


class GitObservationProblem(StrEnum):
    COMMAND_FAILED = "git_observation_command_failed"
    INDEX_MALFORMED = "git_index_observation_malformed"
    PATH_UNAVAILABLE = "git_path_observation_unavailable"
    CONTENT_UNAVAILABLE = "git_content_observation_unavailable"
    LINK_UNAVAILABLE = "git_link_observation_unavailable"
    OBSERVATION_CHANGED = "git_observation_changed"
    EVIDENCE_INVALID = "git_evidence_invalid"


@dataclass(frozen=True)
class GitNameStatusResult:
    entries: tuple[ChangedEntry, ...]
    problem: GitNameStatusProblem | None = None
    path: str = ""


@dataclass(frozen=True)
class _GitBytesResult:
    data: bytes
    problem: GitObservationProblem | None = None


@dataclass(frozen=True)
class _PathStateManifestResult:
    manifest: dict[str, Any] | None
    problem: GitObservationProblem | None = None
    stable_state: StablePathState | None = None


@dataclass(frozen=True)
class PathFingerprintObservation:
    manifest: dict[str, Any]
    stable_state: StablePathState | None = None


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
    if result.returncode != 0:
        return [], RepoGitState(
            False,
            f"{selected.display_path}/ cannot read repository status",
            selected.id,
            selected.display_path,
            "git_status_unavailable",
        )
    return [line for line in result.stdout.splitlines() if line], state


def repo_git_head(root: Path, target: RepoTarget | None = None) -> tuple[str, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return "", state
    assert selected is not None
    repo = selected.root_path
    try:
        result = subprocess.run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        result = None
    head = result.stdout.strip() if result is not None else ""
    if result is not None and result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        return head, state
    try:
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        symbolic = None
    ref = symbolic.stdout.strip() if symbolic is not None else ""
    if symbolic is not None and symbolic.returncode == 0 and ref.startswith("refs/heads/"):
        try:
            exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", ref],
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            exists = None
        if exists is not None and exists.returncode == 1:
            return "<unborn>", state
    return "", RepoGitState(
        False,
        f"{selected.display_path}/ cannot resolve repository HEAD",
        selected.id,
        selected.display_path,
        "git_head_unavailable",
    )


def repo_is_ancestor(root: Path, *, ancestor: str, descendant: str = "HEAD", target: RepoTarget | None = None) -> tuple[bool, RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return False, state
    assert selected is not None
    if descendant == "<unborn>":
        return ancestor == "<unborn>", state
    if ancestor == "<unborn>":
        try:
            resolved = subprocess.run(
                ["git", "rev-parse", "--verify", f"{descendant}^{{commit}}"],
                cwd=selected.root_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            resolved = None
        if resolved is not None and resolved.returncode == 0:
            return True, state
        return False, RepoGitState(
            False,
            f"{selected.display_path}/ cannot resolve repository history descendant: {descendant}",
            selected.id,
            selected.display_path,
        )
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


def _parse_name_status_z(data: bytes) -> GitNameStatusResult:
    if not data:
        return GitNameStatusResult(())
    raw_tokens = data.split(b"\0")
    if raw_tokens[-1] != b"" or any(token == b"" for token in raw_tokens[:-1]):
        return GitNameStatusResult((), GitNameStatusProblem.MALFORMED)
    tokens = [token.decode("utf-8", errors="surrogateescape") for token in raw_tokens[:-1]]
    entries: list[ChangedEntry] = []
    index = 0
    while index < len(tokens):
        code = tokens[index]
        index += 1
        if not re.fullmatch(r"[A-Z](?:[0-9]{1,3})?", code):
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
        change_type = code[0]
        if change_type == "U":
            path = normalize_repo_path(tokens[index]) if index < len(tokens) else ""
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNMERGED, path)
        if change_type in {"X", "B"}:
            path = normalize_repo_path(tokens[index]) if index < len(tokens) else ""
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNSUPPORTED, path)
        if change_type in {"R", "C"}:
            if index + 1 >= len(tokens):
                return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
            old_path = normalize_repo_path(tokens[index])
            path = normalize_repo_path(tokens[index + 1])
            index += 2
            if not path or not old_path:
                return GitNameStatusResult(tuple(entries), GitNameStatusProblem.INVALID_PATH, path or old_path)
            entries.append(("renamed" if change_type == "R" else "copied", path, old_path))
            continue
        if change_type not in {"A", "M", "D", "T"} or len(code) != 1:
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNSUPPORTED)
        if index >= len(tokens):
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
        path = normalize_repo_path(tokens[index])
        index += 1
        if not path:
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.INVALID_PATH)
        mapping = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}
        entries.append((mapping[change_type], path, ""))
    return GitNameStatusResult(tuple(entries))


def _name_status_failure_state(selected: RepoTarget, result: GitNameStatusResult) -> RepoGitState:
    messages = {
        GitNameStatusProblem.MALFORMED: "returned malformed name-status data",
        GitNameStatusProblem.INVALID_PATH: "returned an invalid repository path",
        GitNameStatusProblem.UNMERGED: "contains an unresolved merge path",
        GitNameStatusProblem.UNSUPPORTED: "returned an unsupported change type",
    }
    problem = result.problem or GitNameStatusProblem.MALFORMED
    suffix = f": {result.path}" if result.path else ""
    return RepoGitState(
        False,
        f"{selected.display_path}/ {messages[problem]}{suffix}",
        selected.id,
        selected.display_path,
        problem.value,
    )


def _empty_tree_oid(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            cwd=repo,
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return ""
    object_id = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
        return ""
    return object_id


def repo_commit_range_entries(root: Path, *, base: str, head: str = "HEAD", target: RepoTarget | None = None) -> tuple[list[ChangedEntry], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], state
    assert selected is not None
    repo = selected.root_path
    try:
        resolved_head = subprocess.run(
            ["git", "rev-parse", "--verify", f"{head}^{{commit}}"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        resolved_head = None
    if resolved_head is None or resolved_head.returncode != 0:
        return [], RepoGitState(
            False,
            f"{selected.display_path}/ cannot resolve commit: {head}",
            selected.id,
            selected.display_path,
            "git_commit_range_unavailable",
        )
    diff_base = base
    if base == "<unborn>":
        diff_base = _empty_tree_oid(repo)
        if not diff_base:
            return [], RepoGitState(
                False,
                f"{selected.display_path}/ cannot construct the empty repository tree",
                selected.id,
                selected.display_path,
                "git_commit_range_unavailable",
            )
    else:
        try:
            resolved_base = subprocess.run(
                ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            resolved_base = None
        if resolved_base is None or resolved_base.returncode != 0:
            return [], RepoGitState(
                False,
                f"{selected.display_path}/ cannot resolve commit: {base}",
                selected.id,
                selected.display_path,
                "git_commit_range_unavailable",
            )
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", diff_base, head],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        result = None
    if result is None or result.returncode != 0:
        return [], RepoGitState(
            False,
            f"{selected.display_path}/ cannot diff commit range: {base}..{head}",
            selected.id,
            selected.display_path,
            "git_commit_range_unavailable",
        )
    data = result.stdout
    seen: set[ChangedEntry] = set()
    changes: list[ChangedEntry] = []
    parsed = _parse_name_status_z(data)
    if parsed.problem is not None:
        return [], _name_status_failure_state(selected, parsed)
    for item in parsed.entries:
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
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        head = None
    diff_base = "HEAD" if head is not None and head.returncode == 0 else _empty_tree_oid(repo)
    if not diff_base:
        return [], RepoGitState(False, f"{selected.display_path}/ cannot construct repository baseline", selected.id, selected.display_path)
    diff_commands = (
        ["git", "-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", diff_base],
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-status", "-z", "--find-renames", diff_base],
    )
    for command in diff_commands:
        try:
            result = subprocess.run(
                command,
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            result = None
        if result is None or result.returncode != 0:
            return [], RepoGitState(False, f"{selected.display_path}/ cannot compare repository baseline to index and working tree", selected.id, selected.display_path)
        parsed = _parse_name_status_z(result.stdout)
        if parsed.problem is not None:
            return [], _name_status_failure_state(selected, parsed)
        for item in parsed.entries:
            if item not in seen:
                seen.add(item)
                changes.append(item)
    untracked_result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if untracked_result.returncode != 0:
        return [], RepoGitState(False, f"{selected.display_path}/ cannot read untracked paths", selected.id, selected.display_path)
    for token in untracked_result.stdout.split(b"\0"):
        if not token:
            continue
        path = normalize_repo_path(token.decode("utf-8", errors="surrogateescape"))
        if not path:
            continue
        item = ("untracked", path, "")
        if item not in seen:
            seen.add(item)
            changes.append(item)
    return changes, state


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def normalize_stable_path_state(value: Any) -> StablePathState | None:
    """Validate and canonicalize a content-stable repository path state.

    The identity intentionally excludes Git status, index placement, timestamps,
    and inode metadata so it remains equal after staging or committing the same
    content.
    """
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if not isinstance(kind, str):
        return None
    if kind == "missing" and set(value) == {"kind"}:
        return {"kind": kind}
    if kind == "file" and set(value) == {"kind", "blob_oid", "executable"}:
        blob_oid = value.get("blob_oid")
        executable = value.get("executable")
        if isinstance(blob_oid, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", blob_oid) and isinstance(executable, bool):
            return {"kind": kind, "blob_oid": blob_oid, "executable": executable}
        return None
    if kind == "symlink" and set(value) == {"kind", "target_blob_oid"}:
        target_blob_oid = value.get("target_blob_oid")
        if isinstance(target_blob_oid, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target_blob_oid):
            return {"kind": kind, "target_blob_oid": target_blob_oid}
        return None
    if kind == "gitlink" and set(value) == {"kind", "object_id"}:
        object_id = value.get("object_id")
        if isinstance(object_id, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
            return {"kind": kind, "object_id": object_id}
        return None
    return None


def stable_path_state_digest(value: Any) -> str:
    state = normalize_stable_path_state(value)
    if state is None:
        return ""
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8",
        errors="surrogateescape",
    )
    return _sha256_bytes(encoded)


def _worktree_stable_path_state(repo: Path, path: str, *, index_record: dict[str, str] | None = None) -> StablePathState | None:
    file_path = repo / path
    try:
        file_stat = os.lstat(file_path)
    except (FileNotFoundError, NotADirectoryError):
        return {"kind": "missing"}
    except OSError:
        return None
    if stat.S_ISLNK(file_stat.st_mode):
        try:
            target = os.readlink(os.fsencode(file_path))
        except OSError:
            return None
        result = subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=repo,
            input=target,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        object_id = result.stdout.decode("ascii", errors="ignore").strip()
        return {"kind": "symlink", "target_blob_oid": object_id} if result.returncode == 0 else None
    if stat.S_ISREG(file_stat.st_mode):
        result = subprocess.run(
            ["git", "hash-object", f"--path={path}", "--", path],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        object_id = result.stdout.decode("ascii", errors="ignore").strip()
        if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
            return None
        return {
            "kind": "file",
            "blob_oid": object_id,
            "executable": bool(file_stat.st_mode & 0o111),
        }
    if index_record and index_record.get("mode") == "160000":
        return None
    return None


def _revision_tree_records(repo: Path, revision: str, paths: list[str]) -> dict[str, dict[str, str]] | None:
    if revision == "<unborn>":
        return {}
    try:
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if resolved.returncode != 0:
        return None
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "-z", revision, "--", *paths],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    tokens = result.stdout.split(b"\0")
    if result.stdout and (tokens[-1] != b"" or any(not token for token in tokens[:-1])):
        return None
    requested = set(paths)
    records: dict[str, dict[str, str]] = {}
    for token in tokens[:-1] if result.stdout else ():
        if b"\t" not in token:
            return None
        raw_identity, raw_path = token.split(b"\t", 1)
        try:
            identity = raw_identity.decode("ascii")
        except UnicodeDecodeError:
            return None
        parts = identity.split()
        decoded_path = raw_path.decode("utf-8", errors="surrogateescape")
        path = normalize_repo_path(decoded_path)
        if (
            len(parts) != 3
            or path != decoded_path
            or path not in requested
            or path in records
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[2])
            or (parts[0], parts[1])
            not in {
                ("100644", "blob"),
                ("100755", "blob"),
                ("120000", "blob"),
                ("160000", "commit"),
            }
        ):
            return None
        records[path] = {"mode": parts[0], "type": parts[1], "object": parts[2]}
    return records


def _git_blob(repo: Path, object_id: str) -> bytes | None:
    result = subprocess.run(
        ["git", "cat-file", "blob", object_id],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _revision_stable_path_state(repo: Path, record: dict[str, str] | None) -> StablePathState | None:
    if not record:
        return {"kind": "missing"}
    mode = record.get("mode")
    object_id = record.get("object")
    if not isinstance(mode, str) or not isinstance(object_id, str):
        return None
    if mode == "160000":
        return {"kind": "gitlink", "object_id": object_id}
    if mode == "120000":
        return {"kind": "symlink", "target_blob_oid": object_id}
    if mode in {"100644", "100755"}:
        if _git_blob(repo, object_id) is None:
            return None
        return {
            "kind": "file",
            "blob_oid": object_id,
            "executable": mode == "100755",
        }
    return None


def repo_path_stable_states(
    root: Path,
    paths: list[str],
    target: RepoTarget | None = None,
    *,
    revision: str | None = None,
) -> tuple[dict[str, StablePathState], RepoGitState]:
    """Return path content identities for the worktree or one recorded commit."""
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    normalized_paths = sorted({normalized for value in paths if (normalized := normalize_repo_path(value))})
    if not normalized_paths:
        return {}, state
    repo = selected.root_path
    if revision is None:
        index_records, index_problem = _git_index_records(repo)
        if index_problem is not None:
            return {}, RepoGitState(
                False,
                f"{selected.display_path}/ cannot read repository index state",
                selected.id,
                selected.display_path,
                index_problem.value,
            )
        states: dict[str, StablePathState] = {}
        for path in normalized_paths:
            path_state = _worktree_stable_path_state(repo, path, index_record=index_records.get(path))
            if path_state is None:
                return {}, RepoGitState(
                    False,
                    f"{selected.display_path}/ cannot represent stable path state: {path}",
                    selected.id,
                    selected.display_path,
                )
            states[path] = path_state
        return states, state
    tree_records = _revision_tree_records(repo, revision, normalized_paths)
    if tree_records is None:
        return {}, RepoGitState(
            False,
            f"{selected.display_path}/ cannot resolve stable path state at commit: {revision}",
            selected.id,
            selected.display_path,
        )
    states = {}
    for path in normalized_paths:
        path_state = _revision_stable_path_state(repo, tree_records.get(path))
        if path_state is None:
            return {}, RepoGitState(
                False,
                f"{selected.display_path}/ cannot represent stable path state at {revision}: {path}",
                selected.id,
                selected.display_path,
            )
        states[path] = path_state
    return states, state


def _git_bytes_result(repo: Path, args: list[str]) -> _GitBytesResult:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return _GitBytesResult(b"", GitObservationProblem.COMMAND_FAILED)
    if result.returncode != 0:
        return _GitBytesResult(b"", GitObservationProblem.COMMAND_FAILED)
    return _GitBytesResult(result.stdout)


def _path_state_manifest_result(
    repo: Path,
    path: str,
    *,
    projection: ChangeFingerprintProjection,
    untracked: bool = False,
) -> _PathStateManifestResult:
    normalized = normalize_repo_path(path)
    if not normalized:
        return _PathStateManifestResult(None, GitObservationProblem.PATH_UNAVAILABLE)
    file_path = repo / normalized
    # Plain untracked leaf paths have no index record. The caller checks the
    # untracked set before and after this observation; content is still read.
    if untracked and (file_path.is_symlink() or file_path.is_file()):
        status_result = _GitBytesResult(b"?? " + os.fsencode(normalized) + b"\0")
        index_result = _GitBytesResult(b"")
    else:
        status_result = _git_bytes_result(
            repo,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", normalized],
        )
        index_result = _git_bytes_result(repo, ["ls-files", "--stage", "-z", "--", normalized])
    if status_result.problem is not None or index_result.problem is not None:
        return _PathStateManifestResult(None, GitObservationProblem.COMMAND_FAILED)
    manifest: dict[str, Any] = {
        "path": normalized,
        "git_status_sha256": _sha256_bytes(status_result.data),
        "index_sha256": _sha256_bytes(index_result.data),
    }
    observed_kind = ""
    observed_content: bytes | None = None
    observed_target: str | None = None
    stable_state: StablePathState | None = None
    try:
        file_stat = os.lstat(file_path)
    except (FileNotFoundError, NotADirectoryError):
        observed_kind = "missing"
        manifest.update({"kind": "missing", "mode": ""})
        stable_state = {"kind": "missing"}
    except OSError:
        return _PathStateManifestResult(None, GitObservationProblem.PATH_UNAVAILABLE)
    else:
        manifest["mode"] = f"{stat.S_IMODE(file_stat.st_mode):04o}"
        if stat.S_ISLNK(file_stat.st_mode):
            observed_kind = "symlink"
            try:
                observed_target = os.readlink(file_path)
            except OSError:
                return _PathStateManifestResult(None, GitObservationProblem.LINK_UNAVAILABLE)
            manifest.update({"kind": "symlink", "symlink_target": observed_target})
        elif stat.S_ISREG(file_stat.st_mode):
            observed_kind = "file"
            try:
                observed_content = file_path.read_bytes()
            except OSError:
                return _PathStateManifestResult(None, GitObservationProblem.CONTENT_UNAVAILABLE)
            manifest.update({"kind": "file", "content_sha256": _sha256_bytes(observed_content)})
        elif stat.S_ISDIR(file_stat.st_mode):
            observed_kind = "directory"
            manifest["kind"] = "directory"
        else:
            observed_kind = "other"
            manifest["kind"] = "other"
    return _PathStateManifestResult(manifest, stable_state=stable_state)


def repo_change_fingerprint_observations(
    root: Path,
    entries: list[ChangedEntry],
    target: RepoTarget | None = None,
    *,
    projection: ChangeFingerprintProjection = ChangeFingerprintProjection.CURRENT,
) -> tuple[list[dict[str, str]], dict[str, PathFingerprintObservation], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], {}, state
    assert selected is not None
    ordered_entries = sorted(set(entries), key=lambda item: (item[1], item[2], item[0]))
    paths = sorted({rel for _change, path, old_path in ordered_entries for rel in (old_path, path) if rel})
    observations, observation_state = repo_path_fingerprint_observations(
        root,
        paths,
        selected,
        projection=projection,
    )
    if not observation_state.available:
        return [], {}, observation_state
    records: list[dict[str, str]] = []
    for change, path, old_path in ordered_entries:
        identity = {"change": change, "path": path}
        if old_path:
            identity["old_path"] = old_path
        path_states = [observations[rel].manifest for rel in (old_path, path) if rel]
        encoded = json.dumps(
            {"entry": identity, "path_states": path_states},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        records.append({**identity, "fingerprint_sha256": _sha256_bytes(encoded)})
    return records, observations, state


def repo_path_fingerprint_observations(
    root: Path,
    paths: list[str],
    target: RepoTarget | None = None,
    *,
    projection: ChangeFingerprintProjection = ChangeFingerprintProjection.CURRENT,
) -> tuple[dict[str, PathFingerprintObservation], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    normalized_paths = sorted({normalized for value in paths if (normalized := normalize_repo_path(value))})
    if not normalized_paths:
        return {}, state
    untracked_paths: set[str] = set()
    untracked_before = _git_bytes_result(
        selected.root_path, ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    if untracked_before.problem is not None:
        return {}, RepoGitState(False, "cannot observe untracked paths", selected.id,
                                selected.display_path, untracked_before.problem.value)
    requested_paths = set(normalized_paths)
    untracked_paths = {
        path for raw in untracked_before.data.split(b"\0")
        if raw and (path := os.fsdecode(raw)) in requested_paths
        and not path.startswith(":") and not any(char in path for char in "*?[]\\")
    }

    observations: dict[str, PathFingerprintObservation] = {}
    for path in normalized_paths:
        manifest_result = _path_state_manifest_result(
            selected.root_path,
            path,
            projection=projection,
            untracked=path in untracked_paths,
        )
        if manifest_result.problem is not None or manifest_result.manifest is None:
            problem = manifest_result.problem or GitObservationProblem.PATH_UNAVAILABLE
            return {}, RepoGitState(
                False,
                f"{selected.display_path}/ cannot fingerprint repository path: {path}",
                selected.id,
                selected.display_path,
                problem.value,
            )
        observations[path] = PathFingerprintObservation(
            manifest=manifest_result.manifest,
            stable_state=manifest_result.stable_state,
        )
    if untracked_paths:
        untracked_after = _git_bytes_result(
            selected.root_path, ["ls-files", "--others", "--exclude-standard", "-z"],
        )
        remaining = {os.fsdecode(raw) for raw in untracked_after.data.split(b"\0") if raw}
        if untracked_after.problem is not None or not untracked_paths <= remaining:
            return {}, RepoGitState(False, "untracked paths changed during observation",
                                    selected.id, selected.display_path,
                                    (untracked_after.problem or GitObservationProblem.OBSERVATION_CHANGED).value)
    return observations, state


def repo_change_fingerprint_records(
    root: Path,
    entries: list[ChangedEntry],
    target: RepoTarget | None = None,
) -> tuple[list[dict[str, str]], RepoGitState]:
    records, _observations, state = repo_change_fingerprint_observations(root, entries, target)
    return records, state


def repo_path_fingerprints(root: Path, paths: list[str], target: RepoTarget | None = None) -> tuple[dict[str, str], RepoGitState]:
    observations, state = repo_path_fingerprint_observations(root, paths, target)
    if not state.available:
        return {}, state
    fingerprints: dict[str, str] = {}
    for path, observation in observations.items():
        manifest = observation.manifest
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprints[path] = _sha256_bytes(encoded)
    return fingerprints, state


def _git_index_records(repo: Path) -> tuple[dict[str, dict[str, str]], GitObservationProblem | None]:
    records: dict[str, dict[str, str]] = {}
    result = _git_bytes_result(repo, ["ls-files", "--stage", "-z"])
    if result.problem is not None:
        return {}, result.problem
    for token in result.data.split(b"\0"):
        if not token or b"\t" not in token:
            if token:
                return {}, GitObservationProblem.INDEX_MALFORMED
            continue
        raw_identity, raw_path = token.split(b"\t", 1)
        parts = raw_identity.decode("ascii", errors="ignore").split()
        path = normalize_repo_path(raw_path.decode("utf-8", errors="surrogateescape"))
        if len(parts) != 3 or not path or parts[2] != "0":
            return {}, GitObservationProblem.INDEX_MALFORMED
        records[path] = {"mode": parts[0], "object": parts[1]}
    return records, None


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
    index_records, index_problem = _git_index_records(repo)
    if index_problem is not None:
        return {}, RepoGitState(
            False,
            f"{selected.display_path}/ cannot read repository index state",
            selected.id,
            selected.display_path,
            index_problem.value,
        )
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
    entry_fingerprints, entry_state = repo_change_fingerprint_records(root, entries, selected)
    if not entry_state.available:
        return {}, "", entry_state
    untracked_result = _git_bytes_result(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked_result.problem is not None:
        return {}, "", RepoGitState(
            False,
            f"{selected.display_path}/ cannot read untracked repository paths",
            selected.id,
            selected.display_path,
            untracked_result.problem.value,
        )
    untracked_paths = [
        normalize_repo_path(token.decode("utf-8", errors="surrogateescape"))
        for token in untracked_result.data.split(b"\0")
        if token
    ]
    if any(not path for path in untracked_paths):
        return {}, "", RepoGitState(
            False,
            f"{selected.display_path}/ returned an invalid untracked repository path",
            selected.id,
            selected.display_path,
            GitObservationProblem.PATH_UNAVAILABLE.value,
        )
    untracked: list[dict[str, Any]] = []
    for path in sorted(untracked_paths):
        manifest_result = _path_state_manifest_result(
            repo,
            path,
            projection=ChangeFingerprintProjection.CURRENT,
        )
        if manifest_result.problem is not None or manifest_result.manifest is None:
            problem = manifest_result.problem or GitObservationProblem.PATH_UNAVAILABLE
            return {}, "", RepoGitState(
                False,
                f"{selected.display_path}/ cannot fingerprint untracked repository path: {path}",
                selected.id,
                selected.display_path,
                problem.value,
            )
        untracked.append(manifest_result.manifest)
    staged_result = _git_bytes_result(repo, ["diff", "--cached", "--binary", "--full-index", "--no-ext-diff"])
    unstaged_result = _git_bytes_result(repo, ["diff", "--binary", "--full-index", "--no-ext-diff"])
    if staged_result.problem is not None or unstaged_result.problem is not None:
        return {}, "", RepoGitState(
            False,
            f"{selected.display_path}/ cannot fingerprint repository diff",
            selected.id,
            selected.display_path,
            GitObservationProblem.COMMAND_FAILED.value,
        )
    ownership_items: list[dict[str, str]] = []
    for path, decision in sorted((ownership or {}).items()):
        expected_fields = {"ownership", "decided_at", "baseline_fingerprint", "final_fingerprint"}
        if isinstance(decision, dict) and decision.get("ownership") in {"delta", "external"}:
            expected_fields.add("reviewed_fingerprint")
        if (
            not isinstance(path, str)
            or normalize_repo_path(path) != path
            or not isinstance(decision, dict)
            or set(decision) != expected_fields
            or decision.get("ownership") not in {"task", "preexisting", "delta", "external"}
            or any(not isinstance(decision.get(field), str) for field in expected_fields)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", decision["baseline_fingerprint"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", decision["final_fingerprint"])
            or (decision["ownership"] in {"delta", "external"} and decision["reviewed_fingerprint"] != decision["final_fingerprint"])
        ):
            return {}, "", RepoGitState(
                False,
                f"{selected.display_path}/ received invalid baseline ownership evidence",
                selected.id,
                selected.display_path,
                GitObservationProblem.EVIDENCE_INVALID.value,
            )
        ownership_items.append(
            {
                "path": path,
                "ownership": decision["ownership"],
                "baseline_fingerprint": decision["baseline_fingerprint"],
                "final_fingerprint": decision["final_fingerprint"],
                **({"reviewed_fingerprint": decision["reviewed_fingerprint"]} if decision["ownership"] in {"delta", "external"} else {}),
            }
        )
    manifest: dict[str, Any] = {
        "repo_id": selected.id,
        "repo_path": selected.display_path,
        "mode": mode,
        "start_head": start_head,
        "observed_head": observed_head,
        "changed_entries": _sorted_changed_entries(entries),
        "entry_fingerprints": entry_fingerprints,
        "staged_binary_diff_sha256": _sha256_bytes(staged_result.data),
        "unstaged_binary_diff_sha256": _sha256_bytes(unstaged_result.data),
        "untracked": untracked,
        "ownership": ownership_items,
        "conflict_paths": sorted(set(conflict_paths or [])),
    }
    if mode == "committed_range":
        committed_base = _empty_tree_oid(repo) if start_head == "<unborn>" else start_head
        if not committed_base:
            return {}, "", RepoGitState(
                False,
                f"{selected.display_path}/ cannot construct committed repository baseline",
                selected.id,
                selected.display_path,
                GitObservationProblem.COMMAND_FAILED.value,
            )
        committed_result = _git_bytes_result(
            repo,
            ["diff", "--binary", "--full-index", "--no-ext-diff", committed_base, observed_head],
        )
        if committed_result.problem is not None:
            return {}, "", RepoGitState(
                False,
                f"{selected.display_path}/ cannot fingerprint committed repository diff",
                selected.id,
                selected.display_path,
                committed_result.problem.value,
            )
        manifest["committed_binary_diff_sha256"] = _sha256_bytes(committed_result.data)
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return manifest, _sha256_bytes(encoded), state
