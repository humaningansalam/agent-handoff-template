from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.repoctl.task_lifecycle_helpers import init_committed_product_repo, write_workspace
from tools.repoctl.cli import main
from tests.repoctl.io_audit import reject_directory_enumeration
from tools.repoctl.completion_catalogue import (
    CompletionCataloguePolicy,
    CompletionCatalogueUnavailable,
    CompletionCatalogueUnavailableReason,
    CompletionReceiptInput,
    audit_completion_catalogue,
    completion_catalogue_paths,
    current_completion_frontier,
    file_completion_subject_key,
    ingest_completion_catalogue_tail,
    lookup_completion_exact,
    prepare_completion_sidecar_writes,
    rebuild_completion_catalogue,
    completion_graph_inputs,
    search_completion_history,
    completion_catalogue_status,
    versioned_completion_subject_key,
)
from tools.repoctl.discovery_outcomes import (
    current_path_subject,
)
from tools.repoctl.graph_model import digest_data
from tools.repoctl.repositories import RepoTarget
from tools.repoctl.release import build_release_archive
from tools.repoctl.result_receipts import (
    ContextResultRequest,
    ResultAuthority,
    ResultProducer,
    ResultSelection,
    write_result_receipt,
)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _receipt_input(
    root: Path,
    task_id: str,
    *,
    repo_id: str = "main",
    changed_paths: tuple[str, ...] = ("src/app.py",),
    artifact_text: str | None = None,
) -> CompletionReceiptInput:
    artifact_path = f"docs/archive/tasks/{task_id}--catalogued.md"
    artifact_text = artifact_text or f"# {task_id}\n\nVerified completion.\n"
    verification_digest = _digest("verified")
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 2,
        "repo_id": repo_id,
        "task_id": task_id,
        "status": "done",
        "completed_at": f"{task_id[2:10]}T{task_id[10:16]}Z",
        "task_path_at_completion": artifact_path,
        "content_sha256": _digest(artifact_text),
        "changed_entries": [
            {"change": "modified", "path": path}
            for path in changed_paths
        ],
        "repo_evidence": {
            "mode": "working_tree_diff",
            "attribution": "task_working_tree",
            "start_head": "",
            "observed_head": "",
            "git_available": True,
            "diff_fingerprint_sha256": "sha256:" + ("0" * 64),
            "fingerprint_manifest": {},
            "ownership": {},
            "meta_gate": {},
            "delta": {"changed_count": len(changed_paths)},
        },
        "verification": {
            "source": "task_section",
            "source_path": "",
            "source_sha256": verification_digest,
            "normalization": "normalize_final_newline",
            "normalized_sha256": verification_digest,
            "stored_sha256": verification_digest,
            "truncated": False,
        },
    }
    receipt_path = f"docs/tasks/.repoctl-state/completions/{task_id}.json"
    return CompletionReceiptInput(
        receipt=receipt,
        receipt_path=receipt_path,
        receipt_text=json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        artifact_path=artifact_path,
        artifact_text=artifact_text,
    )


