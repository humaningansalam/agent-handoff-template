from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .context import build_context_bundle
from .context_model import ContextBundle
from .graph import build_graph
from .graph_model import GraphSnapshot
from .graph_model import digest_data
from .repositories import require_repo_target
from .tasks import Problem


def run_context_benchmark(
    root: Path,
    *,
    fixture: Path,
    repo_id: str = "",
    budget_tokens: int = 3000,
    min_recall_at_5: float | None = None,
    min_precision_at_5: float | None = None,
    min_knowledge_recall_at_5: float | None = None,
    min_category_recall_at_5: dict[str, float] | None = None,
    min_category_graph_edge_recall: dict[str, float] | None = None,
    min_category_packed_recall: dict[str, float] | None = None,
    require_source_integrity: bool = False,
    require_knowledge_source_current: bool = False,
    require_no_forbidden: bool = False,
    require_no_cross_repo: bool = False,
    require_fixture_corpus: bool = False,
) -> tuple[dict[str, Any], list[Problem]]:
    questions_path = fixture / "questions.jsonl"
    expected_path = fixture / "expected-sources.json"
    problems: list[Problem] = []
    if not questions_path.is_file():
        return {}, [Problem("error", "context_benchmark_questions_missing", "context benchmark questions.jsonl is missing", questions_path.as_posix())]
    if not expected_path.is_file():
        return {}, [Problem("error", "context_benchmark_expected_missing", "context benchmark expected-sources.json is missing", expected_path.as_posix())]

    questions = _read_questions(questions_path)
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    corpus_status, corpus_problems = _fixture_corpus_status(root, fixture, repo_id=repo_id)
    if require_fixture_corpus:
        problems.extend(corpus_problems)
    results: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question.get("id") or "")
        target_repo_id = repo_id or str(question.get("repo_id") or "")
        target = require_repo_target(root, repo_id=target_repo_id or None)
        bundle, bundle_problems, _meta = build_context_bundle(root, target=target, query=str(question.get("question") or ""), budget_tokens=budget_tokens, explain=True)
        snapshot, graph_problems, _graph_meta = build_graph(root, target=target)
        problems.extend(bundle_problems)
        problems.extend(graph_problems)
        spec = expected.get(question_id, {}) if isinstance(expected, dict) else {}
        results.append(_score_question(question, spec, bundle, [*bundle_problems, *graph_problems], snapshot))

    summary = _summarize(results)
    problems.extend(
        _gate_problems(
            summary,
            min_recall_at_5=min_recall_at_5,
            min_precision_at_5=min_precision_at_5,
            min_knowledge_recall_at_5=min_knowledge_recall_at_5,
            min_category_recall_at_5=min_category_recall_at_5 or {},
            min_category_graph_edge_recall=min_category_graph_edge_recall or {},
            min_category_packed_recall=min_category_packed_recall or {},
            require_source_integrity=require_source_integrity,
            require_knowledge_source_current=require_knowledge_source_current,
            require_no_forbidden=require_no_forbidden,
            require_no_cross_repo=require_no_cross_repo,
            require_fixture_corpus=require_fixture_corpus,
        )
    )
    data = {
        "fixture": fixture.as_posix(),
        "question_count": len(results),
        "results": results,
        "summary": summary,
        "gates": {
            "min_recall_at_5": min_recall_at_5,
            "min_precision_at_5": min_precision_at_5,
            "min_knowledge_recall_at_5": min_knowledge_recall_at_5,
            "min_category_recall_at_5": dict(sorted((min_category_recall_at_5 or {}).items())),
            "min_category_graph_edge_recall": dict(sorted((min_category_graph_edge_recall or {}).items())),
            "min_category_packed_recall": dict(sorted((min_category_packed_recall or {}).items())),
            "require_source_integrity": require_source_integrity,
            "require_knowledge_source_current": require_knowledge_source_current,
            "require_no_forbidden": require_no_forbidden,
            "require_no_cross_repo": require_no_cross_repo,
            "require_fixture_corpus": require_fixture_corpus,
        },
        "fixture_corpus": corpus_status,
    }
    data["benchmark_digest"] = digest_data(data)
    return data, problems


