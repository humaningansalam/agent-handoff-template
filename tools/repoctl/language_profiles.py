from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LanguageProfile:
    id: str
    display_name: str
    suffixes: tuple[str, ...] = ()
    filenames: tuple[str, ...] = ()
    manifest_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    capability: str = "inventory evidence"
    semantic_source: bool = False
    context_source: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationHint:
    command: str
    source_path: str
    provider: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "source_path": self.source_path,
            "provider": self.provider,
            "reason": self.reason,
        }


LANGUAGE_PROFILES: tuple[LanguageProfile, ...] = (
    LanguageProfile(
        id="python",
        display_name="Python",
        suffixes=(".py",),
        manifest_patterns=("pyproject.toml", "pytest.ini", "tox.ini", "noxfile.py", "requirements.txt", "requirements-*.txt"),
        exclude_patterns=(".venv/**", "venv/**", "env/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**", ".tox/**", ".nox/**", "*.egg-info/**", "__pycache__/**", "**/__pycache__/**", "*.pyc", "*.pyo", "**/*.pyc", "**/*.pyo"),
        capability="Python AST semantic provider",
        semantic_source=True,
        context_source=True,
    ),
    LanguageProfile(
        id="javascript",
        display_name="JavaScript",
        suffixes=(".js", ".jsx", ".mjs", ".cjs"),
        manifest_patterns=("package.json", "jsconfig.json", "vite.config.*", "next.config.*", "playwright.config.*"),
        exclude_patterns=("node_modules/**", ".next/**", ".nuxt/**", ".svelte-kit/**", ".turbo/**", ".parcel-cache/**", ".firebase/**", ".playwright-browsers/**", "dist/**", "build/**", "coverage/**", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"),
        capability="TypeScript compiler semantic provider when Node.js is available",
        semantic_source=True,
        context_source=True,
    ),
    LanguageProfile(
        id="typescript",
        display_name="TypeScript",
        suffixes=(".ts", ".tsx", ".mts", ".cts"),
        manifest_patterns=("package.json", "tsconfig.json", "jsconfig.json", "vite.config.*", "next.config.*", "playwright.config.*"),
        exclude_patterns=("node_modules/**", ".next/**", ".nuxt/**", ".svelte-kit/**", ".turbo/**", ".parcel-cache/**", ".firebase/**", ".playwright-browsers/**", "dist/**", "build/**", "coverage/**", "*.tsbuildinfo", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"),
        capability="TypeScript compiler semantic provider when Node.js is available",
        semantic_source=True,
        context_source=True,
    ),
    LanguageProfile(
        id="dart",
        display_name="Dart/Flutter",
        suffixes=(".dart",),
        manifest_patterns=("pubspec.yaml", "analysis_options.yaml"),
        exclude_patterns=(".dart_tool/**", "build/**", ".flutter-plugins", ".flutter-plugins-dependencies"),
        capability="Dart analysis server semantic provider when the Dart SDK is available",
        semantic_source=True,
        context_source=True,
    ),
    LanguageProfile(
        id="csharp",
        display_name="C#/Unity",
        suffixes=(".cs",),
        manifest_patterns=("*.csproj", "*.sln", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt", "Assets/**/*.asmdef"),
        exclude_patterns=("Library/**", "Temp/**", "Obj/**", "obj/**", "bin/**", "Build/**", "Builds/**", "Logs/**", "UserSettings/**", "MemoryCaptures/**"),
        capability="Roslyn semantic provider when Mono and compiler APIs are available",
        semantic_source=True,
        context_source=True,
    ),
    LanguageProfile(id="go", display_name="Go", suffixes=(".go",), manifest_patterns=("go.mod",), exclude_patterns=("vendor/**", "bin/**", "coverage/**"), capability="inventory evidence", semantic_source=True, context_source=True),
    LanguageProfile(id="rust", display_name="Rust", suffixes=(".rs",), manifest_patterns=("Cargo.toml",), exclude_patterns=("target/**", "coverage/**"), capability="inventory evidence", semantic_source=True, context_source=True),
    LanguageProfile(id="java", display_name="Java/Kotlin", suffixes=(".java", ".kt", ".kts"), manifest_patterns=("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), exclude_patterns=(".gradle/**", "build/**", "target/**", "out/**"), capability="inventory evidence", semantic_source=True, context_source=True),
    LanguageProfile(id="swift", display_name="Swift", suffixes=(".swift",), manifest_patterns=("Package.swift", "*.xcodeproj/project.pbxproj", "*.xcworkspace/contents.xcworkspacedata"), exclude_patterns=(".build/**", "DerivedData/**"), capability="inventory evidence", semantic_source=True, context_source=True),
    LanguageProfile(id="shell", display_name="Shell", suffixes=(".sh", ".bash", ".zsh"), capability="inventory evidence", semantic_source=True, context_source=True),
    LanguageProfile(id="sql", display_name="SQL", suffixes=(".sql",), capability="structured SQL relation provider", semantic_source=True, context_source=True),
    LanguageProfile(id="html", display_name="HTML", suffixes=(".html", ".htm"), capability="text context evidence", context_source=True),
    LanguageProfile(id="stylesheet", display_name="Stylesheets", suffixes=(".css", ".scss", ".sass", ".less"), capability="text context evidence", context_source=True),
    LanguageProfile(id="markdown", display_name="Markdown", suffixes=(".md", ".markdown"), capability="document evidence"),
    LanguageProfile(id="json", display_name="JSON", suffixes=(".json",), capability="manifest/config evidence"),
    LanguageProfile(id="toml", display_name="TOML", suffixes=(".toml",), capability="manifest/config evidence"),
    LanguageProfile(id="yaml", display_name="YAML", suffixes=(".yaml", ".yml"), capability="manifest/config evidence"),
)

COMMON_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".git/**",
    ".repometa/**",
    ".repoctl-state/**",
    ".cache/**",
    ".gstack/**",
    "*.lock",
    "*.log",
    "**/*.log",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.zip",
    "**/*.tar",
    "**/*.gz",
)

LANGUAGE_BY_SUFFIX = {suffix: profile.id for profile in LANGUAGE_PROFILES for suffix in profile.suffixes}
LANGUAGE_BY_FILENAME = {filename: profile.id for profile in LANGUAGE_PROFILES for filename in profile.filenames}
PROFILE_BY_ID = {profile.id: profile for profile in LANGUAGE_PROFILES}

DART_IMPORT_RE = re.compile(r"\bimport\s+['\"]([^'\"]+)['\"]")
def default_indexing_excludes() -> list[str]:
    patterns: list[str] = [*COMMON_EXCLUDE_PATTERNS]
    for profile in LANGUAGE_PROFILES:
        patterns.extend(profile.exclude_patterns)
    return _dedupe(patterns)


def product_manifest_patterns() -> list[str]:
    patterns: list[str] = []
    for profile in LANGUAGE_PROFILES:
        patterns.extend(profile.manifest_patterns)
    return _dedupe(patterns)


def language_for_path(path: str) -> str:
    name = Path(path).name
    lowered_name = name.casefold()
    if lowered_name == "dockerfile" or lowered_name.startswith("dockerfile."):
        return "dockerfile"
    if name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[name]
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "unknown")


def graph_language_capabilities(languages: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for language in sorted(language for language in languages if language):
        profile = PROFILE_BY_ID.get(language)
        if profile is None:
            result[language] = {"capability": "inventory evidence", "semantic_source": False, "context_source": False, "notes": ["No registered language profile."]}
            continue
        result[language] = {
            "display_name": profile.display_name,
            "capability": profile.capability,
            "semantic_source": profile.semantic_source,
            "context_source": profile.context_source,
            "notes": list(profile.notes),
        }
    return result


def is_semantic_source_language(language: str) -> bool:
    profile = PROFILE_BY_ID.get(language)
    return bool(profile and profile.semantic_source)


def is_context_source_language(language: str) -> bool:
    profile = PROFILE_BY_ID.get(language)
    return bool(profile and profile.context_source)


def index_dart(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    imports = [match.group(1) for match in DART_IMPORT_RE.finditer(text)]
    return [], _dedupe(imports), [], "ok", ""


def collect_verification_hints(repo: Path) -> list[VerificationHint]:
    hints: list[VerificationHint] = []
    hints.extend(_package_json_hints(repo))
    hints.extend(_python_hints(repo))
    hints.extend(_dart_flutter_hints(repo))
    hints.extend(_unity_hints(repo))
    hints.extend(_go_hints(repo))
    hints.extend(_rust_hints(repo))
    return sorted({(hint.command, hint.source_path, hint.provider, hint.reason): hint for hint in hints}.values(), key=lambda item: (item.source_path, item.command))


def _package_json_hints(repo: Path) -> list[VerificationHint]:
    path = repo / "package.json"
    data = _read_json(path)
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    commands: list[VerificationHint] = []
    for script in ("test", "lint", "typecheck", "check", "build", "e2e"):
        if isinstance(scripts.get(script), str):
            command = "npm test" if script == "test" else f"npm run {script}"
            commands.append(VerificationHint(command, "package.json", "javascript_package_manifest", f"package.json script `{script}`"))
    return commands


def _python_hints(repo: Path) -> list[VerificationHint]:
    hints: list[VerificationHint] = []
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            data = {}
        tool = data.get("tool") if isinstance(data, dict) else {}
        project = data.get("project") if isinstance(data, dict) else {}
        dependency_groups = data.get("dependency-groups") if isinstance(data, dict) else {}
        dependency_values: list[str] = []
        if isinstance(project, dict):
            dependency_values.extend(str(value) for value in project.get("dependencies", []) if isinstance(value, str))
            optional = project.get("optional-dependencies")
            if isinstance(optional, dict):
                dependency_values.extend(str(value) for values in optional.values() if isinstance(values, list) for value in values if isinstance(value, str))
        if isinstance(dependency_groups, dict):
            dependency_values.extend(str(value) for values in dependency_groups.values() if isinstance(values, list) for value in values if isinstance(value, str))
        pytest_configured = isinstance(tool, dict) and isinstance(tool.get("pytest"), dict)
        pytest_declared = any(re.match(r"^pytest(?:\W|$)", value.strip(), re.IGNORECASE) for value in dependency_values)
        if pytest_configured or pytest_declared:
            hints.append(VerificationHint("uv run pytest", "pyproject.toml", "python_project_manifest", "pytest is explicitly configured or declared"))
        if isinstance(tool, dict) and "ruff" in tool:
            hints.append(VerificationHint("uv run ruff check .", "pyproject.toml", "python_project_manifest", "tool.ruff configured"))
        if isinstance(tool, dict) and "mypy" in tool:
            hints.append(VerificationHint("uv run mypy .", "pyproject.toml", "python_project_manifest", "tool.mypy configured"))
    if (repo / "pytest.ini").is_file():
        hints.append(VerificationHint("pytest", "pytest.ini", "python_project_manifest", "pytest.ini present"))
    return hints


def _dart_flutter_hints(repo: Path) -> list[VerificationHint]:
    pubspec = repo / "pubspec.yaml"
    if not pubspec.is_file():
        return []
    text = _read_text(pubspec)
    command = "flutter" if "flutter:" in text or (repo / "lib/main.dart").is_file() else "dart"
    return [
        VerificationHint(f"{command} test", "pubspec.yaml", "dart_flutter_manifest", "Dart/Flutter package manifest present"),
        VerificationHint(f"{command} analyze", "pubspec.yaml", "dart_flutter_manifest", "Dart/Flutter package manifest present"),
    ]


def _unity_hints(repo: Path) -> list[VerificationHint]:
    if not (repo / "ProjectSettings").exists() and not (repo / "Assets").exists():
        return []
    source = "ProjectSettings/ProjectVersion.txt" if (repo / "ProjectSettings/ProjectVersion.txt").is_file() else "Assets"
    return [VerificationHint("Unity Test Runner", source, "unity_project_layout", "Unity layout detected; run EditMode/PlayMode tests with the project Unity version")]


def _go_hints(repo: Path) -> list[VerificationHint]:
    return [VerificationHint("go test ./...", "go.mod", "go_module", "Go module manifest present")] if (repo / "go.mod").is_file() else []


def _rust_hints(repo: Path) -> list[VerificationHint]:
    return [VerificationHint("cargo test", "Cargo.toml", "rust_manifest", "Rust package manifest present")] if (repo / "Cargo.toml").is_file() else []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
