from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.context_task_pack import render_task_context_pack_markdown
from tools.repoctl.graph_model import digest_data
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.context_test_helpers import (
    _setup_context_workspace,
    _write_context_pack_task,
)


def _materialize(root: Path, repo_id: str = "main") -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id=repo_id))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def test_context_pack_groups_task_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010101Z",
        slug="context-pack",
        title="Use Evidence Context for Graph authority",
        query="Graph authority context",
        goal="Explain why Graph remains non-authoritative.",
        first_command='./scripts/repoctl context query "Graph authority" --json',
    )
    output = tmp_path / ".repoctl-state/context-pack/T-20260622010101Z.json"
    assert main(["context", "pack", "--task", "T-20260622010101Z", "--repo-id", "main", "--budget-tokens", "1200", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    data = payload["data"]
    assert artifact == payload
    assert payload["command"] == "context pack"
    assert data["authoritative"] is False
    assert data["view"] == "compact"
    assert data["pack_digest"].startswith("sha256:")
    assert data["artifact"] == {
        "path": ".repoctl-state/context-pack/T-20260622010101Z.json",
        "pack_digest": data["pack_digest"],
    }
    assert data["stage"] == "scoped"
    assert data["seed"]["source"] == "discovery_query_history_only"
    assert data["input_digest"].startswith("sha256:")
    assert data["stop_reason"] in {"required_evidence_satisfied", "budget_reached"}
    assert data["budget"]["final_render_estimated_tokens"] <= 1200
    assert any(item["source_ref"]["path"] == "docs/contracts/repoctl-context-contract.md" for item in data["groups"]["must_read"])
    assert data["metrics"]["group_counts"]["must_read"] == len(data["groups"]["must_read"])
    assert data["metrics"]["unique_must_read_source_count"] >= 1
    assert data["metrics"]["requested_tokens"] == 1200
    assert any(ref["path"] == "docs/contracts/repoctl-context-contract.md" for ref in data["metrics"]["must_read_source_refs"])
    assert "bundle" not in data
    assert payload["warnings"][0]["code"] == "context_pack_not_authoritative"


def test_context_pack_keeps_chosen_and_supporting_sets_disjoint(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    task_id = "T-20260622010121Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="disjoint-scope",
        title="Keep task pack scope disjoint",
        query="alpha beta",
        goal="Keep edit and supporting evidence distinct.",
        reviewed="repos/a.py",
        chosen="repos/a.py",
    )
    task_path = next((tmp_path / "docs/tasks").glob(f"{task_id}--*.md"))
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "- Candidate files reviewed: `repos/a.py`",
            "- Candidate files reviewed:\n  - `repos/a.py`\n  - `repos/b.py`",
        ),
        encoding="utf-8",
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--json"]) == 0

    groups = json.loads(capsys.readouterr().out)["data"]["groups"]
    edit = {item["source_ref"]["path"] for item in groups["edit_candidates"]}
    supporting = {item["source_ref"]["path"] for item in groups["supporting_evidence"]}
    assert edit == {"repos/a.py"}
    assert supporting == {"repos/b.py"}
    assert edit.isdisjoint(supporting)


def test_context_pack_never_drops_required_evidence_to_fit_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    context_docs = []
    for index in range(14):
        rel = f"docs/contracts/required-{index}.md"
        context_docs.append(rel)
        (tmp_path / rel).write_text(f"# Required {index}\n\nRequired evidence {index}.\n", encoding="utf-8")
    task_id = "T-20260622010122Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="required-budget",
        title="Preserve required evidence",
        query="run",
        goal="Keep all required startup evidence visible.",
        context_doc=context_docs[0],
    )
    task_path = next((tmp_path / "docs/tasks").glob(f"{task_id}--*.md"))
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            f"- `{context_docs[0]}`",
            "\n".join(f"- `{path}`" for path in context_docs),
        ),
        encoding="utf-8",
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "450", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    required_paths = {
        item["source_ref"]["path"]
        for items in data["groups"].values()
        for item in items
        if item.get("requirement") == "required" and isinstance(item.get("source_ref"), dict)
    }
    assert set(context_docs).issubset(required_paths)
    assert "AGENTS.md" in required_paths
    assert f"docs/tasks/{task_id}--required-budget.md" in required_paths
    assert "repos/app.py" in required_paths
    assert data["stop_reason"] == "required_evidence_exceeds_budget"
    assert data["budget"]["final_render_estimated_tokens"] > data["budget"]["maximum_estimated_tokens"]
    rendered = render_task_context_pack_markdown(data)
    assert all(path in rendered for path in context_docs)
    assert "AGENTS.md" in rendered
    assert f"docs/tasks/{task_id}--required-budget.md" in rendered
    assert "repos/app.py" in rendered


