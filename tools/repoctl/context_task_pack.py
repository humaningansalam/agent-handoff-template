from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_model import ContextCandidate
from .context_pack import estimate_tokens
from .graph_model import digest_data
from .markdown import find_section
from .repositories import RepoTarget
from .tasks import Problem, Task, resolve_task


def build_task_context_pack(root: Path, *, target: RepoTarget, task_id: str, budget_tokens: int = 5000, explain: bool = False) -> tuple[dict[str, Any], list[Problem], dict[str, Any]]:
    task = resolve_task(root, task_id)
    query = _task_seed_query(task)
    bundle, problems, meta = build_context_bundle(root, target=target, query=query, budget_tokens=budget_tokens, explain=explain)
    groups = _group_candidates(bundle.packed_context if bundle is not None else [])
    groups["reviewed_knowledge"] = bundle.knowledge_results if bundle is not None else []
    data = {
        "schema": "repoctl.context.task_pack",
        "schema_version": 1,
        "authoritative": False,
        "task": {
            "id": task.id,
            "path": task.rel_path,
            "status": task.status,
            "repo_id": str(task.frontmatter.get("repo_id") or ""),
            "area": str(task.frontmatter.get("area") or ""),
        },
        "seed": {
            "source": "task_fields_for_retrieval_only",
            "query": query,
            "used_sections": _used_sections(task),
        },
        "groups": groups,
        "metrics": _pack_metrics(groups, bundle),
        "bundle": bundle.to_dict() if bundle is not None else None,
        "warnings": _pack_warnings(bundle, task),
    }
    data["pack_digest"] = digest_data(data)
    return data, problems, meta


def compare_task_context_packs(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_must_read_drop: int | None = None,
    max_reviewed_knowledge_drop: int | None = None,
    require_warning_stability: bool = False,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_pack_artifact(baseline_path, problems, label="baseline")
    candidate = _read_pack_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
    count_deltas = {
        "must_read": _group_count_delta(baseline, candidate, "must_read"),
        "maybe_relevant": _group_count_delta(baseline, candidate, "maybe_relevant"),
        "verification_hints": _group_count_delta(baseline, candidate, "verification_hints"),
        "reviewed_knowledge": _group_count_delta(baseline, candidate, "reviewed_knowledge"),
    }
    metric_deltas = _metric_deltas(baseline, candidate)
    missing_refs = _missing_group_refs(baseline, candidate, "must_read")
    missing_reviewed_ids = _missing_reviewed_knowledge_ids(baseline, candidate)
    warning_deltas = _warning_deltas(baseline, candidate)
    regressions = _pack_regressions(
        count_deltas,
        missing_refs,
        missing_reviewed_ids,
        warning_deltas,
        max_must_read_drop=max_must_read_drop,
        max_reviewed_knowledge_drop=max_reviewed_knowledge_drop,
        require_warning_stability=require_warning_stability,
    )
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.task_pack.compare",
        "schema_version": 1,
        "baseline": _pack_identity(baseline_path, baseline),
        "candidate": _pack_identity(candidate_path, candidate),
        "count_deltas": count_deltas,
        "metric_deltas": metric_deltas,
        "warning_deltas": warning_deltas,
        "missing_must_read_refs": missing_refs,
        "missing_reviewed_knowledge_ids": missing_reviewed_ids,
        "regressions": [problem.to_dict() for problem in regressions],
        "gates": {
            "max_must_read_drop": max_must_read_drop,
            "max_reviewed_knowledge_drop": max_reviewed_knowledge_drop,
            "require_warning_stability": require_warning_stability,
        },
    }, problems


