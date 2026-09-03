from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tools.repoctl.io import RepoctlError
from tools.repoctl.knowledge_projection import knowledge_projection_path
from tools.repoctl.tasks import archive_locator_text
from tools.repoctl.upgrade import apply_upgrade, plan_upgrade, upgrade_status, write_plan
from tests.repoctl.knowledge_test_helpers import (
    _approve_knowledge_source,
    _setup_knowledge_workspace,
)
from tests.repoctl.meta.test_meta_check import write_repometa


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
    (root / "docs/tasks/T-20260609120000Z--live.md").write_text(
        "---\nid: T-20260609120000Z\ntitle: Live task\nstatus: doing\nowner: unassigned\ncreated: 20260609T120000Z\nparent: ''\ndepends_on: []\n---\n",
        encoding="utf-8",
    )
    (root / "docs/archive/tasks/T-20260608120000Z--done.md").write_text(
        "---\nid: T-20260608120000Z\ntitle: Done task\nstatus: done\nowner: unassigned\ncreated: 20260608T120000Z\nparent: ''\ndepends_on: []\n---\n",
        encoding="utf-8",
    )
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


def add_legacy_follow_up(workspace: Path, *, previous_id: str = "T-20260608120000Z") -> tuple[Path, Path]:
    live = workspace / "docs/tasks/T-20260609120000Z--live.md"
    live.write_text(
        live.read_text(encoding="utf-8").replace(
            "depends_on: []",
            f'follow_up_of: "{previous_id}"\ndepends_on: []',
        ),
        encoding="utf-8",
    )
    archive = workspace / f"docs/archive/tasks/{previous_id}--done.md"
    locator = workspace / f"docs/tasks/.repoctl-state/archive/{previous_id}.json"
    return archive, locator


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


