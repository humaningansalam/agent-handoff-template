from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .graph_model import digest_data
from .io import atomic_write
from .knowledge_candidates import (
    KnowledgeSourceResolutionStatus,
    knowledge_sources_current,
    resolved_knowledge_source_refs,
)
from .knowledge_projection import (
    load_knowledge_projection,
    verify_current_knowledge_projection,
)
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
    records, events, empty_without_projection, projection_problems = _current_render_inputs(
        root,
        repo_id=repo_id,
    )
    if projection_problems:
        return {
            "schema": "repoctl.knowledge.render",
            "schema_version": 1,
            "repo_id": repo_id,
            "authoritative": False,
            "output": output_rel,
            "record_count": len(records),
            "event_count": len(events),
            "event_checks": {"error_count": len(projection_problems)},
            "rendered": [],
        }, projection_problems
    pages = _pages(root, output_dir, records, events)
    page_records = _page_records(records)
    rendered = _rendered_page_entries(root=root, output_dir=output_dir, pages=pages, page_records=page_records, events=events)
    rendered = sorted(rendered, key=lambda item: item["path"])
    render_digest = digest_data({"rendered": rendered})
    manifest, manifest_digest = _render_manifest(repo_id=repo_id, output_rel=output_rel, record_count=len(records), event_count=len(events), render_digest=render_digest, rendered=rendered)
    manifest_path = output_dir / "manifest.json"
    if check:
        if empty_without_projection and not manifest_path.exists():
            check_problems: list[Problem] = []
            check_data = {
                "current": True,
                "status": "empty_not_initialized",
                "missing_pages": [],
                "stale_pages": [],
                "unreadable_pages": [],
                "broken_links": [],
                "stale_owned_pages": [],
                "next_actions": ["Approve reviewed knowledge records before rendering a durable llmwiki view."],
            }
        else:
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


def _current_render_inputs(
    root: Path,
    *,
    repo_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, list[Problem]]:
    """Load the bounded hot set without enumerating immutable history."""

    projection, problems = load_knowledge_projection(root, repo_id=repo_id)
    if problems:
        empty_without_projection = (
            not (root / "docs/knowledge/records").exists()
            and all(
                problem.code == "knowledge_projection_unavailable"
                and problem.cause_code == "missing"
                for problem in problems
            )
        )
        if empty_without_projection:
            return [], [], True, []
        return [], [], False, problems
    projection, problems = verify_current_knowledge_projection(
        root,
        repo_id=repo_id,
        projection=projection,
    )
    if problems:
        return [], [], False, problems
    records = [
        dict(head["record"])
        for head in projection.get("heads", [])
        if isinstance(head, dict) and isinstance(head.get("record"), dict)
    ]
    events_by_id: dict[str, dict[str, Any]] = {}
    for head in projection.get("heads", []):
        if not isinstance(head, dict):
            continue
        binding_events = head.get("binding_events")
        if not isinstance(binding_events, list):
            continue
        for event in binding_events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            if event_id:
                events_by_id[event_id] = dict(event)
    return records, [events_by_id[event_id] for event_id in sorted(events_by_id)], False, []


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