def run_task_context_pack_benchmark(
    root: Path,
    *,
    target: RepoTarget,
    fixture: Path,
    budget_tokens: int = 5000,
    explain: bool = False,
    min_must_read_recall: float | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    cases_path = fixture / "cases.json"
    if not cases_path.is_file():
        return {}, [Problem("error", "context_pack_benchmark_cases_missing", "context pack benchmark cases.json is missing", cases_path.as_posix())]
    try:
        data = json.loads(cases_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Problem("error", "context_pack_benchmark_cases_invalid_json", f"context pack benchmark cases.json is invalid: {exc}", cases_path.as_posix())]
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        return {}, [Problem("error", "context_pack_benchmark_cases_invalid", "context pack benchmark cases must be a list", cases_path.as_posix())]

    results: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        task_id = str(case.get("task_id") or "")
        if not task_id:
            problems.append(Problem("error", "context_pack_benchmark_task_missing", "context pack benchmark case is missing task_id", str(case.get("id") or "")))
            continue
        pack, pack_problems, _meta = build_task_context_pack(root, target=target, task_id=task_id, budget_tokens=budget_tokens, explain=explain)
        problems.extend(pack_problems)
        result = _score_pack_case(case, pack, pack_problems)
        results.append(result)

    summary = _pack_benchmark_summary(results)
    if min_must_read_recall is not None and float(summary.get("mean_must_read_recall") or 0.0) < min_must_read_recall:
        problems.append(Problem("error", "context_pack_benchmark_must_read_recall_failed", "context pack benchmark must_read recall is below gate"))
    payload = {
        "schema": "repoctl.context.task_pack.benchmark",
        "schema_version": 1,
        "fixture": fixture.as_posix(),
        "repository": target.to_dict(),
        "case_count": len(results),
        "results": results,
        "summary": summary,
        "gates": {"min_must_read_recall": min_must_read_recall},
    }
    payload["benchmark_digest"] = digest_data(payload)
    return payload, problems


def compare_task_context_pack_benchmarks(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_mean_must_read_recall_drop: float | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_pack_benchmark_artifact(baseline_path, problems, label="baseline")
    candidate = _read_pack_benchmark_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metric_deltas = {
        "mean_must_read_recall": _float_metric_delta(baseline_summary, candidate_summary, "mean_must_read_recall"),
        "required_must_read_count": _int_metric_delta(baseline_summary, candidate_summary, "required_must_read_count"),
        "warning_count": _int_metric_delta(baseline_summary, candidate_summary, "warning_count"),
    }
    case_deltas = _pack_benchmark_case_deltas(baseline, candidate)
    regressions = _pack_benchmark_regressions(
        metric_deltas,
        case_deltas,
        max_mean_must_read_recall_drop=max_mean_must_read_recall_drop,
    )
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.task_pack.benchmark.compare",
        "schema_version": 1,
        "baseline": _pack_benchmark_identity(baseline_path, baseline),
        "candidate": _pack_benchmark_identity(candidate_path, candidate),
        "metric_deltas": metric_deltas,
        "case_deltas": case_deltas,
        "regressions": [problem.to_dict() for problem in regressions],
        "gates": {"max_mean_must_read_recall_drop": max_mean_must_read_recall_drop},
    }, problems


def _task_seed_query(task: Task) -> str:
    parts = [
        str(task.frontmatter.get("title") or ""),
        str(task.frontmatter.get("area") or ""),
        _section(task, "Context Docs"),
        _section(task, "Goal"),
        _section(task, "Discovery"),
        _section(task, "Handoff"),
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def _read_pack_artifact(path: Path, problems: list[Problem], *, label: str) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "context_pack_artifact_missing", f"{label} context pack artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "context_pack_artifact_invalid_json", f"{label} context pack artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "context_pack_artifact_invalid", f"{label} context pack artifact must be an object", path.as_posix()))
        return {}
    if str(payload.get("command") or "") == "context pack" and payload.get("ok") is False:
        problems.append(Problem("error", "context_pack_artifact_failed", f"{label} context pack artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context pack" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "context_pack_artifact_missing_data", f"{label} context pack artifact is missing data", path.as_posix()))
        return {}
    groups = data.get("groups")
    if not isinstance(groups, dict):
        problems.append(Problem("error", "context_pack_artifact_invalid_data", f"{label} context pack artifact is missing groups", path.as_posix()))
        return {}
    expected_digest = str(data.get("pack_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        problems.append(Problem("error", "context_pack_artifact_digest_mismatch", f"{label} context pack artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _pack_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    return {
        "path": path.as_posix(),
        "pack_digest": str(data.get("pack_digest") or ""),
        "task_id": str(task.get("id") or ""),
    }


def _read_pack_benchmark_artifact(path: Path, problems: list[Problem], *, label: str) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "context_pack_benchmark_artifact_missing", f"{label} context pack benchmark artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "context_pack_benchmark_artifact_invalid_json", f"{label} context pack benchmark artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "context_pack_benchmark_artifact_invalid", f"{label} context pack benchmark artifact must be an object", path.as_posix()))
        return {}
    if str(payload.get("command") or "") == "context pack-benchmark" and payload.get("ok") is False:
        problems.append(Problem("error", "context_pack_benchmark_artifact_failed", f"{label} context pack benchmark artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context pack-benchmark" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "context_pack_benchmark_artifact_missing_data", f"{label} context pack benchmark artifact is missing data", path.as_posix()))
        return {}
    if str(data.get("schema") or "") != "repoctl.context.task_pack.benchmark":
        problems.append(Problem("error", "context_pack_benchmark_artifact_wrong_schema", f"{label} artifact is not a context pack benchmark", path.as_posix()))
        return {}
    expected_digest = str(data.get("benchmark_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"benchmark_digest", "artifact"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        problems.append(Problem("error", "context_pack_benchmark_artifact_digest_mismatch", f"{label} context pack benchmark artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _pack_benchmark_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "benchmark_digest": str(data.get("benchmark_digest") or ""),
        "case_count": int(data.get("case_count") or 0),
    }


def _score_pack_case(case: dict[str, Any], pack: dict[str, Any], problems: list[Problem]) -> dict[str, Any]:
    required = _expected_refs(case.get("required_must_read_refs"))
    must_read_refs = _group_refs(pack, "must_read")
    found = [ref for ref in required if _contains_expected_ref(must_read_refs, ref)]
    warning_codes = [str(warning.get("code") or "") for warning in pack.get("warnings", []) if isinstance(warning, dict) and warning.get("code")]
    return {
        "id": str(case.get("id") or ""),
        "task_id": str(case.get("task_id") or ""),
        "metrics": {
            "must_read_recall": _ratio(len(found), len(required)),
            "required_must_read_count": len(required),
            "warning_count": len(warning_codes),
        },
        "required_must_read_found": found,
        "missing_required_must_read": [ref for ref in required if not _contains_expected_ref(must_read_refs, ref)],
        "warning_codes": sorted(warning_codes),
        "problem_codes": [problem.code for problem in problems],
        "pack_digest": str(pack.get("pack_digest") or ""),
    }


def _pack_benchmark_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mean_must_read_recall": _mean(result.get("metrics", {}).get("must_read_recall", 0.0) for result in results),
        "required_must_read_count": sum(int(result.get("metrics", {}).get("required_must_read_count") or 0) for result in results),
        "warning_count": sum(int(result.get("metrics", {}).get("warning_count") or 0) for result in results),
    }


