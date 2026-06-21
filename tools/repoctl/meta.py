from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git import ChangedEntry, repo_changed_entries, repo_git_state
from .io import RepoctlError, atomic_write
from .markdown import parse_frontmatter
from .repositories import RepoTarget, default_repo_target, require_repo_target
from .tasks import Problem

REPOMETA_DIR = ".repometa"
POLICY_FILE = "policy.json"
ANNOTATIONS_DIR = "annotations"
SHARDS = "0123456789abcdef"
INTERNAL_SCAN_SKIP_DIR_NAMES = {
    ".git",
    ".repometa",
}
REQUIRED_ANNOTATION = {"role", "purpose", "topics"}
FORBIDDEN_ANNOTATION_FIELDS = {
    "id",
    "path",
    "language",
    "kind",
    "imports",
    "calls",
    "deps",
    "symbols",
    "observed_effects",
    "relates_to",
    "last_reviewed",
    "version",
}
DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "indexing": {
        "exclude": [
            ".git/**",
            ".repometa/**",
            "**/*.png",
            "**/*.jpg",
            "**/*.jpeg",
            "**/*.gif",
            "**/*.webp",
            "**/*.zip",
            "**/*.tar",
            "**/*.gz",
        ]
    },
    "vocab": {
        "roles": {
            "base": [
                "service",
                "handler",
                "adapter",
                "component",
                "config",
                "test",
                "workflow",
                "migration",
                "spec",
                "script",
            ],
            "extend": [],
        },
        "declared_effects": {
            "base": ["none", "db", "net", "fs", "ui", "time", "crypto", "config"],
            "extend": ["cache", "queue", "email", "sms", "webhook", "push", "third_party"],
        },
    },
    "defaults": {
        "areas": {
            "backend": ["backend/**", "server/**", "api/**"],
            "frontend": ["frontend/**", "web/**"],
            "mobile": ["android/**", "ios/**", "lib/**"],
            "infra": [".github/**", "docker/**", "deploy/**", "**/Dockerfile"],
        },
        "topics": {
            "auth": ["**/auth/**", "**/*token*", "**/*login*"],
            "billing": ["**/billing/**", "**/pricing/**", "**/checkout/**"],
            "tests": ["**/tests/**", "**/*test*", "**/*.spec.*"],
        },
    },
    "coverage": {"require_annotations": []},
}


@dataclass(frozen=True)
class FileClassification:
    path: str
    classification: str
    areas: list[str]
    default_topics: list[str]
    annotation_present: bool
    reason: str
    change: str = ""
    old_path: str = ""
    workspace_path: str = ""

    @property
    def area(self) -> str:
        return self.areas[0] if self.areas else ""

    @property
    def annotation_required(self) -> bool:
        return self.classification == "annotation_required"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "classification": self.classification,
            "areas": self.areas,
            "area": self.area,
            "default_topics": self.default_topics,
            "annotation_present": self.annotation_present,
            "annotation_required": self.annotation_required,
            "reason": self.reason,
        }
        if self.change:
            data["change"] = self.change
        if self.old_path:
            data["old_path"] = self.old_path
        if self.workspace_path:
            data["workspace_path"] = self.workspace_path
        return data


@dataclass(frozen=True)
class DiscoveryCandidate:
    path: str
    workspace_path: str
    classification: str
    score: int
    signals: list[str]
    areas: list[str]
    topics: list[str]
    annotation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "classification": self.classification,
            "score": self.score,
            "signals": self.signals,
            "areas": self.areas,
            "topics": self.topics,
            "annotation_present": self.annotation is not None,
        }
        if self.annotation is not None:
            data["annotation"] = self.annotation
        return data


@dataclass(frozen=True)
class RepoMetadataFacts:
    path: str
    workspace_path: str
    classification: str
    areas: tuple[str, ...]
    policy_topics: tuple[str, ...]
    annotation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "classification": self.classification,
            "areas": list(self.areas),
            "policy_topics": list(self.policy_topics),
            "annotation_present": self.annotation is not None,
        }
        if self.annotation is not None:
            data["annotation"] = self.annotation
        return data


ChangedFileStatus = FileClassification


def _repo(root: Path, target: RepoTarget | None = None) -> Path:
    selected = target or default_repo_target(root)
    if selected is not None:
        return selected.root_path
    if (root / "repos").exists():
        return root / "repos"
    return root / "repos"


def _repo_prefix(root: Path, repo: Path) -> str:
    try:
        return repo.relative_to(root).as_posix()
    except ValueError:
        return repo.as_posix()


def _workspace_path(root: Path, repo: Path, rel: str = "") -> str:
    prefix = _repo_prefix(root, repo)
    return f"{prefix}/{rel}" if rel else prefix


def _repository_meta(root: Path, repo: Path, target: RepoTarget | None = None) -> dict[str, str]:
    if target is not None:
        return target.to_dict()
    return {"id": "main", "path": _repo_prefix(root, repo), "identity_source": "reserved"}


def _meta_dir(repo: Path) -> Path:
    return repo / REPOMETA_DIR


def _policy_path(repo: Path) -> Path:
    return _meta_dir(repo) / POLICY_FILE


def _annotations_dir(repo: Path) -> Path:
    return _meta_dir(repo) / ANNOTATIONS_DIR


def normalize_repo_path(path: str | Path, *, target: RepoTarget | None = None) -> str:
    raw = str(path).strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if target is not None and raw.startswith(f"{target.display_path}/"):
        raw = raw[len(target.display_path) + 1 :]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise RepoctlError("path must be a repo-relative path without '..'")
    return "/".join(parts)


def shard_for_path(path: str) -> str:
    return hashlib.sha256(normalize_repo_path(path).encode("utf-8")).hexdigest()[0]