def compare_context_benchmarks(
    *,
    root: Path | None = None,
    baseline_path: Path,
    candidate_path: Path,
    max_recall_at_5_drop: float | None = None,
    max_precision_at_5_drop: float | None = None,
    max_knowledge_recall_at_5_drop: float | None = None,
    max_question_recall_at_5_drop: float | None = None,
    require_current_sources: bool = False,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_benchmark_artifact(baseline_path, problems, label="baseline")
    candidate = _read_benchmark_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
    source_drift: list[Problem] = []
    if require_current_sources:
        if root is None:
            source_drift.append(Problem("error", "context_benchmark_current_source_root_missing", "current source gate requires a workspace root"))
        else:
            source_drift.extend(_source_drift_problems(root, baseline, label="baseline"))
            source_drift.extend(_source_drift_problems(root, candidate, label="candidate"))
    problems.extend(source_drift)
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metric_deltas = {
        "mean_recall_at_5": _metric_delta(baseline_summary, candidate_summary, "mean_recall_at_5"),
        "mean_precision_at_5": _metric_delta(baseline_summary, candidate_summary, "mean_precision_at_5"),
        "mean_knowledge_recall_at_5": _metric_delta(baseline_summary, candidate_summary, "mean_knowledge_recall_at_5"),
    }
    question_deltas = _question_deltas(baseline, candidate)
    regressions = _compare_regressions(
        baseline_summary,
        candidate_summary,
        metric_deltas,
        question_deltas,
        max_recall_at_5_drop=max_recall_at_5_drop,
        max_precision_at_5_drop=max_precision_at_5_drop,
        max_knowledge_recall_at_5_drop=max_knowledge_recall_at_5_drop,
        max_question_recall_at_5_drop=max_question_recall_at_5_drop,
    )
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.benchmark.compare",
        "schema_version": 1,
        "baseline": _artifact_identity(baseline_path, baseline),
        "candidate": _artifact_identity(candidate_path, candidate),
        "metric_deltas": metric_deltas,
        "question_deltas": question_deltas,
        "summary_regressions": [problem.to_dict() for problem in regressions],
        "source_drift": [problem.to_dict() for problem in source_drift],
        "gates": {
            "max_recall_at_5_drop": max_recall_at_5_drop,
            "max_precision_at_5_drop": max_precision_at_5_drop,
            "max_knowledge_recall_at_5_drop": max_knowledge_recall_at_5_drop,
            "max_question_recall_at_5_drop": max_question_recall_at_5_drop,
            "require_current_sources": require_current_sources,
        },
    }, problems


def _read_questions(path: Path) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError(f"{path}:{line_number}: question must be an object")
        questions.append(data)
    return questions


