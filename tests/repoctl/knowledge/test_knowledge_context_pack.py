from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import _write_context_pack_task
from tests.repoctl.knowledge_test_helpers import (
    _write_knowledge_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def test_knowledge_candidate_builds_from_context_pack_authority_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622070707Z",
        slug="pack-backed",
        title="Promote context pack authority",
        query="repoctl Context contract",
        goal="Create a candidate from a context pack without making the pack an authority source.",
        first_command="./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260622070707Z.json --repo-id main --json",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    pack = tmp_path / ".repoctl-state/context-pack/T-20260622070707Z.json"

    assert main(["context", "pack", "--task", "T-20260622070707Z", "--repo-id", "main", "--output", pack.as_posix(), "--json"]) == 0
    pack_payload = json.loads(capsys.readouterr().out)

    assert main(["knowledge", "candidate", "build", "--from-pack", pack.as_posix(), "--repo-id", "main", "--kind", "decision", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    candidate = payload["data"]["candidate"]
    assert candidate["authoritative"] is False
    assert candidate["source_refs"][0]["path"] == "docs/contracts/repoctl-context-contract.md"
    assert candidate["source_refs"][0]["content_sha256"].startswith("sha256:")
    assert candidate["derived_from"] == {
        "kind": "context_pack",
        "path": ".repoctl-state/context-pack/T-20260622070707Z.json",
        "pack_digest": pack_payload["data"]["pack_digest"],
    }
    assert "context pack was used only to select authority source refs" in candidate["review"]["checklist"]
    assert all(not ref["path"].startswith(".repoctl-state/") for ref in candidate["source_refs"])

    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["checks"]["pack_provenance_current"] is True
    assert check_payload["warnings"] == []

    failed_pack_payload = json.loads(pack.read_text(encoding="utf-8"))
    failed_pack_payload["ok"] = False
    failed_pack_payload["problems"] = [{"severity": "error", "code": "synthetic_failure", "message": "failed"}]
    failed_pack = tmp_path / ".repoctl-state/context-pack/failed.json"
    failed_pack.write_text(json.dumps(failed_pack_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", failed_pack.as_posix(), "--repo-id", "main", "--kind", "decision", "--json"]) == 1
    failed_payload = json.loads(capsys.readouterr().out)
    assert failed_payload["problems"][0]["code"] == "knowledge_candidate_pack_failed"

    pack.unlink()
    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    missing_pack_payload = json.loads(capsys.readouterr().out)
    assert missing_pack_payload["ok"] is True
    assert missing_pack_payload["data"]["checks"]["source_refs_valid"] is True
    assert missing_pack_payload["data"]["checks"]["pack_provenance_current"] is False
    assert missing_pack_payload["warnings"][0]["code"] == "knowledge_candidate_pack_provenance_missing"

    assert main(["knowledge", "approve", candidate["id"], "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    assert approve_payload["warnings"][0]["code"] == "knowledge_candidate_pack_provenance_missing"
    assert approve_payload["data"]["record"]["created_from"]["candidate_check"]["warning_codes"] == ["knowledge_candidate_pack_provenance_missing"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    record_id = approve_payload["data"]["record"]["id"]
    assert f"records/{record_id}.md" in decisions_text
    record_text = (tmp_path / "docs/knowledge/generated/records" / f"{record_id}.md").read_text(encoding="utf-8")
    assert f"- Approved from candidate: `{candidate['id']}`" in record_text
    assert "- Candidate warnings: `knowledge_candidate_pack_provenance_missing`" in record_text


def test_context_pack_promotes_to_reviewed_knowledge_cleanly(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622090909Z",
        slug="pack-clean",
        title="Promote clean context pack",
        query="repoctl Context contract",
        goal="Promote a current context pack into reviewed knowledge.",
        first_command="./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260622090909Z.json --repo-id main --json",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    pack = tmp_path / ".repoctl-state/context-pack/T-20260622090909Z.json"

    assert main(["context", "pack", "--task", "T-20260622090909Z", "--repo-id", "main", "--output", pack.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--from-pack", pack.as_posix(), "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]

    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["warnings"] == []
    assert check_payload["data"]["checks"]["pack_provenance_current"] is True

    assert main(["knowledge", "approve", candidate["id"], "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    record = approve_payload["data"]["record"]
    assert approve_payload["warnings"] == []
    assert record["created_from"]["candidate_check"]["warning_codes"] == []

    assert main(["knowledge", "query", "context returns source bundles", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["results"][0]["record"]["id"] == record["id"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 0
    render_check = json.loads(capsys.readouterr().out)
    assert render_check["data"]["check"]["current"] is True


def test_knowledge_candidate_from_context_pack_rejects_drift_and_generated_pack(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622080808Z",
        slug="pack-drift",
        title="Reject stale context pack",
        query="repoctl Context contract",
        goal="Reject stale context pack inputs.",
        first_command="./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260622080808Z.json --repo-id main --json",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    pack = tmp_path / ".repoctl-state/context-pack/T-20260622080808Z.json"

    assert main(["context", "pack", "--task", "T-20260622080808Z", "--repo-id", "main", "--output", pack.as_posix(), "--json"]) == 0
    capsys.readouterr()
    outside_pack = tmp_path.parent / f"{tmp_path.name}-outside-pack.json"
    outside_pack.write_text(pack.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["knowledge", "candidate", "build", "--from-pack", outside_pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    outside_payload = json.loads(capsys.readouterr().out)
    assert outside_payload["problems"][0]["code"] == "knowledge_candidate_pack_outside_workspace"

    generated_pack = tmp_path / "docs/knowledge/generated/pack.json"
    generated_pack.parent.mkdir(parents=True, exist_ok=True)
    generated_pack.write_text(pack.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", generated_pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    generated_payload = json.loads(capsys.readouterr().out)
    assert generated_payload["problems"][0]["code"] == "knowledge_candidate_pack_generated"

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after pack.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["problems"][0]["code"] == "knowledge_candidate_pack_source_drift"
    assert drift_payload["problems"][0]["path"] == "docs/contracts/repoctl-context-contract.md"
