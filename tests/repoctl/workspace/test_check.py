from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from tools.repoctl.board import render_board
from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError, repoctl_lock
from tools.repoctl.markdown import find_section, replace_frontmatter_line
from tools.repoctl.tasks import create_task_file
from tests.repoctl.io_audit import reject_directory_enumeration



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

## Discovery

- Candidate query: none yet
- Candidate files reviewed: none yet
- Chosen files: none yet

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
        assert error.code == "task_lock_required"

    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()

    try:
        with repoctl_lock(tmp_path, timeout=0.0, interval=0.0):
            raise AssertionError("lock should not be acquired while the lock directory exists")
    except RepoctlError as error:
        assert error.code == "stale_lock"
        assert error.path == "docs/tasks/.repoctl.lock.d"

def test_frontmatter_replace_preserves_other_lines() -> None:
    text = task_text("T-20260609184046Z", status="todo")
    replaced = replace_frontmatter_line(text, "status", "doing")
    assert "status: doing" in replaced
    assert "owner: \"unassigned\"" in replaced
    assert "```text\n## Handoff\ninside fence" in replaced


def test_check_does_not_require_repo_ref_for_repository_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
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

    receipt = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("not json\n", encoding="utf-8")
    archive = tmp_path / "docs/archive/tasks"
    (archive / "T-20260609184047Z--cold.md").write_text("not a task\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    with monkeypatch.context() as audit_patch:
        with reject_directory_enumeration(audit_patch, receipt.parent, archive) as cold_reads:
            assert main(["check", "--json"]) == 0

    assert cold_reads == []
    bounded = json.loads(capsys.readouterr().out)
    assert bounded["data"]["completion_history"] == {
        "mode": "bounded_catalogue",
        "catalogues": [],
    }

    assert main(["check", "--audit-history", "--json"]) == 1
    audited = json.loads(capsys.readouterr().out)
    assert audited["data"]["completion_history"]["mode"] == "full_archive_audit"
    assert "invalid_completion_receipt" in {problem["code"] for problem in audited["problems"]}


def test_render_board_replaces_only_board_section() -> None:
    text = "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--old.md\n\n## Backlog\n\n- keep\n"
    rendered = render_board(text, ["docs/tasks/T-20260609184047Z--new.md"])
    assert "- docs/tasks/T-20260609184047Z--new.md" in rendered
    assert "- docs/tasks/T-20260609184046Z--old.md" not in rendered
    assert "## Backlog\n\n- keep" in rendered

def test_repoctl_script_uses_system_python_without_workspace_residue(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    root = tmp_path / "workspace"
    write_workspace(root)
    (root / "scripts").mkdir()
    shutil.copy2(source_root / "scripts/repoctl", root / "scripts/repoctl")
    (root / "tools").mkdir()
    shutil.copy2(source_root / "tools/__init__.py", root / "tools/__init__.py")
    shutil.copytree(
        source_root / "tools/repoctl",
        root / "tools/repoctl",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    def tree_snapshot() -> list[tuple[str, str, int, str]]:
        entries: list[tuple[str, str, int, str]] = []
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if ".git" in rel.parts:
                continue
            mode = path.lstat().st_mode & 0o7777
            if path.is_symlink():
                entries.append((rel.as_posix(), "symlink", mode, os.readlink(path)))
            elif path.is_dir():
                entries.append((rel.as_posix(), "directory", mode, ""))
            elif path.is_file():
                entries.append((rel.as_posix(), "file", mode, hashlib.sha256(path.read_bytes()).hexdigest()))
        return entries

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ["bash", "dirname", "python3"]:
        target = Path("/usr/bin") / name
        if not target.exists():
            target = Path("/bin") / name
        (fake_bin / name).symlink_to(target)
    hostile_package = tmp_path / "tools/repoctl"
    hostile_package.mkdir(parents=True)
    (hostile_package.parent / "__init__.py").write_text("", encoding="utf-8")
    (hostile_package / "__init__.py").write_text("", encoding="utf-8")
    (hostile_package / "__main__.py").write_text("raise SystemExit('hostile cwd import')\n", encoding="utf-8")
    before = tree_snapshot()
    for path_value, cwd in (
        (os.environ.get("PATH", ""), root),
        (str(fake_bin), root),
        (os.environ.get("PATH", ""), tmp_path),
    ):
        env = os.environ.copy()
        env["PATH"] = path_value
        env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
        env.pop("VIRTUAL_ENV", None)
        result = subprocess.run(
            [str(root / "scripts/repoctl"), "check", "--json"],
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["ok"] is True

    assert tree_snapshot() == before
    assert not (tmp_path / "uv-cache").exists()


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
    assert any(action["label"] == "Complete task Verification" for action in payload["next_actions"])
    assert all("command" not in action for action in payload["next_actions"])

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
    assert payload["data"]["verification"] == {"default_source": "task_section", "task_section_complete": False}
    assert "missing_discovery_evidence" in payload["data"]["advisory"]
    assert "missing_verification_file" in payload["data"]["advisory"]
    assert any(action["label"] == "Record task discovery evidence" for action in payload["next_actions"])
    after = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert after == before


def test_task_doctor_with_complete_verification_does_not_materialize_finish_writes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace("- pending", "- Command: pytest\n- Result: pass")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_ready"] is True
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_repoctl_lock_recovers_dead_owner_on_same_host(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": 999999999, "hostname": socket.gethostname(), "created_at": "2026-06-22T00:00:00Z"}) + "\n", encoding="utf-8")

    with repoctl_lock(tmp_path, timeout=0.1, interval=0.01):
        assert lock_dir.exists()

    assert not lock_dir.exists()