def _publish(writes: tuple[tuple[Path, str], ...]) -> None:
    for path, text in writes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _publish_receipt_authority(root: Path, item: CompletionReceiptInput) -> None:
    receipt_path = root / Path(item.receipt_path)
    artifact_path = root / Path(item.artifact_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(item.receipt_text, encoding="utf-8")
    artifact_path.write_text(item.artifact_text, encoding="utf-8")


def _current_file_completion_key(root: Path, path: str) -> str:
    subject = current_path_subject(
        root,
        target=RepoTarget("main", root / "repos", "repos", "reserved"),
        path=path,
    )
    return versioned_completion_subject_key(subject["key"], subject["version_digest"])


def _tamper_empty_checkpoint(root: Path, repo_id: str) -> tuple[Path, str]:
    checkpoint_path = completion_catalogue_paths(root, repo_id).checkpoint
    original = checkpoint_path.read_text(encoding="utf-8")
    checkpoint = json.loads(original)
    checkpoint["prefix_window_digest"] = "sha256:" + ("0" * 64)
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return checkpoint_path, original


def test_first_public_finish_can_follow_an_empty_history_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_id, _task_path, verification = _public_finish_fixture(
        tmp_path,
        monkeypatch,
        capsys,
        slug="first-after-empty-rebuild",
    )
    assert main(["history", "rebuild", "--repo-id", "main", "--json"]) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["data"]["last_sequence"] == 0

    checkpoint_path, checkpoint_text = _tamper_empty_checkpoint(tmp_path, "main")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "completion_catalogue_gap"
    assert any(
        action.get("command") == "./scripts/repoctl history rebuild --repo-id main --json"
        for action in rejected["next_actions"]
    )

    checkpoint_path.write_text(checkpoint_text, encoding="utf-8")
    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    assert (tmp_path / finished["data"]["new_path"]).is_file()
    assert (tmp_path / finished["data"]["completion_receipt"]).is_file()
    assert completion_catalogue_status(tmp_path, "main").status == "tail_pending"


def test_workspace_finish_recovery_uses_the_workspace_history_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["task", "create", "--area", "docs", "--start", "--slug", "workspace-docs", "Workspace docs", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    rebuild_completion_catalogue(tmp_path, "", receipt_artifacts=[])
    _tamper_empty_checkpoint(tmp_path, "")
    verification = tmp_path / "workspace-verification.md"
    verification.write_text("- Checked workspace docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    rejected = json.loads(capsys.readouterr().out)
    assert rejected["data"]["repo_id"] == ""
    assert any(
        action.get("command") == "./scripts/repoctl history rebuild --workspace --json"
        for action in rejected["next_actions"]
    )


def _receipt_file_completion_key(receipt: dict[str, object], path: str) -> str:
    outcome = receipt["discovery_outcome"]
    assert isinstance(outcome, dict)
    subjects = outcome["subjects"]
    assert isinstance(subjects, list)
    subject = next(
        item
        for item in subjects
        if isinstance(item, dict)
        and item.get("kind") == "file"
        and item.get("identity") == {"path": path}
    )
    return versioned_completion_subject_key(str(subject["key"]), str(subject["version_digest"]))


def _prepare_and_publish(root: Path, item: CompletionReceiptInput, *, policy: CompletionCataloguePolicy | None = None) -> None:
    kwargs = {"policy": policy} if policy is not None else {}
    prepared = prepare_completion_sidecar_writes(
        root,
        receipt=item.receipt,
        receipt_path=item.receipt_path,
        receipt_text=item.receipt_text,
        artifact_path=item.artifact_path,
        artifact_text=item.artifact_text,
        **kwargs,
    )
    _publish(prepared.writes)


def _catalogue_manifest(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    directory = completion_catalogue_paths(root).directory
    entries: list[tuple[str, str, int, str]] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        size = path.lstat().st_size
        if path.is_symlink():
            entries.append((relative, "symlink", size, path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", size, ""))
        else:
            entries.append((relative, "file", size, hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


def _canonical_event_line(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _event_prefix(event: dict[str, object]) -> str:
    return digest_data(
        {
            "previous_prefix_digest": event["previous_prefix_digest"],
            "event_id": event["event_id"],
        }
    )


def _replace_pending_head_event(
    root: Path,
    *,
    updates: dict[str, object],
    update_head_prefix: bool = True,
) -> tuple[Path, dict[str, object]]:
    paths = completion_catalogue_paths(root, "main")
    head = json.loads(paths.head.read_text(encoding="utf-8"))
    event_path = paths.events_directory / f"{str(head['last_event_id']).removeprefix('sha256:')}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event.update(updates)
    base = {key: value for key, value in event.items() if key != "event_id"}
    event["event_id"] = digest_data(base)
    replacement_path = paths.events_directory / f"{str(event['event_id']).removeprefix('sha256:')}.json"
    replacement_path.write_text(_canonical_event_line(event), encoding="utf-8")
    head["last_event_id"] = event["event_id"]
    if update_head_prefix:
        head["prefix_digest"] = _event_prefix(event)
    paths.head.write_text(json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return replacement_path, event


def test_full_audit_accepts_multi_event_pending_tail_without_mutation(tmp_path: Path) -> None:
    first = _receipt_input(tmp_path, "T-20260813020101Z")
    second = _receipt_input(tmp_path, "T-20260813020102Z", changed_paths=("src/second.py",))
    third = _receipt_input(tmp_path, "T-20260813020103Z", changed_paths=("src/third.py",))
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first])
    _prepare_and_publish(tmp_path, second)
    _prepare_and_publish(tmp_path, third)
    paths = completion_catalogue_paths(tmp_path, "main")
    head = json.loads(paths.head.read_text(encoding="utf-8"))
    assert completion_catalogue_status(tmp_path, "main").status == "tail_pending"
    before = _catalogue_manifest(tmp_path)

    audit = audit_completion_catalogue(
        tmp_path,
        "main",
        receipt_artifacts=[first, second, third],
    )

    assert audit.event_count == 3
    assert audit.last_sequence == 3
    assert audit.last_event_id == head["last_event_id"]
    assert audit.prefix_digest == head["prefix_digest"]
    assert audit.task_ids == tuple(item.receipt["task_id"] for item in (first, second, third))
    assert audit.source_checked is True
    assert _catalogue_manifest(tmp_path) == before


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("payload", CompletionCatalogueUnavailableReason.CORRUPT),
        ("previous_event", CompletionCatalogueUnavailableReason.GAP),
        ("sequence", CompletionCatalogueUnavailableReason.GAP),
        ("prefix", CompletionCatalogueUnavailableReason.PREFIX_MISMATCH),
        ("head", CompletionCatalogueUnavailableReason.PREFIX_MISMATCH),
    ],
)
def test_full_audit_rejects_pending_tail_tamper_without_mutation(
    tmp_path: Path,
    mutation: str,
    reason: CompletionCatalogueUnavailableReason,
) -> None:
    first = _receipt_input(tmp_path, "T-20260813020201Z")
    second = _receipt_input(tmp_path, "T-20260813020202Z", changed_paths=("src/second.py",))
    third = _receipt_input(tmp_path, "T-20260813020203Z", changed_paths=("src/third.py",))
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first])
    _prepare_and_publish(tmp_path, second)
    _prepare_and_publish(tmp_path, third)
    paths = completion_catalogue_paths(tmp_path, "main")
    head = json.loads(paths.head.read_text(encoding="utf-8"))
    event_path = paths.events_directory / f"{str(head['last_event_id']).removeprefix('sha256:')}.json"

    if mutation == "payload":
        event_path.write_text("{\n", encoding="utf-8")
    elif mutation == "previous_event":
        _replace_pending_head_event(
            tmp_path,
            updates={"previous_event_id": "sha256:" + ("0" * 64)},
        )
    elif mutation == "sequence":
        _replace_pending_head_event(tmp_path, updates={"sequence": 4})
    elif mutation == "prefix":
        _replace_pending_head_event(
            tmp_path,
            updates={"previous_prefix_digest": "sha256:" + ("0" * 64)},
        )
    else:
        head["prefix_digest"] = "sha256:" + ("0" * 64)
        paths.head.write_text(json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    before = _catalogue_manifest(tmp_path)
    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        audit_completion_catalogue(
            tmp_path,
            "main",
            receipt_artifacts=[first, second, third],
        )

    assert caught.value.reason == reason
    assert caught.value.code == reason.value
    assert _catalogue_manifest(tmp_path) == before


@pytest.mark.parametrize("mutation", ["cold_prefix", "projection"])
def test_full_audit_rejects_committed_state_tamper_without_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    item = _receipt_input(tmp_path, "T-20260813020301Z")
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])
    paths = completion_catalogue_paths(tmp_path, "main")
    if mutation == "cold_prefix":
        event = json.loads(paths.catalogue.read_text(encoding="utf-8"))
        event["changed_count"] = int(event["changed_count"]) + 1
        base = {key: value for key, value in event.items() if key != "event_id"}
        event["event_id"] = digest_data(base)
        paths.catalogue.write_text(_canonical_event_line(event), encoding="utf-8")
        event_path = paths.events_directory / f"{str(event['event_id']).removeprefix('sha256:')}.json"
        event_path.write_text(_canonical_event_line(event), encoding="utf-8")
    else:
        checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
        projection_path = paths.projection_slots[int(checkpoint["projection_slot"])]
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        projection["eviction_count"] = int(projection["eviction_count"]) + 1
        projection_path.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    before = _catalogue_manifest(tmp_path)
    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        audit_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])

    assert caught.value.reason == CompletionCatalogueUnavailableReason.PREFIX_MISMATCH
    assert _catalogue_manifest(tmp_path) == before


@pytest.mark.parametrize("source_case", ["missing_event", "orphan_event"])
def test_full_audit_checks_source_parity_across_committed_and_pending_events(
    tmp_path: Path,
    source_case: str,
) -> None:
    first = _receipt_input(tmp_path, "T-20260813020401Z")
    second = _receipt_input(tmp_path, "T-20260813020402Z", changed_paths=("src/second.py",))
    third = _receipt_input(tmp_path, "T-20260813020403Z", changed_paths=("src/third.py",))
    fourth = _receipt_input(tmp_path, "T-20260813020404Z", changed_paths=("src/fourth.py",))
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first])
    _prepare_and_publish(tmp_path, second)
    _prepare_and_publish(tmp_path, third)
    sources = [first, second, third, fourth] if source_case == "missing_event" else [first, second]
    before = _catalogue_manifest(tmp_path)

    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        audit_completion_catalogue(tmp_path, "main", receipt_artifacts=sources)

    assert caught.value.reason == CompletionCatalogueUnavailableReason.SOURCE_MISMATCH
    assert _catalogue_manifest(tmp_path) == before


