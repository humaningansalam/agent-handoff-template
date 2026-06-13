from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import UTC, datetime

from tools.contracts.path_contracts import validate_project_slug
from tools.runtime.json_io import read_json_object, read_json_strict, write_json_atomic_under_root

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_ROOT = "ops/research-ops/post-run-audit"
ALLOWED_PROOF_SOURCES = {"notion_api_readback", "local_filesystem", "fingerprint", "artifact_profile"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def evidence_path(root: Path, project: str) -> Path:
    slug = validate_project_slug(project)
    return Path(root) / EVIDENCE_ROOT / slug / "evidence.json"


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(Path(root).resolve()).as_posix()


def _verified_actual_from_file(root: Path, actual_file: str, *, required_kind: str) -> dict[str, Any]:
    path = Path(root) / actual_file
    actual = read_json_strict(path)
    if actual.get("source") != "notion_api_readback":
        raise ValueError("workflow evidence requires notion_api_readback actual source")
    if actual.get("kind") != required_kind:
        raise ValueError(f"workflow evidence requires {required_kind} actual")
    project = str(actual.get("project") or actual.get("metadata", {}).get("project") or "")
    payload = {
        "verified": True,
        "source": "notion_api_readback",
        "kind": str(actual.get("kind") or ""),
        "project": project,
        "actual_file": actual_file,
        "page_id": str(actual.get("id") or actual.get("page_id") or ""),
        "url": str(actual.get("url") or ""),
        "text_hash": str(actual.get("text_hash") or ""),
    }
    execution_id = actual.get("execution_id") or actual.get("metadata", {}).get("execution_id")
    if execution_id:
        payload["execution_id"] = str(execution_id)
    return payload


def _load(root: Path, project: str) -> dict[str, Any]:
    path = evidence_path(root, project)
    payload = read_json_object(path, missing_ok=True)
    if not payload:
        return {"schema_version": EVIDENCE_SCHEMA_VERSION, "project": validate_project_slug(project), "execution_readbacks": [], "proofs": []}
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("workflow evidence schema_version mismatch")
    if payload.get("project") != validate_project_slug(project):
        raise ValueError("workflow evidence project mismatch")
    if not isinstance(payload.get("execution_readbacks"), list):
        payload["execution_readbacks"] = []
    if not isinstance(payload.get("proofs"), list):
        payload["proofs"] = []
    return payload


def _proof(
    *,
    claim: str,
    status: str,
    source: str,
    artifact_path: str = "",
    page_id: str = "",
    hash_value: str = "",
    verified: bool,
    reason: str = "",
) -> dict[str, Any]:
    if source not in ALLOWED_PROOF_SOURCES:
        raise ValueError(f"unsupported proof source: {source}")
    return {
        "claim": claim,
        "status": status,
        "source": source,
        "checked_at": _now_iso(),
        "artifact_path": artifact_path,
        "hash": hash_value,
        "page_id": page_id,
        "verified": verified,
        "reason": reason,
    }


def _upsert_proof(payload: dict[str, Any], proof: dict[str, Any]) -> None:
    claim = str(proof.get("claim") or "")
    proofs = [item for item in payload.get("proofs", []) if not isinstance(item, dict) or str(item.get("claim") or "") != claim]
    payload["proofs"] = [*proofs, proof]


def require_verified_claim(payload: dict[str, Any], claim: str) -> dict[str, Any]:
    for proof in payload.get("proofs", []):
        if isinstance(proof, dict) and proof.get("claim") == claim and proof.get("verified") is True:
            return proof
    raise ValueError(f"verified proof is missing for claim: {claim}")


def _save(root: Path, project: str, payload: dict[str, Any]) -> str:
    path = evidence_path(root, project)
    write_json_atomic_under_root(path, payload, root)
    return _rel(root, path)


def record_project_readback(root: Path, project: str, actual_file: str) -> str:
    payload = _load(root, project)
    payload["project_readback"] = _verified_actual_from_file(root, actual_file, required_kind="notion_project_actual")
    _upsert_proof(
        payload,
        _proof(
            claim="notion_project_synced",
            status="verified",
            source="notion_api_readback",
            artifact_path=actual_file,
            page_id=str(payload["project_readback"].get("page_id") or ""),
            hash_value=str(payload["project_readback"].get("text_hash") or ""),
            verified=True,
        ),
    )
    return _save(root, project, payload)


def record_execution_readback(root: Path, project: str, actual_file: str) -> str:
    payload = _load(root, project)
    readback = _verified_actual_from_file(root, actual_file, required_kind="notion_execution_actual")
    execution_id = str(readback.get("execution_id") or "")
    existing = [
        item for item in payload.get("execution_readbacks", [])
        if not isinstance(item, dict) or str(item.get("execution_id") or "") != execution_id
    ]
    payload["execution_readbacks"] = [*existing, readback]
    _upsert_proof(
        payload,
        _proof(
            claim=f"notion_execution_synced:{execution_id}",
            status="verified",
            source="notion_api_readback",
            artifact_path=actual_file,
            page_id=str(readback.get("page_id") or ""),
            hash_value=str(readback.get("text_hash") or ""),
            verified=True,
        ),
    )
    return _save(root, project, payload)


def record_wrapup_readback(root: Path, project: str, actual_file: str) -> str:
    payload = _load(root, project)
    payload["wrapup_readback"] = _verified_actual_from_file(root, actual_file, required_kind="notion_project_actual")
    _upsert_proof(
        payload,
        _proof(
            claim="notion_project_updated_by_wrapup",
            status="verified",
            source="notion_api_readback",
            artifact_path=actual_file,
            page_id=str(payload["wrapup_readback"].get("page_id") or ""),
            hash_value=str(payload["wrapup_readback"].get("text_hash") or ""),
            verified=True,
        ),
    )
    return _save(root, project, payload)


def record_local_delete(root: Path, project: str, *, active_project_cleared: bool = False) -> str:
    slug = validate_project_slug(project)
    target = Path(root) / "projects" / slug
    if target.exists():
        raise ValueError("local delete proof requires missing project directory")
    payload = _load(root, slug)
    payload["local_delete"] = {"status": "deleted", "source": "local_filesystem", "path": f"projects/{slug}/", "active_project_cleared": active_project_cleared}
    _upsert_proof(
        payload,
        _proof(
            claim="local_project_deleted",
            status="verified",
            source="local_filesystem",
            artifact_path=f"projects/{slug}/",
            verified=True,
            reason="project directory is absent after wrapper-controlled deletion",
        ),
    )
    return _save(root, slug, payload)


def record_delete_readback(root: Path, project: str, readback: dict[str, Any]) -> str:
    if readback.get("source") != "notion_api_readback":
        raise ValueError("delete evidence requires notion_api_readback source")
    if readback.get("archived") is not True or readback.get("in_trash") is not True:
        raise ValueError("delete evidence requires archived=true and in_trash=true readback")
    payload = _load(root, project)
    payload["delete_readback"] = {
        "source": "notion_api_readback",
        "kind": str(readback.get("kind") or "notion_project_archive_actual"),
        "project": validate_project_slug(project),
        "archived": True,
        "in_trash": True,
        "page_id": str(readback.get("notion_page_id") or readback.get("page_id") or ""),
        "url": str(readback.get("url") or ""),
    }
    _upsert_proof(
        payload,
        _proof(
            claim="notion_project_archived",
            status="verified",
            source="notion_api_readback",
            page_id=str(payload["delete_readback"].get("page_id") or ""),
            verified=True,
            reason="Notion archive readback reports archived=true and in_trash=true",
        ),
    )
    return _save(root, project, payload)
