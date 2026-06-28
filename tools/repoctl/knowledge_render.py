from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .graph_model import digest_data
from .io import atomic_write
from .knowledge_candidates import event_integrity_problems
from .tasks import Problem


PAGE_BY_KIND = {
    "decision": "decisions.md",
    "invariant": "invariants.md",
    "failure_mode": "failure-modes.md",
}


def render_knowledge(root: Path, *, repo_id: str, output: Path, check: bool = False) -> tuple[dict[str, Any], list[Problem]]:
    output_dir = output if output.is_absolute() else root / output
    root_real = root.resolve()
    output_real = output_dir.resolve()
    try:
        output_rel = output_real.relative_to(root_real).as_posix()
    except ValueError:
        return {}, [Problem("error", "knowledge_render_output_outside_workspace", "render output must stay inside the workspace", output.as_posix())]
    if not _is_generated_output_path(root=root, output_dir=output_dir):
        return {}, [Problem("error", "knowledge_render_output_not_generated", "render output must stay under docs/knowledge/generated so generated views cannot become context sources", output.as_posix())]
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    events = _load_events(root, repo_id=repo_id)
    event_problems = event_integrity_problems(root, repo_id=repo_id, records=records)
    if event_problems:
        return {
            "schema": "repoctl.knowledge.render",
            "schema_version": 1,
            "repo_id": repo_id,
            "authoritative": False,
            "output": output_rel,
            "record_count": len(records),
            "event_count": len(events),
            "event_checks": {"error_count": len(event_problems)},
            "rendered": [],
        }, event_problems
    pages = _pages(root, records, events)
    page_records = _page_records(records)
    rendered = _rendered_page_entries(root=root, output_dir=output_dir, pages=pages, page_records=page_records, events=events)
    rendered = sorted(rendered, key=lambda item: item["path"])
    render_digest = digest_data({"rendered": rendered})
    manifest, manifest_digest = _render_manifest(repo_id=repo_id, output_rel=output_rel, record_count=len(records), event_count=len(events), render_digest=render_digest, rendered=rendered)
    manifest_path = output_dir / "manifest.json"
    if check:
        check_problems, check_data = _check_rendered_output(root=root, output_dir=output_dir, manifest_path=manifest_path, manifest={**manifest, "manifest_digest": manifest_digest}, pages=pages)
        return {
            "schema": "repoctl.knowledge.render",
            "schema_version": 1,
            "repo_id": repo_id,
            "authoritative": False,
            "mode": "check",
            "output": output_rel,
            "record_count": len(records),
            "event_count": len(events),
            "render_digest": render_digest,
            "manifest": {
                "path": manifest_path.relative_to(root).as_posix(),
                "digest": manifest_digest,
            },
            "rendered": rendered,
            "check": check_data,
        }, check_problems
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = _remove_stale_rendered_files(root=root, output_dir=output_dir, next_page_names=set(pages))
    for name, content in pages.items():
        path = output_dir / name
        atomic_write(path, content)
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
        "removed": removed,
        "rendered": rendered,
    }, []


def _rendered_page_entries(*, root: Path, output_dir: Path, pages: dict[str, str], page_records: dict[str, list[dict[str, Any]]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": (output_dir / name).relative_to(root).as_posix(),
            "digest": digest_data({"content": content}),
            "source_bundle": _page_source_bundle(root, name, page_records.get(name, []), events),
        }
        for name, content in pages.items()
    ]