def test_full_audit_rejects_duplicate_task_across_committed_and_pending_events(tmp_path: Path) -> None:
    item = _receipt_input(tmp_path, "T-20260813020501Z")
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])
    _prepare_and_publish(tmp_path, item)
    before = _catalogue_manifest(tmp_path)

    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        audit_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])

    assert caught.value.reason == CompletionCatalogueUnavailableReason.DUPLICATE_TASK
    assert _catalogue_manifest(tmp_path) == before


def test_check_audits_valid_pending_tail_but_ordinary_check_stays_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"src/app.py": "value = 1\n"})
    first = _receipt_input(tmp_path, "T-20260813020601Z")
    second = _receipt_input(tmp_path, "T-20260813020602Z", changed_paths=("src/second.py",))
    third = _receipt_input(tmp_path, "T-20260813020603Z", changed_paths=("src/third.py",))
    for item in (first, second, third):
        _publish_receipt_authority(tmp_path, item)
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first])
    _prepare_and_publish(tmp_path, second)
    _prepare_and_publish(tmp_path, third)
    paths = completion_catalogue_paths(tmp_path, "main")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    with reject_directory_enumeration(
        monkeypatch,
        paths.catalogue,
        paths.events_directory,
        tmp_path / "docs/tasks/.repoctl-state/completions",
        tmp_path / "docs/archive/tasks",
    ) as cold_reads:
        assert main(["check", "--json"]) == 0

    assert cold_reads == []
    bounded = json.loads(capsys.readouterr().out)
    main_catalogue = next(
        item
        for item in bounded["data"]["completion_history"]["catalogues"]
        if item["repo_id"] == "main"
    )
    assert main_catalogue["status"] == "tail_pending"
    assert main_catalogue["head_sequence"] == 3
    assert main_catalogue["checkpoint_sequence"] == 1

    before = _catalogue_manifest(tmp_path)
    assert main(["check", "--audit-history", "--json"]) == 0
    audited = json.loads(capsys.readouterr().out)
    assert audited["problems"] == []
    assert {
        "repo_id": "main",
        "status": "audited",
        "event_count": 3,
        "last_sequence": 3,
        "source_checked": True,
    } in audited["data"]["completion_history"]["audited_catalogues"]
    assert _catalogue_manifest(tmp_path) == before


def _public_finish_fixture(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    slug: str,
    extra_files: dict[str, str] | None = None,
) -> tuple[str, Path, Path]:
    write_workspace(root)
    repo = root / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n", **(extra_files or {})})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)

    assert main(
        [
            "task",
            "create",
            "--area",
            "repo",
            "--start",
            "--slug",
            slug,
            "Catalogue finish integration",
            "--json",
        ]
    ) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    task_path = next((root / "docs/tasks").glob(f"{task_id}--*.md"))

    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "change app value",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    verification = root / "verification.md"
    verification.write_text("- Ran focused catalogue integration check\n- Result: pass\n", encoding="utf-8")
    return task_id, task_path, verification


