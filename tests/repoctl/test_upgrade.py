from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tools.repoctl.upgrade import apply_upgrade, plan_upgrade, write_plan
from tests.repoctl.test_meta_check import write_repometa


def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True)
    (root / "docs/archive/tasks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "repos").mkdir()
    (root / "docs/BOARD.md").write_text(
        "# Board\n\n## Board\n\n- docs/tasks/T-20260609120000Z--live.md\n\n## Backlog\n\n<!-- backlog:item BL-abc -->\nraw idea\n<!-- /backlog:item -->\n",
        encoding="utf-8",
    )
    (root / "docs/PRD.md").write_text("project prd\n", encoding="utf-8")
    (root / "docs/tasks/T-20260609120000Z--live.md").write_text("live task\n", encoding="utf-8")
    (root / "docs/archive/tasks/T-20260608120000Z--done.md").write_text("archived task\n", encoding="utf-8")
    (root / "scripts/repoctl").write_text("old repoctl\n", encoding="utf-8")
    (root / "repos/app.py").write_text("print('product')\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("rules\n", encoding="utf-8")


def write_source(root: Path, *, manifest: dict | None = None) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "docs/tasks").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("new repoctl\n", encoding="utf-8")
    (root / "docs/tasks/TEMPLATE.md").write_text("new template\n", encoding="utf-8")
    (root / "repoctl-upgrade-manifest.json").write_text(
        json.dumps(
            manifest
            or {
                "schema_version": 1,
                "package": "agent-workspace-control-plane",
                "version": "0.1.0",
                "replace_paths": ["scripts/repoctl", "docs/tasks/TEMPLATE.md"],
                "preserve_paths": ["repos/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_repoctl_json(workspace: Path, args: list[str]) -> dict:
    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHON"] = sys.executable
    result = subprocess.run(["./scripts/repoctl", *args, "--json"], cwd=workspace, env=env, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    return payload


def test_upgrade_plan_is_read_only_and_reports_managed_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    before = {
        "board": (workspace / "docs/BOARD.md").read_text(encoding="utf-8"),
        "task": (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8"),
        "repos": (workspace / "repos/app.py").read_text(encoding="utf-8"),
    }
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [operation["path"] for operation in payload["data"]["operations"]] == ["docs/tasks/TEMPLATE.md", "scripts/repoctl"]
    assert (workspace / "docs/BOARD.md").read_text(encoding="utf-8") == before["board"]
    assert (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8") == before["task"]
    assert (workspace / "repos/app.py").read_text(encoding="utf-8") == before["repos"]


def test_upgrade_apply_uses_plan_and_preserves_project_state(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(source)
    board_before = (workspace / "docs/BOARD.md").read_text(encoding="utf-8")
    task_before = (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {item["path"] for item in payload["data"]["applied"]} == {"docs/tasks/TEMPLATE.md", "scripts/repoctl"}
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "new repoctl\n"
    assert (workspace / "docs/tasks/TEMPLATE.md").read_text(encoding="utf-8") == "new template\n"


def test_upgrade_apply_rejects_forged_preserved_path_operation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "preserve_paths": ["docs/BOARD.md"],
        },
    )
    (source / "docs/BOARD.md").write_text("pwned\n", encoding="utf-8")
    board_before = (workspace / "docs/BOARD.md").read_text(encoding="utf-8")
    plan = plan_upgrade(workspace, source=source)
    plan["operations"] = [
        {
            "path": "docs/BOARD.md",
            "action": "replace",
            "source_hash": "not-bound-to-manifest",
            "target_hash": "",
            "size": 6,
        }
    ]
    write_plan(plan_file, plan)

    with pytest.raises(Exception):
        apply_upgrade(workspace, plan_file=plan_file)

    assert (workspace / "docs/BOARD.md").read_text(encoding="utf-8") != "pwned\n"
    assert (workspace / "docs/BOARD.md").read_text(encoding="utf-8") == board_before


def test_upgrade_plan_rejects_symlink_parent_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    write_workspace(workspace)
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": ["escape/nested/pwned.txt"],
            "preserve_paths": [],
        },
    )
    (source / "escape/nested").mkdir(parents=True)
    (source / "escape/nested/pwned.txt").write_text("pwned\n", encoding="utf-8")

    with pytest.raises(Exception):
        plan_upgrade(workspace, source=source)

    assert not (outside / "nested/pwned.txt").exists()


def test_upgrade_apply_blocks_stale_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": ["AGENTS.md", "docs/tasks/TEMPLATE.md", "scripts/repoctl"],
            "preserve_paths": ["repos/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
        },
    )
    (source / "AGENTS.md").write_text("new rules\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    (workspace / "scripts/repoctl").write_text("local edit after plan\n", encoding="utf-8")

    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "upgrade_plan_stale"
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "local edit after plan\n"


def test_upgrade_apply_rolls_back_files_when_mid_apply_copy_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": ["AGENTS.md", "docs/tasks/TEMPLATE.md", "scripts/repoctl"],
            "preserve_paths": ["repos/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
        },
    )
    (source / "AGENTS.md").write_text("new rules\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()

    calls = 0

    def flaky_copy(source_path: Path, target_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected copy failure")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())

    monkeypatch.setattr("tools.repoctl.upgrade._atomic_copy_file", flaky_copy)

    with pytest.raises(OSError, match="injected copy failure"):
        apply_upgrade(workspace, plan_file=plan_file)

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "rules\n"
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "old repoctl\n"
    assert not (workspace / "docs/tasks/TEMPLATE.md").exists()
    rollback_files = list((workspace / "docs/tasks/.repoctl-state/upgrades").glob("*/rollback.json"))
    assert len(rollback_files) == 1
    rollback = json.loads(rollback_files[0].read_text(encoding="utf-8"))
    assert [entry["action"] for entry in rollback["rolled_back"]] == ["remove_created", "restore"]


def test_upgrade_manifest_rejects_managed_preserve_overlap(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": ["docs/BOARD.md"],
            "preserve_paths": ["docs/BOARD.md"],
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_upgrade_manifest"


def test_upgrade_create_paths_add_missing_workflow_without_overwriting_existing(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    (workspace / "docs/workflows").mkdir(parents=True)
    (workspace / "docs/workflows/INDEX.md").write_text("local workflow index\n", encoding="utf-8")
    (source / "docs/workflows").mkdir(parents=True)
    (source / "docs/workflows/INDEX.md").write_text("upstream index\n", encoding="utf-8")
    (source / "docs/workflows/repo-metadata.md").write_text("upstream metadata workflow\n", encoding="utf-8")
    (source / "repoctl-upgrade-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "agent-workspace-control-plane",
                "version": "0.1.0",
                "replace_paths": [],
                "create_paths": ["docs/workflows/INDEX.md", "docs/workflows/repo-metadata.md"],
                "preserve_paths": ["repos/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert [operation["path"] for operation in plan_payload["data"]["operations"]] == ["docs/workflows/repo-metadata.md"]
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0

    capsys.readouterr()
    assert (workspace / "docs/workflows/INDEX.md").read_text(encoding="utf-8") == "local workflow index\n"
    assert (workspace / "docs/workflows/repo-metadata.md").read_text(encoding="utf-8") == "upstream metadata workflow\n"


def test_upgrade_apply_exposes_context_and_knowledge_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    plan_file = tmp_path / "plan.json"
    source = Path(__file__).resolve().parents[2]
    write_workspace(workspace)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHON"] = sys.executable
    checks = [
        (["./scripts/repoctl", "context", "--help"], ["pack-benchmark-compare"]),
        (["./scripts/repoctl", "knowledge", "--help"], ["render"]),
        (["./scripts/repoctl", "knowledge", "render", "--help"], ["--check"]),
    ]
    for command, expected in checks:
        result = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        for text in expected:
            assert text in result.stdout


def test_upgrade_apply_supports_pack_to_reviewed_knowledge_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    plan_file = tmp_path / "plan.json"
    source = Path(__file__).resolve().parents[2]
    write_workspace(workspace)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0
    capsys.readouterr()

    subprocess.run(["git", "init"], cwd=workspace / "repos", stdout=subprocess.DEVNULL, check=True)
    write_repometa(workspace / "repos")
    task_id = "T-20260624101010Z"
    pack_path = ".repoctl-state/context-pack/T-20260624101010Z.json"
    (workspace / f"docs/tasks/{task_id}--upgrade-knowledge-flow.md").write_text(
        """---
id: T-20260624101010Z
title: "Upgrade knowledge flow smoke"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T101010Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624101010Z - Upgrade knowledge flow smoke

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: Evidence Context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Promote a context pack into reviewed knowledge after upgrade.

## Handoff

- Next exact step: build candidate from context pack.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260624101010Z.json --repo-id main --json`
- Done when: reviewed knowledge is queryable and render output is current.
""",
        encoding="utf-8",
    )

    pack_payload = run_repoctl_json(workspace, ["context", "pack", "--task", task_id, "--repo-id", "main", "--output", pack_path])
    assert pack_payload["data"]["metrics"]["unique_must_read_source_count"] >= 1

    candidate_payload = run_repoctl_json(workspace, ["knowledge", "candidate", "build", "--from-pack", pack_path, "--repo-id", "main", "--kind", "decision"])
    candidate_id = candidate_payload["data"]["candidate"]["id"]
    assert candidate_payload["data"]["candidate"]["authoritative"] is False

    check_payload = run_repoctl_json(workspace, ["knowledge", "candidate", "check", candidate_id, "--repo-id", "main"])
    assert check_payload["data"]["checks"]["pack_provenance_current"] is True

    approve_payload = run_repoctl_json(workspace, ["knowledge", "approve", candidate_id, "--repo-id", "main"])
    record_id = approve_payload["data"]["record"]["id"]
    assert approve_payload["warnings"] == []

    query_payload = run_repoctl_json(workspace, ["knowledge", "query", "context returns source bundles", "--repo-id", "main"])
    assert query_payload["data"]["results"][0]["record"]["id"] == record_id

    render_payload = run_repoctl_json(workspace, ["knowledge", "render", "--repo-id", "main"])
    assert render_payload["data"]["rendered"]
    render_check_payload = run_repoctl_json(workspace, ["knowledge", "render", "--repo-id", "main", "--check"])
    assert render_check_payload["data"]["check"]["current"] is True
