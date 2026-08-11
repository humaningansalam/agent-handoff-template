from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.tasks import repo_changes_since_task_start
from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    init_committed_product_repo,
    record_discovery,
    task_text,
    write_workspace,
)
from tests.repoctl.repository.test_repositories import write_settings


def _repo_child_text(task_id: str, *, parent: str, status: str = "todo") -> str:
    return (
        task_text(task_id, status=status, parent=parent)
        .replace('area: ""', 'area: "backend"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _downgrade_child_evidence_to_v2(root: Path, task_id: str) -> tuple[Path, dict]:
    receipt_path = root / f"docs/tasks/.repoctl-state/completions/{task_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = 2
    receipt.pop("started_at")
    receipt.pop("completed_event_at")
    receipt["repo_evidence"].pop("path_transitions")
    _write_receipt(receipt_path, receipt)

    state_path = root / f"docs/tasks/.repoctl-state/{task_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 3
    state["initial"].pop("started_at")
    state["initial"].pop("dirty_path_states")
    state["initial"].pop("identity_source")
    legacy_ownership = receipt["repo_evidence"]["ownership"]
    for path, decision in state["ownership"].items():
        evidence = legacy_ownership[path]
        decision["baseline_fingerprint"] = evidence["baseline_fingerprint"]
        decision["final_fingerprint"] = evidence["final_fingerprint"]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt_path, receipt


def _parent_child_repo_fixture(root: Path, parent_id: str, child_ids: list[str]) -> tuple[Path, Path]:
    write_workspace(root)
    add_task(root, f"{parent_id}--parent.md", task_text(parent_id))
    for index, child_id in enumerate(child_ids, start=1):
        add_task(root, f"{child_id}--child-{index}.md", _repo_child_text(child_id, parent=parent_id))
    board_paths = [
        f"docs/tasks/{parent_id}--parent.md",
        *(f"docs/tasks/{child_id}--child-{index}.md" for index, child_id in enumerate(child_ids, start=1)),
    ]
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n" + "".join(f"- {path}\n" for path in board_paths) + "\n## Backlog\n",
        encoding="utf-8",
    )
    repo = root / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = root / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    return repo, verification


def test_task_finish_child_does_not_move_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["archived"] is False
    assert payload["data"]["new_path"] == "docs/tasks/T-20260609184047Z--child.md"
    assert (tmp_path / payload["data"]["new_path"]).exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert "docs/tasks/T-20260609184047Z--child.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_child_rolls_back_task_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    original_child = child_path.read_text(encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert child_path.read_text(encoding="utf-8") == original_child
    assert "docs/tasks/T-20260609184047Z--child.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_parent_blocks_when_live_child_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "live_children_block_finish"


def test_task_finish_parent_archives_non_live_child_byte_identically(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z"))
    child_text = (
        task_text("T-20260609184047Z", parent="T-20260609184046Z")
        .replace('area: ""', 'area: "backend"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", child_text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", "T-20260609184047Z", "--json"]) == 0
    capsys.readouterr()
    record_discovery(
        tmp_path,
        "T-20260609184047Z",
        query="update app",
        reviewed="repos/app.py, repos/new.py",
        chosen="repos/app.py, repos/new.py",
    )
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "new.py").write_text("created = True\n", encoding="utf-8")
    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    original_child = child_path.read_bytes()

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    child_archive = tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md"
    assert payload["data"]["archived"] is True
    assert payload["data"]["meta_gate"]["reason"] == "no_task_repo_changes"
    assert payload["data"]["meta_gate"]["baseline_available"] is True
    assert payload["data"]["finish_summary"]["child_attributed_changes"] == 2
    assert child_archive.exists()
    assert not (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    assert child_archive.read_bytes() == original_child


def test_root_parent_requires_root_evidence_for_repository_adopted_after_its_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    (tmp_path / "repos").mkdir()
    web = tmp_path / "repos/web"
    init_committed_product_repo(web, {"app.py": "value = 1\n"})
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})
    (web / "app.py").write_text("value = 0\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()

    api = tmp_path / "repos/api"
    init_committed_product_repo(api, {"app.py": "value = 1\n"})
    write_settings(
        tmp_path,
        {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]},
    )
    (api / "app.py").write_text("value = 2\n", encoding="utf-8")

    unclaimed = repo_changes_since_task_start(tmp_path, parent_id)
    assert unclaimed["baseline_available"] is True
    assert unclaimed["preexisting_count"] == 1
    assert unclaimed["initial_dirty_paths"] == ["repos/web/app.py"]
    assert unclaimed["changes"] == [("modified", "repos/api/app.py", "")]
    assert unclaimed["child_attributed_count"] == 0

    (api / "app.py").write_text("value = 1\n", encoding="utf-8")
    child_text = _repo_child_text(child_id, parent=parent_id).replace('repo_id: "main"', 'repo_id: "api"')
    add_task(tmp_path, f"{child_id}--child.md", child_text)
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(
        tmp_path,
        child_id,
        query="update api",
        reviewed="repos/api/app.py",
        chosen="repos/api/app.py",
    )
    (api / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    attributed = repo_changes_since_task_start(tmp_path, parent_id)
    assert attributed["changes"] == [("modified", "repos/api/app.py", "")]
    assert attributed["child_attributed_count"] == 0
    problem = next(problem for problem in attributed["integrity_problems"] if problem.path == "repos/api/app.py")
    assert problem.code == "root_evidence_incomplete"

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "root_evidence_incomplete"


def test_root_parent_detects_repository_removed_when_one_registered_repo_remains(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    (tmp_path / "repos").mkdir()
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_committed_product_repo(web, {"app.py": "value = 1\n"})
    init_committed_product_repo(api, {"app.py": "value = 1\n"})
    write_settings(
        tmp_path,
        {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]},
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()

    api.rename(tmp_path / "removed-api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})

    delta = repo_changes_since_task_start(tmp_path, parent_id)

    assert delta["baseline_available"] is True
    assert delta["changes"] == [("deleted", "repos/api", "")]
    assert delta["baseline_count"] == 0
    assert delta["current_count"] == 0
    assert delta["preexisting_count"] == 0


def test_root_parent_counts_dirty_baselines_separately_from_repository_removal(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    (tmp_path / "repos").mkdir()
    repositories = []
    for repo_id in ("web", "api", "worker"):
        repo = tmp_path / f"repos/{repo_id}"
        init_committed_product_repo(repo, {"app.py": "value = 1\n"})
        (repo / "app.py").write_text("value = 0\n", encoding="utf-8")
        repositories.append({"id": repo_id, "path": f"repos/{repo_id}"})
    write_settings(tmp_path, {"repositories": repositories})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()

    (tmp_path / "repos/worker").rename(tmp_path / "removed-worker")
    write_settings(
        tmp_path,
        {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]},
    )

    delta = repo_changes_since_task_start(tmp_path, parent_id)

    assert delta["changes"] == [("deleted", "repos/worker", "")]
    assert delta["baseline_conflicts"] == ["repos/worker/app.py"]
    assert delta["baseline_count"] == 3
    assert delta["current_count"] == 2
    assert delta["preexisting_count"] == 2






def test_task_finish_parent_rejects_corrupt_child_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not json\n", encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_receipt_invalid"
    assert (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert receipt_path.read_text(encoding="utf-8") == "{not json\n"


def test_parent_attributes_provable_legacy_v2_child_transition(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    add_task(tmp_path, f"{child_id}--child.md", _repo_child_text(child_id, parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    _downgrade_child_evidence_to_v2(tmp_path, child_id)

    delta = repo_changes_since_task_start(tmp_path, parent_id)

    assert delta["changes"] == []
    assert delta["integrity_problems"] == ()
    assert delta["child_attributed_count"] == 1
    assert delta["child_attributed_changes"][0]["task_ids"] == [child_id]

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_summary"]["child_attributed_changes"] == 1


def test_parent_blocks_legacy_v2_child_without_terminal_fingerprint(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    add_task(tmp_path, f"{child_id}--child.md", _repo_child_text(child_id, parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    receipt_path, receipt = _downgrade_child_evidence_to_v2(tmp_path, child_id)
    manifest = receipt["repo_evidence"]["fingerprint_manifest"]
    manifest.pop("entry_fingerprints")
    receipt["repo_evidence"]["diff_fingerprint_sha256"] = _sha256_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    _write_receipt(receipt_path, receipt)

    delta = repo_changes_since_task_start(tmp_path, parent_id)
    problem = next(problem for problem in delta["integrity_problems"] if problem.path == "repos/app.py")
    assert problem.code == "transition_evidence_incomplete"
    assert problem.cause_code == "legacy_completion_receipt_v2"

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "transition_evidence_incomplete"
    assert payload["problems"][0]["cause_code"] == "legacy_completion_receipt_v2"


def test_parent_attributes_legacy_v2_untracked_path_from_bound_raw_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    repo, verification = _parent_child_repo_fixture(tmp_path, parent_id, [child_id])
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="add module", reviewed="repos/new.py", chosen="repos/new.py")
    (repo / "new.py").write_text("created = True\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    receipt_path, receipt = _downgrade_child_evidence_to_v2(tmp_path, child_id)
    manifest = receipt["repo_evidence"]["fingerprint_manifest"]
    manifest.pop("entry_fingerprints")
    receipt["repo_evidence"]["diff_fingerprint_sha256"] = _sha256_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    _write_receipt(receipt_path, receipt)

    delta = repo_changes_since_task_start(tmp_path, parent_id)
    assert delta["changes"] == []
    assert delta["integrity_problems"] == ()
    assert delta["child_attributed_changes"][0]["path"] == "new.py"


def test_parent_attributes_legacy_v2_delete_from_typed_change_effect(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    repo, verification = _parent_child_repo_fixture(tmp_path, parent_id, [child_id])
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="remove module", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").unlink()
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    receipt_path, receipt = _downgrade_child_evidence_to_v2(tmp_path, child_id)
    manifest = receipt["repo_evidence"]["fingerprint_manifest"]
    manifest.pop("entry_fingerprints")
    receipt["repo_evidence"]["diff_fingerprint_sha256"] = _sha256_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    _write_receipt(receipt_path, receipt)

    delta = repo_changes_since_task_start(tmp_path, parent_id)
    assert delta["changes"] == []
    assert delta["integrity_problems"] == ()
    assert delta["child_attributed_changes"][0]["change"] == "deleted"
    assert delta["child_attributed_changes"][0]["path"] == "app.py"

    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    recreated = repo_changes_since_task_start(tmp_path, parent_id)
    assert recreated["child_attributed_count"] == 0
    assert recreated["changes"] == [("modified", "repos/app.py", "")]
    problem = next(problem for problem in recreated["integrity_problems"] if problem.path == "repos/app.py")
    assert problem.code == "terminal_evidence_drift"


def test_parent_blocks_legacy_v2_transition_after_repository_head_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    repo, verification = _parent_child_repo_fixture(tmp_path, parent_id, [child_id])
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    _downgrade_child_evidence_to_v2(tmp_path, child_id)
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "commit child state"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    delta = repo_changes_since_task_start(tmp_path, parent_id)
    problem = next(problem for problem in delta["integrity_problems"] if problem.path == "repos/app.py")
    assert problem.code == "transition_evidence_incomplete"
    assert problem.cause_code == "legacy_completion_receipt_v2"


def test_parent_orders_connected_v3_path_states_despite_overlapping_task_lifetimes(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    first_child_id = "T-20260609184047Z"
    second_child_id = "T-20260609184048Z"
    repo, verification = _parent_child_repo_fixture(tmp_path, parent_id, [first_child_id, second_child_id])
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", first_child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, first_child_id, query="first update", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", first_child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "start", second_child_id, "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, second_child_id, query="second update", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    assert main([
        "task",
        "baseline",
        "resolve",
        second_child_id,
        "--path",
        "app.py",
        "--ownership",
        "task",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert main(["task", "finish", second_child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    delta = repo_changes_since_task_start(tmp_path, parent_id)
    assert delta["changes"] == []
    assert delta["integrity_problems"] == ()
    assert delta["child_attributed_changes"][0]["task_ids"] == [first_child_id, second_child_id]

    first_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{first_child_id}.json"
    second_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{second_child_id}.json"
    first_receipt = json.loads(first_receipt_path.read_text(encoding="utf-8"))
    second_receipt = json.loads(second_receipt_path.read_text(encoding="utf-8"))
    second_receipt["started_at"] = first_receipt["started_at"]
    _write_receipt(second_receipt_path, second_receipt)

    overlapping = repo_changes_since_task_start(tmp_path, parent_id)
    assert overlapping["changes"] == []
    assert overlapping["integrity_problems"] == ()
    assert overlapping["child_attributed_changes"][0]["task_ids"] == [first_child_id, second_child_id]

    second_receipt["completed_event_at"] = first_receipt["started_at"]
    _write_receipt(second_receipt_path, second_receipt)

    reverse_completion = repo_changes_since_task_start(tmp_path, parent_id)
    assert reverse_completion["changes"] == [("modified", "repos/app.py", "")]
    problem = next(problem for problem in reverse_completion["integrity_problems"] if problem.path == "repos/app.py")
    assert problem.code == "transition_order_ambiguous"


def test_task_finish_parent_rejects_workspace_child_receipt_with_repository_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{child_id}--child.md", task_text(child_id, status="doing", parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    init_committed_product_repo(tmp_path / "repos", {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{child_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["repo_id"] = "main"
    _write_receipt(receipt_path, receipt)

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_receipt_wrong_repository"


def test_committed_range_child_does_not_claim_parent_working_tree_change(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{child_id}--child.md", _repo_child_text(child_id, parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "child change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    assert main(["task", "finish", child_id, "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    delta = repo_changes_since_task_start(tmp_path, parent_id)

    assert delta["child_attributed_count"] == 0
    assert delta["changes"] == [("modified", "repos/app.py", "")]


def test_task_finish_parent_rejects_disappeared_child_working_tree_claim(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id))
    add_task(tmp_path, f"{child_id}--child.md", _repo_child_text(child_id, parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", parent_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "terminal_evidence_drift"


def test_task_finish_parent_rejects_child_receipt_bound_to_another_task_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    parent_path = add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{child_id}--child.md", task_text(child_id, status="doing", parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{child_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["task_path_at_completion"] = f"docs/tasks/{parent_path.name}"
    receipt["content_sha256"] = _sha256_text(parent_path.read_text(encoding="utf-8"))
    _write_receipt(receipt_path, receipt)

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_receipt_invalid"


def test_task_finish_parent_rejects_incoherent_child_repository_evidence_tuple(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{child_id}--child.md", _repo_child_text(child_id, parent=parent_id))
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{child_id}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()
    receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{child_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["repo_evidence"]["mode"] == "working_tree_diff"
    receipt["repo_evidence"]["attribution"] = "range_observed"
    _write_receipt(receipt_path, receipt)

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_receipt_invalid"
