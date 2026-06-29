from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tests.repoctl.knowledge_test_helpers import (
    _read_event,
    _write_event,
    _write_knowledge_docs,
    _setup_knowledge_workspace,
    init_repo,
    write_repometa,
    write_workspace,
)


def test_knowledge_render_rejects_invalid_lifecycle_events(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "2" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)
    output = tmp_path / "docs/knowledge/generated/invalid"

    assert main(["knowledge", "render", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["rendered"] == []
    assert payload["data"]["event_checks"]["error_count"] == 1
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"
    assert not output.exists()


def test_knowledge_render_generated_view_is_not_context_source(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    assert render_payload["data"]["event_count"] == 1
    manifest_path = tmp_path / render_payload["data"]["manifest"]["path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "repoctl.knowledge.render_manifest"
    assert manifest["render_digest"] == render_payload["data"]["render_digest"]
    assert manifest["manifest_digest"] == render_payload["data"]["manifest"]["digest"]
    assert manifest["rendered"] == render_payload["data"]["rendered"]
    rendered_paths = {item["path"] for item in render_payload["data"]["rendered"]}
    assert "docs/knowledge/generated/INDEX.md" in rendered_paths
    assert "docs/knowledge/generated/decisions.md" in rendered_paths
    rendered_by_path = {item["path"]: item for item in render_payload["data"]["rendered"]}
    decisions_bundle = rendered_by_path["docs/knowledge/generated/decisions.md"]["source_bundle"]
    assert decisions_bundle["record_ids"]
    assert decisions_bundle["source_refs"][0]["path"] == "docs/contracts/repoctl-context-contract.md"
    assert decisions_bundle["source_statuses"] == [
        {
            "path": "docs/contracts/repoctl-context-contract.md",
            "section": "Decision",
            "content_sha256": decisions_bundle["source_refs"][0]["content_sha256"],
            "status": "current",
        }
    ]
    assert decisions_bundle["source_status_counts"] == {"current": 1}
    assert decisions_bundle["event_ids"] == [approved_event["id"]]
    assert decisions_bundle["source_bundle_digest"].startswith("sha256:")
    index_bundle = rendered_by_path["docs/knowledge/generated/INDEX.md"]["source_bundle"]
    assert index_bundle["record_ids"] == decisions_bundle["record_ids"]
    assert index_bundle["event_ids"] == [approved_event["id"]]
    index_text = (tmp_path / "docs/knowledge/generated/INDEX.md").read_text(encoding="utf-8")
    assert "- Events: 1" in index_text
    assert "- Events digest: sha256:" in index_text
    assert "## Lifecycle" in index_text
    assert "- reviewed: 1" in index_text
    assert "- stale: 0" in index_text
    assert "- superseded: 0" in index_text
    assert "- deprecated: 0" in index_text
    assert "### Reviewed" in index_text
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert "Non-authoritative generated view" in decisions_text
    record_id = decisions_bundle["record_ids"][0]
    assert f"records/{record_id}.md" in decisions_text
    record_text = (tmp_path / "docs/knowledge/generated/records" / f"{record_id}.md").read_text(encoding="utf-8")
    assert f"- Lifecycle events: `{approved_event['id']}`" in record_text
    assert "status=`current`" in record_text
    assert "docs/contracts/repoctl-context-contract.md#Decision" in record_text

    assert main(["context", "query", "Knowledge Index", "--repo-id", "main", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    refs = [candidate["source_ref"]["path"] for candidate in context_payload["data"]["bundle"]["candidates"]]
    assert all(not path.startswith("docs/knowledge/generated/") for path in refs)


def test_knowledge_render_is_deterministic(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    first_payload = json.loads(capsys.readouterr().out)
    first_files = {
        item["path"]: (tmp_path / item["path"]).read_text(encoding="utf-8")
        for item in first_payload["data"]["rendered"]
    }

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    second_files = {
        item["path"]: (tmp_path / item["path"]).read_text(encoding="utf-8")
        for item in second_payload["data"]["rendered"]
    }

    assert first_payload["data"]["render_digest"] == second_payload["data"]["render_digest"]
    assert first_payload["data"]["rendered"] == second_payload["data"]["rendered"]
    assert first_files == second_files


def test_knowledge_render_removes_manifest_owned_stale_pages_only(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    first_payload = json.loads(capsys.readouterr().out)
    manifest_path = tmp_path / first_payload["data"]["manifest"]["path"]
    generated_dir = manifest_path.parent
    stale_page = generated_dir / "old-decisions.md"
    stale_page.write_text("# Old generated page\n", encoding="utf-8")
    unowned_note = generated_dir / "operator-note.md"
    unowned_note.write_text("local note\n", encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rendered"].append(
        {
            "path": "docs/knowledge/generated/old-decisions.md",
            "digest": "sha256:old",
            "source_bundle": {"record_ids": [], "source_refs": [], "event_ids": []},
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["removed"] == ["docs/knowledge/generated/old-decisions.md"]
    assert not stale_page.exists()
    assert unowned_note.read_text(encoding="utf-8") == "local note\n"


def test_knowledge_render_check_detects_current_and_stale_outputs_without_writing(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    missing_payload = json.loads(capsys.readouterr().out)
    assert missing_payload["data"]["mode"] == "check"
    assert missing_payload["problems"][0]["code"] == "knowledge_render_manifest_missing"
    assert not (tmp_path / "docs/knowledge/generated").exists()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 0
    current_payload = json.loads(capsys.readouterr().out)
    assert current_payload["data"]["check"] == {
        "current": True,
        "missing_pages": [],
        "stale_pages": [],
        "unreadable_pages": [],
        "stale_owned_pages": [],
        "broken_links": [],
    }

    decisions_path = tmp_path / "docs/knowledge/generated/decisions.md"
    original_decisions = decisions_path.read_text(encoding="utf-8")
    (tmp_path / "docs/contracts/repoctl-context-contract.md").write_text("# Changed\n\n## Decision\n\nChanged source.\n", encoding="utf-8")

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    stale_payload = json.loads(capsys.readouterr().out)
    problem_codes = {problem["code"] for problem in stale_payload["problems"]}
    assert "knowledge_render_manifest_stale" in problem_codes
    assert "knowledge_render_page_stale" in problem_codes
    assert stale_payload["data"]["check"]["current"] is False
    stale_pages = set(stale_payload["data"]["check"]["stale_pages"])
    assert "docs/knowledge/generated/INDEX.md" in stale_pages
    assert "docs/knowledge/generated/decisions.md" in stale_pages
    assert any(path.startswith("docs/knowledge/generated/records/") for path in stale_pages)
    assert decisions_path.read_text(encoding="utf-8") == original_decisions


def test_knowledge_render_check_reports_unreadable_generated_page(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    bad_page = tmp_path / "docs/knowledge/generated/invariants.md"
    bad_page.write_bytes(b"\xff\xfe\xfd")

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_render_page_unreadable"
    assert payload["data"]["check"]["unreadable_pages"] == ["docs/knowledge/generated/invariants.md"]
    assert bad_page.read_bytes() == b"\xff\xfe\xfd"


def test_knowledge_render_rejects_output_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    outside = tmp_path.parent / f"{tmp_path.name}-knowledge-render"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1
    outside_payload = json.loads(capsys.readouterr().out)
    assert outside_payload["problems"][0]["code"] == "knowledge_render_output_outside_workspace"
    assert not outside.exists()

    escape = tmp_path.parent / f"{tmp_path.name}-render-escape"
    escape.mkdir()
    symlink = tmp_path / "docs/knowledge/generated"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(escape, target_is_directory=True)

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 1
    symlink_payload = json.loads(capsys.readouterr().out)
    assert symlink_payload["problems"][0]["code"] == "knowledge_render_output_outside_workspace"
    assert not (escape / "INDEX.md").exists()


def test_knowledge_render_rejects_context_source_output_path(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    output = tmp_path / "docs/knowledge/rendered"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_render_output_not_generated"
    assert not output.exists()

    safe_output = tmp_path / "docs/knowledge/generated/snapshot"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", safe_output.as_posix(), "--json"]) == 0
    safe_payload = json.loads(capsys.readouterr().out)
    assert safe_payload["data"]["output"] == "docs/knowledge/generated/snapshot"
    assert (safe_output / "INDEX.md").is_file()


def test_knowledge_render_defaults_to_repo_namespaced_output_for_multirepo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    (tmp_path / "docs/repoctl.json").write_text(
        json.dumps({"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "web", "--json"]) == 0
    web_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", web_candidate, "--repo-id", "web", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "api", "--json"]) == 0
    api_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", api_candidate, "--repo-id", "api", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "web", "--json"]) == 0
    web_render = json.loads(capsys.readouterr().out)
    assert main(["knowledge", "render", "--repo-id", "api", "--json"]) == 0
    api_render = json.loads(capsys.readouterr().out)

    assert web_render["data"]["output"] == "docs/knowledge/generated/web"
    assert api_render["data"]["output"] == "docs/knowledge/generated/api"
    assert (tmp_path / "docs/knowledge/generated/web/manifest.json").is_file()
    assert (tmp_path / "docs/knowledge/generated/api/manifest.json").is_file()
    web_manifest = json.loads((tmp_path / "docs/knowledge/generated/web/manifest.json").read_text(encoding="utf-8"))
    api_manifest = json.loads((tmp_path / "docs/knowledge/generated/api/manifest.json").read_text(encoding="utf-8"))
    assert web_manifest["repo_id"] == "web"
    assert api_manifest["repo_id"] == "api"

    assert main(["knowledge", "render", "--repo-id", "web", "--check", "--json"]) == 0
    web_check = json.loads(capsys.readouterr().out)
    assert web_check["data"]["check"]["current"] is True
