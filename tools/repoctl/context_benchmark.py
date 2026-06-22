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
        },
    }
    data["benchmark_digest"] = digest_data(data)
    return data, problems


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
    return problems