def _render_manifest(*, repo_id: str, output_rel: str, record_count: int, event_count: int, render_digest: str, rendered: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    manifest = {
        "schema": "repoctl.knowledge.render_manifest",
        "schema_version": 1,
        "repo_id": repo_id,
        "authoritative": False,
        "output": output_rel,
        "record_count": record_count,
        "event_count": event_count,
        "render_digest": render_digest,
        "rendered": rendered,
    }
    return manifest, digest_data(manifest)


def _check_rendered_output(*, root: Path, output_dir: Path, manifest_path: Path, manifest: dict[str, Any], pages: dict[str, str]) -> tuple[list[Problem], dict[str, Any]]:
    problems: list[Problem] = []
    current_manifest: dict[str, Any] = {}
    if not manifest_path.is_file():
        problems.append(Problem("error", "knowledge_render_manifest_missing", "render manifest is missing", manifest_path.relative_to(root).as_posix()))
    else:
        try:
            current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(Problem("error", "knowledge_render_manifest_invalid", str(exc), manifest_path.relative_to(root).as_posix()))
    if current_manifest and current_manifest.get("manifest_digest") != manifest.get("manifest_digest"):
        problems.append(Problem("error", "knowledge_render_manifest_stale", "render manifest does not match current knowledge records", manifest_path.relative_to(root).as_posix()))
    missing_pages: list[str] = []
    stale_pages: list[str] = []
    unreadable_pages: list[str] = []
    broken_links: list[dict[str, str]] = []
    for name, content in pages.items():
        page_path = output_dir / name
        page_rel = page_path.relative_to(root).as_posix()
        if not page_path.is_file():
            missing_pages.append(page_rel)
            problems.append(Problem("error", "knowledge_render_page_missing", "rendered knowledge page is missing", page_rel))
            continue
        try:
            current_text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable_pages.append(page_rel)
            problems.append(Problem("error", "knowledge_render_page_unreadable", str(exc), page_rel))
            continue
        current_digest = digest_data({"content": current_text})
        expected_digest = digest_data({"content": content})
        if current_digest != expected_digest:
            stale_pages.append(page_rel)
            problems.append(Problem("error", "knowledge_render_page_stale", "rendered knowledge page does not match current knowledge records", page_rel))
        for link in _broken_internal_links(output_dir=output_dir, page_name=name, content=current_text, page_names=set(pages)):
            broken_links.append({"page": page_rel, "link": link})
            problems.append(Problem("error", "knowledge_render_broken_link", "rendered knowledge page has a broken internal link", f"{page_rel}:{link}"))
    stale_owned_pages = _stale_rendered_files(root=root, output_dir=output_dir, next_page_names=set(pages))
    for page in stale_owned_pages:
        problems.append(Problem("error", "knowledge_render_stale_page", "render output contains a stale page owned by the previous manifest", page))
    return problems, {
        "current": not problems,
        "missing_pages": missing_pages,
        "stale_pages": stale_pages,
        "unreadable_pages": unreadable_pages,
        "broken_links": broken_links,
        "stale_owned_pages": stale_owned_pages,
    }


def _is_generated_output_path(*, root: Path, output_dir: Path) -> bool:
    generated_root = root / "docs/knowledge/generated"
    try:
        output_dir.resolve().relative_to(generated_root.resolve())
    except ValueError:
        return False
    return True


def _remove_stale_rendered_files(*, root: Path, output_dir: Path, next_page_names: set[str]) -> list[str]:
    stale_pages = _stale_rendered_files(root=root, output_dir=output_dir, next_page_names=next_page_names)
    for rel_path in stale_pages:
        (root / rel_path).unlink()
    return stale_pages


def _stale_rendered_files(*, root: Path, output_dir: Path, next_page_names: set[str]) -> list[str]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    rendered = manifest.get("rendered")
    if not isinstance(rendered, list):
        return []
    root_real = root.resolve()
    output_real = output_dir.resolve()
    stale_pages: list[str] = []
    next_reals = {(output_dir / name).resolve() for name in next_page_names}
    for item in rendered:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "")
        if not rel_path:
            continue
        stale_path = root / rel_path
        try:
            stale_real = stale_path.resolve()
            stale_real.relative_to(root_real)
            stale_real.relative_to(output_real)
        except ValueError:
            continue
        if stale_real in next_reals:
            continue
        if stale_path.is_file():
            stale_pages.append(stale_path.relative_to(root).as_posix())
    return sorted(stale_pages)


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
    pages: dict[str, str] = {"INDEX.md": _index_page(root, records, events, by_kind)}
    for kind, filename in PAGE_BY_KIND.items():
        pages[filename] = _kind_page(root, kind, by_kind[kind], _superseded_ids(records), _deprecated_ids(events), events)
    events_by_record = _events_by_record(events)
    superseded_ids = _superseded_ids(records)
    deprecated_ids = _deprecated_ids(events)
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        record_id = str(record.get("id") or "")
        pages[_record_page_name(record_id)] = _record_page(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, events=events_by_record.get(record_id, []))
    for target in _file_targets(records):
        pages[_file_target_page_name(target)] = _file_target_page(root, target, records, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
    for symbol in _symbol_targets(records):
        pages[_symbol_target_page_name(symbol)] = _symbol_target_page(root, symbol, records, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
    pages["history.md"] = _history_page(root, records, events, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
    pages["search-index.json"] = json.dumps(_search_index(root, records, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return pages


def _page_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_page: dict[str, list[dict[str, Any]]] = {"INDEX.md": sorted(records, key=lambda item: str(item.get("id") or ""))}
    for kind, filename in PAGE_BY_KIND.items():
        by_page[filename] = sorted([record for record in records if str(record.get("kind") or "") == kind], key=lambda item: str(item.get("id") or ""))
    by_page["history.md"] = sorted(records, key=lambda item: str(item.get("id") or ""))
    by_page["search-index.json"] = sorted(records, key=lambda item: str(item.get("id") or ""))
    for record in records:
        record_id = str(record.get("id") or "")
        by_page[_record_page_name(record_id)] = [record]
    for target in _file_targets(records):
        by_page[_file_target_page_name(target)] = sorted(
            [record for record in records if target in _record_file_targets(record)],
            key=lambda item: str(item.get("id") or ""),
        )
    for symbol in _symbol_targets(records):
        symbol_id = _symbol_target_id(symbol)
        by_page[_symbol_target_page_name(symbol)] = sorted(
            [record for record in records if symbol_id in {_symbol_target_id(item) for item in _record_symbol_targets(record)}],
            key=lambda item: str(item.get("id") or ""),
        )
    return by_page


def _page_source_bundle(root: Path, name: str, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    refs = _unique_source_refs(records)
    event_ids = [str(event.get("id") or "") for event in events if _event_belongs_to_page(name, event, records)]
    source_statuses = _source_statuses(root, refs)
    bundle = {
        "record_ids": [str(record.get("id") or "") for record in records],
        "source_refs": refs,
        "source_statuses": source_statuses,
        "source_status_counts": _source_status_counts(source_statuses),
        "event_ids": sorted(event_ids),
    }
    bundle["source_bundle_digest"] = digest_data(bundle)
    return bundle


def _source_statuses(root: Path, refs: list[dict[str, Any]]) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    for ref in refs:
        statuses.append(
            {
                "path": str(ref.get("path") or ""),
                "section": str(ref.get("section") or ""),
                "content_sha256": str(ref.get("content_sha256") or ""),
                "status": _source_ref_status(root, ref),
            }
        )
    return sorted(statuses, key=lambda item: (item["path"], item["section"], item["content_sha256"]))


def _source_status_counts(statuses: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in statuses:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


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
    if name == "history.md":
        return True
    record_ids = {str(record.get("id") or "") for record in records}
    return str(event.get("record_id") or "") in record_ids or str(event.get("superseded_by") or "") in record_ids


def _index_page(root: Path, records: list[dict[str, Any]], events: list[dict[str, Any]], by_kind: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "# Knowledge Index",
        "",
        "Non-authoritative generated view. Source records remain under `docs/knowledge/records/`.",
        "",
        "## Pages",
        "",
    ]
    for kind, filename in PAGE_BY_KIND.items():
        label = filename.removesuffix(".md").replace("-", " ").title()
        lines.append(f"- [{label}]({filename}) - {len(by_kind[kind])} records")
    lines.append("- [History](history.md)")
    lines.append("- [Search index](search-index.json)")
    status_groups = _records_by_status(root, records, events)
    lines.extend(["", "## Lifecycle", ""])
    for status in ("reviewed", "stale", "superseded", "deprecated"):
        lines.append(f"- {status}: {len(status_groups.get(status, []))}")
    for status in ("reviewed", "stale", "superseded", "deprecated"):
        items = status_groups.get(status, [])
        if not items:
            continue
        lines.extend(["", f"### {status.title()}", ""])
        for record in items:
            kind = str(record.get("kind") or "")
            filename = _record_page_name(str(record.get("id") or ""))
            title = str(record.get("title") or record.get("id") or "Untitled")
            record_id = str(record.get("id") or "")
            link = filename if filename else ""
            suffix = f" ([{record_id}]({link}))" if link else f" (`{record_id}`)"
            lines.append(f"- {title}{suffix}")
    lines.extend(["", "## Source Bundle", ""])
    lines.append(f"- Records: {len(records)}")
    lines.append(f"- Events: {len(events)}")
    lines.append(f"- Records digest: {digest_data([_record_digest_basis(record) for record in records])}")
    lines.append(f"- Events digest: {digest_data([_event_digest_basis(event) for event in events])}")
    return "\n".join(lines).rstrip() + "\n"


def _records_by_status(root: Path, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    superseded_ids = _superseded_ids(records)
    deprecated_ids = _deprecated_ids(events)
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        groups.setdefault(status, []).append(record)
    for status in groups:
        groups[status] = sorted(groups[status], key=lambda item: str(item.get("id") or ""))
    return groups


def _markdown_anchor(title: str) -> str:
    anchor = "".join(char.lower() if char.isalnum() or char in {" ", "-"} else "" for char in title.strip())
    return "-".join(anchor.split())


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
    current: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        if status == "reviewed":
            current.append(record)
        else:
            historical.append(record)
    lines.extend(["## Current", ""])
    if current:
        for record in sorted(current, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="records/"))
    else:
        lines.append("No current records.")
    lines.extend(["", "## Historical", ""])
    if historical:
        for record in sorted(historical, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="records/"))
    else:
        lines.append("No historical records.")
    return "\n".join(lines).rstrip() + "\n"


def _record_summary_item(root: Path, record: dict[str, Any], *, superseded_ids: set[str], deprecated_ids: set[str], page_prefix: str = "") -> list[str]:
    record_id = str(record.get("id") or "")
    status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
    title = str(record.get("title") or record_id)
    summary = _one_line(str(record.get("claim") or record.get("summary") or ""))
    return [
        f"- [{title}]({page_prefix}{record_id}.md) `{status}` `{record_id}`",
        f"  - {summary}",
    ]


def _record_page(root: Path, record: dict[str, Any], *, superseded_ids: set[str], deprecated_ids: set[str], events: list[dict[str, Any]]) -> str:
    record_id = str(record.get("id") or "")
    status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
    kind = str(record.get("kind") or "")
    kind_page = PAGE_BY_KIND.get(kind, "INDEX.md")
    targets = _record_file_targets(record)
    symbol_targets = _record_symbol_targets(record)
    lines = [
        f"# {record.get('title', record.get('id', 'Untitled'))}",
        "",
        "Non-authoritative generated view. Source records remain under `docs/knowledge/records/`.",
        "",
        "## Lifecycle",
        "",
        f"- Record: `{record_id}`",
        f"- Kind: `{kind}`",
        f"- Status: `{status}`",
        f"- Digest: `{record.get('record_digest', '')}`",
        f"- Kind page: [{kind_page}](../{kind_page})",
    ]
    if record.get("supersedes"):
        links = ", ".join(f"[{item}]({_record_sibling_link(str(item))})" for item in record.get("supersedes", []))
        lines.append(f"- Supersedes: {links}")
    superseded_by = [
        str(event.get("superseded_by") or "")
        for event in events
        if event.get("type") == "superseded" and str(event.get("record_id") or "") == record_id and event.get("superseded_by")
    ]
    if superseded_by:
        links = ", ".join(f"[{item}]({_record_sibling_link(item)})" for item in superseded_by)
        lines.append(f"- Superseded by: {links}")
    if events:
        lines.append(f"- Lifecycle events: `{', '.join(str(event.get('id') or '') for event in events)}`")
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
        "### Applies To",
        "",
    ])
    if targets:
        for target in targets:
            lines.append(f"- File: [{target}](../{_file_target_page_name(target)})")
    if symbol_targets:
        for symbol in symbol_targets:
            lines.append(f"- Symbol: [{_symbol_target_label(symbol)}](../{_symbol_target_page_name(symbol)})")
    if not targets and not symbol_targets:
        lines.append("- No explicit target.")
    lines.extend([
        "",
        "### Origin And Review",
        "",
    ])
    lines.extend(_origin_and_review_lines(record))
    lines.extend([
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
            lines.append(f"- [{ref.get('path', '')}{section}]({_record_source_link(str(ref.get('path') or ''))}) `{digest}` status=`{source_status}`")
    else:
        lines.append("- Missing source refs; do not treat this record as current knowledge.")
    lines.extend(["", "### Event Timeline", ""])
    if events:
        for event in sorted(events, key=lambda item: str(item.get("id") or "")):
            reason = str(event.get("reason") or event.get("review_note") or "")
            suffix = f" - {reason}" if reason else ""
            lines.append(f"- `{event.get('id', '')}` `{event.get('type', '')}`{suffix}")
    else:
        lines.append("- No lifecycle events found.")
    lines.extend(["", "## Navigation", "", f"- [Index](../INDEX.md)", f"- [{kind_page}](../{kind_page})", "- [History](../history.md)"])
    return "\n".join(lines).rstrip() + "\n"


def _origin_and_review_lines(record: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    review = record.get("review") if isinstance(record.get("review"), dict) else {}
    if review:
        lines.append(f"- Reviewed by: `{review.get('reviewed_by', '')}`")
        if review.get("reviewed_at"):
            lines.append(f"- Reviewed at: `{review.get('reviewed_at')}`")
        if review.get("review_note"):
            lines.append(f"- Review note: {review.get('review_note')}")
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
    created_from = record.get("created_from") if isinstance(record.get("created_from"), dict) else {}
    derived = created_from.get("candidate_derived_from") if isinstance(created_from.get("candidate_derived_from"), dict) else {}
    if derived:
        lines.append(f"- Origin kind: `{derived.get('kind', '')}`")
        for key in ("task_id", "verification_artifact", "record_id", "record_digest"):
            if derived.get(key):
                lines.append(f"- {key}: `{derived.get(key)}`")
    if not lines:
        lines.append("- No approval provenance recorded.")
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


def _record_page_name(record_id: str) -> str:
    return f"records/{record_id}.md"


def _record_sibling_link(record_id: str) -> str:
    return f"{record_id}.md"


def _record_source_link(path: str) -> str:
    if path.startswith("docs/"):
        return "../../../" + path.removeprefix("docs/")
    return "../../../" + path


def _file_target_page_name(path: str) -> str:
    return f"targets/files/{quote(path, safe='')}.md"


def _broken_internal_links(*, output_dir: Path, page_name: str, content: str, page_names: set[str]) -> list[str]:
    if not page_name.endswith(".md"):
        return []
    page_path = output_dir / page_name
    output_real = output_dir.resolve()
    broken: list[str] = []
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", content):
        link = match.group(1).split("#", 1)[0]
        if not link or link.startswith(("http://", "https://", "mailto:")):
            continue
        target = (page_path.parent / link).resolve()
        try:
            target.relative_to(output_real)
        except ValueError:
            continue
        rel = target.relative_to(output_real).as_posix()
        if rel not in page_names:
            broken.append(match.group(1))
    return broken


def _file_targets(records: list[dict[str, Any]]) -> list[str]:
    targets: set[str] = set()
    for record in records:
        targets.update(_record_file_targets(record))
    return sorted(targets)


def _symbol_targets(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for record in records:
        for symbol in _record_symbol_targets(record):
            symbol_id = _symbol_target_id(symbol)
            if symbol_id:
                targets[symbol_id] = symbol
    return sorted(targets.values(), key=lambda item: (_symbol_target_id(item), str(item.get("path") or ""), str(item.get("qualified_name") or item.get("name") or "")))


def _record_symbol_targets(record: dict[str, Any]) -> list[dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for item in _record_symbol_target_items(record):
        symbol = _normalize_symbol_target(item)
        if not symbol:
            continue
        targets[_symbol_target_id(symbol)] = symbol
    return sorted(targets.values(), key=lambda item: (_symbol_target_id(item), str(item.get("path") or ""), str(item.get("qualified_name") or item.get("name") or "")))


def _record_symbol_target_items(record: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    applies_to = record.get("applies_to") if isinstance(record.get("applies_to"), dict) else {}
    created_from = record.get("created_from") if isinstance(record.get("created_from"), dict) else {}
    derived = created_from.get("candidate_derived_from") if isinstance(created_from.get("candidate_derived_from"), dict) else {}
    for source in (scope.get("symbols"), applies_to.get("symbols"), derived.get("related_symbols"), derived.get("symbols")):
        if isinstance(source, list):
            items.extend(source)
    return items


def _normalize_symbol_target(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        symbol_id = item.strip()
        return {"id": symbol_id} if symbol_id else {}
    if not isinstance(item, dict):
        return {}
    symbol: dict[str, Any] = {}
    for key in ("id", "provider", "provider_symbol_id", "path", "qualified_name", "name", "kind", "symbol_kind"):
        value = str(item.get(key) or "").strip()
        if value:
            symbol[key] = value
    range_value = item.get("range")
    if isinstance(range_value, dict):
        normalized_range = {key: range_value[key] for key in ("start_line", "start_col", "end_line", "end_col") if key in range_value}
        if normalized_range:
            symbol["range"] = normalized_range
    symbol_id = _symbol_target_id(symbol)
    if not symbol_id:
        return {}
    symbol["id"] = symbol_id
    return symbol


def _symbol_target_id(symbol: dict[str, Any]) -> str:
    explicit = str(symbol.get("id") or "").strip()
    if explicit:
        return explicit
    provider = str(symbol.get("provider") or "").strip()
    provider_symbol_id = str(symbol.get("provider_symbol_id") or "").strip()
    if provider and provider_symbol_id:
        return f"{provider}:{provider_symbol_id}"
    path = str(symbol.get("path") or "").strip()
    name = str(symbol.get("qualified_name") or symbol.get("name") or "").strip()
    if path and name:
        return f"{path}:{name}"
    return ""


def _symbol_target_label(symbol: dict[str, Any]) -> str:
    return str(symbol.get("qualified_name") or symbol.get("name") or symbol.get("provider_symbol_id") or _symbol_target_id(symbol))


def _symbol_target_page_name(symbol_or_id: dict[str, Any] | str) -> str:
    symbol_id = _symbol_target_id(symbol_or_id) if isinstance(symbol_or_id, dict) else str(symbol_or_id)
    return f"targets/symbols/{quote(symbol_id, safe='')}.md"


def _record_file_targets(record: dict[str, Any]) -> list[str]:
    targets: set[str] = set()
    created_from = record.get("created_from") if isinstance(record.get("created_from"), dict) else {}
    derived = created_from.get("candidate_derived_from") if isinstance(created_from.get("candidate_derived_from"), dict) else {}
    changed_files = derived.get("changed_files") if isinstance(derived.get("changed_files"), list) else []
    for item in changed_files:
        path = str(item or "")
        if path and not path.startswith(".repometa/"):
            targets.add(path)
    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    paths = scope.get("paths") if isinstance(scope.get("paths"), list) else []
    for item in paths:
        path = str(item or "")
        if path:
            targets.add(path)
    return sorted(targets)


def _file_target_page(root: Path, target: str, records: list[dict[str, Any]], *, superseded_ids: set[str], deprecated_ids: set[str]) -> str:
    matching = [record for record in records if target in _record_file_targets(record)]
    current = [record for record in matching if _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids) == "reviewed"]
    historical = [record for record in matching if _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids) != "reviewed"]
    lines = [
        f"# Target: {target}",
        "",
        "Non-authoritative generated target page.",
        "",
        "## Current Knowledge",
        "",
    ]
    if current:
        for record in sorted(current, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="../../records/"))
    else:
        lines.append("No current records.")
    lines.extend(["", "## Historical Knowledge", ""])
    if historical:
        for record in sorted(historical, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="../../records/"))
    else:
        lines.append("No historical records.")
    lines.extend(["", "## Navigation", "", "- [Index](../../INDEX.md)", "- [History](../../history.md)"])
    return "\n".join(lines).rstrip() + "\n"


def _symbol_target_page(root: Path, symbol: dict[str, Any], records: list[dict[str, Any]], *, superseded_ids: set[str], deprecated_ids: set[str]) -> str:
    symbol_id = _symbol_target_id(symbol)
    matching = [record for record in records if symbol_id in {_symbol_target_id(item) for item in _record_symbol_targets(record)}]
    current = [record for record in matching if _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids) == "reviewed"]
    historical = [record for record in matching if _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids) != "reviewed"]
    lines = [
        f"# Symbol Target: {_symbol_target_label(symbol)}",
        "",
        "Non-authoritative generated symbol target page.",
        "",
        "## Symbol Identity",
        "",
        f"- id: `{symbol_id}`",
    ]
    for key, label in (
        ("provider", "provider"),
        ("provider_symbol_id", "provider_symbol_id"),
        ("path", "file path"),
        ("qualified_name", "qualified_name"),
        ("name", "name"),
        ("kind", "kind"),
        ("symbol_kind", "symbol_kind"),
    ):
        if symbol.get(key):
            lines.append(f"- {label}: `{symbol[key]}`")
    if isinstance(symbol.get("range"), dict):
        lines.append(f"- range: `{json.dumps(symbol['range'], ensure_ascii=False, sort_keys=True)}`")
    lines.extend(["", "## Current Knowledge", ""])
    if current:
        for record in sorted(current, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="../../records/"))
    else:
        lines.append("No current records.")
    lines.extend(["", "## Historical Knowledge", ""])
    if historical:
        for record in sorted(historical, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids, page_prefix="../../records/"))
    else:
        lines.append("No historical records.")
    lines.extend(["", "## Navigation", "", "- [Index](../../INDEX.md)", "- [History](../../history.md)"])
    return "\n".join(lines).rstrip() + "\n"


def _history_page(root: Path, records: list[dict[str, Any]], events: list[dict[str, Any]], *, superseded_ids: set[str], deprecated_ids: set[str]) -> str:
    lines = [
        "# Knowledge History",
        "",
        "Non-authoritative generated lifecycle view.",
        "",
        "## Records",
        "",
    ]
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        record_id = str(record.get("id") or "")
        lines.append(f"- [{record.get('title', record_id)}](records/{record_id}.md) `{status}` `{record_id}`")
    lines.extend(["", "## Events", ""])
    if events:
        for event in sorted(events, key=lambda item: str(item.get("id") or "")):
            lines.append(f"- `{event.get('id', '')}` `{event.get('type', '')}` record=`{event.get('record_id', '')}` candidate=`{event.get('candidate_id', '')}`")
    else:
        lines.append("No lifecycle events.")
    return "\n".join(lines).rstrip() + "\n"


def _search_index(root: Path, records: list[dict[str, Any]], *, superseded_ids: set[str], deprecated_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        record_id = str(record.get("id") or "")
        rows.append(
            {
                "record_id": record_id,
                "repo_id": str(record.get("repo_id") or ""),
                "kind": str(record.get("kind") or ""),
                "status": _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids),
                "title": str(record.get("title") or ""),
                "claim": str(record.get("claim") or ""),
                "summary": str(record.get("summary") or ""),
                "applies_to": {"files": _record_file_targets(record), "symbols": [_symbol_search_index_entry(symbol) for symbol in _record_symbol_targets(record)], "topics": []},
                "source_paths": sorted(
                    str(ref.get("path") or "")
                    for ref in record.get("source_refs", [])
                    if isinstance(ref, dict) and str(ref.get("path") or "")
                ),
                "page_path": _record_page_name(record_id),
            }
        )
    return rows


def _symbol_search_index_entry(symbol: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": _symbol_target_id(symbol),
        "label": _symbol_target_label(symbol),
        "page_path": _symbol_target_page_name(symbol),
    }
    for key in ("provider", "provider_symbol_id", "path", "qualified_name", "name", "kind", "symbol_kind"):
        if symbol.get(key):
            entry[key] = symbol[key]
    return entry


def _one_line(text: str, *, limit: int = 180) -> str:
    value = " ".join(text.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


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
