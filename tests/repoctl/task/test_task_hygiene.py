from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.markdown import replace_section
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace


def test_task_log_rejects_machine_output_and_preserves_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_path = add_board_task(
        tmp_path,
        "T-20260609184046Z--alpha.md",
        task_text("T-20260609184046Z", status="doing"),
    )
    before = task_path.read_bytes()
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    message = 'residue_paths=["repos/a.py", "repos/b.py"]'
    assert main(["task", "log", "append", "T-20260609184046Z", message, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_log_detail_requires_reference"
    assert task_path.read_bytes() == before


def test_task_audit_reports_known_slop_without_blocking(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    text = replace_section(
        text,
        "Execution Log",
        '- 20260609T184046Z: task canceled: superseded; residue_paths=["repos/a.py"]; baseline_conflicts=[]\n',
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "audit", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["finding_count"] == 1
    assert payload["warnings"][0]["code"] == "task_log_raw_cancel_paths"

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) in {0, 1}
    doctor = json.loads(capsys.readouterr().out)
    assert any(item["code"] == "task_log_raw_cancel_paths" for item in doctor["warnings"])
    assert doctor["data"]["content_audit"]["bytes"] > 0


def test_task_slop_migration_is_dry_run_then_applies_only_recognized_lines(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    text = replace_section(
        text,
        "Execution Log",
        '- 20260609T184046Z: task canceled: superseded; residue_paths=["repos/a.py", "repos/b.py"]; baseline_conflicts=[]\n'
        '- 20260609T184047Z: scope changed: removed repos/a.py; added repos/c.py, repos/d.py; reason=moved\n'
        '- authored note that must remain byte-for-byte.\n',
    )
    task_path = add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    before = task_path.read_bytes()

    assert main(["task", "migrate-slop", "T-20260609184046Z", "--json"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["data"]["mode"] == "dry_run"
    assert dry_run["data"]["candidate_task_count"] == 1
    assert dry_run["data"]["tasks"][0]["change_count"] == 2
    assert task_path.read_bytes() == before

    assert main(["task", "migrate-slop", "T-20260609184046Z", "--apply", "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["data"]["applied_task_count"] == 1
    migrated = task_path.read_text(encoding="utf-8")
    assert "residue_count=2" in migrated
    assert "removed_count=1; added_count=2" in migrated
    assert "residue_paths=[" not in migrated
    assert "authored note that must remain byte-for-byte" in migrated


def _slop_task_text(task_id: str, *, status: str = "doing") -> str:
    return replace_section(
        task_text(task_id, status=status),
        "Execution Log",
        '- 20260609T184046Z: task canceled: superseded; residue_paths=["repos/a.py"]; baseline_conflicts=[]\n',
    )


def test_task_slop_never_rewrites_examples_or_fences() -> None:
    from tools.repoctl.tasks import migrate_task_slop_text

    example = '- 20260609T184046Z: task canceled: explanation; residue_paths=["repos/a.py"]\n'
    log = '## Execution Log\n\n- 20260609T184046Z: scope changed: removed repos/a.py; added repos/b.py; reason=updated\n'
    document = (
        '## Design\n\nThe following example must be preserved.\n' + example +
        '```markdown\n' + example + '```\n' + log +
        '```text\n' + example + '```\n' +
        '- 20260609T184047Z: task canceled: superseded; residue_paths=["repos/a.py"]; baseline_conflicts=[]\n'
    )
    migrated, changes = migrate_task_slop_text(document)
    assert len(changes) == 2
    assert changes[0]['kind'] == 'scope_path_dump'
    assert changes[1]['kind'] == 'cancel_path_dump'
    assert migrated.count(example) == 3
    assert 'detail_ref=' not in migrated
    assert 'removed_count=1; added_count=1' in migrated
    assert 'residue_count=1; baseline_conflict_count=0' in migrated


def test_closed_child_slop_is_not_mutated_by_default_apply(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    path = add_board_task(
        tmp_path, 'T-20260609184046Z--done-child.md',
        _slop_task_text('T-20260609184046Z', status='done'),
    )
    # This task is in docs/tasks/ but has a terminal status and may have a
    # durable receipt; its unarchived location must not authorize edits.
    before = path.read_bytes()
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    assert main(['task', 'migrate-slop', '--apply', '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['data']['applied_task_count'] == 0
    assert data['data']['skipped_closed_count'] == 1
    assert data['data']['tasks'][0]['status'] == 'skipped_closed_receipt_protected'
    assert path.read_bytes() == before


def test_task_log_accepts_short_multiline_decision_and_bounds_huge_line(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    path = add_board_task(
        tmp_path, 'T-20260609184046Z--short-decision.md',
        task_text('T-20260609184046Z', status='doing'),
    )
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    message = 'Decision: use current input\nReason: preserves correctness\nConstraint: no stale cache\nResult: verified locally'
    assert main(['task','log','append','T-20260609184046Z',message,'--json']) == 0
    capsys.readouterr()
    rendered = path.read_text(encoding='utf-8')
    assert all(line in rendered for line in message.splitlines())
    assert main(['task','log','append','T-20260609184046Z','a' * 228000,'--json']) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['problems'][0]['code'] == 'task_log_detail_requires_reference'
    assert path.read_text(encoding='utf-8') == rendered


def test_task_migration_rolls_back_written_files_after_write_failure(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from tools.repoctl import cli

    write_workspace(tmp_path)
    original_paths = [
        add_board_task(tmp_path, f'T-2026060918404{i}Z--task{i}.md', _slop_task_text(f'T-2026060918404{i}Z'))
        for i in (6, 7)
    ]
    originals = [path.read_bytes() for path in original_paths]
    original_atomic = cli.atomic_write
    calls = 0

    def write_and_fail(path, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated mid-batch failure')
        return original_atomic(path, text)

    monkeypatch.setattr(cli, 'atomic_write', write_and_fail)
    with pytest.raises(OSError, match='simulated mid-batch'):
        cli._task_migrate_slop_payload(tmp_path, apply=True)
    assert [path.read_bytes() for path in original_paths] == originals


def test_task_migration_detects_external_change_before_commit(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from tools.repoctl import cli

    write_workspace(tmp_path)
    path = add_board_task(
        tmp_path, 'T-20260609184046Z--task.md', _slop_task_text('T-20260609184046Z'),
    )
    original_parser = cli.migrate_task_slop_text

    def raced_parser(value):
        parsed = original_parser(value)
        path.write_text(value + '\n# external update\n', encoding='utf-8')
        return parsed

    monkeypatch.setattr(cli, 'migrate_task_slop_text', raced_parser)
    with pytest.raises(Exception, match='Task changed'):
        cli._task_migrate_slop_payload(tmp_path, apply=True)
    assert path.read_text(encoding='utf-8').endswith('# external update\n')


def test_closed_archived_done_migration_keeps_receipt_and_historical_catalogue(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.repoctl.task_lifecycle_helpers import init_product_repo, commit_all
    from tools.repoctl.tasks import completion_receipt_artifact_for_task, collect_completion_receipt_collection
    from tools.repoctl.task_history_migration import receipt_witness, proof_paths

    write_workspace(tmp_path)
    task_id = 'T-20260609184046Z'
    text = _slop_task_text(task_id, status='todo').replace('area: ""', 'area: "ops"')
    add_board_task(tmp_path, f'{task_id}--finish-safe.md', text)
    repo = tmp_path / 'repos'
    init_product_repo(repo)
    commit_all(repo)
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)

    assert main(['task', 'start', task_id, '--json']) == 0
    capsys.readouterr()
    assert main(['task', 'finish', task_id, '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    archived = tmp_path / payload['data']['new_path']
    receipt_path = tmp_path / payload['data']['completion_receipt']
    original, receipt_bytes = archived.read_bytes(), receipt_path.read_bytes()
    receipt, problems = completion_receipt_artifact_for_task(tmp_path, task_id=task_id, audit_history=True)
    assert problems == [] and receipt is not None
    assert receipt.content_sha256.endswith(__import__('hashlib').sha256(original).hexdigest())
    collection = collect_completion_receipt_collection(tmp_path)
    assert collection.problems == ()

    assert main(['task', 'migrate-slop', task_id, '--closed', '--json']) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry['data']['tasks'][0]['status'] == 'planned_closed'
    assert archived.read_bytes() == original

    assert main(['task', 'migrate-slop', task_id, '--closed', '--apply', '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['data']['tasks'][0]['status'] == 'applied_closed'
    assert 'residue_count=1' in archived.read_text()
    assert archived.read_bytes() != original
    assert receipt_path.read_bytes() == receipt_bytes
    original_path, manifest_path = proof_paths(tmp_path, task_id)
    assert original_path.read_bytes() == original
    assert manifest_path.is_file()
    witness = receipt_witness(tmp_path, task_id, receipt_path=receipt_path.relative_to(tmp_path).as_posix())
    assert witness.status == 'valid'
    artifact, problems = completion_receipt_artifact_for_task(tmp_path, task_id=task_id, audit_history=True)
    assert problems == [] and artifact is not None
    assert artifact.content_sha256.endswith(__import__('hashlib').sha256(original).hexdigest())
    collection = collect_completion_receipt_collection(tmp_path)
    assert collection.problems == ()
    from tools.repoctl.completion_catalogue import audit_completion_catalogue
    audited = audit_completion_catalogue(tmp_path, receipt_artifacts=collection.artifacts)
    assert audited.source_checked and task_id in audited.task_ids

    archived.write_text(archived.read_text() + 'tamper\n')
    artifact, problems = completion_receipt_artifact_for_task(tmp_path, task_id=task_id, audit_history=True)
    assert artifact is None
    assert any(problem.code == 'invalid_completion_receipt' for problem in problems)


def test_explicit_closed_canceled_task_keeps_original_witness(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools.repoctl.task_history_migration import proof_paths, receipt_witness
    write_workspace(tmp_path)
    task_id = 'T-20260609184046Z'
    path = add_board_task(tmp_path, f'{task_id}--canceled-child.md', _slop_task_text(task_id, status='canceled'))
    original = path.read_bytes()
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    assert main(['task', 'migrate-slop', task_id, '--closed', '--apply', '--json']) == 0
    capsys.readouterr()
    proof, manifest = proof_paths(tmp_path, task_id)
    assert proof.read_bytes() == original
    assert manifest.is_file()
    assert receipt_witness(tmp_path, task_id).status == 'valid'
    assert 'residue_count=1' in path.read_text()


def test_completed_task_missing_receipt_stays_unmodified(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from tools.repoctl import cli
    write_workspace(tmp_path)
    task_id = 'T-20260609184046Z'
    path = add_board_task(tmp_path, f'{task_id}--closed-child.md', _slop_task_text(task_id, status='done'))
    original = path.read_bytes()
    with pytest.raises(Exception, match='no safe receipt'):
        cli._task_migrate_slop_payload(tmp_path, task_id=task_id, apply=True, closed=True)
    assert path.read_bytes() == original