def test_upgrade_plan_rejects_same_version_drift_without_writing_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(source)
    version = "0.1.0"
    (workspace / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (workspace / "repoctl-upgrade-manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": version}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "same_version_managed_content_drift"
    assert not plan_file.exists()
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "old repoctl\n"


def test_upgrade_same_version_plan_treats_current_archive_locator_as_noop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    archive, locator = add_legacy_follow_up(workspace)
    locator.parent.mkdir(parents=True)
    locator.write_text(
        archive_locator_text("T-20260608120000Z", archive.relative_to(workspace).as_posix()),
        encoding="utf-8",
    )
    (workspace / "scripts/repoctl").write_bytes((source / "scripts/repoctl").read_bytes())
    (workspace / "docs/tasks/TEMPLATE.md").write_bytes((source / "docs/tasks/TEMPLATE.md").read_bytes())
    (workspace / "repoctl-upgrade-manifest.json").write_bytes((source / "repoctl-upgrade-manifest.json").read_bytes())

    plan = plan_upgrade(workspace, source=source)

    assert plan["operations"] == []
    assert plan["migrations"] == []


def test_upgrade_plan_accepts_terminal_predecessor_retained_in_task_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    archive, _locator = add_legacy_follow_up(workspace)
    retained = workspace / "docs/tasks/T-20260608120000Z--done.md"
    retained.write_text(
        archive.read_text(encoding="utf-8").replace("status: done", "status: canceled"),
        encoding="utf-8",
    )
    archive.unlink()

    plan = plan_upgrade(workspace, source=source)

    assert plan["conflicts"] == []
    assert plan["migrations"] == []


def test_upgrade_apply_uses_plan_and_preserves_project_state(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(source)
    state_path = workspace / "docs/tasks/.repoctl-state/T-20260609120000Z.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema": "repoctl.task.state",
                "schema_version": 3,
                "task_id": "T-20260609120000Z",
                "initial": {
                    "created": "20260609T120000Z",
                    "repo_id": "main",
                    "repo_path": "repos",
                    "git_toplevel": (workspace / "repos").as_posix(),
                    "start_head": "a" * 40,
                    "dirty_entries": [],
                    "dirty_path_fingerprints": {},
                },
                "ownership": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {item["path"] for item in payload["data"]["applied"]} == {"docs/tasks/TEMPLATE.md", "scripts/repoctl"}
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "new repoctl\n"
    assert (workspace / "docs/tasks/TEMPLATE.md").read_text(encoding="utf-8") == "new template\n"
    preserved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert preserved_state["schema_version"] == 3
    assert preserved_state["initial"]["start_head"] == "a" * 40

    assert main(["upgrade", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["data"]["latest"]["backup"]["availability"] == "available"

    receipt_path = workspace / payload["data"]["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    invalid_receipts = []
    for invalid_path in ("", "README.md"):
        damaged = json.loads(json.dumps(receipt))
        damaged["backup"]["path"] = invalid_path
        invalid_receipts.append(damaged)
    damaged = json.loads(json.dumps(receipt))
    damaged["backups"][0]["backup_path"] = "README.md"
    invalid_receipts.append(damaged)
    damaged = json.loads(json.dumps(receipt))
    damaged["backups"] = []
    invalid_receipts.append(damaged)

    for damaged in invalid_receipts:
        receipt_path.write_text(json.dumps(damaged) + "\n", encoding="utf-8")
        assert main(["upgrade", "status", "--json"]) == 1
        invalid_status = json.loads(capsys.readouterr().out)
        assert [problem["code"] for problem in invalid_status["problems"]] == [
            "upgrade_receipt_invalid"
        ]

    damaged = json.loads(json.dumps(receipt))
    damaged["backups"][0]["backup_digest"] = "sha256:" + "0" * 64
    receipt_path.write_text(json.dumps(damaged) + "\n", encoding="utf-8")
    assert main(["upgrade", "status", "--json"]) == 0
    damaged_status = json.loads(capsys.readouterr().out)
    assert damaged_status["data"]["latest"]["backup"]["availability"] == "digest_mismatch"


def test_upgrade_status_reads_legacy_individual_backups_without_claiming_digest_verification(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    write_workspace(workspace)
    run_id = "20260618025741Z"
    backup_rel = f"docs/tasks/.repoctl-state/upgrades/{run_id}/backup/tools/repoctl/tasks.py"
    backup_path = workspace / backup_rel
    backup_path.parent.mkdir(parents=True)
    backup_path.write_text("old tasks module\n", encoding="utf-8")
    receipt_path = workspace / f"docs/tasks/.repoctl-state/upgrades/{run_id}/receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "backups": [
                    {
                        "path": "tools/repoctl/tasks.py",
                        "backup_path": backup_rel,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status, problems = upgrade_status(workspace)

    assert problems == []
    assert status["latest"]["backup"]["availability"] == "digest_unavailable"

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["backups"][0]["backup_path"] = "README.md"
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")

    status, problems = upgrade_status(workspace)

    assert [problem["code"] for problem in problems] == ["upgrade_receipt_invalid"]
    assert status["latest"]["backup"]["availability"] == "invalid"

    receipt["backups"][0]["backup_path"] = backup_rel
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    outside_run_id = "20260618025742Z"
    outside_run = tmp_path / "outside-upgrade-run"
    outside_run.mkdir()
    (outside_run / "receipt.json").write_text(
        json.dumps(
            {
                "run_id": outside_run_id,
                "backups": [],
                "backup": {
                    "path": f"docs/tasks/.repoctl-state/upgrades/{outside_run_id}/backup",
                    "recorded_digest": "",
                    "retention_status_at_creation": "not_required",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    linked_run = workspace / f"docs/tasks/.repoctl-state/upgrades/{outside_run_id}"
    linked_run.symlink_to(outside_run, target_is_directory=True)

    status, problems = upgrade_status(workspace)

    assert [problem["code"] for problem in problems] == ["upgrade_receipt_invalid"]
    assert status["receipt_count"] == 1

    assert main(
        [
            "upgrade",
            "postflight",
            "--workspace-root",
            workspace.as_posix(),
            "--json",
        ]
    ) == 1
    postflight = json.loads(capsys.readouterr().out)
    assert any(
        problem["code"] == "upgrade_receipt_invalid"
        for problem in postflight["problems"]
    )

    linked_run.unlink()
    receipt_path.write_bytes(b"\xff")
    _status, problems = upgrade_status(workspace)
    assert [problem["code"] for problem in problems] == ["upgrade_receipt_invalid"]


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


def test_upgrade_apply_blocks_stale_archive_migration_before_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    write_source(source)
    archive, locator = add_legacy_follow_up(workspace)
    plan = plan_upgrade(workspace, source=source)
    assert len(plan["migrations"]) == 1
    write_plan(plan_file, plan)

    archive.write_text(archive.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RepoctlError) as error:
        apply_upgrade(workspace, plan_file=plan_file)

    assert error.value.code == "upgrade_plan_stale"
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "old repoctl\n"
    assert not locator.exists()


def test_upgrade_apply_rolls_back_files_when_receipt_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
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
    _archive, locator = add_legacy_follow_up(workspace)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    capsys.readouterr()

    real_atomic_write = __import__("tools.repoctl.upgrade", fromlist=["atomic_write"]).atomic_write

    def fail_receipt(path: Path, text: str) -> None:
        if path.name == "receipt.json":
            raise OSError("injected receipt failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.upgrade.atomic_write", fail_receipt)

    with pytest.raises(OSError):
        apply_upgrade(workspace, plan_file=plan_file)

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "rules\n"
    assert (workspace / "scripts/repoctl").read_text(encoding="utf-8") == "old repoctl\n"
    assert not (workspace / "docs/tasks/TEMPLATE.md").exists()
    assert not locator.exists()
    rollback_files = list((workspace / "docs/tasks/.repoctl-state/upgrades").glob("*/rollback.json"))
    assert len(rollback_files) == 1
    rollback = json.loads(rollback_files[0].read_text(encoding="utf-8"))
    assert [entry["action"] for entry in rollback["rolled_back"]] == [
        "remove_created",
        "restore",
        "remove_created",
        "restore",
    ]


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


def test_upgrade_remove_paths_delete_only_manifested_legacy_files(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    (workspace / "docs/adr").mkdir(parents=True)
    (workspace / "docs/plans").mkdir(parents=True)
    (workspace / "docs/adr/repoctl-graph-v0.md").write_text("old graph adr\n", encoding="utf-8")
    (workspace / "docs/plans/repoctl-graph-roadmap.md").write_text("old graph plan\n", encoding="utf-8")
    prd_before = (workspace / "docs/PRD.md").read_text(encoding="utf-8")
    repo_before = (workspace / "repos/app.py").read_text(encoding="utf-8")
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "remove_paths": ["docs/adr/repoctl-graph-v0.md", "docs/plans/repoctl-graph-roadmap.md"],
            "preserve_paths": ["repos/**", "docs/BOARD.md", "docs/PRD.md", "docs/tasks/T-*.md", "docs/archive/tasks/**"],
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert [(operation["action"], operation["path"]) for operation in plan_payload["data"]["operations"]] == [
        ("remove", "docs/adr/repoctl-graph-v0.md"),
        ("remove", "docs/plans/repoctl-graph-roadmap.md"),
    ]
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [(operation["action"], operation["path"]) for operation in payload["data"]["applied"]] == [
        ("remove", "docs/adr/repoctl-graph-v0.md"),
        ("remove", "docs/plans/repoctl-graph-roadmap.md"),
    ]
    assert not (workspace / "docs/adr/repoctl-graph-v0.md").exists()
    assert not (workspace / "docs/plans/repoctl-graph-roadmap.md").exists()
    assert not (workspace / "docs/plans").exists()
    assert (workspace / "docs/PRD.md").read_text(encoding="utf-8") == prd_before
    assert (workspace / "repos/app.py").read_text(encoding="utf-8") == repo_before
    assert len(list((workspace / "docs/tasks/.repoctl-state/upgrades").glob("*/backup/docs/adr/repoctl-graph-v0.md"))) == 1


def test_upgrade_workspace_root_allows_source_runner_to_clean_target_workspace(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    (workspace / "docs/adr").mkdir(parents=True)
    (workspace / "docs/adr/repoctl-graph-v0.md").write_text("old graph adr\n", encoding="utf-8")
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "remove_paths": ["docs/adr/repoctl-graph-v0.md"],
            "preserve_paths": ["docs/PRD.md", "repos/**"],
        },
    )

    assert main(["upgrade", "plan", "--workspace-root", str(workspace), "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert plan_payload["data"]["workspace_root"] == workspace.resolve().as_posix()
    assert [(operation["action"], operation["path"]) for operation in plan_payload["data"]["operations"]] == [("remove", "docs/adr/repoctl-graph-v0.md")]
    assert main(["upgrade", "apply", "--workspace-root", str(workspace), "--plan-file", str(plan_file), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert not (workspace / "docs/adr/repoctl-graph-v0.md").exists()


def test_upgrade_remove_paths_cannot_target_preserved_files(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "remove_paths": ["docs/PRD.md"],
            "preserve_paths": ["docs/PRD.md", "repos/**"],
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_upgrade_manifest"
    assert (workspace / "docs/PRD.md").read_text(encoding="utf-8") == "project prd\n"


def test_upgrade_remove_paths_reject_directory_targets(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    write_workspace(workspace)
    (workspace / "docs/plans").mkdir(parents=True)
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "remove_paths": ["docs/plans"],
            "preserve_paths": ["docs/PRD.md", "repos/**"],
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["conflicts"][0]["code"] == "remove_target_not_file"
    assert (workspace / "docs/plans").is_dir()


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


def test_upgrade_seeds_preserved_gitkeep_slots_without_touching_adopter_files(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    plan_file = tmp_path / "plan.json"
    write_workspace(workspace)
    (workspace / "docs/knowledge/records").mkdir(parents=True)
    (workspace / "docs/knowledge/events").mkdir(parents=True)
    (workspace / "docs/knowledge/records/K-adopter.json").write_text('{"id":"K-adopter"}\n', encoding="utf-8")
    (workspace / "docs/knowledge/events/E-adopter.json").write_text('{"id":"E-adopter"}\n', encoding="utf-8")
    write_source(
        source,
        manifest={
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": [],
            "create_paths": [],
            "preserve_paths": [
                "docs/knowledge/records/.gitkeep",
                "docs/knowledge/records/**",
                "docs/knowledge/events/.gitkeep",
                "docs/knowledge/events/**",
                "docs/PRD.md",
                "repos/**",
            ],
        },
    )
    (source / "docs/knowledge/records").mkdir(parents=True)
    (source / "docs/knowledge/events").mkdir(parents=True)
    (source / "docs/knowledge/records/.gitkeep").write_text("", encoding="utf-8")
    (source / "docs/knowledge/events/.gitkeep").write_text("", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "plan", "--from", str(source), "--output", str(plan_file), "--json"]) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert [(operation["action"], operation["path"]) for operation in plan_payload["data"]["operations"]] == [
        ("seed_preserve", "docs/knowledge/events/.gitkeep"),
        ("seed_preserve", "docs/knowledge/records/.gitkeep"),
    ]
    assert main(["upgrade", "apply", "--plan-file", str(plan_file), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert (workspace / "docs/knowledge/records/.gitkeep").is_file()
    assert (workspace / "docs/knowledge/events/.gitkeep").is_file()
    assert (workspace / "docs/knowledge/records/K-adopter.json").read_text(encoding="utf-8") == '{"id":"K-adopter"}\n'
    assert (workspace / "docs/knowledge/events/E-adopter.json").read_text(encoding="utf-8") == '{"id":"E-adopter"}\n'


def test_upgrade_apply_rejects_forged_preserve_seed_for_non_gitkeep_path(tmp_path: Path) -> None:
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
            "preserve_paths": ["docs/PRD.md", "repos/**"],
        },
    )
    (source / "docs/PRD.md").write_text("pwned\n", encoding="utf-8")
    prd_before = (workspace / "docs/PRD.md").read_text(encoding="utf-8")
    plan = plan_upgrade(workspace, source=source)
    plan["operations"] = [
        {
            "path": "docs/PRD.md",
            "action": "seed_preserve",
            "source_hash": "not-bound-to-preserve-seed",
            "target_hash": "",
            "size": 6,
        }
    ]
    write_plan(plan_file, plan)

    with pytest.raises(Exception):
        apply_upgrade(workspace, plan_file=plan_file)

    assert (workspace / "docs/PRD.md").read_text(encoding="utf-8") == prd_before


def test_upgrade_apply_exposes_context_and_knowledge_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    plan_file = tmp_path / "plan.json"
    source = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
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
        (["./scripts/repoctl", "context", "--help"], ["query", "pack"]),
        (["./scripts/repoctl", "graph", "--help"], ["build", "query"]),
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
    source = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
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

- `docs/contracts/repoctl-context-contract.md`

## Discovery

- Candidate query: repoctl Context contract
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Promote a context pack into reviewed knowledge after upgrade.

## Handoff

- Next exact step: build candidate from context pack.
- First file to open: `docs/contracts/repoctl-context-contract.md`
- First command to run: `./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260624101010Z.json --repo-id main --claim 'Reviewed Context remains non-authoritative.' --json`
- Done when: reviewed knowledge is queryable and render output is current.
""",
        encoding="utf-8",
    )

    pack_payload = run_repoctl_json(workspace, ["context", "pack", "--task", task_id, "--repo-id", "main", "--output", pack_path])
    assert pack_payload["data"]["metrics"]["unique_must_read_source_count"] >= 1

    candidate_payload = run_repoctl_json(workspace, ["knowledge", "candidate", "build", "--from-pack", pack_path, "--repo-id", "main", "--kind", "decision", "--claim", "Reviewed Context remains non-authoritative."])
    candidate_id = candidate_payload["data"]["candidate"]["id"]
    assert candidate_payload["data"]["candidate"]["authoritative"] is False

    check_payload = run_repoctl_json(workspace, ["knowledge", "candidate", "check", candidate_id, "--repo-id", "main"])
    assert check_payload["data"]["checks"]["pack_provenance_current"] is True

    approve_payload = run_repoctl_json(workspace, ["knowledge", "approve", candidate_id, "--repo-id", "main"])
    record_id = approve_payload["data"]["record"]["id"]
    assert approve_payload["warnings"] == []

    query_payload = run_repoctl_json(workspace, ["knowledge", "query", "context returns source bundles", "--repo-id", "main"])
    assert query_payload["data"]["results"][0]["record"]["id"] == record_id

    render_payload = run_repoctl_json(workspace, ["knowledge", "render", "--repo-id", "main", "--full"])
    assert render_payload["data"]["rendered"]
    render_check_payload = run_repoctl_json(workspace, ["knowledge", "render", "--repo-id", "main", "--check"])
    assert render_check_payload["data"]["check"]["current"] is True


def test_empty_knowledge_is_optional_until_its_projection_is_corrupt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    write_workspace(workspace)
    subprocess.run(["git", "init"], cwd=workspace / "repos", stdout=subprocess.DEVNULL, check=True)
    write_repometa(workspace / "repos")
    records = workspace / "docs/knowledge/records"
    records.mkdir(parents=True)
    (records / ".gitkeep").write_text("", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    assert main(["upgrade", "postflight", "--workspace-root", str(workspace), "--json"]) == 1
    postflight = json.loads(capsys.readouterr().out)
    reviewed = postflight["data"]["repositories"][0]["reviewed_knowledge"]
    assert reviewed["record_count"] == 0
    assert reviewed["projection_status"] == "rebuild_required"
    assert any(problem["code"] == "knowledge_projection_unavailable" for problem in postflight["problems"])
    assert any(
        action.get("command") == "./scripts/repoctl knowledge rebuild --repo-id main --json"
        for action in postflight["data"]["recovery_actions"]
    )

    assert main(["context", "query", "product", "--repo-id", "main", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert not any(problem["code"] == "context_linked_knowledge_unavailable" for problem in context["problems"])
    assert context["data"]["bundle"]["completeness"]["project_knowledge"]["reviewed_records"]["available_record_count"] == 0
    assert not any(action.get("kind") == "knowledge_rebuild" for action in context["next_actions"])

    projection = knowledge_projection_path(workspace, repo_id="main")
    projection.parent.mkdir(parents=True)
    projection.write_text("{not-json\n", encoding="utf-8")
    assert main(["context", "query", "product", "--repo-id", "main", "--json"]) == 1
    corrupt = json.loads(capsys.readouterr().out)
    assert any(
        problem["code"] == "knowledge_projection_unavailable"
        and problem.get("cause_code") == "unreadable"
        for problem in corrupt["problems"]
    )
    assert corrupt["data"]["result_receipt"] is None


def test_postflight_rejects_projection_that_misses_durable_deprecation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    record = _approve_knowledge_source(capsys)["data"]["record"]
    projection_path = knowledge_projection_path(tmp_path, repo_id="main")
    projection_before_deprecation = projection_path.read_bytes()
    reason = tmp_path / "deprecation-reason.md"
    reason.write_text("The reviewed decision no longer applies.\n", encoding="utf-8")
    assert main(
        [
            "knowledge",
            "deprecate",
            record["id"],
            "--repo-id",
            "main",
            "--reason-file",
            reason.as_posix(),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    projection_path.write_bytes(projection_before_deprecation)

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 1
    postflight = json.loads(capsys.readouterr().out)
    reviewed = postflight["data"]["repositories"][0]["reviewed_knowledge"]
    assert reviewed["projection_status"] == "rebuild_required"
    assert any(
        problem["code"] == "knowledge_projection_unavailable"
        and problem["cause_code"] == "cold_lifecycle_mismatch"
        for problem in postflight["problems"]
    )


def test_postflight_reports_invalid_projection_lifecycle_without_crashing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    write_workspace(workspace)
    subprocess.run(["git", "init"], cwd=workspace / "repos", stdout=subprocess.DEVNULL, check=True)
    write_repometa(workspace / "repos")
    records = workspace / "docs/knowledge/records"
    records.mkdir(parents=True)
    projection_path = knowledge_projection_path(workspace, repo_id="main")
    projection_path.parent.mkdir(parents=True)
    projection = {
        "schema": "repoctl.knowledge.current-head",
        "schema_version": 1,
        "repo_id": "main",
        "generation": 1,
        "checkpoint": {"record_count": 0},
        "head_count": 0,
        "heads": [],
    }
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: workspace)

    for lifecycle_counts, cause_code in (
        ({"current": "0", "deprecated": 0, "superseded": 0}, "lifecycle_counts_invalid"),
        ({"current": 0, "deprecated": 1, "superseded": 0}, "lifecycle_record_count_mismatch"),
    ):
        invalid_projection = {**projection, "lifecycle_counts": lifecycle_counts}
        invalid_projection["projection_digest"] = digest_data(invalid_projection)
        projection_path.write_text(json.dumps(invalid_projection), encoding="utf-8")

        assert main(["upgrade", "postflight", "--workspace-root", str(workspace), "--json"]) == 1
        postflight = json.loads(capsys.readouterr().out)
        reviewed = postflight["data"]["repositories"][0]["reviewed_knowledge"]
        assert reviewed["projection_status"] == "rebuild_required"
        assert any(
            problem["code"] == "knowledge_projection_schema_mismatch"
            and problem["cause_code"] == cause_code
            for problem in postflight["problems"]
        )