def _run_release_repoctl(root: Path, *args: str, expected: int = 0) -> dict[str, object]:
    result = subprocess.run(
        ["./scripts/repoctl", *args, "--json"],
        cwd=root,
        env={**os.environ, "UV_CACHE_DIR": str(root.parent / "uv-cache")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == expected, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_release_archive_closes_first_and_later_completion_tails_and_recovers(
    tmp_path: Path,
) -> None:
    source_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts/repoctl").is_file()
    )
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(tmp_path / "release")
    root = next((tmp_path / "release").iterdir())
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n## Backlog\n",
        encoding="utf-8",
    )
    repo = root / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})

    _run_release_repoctl(root, "graph", "build", "--repo-id", "main")
    finished: list[dict[str, object]] = []
    for index in (1, 2):
        created = _run_release_repoctl(
            root,
            "task",
            "create",
            "--area",
            "repo",
            "--start",
            "--slug",
            f"catalogue-release-{index}",
            f"Catalogue release decision {index}",
        )
        task_id = str(created["data"]["task_id"])
        (repo / "app.py").write_text(f"value = {index + 1}\n", encoding="utf-8")
        _run_release_repoctl(
            root,
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            f"catalogue release decision {index}",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
        )
        verification = root / f"verification-{index}.md"
        verification.write_text("- Runtime journey passed\n", encoding="utf-8")
        if index == 2:
            _run_release_repoctl(root, "graph", "build", "--repo-id", "main")
        result = _run_release_repoctl(
            root,
            "task",
            "finish",
            task_id,
            "--verification-file",
            verification.as_posix(),
        )
        finished.append(result)

        audit = _run_release_repoctl(root, "check", "--audit-history")
        catalogue = next(
            item
            for item in audit["data"]["completion_history"]["catalogues"]
            if item["repo_id"] == "main"
        )
        assert catalogue["head_sequence"] == index
        assert _run_release_repoctl(
            root,
            "graph",
            "query",
            "--repo-id",
            "main",
            "--task",
            task_id,
        )["data"]["query_status"] == "found"
        assert _run_release_repoctl(
            root,
            "graph",
            "query",
            "--repo-id",
            "main",
            "--artifact",
            str(result["data"]["new_path"]),
        )["data"]["query_status"] == "found"
        history = _run_release_repoctl(
            root,
            "context",
            "query",
            f"catalogue release decision {index}",
            "--mode",
            "past-decision",
            "--repo-id",
            "main",
            "--full",
        )
        assert history["data"]["bundle"]["completeness"]["explicit_task_history"]["selected_record_count"] >= 1
        if index == 1:
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "user.name=Test",
                    "commit",
                    "-m",
                    "finish first task",
                ],
                cwd=repo,
                check=True,
                stdout=subprocess.DEVNULL,
            )

    paths = completion_catalogue_paths(root, "main")
    paths.catalogue.write_text(
        paths.catalogue.read_text(encoding="utf-8").replace("app.py", "bad.py", 1),
        encoding="utf-8",
    )
    receipts = [
        (root / str(item["data"]["completion_receipt"])).read_bytes()
        for item in finished
    ]
    archives = [(root / str(item["data"]["new_path"])).read_bytes() for item in finished]

    failed = _run_release_repoctl(root, "check", "--audit-history", expected=1)
    recovery = next(
        action
        for action in failed["next_actions"]
        if action.get("kind") == "completion_catalogue_rebuild"
    )
    assert recovery["command"] == "./scripts/repoctl history rebuild --repo-id main --json"
    repaired = subprocess.run(
        shlex.split(str(recovery["command"])),
        cwd=root,
        env={**os.environ, "UV_CACHE_DIR": str(root.parent / "uv-cache")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert repaired.returncode == 0, repaired.stderr or repaired.stdout
    assert _run_release_repoctl(root, "check", "--audit-history")["ok"] is True
    assert receipts == [
        (root / str(item["data"]["completion_receipt"])).read_bytes()
        for item in finished
    ]
    assert archives == [(root / str(item["data"]["new_path"])).read_bytes() for item in finished]


def test_public_task_finish_publishes_catalogue_ingress_and_hot_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_id, _task_path, verification = _public_finish_fixture(
        tmp_path,
        monkeypatch,
        capsys,
        slug="catalogue-public-finish",
    )

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    receipt_path = tmp_path / payload["data"]["completion_receipt"]
    archive_path = tmp_path / payload["data"]["new_path"]
    paths = completion_catalogue_paths(tmp_path, "main")

    assert receipt_path.is_file()
    assert archive_path.is_file()
    assert paths.head.is_file()
    event_paths = sorted(paths.events_directory.glob("*.json"))
    assert len(event_paths) == 1
    assert not paths.catalogue.exists()
    event = json.loads(event_paths[0].read_text(encoding="utf-8"))
    assert event["task_id"] == task_id
    assert event["receipt_path"] == receipt_path.relative_to(tmp_path).as_posix()
    assert event["artifact_path"] == archive_path.relative_to(tmp_path).as_posix()
    assert "receipt" not in event
    assert set(event["hot_witnesses"]) == set(event["subject_keys"])

    refresh = ingest_completion_catalogue_tail(tmp_path, "main")
    assert refresh.ingested_count == 1
    frontier = current_completion_frontier(tmp_path, "main", _current_file_completion_key(tmp_path, "app.py"))
    assert [record["task_id"] for record in frontier.records] == [task_id]


def test_public_finish_projects_discovery_roles_and_verification_to_subject_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_id, _task_path, verification = _public_finish_fixture(
        tmp_path,
        monkeypatch,
        capsys,
        slug="catalogue-outcome-frontier",
    )
    decoy = tmp_path / "repos/decoy.py"
    decoy.write_text("value = 0\n", encoding="utf-8")
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--reviewed",
            "repos/decoy.py",
            "--excluded",
            "repos/decoy.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    decoy.unlink()
    subject_evidence = tmp_path / "outcome-verification.txt"
    subject_evidence.write_text("app.py verification passed\n", encoding="utf-8")
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            subject_evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    ingest_completion_catalogue_tail(tmp_path, "main")

    app = current_completion_frontier(tmp_path, "main", _current_file_completion_key(tmp_path, "app.py"))
    app_role = app.records[0]["outcome"]["subject_roles"]["file:app.py"]
    assert {
        key: app_role[key]
        for key in (
            "reviewed",
            "excluded",
            "chosen",
            "outside_candidate_set",
            "verification_statuses",
        )
    } == {
        "reviewed": True,
        "excluded": False,
        "chosen": True,
        "outside_candidate_set": False,
        "verification_statuses": ["passed"],
    }
    outcome_subject = next(
        subject
        for subject in app.records[0]["outcome"]["subject_roles"].values()
        if subject["key"] == "file:app.py"
    )
    assert outcome_subject["kind"] == "file"
    assert outcome_subject["identity"] == {"path": "app.py"}
    assert outcome_subject["version_digest"].startswith("sha256:")
    decoy_frontier = current_completion_frontier(
        tmp_path,
        "main",
        _receipt_file_completion_key(receipt, "decoy.py"),
    )
    decoy_role = decoy_frontier.records[0]["outcome"]["subject_roles"]["file:decoy.py"]
    assert decoy_role["reviewed"] is True
    assert decoy_role["excluded"] is True
    assert decoy_role["chosen"] is False


def test_hot_outcome_projection_is_file_only_and_keeps_changed_entry_witness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_id, _task_path, verification = _public_finish_fixture(
        tmp_path,
        monkeypatch,
        capsys,
        slug="catalogue-file-only-hot-outcome",
        extra_files={"guide.md": "# Guide\n"},
    )
    repo = tmp_path / "repos"
    target = RepoTarget("main", repo, "repos", "reserved")
    selections = [
        ResultSelection(ResultAuthority.SOURCE, "repos/app.py"),
        ResultSelection(ResultAuthority.DOCUMENT, "repos/guide.md"),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"in_file":"app.py","kind":"symbol","value":"run"}',
        ),
        ResultSelection(ResultAuthority.GRAPH, "run->save"),
    ]
    result_id = digest_data({"mixed completion subjects": task_id})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="change app value", mode="auto"),
        selections=selections,
    )
    for selection in selections:
        assert main(
            [
                "task",
                "discovery",
                "add",
                task_id,
                "--result-producer",
                "context",
                "--result-id",
                result_id,
                "--result-authority",
                selection.authority.value,
                "--result-ref",
                selection.ref,
                "--json",
            ]
        ) == 0
        capsys.readouterr()

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    receipt_path = tmp_path / payload["data"]["completion_receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 4
    subject_kinds = {subject["kind"] for subject in receipt["discovery_outcome"]["subjects"]}
    assert subject_kinds == {"file", "document", "symbol", "relationship_fact"}

    file_subject = next(
        subject
        for subject in receipt["discovery_outcome"]["subjects"]
        if subject["kind"] == "file" and subject["identity"] == {"path": "app.py"}
    )
    file_key = versioned_completion_subject_key(file_subject["key"], file_subject["version_digest"])
    paths = completion_catalogue_paths(tmp_path, "main")
    event = json.loads(next(paths.events_directory.glob("*.json")).read_text(encoding="utf-8"))
    assert event["subject_keys"] == [file_key]
    assert event["hot_witnesses"][file_key]["graph"]["changed_entry"] == {
        "change": "modified",
        "path": "app.py",
    }
    ingest_completion_catalogue_tail(tmp_path, "main")
    frontier = current_completion_frontier(tmp_path, "main", file_key)
    assert set(frontier.records[0]["outcome"]["subject_roles"]) == {"file:app.py"}
    exact = lookup_completion_exact(tmp_path, "main", task_id)
    assert exact is not None
    assert {subject["kind"] for subject in exact.receipt["discovery_outcome"]["subjects"]} == subject_kinds


def test_public_task_finish_rolls_back_catalogue_ingress_when_board_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_id, task_path, verification = _public_finish_fixture(
        tmp_path,
        monkeypatch,
        capsys,
        slug="catalogue-finish-rollback",
    )
    original_task = task_path.read_text(encoding="utf-8")
    original_board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    paths = completion_catalogue_paths(tmp_path, "main")
    attempted_catalogue_paths: list[Path] = []
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path == paths.head or paths.events_directory in path.parents:
            attempted_catalogue_paths.append(path)
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert attempted_catalogue_paths
    assert paths.head in attempted_catalogue_paths
    assert any(paths.events_directory in path.parents for path in attempted_catalogue_paths)

    receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{task_id}.json"
    archive_path = tmp_path / "docs/archive/tasks" / task_path.name
    assert task_path.read_text(encoding="utf-8") == original_task
    assert (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8") == original_board
    assert not archive_path.exists()
    assert not receipt_path.exists()
    assert not paths.head.exists()
    assert not paths.events_directory.exists() or not list(paths.events_directory.iterdir())
    assert not paths.catalogue.exists()
    assert not paths.checkpoint.exists()
    assert all(not path.exists() for path in paths.projection_slots)


def test_prepared_sidecars_and_tail_ingest_are_incremental_without_receipt_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _receipt_input(tmp_path, "T-20260813010101Z")
    second = _receipt_input(tmp_path, "T-20260813010102Z")

    with reject_directory_enumeration(
        monkeypatch,
        tmp_path / "docs/tasks/.repoctl-state/completions",
    ) as receipt_reads:
        _prepare_and_publish(tmp_path, first)
        initial = ingest_completion_catalogue_tail(tmp_path, "main")
        assert initial.ingested_count == 1
        assert initial.last_sequence == 1

        _prepare_and_publish(tmp_path, second)
        updated = ingest_completion_catalogue_tail(tmp_path, "main")
        assert updated.ingested_count == 1
        assert updated.last_sequence == 2
        assert ingest_completion_catalogue_tail(tmp_path, "main").ingested_count == 0
    assert receipt_reads == []

    paths = completion_catalogue_paths(tmp_path, "main")
    assert len(paths.catalogue.read_text(encoding="utf-8").splitlines()) == 2
    lookup = current_completion_frontier(tmp_path, "main", file_completion_subject_key("src/app.py"))
    assert [record["task_id"] for record in lookup.records] == [second.receipt["task_id"], first.receipt["task_id"]]


def test_explicit_cold_reads_admit_first_and_later_pending_tails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _receipt_input(tmp_path, "T-20260813010201Z")
    _publish_receipt_authority(tmp_path, first)
    _prepare_and_publish(tmp_path, first)
    paths = completion_catalogue_paths(tmp_path, "main")

    exact = lookup_completion_exact(tmp_path, "main", first.receipt["task_id"])
    assert exact is not None
    assert exact.receipt == first.receipt
    assert completion_catalogue_status(tmp_path, "main").checkpoint_sequence == 1

    with reject_directory_enumeration(
        monkeypatch,
        paths.catalogue.parent,
        allow_reads=lambda path: path != paths.catalogue.resolve(),
    ):
        hot = current_completion_frontier(tmp_path, "main", "file:src/app.py")
        assert hot.records[0]["task_id"] == first.receipt["task_id"]

    second = _receipt_input(
        tmp_path,
        "T-20260813010202Z",
        artifact_text="# Later migration\n\n## Verification\n\nSecond completion decision.\n",
    )
    _publish_receipt_authority(tmp_path, second)
    _prepare_and_publish(tmp_path, second)
    result = search_completion_history(
        tmp_path,
        "main",
        query_terms=["later", "migration"],
    )
    assert [match.record.task_id for match in result.records] == [second.receipt["task_id"]]
    assert result.checkpoint_sequence == 2
    before = paths.catalogue.read_bytes()
    assert lookup_completion_exact(tmp_path, "main", second.receipt["task_id"]) is not None
    assert paths.catalogue.read_bytes() == before
    assert lookup_completion_exact(tmp_path, "main", "T-20260813010203Z") is None


def test_explicit_history_search_matches_natural_language_terms(tmp_path: Path) -> None:
    migration = _receipt_input(
        tmp_path,
        "T-20260813010210Z",
        artifact_text=(
            "---\n"
            'title: "Cache migration"\n'
            "---\n\n"
            "# T-20260813010210Z - Cache migration\n\n"
            "## Discovery\n\nRollback-safe cache migration uses app.py.\n\n"
            "## Scope\n\ncontent_sha256 schema_version 20260813010210 should stay private.\n\n"
            "## Verification\n\nMigration behavior passed.\n"
        ),
    )
    unrelated = _receipt_input(
        tmp_path,
        "T-20260813010211Z",
        changed_paths=("src/other.py",),
        artifact_text="# Other change\n\n## Verification\n\nUpdated request routing.\n",
    )
    for item in (migration, unrelated):
        _publish_receipt_authority(tmp_path, item)
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[migration, unrelated])

    result = search_completion_history(
        tmp_path,
        "main",
        query_terms=["MIGRATION", "rollback"],
    )

    assert [match.record.task_id for match in result.records] == [migration.receipt["task_id"]]
    assert result.records[0].matched_terms == ("migration", "rollback")
    assert result.records[0].exact_task is False
    assert result.scanned_event_count == 2
    assert result.matched_event_count == 1
    assert result.truncated is False

    event = json.loads(completion_catalogue_paths(tmp_path, "main").catalogue.read_text(encoding="utf-8").splitlines()[0])
    assert len(event["search_terms"]) <= 128
    assert event["search_terms_truncated"] is False
    assert {"cache", "migration", "rollback", "src/app.py"} <= set(event["search_terms"])
    for machine_term in ("sha256", "content_sha256", "schema_version", "20260813010210"):
        assert machine_term not in event["search_terms"]
        assert search_completion_history(
            tmp_path,
            "main",
            query_terms=[machine_term],
        ).records == ()


def test_explicit_history_search_bounds_results_and_reports_truncation(tmp_path: Path) -> None:
    items = [
        _receipt_input(
            tmp_path,
            f"T-2026081301023{index}Z",
            changed_paths=(f"src/{index}.py",),
            artifact_text=f"# Shared migration {index}\n\n## Verification\n\nMigration completed.\n",
        )
        for index in range(3)
    ]
    for item in items:
        _publish_receipt_authority(tmp_path, item)
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=items)

    result = search_completion_history(
        tmp_path,
        "main",
        query_terms=["migration"],
        limit=2,
    )

    assert [match.record.task_id for match in result.records] == [
        items[2].receipt["task_id"],
        items[1].receipt["task_id"],
    ]
    assert result.scanned_event_count == 3
    assert result.matched_event_count == 3
    assert result.truncated is True