def _expected_refs(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if not isinstance(value, list):
        return refs
    for item in value:
        if not isinstance(item, dict):
            continue
        refs.append({"kind": str(item.get("kind") or ""), "path": str(item.get("path") or ""), "section": str(item.get("section") or "")})
    return refs


def _contains_expected_ref(haystack: list[dict[str, str]], needle: dict[str, str]) -> bool:
    for item in haystack:
        if str(item.get("path") or "") != str(needle.get("path") or ""):
            continue
        kind = str(needle.get("kind") or "")
        if kind and str(item.get("kind") or "") != kind:
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


def _float_metric_delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> dict[str, float]:
    baseline_value = float(baseline.get(key) or 0.0)
    candidate_value = float(candidate.get(key) or 0.0)
    return {
        "baseline": round(baseline_value, 6),
        "candidate": round(candidate_value, 6),
        "delta": round(candidate_value - baseline_value, 6),
    }


def _int_metric_delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> dict[str, int]:
    baseline_value = int(baseline.get(key) or 0)
    candidate_value = int(candidate.get(key) or 0)
    return {
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta": candidate_value - baseline_value,
    }


def _pack_benchmark_case_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_cases = _pack_benchmark_cases_by_id(baseline)
    candidate_cases = _pack_benchmark_cases_by_id(candidate)
    deltas: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline_cases) | set(candidate_cases)):
        baseline_case = baseline_cases.get(case_id, {})
        candidate_case = candidate_cases.get(case_id, {})
        baseline_metrics = baseline_case.get("metrics") if isinstance(baseline_case.get("metrics"), dict) else {}
        candidate_metrics = candidate_case.get("metrics") if isinstance(candidate_case.get("metrics"), dict) else {}
        deltas.append(
            {
                "id": case_id,
                "present_in_baseline": bool(baseline_case),
                "present_in_candidate": bool(candidate_case),
                "task_id": str(candidate_case.get("task_id") or baseline_case.get("task_id") or ""),
                "must_read_recall": _float_metric_delta(baseline_metrics, candidate_metrics, "must_read_recall"),
                "required_must_read_count": _int_metric_delta(baseline_metrics, candidate_metrics, "required_must_read_count"),
                "warning_count": _int_metric_delta(baseline_metrics, candidate_metrics, "warning_count"),
            }
        )
    return deltas