def _shard_path(repo: Path, shard: str) -> Path:
    if shard not in SHARDS:
        raise RepoctlError(f"invalid annotation shard: {shard}")
    return _annotations_dir(repo) / f"{shard}.json"


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RepoctlError(f"cannot read {path}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RepoctlError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise RepoctlError(f"JSON root must be an object: {path}")
    return data


def _ensure_store(repo: Path) -> None:
    _annotations_dir(repo).mkdir(parents=True, exist_ok=True)
    policy = _policy_path(repo)
    if not policy.exists():
        atomic_write(policy, _json_dumps(DEFAULT_POLICY))


def init_store(root: Path, *, target: RepoTarget | None = None) -> dict[str, Any]:
    repo = _repo(root, target)
    if not repo.is_dir():
        raise RepoctlError("product repository directory is required before initializing .repometa")
    prefix = _repo_prefix(root, repo)
    created: list[str] = []
    _annotations_dir(repo).mkdir(parents=True, exist_ok=True)
    policy = _policy_path(repo)
    if not policy.exists():
        atomic_write(policy, _json_dumps(DEFAULT_POLICY))
        created.append(f"{prefix}/.repometa/policy.json")
    for shard in SHARDS:
        path = _shard_path(repo, shard)
        if not path.exists():
            atomic_write(path, _json_dumps(_empty_shard()))
            created.append(f"{prefix}/.repometa/annotations/{shard}.json")
    return {"created": created, "created_count": len(created), "policy": f"{prefix}/.repometa/policy.json", "annotations_dir": f"{prefix}/.repometa/annotations"}


def _load_policy(repo: Path, problems: list[Problem] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    path = _policy_path(repo)
    location = _workspace_path(root, repo, ".repometa/policy.json") if root is not None else "repos/.repometa/policy.json"
    if not path.exists():
        if problems is not None:
            problems.append(Problem("error", "missing_repometa_policy", f"{location} is required", location))
            return {}
        raise RepoctlError(f"{location} is required")
    try:
        return _load_json(path)
    except RepoctlError as exc:
        if problems is not None:
            problems.append(Problem("error", "invalid_policy_json", str(exc), location))
            return {}
        raise


def _empty_shard() -> dict[str, Any]:
    return {"schema_version": 1, "annotations": {}, "exclusions": {}}


def _load_shard(repo: Path, shard: str, *, create: bool = False) -> dict[str, Any]:
    path = _shard_path(repo, shard)
    if not path.exists():
        if create:
            return _empty_shard()
        return _empty_shard()
    data = _load_json(path)
    data.setdefault("schema_version", 1)
    data.setdefault("annotations", {})
    data.setdefault("exclusions", {})
    if not isinstance(data.get("annotations"), dict):
        raise RepoctlError(f"annotations must be an object: {path}")
    if not isinstance(data.get("exclusions"), dict):
        raise RepoctlError(f"exclusions must be an object: {path}")
    return data


def _write_shard(repo: Path, shard: str, data: dict[str, Any]) -> None:
    data = {
        "schema_version": 1,
        "annotations": dict(sorted((data.get("annotations") or {}).items())),
        "exclusions": dict(sorted((data.get("exclusions") or {}).items())),
    }
    atomic_write(_shard_path(repo, shard), _json_dumps(data))


def _all_shard_paths(repo: Path) -> list[Path]:
    directory = _annotations_dir(repo)
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _load_all_annotations(repo: Path, problems: list[Problem] | None = None, *, root: Path | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]]:
    annotations: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, dict[str, Any]] = {}
    annotation_shards: dict[str, list[str]] = {}
    exclusion_shards: dict[str, list[str]] = {}
    for path in _all_shard_paths(repo):
        shard = path.stem
        shard_location = _workspace_path(root, repo, f".repometa/annotations/{path.name}") if root is not None else f"repos/.repometa/annotations/{path.name}"
        if shard not in SHARDS:
            if problems is not None:
                problems.append(Problem("error", "invalid_shard_name", "annotation shard must be one of 0.json..f.json", shard_location))
                continue
            raise RepoctlError(f"invalid annotation shard: {path.name}")
        try:
            data = _load_shard(repo, shard)
        except RepoctlError as exc:
            if problems is not None:
                problems.append(Problem("error", "invalid_shard_json", str(exc), shard_location))
                continue
            raise
        for raw_key, raw_value in (data.get("annotations") or {}).items():
            try:
                key = normalize_repo_path(raw_key)
            except RepoctlError:
                key = str(raw_key)
                if problems is not None:
                    problems.append(Problem("error", "invalid_annotation_path", "annotation path must be normalized repo-relative path", shard_location))
            if key != raw_key and problems is not None:
                problems.append(Problem("error", "non_normalized_annotation_path", "annotation path key is not normalized", shard_location))
            if not isinstance(raw_value, dict):
                if problems is not None:
                    problems.append(Problem("error", "invalid_annotation", "annotation value must be an object", _workspace_path(root, repo, key) if root is not None else f"repos/{key}"))
                continue
            annotations.setdefault(key, raw_value)
            annotation_shards.setdefault(key, []).append(shard)
        for raw_key, raw_value in (data.get("exclusions") or {}).items():
            try:
                key = normalize_repo_path(raw_key)
            except RepoctlError:
                key = str(raw_key)
                if problems is not None:
                    problems.append(Problem("error", "invalid_exclusion_path", "exclusion path must be normalized repo-relative path", shard_location))
            if key != raw_key and problems is not None:
                problems.append(Problem("error", "non_normalized_exclusion_path", "exclusion path key is not normalized", shard_location))
            if not isinstance(raw_value, dict):
                if problems is not None:
                    problems.append(Problem("error", "invalid_exclusion", "exclusion value must be an object", _workspace_path(root, repo, key) if root is not None else f"repos/{key}"))
                continue
            exclusions.setdefault(key, raw_value)
            exclusion_shards.setdefault(key, []).append(shard)
    return annotations, exclusions, annotation_shards, exclusion_shards


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _mapping_of_lists(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, patterns in value.items():
        result[str(key)] = _as_list(patterns)
    return result


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _policy_excludes(policy: dict[str, Any]) -> list[str]:
    indexing = policy.get("indexing", {})
    if not isinstance(indexing, dict):
        return []
    return _as_list(indexing.get("exclude"))


def _areas_for(path: str, policy: dict[str, Any]) -> list[str]:
    defaults = policy.get("defaults", {}) if isinstance(policy.get("defaults"), dict) else {}
    result: list[str] = []
    for area, patterns in _mapping_of_lists(defaults.get("areas")).items():
        if _match_any(path, patterns):
            result.append(area)
    return result


def _topics_for(path: str, policy: dict[str, Any], annotation: dict[str, Any] | None = None) -> list[str]:
    defaults = policy.get("defaults", {}) if isinstance(policy.get("defaults"), dict) else {}
    result: list[str] = []
    for topic, patterns in _mapping_of_lists(defaults.get("topics")).items():
        if _match_any(path, patterns):
            result.append(topic)
    if annotation:
        for topic in _as_list(annotation.get("topics")):
            if topic not in result:
                result.append(topic)
    return result


def _coverage_reason(path: str, policy: dict[str, Any]) -> str:
    coverage = policy.get("coverage", {}) if isinstance(policy.get("coverage"), dict) else {}
    rules = coverage.get("require_annotations", [])
    if not isinstance(rules, list):
        return ""
    for rule in rules:
        if isinstance(rule, str):
            if fnmatch.fnmatch(path, rule):
                return f"matched coverage pattern {rule}"
            continue
        if isinstance(rule, dict):
            patterns = _as_list(rule.get("patterns"))
            if _match_any(path, patterns):
                return str(rule.get("reason") or f"matched coverage pattern {patterns[0] if patterns else ''}").strip()
    return ""


def _coverage_patterns(policy: dict[str, Any]) -> list[str]:
    coverage = policy.get("coverage", {}) if isinstance(policy.get("coverage"), dict) else {}
    rules = coverage.get("require_annotations", [])
    if not isinstance(rules, list):
        return []
    patterns: list[str] = []
    for rule in rules:
        if isinstance(rule, str):
            patterns.append(rule)
        elif isinstance(rule, dict):
            patterns.extend(_as_list(rule.get("patterns")))
    return [pattern for pattern in patterns if pattern]


def _patterns_overlap(left: str, right: str) -> bool:
    if left == right or fnmatch.fnmatch(left, right) or fnmatch.fnmatch(right, left):
        return True
    left_prefix = _literal_glob_prefix(left)
    right_prefix = _literal_glob_prefix(right)
    return bool(left_prefix and right_prefix and (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)))


