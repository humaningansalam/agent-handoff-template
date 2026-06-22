from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.test_check import write_workspace
from tests.repoctl.test_meta_check import write_repometa
from tests.repoctl.test_repositories import init_repo


def _write_knowledge_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/plans").mkdir(parents=True, exist_ok=True)
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# Evidence Context Authority\n\n## Decision\n\nContext returns source bundles but does not create authoritative knowledge.\n\n## Authority Rules\n\nReviewed knowledge requires explicit human approval.\n",
        encoding="utf-8",
    )
    (root / "docs/plans/private-plan.md").write_text("# Private Plan\n\nDo not ingest this.\n", encoding="utf-8")


def test_knowledge_candidate_build_list_show(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    build_payload = json.loads(capsys.readouterr().out)
    candidate = build_payload["data"]["candidate"]
    assert candidate["schema"] == "repoctl.knowledge.candidate"
    assert candidate["authoritative"] is False
    assert candidate["status"] == "candidate"
    assert candidate["source_refs"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert candidate["source_refs"][0]["content_sha256"].startswith("sha256:")
    assert build_payload["warnings"][0]["code"] == "knowledge_candidate_not_authoritative"

    assert main(["knowledge", "candidate", "list", "--repo-id", "main", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in list_payload["data"]["candidates"]] == [candidate["id"]]

    assert main(["knowledge", "candidate", "show", candidate["id"], "--repo-id", "main", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["candidate"]["candidate_digest"] == candidate["candidate_digest"]

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]
    assert second_candidate["id"] != candidate["id"]


def test_knowledge_candidate_rejects_plans_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/plans/private-plan.md", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_excluded"


def test_knowledge_approve_show_check_and_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    record = approve_payload["data"]["record"]
    assert record["schema"] == "repoctl.knowledge.record"
    assert record["status"] == "reviewed"
    assert record["authoritative"] is True
    assert record["id"].startswith("K-")
    assert approve_payload["data"]["event"]["type"] == "approved"
    assert record["id"].lower().replace("--", "-") in approve_payload["data"]["event"]["id"]

    assert main(["knowledge", "show", record["id"], "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["record"]["record_digest"] == record["record_digest"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["record_count"] == 1
    assert check_payload["problems"] == []

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 1
    assert query_payload["data"]["results"][0]["record"]["id"] == record["id"]
    assert query_payload["data"]["results"][0]["record"]["status"] == "reviewed"

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    stale_excluded = json.loads(capsys.readouterr().out)
    assert stale_excluded["data"]["result_count"] == 0
    assert stale_excluded["warnings"][0]["code"] == "knowledge_stale_record_excluded"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-stale", "--json"]) == 0
    stale_included = json.loads(capsys.readouterr().out)
    assert stale_included["data"]["results"][0]["record"]["status"] == "stale"

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["problems"][0]["code"] == "knowledge_source_digest_drift"
    assert drift_payload["data"]["records"][0]["status"] == "stale"
