from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.context_task_pack import compact_task_context_pack, render_task_context_pack_markdown
from tools.repoctl.graph_model import digest_data
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tools.repoctl.result_receipts import ResultProducer, result_receipt_path
from tests.repoctl.context_test_helpers import (
    _setup_context_workspace,
    _setup_context_multirepo_workspace,
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
    assert payload["command"] == "context.pack"
    assert data["schema_version"] == 4
    assert data["authoritative"] is False
    assert data["view"] == "compact"
    assert data["pack_digest"].startswith("sha256:")
    assert data["artifact"] == {
        "path": ".repoctl-state/context-pack/T-20260622010101Z.json",
        "pack_digest": data["pack_digest"],
    }
    assert data["stage"] == "scoped"
    assert data["seed"]["source"] == "current_discovery_episode"
    assert data["input_digest"].startswith("sha256:")
    assert data["render_projection"] == "full"
    assert data["stop_reason"] in {"required_evidence_satisfied", "budget_reached"}
    assert data["budget"]["final_render_estimated_tokens"] <= 1200
    assert any(item["source_ref"]["path"] == "docs/contracts/repoctl-context-contract.md" for item in data["groups"]["must_read"])
    assert data["metrics"]["group_counts"]["must_read"] == len(data["groups"]["must_read"])
    assert data["metrics"]["unique_must_read_source_count"] >= 1
    assert data["metrics"]["requested_tokens"] == 1200
    assert any(ref["path"] == "docs/contracts/repoctl-context-contract.md" for ref in data["metrics"]["must_read_source_refs"])
    assert "bundle" not in data
    assert payload["warnings"][0]["code"] == "context_pack_not_authoritative"

    assert main(
        [
            "task",
            "handoff",
            "bind",
            "T-20260622010101Z",
            "--context-pack",
            ".repoctl-state/context-pack/T-20260622010101Z.json",
            "--json",
        ]
    ) == 0
    binding = json.loads(capsys.readouterr().out)
    assert binding["data"]["resume_guidance"]["status"] == "current"
    assert binding["data"]["resume_guidance"]["context_pack"]["status"] == "current"


def test_context_pack_rejects_task_repository_mismatch_before_evidence_collection(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    task_id = "T-20260622010102Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="repository-mismatch",
        title="Keep Context Pack in its task repository",
        query="repository owner",
        goal="Reject a target repository other than the task repository.",
    )
    task_path = next((tmp_path / "docs/tasks").glob(f"{task_id}--*.md"))
    task_path.write_text(task_path.read_text(encoding="utf-8").replace('repo_id: "main"', 'repo_id: "web"'), encoding="utf-8")
    monkeypatch.setattr(
        "tools.repoctl.context_task_pack.load_materialized_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("repository evidence collection started")),
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "api", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_pack_repo_mismatch"
    assert payload["problems"][0]["path"] == f"docs/tasks/{task_id}--repository-mismatch.md"


def test_context_pack_keeps_chosen_and_supporting_sets_disjoint(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (repo / "evidence.csv").write_text("name,value\nbeta,2\n", encoding="utf-8")
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
            "- Candidate files reviewed:\n  - `repos/a.py`\n  - `repos/evidence.csv`",
        ),
        encoding="utf-8",
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--json"]) == 0

    groups = json.loads(capsys.readouterr().out)["data"]["groups"]
    edit = {item["source_ref"]["path"] for item in groups["edit_candidates"]}
    supporting = {item["source_ref"]["path"] for item in groups["supporting_evidence"]}
    assert edit == {"repos/a.py"}
    assert supporting == {"repos/evidence.csv"}
    assert edit.isdisjoint(supporting)


def test_context_pack_uses_only_current_discovery_episode_and_does_not_revalidate_recorded_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "stable.py").write_text("def stable_scope():\n    return True\n", encoding="utf-8")
    (repo / "old.py").write_text("def old_owner():\n    return True\n", encoding="utf-8")
    (repo / "new.py").write_text("def new_owner():\n    return True\n", encoding="utf-8")
    task_id = "T-20260622010123Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="current-discovery-episode",
        title="Use the current discovery episode",
        query="old_owner",
        goal="Keep only current discovery evidence in the Task Pack.",
        status="todo",
        reviewed="repos/old.py",
        chosen="repos/stable.py",
    )
    assert main(["task", "start", task_id, "--force-dirty", "--json"]) == 0
    capsys.readouterr()

    assert main(["context", "query", "old_owner", "--repo-id", "main", "--json"]) == 0
    old_receipt = json.loads(capsys.readouterr().out)["data"]["result_receipt"]
    old_selection = next(
        item["primary_citation"]
        for item in old_receipt["compact"]["representative_citations"]
        if item["primary_citation"]
        == {"authority": "source", "ref": "repos/old.py"}
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--note", "old episode note",
            "--result-producer", old_receipt["producer"],
            "--result-id", old_receipt["result_id"],
            "--result-authority", old_selection["authority"],
            "--result-ref", old_selection["ref"],
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["context", "query", "new_owner", "--repo-id", "main", "--json"]) == 0
    new_receipt = json.loads(capsys.readouterr().out)["data"]["result_receipt"]
    new_selection = next(
        item["primary_citation"]
        for item in new_receipt["compact"]["representative_citations"]
        if item["primary_citation"]
        == {"authority": "source", "ref": "repos/new.py"}
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--reviewed", "repos/new.py",
            "--note", "new episode note",
            "--result-producer", new_receipt["producer"],
            "--result-id", new_receipt["result_id"],
            "--result-authority", new_selection["authority"],
            "--result-ref", new_selection["ref"],
            "--full", "--json",
        ]
    ) == 0
    discovery = json.loads(capsys.readouterr().out)["data"]["discovery"]
    assert discovery["candidate_query_history"] == ["new_owner"]
    assert discovery["candidate_files_reviewed"] == ["repos/new.py"]
    assert discovery["chosen_files"] == ["repos/stable.py"]
    assert discovery["notes"] == ["new episode note"]
    assert discovery["selected_result_evidence"] == [
        {
            "schema_version": 2,
            "producer": "context",
            "result_id": new_receipt["result_id"],
            "episode_id": discovery["selected_result_evidence"][0]["episode_id"],
            "request": new_receipt["request"],
            "authority": "source",
            "ref": "repos/new.py",
        }
    ]

    target = require_repo_target(tmp_path, repo_id="main")
    result_receipt_path(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=new_receipt["result_id"],
    ).unlink()

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--full", "--json"]) == 0
    pack = json.loads(capsys.readouterr().out)["data"]
    assert pack["seed"]["query"] == "new_owner"
    assert pack["seed"]["notes"] == ["new episode note"]
    assert pack["seed"]["selected_result_evidence"] == discovery["selected_result_evidence"]
    assert "old_owner" not in json.dumps(pack["seed"], sort_keys=True)
    edit_paths = {item["source_ref"]["path"] for item in pack["groups"]["edit_candidates"]}
    support_paths = {item["source_ref"]["path"] for item in pack["groups"]["supporting_evidence"]}
    assert edit_paths == {"repos/stable.py"}
    assert support_paths == {"repos/new.py"}