def _literal_glob_prefix(pattern: str) -> str:
    wildcard_positions = [position for token in "*?[" if (position := pattern.find(token)) != -1]
    prefix = pattern[: min(wildcard_positions)] if wildcard_positions else pattern
    if "/" in prefix:
        prefix = prefix.rsplit("/", 1)[0] + "/"
    return prefix


def _vocab(policy: dict[str, Any], name: str) -> set[str]:
    vocab = policy.get("vocab", {}) if isinstance(policy.get("vocab"), dict) else {}
    config = vocab.get(name, {}) if isinstance(vocab.get(name), dict) else {}
    return set(_as_list(config.get("base"))) | set(_as_list(config.get("extend")))


def _policy_scan_skip_dir_names(policy: dict[str, Any]) -> set[str]:
    names = set(INTERNAL_SCAN_SKIP_DIR_NAMES)
    for pattern in _policy_excludes(policy):
        if pattern.endswith("/**") and "/" not in pattern[:-3] and "*" not in pattern[:-3] and "?" not in pattern[:-3] and "[" not in pattern[:-3]:
            names.add(pattern[:-3])
    return names


def _list_repo_files(repo: Path, policy: dict[str, Any]) -> list[str]:
    files: list[str] = []
    skip_dir_names = _policy_scan_skip_dir_names(policy)
    for path in repo.rglob("*"):
        try:
            rel_path = path.relative_to(repo)
        except ValueError:
            continue
        if any(part in skip_dir_names for part in rel_path.parts):
            continue
        if not path.is_file():
            continue
        rel = rel_path.as_posix()
        files.append(rel)
    return sorted(files)


def _classify(path: str, policy: dict[str, Any], annotations: dict[str, dict[str, Any]], exclusions: dict[str, dict[str, Any]], *, change: str = "", old_path: str = "") -> FileClassification:
    rel = normalize_repo_path(path)
    annotation = annotations.get(rel)
    if _match_any(rel, _policy_excludes(policy)):
        return FileClassification(rel, "excluded", [], [], bool(annotation), "matched indexing.exclude", change, old_path)
    if rel in exclusions:
        return FileClassification(rel, "excluded_override", _areas_for(rel, policy), _topics_for(rel, policy, annotation), bool(annotation), str(exclusions[rel].get("reason") or "explicit exclusion override"), change, old_path)
    if annotation is not None:
        return FileClassification(rel, "annotated", _areas_for(rel, policy), _topics_for(rel, policy, annotation), True, "annotation present", change, old_path)
    required = _coverage_reason(rel, policy)
    if required:
        return FileClassification(rel, "annotation_required", _areas_for(rel, policy), _topics_for(rel, policy), False, required, change, old_path)
    return FileClassification(rel, "indexed_only", _areas_for(rel, policy), _topics_for(rel, policy), False, "not covered by annotation policy", change, old_path)


def _with_workspace_path(root: Path, repo: Path, file: FileClassification) -> FileClassification:
    return FileClassification(
        file.path,
        file.classification,
        file.areas,
        file.default_topics,
        file.annotation_present,
        file.reason,
        file.change,
        file.old_path,
        _workspace_path(root, repo, file.path),
    )


def _changed_files(root: Path, target: RepoTarget | None = None) -> list[ChangedEntry]:
    return repo_changed_entries(root, target)[0]