def _pack_benchmark_cases_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, list):
        return {}
    cases: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "")
        if case_id:
            cases[case_id] = item
    return cases


def _pack_benchmark_regressions(
    metric_deltas: dict[str, dict[str, Any]],
    case_deltas: list[dict[str, Any]],
    *,
    max_mean_must_read_recall_drop: float | None,
) -> list[Problem]:
    problems: list[Problem] = []
    if max_mean_must_read_recall_drop is not None and float(metric_deltas["mean_must_read_recall"]["delta"]) < -abs(max_mean_must_read_recall_drop):
        problems.append(Problem("error", "context_pack_benchmark_must_read_recall_regressed", "context pack benchmark mean must_read recall dropped more than allowed"))
    for item in case_deltas:
        if bool(item["present_in_baseline"]) and not bool(item["present_in_candidate"]):
            problems.append(Problem("error", "context_pack_benchmark_case_missing", "candidate context pack benchmark artifact is missing a baseline case", str(item["id"])))
    return problems


def _group_count_delta(baseline: dict[str, Any], candidate: dict[str, Any], group: str) -> dict[str, int]:
    baseline_count = _group_count(baseline, group)
    candidate_count = _group_count(candidate, group)
    return {
        "baseline": baseline_count,
        "candidate": candidate_count,
        "delta": candidate_count - baseline_count,
    }


def _group_count(data: dict[str, Any], group: str) -> int:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    values = groups.get(group)
    return len(values) if isinstance(values, list) else 0


def _metric_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, dict[str, int]]:
    keys = (
        "unique_must_read_source_count",
        "unique_verification_source_count",
        "packed_context_count",
        "candidate_context_count",
        "requested_tokens",
        "estimated_tokens",
    )
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    candidate_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    return {
        key: {
            "baseline": int(baseline_metrics.get(key) or 0),
            "candidate": int(candidate_metrics.get(key) or 0),
            "delta": int(candidate_metrics.get(key) or 0) - int(baseline_metrics.get(key) or 0),
        }
        for key in keys
    }


def _missing_group_refs(baseline: dict[str, Any], candidate: dict[str, Any], group: str) -> list[dict[str, str]]:
    candidate_refs = {_ref_key(ref) for ref in _group_refs(candidate, group)}
    missing = [ref for ref in _group_refs(baseline, group) if _ref_key(ref) not in candidate_refs]
    return sorted(missing, key=lambda item: (item.get("path", ""), item.get("section", ""), item.get("kind", "")))


def _group_refs(data: dict[str, Any], group: str) -> list[dict[str, str]]:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    values = groups.get(group)
    refs: list[dict[str, str]] = []
    if not isinstance(values, list):
        return refs
    for item in values:
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        refs.append(
            {
                "kind": str(ref.get("kind") or ""),
                "path": str(ref.get("path") or ""),
                "section": str(ref.get("section") or ""),
            }
        )
    return refs


def _ref_key(ref: dict[str, str]) -> tuple[str, str, str]:
    return (str(ref.get("kind") or ""), str(ref.get("path") or ""), str(ref.get("section") or ""))