def test_context_pack_compact_filters_noisy_graph_items(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "data").mkdir()
    (repo / "data/runtime.csv").write_text("runtime,state\n1,ignored\n", encoding="utf-8")
    (repo / "public").mkdir()
    (repo / "public/logo.svg").write_text("<svg></svg>\n", encoding="utf-8")
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010111Z",
        slug="compact-noise",
        title="Inspect runtime graph noise",
        query="runtime data logo graph evidence",
        goal="Keep compact context focused on actionable evidence.",
        reviewed="repos/app.py",
        chosen="repos/app.py",
    )

    assert main(["context", "pack", "--task", "T-20260622010111Z", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    compact_text = json.dumps(payload["data"], ensure_ascii=False)
    assert "repos/data/runtime.csv" not in compact_text
    assert "repos/public/logo.svg" not in compact_text
    assert payload["data"]["summary"]["read_first_count"] >= 1
    assert len(compact_text) < 24000
    assert any(warning["code"] == "context_pack_graph_unavailable" for warning in payload["warnings"])


def test_context_pack_uses_startup_fallback_without_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "README.md").write_text("# Product Startup\n\nProduct-specific startup context.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = \"product-startup\"\n", encoding="utf-8")
    task_id = "T-20260622010109Z"
    task_path = tmp_path / "docs/tasks" / f"{task_id}--fallback.md"
    task_path.write_text(
        f"""---
id: {task_id}
title: "Implement product startup flow"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T010109Z
area: "repo"
parent: ""
depends_on: []
---

# {task_id} - Implement product startup flow

## Context Docs

## Discovery

## Goal

Use project context without structured discovery yet.

## Handoff

- Next exact step: read startup evidence.
- First file to open: `docs/PRD.md`
- First command to run: `./scripts/repoctl context pack --task {task_id} --repo-id main --json`
- Done when: startup evidence is visible.
""",
        encoding="utf-8",
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    must_read_paths = {item["source_ref"]["path"] for item in payload["data"]["groups"]["must_read"]}
    warning_codes = {warning["code"] for warning in payload["warnings"]}
    assert "repos/README.md" in must_read_paths
    assert "repos/pyproject.toml" in must_read_paths
    assert "docs/PRD.md" not in must_read_paths
    assert payload["data"]["stage"] == "bootstrap"
    assert "context_pack_no_structured_discovery" in warning_codes


def test_context_pack_includes_manifest_verification_hints(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "package.json").write_text(
        '{"name": "demo", "scripts": {"test": "vitest run", "lint": "eslint .", "build": "vite build"}}\n',
        encoding="utf-8",
    )
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010112Z",
        slug="verification-hints",
        title="Improve frontend verification hints",
        query="frontend verification",
        goal="Surface project verification commands.",
    )

    assert main(["context", "pack", "--task", "T-20260622010112Z", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    verification = payload["data"]["groups"]["verification"]
    command_text = "\n".join(item.get("excerpt", "") for item in verification)
    assert "npm test" in command_text
    assert "npm run lint" in command_text
    assert "npm run build" in command_text
    assert any(item["source_ref"]["kind"] == "verification_hint" and item["source_ref"]["path"] == "repos/package.json" for item in verification)




def test_context_pack_markdown_is_agent_consumable(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010103Z",
        slug="agent-pack",
        title="Change validate token behavior",
        query="What calls validate_token?",
        goal="Change validate_token behavior without missing callers.",
        reviewed="repos/auth/flow.py",
        chosen="repos/auth/flow.py",
        first_command="./scripts/repoctl context pack --task T-20260622010103Z --repo-id main --format markdown",
    )
    _materialize(tmp_path)
    output = tmp_path / ".repoctl-state/context-pack/T-20260622010103Z.md"

    assert main(["context", "pack", "--task", "T-20260622010103Z", "--repo-id", "main", "--format", "markdown", "--output", output.as_posix()]) == 0

    stdout = capsys.readouterr().out
    artifact = output.read_text(encoding="utf-8")
    assert stdout == "context pack written: .repoctl-state/context-pack/T-20260622010103Z.md\n"
    assert "# Agent Context Pack" in artifact
    assert "## Task Startup Order" in artifact
    assert "## Definitions, Callers, Imports, Dependents" in artifact
    assert "login --CALLS--> validate_token" in artifact
    assert "Context Pack is read-only evidence" not in artifact

    assert main(["context", "pack", "--task", "T-20260622010103Z", "--repo-id", "main", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    groups = payload["data"]["groups"]
    assert "likely_change" in groups
    assert "impact" in groups
    assert "verification" in groups
    assert "warnings" in groups
    assert any("login --CALLS--> validate_token" in str(item.get("excerpt", "")) for item in groups["impact"])


def test_context_pack_warns_on_incomplete_graph_code_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010102Z",
        slug="context-pack-parse-warning",
        title="Inspect parse warning context",
        query="parse warning",
        goal="Inspect parse warning context.",
        reviewed="repos/broken.py",
        chosen="repos/broken.py",
        first_command="./scripts/repoctl context pack --task T-20260622010102Z --repo-id main --json",
    )
    _materialize(tmp_path)
    assert main(["context", "pack", "--task", "T-20260622010102Z", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["bundle"]["completeness"]["graph_completeness"]["parse_error_count"] == 1
    assert any(warning["code"] == "context_pack_graph_code_facts_incomplete" for warning in payload["warnings"])


def test_context_pack_rejects_output_symlink_escape(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622011111Z",
        slug="context-pack-boundary",
        title="Keep context pack output inside workspace",
        query="context pack boundary",
        goal="Reject context pack output outside the workspace.",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    escape = tmp_path.parent / f"{tmp_path.name}-context-pack-escape"
    escape.mkdir()
    symlink = tmp_path / ".repoctl-state/context-pack"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(escape, target_is_directory=True)

    assert main(["context", "pack", "--task", "T-20260622011111Z", "--repo-id", "main", "--output", ".repoctl-state/context-pack/out.json", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_pack_output_outside_workspace"
    assert not (escape / "out.json").exists()


def test_context_pack_does_not_load_unrelated_knowledge_history(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622012121Z",
        slug="failed-pack",
        title="Reject failed context pack artifact",
        query="source authority knowledge",
        goal="Do not write failed context pack artifacts.",
    )
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    event_id = json.loads(capsys.readouterr().out)["data"]["event"]["id"]
    event_path = tmp_path / "docs/knowledge/events" / f"{event_id}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["record_digest"] = "sha256:" + "6" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / ".repoctl-state/context-pack/failed.json"

    assert main(["context", "pack", "--task", "T-20260622012121Z", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"] == []
    assert "reviewed_knowledge" not in payload["data"]["groups"]
    assert output.exists()
