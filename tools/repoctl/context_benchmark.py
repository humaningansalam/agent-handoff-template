from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_model import ContextBundle
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
    require_source_integrity: bool = False,
    require_knowledge_source_current: bool = False,
    require_no_forbidden: bool = False,
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
    results: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question.get("id") or "")
        target_repo_id = repo_id or str(question.get("repo_id") or "")
        target = require_repo_target(root, repo_id=target_repo_id or None)
        bundle, bundle_problems, _meta = build_context_bundle(root, target=target, query=str(question.get("question") or ""), budget_tokens=budget_tokens, explain=True)
        problems.extend(bundle_problems)
        spec = expected.get(question_id, {}) if isinstance(expected, dict) else {}
        results.append(_score_question(question, spec, bundle, bundle_problems))

    summary = _summarize(results)
    problems.extend(
        _gate_problems(
            summary,
            min_recall_at_5=min_recall_at_5,
            min_precision_at_5=min_precision_at_5,
            min_knowledge_recall_at_5=min_knowledge_recall_at_5,
            require_source_integrity=require_source_integrity,
            require_knowledge_source_current=require_knowledge_source_current,
            require_no_forbidden=require_no_forbidden,
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
            "require_source_integrity": require_source_integrity,
            "require_knowledge_source_current": require_knowledge_source_current,
            "require_no_forbidden": require_no_forbidden,
        },
    }
    data["benchmark_digest"] = digest_data(data)
    return data, problems


def compare_context_benchmarks(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_recall_at_5_drop: float | None = None,
    max_precision_at_5_drop: float | None = None,
    max_knowledge_recall_at_5_drop: float | None = None,
    max_question_recall_at_5_drop: float | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_benchmark_artifact(baseline_path, problems, label="baseline")
    candidate = _read_benchmark_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
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
        "gates": {
            "max_recall_at_5_drop": max_recall_at_5_drop,
            "max_precision_at_5_drop": max_precision_at_5_drop,
            "max_knowledge_recall_at_5_drop": max_knowledge_recall_at_5_drop,
            "max_question_recall_at_5_drop": max_question_recall_at_5_drop,
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


def _score_question(question: dict[str, Any], spec: dict[str, Any], bundle: ContextBundle | None, problems: list[Problem]) -> dict[str, Any]:
    required = _refs(spec.get("required_source_refs"))
    required_knowledge = _refs(spec.get("required_knowledge_source_refs"))
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
    required_top5 = [ref for ref in required if _contains_ref(top5, ref)]
    required_top10 = [ref for ref in required if _contains_ref(top10, ref)]
    required_knowledge_top5 = [ref for ref in required_knowledge if _contains_ref(knowledge_top5, ref)]
    selected_forbidden = [ref for ref in forbidden if _contains_ref(candidate_refs, ref) or _contains_ref(packed_refs, ref)]
    relevant_top5 = sum(1 for ref in top5 if _contains_ref(required, ref) or _contains_ref(optional, ref))
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
            "source_ref_integrity": len(integrity_failures) == 0,
            "knowledge_source_ref_integrity": len(knowledge_integrity_failures) == 0,
            "forbidden_selected": len(selected_forbidden),
            "packed_required_found": sum(1 for ref in required if _contains_ref(packed_refs, ref)),
            "knowledge_recall_at_5": _ratio(len(required_knowledge_top5), len(required_knowledge)) if required_knowledge else 0.0,
            "required_knowledge_count": len(required_knowledge),
            "knowledge_result_count": len(knowledge_refs),
            "knowledge_score_breakdown_present": all(result["has_field_breakdown"] for result in knowledge_score_results),
            "knowledge_source_status_current": stale_knowledge_excluded == 0 and all(status.get("digest_matches") is True for status in knowledge_source_statuses),
            "knowledge_stale_record_excluded": stale_knowledge_excluded,
        },
        "required_found_at_5": required_top5,
        "required_found_at_10": required_top10,
        "missing_required_at_10": [ref for ref in required if not _contains_ref(top10, ref)],
        "required_knowledge_found_at_5": required_knowledge_top5,
        "missing_required_knowledge_at_5": [ref for ref in required_knowledge if not _contains_ref(knowledge_top5, ref)],
        "selected_forbidden": selected_forbidden,
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
        "source_ref_integrity": all(metric["source_ref_integrity"] for metric in metrics),
        "knowledge_source_ref_integrity": all(metric["knowledge_source_ref_integrity"] for metric in metrics),
        "mean_knowledge_recall_at_5": _mean(metric["knowledge_recall_at_5"] for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_expected_questions": sum(1 for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_result_questions": sum(1 for metric in metrics if metric["knowledge_result_count"]),
        "knowledge_score_breakdown_integrity": all(metric["knowledge_score_breakdown_present"] for metric in metrics),
        "knowledge_source_status_current": all(metric["knowledge_source_status_current"] for metric in metrics),
        "knowledge_stale_record_excluded": sum(int(metric["knowledge_stale_record_excluded"]) for metric in metrics),
        "forbidden_selected": sum(int(metric["forbidden_selected"]) for metric in metrics),
    }


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
    require_source_integrity: bool,
    require_knowledge_source_current: bool,
    require_no_forbidden: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    if min_recall_at_5 is not None and float(summary.get("mean_recall_at_5") or 0.0) < min_recall_at_5:
        problems.append(Problem("error", "context_benchmark_recall_gate_failed", "context benchmark mean Recall@5 is below gate"))
    if min_precision_at_5 is not None and float(summary.get("mean_precision_at_5") or 0.0) < min_precision_at_5:
        problems.append(Problem("error", "context_benchmark_precision_gate_failed", "context benchmark mean Precision@5 is below gate"))
    if min_knowledge_recall_at_5 is not None and float(summary.get("mean_knowledge_recall_at_5") or 0.0) < min_knowledge_recall_at_5:
        problems.append(Problem("error", "context_benchmark_knowledge_gate_failed", "context benchmark mean knowledge Recall@5 is below gate"))
    if require_source_integrity and not bool(summary.get("source_ref_integrity")):
        problems.append(Problem("error", "context_benchmark_source_integrity_failed", "context benchmark source ref integrity failed"))
    if require_source_integrity and not bool(summary.get("knowledge_source_ref_integrity")):
        problems.append(Problem("error", "context_benchmark_knowledge_integrity_failed", "context benchmark knowledge source ref integrity failed"))
    if require_knowledge_source_current and not bool(summary.get("knowledge_source_status_current")):
        problems.append(Problem("error", "context_benchmark_knowledge_source_stale", "context benchmark knowledge source status is not current"))
    if require_no_forbidden and int(summary.get("forbidden_selected") or 0) > 0:
        problems.append(Problem("error", "context_benchmark_forbidden_selected", "context benchmark selected forbidden source refs"))
    return problems