def test_explicit_history_search_validates_only_selected_authorities(tmp_path: Path) -> None:
    selected = _receipt_input(
        tmp_path,
        "T-20260813010240Z",
        artifact_text="# Selected task\n\n## Verification\n\nShared migration decision.\n",
    )
    unselected = _receipt_input(
        tmp_path,
        "T-20260813010241Z",
        artifact_text="# Newer task\n\n## Verification\n\nShared migration follow-up.\n",
    )
    for item in (selected, unselected):
        _publish_receipt_authority(tmp_path, item)
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[selected, unselected])
    (tmp_path / unselected.receipt_path).unlink()

    result = search_completion_history(
        tmp_path,
        "main",
        query_terms=["migration"],
        task_ids=[str(selected.receipt["task_id"])],
        limit=1,
    )

    assert [match.record.task_id for match in result.records] == [selected.receipt["task_id"]]
    assert result.matched_event_count == 2
    assert result.truncated is True


def test_explicit_history_search_reports_typed_source_mismatch(tmp_path: Path) -> None:
    item = _receipt_input(
        tmp_path,
        "T-20260813010250Z",
        artifact_text="# Original migration\n\n## Verification\n\nOriginal decision.\n",
    )
    _publish_receipt_authority(tmp_path, item)
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])

    replacement_text = "# Revised migration\n\nRevised decision.\n"
    replacement_receipt = dict(item.receipt)
    replacement_receipt["content_sha256"] = _digest(replacement_text)
    (tmp_path / item.artifact_path).write_text(replacement_text, encoding="utf-8")
    (tmp_path / item.receipt_path).write_text(
        json.dumps(replacement_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        search_completion_history(
            tmp_path,
            "main",
            query_terms=[],
            task_ids=[str(item.receipt["task_id"])],
        )

    assert caught.value.reason == CompletionCatalogueUnavailableReason.SOURCE_MISMATCH
    assert caught.value.code == CompletionCatalogueUnavailableReason.SOURCE_MISMATCH.value


@pytest.mark.parametrize(
    ("query_terms", "task_ids", "limit"),
    [
        ([], [], 1),
        (["migration"], ["not-a-task"], 1),
        (["migration"], [], 0),
        (["migration"], [], True),
    ],
)
def test_explicit_history_search_rejects_invalid_or_empty_input(
    tmp_path: Path,
    query_terms: list[str],
    task_ids: list[str],
    limit: int,
) -> None:
    with pytest.raises(ValueError):
        search_completion_history(
            tmp_path,
            "main",
            query_terms=query_terms,
            task_ids=task_ids,
            limit=limit,
        )


def test_bounded_status_does_not_claim_uncatalogued_raw_receipt_coverage(tmp_path: Path) -> None:
    item = _receipt_input(tmp_path, "T-20260813010204Z")
    _publish_receipt_authority(tmp_path, item)

    status = completion_catalogue_status(tmp_path, "main")

    assert status.status == "empty"
    assert status.history_complete is False


def test_hot_frontier_policy_is_finite_and_reports_evicted_history(tmp_path: Path) -> None:
    policy = CompletionCataloguePolicy(
        max_subjects=2,
        max_frontier_per_subject=1,
        max_subjects_per_event=2,
        max_subject_key_bytes=128,
    )
    inputs = [
        _receipt_input(tmp_path, "T-20260813010301Z", changed_paths=("src/one.py", "src/two.py", "src/three.py")),
        _receipt_input(tmp_path, "T-20260813010302Z", changed_paths=("src/two.py",)),
    ]
    for item in inputs:
        _prepare_and_publish(tmp_path, item, policy=policy)
    ingest_completion_catalogue_tail(tmp_path, "main", policy=policy)

    retained = current_completion_frontier(tmp_path, "main", "file:src/two.py", policy=policy)
    assert len(retained.records) == 1
    assert retained.records[0]["task_id"] == "T-20260813010302Z"

    evicted = current_completion_frontier(tmp_path, "main", "file:src/three.py", policy=policy)
    assert evicted.records == ()
    assert evicted.may_have_cold_history is True


def test_hot_projection_stays_subject_local_as_cold_outcome_grows(tmp_path: Path) -> None:
    policy = CompletionCataloguePolicy(
        max_subjects=64,
        max_frontier_per_subject=1,
        max_subjects_per_event=128,
    )

    def projection_size(subject_count: int) -> tuple[int, dict[str, object]]:
        item = _receipt_input(
            tmp_path,
            f"T-20260813{subject_count:06d}Z",
            changed_paths=("src/app.py",),
        )
        subjects = [
            {
                "id": f"s{index}",
                "kind": "file",
                "key": f"file:src/file-{index}.py",
                "identity": {"path": f"src/file-{index}.py"},
                "version_digest": "sha256:" + ("a" * 64),
            }
            for index in range(subject_count)
        ]
        receipt = dict(item.receipt)
        receipt["discovery_outcome"] = {
            "outcome_digest": "sha256:" + ("b" * 64),
            "subjects": subjects,
            "active_chosen": [subject["id"] for subject in subjects],
            "episodes": [
                {
                    "reviewed": [subject["id"] for subject in subjects],
                    "excluded": [],
                    "outside_candidate_set": [],
                }
            ],
            "verification_records": [],
        }
        expanded = CompletionReceiptInput(
            receipt=receipt,
            receipt_path=item.receipt_path,
            receipt_text=json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            artifact_path=item.artifact_path,
            artifact_text=item.artifact_text,
        )
        local_root = tmp_path / f"case-{subject_count}"
        rebuild_completion_catalogue(local_root, "main", receipt_artifacts=[expanded], policy=policy)
        paths = completion_catalogue_paths(local_root, "main")
        checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
        projection_path = paths.projection_slots[int(checkpoint["projection_slot"])]
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        record = next(
            subject["frontier"][0]
            for subject in projection["subjects"]
            if "outcome" in subject["frontier"][0]
        )
        return projection_path.stat().st_size, record

    small_bytes, small_record = projection_size(128)
    large_bytes, large_record = projection_size(5000)

    assert large_bytes <= small_bytes + 1024
    assert len(large_record["outcome"]["subject_roles"]) == 1
    assert len(json.dumps(large_record, separators=(",", ":")).encode("utf-8")) <= policy.max_hot_record_bytes


def test_graph_hot_records_do_not_open_cold_or_event_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _receipt_input(tmp_path, "T-20260813010304Z")
    rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[item])
    paths = completion_catalogue_paths(tmp_path, "main")
    with reject_directory_enumeration(
        monkeypatch,
        paths.catalogue,
        paths.events_directory,
    ):
        records = completion_graph_inputs(tmp_path, "main")

    assert len(records) == 1
    assert records[0].receipt["changed_entries"] == [
        {"change": "modified", "path": "src/app.py"}
    ]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("gap", CompletionCatalogueUnavailableReason.GAP),
        ("schema", CompletionCatalogueUnavailableReason.SCHEMA_MISMATCH),
        ("policy", CompletionCatalogueUnavailableReason.POLICY_MISMATCH),
    ],
)
def test_tail_ingest_fails_closed_with_typed_unavailable(
    tmp_path: Path,
    mutation: str,
    reason: CompletionCatalogueUnavailableReason,
) -> None:
    item = _receipt_input(tmp_path, "T-20260813010401Z")
    _prepare_and_publish(tmp_path, item)
    paths = completion_catalogue_paths(tmp_path, "main")
    if mutation == "gap":
        paths.events_directory.joinpath(paths.head.name).parent.mkdir(parents=True, exist_ok=True)
        paths.events_directory.joinpath(next(paths.events_directory.iterdir()).name).unlink()
    else:
        head = json.loads(paths.head.read_text(encoding="utf-8"))
        if mutation == "schema":
            head["schema_version"] = 999
        else:
            head["policy_digest"] = "sha256:" + ("0" * 64)
        paths.head.write_text(json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        ingest_completion_catalogue_tail(tmp_path, "main")
    assert caught.value.reason == reason
    assert caught.value.code == reason.value


def test_prefix_tamper_is_typed_unavailable_and_explicit_rebuild_recovers(tmp_path: Path) -> None:
    first = _receipt_input(
        tmp_path,
        "T-20260813010501Z",
        changed_paths=("src/app.py", *(f"src/history/{index:03d}/application-module.py" for index in range(128))),
    )
    second = _receipt_input(tmp_path, "T-20260813010502Z", changed_paths=("src/other.py",))
    refresh = rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first, second])
    assert refresh.mode == "rebuild"
    assert refresh.ingested_count == 2
    audit = audit_completion_catalogue(tmp_path, "main")
    assert audit.event_count == 2

    paths = completion_catalogue_paths(tmp_path, "main")
    cold = paths.catalogue.read_text(encoding="utf-8")
    paths.catalogue.write_text(cold.replace("src/app.py", "src/tam.py", 1), encoding="utf-8")
    # A no-op ordinary tail ingest is bounded and deliberately does not reread
    # the cold prefix.  Full-prefix tamper detection belongs to the explicit
    # audit/recovery path.
    assert ingest_completion_catalogue_tail(tmp_path, "main").ingested_count == 0
    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        audit_completion_catalogue(tmp_path, "main")
    assert caught.value.reason == CompletionCatalogueUnavailableReason.CORRUPT

    rebuilt = rebuild_completion_catalogue(tmp_path, "main", receipt_artifacts=[first, second])
    assert rebuilt.last_sequence == 2
    assert audit_completion_catalogue(tmp_path, "main").event_count == 2