def _missing_reviewed_knowledge_ids(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    candidate_ids = set(_reviewed_knowledge_ids(candidate))
    return sorted(record_id for record_id in _reviewed_knowledge_ids(baseline) if record_id not in candidate_ids)


def _warning_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_codes = _warning_codes(baseline)
    candidate_codes = _warning_codes(candidate)
    all_codes = sorted(set(baseline_codes) | set(candidate_codes))
    return {
        "baseline_codes": sorted(baseline_codes),
        "candidate_codes": sorted(candidate_codes),
        "missing_codes": sorted(code for code in baseline_codes if code not in candidate_codes),
        "added_codes": sorted(code for code in candidate_codes if code not in baseline_codes),
        "counts": {
            code: {
                "baseline": baseline_codes.count(code),
                "candidate": candidate_codes.count(code),
                "delta": candidate_codes.count(code) - baseline_codes.count(code),
            }
            for code in all_codes
        },
    }


def _warning_codes(data: dict[str, Any]) -> list[str]:
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        return []
    codes: list[str] = []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "")
        if code:
            codes.append(code)
    return codes


def _reviewed_knowledge_ids(data: dict[str, Any]) -> list[str]:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    values = groups.get("reviewed_knowledge")
    ids: list[str] = []
    if not isinstance(values, list):
        return ids
    for item in values:
        if not isinstance(item, dict):
            continue
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        record_id = str(record.get("id") or "")
        if record_id:
            ids.append(record_id)
    return ids