def _changed_git_problem(root: Path, target: RepoTarget | None = None) -> Problem | None:
    repo = _repo(root, target)
    if not repo.exists():
        return None
    state = repo_git_state(root, target)
    if state.available:
        return None
    repo_path = _repo_prefix(root, repo)
    return Problem(
        "error",
        "repo_git_unavailable",
        f"{repo_path}/ is expected to be an independent git repository; changed-file metadata gate cannot run safely",
        f"{repo_path}/",
    )


def _validate_policy(policy: dict[str, Any], *, location: str = "repos/.repometa/policy.json") -> list[Problem]:
    problems: list[Problem] = []
    if policy.get("schema_version") != 1:
        problems.append(Problem("error", "invalid_policy_schema_version", "policy.json schema_version must be 1", location))
    allowed_top = {"schema_version", "indexing", "vocab", "defaults", "coverage"}
    for key in sorted(set(policy) - allowed_top):
        problems.append(Problem("error", "unknown_policy_field", f"unknown policy field: {key}", location))
    indexing = policy.get("indexing", {})
    if indexing and not isinstance(indexing, dict):
        problems.append(Problem("error", "invalid_policy_indexing", "indexing must be an object", location))
    elif isinstance(indexing, dict):
        exclude = indexing.get("exclude", [])
        if not isinstance(exclude, list) or not all(isinstance(item, str) and item for item in exclude):
            problems.append(Problem("error", "invalid_policy_indexing", "indexing.exclude must be a string array", location))
        for key in sorted(set(indexing) - {"exclude"}):
            problems.append(Problem("error", "unknown_policy_field", f"unknown indexing field: {key}", location))
    vocab = policy.get("vocab", {})
    if vocab and not isinstance(vocab, dict):
        problems.append(Problem("error", "invalid_policy_vocab", "vocab must be an object", location))
    elif isinstance(vocab, dict):
        for name in ("roles", "declared_effects"):
            config = vocab.get(name, {})
            if config and not isinstance(config, dict):
                problems.append(Problem("error", "invalid_policy_vocab", f"vocab.{name} must be an object", location))
                continue
            if isinstance(config, dict):
                for field in ("base", "extend"):
                    values = config.get(field, [])
                    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                        problems.append(Problem("error", "invalid_policy_vocab", f"vocab.{name}.{field} must be a string array", location))
                for key in sorted(set(config) - {"base", "extend"}):
                    problems.append(Problem("error", "unknown_policy_field", f"unknown vocab.{name} field: {key}", location))
        for key in sorted(set(vocab) - {"roles", "declared_effects"}):
            problems.append(Problem("error", "unknown_policy_field", f"unknown vocab field: {key}", location))
    defaults = policy.get("defaults", {})
    if defaults and not isinstance(defaults, dict):
        problems.append(Problem("error", "invalid_policy_defaults", "defaults must be an object", location))
    elif isinstance(defaults, dict):
        for field in ("areas", "topics"):
            mapping = defaults.get(field, {})
            if mapping and not isinstance(mapping, dict):
                problems.append(Problem("error", "invalid_policy_defaults", f"defaults.{field} must be an object", location))
                continue
            if isinstance(mapping, dict):
                for name, patterns in mapping.items():
                    if not isinstance(name, str) or not name:
                        problems.append(Problem("error", "invalid_policy_defaults", f"defaults.{field} keys must be non-empty strings", location))
                    if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
                        problems.append(Problem("error", "invalid_policy_defaults", f"defaults.{field}.{name} must be a non-empty string array", location))
        for key in sorted(set(defaults) - {"areas", "topics"}):
            problems.append(Problem("error", "unknown_policy_field", f"unknown defaults field: {key}", location))
    coverage = policy.get("coverage", {})
    if coverage and not isinstance(coverage, dict):
        problems.append(Problem("error", "invalid_policy_coverage", "coverage must be an object", location))
    elif isinstance(coverage, dict):
        rules = coverage.get("require_annotations", [])
        if not isinstance(rules, list):
            problems.append(Problem("error", "invalid_policy_coverage", "coverage.require_annotations must be an array", location))
        else:
            for index, rule in enumerate(rules):
                if isinstance(rule, str):
                    if not rule:
                        problems.append(Problem("error", "invalid_policy_coverage", f"coverage.require_annotations[{index}] must be non-empty", location))
                elif isinstance(rule, dict):
                    patterns = rule.get("patterns")
                    if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
                        problems.append(Problem("error", "invalid_policy_coverage", f"coverage.require_annotations[{index}].patterns must be a non-empty string array", location))
                    if "reason" in rule and not isinstance(rule.get("reason"), str):
                        problems.append(Problem("error", "invalid_policy_coverage", f"coverage.require_annotations[{index}].reason must be a string", location))
                    for key in sorted(set(rule) - {"patterns", "reason"}):
                        problems.append(Problem("error", "unknown_policy_field", f"unknown coverage rule field: {key}", location))
                else:
                    problems.append(Problem("error", "invalid_policy_coverage", f"coverage.require_annotations[{index}] must be a string or object", location))
        for key in sorted(set(coverage) - {"require_annotations"}):
            problems.append(Problem("error", "unknown_policy_field", f"unknown coverage field: {key}", location))
    if isinstance(indexing, dict) and isinstance(indexing.get("exclude", []), list):
        excludes = [pattern for pattern in indexing.get("exclude", []) if isinstance(pattern, str)]
        for required in _coverage_patterns(policy):
            for excluded in excludes:
                if _patterns_overlap(required, excluded):
                    problems.append(Problem("error", "policy_coverage_excluded", f"coverage pattern {required} is also excluded by indexing pattern {excluded}", location))
    return problems


