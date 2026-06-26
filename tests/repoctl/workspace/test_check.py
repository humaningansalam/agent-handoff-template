from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

from tools.repoctl.board import render_board
from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError, repoctl_lock
from tools.repoctl.markdown import find_section, replace_frontmatter_line
from tools.repoctl.tasks import create_task_file



def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True)
    (root / "docs/archive/tasks").mkdir(parents=True)
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\nintro\n\n## Board\n\n## Backlog\n\n<!-- backlog -->\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "docs/tasks/TEMPLATE.md").is_file())
    (root / "docs/tasks/TEMPLATE.md").write_text((source_root / "docs/tasks/TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docs/tasks/PARENT_TEMPLATE.md").write_text((source_root / "docs/tasks/PARENT_TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8")

def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)

def task_text(task_id: str, *, status: str = "todo", parent: str = "") -> str:
    return f'''---
id: {task_id}
title: "Task {task_id}"
status: {status}
owner: "unassigned"
repo_id: ""
repo_ref: ""
created: 20260609T184046Z
area: ""
parent: "{parent}"
depends_on: []
---

# {task_id} - Task

## Execution Log

- created

```text
## Handoff
inside fence
```

## Verification

- pending

## Handoff

- Next exact step: test
- First file to open: `docs/BOARD.md`
- First command to run: `repoctl check`
- Done when: done
'''

def add_task(root: Path, name: str, text: str) -> Path:
    path = root / "docs/tasks" / name
    path.write_text(text, encoding="utf-8")
    return path

def test_section_scanner_ignores_code_fence_heading() -> None:
    text = task_text("T-20260609184046Z")
    section = find_section(text, "Handoff")
    assert text[section.start : section.body_start].strip() == "## Handoff"
    assert "inside fence" not in text[section.body_start : section.end]

def test_repoctl_lock_uses_repoctl_lock_dir_and_times_out(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    try:
        create_task_file(tmp_path, title="No Lock", slug="no-lock")
        raise AssertionError("task creation should require the repoctl lock")
    except RepoctlError as error:
        assert "task creation requires repoctl lock" in str(error)

    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()

    try:
        with repoctl_lock(tmp_path, timeout=0.0, interval=0.0):
            raise AssertionError("lock should not be acquired while the lock directory exists")
    except RepoctlError as error:
        assert "docs/tasks/.repoctl.lock.d" in str(error)

def test_frontmatter_replace_preserves_other_lines() -> None:
    text = task_text("T-20260609184046Z", status="todo")
    replaced = replace_frontmatter_line(text, "status", "doing")
    assert "status: doing" in replaced
    assert "owner: \"unassigned\"" in replaced
    assert "```text\n## Handoff\ninside fence" in replaced

def test_clean_check_reports_release_candidate_field_gates(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    (tmp_path / "tests/fixtures/context-benchmark").mkdir(parents=True)
    (tmp_path / "tests/fixtures/context-benchmark/corpus.json").write_text('{"repositories":{"main":{"files":[]}}}\n', encoding="utf-8")
    (tmp_path / "tests/fixtures/context-benchmark/questions.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "tests/fixtures/context-benchmark/expected-sources.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tests/fixtures/context-pack-benchmark").mkdir(parents=True)
    (tmp_path / "tests/fixtures/context-pack-benchmark/cases.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "tests/fixtures/context-pack-benchmark/tasks.json").write_text('{"tasks":[]}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    gates = payload["data"]["field_gates"]["release_candidate"]
    commands = [gate["command"] for gate in gates]
    assert payload["next_actions"] == []
    assert any("context benchmark-materialize" in command for command in commands)
    assert any("context benchmark --fixture tests/fixtures/context-benchmark" in command for command in commands)
    assert any("context pack-benchmark-materialize" in command for command in commands)
    assert any("context pack-benchmark" in command for command in commands)
    assert any(gate["mutates_workspace"] is True for gate in gates if "benchmark-materialize" in gate["command"])

def test_check_does_not_require_repo_ref_for_repository_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "backend"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not any(problem["code"] == "missing_repo_ref" for problem in payload["problems"])
    assert not any(warning["code"] == "missing_repo_ref" for warning in payload["warnings"])

    assert main(["task", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_repo_ref" for warning in payload["warnings"])

def test_render_board_replaces_only_board_section() -> None:
    text = "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--old.md\n\n## Backlog\n\n- keep\n"
    rendered = render_board(text, ["docs/tasks/T-20260609184047Z--new.md"])
    assert "- docs/tasks/T-20260609184047Z--new.md" in rendered
    assert "- docs/tasks/T-20260609184046Z--old.md" not in rendered
    assert "## Backlog\n\n- keep" in rendered

def test_repoctl_script_falls_back_to_python3_without_uv(tmp_path: Path) -> None:
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ["bash", "dirname", "python3"]:
        target = Path("/usr/bin") / name
        if not target.exists():
            target = Path("/bin") / name
        (fake_bin / name).symlink_to(target)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(
        [str(root / "scripts/repoctl"), "check", "--json"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True

def test_json_error_contract_includes_next_actions_for_missing_verification(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "task.finish"
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert any(action["label"] == "Create verification evidence" for action in payload["next_actions"])
    assert any(action["command"].endswith("--use-task-verification --json") for action in payload["next_actions"])

def test_task_doctor_is_read_only_and_reports_advisory_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    Path("/tmp/T-20260609184046Z-verification.md").unlink(missing_ok=True)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    before = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.doctor"
    assert payload["data"]["finish_ready"] is False
    assert "missing_discovery_evidence" in payload["data"]["advisory"]
    assert "missing_verification_file" in payload["data"]["advisory"]
    assert any(action["label"] == "Record task discovery evidence" for action in payload["next_actions"])
    after = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert after == before

def test_repoctl_lock_recovers_dead_owner_on_same_host(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": 999999999, "hostname": socket.gethostname(), "created_at": "2026-06-22T00:00:00Z"}) + "\n", encoding="utf-8")

    with repoctl_lock(tmp_path, timeout=0.1, interval=0.01):
        assert lock_dir.exists()

    assert not lock_dir.exists()

def test_removed_creator_references_stay_removed() -> None:
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    forbidden = "new" "-task.sh"
    assert not (root / "scripts" / forbidden).exists()
    paths = [
        *root.glob("AGENTS.md"),
        *root.glob("README.md"),
        *root.glob("docs/**/*.md"),
        *root.glob("tests/**/*.py"),
        *root.glob("tools/repoctl/**/*.py"),
    ]
    paths = [path for path in paths if "docs/archive/tasks" not in path.as_posix()]
    offenders = [path for path in paths if forbidden in path.read_text(encoding="utf-8")]
    assert offenders == []
