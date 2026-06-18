from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main


def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True)
    (root / "docs/archive/tasks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "repo").mkdir()
    (root / "docs/BOARD.md").write_text(
        "# Board\n\n## Board\n\n- docs/tasks/T-20260609120000Z--live.md\n\n## Backlog\n\n<!-- backlog:item BL-abc -->\nraw idea\n<!-- /backlog:item -->\n",
        encoding="utf-8",
    )
    (root / "docs/PRD.md").write_text("project prd\n", encoding="utf-8")
    (root / "docs/tasks/T-20260609120000Z--live.md").write_text("live task\n", encoding="utf-8")
    (root / "docs/archive/tasks/T-20260608120000Z--done.md").write_text("archived task\n", encoding="utf-8")
    (root / "scripts/repoctl").write_text("old repoctl\n", encoding="utf-8")
    (root / "repo/app.py").write_text("print('product')\n", encoding="utf-8")
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
                "preserve_paths": ["repo/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_upgrade_plan_is_read_only_and_reports_managed_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    before = {
        "board": (workspace / "docs/BOARD.md").read_text(encoding="utf-8"),
        "task": (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8"),
        "repo": (workspace / "repo/app.py").read_text(encoding="utf-8"),
    }
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [operation["path"] for operation in payload["data"]["operations"]] == ["docs/tasks/TEMPLATE.md", "scripts/repoctl"]
    assert (workspace / "docs/BOARD.md").read_text(encoding="utf-8") == before["board"]
    assert (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8") == before["task"]
    assert (workspace / "repo/app.py").read_text(encoding="utf-8") == before["repo"]


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
    assert (workspace / "docs/BOARD.md").read_text(encoding="utf-8") == board_before
    assert (workspace / "docs/tasks/T-20260609120000Z--live.md").read_text(encoding="utf-8") == task_before
    assert (workspace / "docs/PRD.md").read_text(encoding="utf-8") == "project prd\n"
    assert (workspace / "repo/app.py").read_text(encoding="utf-8") == "print('product')\n"
    assert (workspace / payload["data"]["receipt_path"]).is_file()


def test_upgrade_apply_blocks_stale_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(source)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    (workspace / "scripts/repoctl").write_text("local edit after plan\n", encoding="utf-8")

    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "upgrade_plan_stale"
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "local edit after plan\n"


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
                "preserve_paths": ["repo/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
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