def _validate_annotation(path: str, data: dict[str, Any], policy: dict[str, Any], *, workspace_path: str | None = None) -> list[Problem]:
    problems: list[Problem] = []
    location = workspace_path or f"repos/{path}"
    missing = sorted(field for field in REQUIRED_ANNOTATION if field not in data or data[field] in ("", [], None))
    if missing:
        problems.append(Problem("error", "missing_annotation_fields", f"missing required annotation fields: {', '.join(missing)}", location))
    forbidden = sorted(set(data) & FORBIDDEN_ANNOTATION_FIELDS)
    if forbidden:
        problems.append(Problem("error", "forbidden_annotation_field", f"forbidden annotation fields: {', '.join(forbidden)}", location))
    allowed = REQUIRED_ANNOTATION | {"declared_effects", "caution"}
    unknown = sorted(set(data) - allowed - FORBIDDEN_ANNOTATION_FIELDS)
    if unknown:
        problems.append(Problem("error", "unknown_annotation_field", f"unknown annotation fields: {', '.join(unknown)}", location))
    roles = _vocab(policy, "roles")
    role = str(data.get("role") or "")
    if role and roles and role not in roles:
        problems.append(Problem("error", "invalid_role", f"role is not in policy vocab: {role}", location))
    topics = data.get("topics")
    if topics is not None and (not isinstance(topics, list) or not all(isinstance(item, str) and item for item in topics)):
        problems.append(Problem("error", "invalid_topics", "topics must be a non-empty string array", location))
    effects = data.get("declared_effects")
    if effects is not None:
        allowed_effects = _vocab(policy, "declared_effects")
        values = _as_list(effects)
        if not isinstance(effects, list) or not all(isinstance(item, str) and item for item in effects):
            problems.append(Problem("error", "invalid_declared_effects", "declared_effects must be a string array", location))
        invalid = sorted(set(values) - allowed_effects)
        if invalid:
            problems.append(Problem("error", "invalid_declared_effects", f"declared_effects not in policy vocab: {', '.join(invalid)}", location))
        if "none" in values and len(values) > 1:
            problems.append(Problem("error", "invalid_declared_effects_none_combo", "declared_effects cannot combine none with other effects", location))
    caution = data.get("caution")
    if caution is not None and (not isinstance(caution, list) or not all(isinstance(item, str) and item for item in caution)):
        problems.append(Problem("error", "invalid_caution", "caution must be a string array", location))
    purpose = data.get("purpose")
    if purpose is not None and (not isinstance(purpose, str) or not purpose.strip()):
        problems.append(Problem("error", "invalid_purpose", "purpose must be a non-empty string", location))
    return problems


def _residue_problem(root: Path, repo: Path, rel: str) -> Problem | None:
    path = repo / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if "@meta" in text:
        return Problem("error", "inline_meta_residue", "inline @meta/frontmatter metadata is not allowed; use .repometa", _workspace_path(root, repo, rel))
    if rel.endswith(('.md', '.markdown')):
        frontmatter, _body = parse_frontmatter(text)
        if {"role", "purpose", "topics"} & set(frontmatter):
            return Problem("error", "inline_meta_residue", "inline @meta/frontmatter metadata is not allowed; use .repometa", _workspace_path(root, repo, rel))
    return None


def meta_inventory(root: Path, *, changed: bool = False, changes: list[ChangedEntry] | None = None, target: RepoTarget | None = None) -> tuple[list[FileClassification], list[Problem], dict[str, Any]]:
    repo = _repo(root, target)
    repository = _repository_meta(root, repo, target)
    if not repo.exists():
        summary: dict[str, int] = {key: 0 for key in ["total", "excluded", "annotated", "annotation_required", "indexed_only", "excluded_override", "orphan_annotation", "orphan_exclusion", "move_candidate"]}
        return [], [], {"scope": "changed" if changed else "all", "summary": summary, "repository": repository}
    problems: list[Problem] = []
    git_problem = _changed_git_problem(root, target) if changed else None
    if git_problem:
        summary: dict[str, int] = {key: 0 for key in ["total", "excluded", "annotated", "annotation_required", "indexed_only", "excluded_override", "orphan_annotation", "orphan_exclusion", "move_candidate"]}
        return [], [git_problem], {"scope": "changed", "summary": summary, "repository": repository, "repo_git": {"available": False, "reason": git_problem.code}}
    raw_changes = changes if changes is not None else (_changed_files(root, target) if changed else [])
    if changed and not raw_changes:
        summary: dict[str, int] = {key: 0 for key in ["total", "excluded", "annotated", "annotation_required", "indexed_only", "excluded_override", "orphan_annotation", "orphan_exclusion", "move_candidate"]}
        return [], [], {"scope": "changed", "summary": summary, "repository": repository}
    policy = _load_policy(repo, problems, root=root)
    policy_location = _workspace_path(root, repo, ".repometa/policy.json")
    problems.extend(_validate_policy(policy, location=policy_location) if policy else [])
    annotations, exclusions, annotation_shards, exclusion_shards = _load_all_annotations(repo, problems, root=root)
    files: list[FileClassification] = []
    existing = set(_list_repo_files(repo, policy))
    if changed:
        for change, path, old_path in raw_changes:
            if change == "deleted":
                if path in annotations:
                    classification = FileClassification(path, "orphan_annotation", [], [], True, "deleted file has annotation", change, old_path)
                elif path in exclusions:
                    classification = FileClassification(path, "orphan_exclusion", [], [], False, "deleted file has exclusion", change, old_path)
                else:
                    classification = FileClassification(path, "excluded", [], [], False, "deleted file", change, old_path)
            elif change == "renamed":
                base = _classify(path, policy, annotations, exclusions, change=change, old_path=old_path)
                if old_path in annotations or old_path in exclusions:
                    base = FileClassification(path, "move_candidate", base.areas, base.default_topics, base.annotation_present, f"annotation exists at old path {old_path}", change, old_path)
                classification = base
            else:
                classification = _classify(path, policy, annotations, exclusions, change=change, old_path=old_path)
            files.append(_with_workspace_path(root, repo, classification))
    else:
        for path in existing:
            files.append(_with_workspace_path(root, repo, _classify(path, policy, annotations, exclusions)))
    if not changed:
        for path in sorted(set(annotations) - existing):
            files.append(_with_workspace_path(root, repo, FileClassification(path, "orphan_annotation", [], [], True, "annotation exists for missing file")))
        for path in sorted(set(exclusions) - existing):
            files.append(_with_workspace_path(root, repo, FileClassification(path, "orphan_exclusion", [], [], False, "exclusion exists for missing file")))
    summary: dict[str, int] = {key: 0 for key in ["total", "excluded", "annotated", "annotation_required", "indexed_only", "excluded_override", "orphan_annotation", "orphan_exclusion", "move_candidate"]}
    summary["total"] = len(files)
    for file in files:
        summary[file.classification] = summary.get(file.classification, 0) + 1
    meta = {"scope": "changed" if changed else "all", "summary": summary, "repository": repository}
    return files, problems, meta


