from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROLE_DIR = ROOT / "ai/roles"
MANIFEST_PATH = ROOT / "ai/generated-manifest.json"
RENDERER_VERSION = 1


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _load_roles() -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for path in sorted(ROLE_DIR.glob("*.yaml")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid JSON-compatible YAML role source: {path.relative_to(ROOT)}: {exc}") from exc
        if not isinstance(data, dict):
            raise SystemExit(f"role source must be an object: {path.relative_to(ROOT)}")
        required = {"name", "description", "tools", "permission_mode", "color", "prompt"}
        missing = sorted(required - set(data))
        if missing:
            raise SystemExit(f"role source is missing {', '.join(missing)}: {path.relative_to(ROOT)}")
        if path.stem != data["name"] or not isinstance(data["tools"], list):
            raise SystemExit(f"role source identity is invalid: {path.relative_to(ROOT)}")
        data["source_path"] = path.relative_to(ROOT).as_posix()
        roles.append(data)
    if not roles:
        raise SystemExit("no canonical role sources found under ai/roles")
    return roles


def _render_claude(role: dict[str, Any]) -> bytes:
    tools = ", ".join(str(tool) for tool in role["tools"])
    text = (
        "---\n"
        f"name: {role['name']}\n"
        f"description: {json.dumps(str(role['description']), ensure_ascii=False)}\n"
        f"tools: {tools}\n"
        f"permissionMode: {role['permission_mode']}\n"
        f"color: {role['color']}\n"
        "---\n\n"
        f"{str(role['prompt']).rstrip()}\n"
    )
    return text.encode("utf-8")


def _render_codex(role: dict[str, Any]) -> bytes:
    text = (
        f"name = {json.dumps(str(role['name']), ensure_ascii=False)}\n"
        f"description = {json.dumps(str(role['description']), ensure_ascii=False)}\n"
        f"permission_mode = {json.dumps(str(role['permission_mode']), ensure_ascii=False)}\n"
        f"tools = {json.dumps([str(tool) for tool in role['tools']], ensure_ascii=False)}\n"
        f"developer_instructions = {json.dumps(str(role['prompt']).rstrip() + chr(10), ensure_ascii=False)}\n"
    )
    return text.encode("utf-8")


def _render_registry(roles: list[dict[str, Any]]) -> bytes:
    lines = [
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        '"""Generated from ai/roles/*.yaml. Do not edit directly."""',
        "",
        "",
        "@dataclass(frozen=True)",
        "class AgentSpec:",
        "    name: str",
        "    kind: str",
        "    tools: tuple[str, ...]",
        '    permission_mode: str = "default"',
        "",
        "",
        "AGENTS: tuple[AgentSpec, ...] = (",
    ]
    for role in roles:
        tools = ", ".join(repr(str(tool)) for tool in role["tools"])
        if len(role["tools"]) == 1:
            tools += ","
        lines.extend(
            [
                "    AgentSpec(",
                f"        name={role['name']!r},",
                "        kind='maintenance-worker',",
                f"        tools=({tools}),",
                f"        permission_mode={str(role['permission_mode'])!r},",
                "    ),",
            ]
        )
    lines.extend([")", "", "AGENTS_BY_NAME: dict[str, AgentSpec] = {agent.name: agent for agent in AGENTS}", ""])
    return "\n".join(lines).encode("utf-8")


def _expected_outputs(roles: list[dict[str, Any]]) -> dict[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    for role in roles:
        name = str(role["name"])
        outputs[ROOT / ".claude/agents" / f"{name}.md"] = _render_claude(role)
        outputs[ROOT / ".codex/agents" / f"{name}.toml"] = _render_codex(role)
    outputs[ROOT / "tools/registries/agent_registry.py"] = _render_registry(roles)
    skill_source = ROOT / ".agents/skills/maintenance-workflow/SKILL.md"
    if skill_source.is_file():
        outputs[ROOT / ".claude/skills/maintenance-workflow/SKILL.md"] = skill_source.read_bytes()
    return outputs


def _manifest(roles: list[dict[str, Any]], outputs: dict[Path, bytes]) -> bytes:
    sources = [ROOT / str(role["source_path"]) for role in roles]
    skill_source = ROOT / ".agents/skills/maintenance-workflow/SKILL.md"
    if skill_source.is_file():
        sources.append(skill_source)
    data = {
        "schema": "repoctl.generated-adapters",
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "sources": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256(path.read_bytes())}
            for path in sorted(sources)
        ],
        "outputs": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256(content)}
            for path, content in sorted(outputs.items(), key=lambda item: item[0].as_posix())
        ],
    }
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render(*, check: bool) -> int:
    roles = _load_roles()
    outputs = _expected_outputs(roles)
    outputs[MANIFEST_PATH] = _manifest(roles, outputs)
    stale = [path for path, content in outputs.items() if not path.is_file() or path.read_bytes() != content]
    expected_paths = set(outputs)
    orphaned = [
        path
        for pattern in (".claude/agents/maintenance-*.md", ".codex/agents/maintenance-*.toml")
        for path in ROOT.glob(pattern)
        if path not in expected_paths
    ]
    if check:
        for path in [*stale, *orphaned]:
            print(path.relative_to(ROOT).as_posix())
        return 1 if stale or orphaned else 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for path in orphaned:
        path.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return render(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
