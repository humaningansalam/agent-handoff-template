from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .graph_model import digest_data
from .io import atomic_write
from .tasks import Problem


PAGE_BY_KIND = {
    "decision": "decisions.md",
    "invariant": "invariants.md",
    "failure_mode": "failure-modes.md",
}


def render_knowledge(root: Path, *, repo_id: str, output: Path) -> tuple[dict[str, Any], list[Problem]]:
    output_dir = output if output.is_absolute() else root / output
    root_real = root.resolve()
    output_real = output_dir.resolve()
    try:
        output_rel = output_real.relative_to(root_real).as_posix()
    except ValueError:
        return {}, [Problem("error", "knowledge_render_output_outside_workspace", "render output must stay inside the workspace", output.as_posix())]
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    events = _load_events(root, repo_id=repo_id)
    rendered: list[dict[str, Any]] = []
    pages = _pages(root, records, events)
    page_records = _page_records(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        path = output_dir / name
        atomic_write(path, content)
        rendered.append(
            {
                "path": path.relative_to(root).as_posix(),
                "digest": digest_data({"content": content}),
                "source_bundle": _page_source_bundle(name, page_records.get(name, []), events),
            }
        )
    rendered = sorted(rendered, key=lambda item: item["path"])
    render_digest = digest_data({"rendered": rendered})
    manifest = {
        "schema": "repoctl.knowledge.render_manifest",
        "schema_version": 1,
        "repo_id": repo_id,
        "authoritative": False,
        "output": output_rel,
        "record_count": len(records),
        "event_count": len(events),
        "render_digest": render_digest,
        "rendered": rendered,
    }
    manifest_digest = digest_data(manifest)
    manifest_path = output_dir / "manifest.json"
    atomic_write(manifest_path, json.dumps({**manifest, "manifest_digest": manifest_digest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "schema": "repoctl.knowledge.render",
        "schema_version": 1,
        "repo_id": repo_id,
        "authoritative": False,
        "output": output_rel,
        "record_count": len(records),
        "event_count": len(events),
        "render_digest": render_digest,
        "manifest": {
            "path": manifest_path.relative_to(root).as_posix(),
            "digest": manifest_digest,
        },
        "rendered": rendered,
    }, []


def _load_records(root: Path) -> list[dict[str, Any]]:
    directory = root / "docs/knowledge/records"
    records: list[dict[str, Any]] = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("K-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            records.append(data)
    return records


def _load_events(root: Path, *, repo_id: str) -> list[dict[str, Any]]:
    directory = root / "docs/knowledge/events"
    events: list[dict[str, Any]] = []
    if not directory.exists():
        return events
    for path in sorted(directory.glob("E-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("repo_id") or "") == repo_id:
            events.append(data)
    return events


def _pages(root: Path, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, str]:
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in PAGE_BY_KIND}
    for record in records:
        kind = str(record.get("kind") or "")
        if kind in by_kind:
            by_kind[kind].append(record)
    pages: dict[str, str] = {"INDEX.md": _index_page(records, events, by_kind)}
    for kind, filename in PAGE_BY_KIND.items():
        pages[filename] = _kind_page(root, kind, by_kind[kind], _superseded_ids(records), _deprecated_ids(events), events)
    return pages


def _page_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_page: dict[str, list[dict[str, Any]]] = {"INDEX.md": sorted(records, key=lambda item: str(item.get("id") or ""))}
    for kind, filename in PAGE_BY_KIND.items():
        by_page[filename] = sorted([record for record in records if str(record.get("kind") or "") == kind], key=lambda item: str(item.get("id") or ""))
    return by_page


def _page_source_bundle(name: str, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    refs = _unique_source_refs(records)
    event_ids = [str(event.get("id") or "") for event in events if _event_belongs_to_page(name, event, records)]
    bundle = {
        "record_ids": [str(record.get("id") or "") for record in records],
        "source_refs": refs,
        "event_ids": sorted(event_ids),
    }
    bundle["source_bundle_digest"] = digest_data(bundle)
    return bundle


def _unique_source_refs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for record in records:
        source_refs = record.get("source_refs", [])
        if not isinstance(source_refs, list):
            continue
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            refs[key] = ref
    return [refs[key] for key in sorted(refs)]


def _event_belongs_to_page(name: str, event: dict[str, Any], records: list[dict[str, Any]]) -> bool:
    if name == "INDEX.md":
        return True
    record_ids = {str(record.get("id") or "") for record in records}
    return str(event.get("record_id") or "") in record_ids or str(event.get("superseded_by") or "") in record_ids


def _index_page(records: list[dict[str, Any]], events: list[dict[str, Any]], by_kind: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "# Knowledge Index",
        "",
        "Non-authoritative generated view. Source records remain under `docs/knowledge/records/`.",
        "",
        "## Pages",
        "",
    ]
    for kind, filename in PAGE_BY_KIND.items():
        lines.append(f"- [{kind.replace('_', ' ').title()}]({filename}) - {len(by_kind[kind])} records")
    lines.extend(["", "## Source Bundle", ""])
    lines.append(f"- Records: {len(records)}")
    lines.append(f"- Events: {len(events)}")
    lines.append(f"- Records digest: {digest_data([_record_digest_basis(record) for record in records])}")
    lines.append(f"- Events digest: {digest_data([_event_digest_basis(event) for event in events])}")
    return "\n".join(lines).rstrip() + "\n"


def _kind_page(root: Path, kind: str, records: list[dict[str, Any]], superseded_ids: set[str], deprecated_ids: set[str], events: list[dict[str, Any]]) -> str:
    title = kind.replace("_", " ").title()
    lines = [
        f"# {title}",
        "",
        "Non-authoritative generated view. Check record source refs before using these facts.",
        "",
    ]
    if not records:
        lines.append("No reviewed records.")
        return "\n".join(lines).rstrip() + "\n"
    events_by_record = _events_by_record(events)
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        lines.extend(_record_section(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, events=events_by_record.get(str(record.get("id") or ""), [])))
    return "\n".join(lines).rstrip() + "\n"


def _record_section(root: Path, record: dict[str, Any], *, superseded_ids: set[str], deprecated_ids: set[str], events: list[dict[str, Any]]) -> list[str]:
    record_id = str(record.get("id") or "")
    lines = [
        f"## {record.get('title', record.get('id', 'Untitled'))}",
        "",
        f"- Record: `{record_id}`",
        f"- Status: `{_derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)}`",
        f"- Digest: `{record.get('record_digest', '')}`",
    ]
    if record.get("supersedes"):
        lines.append(f"- Supersedes: `{', '.join(str(item) for item in record.get('supersedes', []))}`")
    superseded_by = [str(event.get("superseded_by") or "") for event in events if event.get("type") == "superseded" and event.get("superseded_by")]
    if superseded_by:
        lines.append(f"- Superseded by: `{', '.join(superseded_by)}`")
    if events:
        lines.append(f"- Lifecycle events: `{', '.join(str(event.get('id') or '') for event in events)}`")
    approval_context = _approval_context(record)
    if approval_context:
        lines.append(f"- Approved from candidate: `{approval_context['candidate_id']}`")
        if approval_context["warning_codes"]:
            lines.append(f"- Candidate warnings: `{', '.join(approval_context['warning_codes'])}`")
        if approval_context["related_records"]:
            related = ", ".join(
                f"{item.get('record_id', '')} status={item.get('status', '')} relation={item.get('relation', '')}"
                for item in approval_context["related_records"]
                if isinstance(item, dict)
            )
            if related:
                lines.append(f"- Related at approval: `{related}`")
    lines.extend([
        "",
        "### Claim",
        "",
        str(record.get("claim") or "").strip() or "(empty)",
        "",
        "### Summary",
        "",
        str(record.get("summary") or "").strip() or "(empty)",
        "",
        "### Sources",
        "",
    ])
    refs = record.get("source_refs", [])
    if isinstance(refs, list) and refs:
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            section = f"#{ref.get('section')}" if ref.get("section") else ""
            digest = ref.get("content_sha256", "")
            source_status = _source_ref_status(root, ref)
            lines.append(f"- `{ref.get('path', '')}{section}` `{digest}` status=`{source_status}`")
    else:
        lines.append("- Missing source refs; do not treat this record as current knowledge.")
    lines.append("")
    return lines


def _record_digest_basis(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id", ""),
        "record_digest": record.get("record_digest", ""),
        "source_refs": record.get("source_refs", []),
    }


def _event_digest_basis(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id", ""),
        "type": event.get("type", ""),
        "event_digest": event.get("event_digest", ""),
        "record_id": event.get("record_id", ""),
        "candidate_id": event.get("candidate_id", ""),
        "superseded_by": event.get("superseded_by", ""),
    }


def _approval_context(record: dict[str, Any]) -> dict[str, Any]:
    created_from = record.get("created_from")
    if not isinstance(created_from, dict):
        return {}
    candidate_check = created_from.get("candidate_check")
    if not isinstance(candidate_check, dict):
        candidate_check = {}
    warning_codes = candidate_check.get("warning_codes")
    related_records = candidate_check.get("related_records")
    return {
        "candidate_id": str(created_from.get("candidate_id") or ""),
        "warning_codes": warning_codes if isinstance(warning_codes, list) else [],
        "related_records": related_records if isinstance(related_records, list) else [],
    }


def _source_ref_status(root: Path, ref: dict[str, Any]) -> str:
    rel = str(ref.get("path") or "")
    expected = str(ref.get("content_sha256") or "")
    path = root / rel
    if not path.is_file():
        return "missing"
    actual = "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    if actual != expected:
        return "digest_mismatch"
    return "current"


def _events_by_record(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_record: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        record_ids = [str(event.get("record_id") or "")]
        if event.get("superseded_by"):
            record_ids.append(str(event.get("superseded_by") or ""))
        for record_id in record_ids:
            if record_id:
                by_record.setdefault(record_id, []).append(event)
    return by_record


def _superseded_ids(records: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for record in records:
        supersedes = record.get("supersedes", [])
        if isinstance(supersedes, list):
            values.update(str(item) for item in supersedes if str(item))
    return values


def _deprecated_ids(events: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for event in events:
        if event.get("type") == "deprecated":
            record_id = str(event.get("record_id") or "")
            if record_id:
                values.add(record_id)
    return values


def _derived_status(root: Path, record: dict[str, Any], *, superseded_ids: set[str], deprecated_ids: set[str]) -> str:
    if _has_digest_drift(root, record):
        return "stale"
    if str(record.get("id") or "") in superseded_ids:
        return "superseded"
    if str(record.get("id") or "") in deprecated_ids:
        return "deprecated"
    return str(record.get("status") or "")


def _has_digest_drift(root: Path, record: dict[str, Any]) -> bool:
    refs = record.get("source_refs", [])
    if not isinstance(refs, list):
        return True
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rel = str(ref.get("path") or "")
        expected = str(ref.get("content_sha256") or "")
        path = root / rel
        if not path.is_file():
            return True
        actual = "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if actual != expected:
            return True
    return False