def read_metadata_facts(root: Path, *, target: RepoTarget) -> tuple[list[RepoMetadataFacts], list[Problem], dict[str, Any]]:
    files, problems, meta = meta_inventory(root, changed=False, target=target)
    repo = _repo(root, target)
    if not repo.exists() or problems:
        return [], problems, meta
    policy = _load_policy(repo, problems, root=root)
    if not policy:
        return [], problems, meta
    annotations, _exclusions, _annotation_shards, _exclusion_shards = _load_all_annotations(repo, problems, root=root)
    facts: list[RepoMetadataFacts] = []
    for file in files:
        if file.classification in {"orphan_annotation", "orphan_exclusion"}:
            continue
        annotation = annotations.get(file.path)
        facts.append(
            RepoMetadataFacts(
                path=file.path,
                workspace_path=file.workspace_path,
                classification=file.classification,
                areas=tuple(sorted(set(file.areas))),
                policy_topics=tuple(sorted(set(_topics_for(file.path, policy)))),
                annotation=dict(annotation) if annotation is not None else None,
            )
        )
    facts.sort(key=lambda item: item.path)
    return facts, problems, meta


def meta_status(root: Path, *, changed: bool = False, changes: list[ChangedEntry] | None = None, target: RepoTarget | None = None) -> tuple[list[FileClassification], list[Problem], dict[str, Any]]:
    return meta_inventory(root, changed=changed, changes=changes, target=target)


def check_meta(root: Path, *, changed: bool = False, changes: list[ChangedEntry] | None = None, target: RepoTarget | None = None) -> list[Problem]:
    repo = _repo(root, target)
    if not repo.exists():
        return []
    problems: list[Problem] = []
    git_problem = _changed_git_problem(root, target) if changed else None
    if git_problem:
        return [git_problem]
    changed_related: set[str] = set()
    if changed:
        changed_files = changes if changes is not None else _changed_files(root, target)
        if not changed_files:
            return []
        for change, path, old_path in changed_files:
            changed_related.add(path)
            if old_path:
                changed_related.add(old_path)
    policy = _load_policy(repo, problems, root=root)
    policy_location = _workspace_path(root, repo, ".repometa/policy.json")
    problems.extend(_validate_policy(policy, location=policy_location) if policy else [])
    annotations, exclusions, annotation_shards, exclusion_shards = _load_all_annotations(repo, problems, root=root)
    existing = set(_list_repo_files(repo, policy))
    for path, shards in sorted(annotation_shards.items()):
        if changed and path not in changed_related:
            continue
        expected = shard_for_path(path)
        workspace_path = _workspace_path(root, repo, path)
        if len(shards) > 1:
            problems.append(Problem("error", "duplicate_annotation_path", "same annotation path appears in multiple shards", workspace_path))
        for shard in shards:
            if shard != expected:
                problems.append(Problem("error", "wrong_annotation_shard", f"annotation belongs in shard {expected}.json", _workspace_path(root, repo, f".repometa/annotations/{shard}.json")))
    for path, shards in sorted(exclusion_shards.items()):
        if changed and path not in changed_related:
            continue
        expected = shard_for_path(path)
        workspace_path = _workspace_path(root, repo, path)
        if len(shards) > 1:
            problems.append(Problem("error", "duplicate_exclusion_path", "same exclusion path appears in multiple shards", workspace_path))
        for shard in shards:
            if shard != expected:
                problems.append(Problem("error", "wrong_exclusion_shard", f"exclusion belongs in shard {expected}.json", _workspace_path(root, repo, f".repometa/annotations/{shard}.json")))
    for path, data in sorted(annotations.items()):
        if changed and path not in changed_related:
            continue
        problems.extend(_validate_annotation(path, data, policy, workspace_path=_workspace_path(root, repo, path)))
    for path, data in sorted(exclusions.items()):
        if changed and path not in changed_related:
            continue
        allowed = {"reason", "excluded_by"}
        unknown = sorted(set(data) - allowed)
        workspace_path = _workspace_path(root, repo, path)
        if unknown:
            problems.append(Problem("error", "unknown_exclusion_field", f"unknown exclusion fields: {', '.join(unknown)}", workspace_path))
        if not data.get("reason"):
            problems.append(Problem("error", "missing_exclusion_reason", "exclusion reason is required", workspace_path))
    inventory, inventory_problems, _meta = meta_inventory(root, changed=changed, changes=changes, target=target)
    problems.extend(inventory_problems)
    considered_paths = {file.path for file in inventory if not changed or file.change}
    for file in inventory:
        if file.classification == "annotation_required":
            problems.append(Problem("error", "annotation_required", f"file matches coverage rule: {file.reason}", file.workspace_path or _workspace_path(root, repo, file.path)))
        elif file.classification == "move_candidate":
            problems.append(Problem("error", "move_candidate", f"metadata move requires explicit repoctl meta move from {file.old_path}", file.workspace_path or _workspace_path(root, repo, file.path)))
        elif file.classification == "orphan_annotation" and (not changed or file.path in considered_paths):
            problems.append(Problem("error", "orphan_annotation", file.reason, file.workspace_path or _workspace_path(root, repo, file.path)))
        elif file.classification == "orphan_exclusion" and (not changed or file.path in considered_paths):
            problems.append(Problem("error", "orphan_exclusion", file.reason, file.workspace_path or _workspace_path(root, repo, file.path)))
    residue_scope = considered_paths if changed else existing
    for rel in sorted(residue_scope):
        if _match_any(rel, _policy_excludes(policy)):
            continue
        problem = _residue_problem(root, repo, rel)
        if problem:
            problems.append(problem)
    seen: set[tuple[str, str, str]] = set()
    unique: list[Problem] = []
    for problem in problems:
        key = (problem.code, problem.path, problem.message)
        if key not in seen:
            seen.add(key)
            unique.append(problem)
    return unique


