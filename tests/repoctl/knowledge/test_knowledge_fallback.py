from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.knowledge_projection import knowledge_projection_path
from tests.repoctl.knowledge_test_helpers import _add_knowledge_source, _setup_knowledge_workspace


def payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_sessionplane_projection_missing_uses_direct_v2_record(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    record = _add_knowledge_source(capsys, claim="Single-flight keeps one active generation per key.")["data"]["record"]
    knowledge_projection_path(tmp_path, repo_id="main").unlink()

    assert main(["knowledge", "query", "single-flight generation", "--repo-id", "main", "--full", "--json"]) == 0
    result = payload(capsys)
    assert [item["record"]["id"] for item in result["data"]["results"]] == [record["id"]]
    assert result["data"]["lifecycle"]["projection_status"] == "degraded_direct"
    assert {item["code"] for item in result["warnings"]} == {"knowledge_projection_degraded"}


def test_bible_simple_invalid_projection_uses_direct_current_heads(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    first = _add_knowledge_source(capsys, claim="Manifest entries keep stable source identity.")["data"]["record"]
    second = _add_knowledge_source(capsys, claim="Manifest loading rejects duplicate identities.")["data"]["record"]
    projection = knowledge_projection_path(tmp_path, repo_id="main")
    projection.write_text('{"schema":"broken","heads":["missing-a","missing-b","missing-c"]}\n', encoding="utf-8")

    assert main(["knowledge", "query", "manifest identity", "--repo-id", "main", "--full", "--json"]) == 0
    result = payload(capsys)
    assert {item["record"]["id"] for item in result["data"]["results"]} == {first["id"], second["id"]}
    assert result["data"]["lifecycle"]["projection_status"] == "degraded_direct"
    assert any(item["code"] == "knowledge_projection_degraded" for item in result["warnings"])


def test_direct_record_corruption_remains_an_error_during_fallback(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    record = _add_knowledge_source(capsys, claim="Corrupt originals are never projected as valid.")["data"]["record"]
    (tmp_path / f"docs/knowledge/records/{record['id']}.json").write_text("{not-json\n", encoding="utf-8")
    knowledge_projection_path(tmp_path, repo_id="main").unlink()

    assert main(["knowledge", "query", "corrupt originals", "--repo-id", "main", "--json"]) == 1
    result = payload(capsys)
    assert result["data"]["results"] == []
    assert result["problems"][0]["code"] == "knowledge_record_invalid_json"


def test_equivalent_legacy_record_does_not_repeat_migration_warning(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    current = _add_knowledge_source(capsys, claim="Equivalent legacy knowledge migrates once.")["data"]["record"]
    legacy_id = "K-20260609184045Z--equivalent-legacy"
    (tmp_path / f"docs/knowledge/records/{legacy_id}.json").write_text(
        json.dumps({
            "schema": "repoctl.knowledge.record",
            "schema_version": 1,
            "id": legacy_id,
            "repo_id": "main",
            "kind": current["kind"],
            "claim": current["claim"],
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    result = payload(capsys)
    assert result["data"]["legacy_record_count"] == 0
    assert not any(item["code"] == "knowledge_record_migration_required" for item in result["problems"])


def test_same_claim_with_different_reason_is_not_silently_deduplicated(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    current = _add_knowledge_source(capsys, claim="Shared claim can carry different meaning.")["data"]["record"]
    legacy_id = "K-20260609184045Z--different-meaning"
    (tmp_path / f"docs/knowledge/records/{legacy_id}.json").write_text(
        json.dumps({
            "schema": "repoctl.knowledge.record",
            "schema_version": 1,
            "id": legacy_id,
            "repo_id": "main",
            "kind": current["kind"],
            "claim": current["claim"],
            "reason": "A materially different reason.",
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    result = payload(capsys)
    assert result["data"]["legacy_record_count"] == 1
    assert any(item["code"] == "knowledge_record_migration_required" for item in result["problems"])


def test_source_relocation_is_unique_and_digest_bound(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    old = tmp_path / "docs/contracts/repoctl-context-contract.md"
    record = _add_knowledge_source(capsys, claim="Moved sources retain identity by digest.")["data"]["record"]
    moved = tmp_path / "docs/contracts/moved-context-contract.md"
    old.rename(moved)

    assert main(["knowledge", "query", "moved sources", "--repo-id", "main", "--full", "--json"]) == 0
    result = payload(capsys)
    returned = result["data"]["results"][0]["record"]
    assert returned["id"] == record["id"]
    assert returned["source_resolutions"][0]["status"] == "relocated"
    assert returned["source_resolutions"][0]["resolved_path"] == "docs/contracts/moved-context-contract.md"
    assert not any(item["code"] == "knowledge_source_changed" for item in result["warnings"])

    duplicate = tmp_path / "docs/contracts/duplicate-context-contract.md"
    duplicate.write_bytes(moved.read_bytes())
    assert main(["knowledge", "query", "moved sources", "--repo-id", "main", "--full", "--json"]) == 0
    ambiguous = payload(capsys)
    resolution = ambiguous["data"]["results"][0]["record"]["source_resolutions"][0]
    assert resolution["status"] == "invalid_identity"
    assert resolution["cause_code"] == "source_relocation_ambiguous"
    assert any(item["code"] == "knowledge_source_changed" for item in ambiguous["warnings"])