def _pack_regressions(
    count_deltas: dict[str, dict[str, int]],
    missing_must_read_refs: list[dict[str, str]],
    missing_reviewed_knowledge_ids: list[str],
    warning_deltas: dict[str, Any],
    *,
    max_must_read_drop: int | None,
    max_reviewed_knowledge_drop: int | None,
    require_warning_stability: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    if max_must_read_drop is not None and int(count_deltas["must_read"]["delta"]) < -abs(max_must_read_drop):
        problems.append(Problem("error", "context_pack_must_read_regressed", "context pack must_read count dropped more than allowed"))
    if max_reviewed_knowledge_drop is not None and int(count_deltas["reviewed_knowledge"]["delta"]) < -abs(max_reviewed_knowledge_drop):
        problems.append(Problem("error", "context_pack_reviewed_knowledge_regressed", "context pack reviewed_knowledge count dropped more than allowed"))
    for ref in missing_must_read_refs:
        problems.append(Problem("error", "context_pack_must_read_ref_missing", "candidate context pack is missing a baseline must_read source ref", f"{ref.get('path', '')}#{ref.get('section', '')}"))
    for record_id in missing_reviewed_knowledge_ids:
        problems.append(Problem("error", "context_pack_reviewed_knowledge_missing", "candidate context pack is missing a baseline reviewed knowledge record", record_id))
    if require_warning_stability:
        for code in warning_deltas.get("missing_codes", []):
            problems.append(Problem("error", "context_pack_warning_missing", "candidate context pack is missing a baseline warning code", str(code)))
        for code in warning_deltas.get("added_codes", []):
            problems.append(Problem("error", "context_pack_warning_added", "candidate context pack added a warning code", str(code)))
    return problems


def _used_sections(task: Task) -> list[str]:
    return [name for name in ("Context Docs", "Goal", "Discovery", "Handoff") if _section(task, name).strip()]


def _section(task: Task, heading: str) -> str:
    try:
        section = find_section(task.body, heading)
    except Exception:
        return ""
    return task.body[section.body_start : section.end].strip()


def _group_candidates(candidates: list[ContextCandidate]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"must_read": [], "maybe_relevant": [], "verification_hints": []}
    for candidate in candidates:
        ref = candidate.source_ref
        item = candidate.to_dict()
        if ref.kind in {"completion_receipt", "task_artifact"} or "Verification" in ref.section:
            groups["verification_hints"].append(item)
        elif ref.path == "AGENTS.md" or ref.path.startswith("docs/contracts/") or ref.path.startswith("docs/adr/"):
            groups["must_read"].append(item)
        else:
            groups["maybe_relevant"].append(item)
    return groups


def _pack_metrics(groups: dict[str, list[dict[str, Any]]], bundle: Any) -> dict[str, Any]:
    group_counts = {name: len(items) for name, items in sorted(groups.items())}
    group_estimated_tokens = {
        name: sum(estimate_tokens(str(item.get("excerpt") or _knowledge_text(item))) for item in items)
        for name, items in sorted(groups.items())
    }
    must_read_refs = _source_ref_keys(groups.get("must_read", []))
    verification_refs = _source_ref_keys(groups.get("verification_hints", []))
    budget = bundle.budget if bundle is not None else {}
    return {
        "group_counts": group_counts,
        "group_estimated_tokens": group_estimated_tokens,
        "must_read_source_refs": must_read_refs,
        "verification_source_refs": verification_refs,
        "unique_must_read_source_count": len({(ref["kind"], ref["path"], ref["section"]) for ref in must_read_refs}),
        "unique_verification_source_count": len({(ref["kind"], ref["path"], ref["section"]) for ref in verification_refs}),
        "packed_context_count": int(budget.get("packed_count") or 0),
        "candidate_context_count": int(budget.get("candidate_count") or 0),
        "requested_tokens": int(budget.get("requested_tokens") or 0),
        "estimated_tokens": int(budget.get("estimated_tokens") or 0),
    }


def _source_ref_keys(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in items:
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        refs.append(
            {
                "kind": str(ref.get("kind") or ""),
                "path": str(ref.get("path") or ""),
                "section": str(ref.get("section") or ""),
            }
        )
    return sorted(refs, key=lambda ref: (ref["kind"], ref["path"], ref["section"]))


def _knowledge_text(item: dict[str, Any]) -> str:
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    return "\n".join(str(record.get(key) or "") for key in ("title", "claim", "summary"))


def _pack_warnings(bundle: Any, task: Task) -> list[dict[str, str]]:
    warnings = [
        {
            "code": "context_pack_not_authoritative",
            "message": "task context pack uses task text only as retrieval seed; it does not set task scope or create knowledge",
        }
    ]
    task_repo_id = str(task.frontmatter.get("repo_id") or "")
    if task_repo_id and bundle is not None and str(bundle.repository.get("id") or "") != task_repo_id:
        warnings.append(
            {
                "code": "context_pack_repo_mismatch",
                "message": f"task repo_id is {task_repo_id}, but context pack used {bundle.repository.get('id')}",
            }
        )
    if bundle is None:
        return warnings
    completeness = bundle.completeness if isinstance(bundle.completeness, dict) else {}
    if completeness.get("graph_available") is False:
        warnings.append(
            {
                "code": "context_pack_graph_unavailable",
                "message": "context pack was built without a Graph snapshot; graph-backed file and symbol evidence may be incomplete",
            }
        )
    graph_completeness = completeness.get("graph_completeness") if isinstance(completeness.get("graph_completeness"), dict) else {}
    parse_error_count = int(graph_completeness.get("parse_error_count") or 0)
    if parse_error_count > 0 or graph_completeness.get("code_facts_complete") is False:
        warnings.append(
            {
                "code": "context_pack_graph_code_facts_incomplete",
                "message": f"Graph code facts are incomplete; parse_error_count={parse_error_count}",
            }
        )
    provider_failures = graph_completeness.get("provider_failures")
    if isinstance(provider_failures, list) and provider_failures:
        warnings.append(
            {
                "code": "context_pack_graph_provider_failures",
                "message": f"Graph provider failures are present; count={len(provider_failures)}",
            }
        )
    knowledge_lifecycle = completeness.get("knowledge_lifecycle") if isinstance(completeness.get("knowledge_lifecycle"), dict) else {}
    excluded_statuses = knowledge_lifecycle.get("excluded_statuses") if isinstance(knowledge_lifecycle.get("excluded_statuses"), dict) else {}
    for status in ("stale", "superseded", "deprecated"):
        count = int(excluded_statuses.get(status) or 0)
        if count > 0:
            warnings.append(
                {
                    "code": f"context_pack_knowledge_{status}_excluded",
                    "message": f"context pack excluded {count} {status} knowledge record(s) from default reviewed knowledge",
                }
            )
    return warnings
