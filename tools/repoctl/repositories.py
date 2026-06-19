from __future__ import annotations

import re
import subprocess
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write
from .settings import load_repoctl_settings


REPO_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
PRODUCT_REPO_DIRS = ("repos",)


@dataclass(frozen=True)
class RepoTarget:
    id: str
    root_path: Path
    display_path: str
    identity_source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "path": self.display_path,
            "identity_source": self.identity_source,
        }


@dataclass(frozen=True)
class RepoCandidate:
    root_path: Path
    display_path: str
    suggested_id: str
    git_toplevel: str
    validation_status: str = "valid"
    identity_status: str = "unbound"

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.display_path,
            "suggested_id": self.suggested_id,
            "git_toplevel": self.git_toplevel,
            "validation_status": self.validation_status,
            "identity_status": self.identity_status,
        }


@dataclass(frozen=True)
class RepoLayout:
    placement: str
    targets: tuple[RepoTarget, ...]
    candidates: tuple[RepoCandidate, ...] = ()
    registry_ready: bool = True
    revision: str = ""
    warnings: tuple[str, ...] = ()
    problems: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "placement": self.placement,
            "registry_ready": self.registry_ready,
            "revision": self.revision,
            "targets": [target.to_dict() for target in self.targets],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "warnings": list(self.warnings),
            "problems": list(self.problems),
        }