def _fixture_corpus_status(root: Path, fixture: Path, *, repo_id: str = "") -> tuple[dict[str, Any], list[Problem]]:
    corpus_path = fixture / "corpus.json"
    if not corpus_path.is_file():
        return {"present": False, "repositories": {}, "file_count": 0, "missing_count": 0, "digest_drift_count": 0}, []
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"present": True, "invalid": True, "file_count": 0, "missing_count": 0, "digest_drift_count": 0}, [
            Problem("error", "context_benchmark_corpus_invalid_json", f"context benchmark corpus.json is invalid: {exc}", corpus_path.as_posix())
        ]
    repositories = corpus.get("repositories") if isinstance(corpus, dict) else {}
    if not isinstance(repositories, dict):
        return {"present": True, "invalid": True, "file_count": 0, "missing_count": 0, "digest_drift_count": 0}, [
            Problem("error", "context_benchmark_corpus_invalid", "context benchmark corpus repositories must be an object", corpus_path.as_posix())
        ]

    wanted_repo_ids = [repo_id] if repo_id else sorted(str(key) for key in repositories)
    status_repos: dict[str, Any] = {}
    problems: list[Problem] = []
    file_count = 0
    missing_count = 0
    digest_drift_count = 0
    for wanted_repo_id in wanted_repo_ids:
        repo_corpus = repositories.get(wanted_repo_id)
        if not isinstance(repo_corpus, dict):
            continue
        target = require_repo_target(root, repo_id=wanted_repo_id or None)
        repo_status = {"file_count": 0, "missing": [], "digest_drift": []}
        files = repo_corpus.get("files") if isinstance(repo_corpus.get("files"), list) else []
        for item in files:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            expected_content = str(item.get("content") or "")
            if not rel:
                continue
            file_count += 1
            repo_status["file_count"] += 1
            path = target.root_path / rel
            if not path.is_file():
                missing_count += 1
                repo_status["missing"].append(rel)
                problems.append(Problem("error", "context_benchmark_corpus_file_missing", "context benchmark corpus file is missing from workspace", rel))
                continue
            actual = path.read_text(encoding="utf-8")
            expected_digest = _text_digest(expected_content)
            actual_digest = _text_digest(actual)
            if actual_digest != expected_digest:
                digest_drift_count += 1
                repo_status["digest_drift"].append(rel)
                problems.append(Problem("error", "context_benchmark_corpus_file_digest_drift", "context benchmark corpus file content differs from fixture", rel))
        status_repos[wanted_repo_id] = repo_status
    return {
        "present": True,
        "repositories": status_repos,
        "file_count": file_count,
        "missing_count": missing_count,
        "digest_drift_count": digest_drift_count,
    }, problems


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_benchmark_artifact(path: Path, problems: list[Problem], *, label: str) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "context_benchmark_artifact_missing", f"{label} context benchmark artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "context_benchmark_artifact_invalid_json", f"{label} context benchmark artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "context_benchmark_artifact_invalid", f"{label} context benchmark artifact must be an object", path.as_posix()))
        return {}
    if str(payload.get("command") or "") == "context benchmark" and payload.get("ok") is False:
        problems.append(Problem("error", "context_benchmark_artifact_failed", f"{label} context benchmark artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context benchmark" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "context_benchmark_artifact_missing_data", f"{label} context benchmark artifact is missing data", path.as_posix()))
        return {}
    summary = data.get("summary")
    results = data.get("results")
    if not isinstance(summary, dict) or not isinstance(results, list):
        problems.append(Problem("error", "context_benchmark_artifact_invalid_data", f"{label} context benchmark artifact is missing benchmark summary/results", path.as_posix()))
        return {}
    expected_digest = str(data.get("benchmark_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"benchmark_digest", "artifact"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        problems.append(Problem("error", "context_benchmark_artifact_digest_mismatch", f"{label} context benchmark artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _artifact_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "benchmark_digest": str(data.get("benchmark_digest") or ""),
        "question_count": int(data.get("question_count") or 0),
    }


def _source_drift_problems(root: Path, data: dict[str, Any], *, label: str) -> list[Problem]:
    problems: list[Problem] = []
    seen: set[tuple[str, str]] = set()
    for ref in _artifact_refs(data):
        rel = str(ref.get("path") or "")
        expected = str(ref.get("content_sha256") or "")
        if not rel or rel.startswith("<") or not expected.startswith("sha256:"):
            continue
        key = (rel, expected)
        if key in seen:
            continue
        seen.add(key)
        path = root / rel
        if not path.is_file():
            problems.append(Problem("error", "context_benchmark_artifact_source_missing", f"{label} benchmark source ref no longer exists", rel))
            continue
        actual = "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if actual != expected:
            problems.append(Problem("error", "context_benchmark_artifact_source_digest_drift", f"{label} benchmark source ref digest no longer matches current source", rel))
    return problems


def _artifact_refs(data: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    results = data.get("results") if isinstance(data.get("results"), list) else []
    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ("top_refs", "top_knowledge_refs", "required_found_at_5", "required_found_at_10"):
            values = result.get(key)
            if isinstance(values, list):
                refs.extend(ref for ref in values if isinstance(ref, dict))
    return refs


def _metric_delta(baseline_summary: dict[str, Any], candidate_summary: dict[str, Any], key: str) -> dict[str, float]:
    baseline = float(baseline_summary.get(key) or 0.0)
    candidate = float(candidate_summary.get(key) or 0.0)
    return {
        "baseline": round(baseline, 6),
        "candidate": round(candidate, 6),
        "delta": round(candidate - baseline, 6),
    }


def _question_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_by_id = _results_by_id(baseline.get("results"))
    candidate_by_id = _results_by_id(candidate.get("results"))
    deltas: list[dict[str, Any]] = []
    for question_id in sorted(set(baseline_by_id) | set(candidate_by_id)):
        baseline_result = baseline_by_id.get(question_id, {})
        candidate_result = candidate_by_id.get(question_id, {})
        baseline_metrics = baseline_result.get("metrics") if isinstance(baseline_result.get("metrics"), dict) else {}
        candidate_metrics = candidate_result.get("metrics") if isinstance(candidate_result.get("metrics"), dict) else {}
        deltas.append(
            {
                "id": question_id,
                "category": str(candidate_result.get("category") or baseline_result.get("category") or ""),
                "present_in_baseline": bool(baseline_result),
                "present_in_candidate": bool(candidate_result),
                "metrics": {
                    "recall_at_5": _metric_delta(baseline_metrics, candidate_metrics, "recall_at_5"),
                    "precision_at_5": _metric_delta(baseline_metrics, candidate_metrics, "precision_at_5"),
                    "knowledge_recall_at_5": _metric_delta(baseline_metrics, candidate_metrics, "knowledge_recall_at_5"),
                },
            }
        )
    return deltas


def _results_by_id(value: Any) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not isinstance(value, list):
        return results
    for item in value:
        if isinstance(item, dict):
            question_id = str(item.get("id") or "")
            if question_id:
                results[question_id] = item
    return results


def _compare_regressions(
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    metric_deltas: dict[str, dict[str, float]],
    question_deltas: list[dict[str, Any]],
    *,
    max_recall_at_5_drop: float | None,
    max_precision_at_5_drop: float | None,
    max_knowledge_recall_at_5_drop: float | None,
    max_question_recall_at_5_drop: float | None,
) -> list[Problem]:
    problems: list[Problem] = []
    _append_drop_regression(problems, metric_deltas, "mean_recall_at_5", max_recall_at_5_drop, "context_benchmark_recall_regressed")
    _append_drop_regression(problems, metric_deltas, "mean_precision_at_5", max_precision_at_5_drop, "context_benchmark_precision_regressed")
    _append_drop_regression(problems, metric_deltas, "mean_knowledge_recall_at_5", max_knowledge_recall_at_5_drop, "context_benchmark_knowledge_recall_regressed")
    if max_question_recall_at_5_drop is not None:
        for item in question_deltas:
            delta = float(item.get("metrics", {}).get("recall_at_5", {}).get("delta") or 0.0)
            if delta < -abs(max_question_recall_at_5_drop):
                problems.append(Problem("error", "context_benchmark_question_recall_regressed", "context benchmark question Recall@5 dropped more than allowed", str(item.get("id") or "")))
    for item in question_deltas:
        if bool(item.get("present_in_baseline")) and not bool(item.get("present_in_candidate")):
            problems.append(Problem("error", "context_benchmark_question_missing", "candidate context benchmark artifact is missing a baseline question", str(item.get("id") or "")))
    for key, code in {
        "source_ref_integrity": "context_benchmark_source_integrity_regressed",
        "knowledge_source_ref_integrity": "context_benchmark_knowledge_integrity_regressed",
        "knowledge_source_status_current": "context_benchmark_knowledge_source_status_regressed",
    }.items():
        if bool(baseline_summary.get(key)) and not bool(candidate_summary.get(key)):
            problems.append(Problem("error", code, f"context benchmark {key} regressed"))
    return problems


def _append_drop_regression(problems: list[Problem], metric_deltas: dict[str, dict[str, float]], key: str, max_drop: float | None, code: str) -> None:
    if max_drop is None:
        return
    delta = float(metric_deltas.get(key, {}).get("delta") or 0.0)
    if delta < -abs(max_drop):
        problems.append(Problem("error", code, f"context benchmark {key} dropped more than allowed"))


def _score_question(question: dict[str, Any], spec: dict[str, Any], bundle: ContextBundle | None, problems: list[Problem], snapshot: GraphSnapshot | None = None) -> dict[str, Any]:
    required = _refs(spec.get("required_source_refs"))
    required_knowledge = _refs(spec.get("required_knowledge_source_refs"))
    required_edges = _refs(spec.get("required_graph_edges"))
    optional = _refs(spec.get("acceptable_optional_refs"))
    forbidden = _refs(spec.get("forbidden_refs"))
    candidate_refs = _bundle_refs(bundle, field="candidates")
    packed_refs = _bundle_refs(bundle, field="packed_context")
    knowledge_refs = _knowledge_refs(bundle)
    knowledge_score_results = _knowledge_score_results(bundle)
    knowledge_source_statuses = _knowledge_source_statuses(bundle)
    problem_codes = [problem.code for problem in problems]
    stale_knowledge_excluded = problem_codes.count("knowledge_stale_record_excluded")

    top5 = candidate_refs[:5]
    top10 = candidate_refs[:10]
    knowledge_top5 = knowledge_refs[:5]
    packed_required = [ref for ref in required if _contains_ref(packed_refs, ref)]
    required_top5 = [ref for ref in required if _contains_ref(top5, ref)]
    required_top10 = [ref for ref in required if _contains_ref(top10, ref)]
    required_knowledge_top5 = [ref for ref in required_knowledge if _contains_ref(knowledge_top5, ref)]
    graph_edges = _graph_edges(snapshot)
    required_edges_found = [edge for edge in required_edges if _contains_edge(graph_edges, edge)]
    selected_forbidden = [ref for ref in forbidden if _contains_ref(candidate_refs, ref) or _contains_ref(packed_refs, ref)]
    cross_repo_refs = _cross_repo_refs([*candidate_refs, *packed_refs, *knowledge_refs], expected_repo_id=str(question.get("repo_id") or ""))
    relevant_top5 = sum(1 for ref in top5 if _matches_any_expected(ref, [*required, *optional]))
    integrity_failures = [ref for ref in candidate_refs if not str(ref.get("content_sha256") or "").startswith("sha256:")]
    knowledge_integrity_failures = [ref for ref in knowledge_refs if not str(ref.get("content_sha256") or "").startswith("sha256:")]

    return {
        "id": str(question.get("id") or ""),
        "category": str(question.get("category") or ""),
        "repo_id": str(question.get("repo_id") or ""),
        "query": str(question.get("question") or ""),
        "metrics": {
            "recall_at_5": _ratio(len(required_top5), len(required)),
            "recall_at_10": _ratio(len(required_top10), len(required)),
            "precision_at_5": _ratio(relevant_top5, len(top5)),
            "packed_recall": _ratio(len(packed_required), len(required)),
            "source_ref_integrity": len(integrity_failures) == 0,
            "knowledge_source_ref_integrity": len(knowledge_integrity_failures) == 0,
            "forbidden_selected": len(selected_forbidden),
            "cross_repo_ref_count": len(cross_repo_refs),
            "packed_required_found": len(packed_required),
            "knowledge_recall_at_5": _ratio(len(required_knowledge_top5), len(required_knowledge)) if required_knowledge else 0.0,
            "graph_edge_recall": _ratio(len(required_edges_found), len(required_edges)) if required_edges else 1.0,
            "required_graph_edge_count": len(required_edges),
            "required_knowledge_count": len(required_knowledge),
            "knowledge_result_count": len(knowledge_refs),
            "knowledge_score_breakdown_present": all(result["has_field_breakdown"] for result in knowledge_score_results),
            "knowledge_source_status_current": stale_knowledge_excluded == 0 and all(status.get("digest_matches") is True for status in knowledge_source_statuses),
            "knowledge_stale_record_excluded": stale_knowledge_excluded,
        },
        "required_found_at_5": required_top5,
        "required_found_at_10": required_top10,
        "missing_required_at_10": [ref for ref in required if not _contains_ref(top10, ref)],
        "packed_required_found_refs": packed_required,
        "missing_required_from_packed": [ref for ref in required if not _contains_ref(packed_refs, ref)],
        "required_knowledge_found_at_5": required_knowledge_top5,
        "missing_required_knowledge_at_5": [ref for ref in required_knowledge if not _contains_ref(knowledge_top5, ref)],
        "required_graph_edges_found": required_edges_found,
        "missing_required_graph_edges": [edge for edge in required_edges if not _contains_edge(graph_edges, edge)],
        "selected_forbidden": selected_forbidden,
        "cross_repo_refs": cross_repo_refs,
        "top_refs": top5,
        "top_knowledge_refs": knowledge_top5,
        "knowledge_score_results": knowledge_score_results,
        "knowledge_source_statuses": knowledge_source_statuses,
        "problem_codes": problem_codes,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [result["metrics"] for result in results]
    return {
        "mean_recall_at_5": _mean(metric["recall_at_5"] for metric in metrics),
        "mean_recall_at_10": _mean(metric["recall_at_10"] for metric in metrics),
        "mean_precision_at_5": _mean(metric["precision_at_5"] for metric in metrics),
        "mean_packed_recall": _mean(metric["packed_recall"] for metric in metrics),
        "source_ref_integrity": all(metric["source_ref_integrity"] for metric in metrics),
        "knowledge_source_ref_integrity": all(metric["knowledge_source_ref_integrity"] for metric in metrics),
        "mean_knowledge_recall_at_5": _mean(metric["knowledge_recall_at_5"] for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_expected_questions": sum(1 for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_result_questions": sum(1 for metric in metrics if metric["knowledge_result_count"]),
        "knowledge_score_breakdown_integrity": all(metric["knowledge_score_breakdown_present"] for metric in metrics),
        "knowledge_source_status_current": all(metric["knowledge_source_status_current"] for metric in metrics),
        "knowledge_stale_record_excluded": sum(int(metric["knowledge_stale_record_excluded"]) for metric in metrics),
        "mean_graph_edge_recall": _mean(metric["graph_edge_recall"] for metric in metrics if metric["required_graph_edge_count"]),
        "graph_edge_expected_questions": sum(1 for metric in metrics if metric["required_graph_edge_count"]),
        "forbidden_selected": sum(int(metric["forbidden_selected"]) for metric in metrics),
        "cross_repo_ref_count": sum(int(metric["cross_repo_ref_count"]) for metric in metrics),
        "by_category": _summarize_by_category(results),
    }


def _summarize_by_category(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        category = str(result.get("category") or "uncategorized")
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        grouped.setdefault(category, []).append(metrics)
    summary: dict[str, dict[str, Any]] = {}
    for category, items in sorted(grouped.items()):
        summary[category] = {
            "question_count": len(items),
            "mean_recall_at_5": _mean(metric.get("recall_at_5", 0.0) for metric in items),
            "mean_recall_at_10": _mean(metric.get("recall_at_10", 0.0) for metric in items),
            "mean_precision_at_5": _mean(metric.get("precision_at_5", 0.0) for metric in items),
            "mean_packed_recall": _mean(metric.get("packed_recall", 0.0) for metric in items),
            "mean_graph_edge_recall": _mean(metric.get("graph_edge_recall", 1.0) for metric in items if metric.get("required_graph_edge_count", 0)),
            "graph_edge_expected_questions": sum(1 for metric in items if metric.get("required_graph_edge_count", 0)),
            "forbidden_selected": sum(int(metric.get("forbidden_selected") or 0) for metric in items),
            "cross_repo_ref_count": sum(int(metric.get("cross_repo_ref_count") or 0) for metric in items),
        }
    return summary


def _bundle_refs(bundle: ContextBundle | None, *, field: str) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    values = bundle.candidates if field == "candidates" else bundle.packed_context
    return [candidate.source_ref.to_dict() for candidate in values]


def _knowledge_refs(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    refs: list[dict[str, Any]] = []
    for item in bundle.knowledge_results:
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        source_refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
        for ref in source_refs:
            if isinstance(ref, dict):
                refs.append(ref)
    return refs


def _knowledge_score_results(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    results: list[dict[str, Any]] = []
    required_keys = {"exact_claim", "exact_summary", "exact_source", "fts", "authority"}
    for item in bundle.knowledge_results:
        breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        results.append(
            {
                "record_id": str(record.get("id") or ""),
                "has_field_breakdown": required_keys.issubset(set(str(key) for key in breakdown)),
                "score_breakdown_keys": sorted(str(key) for key in breakdown),
            }
        )
    return results


def _knowledge_source_statuses(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    statuses: list[dict[str, Any]] = []
    for item in bundle.knowledge_results:
        explain = item.get("explain") if isinstance(item.get("explain"), dict) else {}
        source_statuses = explain.get("source_ref_statuses") if isinstance(explain.get("source_ref_statuses"), list) else []
        for status in source_statuses:
            if isinstance(status, dict):
                statuses.append(status)
    return statuses


def _refs(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _contains_ref(haystack: list[dict[str, Any]], needle: dict[str, Any]) -> bool:
    for item in haystack:
        if str(item.get("path") or "") != str(needle.get("path") or ""):
            continue
        section = str(needle.get("section") or "")
        if section and str(item.get("section") or "") != section:
            continue
        return True
    return False


def _matches_any_expected(ref: dict[str, Any], expected: list[dict[str, Any]]) -> bool:
    return any(_contains_ref([ref], item) for item in expected)


def _graph_edges(snapshot: GraphSnapshot | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return [edge.to_dict() for edge in snapshot.edges]


def _contains_edge(haystack: list[dict[str, Any]], needle: dict[str, Any]) -> bool:
    for item in haystack:
        if str(item.get("kind") or "") != str(needle.get("kind") or ""):
            continue
        if str(item.get("from") or "") != str(needle.get("from") or ""):
            continue
        if str(item.get("to") or "") != str(needle.get("to") or ""):
            continue
        assertion = str(needle.get("assertion") or "")
        if assertion and str(item.get("assertion") or "") != assertion:
            continue
        source = str(needle.get("source") or "")
        if source and str(item.get("source") or "") != source:
            continue
        return True
    return False


def _cross_repo_refs(refs: list[dict[str, Any]], *, expected_repo_id: str) -> list[dict[str, Any]]:
    if not expected_repo_id:
        return []
    leaked: list[dict[str, Any]] = []
    for ref in refs:
        ref_repo_id = str(ref.get("repo_id") or "") or _repo_id_from_graph_ref(str(ref.get("path") or ""))
        if ref_repo_id and ref_repo_id != expected_repo_id:
            leaked.append(ref)
    return leaked


def _repo_id_from_graph_ref(path: str) -> str:
    if not path.startswith("<graph:repo:") or not path.endswith(">"):
        return ""
    rest = path[len("<graph:repo:") : -1]
    encoded_repo_id = rest.split(":", 1)[0]
    return unquote(encoded_repo_id)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 6)


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(float(item) for item in items) / len(items), 6)


def _gate_problems(
    summary: dict[str, Any],
    *,
    min_recall_at_5: float | None,
    min_precision_at_5: float | None,
    min_knowledge_recall_at_5: float | None,
    min_category_recall_at_5: dict[str, float],
    min_category_graph_edge_recall: dict[str, float],
    min_category_packed_recall: dict[str, float],
    require_source_integrity: bool,
    require_knowledge_source_current: bool,
    require_no_forbidden: bool,
    require_no_cross_repo: bool,
    require_fixture_corpus: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    if min_recall_at_5 is not None and float(summary.get("mean_recall_at_5") or 0.0) < min_recall_at_5:
        problems.append(Problem("error", "context_benchmark_recall_gate_failed", "context benchmark mean Recall@5 is below gate"))
    if min_precision_at_5 is not None and float(summary.get("mean_precision_at_5") or 0.0) < min_precision_at_5:
        problems.append(Problem("error", "context_benchmark_precision_gate_failed", "context benchmark mean Precision@5 is below gate"))
    if min_knowledge_recall_at_5 is not None and float(summary.get("mean_knowledge_recall_at_5") or 0.0) < min_knowledge_recall_at_5:
        problems.append(Problem("error", "context_benchmark_knowledge_gate_failed", "context benchmark mean knowledge Recall@5 is below gate"))
    by_category = summary.get("by_category") if isinstance(summary.get("by_category"), dict) else {}
    for category, threshold in sorted(min_category_recall_at_5.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_recall_at_5") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_recall_gate_failed", f"context benchmark {category} Recall@5 is below gate", category))
    for category, threshold in sorted(min_category_graph_edge_recall.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_graph_edge_recall") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_graph_edge_gate_failed", f"context benchmark {category} graph edge recall is below gate", category))
    for category, threshold in sorted(min_category_packed_recall.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_packed_recall") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_packed_recall_gate_failed", f"context benchmark {category} packed recall is below gate", category))
    if require_source_integrity and not bool(summary.get("source_ref_integrity")):
        problems.append(Problem("error", "context_benchmark_source_integrity_failed", "context benchmark source ref integrity failed"))
    if require_source_integrity and not bool(summary.get("knowledge_source_ref_integrity")):
        problems.append(Problem("error", "context_benchmark_knowledge_integrity_failed", "context benchmark knowledge source ref integrity failed"))
    if require_knowledge_source_current and not bool(summary.get("knowledge_source_status_current")):
        problems.append(Problem("error", "context_benchmark_knowledge_source_stale", "context benchmark knowledge source status is not current"))
    if require_no_forbidden and int(summary.get("forbidden_selected") or 0) > 0:
        problems.append(Problem("error", "context_benchmark_forbidden_selected", "context benchmark selected forbidden source refs"))
    if require_no_cross_repo and int(summary.get("cross_repo_ref_count") or 0) > 0:
        problems.append(Problem("error", "context_benchmark_cross_repo_leakage", "context benchmark selected source refs from another repository"))
    return problems
