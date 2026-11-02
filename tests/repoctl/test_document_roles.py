from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.document_roles import DocumentRole, classify_document_role


def test_current_document_structure_has_explicit_roles() -> None:
    assert classify_document_role("docs/PRD.md") is DocumentRole.PRODUCT_AUTHORITY
    assert classify_document_role("docs/specs/graph/requirements.md") is DocumentRole.PRODUCT_AUTHORITY
    assert classify_document_role("docs/specs/graph/design.md") is DocumentRole.GOVERNANCE_AUTHORITY
    assert classify_document_role("docs/architecture/overview.md") is DocumentRole.GOVERNANCE_AUTHORITY
    assert classify_document_role("docs/adr/0001-boundary.md") is DocumentRole.GOVERNANCE_AUTHORITY
    assert classify_document_role("docs/research/indexer-benchmark.md") is DocumentRole.REFERENCE


def test_old_prd_subtree_is_not_product_authority() -> None:
    assert classify_document_role("docs/prd/product.md") is DocumentRole.REFERENCE
    assert classify_document_role("docs/prd/future-benchmark.md") is DocumentRole.REFERENCE
    assert classify_document_role("docs/prd/research-hypothesis.md") is DocumentRole.REFERENCE


def test_graph_workflow_benchmark_has_representative_work_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "tests/fixtures/graph-workflow-benchmark/cases.json").read_text(encoding="utf-8"))
    assert {case["id"] for case in data["cases"]} == {
        "basic-ml-predict",
        "kinport-audio-clock",
        "gakza-dart-precision",
        "cops-and-robbers-stale-empty",
    }
    assert all(case["required_stages"] and case["success"] for case in data["cases"])


def test_unadopted_spec_and_adr_are_reference_not_authority() -> None:
    from tools.repoctl.context_chunks import chunk_markdown_text
    from tools.repoctl.document_roles import declared_document_role

    for name in ("docs/specs/payments/requirements.md", "docs/adr/0005-unadopted.md"):
        assert declared_document_role("# Future implementation\n\nStatus: proposed\n", path=name) is DocumentRole.REFERENCE
        chunks = chunk_markdown_text(__import__('pathlib').Path('.'), name, "# Future implementation\n\nStatus: proposed\n\nSuggested only")
        assert all(item.document_role is DocumentRole.REFERENCE for item in chunks)
    assert declared_document_role("# Contract\n\nStatus: accepted\n", path="docs/specs/payments/requirements.md") is DocumentRole.PRODUCT_AUTHORITY
    assert declared_document_role("# Contract\n", path="docs/specs/payments/requirements.md") is DocumentRole.PRODUCT_AUTHORITY


def test_graph_search_never_promotes_explicitly_proposed_spec(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.repoctl.context_test_helpers import _setup_context_workspace
    from tests.repoctl.graph.test_graph_build import _materialize
    from tools.repoctl.cli import main

    _setup_context_workspace(tmp_path, monkeypatch)
    spec = tmp_path / 'docs/specs/route/requirements.md'
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text('# Idempotency\n\nStatus: proposed\n\nzzroute_nonce_17 must be unique.\n', encoding='utf-8')
    _materialize(tmp_path)
    assert main(['graph','search','zzroute_nonce_17','--repo-id','main','--full','--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    candidates = [item for item in payload['data']['bundle']['evidence']
                  if item['source_ref']['path']=='docs/specs/route/requirements.md']
    assert candidates
    assert all(item['document_role']=='reference' for item in candidates)
    spec.write_text('# Idempotency\n\nStatus: accepted\n\nzzroute_nonce_17 must be unique.\n', encoding='utf-8')
    assert main(['graph','search','zzroute_nonce_17','--repo-id','main','--full','--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    latest = [item for item in accepted['data']['bundle']['evidence']
              if item['source_ref']['path']=='docs/specs/route/requirements.md']
    assert latest
    assert all(item['document_role']=='product_authority' for item in latest)


def test_old_docs_prd_is_not_collected_for_graph_search(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.repoctl.context_test_helpers import _setup_context_workspace
    from tests.repoctl.graph.test_graph_build import _materialize
    from tools.repoctl.cli import main
    _setup_context_workspace(tmp_path, monkeypatch)
    old = tmp_path / 'docs/prd/old-priority.md'
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text('# Legacy priority\n\nzzold_prd_marker_72\n', encoding='utf-8')
    product_old = tmp_path / 'repos/docs/prd/old-priority.md'
    product_old.parent.mkdir(parents=True, exist_ok=True)
    product_old.write_text('# Legacy product priority\n\nzzold_prd_marker_72\n', encoding='utf-8')
    _materialize(tmp_path)
    assert main(['graph','search','zzold_prd_marker_72','--repo-id','main','--full','--json']) == 0
    result = json.loads(capsys.readouterr().out)
    paths = [item['source_ref']['path'] for item in result['data']['bundle']['evidence']]
    assert old.relative_to(tmp_path).as_posix() not in paths
    assert product_old.relative_to(tmp_path).as_posix() not in paths
