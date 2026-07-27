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
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
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
    assert payload["data"]["meta_gate"]["baseline_available"] is False
    assert payload["data"]["finish_summary"]["child_attributed_changes"] == 2
    assert child_archive.exists()
    assert not (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    assert child_archive.read_bytes() == original_child


def test_root_parent_tracks_repository_adopted_after_its_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
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
    assert attributed["changes"] == []
    assert attributed["child_attributed_count"] == 1
    assert attributed["child_attributed_changes"][0]["repo_id"] == "api"

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_summary"]["child_attributed_changes"] == 1


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


def test_task_finish_parent_rejects_duplicate_child_claims_before_matching_current_content(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    first_child_id = "T-20260609184047Z"
    second_child_id = "T-20260609184048Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{first_child_id}--first-child.md", _repo_child_text(first_child_id, parent=parent_id))
    second_path = add_task(
        tmp_path,
        f"{second_child_id}--second-child.md",
        _repo_child_text(second_child_id, parent=parent_id, status="done"),
    )
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{first_child_id}--first-child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", first_child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, first_child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", first_child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    first_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{first_child_id}.json"
    second_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{second_child_id}.json"
    second_receipt = json.loads(first_receipt_path.read_text(encoding="utf-8"))
    second_receipt["task_id"] = second_child_id
    second_receipt["task_path_at_completion"] = f"docs/tasks/{second_path.name}"
    second_receipt["content_sha256"] = _sha256_text(second_path.read_text(encoding="utf-8"))
    manifest = second_receipt["repo_evidence"]["fingerprint_manifest"]
    manifest["entry_fingerprints"][0]["fingerprint_sha256"] = "sha256:" + "1" * 64
    encoded_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    second_receipt["repo_evidence"]["diff_fingerprint_sha256"] = _sha256_text(encoded_manifest)
    _write_receipt(second_receipt_path, second_receipt)

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_attribution_duplicate"


def test_task_finish_parent_rejects_duplicate_legacy_child_claims_without_entry_fingerprints(tmp_path: Path, monkeypatch, capsys) -> None:
    parent_id = "T-20260609184046Z"
    first_child_id = "T-20260609184047Z"
    second_child_id = "T-20260609184048Z"
    write_workspace(tmp_path)
    add_task(tmp_path, f"{parent_id}--parent.md", task_text(parent_id, status="doing"))
    add_task(tmp_path, f"{first_child_id}--first-child.md", _repo_child_text(first_child_id, parent=parent_id))
    second_path = add_task(
        tmp_path,
        f"{second_child_id}--second-child.md",
        _repo_child_text(second_child_id, parent=parent_id, status="done"),
    )
    (tmp_path / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{parent_id}--parent.md\n- docs/tasks/{first_child_id}--first-child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", first_child_id, "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, first_child_id, query="update app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(["task", "finish", first_child_id, "--verification-file", str(verification), "--json"]) == 0
    capsys.readouterr()

    first_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{first_child_id}.json"
    second_receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{second_child_id}.json"
    first_receipt = json.loads(first_receipt_path.read_text(encoding="utf-8"))
    first_manifest = first_receipt["repo_evidence"]["fingerprint_manifest"]
    first_manifest.pop("entry_fingerprints")
    first_receipt["repo_evidence"]["diff_fingerprint_sha256"] = _sha256_text(
        json.dumps(first_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    _write_receipt(first_receipt_path, first_receipt)

    second_receipt = json.loads(json.dumps(first_receipt))
    second_receipt["task_id"] = second_child_id
    second_receipt["task_path_at_completion"] = f"docs/tasks/{second_path.name}"
    second_receipt["content_sha256"] = _sha256_text(second_path.read_text(encoding="utf-8"))
    _write_receipt(second_receipt_path, second_receipt)
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_attribution_duplicate"


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
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")

    assert main(["task", "finish", parent_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "child_completion_evidence_drift"


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
