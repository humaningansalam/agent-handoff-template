from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tools.repoctl.context_benchmark import _retrieval_evidence
from tools.repoctl.graph_model import digest_data
from tools.repoctl.knowledge_projection import initialize_empty_knowledge_projection
from tests.repoctl.context_test_helpers import (
    _setup_context_multirepo_workspace,
    _write_context_benchmark_collection_corpus,
    _write_context_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def _setup_benchmark_workspace(tmp_path: Path, monkeypatch) -> Path:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    initialize_empty_knowledge_projection(tmp_path, repo_id="main")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    return Path("tests/fixtures/context-benchmark").resolve()


def _tree_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def _write_benchmark_artifact(path: Path, data: dict) -> None:
    copied = json.loads(json.dumps(data))
    copied["benchmark_digest"] = digest_data(
        {
            key: value
            for key, value in copied.items()
            if key not in {"benchmark_digest", "artifact"}
        }
    )
    path.write_text(json.dumps(copied, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _arm_payload_digest(data: dict) -> str:
    copied = {
        key: value
        for key, value in data.items()
        if key not in {"artifact", "benchmark_digest", "execution"}
    }
    copied["attribution"] = {
        key: (
            [
                {
                    candidate_key: candidate_value
                    for candidate_key, candidate_value in candidate.items()
                    if candidate_key != "member_id"
                }
                for candidate in value
            ]
            if key == "candidates"
            else value
        )
        for key, value in copied["attribution"].items()
        if key not in {"protocol", "source_artifacts"}
    }
    return digest_data(copied)


def _run_benchmark_arm(
    tmp_path: Path,
    fixture: Path,
    monkeypatch,
    capsys,
    *,
    label: str,
) -> tuple[dict, list[dict]]:
    comparison_root = tmp_path.parent
    executions: list[dict] = []
    data: dict = {}
    for index in range(4):
        workspace = comparison_root / f"{tmp_path.name}-{label}-{index}"
        shutil.copytree(tmp_path, workspace)
        monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda workspace=workspace: workspace)
        output = "benchmark.json"
        assert main([
            "context",
            "benchmark",
            "--fixture",
            fixture.as_posix(),
            "--repo-id",
            "main",
            "--attribution",
            "--output",
            output,
            "--json",
        ]) == 0
        data = json.loads(capsys.readouterr().out)["data"]
        path = workspace / output
        execution = data["execution"]
        executions.append({
            "agent": "codex",
            "arm_payload_digest": _arm_payload_digest(data),
            "artifact_path": path.relative_to(comparison_root).as_posix(),
            "artifact_sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "execution_id": execution["execution_id"],
            "fresh_workspace": execution["fresh_workspace"],
            "model": "gpt-5",
            "permission_digest": "sha256:" + ("3" * 64),
            "pinned_commit": "sha256:" + ("1" * 64),
            "prompt_digest": "sha256:" + ("2" * 64),
            "run_id": execution["execution_id"],
            "workspace_witness": execution["workspace_witness"],
        })
        executions[-1]["run_digest"] = digest_data(executions[-1])
    aggregate = json.loads(json.dumps(data))
    aggregate.pop("artifact", None)
    aggregate["attribution"]["protocol"] = {
        "runs": executions,
        "repetition_count": len(executions),
    }
    return aggregate, executions


def _refresh_knowledge_digests(capture: dict) -> None:
    record = capture["record"]
    event = capture["event"]
    record["record_digest"] = digest_data({key: value for key, value in record.items() if key != "record_digest"})
    event["record_digest"] = record["record_digest"]
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})