def _safe_rel(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if Path(raw).is_absolute():
        raise RepoctlError(f"repository path must be workspace-relative: {value}")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise RepoctlError(f"invalid repository path: {value}")
    return "/".join(parts)


def _validate_product_repo_rel(rel: str) -> None:
    parts = rel.split("/")
    if rel == "repos":
        return
    if len(parts) == 2 and parts[0] == "repos" and parts[1]:
        return
    raise RepoctlError(f"repository path must be repos or repos/<id>: {rel}", code="repository_topology_invalid", path=rel)


def _casefold_id(value: str) -> str:
    return value.casefold()


def _git_toplevel(path: Path) -> Path | None:
    if not path.exists():
        return None
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return None
    try:
        return Path(result.stdout.strip()).resolve()
    except OSError:
        return None


def is_git_repo_root(path: Path) -> bool:
    git_marker = path / ".git"
    if not git_marker.exists():
        return False
    top = _git_toplevel(path)
    try:
        return top == path.resolve()
    except OSError:
        return False


def _revision(layout_data: dict[str, Any]) -> str:
    encoded = json.dumps(layout_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _target(root: Path, repo_id: str, rel: str, *, identity_source: str) -> RepoTarget:
    if not REPO_ID_RE.match(repo_id):
        raise RepoctlError(f"invalid repository id: {repo_id}")
    rel = _safe_rel(rel)
    _validate_product_repo_rel(rel)
    if rel == "repos" and repo_id != "main":
        raise RepoctlError("direct repos/ repository must use repository id: main", code="repository_topology_invalid", path=rel)
    path = root / rel
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        raise RepoctlError(f"repository path must stay inside workspace: {rel}")
    if not is_git_repo_root(path):
        raise RepoctlError(f"repository path is not a git top-level: {rel}")
    return RepoTarget(repo_id, path, rel, identity_source)


def _configured_targets(root: Path, settings: dict[str, Any], *, allow_unconfigured: bool = False) -> tuple[RepoTarget, ...] | None:
    raw = settings.get("repositories")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise RepoctlError("docs/repoctl.json repositories must be a non-empty array")
    targets: list[RepoTarget] = []
    ids: set[str] = set()
    paths: set[str] = set()
    resolved_roots: set[str] = set()
    git_toplevels: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RepoctlError("docs/repoctl.json repositories entries must be objects")
        repo_id = item.get("id")
        path = item.get("path")
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise RepoctlError("repository id must be a non-empty string")
        if not isinstance(path, str) or not path.strip():
            raise RepoctlError("repository path must be a non-empty string")
        folded = _casefold_id(repo_id.strip())
        if folded in ids:
            raise RepoctlError(f"duplicate repository id: {repo_id}")
        ids.add(folded)
        rel = _safe_rel(path)
        if rel in paths:
            raise RepoctlError(f"duplicate repository path: {rel}")
        paths.add(rel)
        target = _target(root, repo_id.strip(), rel, identity_source="pinned")
        resolved_root = target.root_path.resolve().as_posix()
        if resolved_root in resolved_roots:
            raise RepoctlError(f"duplicate repository real path: {rel}", code="repository_topology_invalid", path=rel)
        resolved_roots.add(resolved_root)
        git_top = _git_toplevel(target.root_path)
        git_top_key = git_top.as_posix() if git_top else resolved_root
        if git_top_key in git_toplevels:
            raise RepoctlError(f"duplicate repository git root: {rel}", code="repository_topology_invalid", path=rel)
        git_toplevels.add(git_top_key)
        targets.append(target)
    _validate_no_nested_targets(targets)
    discovered = _discover_git_roots(root)
    configured_paths = {target.display_path for target in targets}
    extras = sorted(path for path in discovered if path not in configured_paths)
    if extras and not allow_unconfigured:
        raise RepoctlError(f"unconfigured product repository detected: {', '.join(extras)}")
    return tuple(targets)


def _discover_git_roots(root: Path) -> list[str]:
    candidates: list[str] = []
    for rel in PRODUCT_REPO_DIRS:
        path = root / rel
        if is_git_repo_root(path):
            candidates.append(rel)
    repos_dir = root / "repos"
    if repos_dir.is_dir():
        for child in sorted(repos_dir.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            rel = f"repos/{child.name}"
            if is_git_repo_root(child):
                candidates.append(rel)
    return candidates


def _suggested_id_for_candidate(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def _candidates_for_discovered(root: Path, discovered: list[str]) -> tuple[RepoCandidate, ...]:
    candidates: list[RepoCandidate] = []
    for rel in discovered:
        path = root / rel
        top = _git_toplevel(path)
        candidates.append(RepoCandidate(path, rel, _suggested_id_for_candidate(rel), top.as_posix() if top else ""))
    return tuple(candidates)


def _problem(severity: str, code: str, message: str, path: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "path": path}


def _unowned_product_paths(root: Path, owned_paths: set[str]) -> tuple[dict[str, str], ...]:
    repos_dir = root / "repos"
    if not repos_dir.is_dir():
        return ()
    problems: list[dict[str, str]] = []
    for child in sorted(repos_dir.iterdir()):
        rel = f"repos/{child.name}"
        if rel in owned_paths:
            continue
        problems.append(_problem("error", "repository_unowned_product_path", f"collection layout contains unowned product path: {rel}", rel))
    return tuple(problems)


def _nested_git_root_problems(root: Path, rels: set[str]) -> tuple[dict[str, str], ...]:
    problems: list[dict[str, str]] = []
    seen: set[str] = set()
    for rel in sorted(rels):
        path = root / rel
        if not path.is_dir():
            continue
        root_marker = path / ".git"
        for marker in path.rglob(".git"):
            if marker == root_marker:
                continue
            nested_root = marker.parent
            try:
                nested_rel = nested_root.relative_to(root).as_posix()
            except ValueError:
                nested_rel = nested_root.as_posix()
            if nested_rel in seen:
                continue
            seen.add(nested_rel)
            problems.append(_problem("error", "repository_topology_invalid", f"nested git repository is not allowed under product repository: {nested_rel}", nested_rel))
    return tuple(problems)


def _validate_no_nested_targets(targets: list[RepoTarget] | tuple[RepoTarget, ...]) -> None:
    rels = sorted(target.display_path for target in targets)
    for index, rel in enumerate(rels):
        for other in rels[index + 1 :]:
            if other.startswith(f"{rel}/"):
                raise RepoctlError(f"nested repository paths are not allowed: {rel} and {other}")


def _layout_from_settings(root: Path, settings: dict[str, Any]) -> RepoLayout:
    configured = _configured_targets(root, settings, allow_unconfigured=True)
    if configured is not None:
        placement = "collection" if len(configured) > 1 or any("/" in target.display_path for target in configured) else "direct"
        discovered = _discover_git_roots(root)
        configured_paths = {target.display_path for target in configured}
        extras = sorted(path for path in discovered if path not in configured_paths)
        candidates = _candidates_for_discovered(root, extras)
        topology_problems = _nested_git_root_problems(root, configured_paths)
        unowned = _unowned_product_paths(root, configured_paths | set(discovered)) if placement == "collection" else ()
        problems = (*topology_problems, *unowned)
        ready = not candidates and not problems
        layout = RepoLayout(placement, configured, candidates=candidates, registry_ready=ready, problems=problems)
        return RepoLayout(placement, configured, candidates=candidates, registry_ready=ready, problems=problems, revision=_revision(layout.to_dict()))

    discovered = _discover_git_roots(root)
    if not discovered:
        if (root / "repos").exists():
            problem = _problem("error", "repository_git_unavailable", "repository path is not a git top-level: repos", "repos")
            layout = RepoLayout("direct", (), registry_ready=False, problems=(problem,))
            return RepoLayout("direct", (), registry_ready=False, problems=(problem,), revision=_revision(layout.to_dict()))
        layout = RepoLayout("empty", (), registry_ready=True)
        return RepoLayout("empty", (), registry_ready=True, revision=_revision(layout.to_dict()))
    if "repos" in discovered and len(discovered) > 1:
        raise RepoctlError(f"ambiguous product repositories detected; configure docs/repoctl.json repositories: {', '.join(discovered)}")
    if discovered == ["repos"]:
        targets = (_target(root, "main", "repos", identity_source="reserved"),)
        problems = _nested_git_root_problems(root, {"repos"})
        ready = not problems
        layout = RepoLayout("direct", targets, registry_ready=ready, problems=problems)
        return RepoLayout("direct", targets, registry_ready=ready, problems=problems, revision=_revision(layout.to_dict()))
    candidates = _candidates_for_discovered(root, discovered)
    problems = (*_nested_git_root_problems(root, set(discovered)), *_unowned_product_paths(root, set(discovered)))
    layout = RepoLayout("collection", (), candidates=candidates, registry_ready=False, problems=problems)
    return RepoLayout("collection", (), candidates=candidates, registry_ready=False, problems=problems, revision=_revision(layout.to_dict()))


def repo_layout(root: Path) -> RepoLayout:
    return _layout_from_settings(root, load_repoctl_settings(root))


def default_repo_target(root: Path) -> RepoTarget | None:
    layout = repo_layout(root)
    if not layout.registry_ready:
        raise RepoctlError("repository identities are unbound; run repoctl repo adopt before mutating product repositories", code="repository_identity_unbound")
    if not layout.targets:
        return None
    if len(layout.targets) > 1:
        raise RepoctlError("multiple product repositories configured; pass --repo-id", code="repository_selector_required")
    return layout.targets[0]


def require_repo_target(root: Path, repo_id: str | None = None) -> RepoTarget:
    layout = repo_layout(root)
    if not layout.registry_ready:
        raise RepoctlError("repository identities are unbound; run repoctl repo adopt before mutating product repositories", code="repository_identity_unbound")
    if not layout.targets:
        raise RepoctlError("product repository is missing", code="repository_not_found")
    if repo_id:
        for target in layout.targets:
            if target.id == repo_id:
                return target
        if repo_id == "main" and not layout.targets:
            return RepoTarget("main", root / "repos", "repos", "reserved")
        raise RepoctlError(f"repository not found: {repo_id}", code="repository_not_found")
    if len(layout.targets) > 1:
        raise RepoctlError("multiple product repositories configured; pass --repo-id", code="repository_selector_required")
    return layout.targets[0]


def repository_for_workspace_path(root: Path, path: Path) -> RepoTarget | None:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    for target in repo_layout(root).targets:
        try:
            resolved.relative_to(target.root_path.resolve())
            return target
        except (OSError, ValueError):
            continue
    return None


def repo_check_problems(layout: RepoLayout) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = list(layout.problems)
    for target in layout.targets:
        if not is_git_repo_root(target.root_path):
            problems.append(
                {
                    "severity": "error",
                    "code": "repository_git_unavailable",
                    "message": f"repository path is not a git top-level: {target.display_path}",
                    "path": target.display_path,
                }
            )
    if layout.candidates:
        for candidate in layout.candidates:
            problems.append(
                {
                    "severity": "error",
                    "code": "repository_identity_unbound",
                    "message": f"repository candidate has no pinned stable id: {candidate.display_path}",
                    "path": candidate.display_path,
                }
            )
    return problems


def adopt_repositories(root: Path, *, all_candidates: bool = False, path: str = "", repo_id: str = "") -> RepoLayout:
    layout = repo_layout(root)
    if not layout.candidates:
        raise RepoctlError("no unbound repository candidates to adopt")
    if all_candidates and repo_id:
        raise RepoctlError("--id can only be used when adopting one repository candidate")
    selected: list[RepoCandidate]
    if all_candidates:
        selected = list(layout.candidates)
    else:
        rel = _safe_rel(path)
        selected = [candidate for candidate in layout.candidates if candidate.display_path == rel]
        if not selected:
            raise RepoctlError(f"repository candidate not found: {rel}", code="repository_not_found")
    settings = load_repoctl_settings(root)
    raw_entries = settings.get("repositories", [])
    if raw_entries is None:
        raw_entries = []
    if not isinstance(raw_entries, list):
        raise RepoctlError("docs/repoctl.json repositories must be an array")

    entries: list[dict[str, str]] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            raise RepoctlError("docs/repoctl.json repositories entries must be objects")
        existing_id = item.get("id")
        existing_path = item.get("path")
        if not isinstance(existing_id, str) or not isinstance(existing_path, str):
            raise RepoctlError("repository entries require string id and path")
        folded = _casefold_id(existing_id.strip())
        rel = _safe_rel(existing_path)
        if folded in ids:
            raise RepoctlError(f"duplicate repository id: {existing_id}")
        if rel in paths:
            raise RepoctlError(f"duplicate repository path: {rel}")
        ids.add(folded)
        paths.add(rel)
        entries.append({"id": existing_id.strip(), "path": rel})

    for candidate in selected:
        chosen_id = repo_id.strip() if repo_id else candidate.suggested_id
        if not REPO_ID_RE.match(chosen_id):
            raise RepoctlError(f"invalid repository id: {chosen_id}", code="repository_topology_invalid", path=candidate.display_path)
        folded = _casefold_id(chosen_id)
        if folded in ids:
            raise RepoctlError(f"duplicate repository id: {chosen_id}", code="repository_topology_invalid")
        ids.add(folded)
        if candidate.display_path in paths:
            raise RepoctlError(f"duplicate repository path: {candidate.display_path}", code="repository_topology_invalid")
        paths.add(candidate.display_path)
        entries.append({"id": chosen_id, "path": candidate.display_path})

    new_settings = dict(settings)
    new_settings["repositories"] = entries
    validated = _layout_from_settings(root, new_settings)
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    config_path = docs_dir / "repoctl.json"
    rendered = json.dumps(new_settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write(config_path, rendered)
    return validated