def _pages(root: Path, output_dir: Path, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, str]:
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in PAGE_BY_KIND}
    for record in records:
        kind = str(record.get("kind") or "")
        if kind in by_kind:
            by_kind[kind].append(record)
    pages: dict[str, str] = {"INDEX.md": _index_page(root, records, events, by_kind)}
    for kind, filename in PAGE_BY_KIND.items():
        pages[filename] = _kind_page(root, kind, by_kind[kind])
    events_by_record = _events_by_record(events)
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        record_id = str(record.get("id") or "")
        pages[_record_page_name(record_id)] = _record_page(
            root,
            output_dir,
            record,
            events=events_by_record.get(record_id, []),
        )
    for target in _file_targets(records):
        pages[_file_target_page_name(target)] = _file_target_page(root, target, records)
    for symbol in _symbol_targets(records):
        pages[_symbol_target_page_name(symbol)] = _symbol_target_page(root, symbol, records)
    pages["search-index.json"] = json.dumps(_search_index(root, records), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return pages


def _page_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_page: dict[str, list[dict[str, Any]]] = {"INDEX.md": sorted(records, key=lambda item: str(item.get("id") or ""))}
    for kind, filename in PAGE_BY_KIND.items():
        by_page[filename] = sorted([record for record in records if str(record.get("kind") or "") == kind], key=lambda item: str(item.get("id") or ""))
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
    declared_refs = _unique_declared_source_refs(records)
    resolved_refs = _unique_resolved_source_refs(root, records)
    event_ids = [str(event.get("id") or "") for event in events if _event_belongs_to_page(name, event, records)]
    source_statuses = _source_statuses(resolved_refs)
    bundle = {
        "record_ids": [str(record.get("id") or "") for record in records],
        "source_refs": declared_refs,
        "resolved_source_refs": resolved_refs,
        "source_statuses": source_statuses,
        "source_status_counts": _source_status_counts(source_statuses),
        "event_ids": sorted(event_ids),
    }
    bundle["source_bundle_digest"] = digest_data(bundle)
    return bundle


def _source_statuses(refs: list[dict[str, Any]]) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    for ref in refs:
        status = {
            "path": str(ref.get("path") or ""),
            "declared_path": str(ref.get("declared_path") or ref.get("path") or ""),
            "resolved_path": str(ref.get("resolved_path") or ref.get("path") or ""),
            "section": str(ref.get("section") or ""),
            "content_sha256": str(ref.get("content_sha256") or ""),
            "status": _source_ref_status(ref),
        }
        if ref.get("resolution_cause_code"):
            status["cause_code"] = str(ref["resolution_cause_code"])
        statuses.append(status)
    return sorted(statuses, key=lambda item: (item["path"], item["section"], item["content_sha256"]))


def _source_status_counts(statuses: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in statuses:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _unique_declared_source_refs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for record in records:
        source_refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            refs[key] = ref
    return [refs[key] for key in sorted(refs)]


def _unique_resolved_source_refs(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for record in records:
        for ref in resolved_knowledge_source_refs(root, record):
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
    lines.append("- [Search index](search-index.json)")
    status_groups = _records_by_status(root, records)
    lines.extend(["", "## Lifecycle", ""])
    for status in ("reviewed", "stale"):
        lines.append(f"- {status}: {len(status_groups.get(status, []))}")
    for status in ("reviewed", "stale"):
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


def _records_by_status(root: Path, records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        status = _derived_status(root, record)
        groups.setdefault(status, []).append(record)
    for status in groups:
        groups[status] = sorted(groups[status], key=lambda item: str(item.get("id") or ""))
    return groups


def _kind_page(root: Path, kind: str, records: list[dict[str, Any]]) -> str:
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
    lines.extend(["## Current Heads", ""])
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        lines.extend(_record_summary_item(root, record, page_prefix="records/"))
    return "\n".join(lines).rstrip() + "\n"


def _record_summary_item(root: Path, record: dict[str, Any], *, page_prefix: str = "") -> list[str]:
    record_id = str(record.get("id") or "")
    status = _derived_status(root, record)
    title = str(record.get("title") or record_id)
    summary = _one_line(str(record.get("claim") or record.get("summary") or ""))
    return [
        f"- [{title}]({page_prefix}{record_id}.md) `{status}` `{record_id}`",
        f"  - {summary}",
    ]


def _record_page(root: Path, output_dir: Path, record: dict[str, Any], *, events: list[dict[str, Any]]) -> str:
    record_id = str(record.get("id") or "")
    status = _derived_status(root, record)
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
        record_ids = ", ".join(f"`{item}`" for item in record.get("supersedes", []))
        lines.append(f"- Supersedes: {record_ids}")
    superseded_by = [
        str(event.get("superseded_by") or "")
        for event in events
        if event.get("type") == "superseded" and str(event.get("record_id") or "") == record_id and event.get("superseded_by")
    ]
    if superseded_by:
        record_ids = ", ".join(f"`{item}`" for item in superseded_by)
        lines.append(f"- Superseded by: {record_ids}")
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
    refs = resolved_knowledge_source_refs(root, record)
    page_name = _record_page_name(record_id)
    if isinstance(refs, list) and refs:
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            section = f"#{ref.get('section')}" if ref.get("section") else ""
            digest = ref.get("content_sha256", "")
            source_status = _source_ref_status(ref)
            declared = str(ref.get("declared_path") or "")
            declared_suffix = f" declared=`{declared}`" if declared and declared != str(ref.get("path") or "") else ""
            source_path = str(ref.get("path") or "")
            source_link = (
                _record_source_link(root, output_dir, page_name, source_path)
                if source_status
                in {
                    KnowledgeSourceResolutionStatus.CURRENT.value,
                    KnowledgeSourceResolutionStatus.RELOCATED.value,
                    KnowledgeSourceResolutionStatus.DIGEST_MISMATCH.value,
                }
                else ""
            )
            source_label = f"[{source_path}{section}]({source_link})" if source_link else f"`{source_path}{section}`"
            cause = str(ref.get("resolution_cause_code") or "")
            cause_suffix = f" cause=`{cause}`" if cause else ""
            lines.append(f"- {source_label} `{digest}` status=`{source_status}`{declared_suffix}{cause_suffix}")
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
    lines.extend(["", "## Navigation", "", "- [Index](../INDEX.md)", f"- [{kind_page}](../{kind_page})"])
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


def _record_source_link(root: Path, output_dir: Path, page_name: str, path: str) -> str:
    if (
        not path
        or path != path.strip().replace("\\", "/")
        or Path(path).is_absolute()
        or ".." in Path(path).parts
    ):
        return ""
    target = root / path
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return ""
    relative = Path(os.path.relpath(target, start=(output_dir / page_name).parent)).as_posix()
    return quote(relative, safe="/.:@-")


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
    for source in (scope.get("symbols"), applies_to.get("symbols"), derived.get("symbols")):
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


def _file_target_page(root: Path, target: str, records: list[dict[str, Any]]) -> str:
    matching = [record for record in records if target in _record_file_targets(record)]
    lines = [
        f"# Target: {target}",
        "",
        "Non-authoritative generated target page.",
        "",
        "## Current Knowledge",
        "",
    ]
    if matching:
        for record in sorted(matching, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, page_prefix="../../records/"))
    else:
        lines.append("No current records.")
    lines.extend(["", "## Navigation", "", "- [Index](../../INDEX.md)"])
    return "\n".join(lines).rstrip() + "\n"


def _symbol_target_page(root: Path, symbol: dict[str, Any], records: list[dict[str, Any]]) -> str:
    symbol_id = _symbol_target_id(symbol)
    matching = [record for record in records if symbol_id in {_symbol_target_id(item) for item in _record_symbol_targets(record)}]
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
    if matching:
        for record in sorted(matching, key=lambda item: str(item.get("id") or "")):
            lines.extend(_record_summary_item(root, record, page_prefix="../../records/"))
    else:
        lines.append("No current records.")
    lines.extend(["", "## Navigation", "", "- [Index](../../INDEX.md)"])
    return "\n".join(lines).rstrip() + "\n"


def _search_index(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        record_id = str(record.get("id") or "")
        rows.append(
            {
                "record_id": record_id,
                "repo_id": str(record.get("repo_id") or ""),
                "kind": str(record.get("kind") or ""),
                "status": _derived_status(root, record),
                "title": str(record.get("title") or ""),
                "claim": str(record.get("claim") or ""),
                "summary": str(record.get("summary") or ""),
                "applies_to": {"files": _record_file_targets(record), "symbols": [_symbol_search_index_entry(symbol) for symbol in _record_symbol_targets(record)], "topics": []},
                "source_paths": sorted(
                    str(ref.get("path") or "")
                    for ref in resolved_knowledge_source_refs(root, record)
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


def _source_ref_status(ref: dict[str, Any]) -> str:
    projected_status = str(ref.get("resolution_status") or "")
    if projected_status in {status.value for status in KnowledgeSourceResolutionStatus}:
        return projected_status
    return KnowledgeSourceResolutionStatus.INVALID_IDENTITY.value


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


def _derived_status(root: Path, record: dict[str, Any]) -> str:
    if _has_digest_drift(root, record):
        return "stale"
    return str(record.get("status") or "")


def _has_digest_drift(root: Path, record: dict[str, Any]) -> bool:
    return not knowledge_sources_current(root, record)
