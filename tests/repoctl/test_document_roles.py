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


def test_legacy_prd_research_is_not_promoted_by_location() -> None:
    assert classify_document_role("docs/prd/product.md") is DocumentRole.PRODUCT_AUTHORITY
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
