from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .graph_model import digest_data
from .io import RepoctlError
from .repositories import REPO_ID_RE, RepoTarget, RepositoryIdentitySource
from .result_receipts import ResultAuthority, ResultProducer, ResultSelection


DISCOVERY_OUTCOME_SCHEMA = "repoctl.task.discovery-outcome"
DISCOVERY_OUTCOME_SCHEMA_VERSION = 1
VERIFICATION_STATUSES = frozenset({"passed", "failed", "mixed", "blocked"})
HOT_SUBJECT_KINDS = frozenset({"file"})
_WORKSPACE_VERIFICATION_QUERY = "repoctl workspace verification artifacts"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_TASK_ID_RE = re.compile(r"T-[0-9]{14}Z")


def outcome_state_path(root: Path, task_id: str) -> Path:
    if _TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError("discovery outcome task identity is invalid")
    return root / "docs/tasks/.repoctl-state/discovery-outcomes" / f"{task_id}.json"


def load_outcome_state(root: Path, task_id: str) -> dict[str, Any] | None:
    path = outcome_state_path(root, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepoctlError(
            "task Discovery outcome state is unreadable",
            code="discovery_outcome_state_invalid",
            path=path.relative_to(root).as_posix(),
        ) from exc
    try:
        return _validate_state(data, task_id=task_id)
    except ValueError as exc:
        raise RepoctlError(
            str(exc),
            code="discovery_outcome_state_invalid",
            path=path.relative_to(root).as_posix(),
        ) from exc


def serialize_outcome_state(data: Mapping[str, Any]) -> str:
    validated = _validate_state(dict(data), task_id=str(data.get("task_id") or ""))
    return json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def update_outcome_state(
    root: Path,
    *,
    task_id: str,
    target: RepoTarget | None,
    query: str,
    episode_id: str,
    starts_new_episode: bool,
    reviewed_paths: Iterable[str],
    excluded_paths: Iterable[str],
    chosen_paths: Iterable[str],
    result_receipt: Mapping[str, Any] | None = None,
    result_selections: Iterable[ResultSelection] = (),
) -> dict[str, Any]:
    previous = load_outcome_state(root, task_id)
    repository = target.to_dict() if target is not None else None
    if previous is not None and previous.get("repository") != repository:
        raise RepoctlError(
            "task Discovery outcome repository identity changed",
            code="discovery_outcome_repository_mismatch",
            path=outcome_state_path(root, task_id).relative_to(root).as_posix(),
        )
    state = previous or _with_state_digest({
        "schema": DISCOVERY_OUTCOME_SCHEMA,
        "schema_version": DISCOVERY_OUTCOME_SCHEMA_VERSION,
        "task_id": task_id,
        "repository": repository,
        "active_chosen": [],
        "prior_episodes": [],
        "active_episode": None,
        "verification_records": [],
    })
    active = _copy(state.get("active_episode"))
    if starts_new_episode or active is None:
        if active is not None and _episode_has_evidence(active):
            state["prior_episodes"] = [*state["prior_episodes"], _seal_episode(active)]
        active = _empty_episode(episode_id=episode_id or _query_episode_id(query), query=query)
    elif episode_id and active["episode_id"] != episode_id:
        # A Context result can adopt an exact-query Graph-only episode.  The
        # task layer has already proven that ownership transition and rewrites
        # every flat citation to the adopted episode identity.
        active["episode_id"] = episode_id
        for citation in active["citations"]:
            citation["episode_id"] = episode_id

    chosen_subjects = _canonical_path_subjects(root, target=target, paths=chosen_paths)
    reviewed_subjects = _canonical_path_subjects(root, target=target, paths=reviewed_paths)
    excluded_subjects = _canonical_path_subjects(root, target=target, paths=excluded_paths)
    reviewed_by_key = {_subject_identity_key(item): item for item in reviewed_subjects}
    excluded_by_key = {_subject_identity_key(item): item for item in excluded_subjects}
    missing_review = sorted(set(excluded_by_key) - set(reviewed_by_key))
    if missing_review:
        raise RepoctlError(
            "excluded Discovery subjects must also be explicitly reviewed in the active episode",
            code="discovery_excluded_not_reviewed",
            path=missing_review[0],
        )
    chosen_keys = {_subject_identity_key(item) for item in chosen_subjects}
    overlap = sorted(chosen_keys & set(excluded_by_key))
    if overlap:
        raise RepoctlError(
            "excluded Discovery subjects and active Chosen scope must be disjoint",
            code="discovery_excluded_chosen_conflict",
            path=overlap[0],
        )

    state["active_chosen"] = _subjects_by_identity(chosen_subjects)
    active["reviewed"] = _subjects_by_identity([*active["reviewed"], *reviewed_subjects])
    active["excluded"] = _subjects_by_identity([*active["excluded"], *excluded_subjects])

    selections = list(result_selections)
    if result_receipt is not None and selections:
        capsules = result_member_capsules(root, target=target, receipt=result_receipt)
        capsule_by_selection = {
            (str(item["authority"]), str(item["ref"])): item
            for item in capsules
        }
        citations = []
        for selection in selections:
            capsule = capsule_by_selection.get((selection.authority.value, selection.ref))
            if capsule is None:
                raise RepoctlError(
                    "selected result member has no canonical capsule",
                    code="discovery_result_member_missing",
                    path=selection.ref,
                )
            citations.append(
                {
                    "producer": str(result_receipt["producer"]),
                    "result_id": str(result_receipt["result_id"]),
                    "episode_id": active["episode_id"],
                    "canonical_request_digest": digest_data(result_receipt["request"]),
                    "member_id": capsule["member_id"],
                    "source_receipt_digest": str(result_receipt["receipt_digest"]),
                    "member": capsule,
                }
            )
        active["citations"] = _dedupe_dicts([*active["citations"], *citations], key="member_id")
        if active["seed_result"] is None:
            candidate_keys = sorted({_subject_identity_key(item["subject"]) for item in capsules})
            active["seed_result"] = {
                "producer": str(result_receipt["producer"]),
                "result_id": str(result_receipt["result_id"]),
                "source_receipt_digest": str(result_receipt["receipt_digest"]),
                "canonical_request_digest": digest_data(result_receipt["request"]),
                "candidate_member_set_digest": digest_data(candidate_keys),
                "candidate_subject_keys": candidate_keys,
            }

    candidate_keys = set((active.get("seed_result") or {}).get("candidate_subject_keys") or [])
    active["outside_candidate_set"] = sorted(
        {
            *active["outside_candidate_set"],
            *(
                _subject_identity_key(subject)
                for subject in active["reviewed"]
                if active.get("seed_result") is not None
                and _subject_identity_key(subject) not in candidate_keys
            ),
        }
    )
    state["active_episode"] = active
    return _with_state_digest(state)


def add_verification_record(
    root: Path,
    *,
    task_id: str,
    target: RepoTarget | None = None,
    status: str,
    evidence_ref: str,
    subject_refs: Iterable[str],
    claim_ids: Iterable[str] = (),
) -> dict[str, Any]:
    state = load_outcome_state(root, task_id)
    if state is None:
        raise RepoctlError(
            "structured verification requires recorded Discovery outcome state",
            code="discovery_outcome_missing",
        )
    return _add_verification_record_to_state(
        root,
        state=state,
        status=status,
        evidence_ref=evidence_ref,
        subject_refs=subject_refs,
        claim_ids=claim_ids,
        target=target,
    )


def add_workspace_artifact_verification_record(
    root: Path,
    *,
    task_id: str,
    status: str,
    evidence_ref: str,
    artifact_refs: Iterable[str],
    subject_refs: Iterable[str] = (),
    claim_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Record root-task verification against typed, non-product artifacts."""

    artifacts = [
        _workspace_artifact_subject(root, str(value))
        for value in artifact_refs
    ]
    if not artifacts:
        raise RepoctlError(
            "workspace artifact verification requires at least one artifact",
            code="verification_coverage_missing",
        )
    state = load_outcome_state(root, task_id)
    if state is not None and state.get("repository") is not None:
        raise RepoctlError(
            "workspace verification artifacts cannot be attached to a product repository outcome",
            code="workspace_verification_artifact_invalid",
        )
    state = state or _with_state_digest({
        "schema": DISCOVERY_OUTCOME_SCHEMA,
        "schema_version": DISCOVERY_OUTCOME_SCHEMA_VERSION,
        "task_id": task_id,
        "repository": None,
        "active_chosen": [],
        "prior_episodes": [],
        "active_episode": None,
        "verification_records": [],
    })
    active = _copy(state.get("active_episode"))
    episode_id = digest_data({"kind": "workspace_verification_episode", "task_id": task_id})
    if active is None or active.get("episode_id") != episode_id:
        if active is not None and _episode_has_evidence(active):
            state["prior_episodes"] = [*state["prior_episodes"], _seal_episode(active)]
        active = _empty_episode(
            episode_id=episode_id,
            query=_WORKSPACE_VERIFICATION_QUERY,
        )
    active["reviewed"] = _subjects_by_identity([*active["reviewed"], *artifacts])
    state["active_episode"] = active
    return _add_verification_record_to_state(
        root,
        state=state,
        status=status,
        evidence_ref=evidence_ref,
        subject_refs=[*subject_refs, *(str(item["subject_id"]) for item in artifacts)],
        claim_ids=claim_ids,
        target=None,
    )


def _add_verification_record_to_state(
    root: Path,
    *,
    state: dict[str, Any],
    status: str,
    evidence_ref: str,
    subject_refs: Iterable[str],
    claim_ids: Iterable[str],
    target: RepoTarget | None,
) -> dict[str, Any]:
    if status not in VERIFICATION_STATUSES:
        raise RepoctlError("verification status is invalid", code="verification_status_invalid")
    evidence = _canonical_evidence(root, evidence_ref)
    available_subjects = _state_subjects(state)
    by_ref: dict[str, dict[str, Any]] = {}
    for subject in available_subjects:
        by_ref[str(subject["subject_id"])] = subject
        by_ref[str(subject["key"])] = subject
        identity = subject.get("identity")
        if isinstance(identity, dict) and isinstance(identity.get("path"), str):
            by_ref[str(identity["path"])] = subject
            repository = state.get("repository")
            if isinstance(repository, dict):
                prefix = str(repository.get("path") or "").rstrip("/")
                if prefix:
                    by_ref[f"{prefix}/{identity['path']}"] = subject
    selected: list[dict[str, Any]] = []
    for raw in subject_refs:
        value = str(raw).strip()
        subject = by_ref.get(value)
        if subject is None:
            raise RepoctlError(
                "verification subject is not part of the task Discovery outcome",
                code="verification_subject_unknown",
                path=value,
            )
        selected.append(subject)
    selected = _bind_current_verification_files(
        root,
        state,
        selected,
        target=target,
    )
    canonical_claims = sorted(set(str(value) for value in claim_ids if _DIGEST_RE.fullmatch(str(value))))
    if len(canonical_claims) != len(set(str(value) for value in claim_ids)):
        raise RepoctlError("verification claim IDs must be sha256 digests", code="verification_claim_invalid")
    if not selected and not canonical_claims:
        raise RepoctlError(
            "structured verification must cover at least one subject or claim",
            code="verification_coverage_missing",
        )
    record_base = {
        "status": status,
        "evidence": evidence,
        "subject_ids": sorted({str(item["subject_id"]) for item in selected}),
        "claim_ids": canonical_claims,
    }
    record = {"record_id": digest_data(record_base), **record_base}
    state["verification_records"] = _dedupe_dicts(
        [*state["verification_records"], record],
        key="record_id",
    )
    return _with_state_digest(state)


def structured_verification_coverage(
    root: Path,
    *,
    task_id: str,
    target: RepoTarget,
    subject_refs: Iterable[str],
) -> dict[str, Any]:
    """Report exact-current passed coverage for selected Discovery file subjects."""

    requested = sorted({
        relative
        for value in subject_refs
        if (relative := _relative_repo_path(target, str(value))) is not None
    })
    state = load_outcome_state(root, task_id)
    if state is None:
        return {
            "status": "outcome_missing" if requested else "not_applicable",
            "required_subjects": requested,
            "passed_subjects": [],
            "missing_subjects": requested,
            "nonpassing_subjects": [],
        }
    chosen_paths = {
        str(identity["path"])
        for subject in state["active_chosen"]
        if subject.get("kind") == "file"
        and isinstance((identity := subject.get("identity")), dict)
        and isinstance(identity.get("path"), str)
    }
    required = [path for path in requested if path in chosen_paths]
    statuses_by_subject: dict[str, set[str]] = {}
    for record in state["verification_records"]:
        for subject_id in record["subject_ids"]:
            statuses_by_subject.setdefault(str(subject_id), set()).add(str(record["status"]))
    passed: list[str] = []
    missing: list[str] = []
    nonpassing: list[str] = []
    for path in required:
        current = current_path_subject(root, target=target, path=path)
        statuses = statuses_by_subject.get(str(current["subject_id"]), set())
        if statuses == {"passed"}:
            passed.append(path)
        elif not statuses:
            missing.append(path)
        else:
            nonpassing.append(path)
    status = (
        "not_applicable"
        if not required
        else "missing"
        if missing
        else "nonpassing"
        if nonpassing
        else "complete"
    )
    return {
        "status": status,
        "required_subjects": required,
        "passed_subjects": passed,
        "missing_subjects": missing,
        "nonpassing_subjects": nonpassing,
    }


def completion_outcome_projection(root: Path, task_id: str) -> dict[str, Any] | None:
    state = load_outcome_state(root, task_id)
    if state is None:
        return None
    episodes = [*state["prior_episodes"]]
    if state.get("active_episode") is not None and _episode_has_evidence(state["active_episode"]):
        episodes.append(_seal_episode(state["active_episode"]))
    all_subjects = _state_subjects(state)
    subject_by_stable_id = {str(item["subject_id"]): item for item in all_subjects}
    ordered_subjects = sorted(
        subject_by_stable_id.values(),
        key=lambda item: (str(item["kind"]), _canonical_json(item["identity"]), str(item["version_digest"])),
    )
    local_id_by_stable = {
        str(subject["subject_id"]): f"s{index}"
        for index, subject in enumerate(ordered_subjects, start=1)
    }
    subject_table = [
        {
            "id": local_id_by_stable[str(subject["subject_id"])],
            "kind": subject["kind"],
            "identity": subject["identity"],
            "key": subject["key"],
            "version_digest": subject["version_digest"],
        }
        for subject in ordered_subjects
    ]
    projected_episodes: list[dict[str, Any]] = []
    for episode in episodes:
        seed = _copy(episode.get("seed_result"))
        if isinstance(seed, dict):
            seed.pop("candidate_subject_keys", None)
        projected_episodes.append(
            {
                "episode_id": episode["episode_id"],
                "query_digest": digest_data({"query": episode["query"]}),
                "seed_result": seed,
                "citations": [
                    {
                        key: citation[key]
                        for key in (
                            "producer",
                            "result_id",
                            "episode_id",
                            "canonical_request_digest",
                            "member_id",
                            "source_receipt_digest",
                        )
                    }
                    | {
                        "subject_id": local_id_by_stable[str(citation["member"]["subject"]["subject_id"])],
                        "claims": citation["member"]["claims"],
                    }
                    for citation in episode["citations"]
                ],
                "reviewed": sorted(
                    local_id_by_stable[str(subject["subject_id"])]
                    for subject in episode["reviewed"]
                ),
                "excluded": sorted(
                    local_id_by_stable[str(subject["subject_id"])]
                    for subject in episode["excluded"]
                ),
                "outside_candidate_set": sorted(
                    local_id_by_stable[str(subject["subject_id"])]
                    for subject in episode["reviewed"]
                    if _subject_identity_key(subject) in episode["outside_candidate_set"]
                ),
            }
        )
    verification_records = [
        {
            **record,
            "subject_ids": sorted(
                local_id_by_stable[subject_id]
                for subject_id in record["subject_ids"]
                if subject_id in local_id_by_stable
            ),
        }
        for record in state["verification_records"]
    ]
    projection_base = {
        "schema": "repoctl.task.discovery-completion-outcome",
        "schema_version": 1,
        "repository": state["repository"],
        "subjects": subject_table,
        "active_chosen": sorted(
            local_id_by_stable[str(subject["subject_id"])]
            for subject in state["active_chosen"]
        ),
        "episodes": projected_episodes,
        "verification_records": verification_records,
    }
    return {**projection_base, "outcome_digest": digest_data(projection_base)}


def _bind_current_verification_files(
    root: Path,
    state: dict[str, Any],
    selected: list[dict[str, Any]],
    *,
    target: RepoTarget | None,
) -> list[dict[str, Any]]:
    """Capture the file version actually covered by explicit verification."""

    if target is None:
        return selected
    episodes = [*state["prior_episodes"]]
    if state.get("active_episode") is not None:
        episodes.append(state["active_episode"])
    role_subjects = [*state["active_chosen"]]
    for episode in episodes:
        role_subjects.extend(episode["reviewed"])
    role_keys = {str(subject["key"]) for subject in role_subjects}

    current_by_key: dict[str, dict[str, Any]] = {}
    rebound: list[dict[str, Any]] = []
    for subject in selected:
        key = str(subject["key"])
        if subject.get("kind") != "file" or key not in role_keys:
            rebound.append(subject)
            continue
        identity = subject.get("identity")
        path = identity.get("path") if isinstance(identity, Mapping) else None
        if not isinstance(path, str):
            rebound.append(subject)
            continue
        current = current_path_subject(
            root,
            target=target,
            path=path,
        )
        current_by_key[key] = current
        rebound.append(current)
    if not current_by_key:
        return selected

    state["active_chosen"] = _subjects_by_identity(
        current_by_key.get(str(subject["key"]), subject)
        for subject in state["active_chosen"]
    )
    for episode in episodes:
        reviewed_keys = {str(subject["key"]) for subject in episode["reviewed"]}
        outside_ids = set(episode["outside_candidate_set"])
        outside_keys = {
            str(subject["key"])
            for subject in episode["reviewed"]
            if str(subject["subject_id"]) in outside_ids
        }
        additions = [
            current
            for key, current in current_by_key.items()
            if key in reviewed_keys
        ]
        episode["reviewed"] = _subjects_by_identity([*episode["reviewed"], *additions])
        episode["outside_candidate_set"] = sorted({
            *outside_ids,
            *(
                str(current["subject_id"])
                for key, current in current_by_key.items()
                if key in outside_keys
            ),
        })
    return rebound


def validate_completion_outcome(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("completion Discovery outcome must be an object")
    expected = {
        "schema",
        "schema_version",
        "repository",
        "subjects",
        "active_chosen",
        "episodes",
        "verification_records",
        "outcome_digest",
    }
    if (
        set(value) != expected
        or value.get("schema") != "repoctl.task.discovery-completion-outcome"
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("completion Discovery outcome schema is invalid")
    basis = {key: value[key] for key in value if key != "outcome_digest"}
    if value.get("outcome_digest") != digest_data(basis):
        raise ValueError("completion Discovery outcome digest does not match")
    repository = value.get("repository")
    if repository is not None and (
        not isinstance(repository, dict)
        or set(repository) != {"id", "path", "identity_source"}
        or not isinstance(repository.get("id"), str)
        or REPO_ID_RE.fullmatch(repository["id"]) is None
        or not _canonical_completion_ref(repository.get("path"), relative=True)
        or repository.get("identity_source")
        not in {item.value for item in RepositoryIdentitySource}
    ):
        raise ValueError("completion Discovery outcome repository is invalid")
    subjects = value.get("subjects")
    active_chosen = value.get("active_chosen")
    if not isinstance(subjects, list) or not _canonical_string_list(active_chosen):
        raise ValueError("completion Discovery outcome subjects are invalid")
    subject_ids: set[str] = set()
    subject_versions: set[tuple[str, str]] = set()
    subject_stable_ids: dict[str, str] = {}
    subject_order: list[tuple[str, str, str]] = []
    for index, subject in enumerate(subjects, start=1):
        if not isinstance(subject, dict) or set(subject) != {"id", "kind", "identity", "key", "version_digest"}:
            raise ValueError("completion Discovery outcome subject is invalid")
        if subject.get("id") != f"s{index}" or subject["id"] in subject_ids:
            raise ValueError("completion Discovery outcome subject identity is invalid")
        kind = subject.get("kind")
        identity = subject.get("identity")
        if kind not in {"file", "document", "symbol", "task", "artifact", "knowledge", "relationship_fact"}:
            raise ValueError("completion Discovery outcome subject kind is invalid")
        if not _valid_completion_subject_identity(kind, identity):
            raise ValueError("completion Discovery outcome subject identity is invalid")
        key_identity = identity.get("path") if isinstance(identity.get("path"), str) else _canonical_json(identity)
        expected_key = f"{kind}:{key_identity}"
        version_digest = subject.get("version_digest")
        if (
            subject.get("key") != expected_key
            or (expected_key, str(version_digest)) in subject_versions
            or not _is_digest(version_digest)
            or (
                kind not in {"file", "document"}
                and version_digest != digest_data({"kind": kind, "identity": identity})
            )
        ):
            raise ValueError("completion Discovery outcome subject key/version is invalid")
        subject_ids.add(subject["id"])
        subject_versions.add((expected_key, str(version_digest)))
        subject_stable_ids[subject["id"]] = digest_data(
            {"kind": kind, "identity": identity, "version_digest": version_digest}
        )
        subject_order.append((kind, _canonical_json(identity), version_digest))
    if subject_order != sorted(subject_order):
        raise ValueError("completion Discovery outcome subjects are not canonical")
    for role_id in active_chosen:
        if role_id not in subject_ids:
            raise ValueError("completion Discovery outcome Chosen reference is invalid")
    if not isinstance(value.get("episodes"), list) or not isinstance(value.get("verification_records"), list):
        raise ValueError("completion Discovery outcome records are invalid")
    claim_ids: set[str] = set()
    for episode in value["episodes"]:
        if not isinstance(episode, dict) or set(episode) != {"episode_id", "query_digest", "seed_result", "citations", "reviewed", "excluded", "outside_candidate_set"}:
            raise ValueError("completion Discovery episode is invalid")
        if not _is_digest(episode.get("episode_id")) or not _is_digest(episode.get("query_digest")):
            raise ValueError("completion Discovery episode identity is invalid")
        if not all(
            _canonical_string_list(episode.get(field))
            for field in ("reviewed", "excluded", "outside_candidate_set")
        ):
            raise ValueError("completion Discovery episode roles are invalid")
        reviewed = set(episode["reviewed"])
        excluded = set(episode["excluded"])
        if not excluded <= reviewed or not reviewed <= subject_ids or not set(episode["outside_candidate_set"]) <= subject_ids:
            raise ValueError("completion Discovery episode roles are invalid")
        if excluded & set(active_chosen):
            raise ValueError("completion Discovery Excluded and Chosen roles conflict")
        citations = episode.get("citations")
        if not isinstance(citations, list):
            raise ValueError("completion Discovery citations are invalid")
        citation_member_ids: list[str] = []
        for citation in citations:
            if not _valid_completion_citation(citation, episode_id=episode["episode_id"]):
                raise ValueError("completion Discovery citation is invalid")
            if citation["subject_id"] not in subject_ids:
                raise ValueError("completion Discovery citation subject is invalid")
            citation_member_ids.append(citation["member_id"])
            claim_ids.update(claim["evidence_digest"] for claim in citation["claims"])
        if citation_member_ids != sorted(set(citation_member_ids)):
            raise ValueError("completion Discovery citations are not canonical")
        seed_result = episode.get("seed_result")
        if not _valid_completion_seed(seed_result):
            raise ValueError("completion Discovery seed result is invalid")
        if bool(citations) != (seed_result is not None):
            raise ValueError("completion Discovery seed result and citations disagree")
        if seed_result is not None and not any(
            all(citation[field] == seed_result[field] for field in (
                "producer",
                "result_id",
                "source_receipt_digest",
                "canonical_request_digest",
            ))
            for citation in citations
        ):
            raise ValueError("completion Discovery seed result has no citation")
    verification_ids: list[str] = []
    for record in value["verification_records"]:
        if not isinstance(record, dict) or set(record) != {
            "record_id",
            "status",
            "evidence",
            "subject_ids",
            "claim_ids",
        }:
            raise ValueError("completion structured verification is invalid")
        record_subject_ids = record.get("subject_ids")
        record_claim_ids = record.get("claim_ids")
        evidence = record.get("evidence")
        if (
            record.get("status") not in VERIFICATION_STATUSES
            or not _canonical_string_list(record_subject_ids)
            or not set(record_subject_ids) <= subject_ids
            or not _canonical_digest_list(record_claim_ids)
            or not set(record_claim_ids) <= claim_ids
            or not record_subject_ids and not record_claim_ids
            or not _valid_completion_evidence(evidence)
        ):
            raise ValueError("completion structured verification is invalid")
        stable_subject_ids = sorted(subject_stable_ids[item] for item in record_subject_ids)
        record_base = {
            "status": record["status"],
            "evidence": evidence,
            "subject_ids": stable_subject_ids,
            "claim_ids": record_claim_ids,
        }
        if record.get("record_id") != digest_data(record_base):
            raise ValueError("completion structured verification digest is invalid")
        verification_ids.append(record["record_id"])
    if verification_ids != sorted(set(verification_ids)):
        raise ValueError("completion structured verification is not canonical")
    return _copy(value)


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _canonical_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and value == sorted(set(value))
    )


def _canonical_digest_list(value: Any) -> bool:
    return _canonical_string_list(value) and all(_is_digest(item) for item in value)


def _canonical_completion_ref(value: Any, *, relative: bool = False) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if not relative:
        return True
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and str(path) == value and all(part not in {"", ".", ".."} for part in path.parts)


def _valid_completion_subject_identity(kind: str, identity: Any) -> bool:
    if not isinstance(identity, dict):
        return False
    if kind in {"file", "document"}:
        return set(identity) == {"path"} and _canonical_completion_ref(identity.get("path"), relative=True)
    if kind == "artifact":
        return set(identity) == {"path"} and _canonical_completion_ref(identity.get("path"))
    if kind == "knowledge":
        return set(identity) == {"record_id"} and _canonical_completion_ref(identity.get("record_id"))
    if kind == "task":
        return (
            set(identity) == {"task_id"}
            and isinstance(identity.get("task_id"), str)
            and _TASK_ID_RE.fullmatch(identity["task_id"]) is not None
        )
    if kind == "symbol":
        return (
            set(identity) in ({"name"}, {"name", "in_file"})
            and _canonical_completion_ref(identity.get("name"))
            and ("in_file" not in identity or _canonical_completion_ref(identity["in_file"], relative=True))
        )
    return (
        set(identity) == {"producer", "ref"}
        and identity.get("producer") in {item.value for item in ResultProducer}
        and _canonical_completion_ref(identity.get("ref"))
    )


def _valid_completion_seed(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {
            "producer",
            "result_id",
            "source_receipt_digest",
            "canonical_request_digest",
            "candidate_member_set_digest",
        }
        and value.get("producer") in {item.value for item in ResultProducer}
        and all(
            _is_digest(value.get(field))
            for field in (
                "result_id",
                "source_receipt_digest",
                "canonical_request_digest",
                "candidate_member_set_digest",
            )
        )
    )


def _valid_completion_citation(value: Any, *, episode_id: str) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "producer",
        "result_id",
        "episode_id",
        "canonical_request_digest",
        "member_id",
        "source_receipt_digest",
        "subject_id",
        "claims",
    }:
        return False
    if (
        value.get("producer") not in {item.value for item in ResultProducer}
        or value.get("episode_id") != episode_id
        or not all(
            _is_digest(value.get(field))
            for field in (
                "result_id",
                "episode_id",
                "canonical_request_digest",
                "member_id",
                "source_receipt_digest",
            )
        )
        or not isinstance(value.get("subject_id"), str)
        or not isinstance(value.get("claims"), list)
        or len(value["claims"]) != 1
    ):
        return False
    claim = value["claims"][0]
    if not isinstance(claim, dict) or set(claim) != {
        "kind",
        "source_ref",
        "fact_ref",
        "source_receipt_digest",
        "evidence_digest",
    }:
        return False
    source_ref = claim.get("source_ref")
    authority, separator, ref = source_ref.partition(":") if isinstance(source_ref, str) else ("", "", "")
    claim_base = {key: claim[key] for key in claim if key != "evidence_digest"}
    member_base = {
        "producer": value["producer"],
        "result_id": value["result_id"],
        "authority": authority,
        "ref": ref,
    }
    return (
        claim.get("kind") == "result_selection"
        and separator == ":"
        and authority in {item.value for item in ResultAuthority}
        and _canonical_completion_ref(ref)
        and claim.get("fact_ref") == value["result_id"]
        and claim.get("source_receipt_digest") == value["source_receipt_digest"]
        and claim.get("evidence_digest") == digest_data(claim_base)
        and value.get("member_id") == digest_data(member_base)
    )


def _valid_completion_evidence(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"ref", "digest", "kind"}:
        return False
    if value.get("kind") not in {"digest", "file"} or not _is_digest(value.get("digest")):
        return False
    if value["kind"] == "digest":
        return value.get("ref") == value["digest"]
    return _canonical_completion_ref(value.get("ref"))


def result_member_capsules(
    root: Path,
    *,
    target: RepoTarget | None,
    receipt: Mapping[str, Any],
) -> list[dict[str, Any]]:
    producer = ResultProducer(str(receipt.get("producer") or ""))
    source_digest = str(receipt.get("receipt_digest") or "")
    if _DIGEST_RE.fullmatch(source_digest) is None:
        raise ValueError("result receipt digest is invalid")
    capsules: list[dict[str, Any]] = []
    for raw in receipt.get("selectable", []):
        selection = ResultSelection(ResultAuthority(str(raw["authority"])), str(raw["ref"]))
        subject = _selection_subject(root, target=target, producer=producer, selection=selection)
        member_basis = {
            "producer": producer.value,
            "result_id": str(receipt["result_id"]),
            "authority": selection.authority.value,
            "ref": selection.ref,
        }
        claim_basis = {
            "kind": "result_selection",
            "source_ref": f"{selection.authority.value}:{selection.ref}",
            "fact_ref": str(receipt["result_id"]),
            "source_receipt_digest": source_digest,
        }
        capsules.append(
            {
                "member_id": digest_data(member_basis),
                "authority": selection.authority.value,
                "ref": selection.ref,
                "subject": subject,
                "claims": [{**claim_basis, "evidence_digest": digest_data(claim_basis)}],
            }
        )
    return sorted(capsules, key=lambda item: str(item["member_id"]))


def _selection_subject(
    root: Path,
    *,
    target: RepoTarget | None,
    producer: ResultProducer,
    selection: ResultSelection,
) -> dict[str, Any]:
    authority = selection.authority
    ref = selection.ref
    if authority in {ResultAuthority.SOURCE, ResultAuthority.DOCUMENT}:
        kind = "document" if authority is ResultAuthority.DOCUMENT else "file"
        return _path_subject(root, target=target, path=ref, kind=kind)
    if authority is ResultAuthority.KNOWLEDGE:
        return _opaque_subject("knowledge", {"record_id": ref})
    if authority is ResultAuthority.TASK_HISTORY:
        task_match = _TASK_ID_RE.search(ref)
        if task_match and ref == task_match.group(0):
            return _opaque_subject("task", {"task_id": ref})
        return _opaque_subject("artifact", {"path": ref})
    try:
        selector = json.loads(ref)
    except json.JSONDecodeError:
        selector = None
    if isinstance(selector, dict) and isinstance(selector.get("kind"), str):
        kind = str(selector["kind"])
        value = str(selector.get("value") or "")
        if kind in {"file", "impact_file"}:
            return _path_subject(root, target=target, path=value, kind="file")
        if kind in {"symbol", "callers_of", "callees_of", "impact_symbol"}:
            identity = {"name": value}
            if isinstance(selector.get("in_file"), str):
                identity["in_file"] = selector["in_file"]
            return _opaque_subject("symbol", identity)
    if target is not None:
        candidate = _relative_repo_path(target, ref)
        if candidate and (target.root_path / candidate).is_file():
            return _path_subject(root, target=target, path=ref, kind="file")
    return _opaque_subject("relationship_fact", {"producer": producer.value, "ref": ref})


def _canonical_path_subjects(root: Path, *, target: RepoTarget | None, paths: Iterable[str]) -> list[dict[str, Any]]:
    return [_path_subject(root, target=target, path=str(path), kind="file") for path in paths]


def _workspace_artifact_subject(root: Path, value: str) -> dict[str, Any]:
    raw = str(value)
    pure = PurePosixPath(raw)
    invalid = (
        not raw
        or raw != raw.strip()
        or "\\" in raw
        or pure.is_absolute()
        or str(pure) != raw
        or any(part in {"", ".", ".."} for part in pure.parts)
        or (pure.parts and pure.parts[0] == "repos")
    )
    if invalid:
        raise RepoctlError(
            "workspace verification artifact must be a canonical non-product path inside the workspace",
            code="workspace_verification_artifact_invalid",
            path=raw,
        )
    candidate = root / raw
    try:
        workspace_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved_relative = resolved.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepoctlError(
            "workspace verification artifact must resolve to an existing file inside the workspace",
            code="workspace_verification_artifact_invalid",
            path=raw,
        ) from exc
    if not resolved.is_file() or (resolved_relative.parts and resolved_relative.parts[0] == "repos"):
        raise RepoctlError(
            "workspace verification artifact must be a regular non-product file",
            code="workspace_verification_artifact_invalid",
            path=raw,
        )
    return _opaque_subject("artifact", {"path": raw})


def current_path_subject(
    root: Path,
    *,
    target: RepoTarget | None,
    path: str,
    kind: str = "file",
) -> dict[str, Any]:
    """Return the canonical current path subject used by capture and consumers."""
    if kind not in {"file", "document"}:
        raise ValueError("current path subject kind must be file or document")
    relative = _relative_repo_path(target, path)
    if relative is None:
        raise RepoctlError("Discovery outcome path is not in the selected repository", code="invalid_discovery_path", path=path)
    resolved = target.root_path / relative if target is not None else root / relative
    try:
        content_digest = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else digest_data({"missing": relative})
    except OSError as exc:
        raise RepoctlError("Discovery outcome subject cannot be read", code="discovery_subject_unreadable", path=path) from exc
    classification_digest = digest_data({"kind": kind, "suffix": PurePosixPath(relative).suffix.casefold()})
    identity = {"path": relative}
    version_digest = digest_data(
        {
            "identity": identity,
            "content_digest": content_digest,
            "classification_digest": classification_digest,
        }
    )
    return _subject(kind, identity, version_digest)


def _path_subject(root: Path, *, target: RepoTarget | None, path: str, kind: str) -> dict[str, Any]:
    return current_path_subject(root, target=target, path=path, kind=kind)


def _relative_repo_path(target: RepoTarget | None, value: str) -> str | None:
    raw = str(value).strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return None
    if target is not None:
        prefix = target.display_path.rstrip("/") + "/"
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    path = PurePosixPath(raw)
    if str(path) != raw or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return raw


def _opaque_subject(kind: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    return _subject(kind, dict(identity), digest_data({"kind": kind, "identity": identity}))


def _subject(kind: str, identity: Mapping[str, Any], version_digest: str) -> dict[str, Any]:
    key_identity = identity.get("path") if isinstance(identity.get("path"), str) else _canonical_json(identity)
    key = f"{kind}:{key_identity}"
    basis = {"kind": kind, "identity": dict(identity), "version_digest": version_digest}
    return {"subject_id": digest_data(basis), **basis, "key": key}


def _state_subjects(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [*_copy(state.get("active_chosen") or [])]
    episodes = [*_copy(state.get("prior_episodes") or [])]
    if state.get("active_episode") is not None:
        episodes.append(_copy(state["active_episode"]))
    for episode in episodes:
        values.extend(episode.get("reviewed") or [])
        values.extend(episode.get("excluded") or [])
        values.extend(
            citation["member"]["subject"]
            for citation in episode.get("citations") or []
            if isinstance(citation, dict) and isinstance(citation.get("member"), dict)
        )
    by_id = {str(item["subject_id"]): item for item in values}
    return [by_id[key] for key in sorted(by_id)]


def _canonical_evidence(root: Path, value: str) -> dict[str, str]:
    ref = str(value).strip()
    if _DIGEST_RE.fullmatch(ref):
        return {"ref": ref, "digest": ref, "kind": "digest"}
    candidate = Path(ref)
    resolved = candidate if candidate.is_absolute() else root / candidate
    if not resolved.is_file():
        raise RepoctlError("verification evidence ref must be an existing file or sha256 digest", code="verification_evidence_invalid", path=ref)
    try:
        digest = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise RepoctlError("verification evidence cannot be read", code="verification_evidence_invalid", path=ref) from exc
    if candidate.is_absolute():
        canonical_ref = candidate.as_posix()
    else:
        pure = PurePosixPath(ref)
        if str(pure) != ref or any(part in {"", ".", ".."} for part in pure.parts):
            raise RepoctlError("verification evidence path is not canonical", code="verification_evidence_invalid", path=ref)
        canonical_ref = ref
    return {"ref": canonical_ref, "digest": digest, "kind": "file"}


def _empty_episode(*, episode_id: str, query: str) -> dict[str, Any]:
    return {
        "episode_id": episode_id,
        "query": query,
        "seed_result": None,
        "citations": [],
        "reviewed": [],
        "excluded": [],
        "outside_candidate_set": [],
    }


def _query_episode_id(query: str) -> str:
    return digest_data({"kind": "task_discovery_query", "query": query})


def _episode_has_evidence(episode: Mapping[str, Any]) -> bool:
    return bool(episode.get("citations") or episode.get("reviewed") or episode.get("excluded"))


def _seal_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    return _copy(episode)


def _subjects_by_identity(subjects: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {_subject_identity_key(item): _copy(item) for item in subjects}
    return [by_key[key] for key in sorted(by_key)]


def _subject_identity_key(subject: Mapping[str, Any]) -> str:
    return str(subject["subject_id"])


def _dedupe_dicts(values: Iterable[Mapping[str, Any]], *, key: str) -> list[dict[str, Any]]:
    by_key = {str(value[key]): _copy(value) for value in values}
    return [by_key[value] for value in sorted(by_key)]


def _with_state_digest(state: Mapping[str, Any]) -> dict[str, Any]:
    basis = {key: _copy(value) for key, value in state.items() if key != "state_digest"}
    return {**basis, "state_digest": digest_data(basis)}


def _validate_state(value: Any, *, task_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("task Discovery outcome state must be an object")
    expected = {
        "schema",
        "schema_version",
        "task_id",
        "repository",
        "active_chosen",
        "prior_episodes",
        "active_episode",
        "verification_records",
        "state_digest",
    }
    if set(value) != expected or value.get("schema") != DISCOVERY_OUTCOME_SCHEMA or value.get("schema_version") != DISCOVERY_OUTCOME_SCHEMA_VERSION:
        raise ValueError("task Discovery outcome state schema is invalid")
    if value.get("task_id") != task_id or _TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError("task Discovery outcome identity is invalid")
    basis = {key: value[key] for key in value if key != "state_digest"}
    if value.get("state_digest") != digest_data(basis):
        raise ValueError("task Discovery outcome state digest does not match")
    repository = value.get("repository")
    if repository is not None and (
        not isinstance(repository, dict)
        or set(repository) != {"id", "path", "identity_source"}
        or not all(isinstance(repository.get(key), str) and repository[key] for key in repository)
    ):
        raise ValueError("task Discovery outcome repository is invalid")
    if not isinstance(value.get("active_chosen"), list) or not isinstance(value.get("prior_episodes"), list) or not isinstance(value.get("verification_records"), list):
        raise ValueError("task Discovery outcome collections are invalid")
    episodes = [*value["prior_episodes"]]
    if value.get("active_episode") is not None:
        episodes.append(value["active_episode"])
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) != {"episode_id", "query", "seed_result", "citations", "reviewed", "excluded", "outside_candidate_set"}:
            raise ValueError("task Discovery episode schema is invalid")
        if not _DIGEST_RE.fullmatch(str(episode.get("episode_id") or "")) or not isinstance(episode.get("query"), str):
            raise ValueError("task Discovery episode identity is invalid")
        reviewed = {_subject_identity_key(item) for item in episode["reviewed"]}
        excluded = {_subject_identity_key(item) for item in episode["excluded"]}
        if not excluded <= reviewed:
            raise ValueError("task Discovery Excluded must be a subset of Reviewed")
    chosen = {_subject_identity_key(item) for item in value["active_chosen"]}
    if any(chosen & {_subject_identity_key(item) for item in episode["excluded"]} for episode in episodes):
        raise ValueError("task Discovery Excluded and Chosen roles conflict")
    return _copy(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))