def show_annotation(root: Path, path: str, *, target: RepoTarget | None = None) -> dict[str, Any]:
    repo = _repo(root, target)
    rel = normalize_repo_path(path, target=target)
    shard = shard_for_path(rel)
    data = _load_shard(repo, shard)
    annotation = (data.get("annotations") or {}).get(rel)
    exclusion = (data.get("exclusions") or {}).get(rel)
    return {"path": rel, "workspace_path": _workspace_path(root, repo, rel), "repository": _repository_meta(root, repo, target), "shard": shard, "annotation": annotation, "exclusion": exclusion}


def _annotation_topics(annotation: dict[str, Any] | None) -> list[str]:
    if not annotation:
        return []
    topics = annotation.get("topics")
    return [str(topic) for topic in topics if str(topic).strip()] if isinstance(topics, list) else []


def _annotation_effects(annotation: dict[str, Any] | None) -> list[str]:
    if not annotation:
        return []
    effects = annotation.get("declared_effects")
    return [str(effect) for effect in effects if str(effect).strip()] if isinstance(effects, list) else []


def _candidate_from_file(file: FileClassification, annotation: dict[str, Any] | None, *, score: int, signals: list[str]) -> DiscoveryCandidate:
    topics = sorted(set(file.default_topics + _annotation_topics(annotation)))
    return DiscoveryCandidate(
        path=file.path,
        workspace_path=file.workspace_path,
        classification=file.classification,
        score=score,
        signals=signals,
        areas=file.areas,
        topics=topics,
        annotation=annotation,
    )


def _visible_for_discovery(file: FileClassification) -> bool:
    return file.classification not in {"excluded", "excluded_override", "orphan_annotation", "orphan_exclusion"}


def meta_query(
    root: Path,
    *,
    role: str = "",
    topics: list[str] | None = None,
    area: str = "",
    effects: list[str] | None = None,
    limit: int = 50,
    target: RepoTarget | None = None,
) -> tuple[list[DiscoveryCandidate], list[Problem], dict[str, Any]]:
    files, problems, meta = meta_inventory(root, changed=False, target=target)
    repo = _repo(root, target)
    if not repo.exists():
        return [], problems, {**meta, "query": {"role": role, "topics": topics or [], "area": area, "effects": effects or [], "limit": limit}}
    annotations, _exclusions, _annotation_shards, _exclusion_shards = _load_all_annotations(repo, problems, root=root)
    wanted_role = role.strip().lower()
    wanted_topics = {topic.strip().lower() for topic in (topics or []) if topic.strip()}
    wanted_area = area.strip().lower()
    wanted_effects = {effect.strip().lower() for effect in (effects or []) if effect.strip()}
    candidates: list[DiscoveryCandidate] = []
    for file in files:
        if not _visible_for_discovery(file):
            continue
        annotation = annotations.get(file.path)
        role_value = str((annotation or {}).get("role") or "").lower()
        file_topics = {topic.lower() for topic in file.default_topics + _annotation_topics(annotation)}
        file_areas = {area_value.lower() for area_value in file.areas}
        file_effects = {effect.lower() for effect in _annotation_effects(annotation)}
        signals: list[str] = []
        if wanted_role:
            if role_value != wanted_role:
                continue
            signals.append(f"role:{role_value}")
        if wanted_topics:
            matched = sorted(wanted_topics & file_topics)
            if not matched:
                continue
            signals.extend(f"topic:{topic}" for topic in matched)
        if wanted_area:
            if wanted_area not in file_areas:
                continue
            signals.append(f"area:{wanted_area}")
        if wanted_effects:
            matched = sorted(wanted_effects & file_effects)
            if not matched:
                continue
            signals.extend(f"effect:{effect}" for effect in matched)
        if not signals:
            signals.append("match:all-indexed")
        score = len(signals) * 10 + (5 if annotation else 0)
        candidates.append(_candidate_from_file(file, annotation, score=score, signals=signals))
    candidates.sort(key=lambda item: (-item.score, item.path))
    return candidates[: max(0, limit)], problems, {**meta, "query": {"role": role, "topics": topics or [], "area": area, "effects": effects or [], "limit": limit}}