def test_context_benchmark_materializes_real_fixture_and_measures_retrieval_quality(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    materialized = json.loads(capsys.readouterr().out)
    assert materialized["data"]["totals"]["created"] >= 10
    assert materialized["data"]["totals"]["conflict"] == 0

    assert main(
        [
            "context",
            "benchmark",
            "--fixture",
            fixture.as_posix(),
            "--repo-id",
            "main",
            "--min-recall-at-5",
            "0.85",
            "--require-source-integrity",
            "--require-fixture-corpus",
            "--require-no-forbidden",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["summary"]
    assert payload["data"]["question_count"] == 35
    assert summary["source_ref_integrity"] is True
    assert summary["mean_recall_at_5"] >= 0.85
    assert summary["by_category"]["method-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["cross-file-call-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["typed-structured-dependency-closure"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-workflow"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-shell"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-sql-seed"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-sql-rpc-typescript"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["integrated-owner-test"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["area-isolation"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["multi-owner-impact"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["multi-owner-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["typed-consumer-closure"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["typed-structured-dependency-closure"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["anchor-coherence"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["anchor-coherence"]["mean_graph_edge_recall"] == 1.0
    by_id = {result["id"]: result for result in payload["data"]["results"]}
    for question_id in ("Q-004", "Q-005", "Q-020", "Q-025"):
        assert by_id[question_id]["missing_required_from_visible"] == []
    assert by_id["Q-004"]["selected_forbidden"] == []
    assert by_id["Q-036"]["missing_required_from_visible"] == []
    assert by_id["Q-036"]["selected_forbidden"] == []
    assert summary["generated_or_ignored_noise"] == 0
    assert summary["forbidden_selected"] == 0
    assert payload["problems"] == []

    assert main(
        [
            "context",
            "query",
            "packaged/operational_controls.py",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0
    exact_copy = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert any(
        item["source_ref"]["path"] == "repos/packaged/operational_controls.py"
        and set(item["evidence_kinds"]) & {"exact_path", "exact_filename"}
        for item in exact_copy["evidence"]
    )


def test_context_benchmark_keeps_multirepo_results_isolated(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)

    assert main(
        [
            "context",
            "benchmark",
            "--fixture",
            fixture.as_posix(),
            "--require-fixture-corpus",
            "--require-no-cross-repo",
            "--require-no-forbidden",
            "--min-category-visible-recall",
            "multi-repo-isolation=1.0",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    result = payload["data"]["results"][0]
    assert result["repo_id"] == "web"
    assert result["metrics"]["visible_recall"] == 1.0
    assert result["selected_forbidden"] == []
    assert result["cross_repo_refs"] == []
    assert payload["data"]["summary"]["cross_repo_ref_count"] == 0
    assert payload["problems"] == []


def test_context_benchmark_attribution_is_opt_in_stable_and_read_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    before = _tree_manifest(tmp_path)
    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    default_payload = json.loads(capsys.readouterr().out)
    assert '"attribution":' not in json.dumps(default_payload, sort_keys=True)
    assert _tree_manifest(tmp_path) == before

    command = ["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)["data"]
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)["data"]
    normal_fields = lambda data: {
        key: value
        for key, value in data.items()
        if key not in {"attribution", "benchmark_digest", "execution"}
    }
    assert normal_fields(first) == normal_fields(default_payload["data"])
    canonical = lambda data: [
        {
            key: candidate[key]
            for key in ("case_id", "candidate_id", "subject_key", "version_digest", "stages", "retrieval")
        }
        for candidate in data["attribution"]["candidates"]
    ]
    assert digest_data(canonical(first)) == digest_data(canonical(second))
    assert _tree_manifest(tmp_path) == before

    output_command = [*command[:-1], "--output", "attribution-output.json", "--json"]
    assert main(output_command) == 0
    output_payload = json.loads(capsys.readouterr().out)
    assert output_payload["ok"] is True
    assert tuple(entry for entry in _tree_manifest(tmp_path) if entry[0] != "attribution-output.json") == before

    attribution = first["attribution"]
    assert attribution["claim_scope"] == "correlation_only"
    assert attribution["non_gating"] is True
    by_case = {item["case_id"]: item for item in attribution["candidates"]}
    assert all(by_case["full-chain"]["stages"].values())
    assert by_case["compact-hidden"]["stages"]["retrieved"] is True
    assert by_case["compact-hidden"]["stages"]["compact_visible"] is False
    assert by_case["compact-hidden"]["retrieval"]["rank"] == 10
    assert by_case["compact-hidden"]["retrieval"]["score"] == 7.216669
    assert by_case["visible-unselected"]["stages"]["compact_visible"] is True
    assert by_case["visible-unselected"]["stages"]["selected"] is False
    assert by_case["selected-unverified"]["stages"]["selected"] is True
    assert by_case["selected-unverified"]["stages"]["reviewed"] is True
    assert by_case["selected-unverified"]["stages"]["verified"] is False
    assert by_case["stale-not-reused"]["stages"]["later_reused"] is False
    assert by_case["knowledge-only-reuse"]["stages"]["later_reused"] is True
    for candidate in attribution["candidates"]:
        if candidate["stages"]["retrieved"] is True:
            assert set(candidate["retrieval"]) == {
                "rank",
                "lane",
                "score",
                "score_breakdown",
                "typed_contributions",
            }


def test_context_benchmark_attribution_unions_duplicate_typed_contributions() -> None:
    first = {
        "source_ref": {"kind": "current_source", "path": "repos/example.py"},
        "score": 10.0,
        "score_breakdown": {"graph": 0.0},
    }
    second = {
        "source_ref": {"kind": "current_source", "path": "repos/example.py"},
        "score": 1.0,
        "score_breakdown": {"knowledge_path": 1.0},
        "graph_path": ["repos/example.py", "repos/caller.py"],
    }

    retrieval = _retrieval_evidence({"evidence": [first, second]})[("source", "repos/example.py")]

    assert retrieval["rank"] == 1
    assert retrieval["score"] == 10.0
    assert retrieval["score_breakdown"] == {"graph": 0.0}
    assert retrieval["typed_contributions"] == {"graph": True, "knowledge": True}


def test_context_benchmark_attribution_uses_unknown_and_exact_subject_versions(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / "attribution-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases["trace"]["completion"] = None
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 0
    candidates = json.loads(capsys.readouterr().out)["data"]["attribution"]["candidates"]
    for candidate in candidates:
        assert candidate["stages"]["selected"] == "unknown"
        assert candidate["stages"]["reviewed"] == "unknown"
        assert candidate["stages"]["chosen"] == "unknown"
        assert candidate["stages"]["verified"] == "unknown"
        assert candidate["stages"]["later_reused"] == "unknown"

    cases = json.loads(fixture.joinpath("attribution-cases.json").read_text(encoding="utf-8"))
    cases["trace"]["completion"]["roles"]["version_overrides"] = [{
        "authority": "source",
        "ref": "repos/frontend/src/api/tokens.ts",
        "version_digest": "sha256:" + ("8" * 64),
    }]
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 0
    by_case = {
        item["case_id"]: item
        for item in json.loads(capsys.readouterr().out)["data"]["attribution"]["candidates"]
    }
    assert by_case["selected-unverified"]["stages"]["selected"] is False
    assert by_case["selected-unverified"]["stages"]["reviewed"] is False


def test_context_benchmark_attribution_missing_compact_projection_is_trace_scoped(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / "missing-compact-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases["trace"]["compact_capture"] = "missing"
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 0
    attribution = json.loads(capsys.readouterr().out)["data"]["attribution"]
    assert attribution["source_artifacts"]["projection_digest"] == ""
    assert all(candidate["stages"]["compact_visible"] == "unknown" for candidate in attribution["candidates"])


@pytest.mark.parametrize(
    ("missing_channel", "true_case", "unknown_case"),
    [
        ("later_results", "knowledge-only-reuse", "full-chain"),
        ("knowledge_results", "full-chain", "knowledge-only-reuse"),
    ],
)
def test_context_benchmark_attribution_partial_reuse_capture_is_unknown(
    tmp_path: Path,
    monkeypatch,
    capsys,
    missing_channel: str,
    true_case: str,
    unknown_case: str,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / f"missing-{missing_channel}-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases["trace"].pop(missing_channel)
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 0
    by_case = {
        item["case_id"]: item
        for item in json.loads(capsys.readouterr().out)["data"]["attribution"]["candidates"]
    }
    assert by_case[true_case]["stages"]["later_reused"] is True
    assert by_case[unknown_case]["stages"]["later_reused"] == "unknown"


@pytest.mark.parametrize("tamper", ["approval_binding", "event_digest", "record_digest"])
def test_context_benchmark_attribution_rejects_tampered_knowledge_artifacts(
    tmp_path: Path,
    monkeypatch,
    capsys,
    tamper: str,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / "tampered-knowledge-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    capture = cases["trace"]["knowledge_results"][0]
    if tamper == "approval_binding":
        capture["event"]["record_digest"] = "sha256:" + ("0" * 64)
        capture["event"]["event_digest"] = digest_data(
            {key: value for key, value in capture["event"].items() if key != "event_digest"}
        )
    else:
        capture["event" if tamper == "event_digest" else "record"][tamper] = "sha256:" + ("0" * 64)
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_benchmark_attribution_invalid"


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_source_kind",
        "unknown_source_kind",
        "future_review",
        "future_approval",
        "absolute_record_id",
        "traversal_event_id",
        "invalid_record_kind",
        "pending_review_status",
    ],
)
def test_context_benchmark_attribution_rejects_invalid_knowledge_provenance_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
    tamper: str,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / "invalid-knowledge-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    capture = cases["trace"]["knowledge_results"][0]
    if tamper == "missing_source_kind":
        capture["record"]["source_refs"][0].pop("kind")
    elif tamper == "unknown_source_kind":
        capture["record"]["source_refs"][0]["kind"] = "unknown"
    elif tamper == "future_review":
        capture["record"]["review"]["reviewed_at"] = "2026-08-26T00:00:00Z"
    elif tamper == "future_approval":
        capture["event"]["approved_at"] = "2026-08-26T00:00:00Z"
    elif tamper == "absolute_record_id":
        capture["record"]["id"] = "/tmp/escaped-record"
    elif tamper == "traversal_event_id":
        capture["event"]["id"] = "../escaped-event"
    elif tamper == "invalid_record_kind":
        capture["record"]["kind"] = "unknown"
        _refresh_knowledge_digests(capture)
    else:
        capture["record"]["review"]["status"] = "pending"
        _refresh_knowledge_digests(capture)
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = _tree_manifest(tmp_path)

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_benchmark_attribution_invalid"
    assert _tree_manifest(tmp_path) == before


@pytest.mark.parametrize(
    "timestamp",
    ["completion", "direct_later", "knowledge_result", "knowledge_review", "knowledge_approval"],
)
def test_context_benchmark_attribution_rejects_timezone_naive_timestamps(
    tmp_path: Path,
    monkeypatch,
    capsys,
    timestamp: str,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    copied_fixture = tmp_path / "naive-timestamp-fixture"
    shutil.copytree(fixture, copied_fixture)
    cases_path = copied_fixture / "attribution-cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    trace = cases["trace"]
    if timestamp == "completion":
        trace["completion"]["completed_at"] = "2026-08-24T01:00:00"
    elif timestamp == "direct_later":
        trace["later_results"][0]["captured_at"] = "2026-08-25T00:00:00"
    elif timestamp == "knowledge_result":
        trace["knowledge_results"][0]["captured_at"] = "2026-08-25T00:03:00"
    elif timestamp == "knowledge_review":
        trace["knowledge_results"][0]["record"]["review"]["reviewed_at"] = "2026-08-25T00:02:00"
    else:
        trace["knowledge_results"][0]["event"]["approved_at"] = "2026-08-25T00:02:00"
    cases_path.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = _tree_manifest(tmp_path)

    assert main(["context", "benchmark", "--fixture", copied_fixture.as_posix(), "--repo-id", "main", "--attribution", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_benchmark_attribution_invalid"
    assert _tree_manifest(tmp_path) == before


def test_context_benchmark_compare_causal_eligibility_is_strict_and_non_gating(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    baseline_data, baseline_runs = _run_benchmark_arm(tmp_path, fixture, monkeypatch, capsys, label="baseline")
    candidate_source, candidate_runs = _run_benchmark_arm(tmp_path, fixture, monkeypatch, capsys, label="candidate")
    comparison_root = tmp_path.parent
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: comparison_root)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write_benchmark_artifact(baseline, baseline_data)

    copied_runs: list[dict] = []
    redigested_copied_runs: list[dict] = []
    copied_source = comparison_root / candidate_runs[0]["artifact_path"]
    copied_payload = json.loads(copied_source.read_text(encoding="utf-8"))
    for index in range(8):
        copied_workspace = comparison_root / f"{tmp_path.name}-copied-{index}"
        copied_workspace.mkdir()
        internal_path = f"copy-{index}.json"
        copied_path = copied_workspace / internal_path
        payload = json.loads(json.dumps(copied_payload))
        payload["data"]["artifact"]["path"] = internal_path
        copied_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run = json.loads(json.dumps(candidate_runs[0]))
        run["artifact_path"] = copied_path.relative_to(comparison_root).as_posix()
        run["artifact_sha256"] = "sha256:" + hashlib.sha256(copied_path.read_bytes()).hexdigest()
        run["run_digest"] = digest_data({key: value for key, value in run.items() if key != "run_digest"})
        copied_runs.append(run)

        redigested_workspace = comparison_root / f"{tmp_path.name}-redigested-{index}"
        redigested_workspace.mkdir()
        redigested_path = redigested_workspace / internal_path
        redigested_payload = json.loads(json.dumps(copied_payload))
        execution_id = f"copied-execution-{index}"
        workspace_witness = digest_data({"workspace_root": redigested_workspace.resolve().as_posix()})
        redigested_payload["data"]["execution"] = {
            "execution_id": execution_id,
            "fresh_workspace": True,
            "workspace_witness": workspace_witness,
        }
        redigested_payload["data"]["benchmark_digest"] = digest_data({
            key: value
            for key, value in redigested_payload["data"].items()
            if key not in {"artifact", "benchmark_digest"}
        })
        redigested_payload["data"]["artifact"] = {
            "path": internal_path,
            "benchmark_digest": redigested_payload["data"]["benchmark_digest"],
        }
        redigested_path.write_text(json.dumps(redigested_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        redigested_run = json.loads(json.dumps(candidate_runs[0]))
        redigested_run.update({
            "artifact_path": redigested_path.relative_to(comparison_root).as_posix(),
            "artifact_sha256": "sha256:" + hashlib.sha256(redigested_path.read_bytes()).hexdigest(),
            "execution_id": execution_id,
            "run_id": execution_id,
            "workspace_witness": workspace_witness,
        })
        redigested_run["run_digest"] = digest_data({
            key: value for key, value in redigested_run.items() if key != "run_digest"
        })
        redigested_copied_runs.append(redigested_run)

    scenarios = [
        ({}, "insufficient_evidence"),
        ({"drop_run": True}, "insufficient_evidence"),
        ({"pinned_commit": "sha256:" + ("9" * 64)}, "insufficient_evidence"),
        ({"prompt_digest": "sha256:" + ("8" * 64)}, "insufficient_evidence"),
        ({"permission_digest": "sha256:" + ("7" * 64)}, "insufficient_evidence"),
        ({"fresh_workspace": False}, "insufficient_evidence"),
        ({"tamper_run_digest": True}, "insufficient_evidence"),
        ({"artifact_sha256": "sha256:" + ("0" * 64)}, "insufficient_evidence"),
        ({"artifact_path": "benchmark-runs/missing.json"}, "insufficient_evidence"),
        ({"arm_payload_digest": "sha256:" + ("0" * 64)}, "insufficient_evidence"),
        ({"shared_artifact": True}, "insufficient_evidence"),
        ({"copied_eight": True}, "insufficient_evidence"),
        ({"redigested_copied_eight": True}, "insufficient_evidence"),
    ]
    for change, expected in scenarios:
        baseline_case = json.loads(json.dumps(baseline_data))
        candidate_data = json.loads(json.dumps(candidate_source))
        runs = candidate_data["attribution"]["protocol"]["runs"]
        if change.get("copied_eight"):
            baseline_case["attribution"]["protocol"] = {"runs": copied_runs[:4], "repetition_count": 4}
            candidate_data["attribution"]["protocol"] = {"runs": copied_runs[4:], "repetition_count": 4}
            runs = candidate_data["attribution"]["protocol"]["runs"]
        elif change.get("redigested_copied_eight"):
            baseline_case["attribution"]["protocol"] = {"runs": redigested_copied_runs[:4], "repetition_count": 4}
            candidate_data["attribution"]["protocol"] = {"runs": redigested_copied_runs[4:], "repetition_count": 4}
            runs = candidate_data["attribution"]["protocol"]["runs"]
        elif change.get("drop_run"):
            runs.pop()
        elif change.get("shared_artifact"):
            runs[0] = json.loads(json.dumps(baseline_runs[0]))
        else:
            run_changes = {key: value for key, value in change.items() if key != "tamper_run_digest"}
            for run in runs:
                run.update(run_changes)
                run["run_digest"] = digest_data({key: value for key, value in run.items() if key != "run_digest"})
            if change.get("tamper_run_digest"):
                runs[0]["run_digest"] = "sha256:" + ("0" * 64)
        candidate_data["attribution"]["protocol"]["repetition_count"] = len(runs)
        _write_benchmark_artifact(baseline, baseline_case)
        _write_benchmark_artifact(candidate, candidate_data)

        assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["causal_eligibility"]["status"] == expected, payload["data"]["causal_eligibility"]
        assert payload["data"]["causal_eligibility"]["claim_scope"] == "correlation_only"
        assert "independent_execution_receipt_missing" in payload["data"]["causal_eligibility"]["reasons"]
        assert payload["problems"] == []

    assert len({run["artifact_path"] for run in [*baseline_runs, *candidate_runs]}) == 8