def test_context_pack_never_drops_required_evidence_to_fit_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def execute():\n    return 'invoice settlement owner'\n", encoding="utf-8")
    _materialize(tmp_path)
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
        query="invoice settlement owner",
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
    assert data["render_projection"] == "required_reference_manifest"
    assert data["stop_reason"] == "budget_reached"
    assert data["budget"]["final_render_estimated_tokens"] <= data["budget"]["maximum_estimated_tokens"]
    rendered = render_task_context_pack_markdown(data)
    assert {item["path"] for item in data["seed"]["graph_seed_refs"]} == {"app.py"}
    assert data["seed"]["graph_seed_refs"][0]["provenance"] == "lexical_file"
    assert "## Graph Seed Identities" in rendered
    assert all(path in rendered for path in context_docs)
    assert "AGENTS.md" in rendered
    assert f"docs/tasks/{task_id}--required-budget.md" in rendered
    assert "repos/app.py" in rendered

    exact_budget = data["budget"]["final_render_estimated_tokens"]
    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", str(exact_budget), "--json"]) == 0
    exact = json.loads(capsys.readouterr().out)["data"]
    assert exact["render_projection"] == "required_reference_manifest"
    assert exact["stop_reason"] == "budget_reached"
    assert exact["budget"]["final_render_estimated_tokens"] <= exact_budget
    assert exact["seed"]["graph_seed_refs"] == data["seed"]["graph_seed_refs"]

    output = tmp_path / ".repoctl-state/context-pack/reference-compact.json"
    assert main(
        [
            "context",
            "pack",
            "--task",
            task_id,
            "--repo-id",
            "main",
            "--budget-tokens",
            "450",
            "--output",
            output.as_posix(),
            "--json",
        ]
    ) == 0
    compact = json.loads(capsys.readouterr().out)
    assert compact["data"]["render_projection"] == "required_reference_manifest"
    assert main(["task", "handoff", "bind", task_id, "--context-pack", output.as_posix(), "--json"]) == 0
    binding = json.loads(capsys.readouterr().out)
    assert binding["data"]["resume_guidance"]["context_pack"]["status"] == "current"
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert stale["context_pack"]["status"] == "stale"
    assert "pack_inputs_changed" in stale["context_pack"]["reason_codes"]