def _text_tokens(text: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _identifier_tokens(text: str) -> set[str]:
    # Split path/code identifiers on separators and common camel-case boundaries.
    spaced = re.sub(r"(?<=[0-9A-Za-z])(?=[A-Z][a-z])", " ", text)
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", spaced)
    return set(_text_tokens(spaced))


def _annotation_text(annotation: dict[str, Any] | None) -> str:
    if not annotation:
        return ""
    values: list[str] = []
    for key in ("role", "purpose"):
        value = annotation.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("topics", "declared_effects", "caution"):
        value = annotation.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
    return " ".join(values)


def meta_suggest(root: Path, *, text: str, limit: int = 20, target: RepoTarget | None = None) -> tuple[list[DiscoveryCandidate], list[Problem], dict[str, Any]]:
    query = text.strip()
    if not query:
        raise RepoctlError("--text is required")
    tokens = _text_tokens(query)
    files, problems, meta = meta_inventory(root, changed=False, target=target)
    repo = _repo(root, target)
    if not repo.exists():
        return [], problems, {**meta, "suggestion": {"text": query, "tokens": tokens, "limit": limit, "authoritative": False}}
    annotations, _exclusions, _annotation_shards, _exclusion_shards = _load_all_annotations(repo, problems, root=root)
    candidates: list[DiscoveryCandidate] = []
    for file in files:
        if not _visible_for_discovery(file):
            continue
        annotation = annotations.get(file.path)
        path_tokens = _identifier_tokens(file.path)
        basename_tokens = _identifier_tokens(Path(file.path).name)
        annotation_tokens = _identifier_tokens(_annotation_text(annotation))
        default_tokens = _identifier_tokens(" ".join(file.areas + file.default_topics))
        signals: list[str] = []
        score = 0
        for token in tokens:
            if token in basename_tokens:
                score += 8
                signals.append(f"filename:{token}")
            elif token in path_tokens:
                score += 5
                signals.append(f"path:{token}")
            if annotation and token in annotation_tokens:
                score += 10
                signals.append(f"annotation:{token}")
            if token in default_tokens:
                score += 4
                signals.append(f"policy-default:{token}")
        if annotation and score:
            score += 3
        if not score:
            continue
        candidates.append(_candidate_from_file(file, annotation, score=score, signals=sorted(set(signals))))
    candidates.sort(key=lambda item: (-item.score, item.path))
    return candidates[: max(0, limit)], problems, {**meta, "suggestion": {"text": query, "tokens": tokens, "limit": limit, "authoritative": False}}


def set_annotation(root: Path, path: str, *, role: str, purpose: str, topics: list[str], declared_effects: list[str] | None = None, caution: list[str] | None = None, target: RepoTarget | None = None) -> dict[str, Any]:
    repo = _repo(root, target)
    rel = normalize_repo_path(path, target=target)
    if not (repo / rel).is_file():
        raise RepoctlError(f"repo path does not exist: {rel}")
    shard = shard_for_path(rel)
    annotation: dict[str, Any] = {"role": role, "purpose": purpose.strip(), "topics": [topic for topic in topics if topic]}
    if declared_effects:
        annotation["declared_effects"] = [effect for effect in declared_effects if effect]
    if caution:
        annotation["caution"] = [item for item in caution if item]
    policy = _load_policy(repo, root=root)
    problems = _validate_annotation(rel, annotation, policy, workspace_path=_workspace_path(root, repo, rel))
    errors = [problem for problem in problems if problem.severity == "error"]
    if errors:
        raise RepoctlError(errors[0].message)
    _ensure_store(repo)
    data = _load_shard(repo, shard, create=True)
    data.setdefault("annotations", {})[rel] = annotation
    data.setdefault("exclusions", {}).pop(rel, None)
    _write_shard(repo, shard, data)
    return {"path": rel, "workspace_path": _workspace_path(root, repo, rel), "repository": _repository_meta(root, repo, target), "shard": shard, "annotation": annotation}


def remove_annotation(root: Path, path: str, *, target: RepoTarget | None = None) -> dict[str, Any]:
    repo = _repo(root, target)
    rel = normalize_repo_path(path, target=target)
    shard = shard_for_path(rel)
    data = _load_shard(repo, shard)
    removed = data.setdefault("annotations", {}).pop(rel, None)
    removed_exclusion = data.setdefault("exclusions", {}).pop(rel, None)
    if removed is None and removed_exclusion is None:
        raise RepoctlError("annotation or exclusion not found")
    _write_shard(repo, shard, data)
    return {"path": rel, "workspace_path": _workspace_path(root, repo, rel), "repository": _repository_meta(root, repo, target), "shard": shard, "removed_annotation": removed is not None, "removed_exclusion": removed_exclusion is not None}


def move_annotation(root: Path, old_path: str, new_path: str, *, target: RepoTarget | None = None) -> dict[str, Any]:
    repo = _repo(root, target)
    old = normalize_repo_path(old_path, target=target)
    new = normalize_repo_path(new_path, target=target)
    old_shard = shard_for_path(old)
    new_shard = shard_for_path(new)
    old_data = _load_shard(repo, old_shard)
    annotation = old_data.setdefault("annotations", {}).get(old)
    if annotation is None:
        raise RepoctlError("old annotation not found")
    new_data = old_data if old_shard == new_shard else _load_shard(repo, new_shard, create=True)
    if new in new_data.setdefault("annotations", {}):
        raise RepoctlError("new annotation already exists")
    new_data["annotations"][new] = annotation
    _write_shard(repo, new_shard, new_data)
    if old_shard == new_shard:
        new_data["annotations"].pop(old, None)
        _write_shard(repo, new_shard, new_data)
    else:
        old_data["annotations"].pop(old, None)
        _write_shard(repo, old_shard, old_data)
    return {"old_path": old, "new_path": new, "old_workspace_path": _workspace_path(root, repo, old), "new_workspace_path": _workspace_path(root, repo, new), "repository": _repository_meta(root, repo, target), "old_shard": old_shard, "new_shard": new_shard}


def exclude_path(root: Path, path: str, *, reason: str, excluded_by: str = "agent", target: RepoTarget | None = None) -> dict[str, Any]:
    if not reason.strip():
        raise RepoctlError("--reason is required")
    repo = _repo(root, target)
    rel = normalize_repo_path(path, target=target)
    if not (repo / rel).is_file():
        raise RepoctlError(f"repo path does not exist: {rel}")
    _ensure_store(repo)
    shard = shard_for_path(rel)
    data = _load_shard(repo, shard, create=True)
    data.setdefault("exclusions", {})[rel] = {"reason": reason.strip(), "excluded_by": excluded_by}
    data.setdefault("annotations", {}).pop(rel, None)
    _write_shard(repo, shard, data)
    return {"path": rel, "workspace_path": _workspace_path(root, repo, rel), "repository": _repository_meta(root, repo, target), "shard": shard, "exclusion": data["exclusions"][rel]}