def test_public_history_rebuild_recovers_typed_unavailable_catalogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"src/app.py": "value = 1\n"})
    item = _receipt_input(tmp_path, "T-20260813010701Z")
    _publish_receipt_authority(tmp_path, item)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["history", "rebuild", "--repo-id", "main", "--json"]) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["ok"] is True
    assert rebuilt["command"] == "history.rebuild"
    assert rebuilt["data"]["repository"]["id"] == "main"
    assert rebuilt["data"]["mode"] == "rebuild"
    assert rebuilt["data"]["ingested_count"] == 1
    assert rebuilt["data"]["last_sequence"] == 1
    assert audit_completion_catalogue(tmp_path, "main").event_count == 1

    paths = completion_catalogue_paths(tmp_path, "main")
    paths.head.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CompletionCatalogueUnavailable) as caught:
        completion_catalogue_status(tmp_path, "main")
    assert caught.value.reason == CompletionCatalogueUnavailableReason.SCHEMA_MISMATCH

    assert main(["graph", "build", "--repo-id", "main", "--rebuild", "--json"]) == 0
    unavailable = json.loads(capsys.readouterr().out)
    recovery = next(
        action
        for action in unavailable["next_actions"]
        if action.get("kind") == "completion_catalogue_rebuild"
    )
    assert recovery["command"] == (
        "./scripts/repoctl history rebuild --repo-id main --json"
    )
    assert paths.head.read_text(encoding="utf-8") == "{}\n"

    assert main(["history", "rebuild", "--repo-id", "main", "--json"]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["data"]["ingested_count"] == 1
    assert audit_completion_catalogue(tmp_path, "main").event_count == 1