def test_context_pack_reports_irreducible_required_reference_overflow(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    task_id = "T-20260622010123Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="required-overflow",
        title="Report irreducible required evidence overflow",
        query="run",
        goal="Keep required source identities explicit.",
        reviewed="repos/app.py",
        chosen="repos/app.py",
    )
    output = tmp_path / ".repoctl-state/context-pack/irreducible.json"

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "1500", "--output", output.as_posix(), "--full", "--json"]) == 0
    previous_artifact = output.read_bytes()
    assert previous_artifact
    capsys.readouterr()

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "1", "--output", output.as_posix(), "--full", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert payload["ok"] is False
    assert payload["problems"][0]["code"] == "context_pack_required_evidence_exceeds_budget"
    assert data["render_projection"] == "required_reference_manifest"
    assert data["stop_reason"] == "required_evidence_exceeds_budget"
    assert data["budget"]["final_render_estimated_tokens"] > data["budget"]["maximum_estimated_tokens"]
    assert not output.exists()
    rendered = render_task_context_pack_markdown(data)
    assert "AGENTS.md" in rendered
    assert f"docs/tasks/{task_id}--required-overflow.md" in rendered
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


def test_context_pack_compact_bounds_human_text_and_content_binds_request_preview() -> None:
    long_text = "owner routing evidence " * 6000
    request = {"kind": "context_query", "query": long_text.strip(), "mode": "auto"}
    data = {
        "schema": "repoctl.context.task_pack",
        "schema_version": 4,
        "authoritative": False,
        "stage": "scoped",
        "render_projection": "full",
        "input_digest": digest_data({"input": long_text}),
        "stop_reason": "required_evidence_satisfied",
        "budget": {"maximum_estimated_tokens": 1500, "final_render_estimated_tokens": 900},
        "task": {"id": "T-20260811010101Z", "repo_id": "main"},
        "seed": {
            "source": "current_discovery_episode",
            "query": long_text,
            "notes": [long_text],
            "selected_result_evidence": [
                {
                    "schema_version": 2,
                    "producer": "context",
                    "result_id": digest_data({"result": long_text}),
                    "episode_id": digest_data({"episode": long_text}),
                    "request": request,
                    "authority": "source",
                    "ref": "repos/src/owner.py",
                }
            ],
            "graph_seed_refs": [],
            "used_sections": ["Discovery"],
        },
        "groups": {},
        "metrics": {"requested_tokens": 1500, "estimated_tokens": 900},
        "warnings": [],
        "pack_digest": digest_data({"pack": long_text}),
    }

    compact = compact_task_context_pack(data)

    assert len(compact["seed"]["query_preview"]) <= 240
    assert len(compact["seed"]["notes"][0]) <= 320
    selected = compact["seed"]["selected_result_evidence"][0]
    assert selected["request_preview"]["query"].endswith("...")
    assert selected["request_digest"] == digest_data(request)
    assert "request" not in selected
    assert len(json.dumps(compact, ensure_ascii=False)) < 12000


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
    assert "docs/PRD.md" in must_read_paths
    assert payload["data"]["stage"] == "bootstrap"
    assert "context_pack_no_structured_discovery" in warning_codes


