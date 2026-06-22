from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_model import ContextBundle
from .repositories import require_repo_target
from .tasks import Problem


def run_context_benchmark(root: Path, *, fixture: Path, repo_id: str = "", budget_tokens: int = 3000) -> tuple[dict[str, Any], list[Problem]]:
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
        bundle, bundle_problems, _meta = build_context_bundle(root, target=target, query=str(question.get("question") or ""), budget_tokens=budget_tokens)
        problems.extend(bundle_problems)
        spec = expected.get(question_id, {}) if isinstance(expected, dict) else {}
        results.append(_score_question(question, spec, bundle))

    summary = _summarize(results)
    return {
        "fixture": fixture.as_posix(),
        "question_count": len(results),
        "results": results,
        "summary": summary,
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


def _score_question(question: dict[str, Any], spec: dict[str, Any], bundle: ContextBundle | None) -> dict[str, Any]:
    required = _refs(spec.get("required_source_refs"))
    required_knowledge = _refs(spec.get("required_knowledge_source_refs"))
    optional = _refs(spec.get("acceptable_optional_refs"))
    forbidden = _refs(spec.get("forbidden_refs"))
    candidate_refs = _bundle_refs(bundle, field="candidates")
    packed_refs = _bundle_refs(bundle, field="packed_context")
    knowledge_refs = _knowledge_refs(bundle)

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
        },
        "required_found_at_5": required_top5,
        "required_found_at_10": required_top10,
        "missing_required_at_10": [ref for ref in required if not _contains_ref(top10, ref)],
        "required_knowledge_found_at_5": required_knowledge_top5,
        "missing_required_knowledge_at_5": [ref for ref in required_knowledge if not _contains_ref(knowledge_top5, ref)],
        "selected_forbidden": selected_forbidden,
        "top_refs": top5,
        "top_knowledge_refs": knowledge_top5,
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
