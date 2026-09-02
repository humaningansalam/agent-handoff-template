from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote

from .context import build_context_bundle, compact_context_bundle
from .context_model import ContextBundle
from .discovery_outcomes import result_member_capsules, validate_completion_outcome
from .graph_model import GraphSnapshot
from .graph_model import digest_data
from .graph_store import materialize_graph
from .knowledge_candidates import (
    KnowledgeSourceRefKind,
    KnowledgeSourceResolutionStatus,
    _knowledge_approval_binding,
    _knowledge_record_contract_problems,
    knowledge_source_ref_resolutions,
)
from .knowledge_projection import rebuild_knowledge_projection
from .language_profiles import default_indexing_excludes
from .repositories import RepoTarget, RepositoryIdentitySource, require_repo_target
from .result_receipts import (
    ContextResultRequest,
    ResultAuthority,
    ResultProducer,
    ResultSelection,
    context_result_citations,
    context_result_receipt_projection,
    write_result_receipt,
)
from .tasks import Problem

IGNORED_SOURCE_PATTERNS = tuple(default_indexing_excludes())
KNOWLEDGE_RECORD_ID_RE = re.compile(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*")
KNOWLEDGE_EVENT_ID_RE = re.compile(r"E-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*")


def run_context_benchmark(
    root: Path,
    *,
    fixture: Path,
    repo_id: str = "",
    min_recall_at_5: float | None = None,
    min_precision_at_5: float | None = None,
    min_knowledge_recall_at_5: float | None = None,
    min_category_recall_at_5: dict[str, float] | None = None,
    min_category_knowledge_recall_at_5: dict[str, float] | None = None,
    min_category_graph_edge_recall: dict[str, float] | None = None,
    min_category_visible_recall: dict[str, float] | None = None,
    require_source_integrity: bool = False,
    require_knowledge_source_current: bool = False,
    require_no_forbidden: bool = False,
    require_no_cross_repo: bool = False,
    require_fixture_corpus: bool = False,
    include_attribution: bool = False,
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
    attribution_inputs: dict[str, tuple[ContextBundle, dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = {}
    with TemporaryDirectory(prefix="repoctl-context-benchmark-") as temporary_state:
        graph_state_root = Path(temporary_state)
        graph_cache: dict[str, tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]] = {}
        for question in questions:
            question_id = str(question.get("id") or "")
            target_repo_id = repo_id or str(question.get("repo_id") or "")
            target = require_repo_target(root, repo_id=target_repo_id or None)
            graph_result = graph_cache.get(target.id)
            if graph_result is None:
                graph_result = materialize_graph(root, target=target, rebuild=True, state_root=graph_state_root)
                graph_cache[target.id] = graph_result
            bundle, bundle_problems, _meta = build_context_bundle(
                root,
                target=target,
                query=str(question.get("question") or ""),
                explain=True,
                mode=str(question.get("mode") or ""),
                graph_result=graph_result,
                graph_state_root=graph_state_root,
            )
            _snapshot, graph_problems, _graph_meta = graph_result
            problems.extend(bundle_problems)
            problems.extend(graph_problems)
            spec = expected.get(question_id, {}) if isinstance(expected, dict) else {}
            results.append(_score_question(question, spec, bundle, [*bundle_problems, *graph_problems]))
            if include_attribution:
                full_bundle = bundle.to_dict()
                request = ContextResultRequest(
                    query=str(question.get("question") or ""),
                    mode=str(bundle.query.get("mode") or "auto"),
                )
                receipt = write_result_receipt(
                    graph_state_root,
                    target=target,
                    producer=ResultProducer.CONTEXT,
                    result_id=str(full_bundle["bundle_digest"]),
                    request=request,
                    selections=context_result_citations(full_bundle),
                )
                compact_bundle = compact_context_bundle(bundle)
                projection = context_result_receipt_projection(receipt, compact_bundle=compact_bundle, full=True)
                attribution_inputs[question_id] = (
                    bundle,
                    projection,
                    receipt,
                    result_member_capsules(root, target=target, receipt=receipt),
                )

    summary = _summarize(results)
    problems.extend(
        _gate_problems(
            summary,
            min_recall_at_5=min_recall_at_5,
            min_precision_at_5=min_precision_at_5,
            min_knowledge_recall_at_5=min_knowledge_recall_at_5,
            min_category_recall_at_5=min_category_recall_at_5 or {},
            min_category_knowledge_recall_at_5=min_category_knowledge_recall_at_5 or {},
            min_category_graph_edge_recall=min_category_graph_edge_recall or {},
            min_category_visible_recall=min_category_visible_recall or {},
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
            "min_category_knowledge_recall_at_5": dict(sorted((min_category_knowledge_recall_at_5 or {}).items())),
            "min_category_graph_edge_recall": dict(sorted((min_category_graph_edge_recall or {}).items())),
            "min_category_visible_recall": dict(sorted((min_category_visible_recall or {}).items())),
            "require_source_integrity": require_source_integrity,
            "require_knowledge_source_current": require_knowledge_source_current,
            "require_no_forbidden": require_no_forbidden,
            "require_no_cross_repo": require_no_cross_repo,
            "require_fixture_corpus": require_fixture_corpus,
        },
        "fixture_corpus": corpus_status,
    }
    if include_attribution:
        attribution, attribution_problems = _attribution_capsule(root, fixture, attribution_inputs=attribution_inputs)
        problems.extend(attribution_problems)
        if attribution:
            data["attribution"] = attribution
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
    result = {
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
    }
    return result, problems


def _attribution_capsule(
    root: Path,
    fixture: Path,
    *,
    attribution_inputs: dict[str, tuple[ContextBundle, dict[str, Any], dict[str, Any], list[dict[str, Any]]]],
) -> tuple[dict[str, Any], list[Problem]]:
    path = fixture / "attribution-cases.json"
    if not path.is_file():
        return {}, [Problem("error", "context_benchmark_attribution_missing", "attribution mode requires attribution-cases.json", path.as_posix())]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return _project_attribution_cases(root, value, attribution_inputs=attribution_inputs), []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, [Problem("error", "context_benchmark_attribution_invalid", f"context benchmark attribution fixture is invalid: {exc}", path.as_posix())]


def _project_attribution_cases(
    root: Path,
    value: Any,
    *,
    attribution_inputs: dict[str, tuple[ContextBundle, dict[str, Any], dict[str, Any], list[dict[str, Any]]]],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "schema_version", "question_id", "cases", "trace"}:
        raise ValueError("attribution fixture schema is invalid")
    if value.get("schema") != "repoctl.context.benchmark.attribution-cases" or value.get("schema_version") != 2:
        raise ValueError("attribution fixture version is invalid")
    question_id = str(value.get("question_id") or "")
    inputs = attribution_inputs.get(question_id)
    if inputs is None:
        raise ValueError("attribution fixture question is not a benchmark question")
    bundle, projection, receipt, members = inputs
    raw_cases = value.get("cases")
    trace = value.get("trace")
    if not isinstance(raw_cases, list) or not isinstance(trace, dict):
        raise ValueError("attribution cases and trace are required")

    member_by_selection = {(str(item["authority"]), str(item["ref"])): item for item in members}
    cases: dict[str, dict[str, Any]] = {}
    for raw in raw_cases:
        if not isinstance(raw, dict) or set(raw) != {"authority", "case_id", "ref"}:
            raise ValueError("attribution case is invalid")
        case_id = str(raw.get("case_id") or "")
        authority = str(raw.get("authority") or "")
        ref = str(raw.get("ref") or "")
        member = member_by_selection.get((authority, ref))
        if not case_id or case_id in cases:
            raise ValueError("attribution case identity is invalid")
        if member is None:
            raise ValueError(f"attribution case {case_id} is not a selectable receipt member")
        subject = member["subject"]
        identity = {
            "authority": authority,
            "ref": ref,
            "subject_key": subject["key"],
            "version_digest": subject["version_digest"],
        }
        cases[case_id] = {
            "authority": authority,
            "ref": ref,
            "member": member,
            "candidate_id": digest_data(identity),
        }

    compact_capture = str(trace.get("compact_capture") or "")
    if compact_capture not in {"captured", "missing"}:
        raise ValueError("compact projection capture is invalid")

    completion_capture = trace.get("completion")
    completion_outcome: dict[str, Any] | None = None
    completed_at: datetime | None = None
    if completion_capture is not None:
        if not isinstance(completion_capture, dict) or set(completion_capture) != {"completed_at", "roles"}:
            raise ValueError("completion capture is invalid")
        completed_at = _timestamp(completion_capture.get("completed_at"), label="completion outcome")
        completion_outcome = _build_completion_outcome(receipt, members, completion_capture.get("roles"))

    selected, reviewed, chosen, verified = _completion_stage_sets(completion_outcome, receipt=receipt, members=members)
    reused = _later_reused_subjects(
        trace.get("later_results"),
        completed_at=completed_at,
        receipt=receipt,
        members=members,
    )

    full = bundle.to_dict()
    knowledge_reused = _knowledge_reused_subjects(
        trace.get("knowledge_results"),
        root=root,
        completed_at=completed_at,
        bundle=full,
        receipt=receipt,
        members=members,
    )
    retrieval_by_selection = _retrieval_evidence(full)
    compact_visible = {
        (str(entry["primary_citation"]["authority"]), str(entry["primary_citation"]["ref"]))
        for entry in projection["compact"]["representative_citations"]
    }

    projected: list[dict[str, Any]] = []
    for case_id, case in sorted(cases.items(), key=lambda item: item[1]["candidate_id"]):
        member = case["member"]
        subject_id = str(member["subject"]["subject_id"])
        selection = (case["authority"], case["ref"])
        retrieval = retrieval_by_selection.get(selection)
        visible: bool | str = "unknown" if compact_capture == "missing" else selection in compact_visible
        projected.append(
            {
                "case_id": case_id,
                "candidate_id": case["candidate_id"],
                "authority": case["authority"],
                "ref": case["ref"],
                "member_id": member["member_id"],
                "subject_key": member["subject"]["key"],
                "version_digest": member["subject"]["version_digest"],
                "stages": {
                    "available": True,
                    "retrieved": retrieval is not None,
                    "compact_visible": visible,
                    "selected": "unknown" if completion_outcome is None else subject_id in selected,
                    "reviewed": "unknown" if completion_outcome is None else subject_id in reviewed,
                    "chosen": "unknown" if completion_outcome is None else subject_id in chosen,
                    "verified": "unknown" if completion_outcome is None else subject_id in verified,
                    "later_reused": _reuse_stage(subject_id, direct=reused, knowledge=knowledge_reused),
                },
                "retrieval": retrieval,
            }
        )
    return {
        "schema": "repoctl.context.benchmark.attribution",
        "schema_version": 1,
        "claim_scope": "correlation_only",
        "non_gating": True,
        "captured_at": str(trace.get("captured_at") or ""),
        "source_artifacts": {
            "question_id": question_id,
            "producer": receipt["producer"],
            "result_id": receipt["result_id"],
            "receipt_digest": receipt["receipt_digest"],
            "projection_digest": digest_data(projection) if compact_capture == "captured" else "",
            "completion_outcome_digest": completion_outcome.get("outcome_digest") if completion_outcome else "",
        },
        "candidates": projected,
    }


def _selection_set(value: Any, *, label: str) -> set[tuple[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} is missing")
    result: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"authority", "ref"}:
            raise ValueError(f"{label} selection is invalid")
        selection = (str(item.get("authority") or ""), str(item.get("ref") or ""))
        if selection[0] not in {authority.value for authority in ResultAuthority} or not selection[1] or selection in result:
            raise ValueError(f"{label} selection is invalid")
        result.add(selection)
    return result


def _build_completion_outcome(
    receipt: dict[str, Any],
    members: list[dict[str, Any]],
    roles: Any,
) -> dict[str, Any]:
    expected = {"chosen", "reviewed", "selected", "verification", "version_overrides"}
    if not isinstance(roles, dict) or set(roles) != expected:
        raise ValueError("completion roles are invalid")
    member_by_selection = {(str(item["authority"]), str(item["ref"])): item for item in members}
    selected = _selection_set(roles["selected"], label="selected")
    reviewed = _selection_set(roles["reviewed"], label="reviewed")
    chosen = _selection_set(roles["chosen"], label="chosen")
    raw_verification = roles["verification"]
    if not isinstance(raw_verification, list):
        raise ValueError("completion verification is invalid")
    verification_selections: set[tuple[str, str]] = set()
    for record in raw_verification:
        if not isinstance(record, dict) or set(record) != {"refs", "status"} or str(record.get("status") or "") not in {"passed", "failed", "mixed", "blocked"}:
            raise ValueError("completion verification is invalid")
        verification_selections.update(_selection_set(record["refs"], label="verification"))
    overrides: dict[tuple[str, str], str] = {}
    if not isinstance(roles["version_overrides"], list):
        raise ValueError("completion version overrides are invalid")
    for item in roles["version_overrides"]:
        if not isinstance(item, dict) or set(item) != {"authority", "ref", "version_digest"}:
            raise ValueError("completion version override is invalid")
        key = (str(item.get("authority") or ""), str(item.get("ref") or ""))
        version = str(item.get("version_digest") or "")
        if key in overrides or key not in member_by_selection or not _sha256(version):
            raise ValueError("completion version override is invalid")
        overrides[key] = version
    referenced = selected | reviewed | chosen | verification_selections
    if not referenced <= set(member_by_selection):
        raise ValueError("completion role references a non-member")

    subject_by_selection: dict[tuple[str, str], dict[str, Any]] = {}
    for key in referenced:
        subject = json.loads(json.dumps(member_by_selection[key]["subject"]))
        if key in overrides:
            subject["version_digest"] = overrides[key]
            subject["subject_id"] = digest_data({
                "kind": subject["kind"],
                "identity": subject["identity"],
                "version_digest": subject["version_digest"],
            })
        subject_by_selection[key] = subject
    ordered_subjects = sorted(
        {str(item["subject_id"]): item for item in subject_by_selection.values()}.values(),
        key=lambda item: (str(item["kind"]), json.dumps(item["identity"], sort_keys=True, separators=(",", ":")), str(item["version_digest"])),
    )
    local_by_stable = {str(item["subject_id"]): f"s{index}" for index, item in enumerate(ordered_subjects, start=1)}
    subject_table = [
        {
            "id": local_by_stable[str(item["subject_id"])],
            "kind": item["kind"],
            "identity": item["identity"],
            "key": item["key"],
            "version_digest": item["version_digest"],
        }
        for item in ordered_subjects
    ]
    query = str(receipt["request"]["query"])
    episode_id = digest_data({"kind": "task_discovery_query", "query": query})
    request_digest = digest_data(receipt["request"])
    citations = []
    for key in selected:
        member = member_by_selection[key]
        subject = subject_by_selection[key]
        citations.append({
            "producer": receipt["producer"],
            "result_id": receipt["result_id"],
            "episode_id": episode_id,
            "canonical_request_digest": request_digest,
            "member_id": member["member_id"],
            "source_receipt_digest": receipt["receipt_digest"],
            "subject_id": local_by_stable[str(subject["subject_id"])],
            "claims": member["claims"],
        })
    citations.sort(key=lambda item: item["member_id"])
    seed_result = None
    if citations:
        seed_result = {
            "producer": receipt["producer"],
            "result_id": receipt["result_id"],
            "source_receipt_digest": receipt["receipt_digest"],
            "canonical_request_digest": request_digest,
            "candidate_member_set_digest": digest_data(sorted(str(item["subject"]["subject_id"]) for item in members)),
        }
    verification_records = []
    for index, raw in enumerate(raw_verification, start=1):
        refs = _selection_set(raw["refs"], label="verification")
        stable_ids = sorted(str(subject_by_selection[key]["subject_id"]) for key in refs)
        evidence_digest = digest_data({"fixture": "context-attribution", "record": index})
        base = {
            "status": raw["status"],
            "evidence": {"ref": evidence_digest, "digest": evidence_digest, "kind": "digest"},
            "subject_ids": stable_ids,
            "claim_ids": [],
        }
        verification_records.append({
            "record_id": digest_data(base),
            **base,
            "subject_ids": sorted(local_by_stable[stable_id] for stable_id in stable_ids),
        })
    verification_records.sort(key=lambda item: item["record_id"])
    base = {
        "schema": "repoctl.task.discovery-completion-outcome",
        "schema_version": 1,
        "repository": receipt["repository"],
        "subjects": subject_table,
        "active_chosen": sorted(local_by_stable[str(subject_by_selection[key]["subject_id"])] for key in chosen),
        "episodes": [{
            "episode_id": episode_id,
            "query_digest": digest_data({"query": query}),
            "seed_result": seed_result,
            "citations": citations,
            "reviewed": sorted(local_by_stable[str(subject_by_selection[key]["subject_id"])] for key in reviewed),
            "excluded": [],
            "outside_candidate_set": [],
        }],
        "verification_records": verification_records,
    }
    return validate_completion_outcome({**base, "outcome_digest": digest_data(base)})


def _completion_stage_sets(
    outcome: dict[str, Any] | None,
    *,
    receipt: dict[str, Any],
    members: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str], set[str]]:
    if outcome is None:
        return set(), set(), set(), set()
    stable_by_local = {
        str(item["id"]): digest_data({
            "kind": item["kind"],
            "identity": item["identity"],
            "version_digest": item["version_digest"],
        })
        for item in outcome["subjects"]
    }
    member_by_id = {str(item["member_id"]): item for item in members}
    selected: set[str] = set()
    claim_to_subject: dict[str, str] = {}
    for episode in outcome["episodes"]:
        for citation in episode["citations"]:
            member = member_by_id.get(str(citation["member_id"]))
            stable_id = stable_by_local.get(str(citation["subject_id"]))
            if (
                member is None
                or stable_id != str(member["subject"]["subject_id"])
                or citation["producer"] != receipt["producer"]
                or citation["result_id"] != receipt["result_id"]
                or citation["source_receipt_digest"] != receipt["receipt_digest"]
                or citation["claims"] != member["claims"]
            ):
                continue
            selected.add(stable_id)
            claim_to_subject.update((str(claim["evidence_digest"]), stable_id) for claim in citation["claims"])
    reviewed = {
        stable_by_local[local_id]
        for episode in outcome["episodes"]
        for local_id in episode["reviewed"]
    }
    chosen = {stable_by_local[local_id] for local_id in outcome["active_chosen"]}
    verified: set[str] = set()
    for record in outcome["verification_records"]:
        if record["status"] != "passed":
            continue
        verified.update(stable_by_local[local_id] for local_id in record["subject_ids"])
        verified.update(claim_to_subject[claim_id] for claim_id in record["claim_ids"] if claim_id in claim_to_subject)
    return selected, reviewed, chosen, verified


def _later_reused_subjects(
    value: Any,
    *,
    completed_at: datetime | None,
    receipt: dict[str, Any],
    members: list[dict[str, Any]],
) -> set[str] | None:
    if value is None or completed_at is None:
        return None
    if not isinstance(value, list):
        raise ValueError("later result capture is invalid")
    reused: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"captured_at", "roles"}:
            raise ValueError("later result capture is invalid")
        if _timestamp(item["captured_at"], label="later result") <= completed_at:
            continue
        outcome = _build_completion_outcome(receipt, members, item["roles"])
        selected, _reviewed, _chosen, _verified = _completion_stage_sets(outcome, receipt=receipt, members=members)
        reused.update(selected)
    return reused


def _reuse_stage(
    subject_id: str,
    *,
    direct: set[str] | None,
    knowledge: set[str] | None,
) -> bool | str:
    if (direct is not None and subject_id in direct) or (knowledge is not None and subject_id in knowledge):
        return True
    if direct is not None and knowledge is not None:
        return False
    return "unknown"


def _knowledge_reused_subjects(
    value: Any,
    *,
    root: Path,
    completed_at: datetime | None,
    bundle: dict[str, Any],
    receipt: dict[str, Any],
    members: list[dict[str, Any]],
) -> set[str] | None:
    if value is None or completed_at is None:
        return None
    if not isinstance(value, list):
        raise ValueError("later Knowledge capture is invalid")
    source_versions = {
        str(item["source_ref"]["path"]): str(item["source_ref"].get("content_sha256") or "")
        for item in bundle.get("evidence", [])
        if isinstance(item, dict)
        and isinstance(item.get("source_ref"), dict)
        and isinstance(item["source_ref"].get("path"), str)
    }
    source_members = {
        str(item["ref"]): item
        for item in members
        if item.get("authority") == ResultAuthority.SOURCE.value
    }
    reused: set[str] = set()
    for item in value:
        expected = {"captured_at", "event", "query", "record"}
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("later Knowledge capture is invalid")
        captured_at = _timestamp(item["captured_at"], label="later Knowledge result")
        if captured_at <= completed_at:
            continue
        record = item.get("record")
        event = item.get("event")
        if not isinstance(record, dict) or not isinstance(event, dict):
            raise ValueError("later Knowledge artifacts are invalid")
        record_id = str(record.get("id") or "")
        event_id = str(event.get("id") or "")
        if KNOWLEDGE_RECORD_ID_RE.fullmatch(record_id) is None or KNOWLEDGE_EVENT_ID_RE.fullmatch(event_id) is None:
            raise ValueError("later Knowledge artifact identity is invalid")
        review = record.get("review") if isinstance(record.get("review"), dict) else {}
        if (
            _timestamp(review.get("reviewed_at"), label="Knowledge review") > captured_at
            or _timestamp(event.get("approved_at"), label="Knowledge approval") > captured_at
        ):
            raise ValueError("later Knowledge approval occurs after its result")
        source_refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
        valid_source_kinds = {kind.value for kind in KnowledgeSourceRefKind}
        if any(
            not isinstance(source_ref, dict)
            or str(source_ref.get("kind") or "") not in valid_source_kinds
            for source_ref in source_refs
        ):
            raise ValueError("later Knowledge source ref kind is invalid")
        source_resolutions = knowledge_source_ref_resolutions(root, record)
        if len(source_resolutions) != len(source_refs) or any(
            resolution.status not in {
                KnowledgeSourceResolutionStatus.CURRENT,
                KnowledgeSourceResolutionStatus.RELOCATED,
            }
            for resolution in source_resolutions
        ):
            raise ValueError("later Knowledge source refs are invalid")
        contract_problems = [
            *_knowledge_record_contract_problems(
                record,
                record_id=record_id,
                repo_id=str(receipt["repository"]["id"]),
            ),
            *_knowledge_approval_binding(record, [event]).problems,
        ]
        if any(problem.severity == "error" for problem in contract_problems):
            codes = ",".join(sorted({problem.code for problem in contract_problems if problem.severity == "error"}))
            raise ValueError(f"later Knowledge artifacts violate the canonical contract: {codes}")
        with TemporaryDirectory(prefix="repoctl-context-attribution-knowledge-") as temporary:
            knowledge_root = Path(temporary)
            records_dir = knowledge_root / "docs/knowledge/records"
            events_dir = knowledge_root / "docs/knowledge/events"
            record_path = (records_dir / f"{record_id}.json").resolve()
            event_path = (events_dir / f"{event_id}.json").resolve()
            if record_path.parent != records_dir.resolve() or event_path.parent != events_dir.resolve():
                raise ValueError("later Knowledge artifact path escapes temporary state")
            records_dir.mkdir(parents=True, exist_ok=True)
            events_dir.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            event_path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            projection, problems = rebuild_knowledge_projection(knowledge_root, repo_id=str(receipt["repository"]["id"]))
            if problems or record_id not in {
                str(head.get("record", {}).get("id") or "")
                for head in projection.get("heads", [])
                if isinstance(head, dict)
            }:
                raise ValueError("later Knowledge record is not an approved current head")
            target = RepoTarget(
                id=str(receipt["repository"]["id"]),
                root_path=knowledge_root / str(receipt["repository"]["path"]),
                display_path=str(receipt["repository"]["path"]),
                identity_source=RepositoryIdentitySource(str(receipt["repository"]["identity_source"])),
            )
            target.root_path.mkdir(parents=True, exist_ok=True)
            request = ContextResultRequest(query=str(item.get("query") or ""), mode="past_decision")
            result_id = digest_data({"captured_at": item["captured_at"], "query": request.query, "record_id": record_id})
            later_receipt = write_result_receipt(
                knowledge_root,
                target=target,
                producer=ResultProducer.CONTEXT,
                result_id=result_id,
                request=request,
                selections=[ResultSelection(ResultAuthority.KNOWLEDGE, record_id)],
            )
            later_members = result_member_capsules(knowledge_root, target=target, receipt=later_receipt)
            knowledge_selection = [{"authority": "knowledge", "ref": record_id}]
            later_outcome = _build_completion_outcome(
                later_receipt,
                later_members,
                {
                    "selected": knowledge_selection,
                    "reviewed": knowledge_selection,
                    "chosen": knowledge_selection,
                    "verification": [],
                    "version_overrides": [],
                },
            )
            selected, _reviewed, _chosen, _verified = _completion_stage_sets(
                later_outcome,
                receipt=later_receipt,
                members=later_members,
            )
            if not selected:
                raise ValueError("later Knowledge result has no exact selected citation")
        for source_ref, resolution in zip(source_refs, source_resolutions, strict=True):
            if not isinstance(source_ref, dict):
                continue
            path = str(source_ref.get("path") or "")
            member = source_members.get(path)
            if (
                source_ref.get("kind") != KnowledgeSourceRefKind.CURRENT_SOURCE.value
                or resolution.status is not KnowledgeSourceResolutionStatus.CURRENT
                or member is None
                or str(source_ref.get("content_sha256") or "") != resolution.actual_sha256
                or resolution.actual_sha256 != source_versions.get(path)
            ):
                continue
            reused.add(str(member["subject"]["subject_id"]))
    return reused


def _retrieval_evidence(bundle: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    groups = bundle.get("groups") if isinstance(bundle.get("groups"), dict) else {}
    lane_by_ref: dict[tuple[str, str], str] = {}
    for lane, items in groups.items():
        if not isinstance(items, list):
            continue
        for item in items:
            source_ref = item.get("source_ref") if isinstance(item, dict) and isinstance(item.get("source_ref"), dict) else {}
            path = str(source_ref.get("path") or "")
            kind = str(source_ref.get("kind") or "")
            authority = "document" if kind == "document" else "source"
            if path:
                lane_by_ref.setdefault((authority, path), str(lane))
    result: dict[tuple[str, str], dict[str, Any]] = {}
    evidence = bundle.get("evidence") if isinstance(bundle.get("evidence"), list) else []
    for rank, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            continue
        source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        ref = str(source_ref.get("path") or "")
        kind = str(source_ref.get("kind") or "")
        authority = "document" if kind == "document" else "source"
        if not ref:
            continue
        breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
        key = (authority, ref)
        contributions = {
            "graph": bool(item.get("graph_path") or float(breakdown.get("graph") or 0.0)),
            "knowledge": bool(item.get("related_record_ids") or float(breakdown.get("knowledge_path") or 0.0)),
        }
        if key in result:
            for contribution, observed in contributions.items():
                result[key]["typed_contributions"][contribution] |= observed
            continue
        result[key] = {
            "rank": rank,
            "lane": lane_by_ref.get(key, kind or authority),
            "score": item.get("score"),
            "score_breakdown": dict(sorted(breakdown.items())),
            "typed_contributions": contributions,
        }
    return result


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} timestamp is invalid")
    return parsed.astimezone(UTC)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(char in "0123456789abcdef" for char in value[7:])


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
    corpus, load_problems = _load_fixture_corpus(fixture)
    if load_problems:
        if any(problem.code == "context_benchmark_corpus_missing" for problem in load_problems):
            return {"present": False, "repositories": {}, "file_count": 0, "missing_count": 0, "digest_drift_count": 0}, []
        return {"present": True, "invalid": True, "file_count": 0, "missing_count": 0, "digest_drift_count": 0}, load_problems
    repositories = corpus["repositories"]

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


def materialize_context_benchmark_corpus(root: Path, *, fixture: Path, repo_id: str = "", force: bool = False) -> tuple[dict[str, Any], list[Problem]]:
    corpus, load_problems = _load_fixture_corpus(fixture)
    if load_problems:
        return {}, load_problems
    repositories = corpus["repositories"]
    wanted_repo_ids = [repo_id] if repo_id else sorted(str(key) for key in repositories)
    results: dict[str, Any] = {}
    problems: list[Problem] = []
    totals = {"created": 0, "unchanged": 0, "overwritten": 0, "conflict": 0, "file_count": 0}
    for wanted_repo_id in wanted_repo_ids:
        repo_corpus = repositories.get(wanted_repo_id)
        if not isinstance(repo_corpus, dict):
            continue
        target = require_repo_target(root, repo_id=wanted_repo_id or None)
        repo_result = {"created": [], "unchanged": [], "overwritten": [], "conflicts": []}
        files = repo_corpus.get("files") if isinstance(repo_corpus.get("files"), list) else []
        for item in files:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            content = str(item.get("content") or "")
            if not rel:
                continue
            totals["file_count"] += 1
            path = target.root_path / rel
            if path.exists() and path.read_text(encoding="utf-8") != content:
                if not force:
                    repo_result["conflicts"].append(rel)
                    totals["conflict"] += 1
                    problems.append(Problem("error", "context_benchmark_corpus_materialize_conflict", "fixture corpus would overwrite an existing file; pass --force to replace it", rel))
                    continue
                path.write_text(content, encoding="utf-8")
                repo_result["overwritten"].append(rel)
                totals["overwritten"] += 1
                continue
            if path.exists():
                repo_result["unchanged"].append(rel)
                totals["unchanged"] += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            repo_result["created"].append(rel)
            totals["created"] += 1
        results[wanted_repo_id] = repo_result
    return {
        "schema": "repoctl.context.benchmark.materialize",
        "schema_version": 1,
        "fixture": fixture.as_posix(),
        "repo_id": repo_id,
        "force": force,
        "repositories": results,
        "totals": totals,
    }, problems


def _load_fixture_corpus(fixture: Path) -> tuple[dict[str, Any], list[Problem]]:
    corpus_path = fixture / "corpus.json"
    if not corpus_path.is_file():
        return {}, [Problem("error", "context_benchmark_corpus_missing", "context benchmark corpus.json is missing", corpus_path.as_posix())]
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Problem("error", "context_benchmark_corpus_invalid_json", f"context benchmark corpus.json is invalid: {exc}", corpus_path.as_posix())]
    repositories = corpus.get("repositories") if isinstance(corpus, dict) else {}
    if not isinstance(repositories, dict):
        return {}, [Problem("error", "context_benchmark_corpus_invalid", "context benchmark corpus repositories must be an object", corpus_path.as_posix())]
    return {"repositories": repositories}, []


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
    if str(payload.get("command") or "") == "context.benchmark" and payload.get("ok") is False:
        problems.append(Problem("error", "context_benchmark_artifact_failed", f"{label} context benchmark artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context.benchmark" else payload
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


def _score_question(question: dict[str, Any], spec: dict[str, Any], bundle: ContextBundle | None, problems: list[Problem]) -> dict[str, Any]:
    labels = _source_labels(spec)
    required = labels["must_find"]
    required_knowledge = _refs(spec.get("required_knowledge_source_refs"))
    required_edges = _refs(spec.get("required_graph_edges"))
    optional = labels["acceptable"]
    supporting = labels["supporting"]
    forbidden = labels["noise"]
    evidence_refs = _bundle_refs(bundle)
    compact_payload = compact_context_bundle(bundle) if bundle is not None else {}
    visible_refs = _compact_source_refs(compact_payload)
    knowledge_refs = _knowledge_refs(bundle)
    knowledge_score_results = _knowledge_score_results(bundle)
    knowledge_source_statuses = _knowledge_source_statuses(bundle)
    problem_codes = [problem.code for problem in problems]
    stale_knowledge_excluded = problem_codes.count("knowledge_stale_record_excluded")
    superseded_knowledge_excluded = problem_codes.count("knowledge_superseded_record_excluded")
    deprecated_knowledge_excluded = problem_codes.count("knowledge_deprecated_record_excluded")

    top5 = evidence_refs[:5]
    top10 = evidence_refs[:10]
    knowledge_top5 = knowledge_refs[:5]
    visible_required = [ref for ref in required if _contains_ref(visible_refs, ref)]
    required_top5 = [ref for ref in required if _contains_ref(top5, ref)]
    required_top10 = [ref for ref in required if _contains_ref(top10, ref)]
    required_knowledge_top5 = [ref for ref in required_knowledge if _contains_ref(knowledge_top5, ref)]
    graph_edges = _bundle_graph_edges(bundle)
    required_edges_found = [edge for edge in required_edges if _contains_edge(graph_edges, edge)]
    selected_forbidden = [ref for ref in forbidden if _contains_ref(visible_refs, ref)]
    cross_repo_refs = _cross_repo_refs([*visible_refs, *knowledge_refs], expected_repo_id=str(question.get("repo_id") or ""))
    precision_top5 = [ref for ref in top5 if not _matches_any_expected(ref, supporting)]
    relevant_top5 = sum(1 for ref in precision_top5 if _matches_any_expected(ref, [*required, *optional]))
    supporting_top5 = [ref for ref in top5 if _matches_any_expected(ref, supporting)]
    first_correct_rank = next((index for index, ref in enumerate(evidence_refs, start=1) if _matches_any_expected(ref, required)), 0)
    generated_noise = [ref for ref in _dedupe_refs(visible_refs) if _is_generated_or_ignored_ref(ref) and not _matches_any_expected(ref, supporting)]
    verification = _verification_hint_metrics(spec, visible_refs)
    integrity_failures = [ref for ref in evidence_refs if not str(ref.get("content_sha256") or "").startswith("sha256:")]
    knowledge_integrity_failures = [ref for ref in knowledge_refs if not str(ref.get("content_sha256") or "").startswith("sha256:")]
    compact_bytes = len(json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    return {
        "id": str(question.get("id") or ""),
        "category": str(question.get("category") or ""),
        "repo_id": str(question.get("repo_id") or ""),
        "query": str(question.get("question") or ""),
        "mode": str(bundle.query.get("mode") or "") if bundle is not None else str(question.get("mode") or "auto"),
        "metrics": {
            "recall_at_5": _ratio(len(required_top5), len(required)),
            "recall_at_10": _ratio(len(required_top10), len(required)),
            "precision_at_5": _ratio(relevant_top5, len(precision_top5)),
            "first_correct_rank": first_correct_rank,
            "must_find_count": len(required),
            "supporting_hit_at_5": _ratio(len(supporting_top5), len(supporting)) if supporting else 0.0,
            "supporting_expected_count": len(supporting),
            "noise_selected": len(selected_forbidden),
            "generated_or_ignored_noise": len(generated_noise),
            "verification_hint_accuracy": verification["accuracy"],
            "verification_hint_expected_count": verification["expected_count"],
            "output_estimated_tokens": (compact_bytes + 3) // 4,
            "output_serialized_bytes": compact_bytes,
            "output_evidence_count": len(evidence_refs),
            "output_visible_count": len(visible_refs),
            "visible_recall": _ratio(len(visible_required), len(required)),
            "source_ref_integrity": len(integrity_failures) == 0,
            "knowledge_source_ref_integrity": len(knowledge_integrity_failures) == 0,
            "forbidden_selected": len(selected_forbidden),
            "cross_repo_ref_count": len(cross_repo_refs),
            "visible_required_found": len(visible_required),
            "knowledge_recall_at_5": _ratio(len(required_knowledge_top5), len(required_knowledge)) if required_knowledge else 0.0,
            "graph_edge_recall": _ratio(len(required_edges_found), len(required_edges)) if required_edges else 1.0,
            "required_graph_edge_count": len(required_edges),
            "required_knowledge_count": len(required_knowledge),
            "knowledge_result_count": len(knowledge_refs),
            "knowledge_score_breakdown_present": all(result["has_field_breakdown"] for result in knowledge_score_results),
            "knowledge_source_status_current": stale_knowledge_excluded == 0 and all(status.get("digest_matches") is True for status in knowledge_source_statuses),
            "knowledge_stale_record_excluded": stale_knowledge_excluded,
            "knowledge_superseded_record_excluded": superseded_knowledge_excluded,
            "knowledge_deprecated_record_excluded": deprecated_knowledge_excluded,
        },
        "required_found_at_5": required_top5,
        "required_found_at_10": required_top10,
        "missing_required_at_10": [ref for ref in required if not _contains_ref(top10, ref)],
        "visible_required_found_refs": visible_required,
        "missing_required_from_visible": [ref for ref in required if not _contains_ref(visible_refs, ref)],
        "required_knowledge_found_at_5": required_knowledge_top5,
        "missing_required_knowledge_at_5": [ref for ref in required_knowledge if not _contains_ref(knowledge_top5, ref)],
        "required_graph_edges_found": required_edges_found,
        "missing_required_graph_edges": [edge for edge in required_edges if not _contains_edge(graph_edges, edge)],
        "selected_forbidden": selected_forbidden,
        "selected_noise": selected_forbidden,
        "generated_or_ignored_noise": generated_noise,
        "supporting_found_at_5": supporting_top5,
        "verification_hints": verification,
        "cross_repo_refs": cross_repo_refs,
        "top_refs": top5,
        "top_knowledge_refs": knowledge_top5,
        "knowledge_score_results": knowledge_score_results,
        "knowledge_source_statuses": knowledge_source_statuses,
        "problem_codes": problem_codes,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [result["metrics"] for result in results]
    ranked = [metric for metric in metrics if metric["must_find_count"]]
    found_ranks = [metric["first_correct_rank"] for metric in ranked if metric["first_correct_rank"]]
    verification_metrics = [metric for metric in metrics if metric["verification_hint_expected_count"]]
    return {
        "mean_recall_at_5": _mean(metric["recall_at_5"] for metric in metrics),
        "mean_recall_at_10": _mean(metric["recall_at_10"] for metric in metrics),
        "mean_precision_at_5": _mean(metric["precision_at_5"] for metric in metrics),
        "first_correct_found_rate": _ratio(len(found_ranks), len(ranked)),
        "mean_first_correct_rank": _mean(found_ranks),
        "mean_supporting_hit_at_5": _mean(metric["supporting_hit_at_5"] for metric in metrics if metric["supporting_expected_count"]),
        "noise_selected": sum(int(metric["noise_selected"]) for metric in metrics),
        "generated_or_ignored_noise": sum(int(metric["generated_or_ignored_noise"]) for metric in metrics),
        "mean_verification_hint_accuracy": _mean(metric["verification_hint_accuracy"] for metric in verification_metrics),
        "verification_hint_expected_questions": len(verification_metrics),
        "mean_output_estimated_tokens": _mean(metric["output_estimated_tokens"] for metric in metrics),
        "max_output_estimated_tokens": max((int(metric["output_estimated_tokens"]) for metric in metrics), default=0),
        "mean_output_serialized_bytes": _mean(metric["output_serialized_bytes"] for metric in metrics),
        "max_output_serialized_bytes": max((int(metric["output_serialized_bytes"]) for metric in metrics), default=0),
        "mean_output_evidence_count": _mean(metric["output_evidence_count"] for metric in metrics),
        "mean_output_visible_count": _mean(metric["output_visible_count"] for metric in metrics),
        "mean_visible_recall": _mean(metric["visible_recall"] for metric in metrics),
        "source_ref_integrity": all(metric["source_ref_integrity"] for metric in metrics),
        "knowledge_source_ref_integrity": all(metric["knowledge_source_ref_integrity"] for metric in metrics),
        "mean_knowledge_recall_at_5": _mean(metric["knowledge_recall_at_5"] for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_expected_questions": sum(1 for metric in metrics if metric["required_knowledge_count"]),
        "knowledge_result_questions": sum(1 for metric in metrics if metric["knowledge_result_count"]),
        "knowledge_score_breakdown_integrity": all(metric["knowledge_score_breakdown_present"] for metric in metrics),
        "knowledge_source_status_current": all(metric["knowledge_source_status_current"] for metric in metrics),
        "knowledge_stale_record_excluded": sum(int(metric["knowledge_stale_record_excluded"]) for metric in metrics),
        "knowledge_superseded_record_excluded": sum(int(metric["knowledge_superseded_record_excluded"]) for metric in metrics),
        "knowledge_deprecated_record_excluded": sum(int(metric["knowledge_deprecated_record_excluded"]) for metric in metrics),
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
        ranked = [metric for metric in items if metric.get("must_find_count", 0)]
        found_ranks = [int(metric.get("first_correct_rank") or 0) for metric in ranked if metric.get("first_correct_rank")]
        verification_metrics = [metric for metric in items if metric.get("verification_hint_expected_count", 0)]
        summary[category] = {
            "question_count": len(items),
            "mean_recall_at_5": _mean(metric.get("recall_at_5", 0.0) for metric in items),
            "mean_recall_at_10": _mean(metric.get("recall_at_10", 0.0) for metric in items),
            "mean_precision_at_5": _mean(metric.get("precision_at_5", 0.0) for metric in items),
            "first_correct_found_rate": _ratio(len(found_ranks), len(ranked)),
            "mean_first_correct_rank": _mean(found_ranks),
            "mean_supporting_hit_at_5": _mean(metric.get("supporting_hit_at_5", 0.0) for metric in items if metric.get("supporting_expected_count", 0)),
            "noise_selected": sum(int(metric.get("noise_selected") or 0) for metric in items),
            "generated_or_ignored_noise": sum(int(metric.get("generated_or_ignored_noise") or 0) for metric in items),
            "mean_verification_hint_accuracy": _mean(metric.get("verification_hint_accuracy", 0.0) for metric in verification_metrics),
            "mean_output_estimated_tokens": _mean(metric.get("output_estimated_tokens", 0) for metric in items),
            "max_output_estimated_tokens": max((int(metric.get("output_estimated_tokens") or 0) for metric in items), default=0),
            "mean_visible_recall": _mean(metric.get("visible_recall", 0.0) for metric in items),
            "mean_knowledge_recall_at_5": _mean(metric.get("knowledge_recall_at_5", 0.0) for metric in items if metric.get("required_knowledge_count", 0)),
            "knowledge_expected_questions": sum(1 for metric in items if metric.get("required_knowledge_count", 0)),
            "mean_graph_edge_recall": _mean(metric.get("graph_edge_recall", 1.0) for metric in items if metric.get("required_graph_edge_count", 0)),
            "graph_edge_expected_questions": sum(1 for metric in items if metric.get("required_graph_edge_count", 0)),
            "forbidden_selected": sum(int(metric.get("forbidden_selected") or 0) for metric in items),
            "cross_repo_ref_count": sum(int(metric.get("cross_repo_ref_count") or 0) for metric in items),
            "knowledge_stale_record_excluded": sum(int(metric.get("knowledge_stale_record_excluded") or 0) for metric in items),
            "knowledge_superseded_record_excluded": sum(int(metric.get("knowledge_superseded_record_excluded") or 0) for metric in items),
            "knowledge_deprecated_record_excluded": sum(int(metric.get("knowledge_deprecated_record_excluded") or 0) for metric in items),
        }
    return summary


def _bundle_refs(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    return [candidate.source_ref.to_dict() for candidate in bundle.evidence]


def _compact_source_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    refs: list[dict[str, Any]] = []
    for items in groups.values():
        if not isinstance(items, list):
            continue
        for item in items:
            ref = item.get("source_ref") if isinstance(item, dict) and isinstance(item.get("source_ref"), dict) else None
            if ref is not None:
                refs.append(ref)
    return _dedupe_refs(refs)


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


def _source_labels(spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    labels = {name: _refs(spec.get(name)) for name in ("must_find", "acceptable", "supporting", "noise")}
    if any(name in spec for name in labels):
        return labels
    raw_labels = spec.get("source_labels")
    if isinstance(raw_labels, list):
        parsed = {name: [] for name in labels}
        for item in raw_labels:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            if label not in parsed:
                continue
            parsed[label].append({key: value for key, value in item.items() if key != "label"})
        return parsed
    return {
        "must_find": _refs(spec.get("required_source_refs")),
        "acceptable": _refs(spec.get("acceptable_optional_refs")),
        "supporting": _refs(spec.get("supporting_source_refs")),
        "noise": _refs(spec.get("forbidden_refs")),
    }


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (str(ref.get("path") or ""), str(ref.get("section") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _repo_path_from_ref(ref: dict[str, Any]) -> str:
    path = str(ref.get("path") or "")
    if path.startswith("<graph:repo:") and path.endswith(">"):
        body = path[len("<graph:repo:") : -1]
        if ":file:" in body:
            return unquote(body.split(":file:", 1)[1])
        if ":symbol:" in body:
            symbol_id = unquote(body.split(":symbol:", 1)[1])
            parts = symbol_id.split(":", 2)
            return parts[1] if len(parts) > 1 else ""
        return ""
    if path.startswith("<"):
        return ""
    return path.removeprefix("repos/")


def _is_generated_or_ignored_ref(ref: dict[str, Any]) -> bool:
    path = _repo_path_from_ref(ref)
    return bool(path and any(fnmatch.fnmatch(path, pattern) for pattern in IGNORED_SOURCE_PATTERNS))


def _verification_hint_metrics(spec: dict[str, Any], visible_refs: list[dict[str, Any]]) -> dict[str, Any]:
    raw_expected = spec.get("expected_verification_hints")
    if not isinstance(raw_expected, list):
        return {"accuracy": 0.0, "expected_count": 0, "selected_count": 0, "found": [], "missing": [], "unexpected": []}
    expected = [item for item in raw_expected if isinstance(item, dict) and str(item.get("command_or_capability") or "").strip()]
    positive = [item for item in expected if str(item.get("status") or "acceptable") in {"must_find", "acceptable"}]
    selected = [ref for ref in visible_refs if str(ref.get("kind") or "") == "verification_hint"]

    def matches(ref: dict[str, Any], item: dict[str, Any]) -> bool:
        needle = str(item.get("command_or_capability") or "").casefold()
        haystack = f"{ref.get('path', '')} {ref.get('section', '')}".casefold()
        return bool(needle and needle in haystack)

    found = [item for item in positive if any(matches(ref, item) for ref in selected)]
    unexpected = [ref for ref in selected if not any(matches(ref, item) for item in positive)]
    missing = [item for item in positive if item not in found]
    denominator = len(found) + len(unexpected) + len(missing)
    accuracy = _ratio(len(found), denominator) if denominator else 1.0
    return {
        "accuracy": accuracy,
        "expected_count": len(positive),
        "selected_count": len(selected),
        "found": found,
        "missing": missing,
        "unexpected": unexpected,
    }


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


def _bundle_graph_edges(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for candidate in bundle.evidence:
        for relation in candidate.graph_path:
            if not isinstance(relation, dict):
                continue
            edge = {
                "kind": str(relation.get("edge") or ""),
                "from": str(relation.get("from_id") or ""),
                "to": str(relation.get("to_id") or ""),
                "assertion": str(relation.get("assertion") or ""),
                "source": str(relation.get("provider") or ""),
            }
            key = (edge["kind"], edge["from"], edge["to"], edge["assertion"], edge["source"])
            if not all(key[:3]) or key in seen:
                continue
            seen.add(key)
            edges.append(edge)
    return edges


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
        path = str(ref.get("path") or "")
        ref_repo_id = str(ref.get("repo_id") or "") or _repo_id_from_graph_ref(path) or _repo_id_from_source_ref(path, expected_repo_id=expected_repo_id)
        if ref_repo_id and ref_repo_id != expected_repo_id:
            leaked.append(ref)
    return leaked


def _repo_id_from_graph_ref(path: str) -> str:
    if not path.startswith("<graph:repo:") or not path.endswith(">"):
        return ""
    rest = path[len("<graph:repo:") : -1]
    encoded_repo_id = rest.split(":", 1)[0]
    return unquote(encoded_repo_id)


def _repo_id_from_source_ref(path: str, *, expected_repo_id: str) -> str:
    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == "repos" and expected_repo_id != "main":
        return parts[1]
    return ""


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
    min_category_knowledge_recall_at_5: dict[str, float],
    min_category_graph_edge_recall: dict[str, float],
    min_category_visible_recall: dict[str, float],
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
    for category, threshold in sorted(min_category_knowledge_recall_at_5.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_knowledge_recall_at_5") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_knowledge_gate_failed", f"context benchmark {category} knowledge Recall@5 is below gate", category))
    for category, threshold in sorted(min_category_graph_edge_recall.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_graph_edge_recall") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_graph_edge_gate_failed", f"context benchmark {category} graph edge recall is below gate", category))
    for category, threshold in sorted(min_category_visible_recall.items()):
        category_summary = by_category.get(category) if isinstance(by_category.get(category), dict) else {}
        recall = float(category_summary.get("mean_visible_recall") or 0.0)
        if recall < threshold:
            problems.append(Problem("error", "context_benchmark_category_visible_recall_gate_failed", f"context benchmark {category} visible recall is below gate", category))
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