def test_context_pack_uses_split_prd_and_procedure_but_excludes_generated_view(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def update_repository_metadata():\n    return True\n", encoding="utf-8")
    for index in range(12):
        (repo / f"repository_metadata_authority_procedure_{index}.py").write_text(
            f"def repository_metadata_authority_procedure_{index}():\n    return True\n",
            encoding="utf-8",
        )
    (tmp_path / "docs/PRD.md").unlink()
    (tmp_path / "docs/prd").mkdir()
    split_prd = tmp_path / "docs/prd/repository-understanding.md"
    split_prd.write_text(
        "# Repository Understanding\n\nRepository metadata changes must preserve project authority and current source evidence.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/prd/billing.md").write_text(
        "# Billing\n\nInvoice retry policy and subscription lifecycle.\n",
        encoding="utf-8",
    )
    procedure = tmp_path / "docs/workflows/repo-metadata.md"
    procedure.write_text(
        "# Repository Metadata Procedure\n\nUpdate repository metadata through repoctl after inspecting the owning source file.\n\n## Verification\n\nVerify the repository metadata authority procedure result.\n",
        encoding="utf-8",
    )
    generated = tmp_path / "docs/knowledge/generated/repository-metadata.md"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        "# Generated View\n\nRendered repository metadata reference.\n",
        encoding="utf-8",
    )
    product_generated = repo / "docs/knowledge/generated/repository-metadata.md"
    product_generated.parent.mkdir(parents=True)
    product_generated.write_text(
        "# Product Generated View\n\nRendered product repository metadata reference.\n",
        encoding="utf-8",
    )
    product_procedure = repo / "docs/workflows/repository-metadata.md"
    product_procedure.parent.mkdir(parents=True)
    product_procedure.write_text(
        "# Product Repository Metadata Procedure\n\nRepository metadata authority procedure for the selected product.\n",
        encoding="utf-8",
    )
    task_id = "T-20260622010123Z"
    aliased_generated = "docs/../docs/knowledge/generated/repository-metadata.md"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="document-roles",
        title="Update repository metadata safely",
        query="repository metadata authority procedure",
        goal="Use the applicable project authority and procedure.",
        context_doc=aliased_generated,
    )
    task_path = next((tmp_path / "docs/tasks").glob(f"{task_id}--*.md"))
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            f"- `{aliased_generated}`",
            "\n".join(
                (
                    f"- `{aliased_generated}`",
                    "- `docs/prd/repository-understanding.md`",
                    "- `docs/knowledge/generated/repository-metadata.md`",
                    "- `repos/docs/knowledge/generated/repository-metadata.md`",
                    "- `repos/docs/workflows/repository-metadata.md`",
                )
            ),
        ),
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "3000", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    original_input_digest = payload["data"]["input_digest"]
    groups = payload["data"]["groups"]
    must_read = {
        item["source_ref"]["path"]: item
        for item in groups["must_read"]
    }
    must_read_paths = [item["source_ref"]["path"] for item in groups["must_read"]]
    assert len(must_read_paths) == len(set(must_read_paths))
    assert must_read_paths.count("docs/prd/repository-understanding.md") == 1
    assert must_read["docs/prd/repository-understanding.md"]["document_role"] == "product_authority"
    assert must_read["docs/prd/repository-understanding.md"]["requirement"] == "required"
    assert must_read["docs/workflows/repo-metadata.md"]["document_role"] == "procedure"
    assert must_read["repos/docs/workflows/repository-metadata.md"]["document_role"] == "procedure"
    assert must_read["repos/docs/workflows/repository-metadata.md"]["requirement"] == "required"
    assert all(
        item["source_ref"]["path"] != "docs/workflows/repo-metadata.md"
        for item in groups["verification"]
    )
    assert all(
        item.get("source_ref", {}).get("path")
        not in {
            "docs/knowledge/generated/repository-metadata.md",
            "repos/docs/knowledge/generated/repository-metadata.md",
        }
        for items in groups.values()
        for item in items
        if isinstance(item, dict)
    )
    warning_codes = {warning["code"] for warning in payload["warnings"]}
    assert "context_pack_context_doc_invalid_path" in warning_codes
    assert "context_pack_generated_view_excluded" in warning_codes
    assert "context_pack_product_authority_missing" not in warning_codes

    generated.write_text(
        "# Generated View\n\nChanged rendered repository metadata reference.\n",
        encoding="utf-8",
    )
    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "3000", "--json"]) == 0

    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["data"]["input_digest"] == original_input_digest

    split_prd.write_text(
        "# Repository Understanding\n\nChanged product authority for repository metadata.\n",
        encoding="utf-8",
    )
    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--budget-tokens", "3000", "--json"]) == 0

    authority_changed = json.loads(capsys.readouterr().out)
    assert authority_changed["data"]["input_digest"] != original_input_digest


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
    assert artifact.startswith("<!-- repoctl-context-pack-envelope {")

    assert main(
        [
            "task",
            "handoff",
            "bind",
            "T-20260622010103Z",
            "--context-pack",
            ".repoctl-state/context-pack/T-20260622010103Z.md",
            "--json",
        ]
    ) == 0
    binding = json.loads(capsys.readouterr().out)
    assert binding["data"]["resume_guidance"]["status"] == "current"
    assert binding["data"]["resume_guidance"]["context_pack"]["status"] == "current"

    assert main(["context", "pack", "--task", "T-20260622010103Z", "--repo-id", "main", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    groups = payload["data"]["groups"]
    assert "likely_change" in groups
    assert "impact" in groups
    assert "verification" in groups
    assert "warnings" in groups
    assert any("login --CALLS--> validate_token" in str(item.get("excerpt", "")) for item in groups["impact"])


def test_context_pack_excludes_stale_chosen_file_graph_relations(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    flow = repo / "auth/flow.py"
    flow.parent.mkdir()
    flow.write_text(
        "def validate_token(token: str) -> bool:\n"
        "    return token == \"ok\"\n\n\n"
        "def login(token: str) -> str:\n"
        "    return \"ok\" if validate_token(token) else \"denied\"\n",
        encoding="utf-8",
    )
    task_id = "T-20260622010124Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="stale-graph",
        title="Change login behavior",
        query="What calls validate_token?",
        goal="Change login behavior without relying on stale Graph relations.",
        reviewed="repos/auth/flow.py",
        chosen="repos/auth/flow.py",
    )
    _materialize(tmp_path)
    flow.write_text(
        "def login(token: str) -> str:\n"
        "    return \"ok\" if token == \"ok\" else \"denied\"\n",
        encoding="utf-8",
    )

    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    groups = payload["data"]["groups"]
    assert all("login --CALLS--> validate_token" not in str(item.get("excerpt", "")) for item in groups["impact"])
    assert any(item.get("code") == "context_graph_stale" for item in groups["warnings"])
    assert any(warning.get("code") == "context_graph_stale" for warning in payload["warnings"])


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
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
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


def test_bound_context_pack_detects_same_head_source_drift_without_rewriting_artifact(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    app = repo / "app.py"
    app.write_text("def run():\n    return 1\n", encoding="utf-8")
    task_id = "T-20260622013131Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="bound-drift",
        title="Track bound context source drift",
        query="run owner",
        goal="Reject a bound pack after its source changes.",
    )
    output = tmp_path / ".repoctl-state/context-pack/bound-drift.md"
    assert main(
        [
            "context",
            "pack",
            "--task",
            task_id,
            "--repo-id",
            "main",
            "--budget-tokens",
            "1800",
            "--format",
            "markdown",
            "--output",
            output.as_posix(),
        ]
    ) == 0
    capsys.readouterr()
    artifact = output.read_bytes()
    assert main(["task", "handoff", "bind", task_id, "--context-pack", output.as_posix(), "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    current = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert current["status"] == "current"
    assert current["context_pack"]["status"] == "current"
    assert output.read_bytes() == artifact

    app.write_text("def run():\n    return 2\n", encoding="utf-8")
    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert stale["status"] == "inactive"
    assert stale["context_pack"]["status"] == "stale"
    assert {"pack_inputs_changed", "pack_source_changed"} & set(stale["context_pack"]["reason_codes"])
    assert output.read_bytes() == artifact


def test_context_pack_binding_rejects_missing_tampered_wrong_task_and_legacy_markdown(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    task_id = "T-20260622014141Z"
    other_id = "T-20260622014142Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="binding-rejection",
        title="Reject invalid pack bindings",
        query="run owner",
        goal="Bind only the exact current task pack.",
    )
    _write_context_pack_task(
        tmp_path,
        task_id=other_id,
        slug="other-binding",
        title="Other task pack",
        query="run owner",
        goal="Produce a different task identity.",
    )
    valid = tmp_path / ".repoctl-state/context-pack/valid.json"
    other = tmp_path / ".repoctl-state/context-pack/other.json"
    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--output", valid.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["context", "pack", "--task", other_id, "--repo-id", "main", "--output", other.as_posix(), "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "handoff", "bind", task_id, "--context-pack", ".repoctl-state/context-pack/missing.json", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "context_pack_missing"

    tampered = tmp_path / ".repoctl-state/context-pack/tampered.json"
    tampered_payload = json.loads(valid.read_text(encoding="utf-8"))
    tampered_payload["data"]["stop_reason"] = "tampered"
    tampered.write_text(json.dumps(tampered_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert main(["task", "handoff", "bind", task_id, "--context-pack", tampered.as_posix(), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "context_pack_artifact_digest_mismatch"

    assert main(["task", "handoff", "bind", task_id, "--context-pack", other.as_posix(), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "context_pack_binding_identity_mismatch"

    legacy = tmp_path / ".repoctl-state/context-pack/legacy.md"
    legacy.write_text("# Agent Context Pack\n\nHistorical text only.\n", encoding="utf-8")
    assert main(["task", "handoff", "bind", task_id, "--context-pack", legacy.as_posix(), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "context_pack_binding_metadata_missing"

    markdown = tmp_path / ".repoctl-state/context-pack/valid.md"
    assert main(
        [
            "context",
            "pack",
            "--task",
            task_id,
            "--repo-id",
            "main",
            "--format",
            "markdown",
            "--output",
            markdown.as_posix(),
        ]
    ) == 0
    capsys.readouterr()
    markdown_text = markdown.read_text(encoding="utf-8")
    body_tampered = tmp_path / ".repoctl-state/context-pack/body-tampered.md"
    body_tampered.write_text(markdown_text + "tampered\n", encoding="utf-8")
    assert main(["task", "handoff", "bind", task_id, "--context-pack", body_tampered.as_posix(), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "context_pack_binding_invalid"

    first_line, body = markdown_text.split("\n", 1)
    prefix = "<!-- repoctl-context-pack-envelope "
    envelope = json.loads(first_line[len(prefix) : -4])
    envelope["input_digest"] = "sha256:" + "3" * 64
    envelope_tampered = tmp_path / ".repoctl-state/context-pack/envelope-tampered.md"
    envelope_tampered.write_text(
        prefix + json.dumps(envelope, separators=(",", ":"), sort_keys=True) + " -->\n" + body,
        encoding="utf-8",
    )
    assert main(["task", "handoff", "bind", task_id, "--context-pack", envelope_tampered.as_posix(), "--json"]) == 2
    stale_payload = json.loads(capsys.readouterr().out)
    assert stale_payload["problems"][0]["code"] == "context_pack_stale"
    assert [action["kind"] for action in stale_payload["next_actions"]] == ["context_pack_refresh", "task_handoff_bind"]
    assert stale_payload["next_actions"][0]["command"].endswith(
        "--format markdown --output .repoctl-state/context-pack/envelope-tampered.md"
    )


def test_context_pack_binding_uses_canonical_candidate_digests_for_scoped_fallback_and_verification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "README.md").write_text("# Product\n\nScoped fallback text.\n", encoding="utf-8")
    (repo / "app.py").write_bytes(b"def run():\r\n    return 1\r\n")
    package = repo / "package.json"
    package.write_text('{"name":"demo","scripts":{"test":"vitest run"}}\n', encoding="utf-8")
    task_id = "T-20260622014646Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="canonical-input-digests",
        title="Keep source digest ownership canonical",
        query="run verification",
        goal="Bind current scoped source and manifest-derived verification evidence.",
    )
    output = tmp_path / ".repoctl-state/context-pack/canonical-inputs.json"
    assert main(["context", "pack", "--task", task_id, "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "handoff", "bind", task_id, "--context-pack", output.as_posix(), "--json"]) == 0
    current = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert current["status"] == "current"
    assert current["context_pack"]["status"] == "current"

    package.write_text('{"name":"demo","scripts":{"test":"vitest run --coverage"}}\n', encoding="utf-8")
    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert stale["context_pack"]["status"] == "stale"
    assert "pack_inputs_changed" in stale["context_pack"]["reason_codes"]


def test_required_reference_manifest_markdown_has_verifiable_envelope_and_binds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    context_docs = []
    for index in range(14):
        rel = f"docs/contracts/envelope-required-{index}.md"
        context_docs.append(rel)
        (tmp_path / rel).write_text(f"# Required {index}\n\nEvidence {index}.\n", encoding="utf-8")
    task_id = "T-20260622015151Z"
    _write_context_pack_task(
        tmp_path,
        task_id=task_id,
        slug="reference-envelope",
        title="Bind a reference manifest",
        query="run",
        goal="Keep required references verifiable.",
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
    output = tmp_path / ".repoctl-state/context-pack/reference-manifest.md"
    assert main(
        [
            "context",
            "pack",
            "--task",
            task_id,
            "--repo-id",
            "main",
            "--budget-tokens",
            "450",
            "--format",
            "markdown",
            "--output",
            output.as_posix(),
        ]
    ) == 0
    capsys.readouterr()
    text = output.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    prefix = "<!-- repoctl-context-pack-envelope "
    assert first_line.startswith(prefix) and first_line.endswith(" -->")
    envelope = json.loads(first_line[len(prefix) : -4])
    assert envelope["schema"] == "repoctl.context.task_pack.markdown_envelope"
    assert envelope["task_pack_schema_version"] == 4
    assert envelope["task_id"] == task_id
    assert envelope["repo_id"] == "main"
    assert set(envelope) == {
        "schema",
        "schema_version",
        "task_pack_schema_version",
        "task_id",
        "repo_id",
        "input_digest",
        "body_sha256",
    }
    assert len(first_line) < 600

    assert main(["task", "handoff", "bind", task_id, "--context-pack", output.as_posix(), "--json"]) == 0
    binding = json.loads(capsys.readouterr().out)
    assert binding["data"]["resume_guidance"]["status"] == "current"
    assert binding["data"]["resume_guidance"]["context_pack"]["status"] == "current"
