from __future__ import annotations

import json
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
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    rendered: list[dict[str, Any]] = []
    pages = _pages(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        path = output_dir / name
        atomic_write(path, content)
        rendered.append({"path": path.relative_to(root).as_posix(), "digest": digest_data({"content": content})})
    return {
        "schema": "repoctl.knowledge.render",
        "schema_version": 1,
        "repo_id": repo_id,
        "authoritative": False,
        "output": output_dir.relative_to(root).as_posix(),
        "record_count": len(records),
        "rendered": sorted(rendered, key=lambda item: item["path"]),
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


def _pages(records: list[dict[str, Any]]) -> dict[str, str]:
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in PAGE_BY_KIND}
    for record in records:
        kind = str(record.get("kind") or "")
        if kind in by_kind:
            by_kind[kind].append(record)
    pages: dict[str, str] = {"INDEX.md": _index_page(records, by_kind)}
    for kind, filename in PAGE_BY_KIND.items():
        pages[filename] = _kind_page(kind, by_kind[kind])
    return pages


def _index_page(records: list[dict[str, Any]], by_kind: dict[str, list[dict[str, Any]]]) -> str:
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
    lines.append(f"- Records digest: {digest_data([_record_digest_basis(record) for record in records])}")
    return "\n".join(lines).rstrip() + "\n"


def _kind_page(kind: str, records: list[dict[str, Any]]) -> str:
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
    for record in sorted(records, key=lambda item: str(item.get("id") or "")):
        lines.extend(_record_section(record))
    return "\n".join(lines).rstrip() + "\n"


def _record_section(record: dict[str, Any]) -> list[str]:
    lines = [
        f"## {record.get('title', record.get('id', 'Untitled'))}",
        "",
        f"- Record: `{record.get('id', '')}`",
        f"- Status: `{record.get('status', '')}`",
        f"- Digest: `{record.get('record_digest', '')}`",
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
    ]
    refs = record.get("source_refs", [])
    if isinstance(refs, list) and refs:
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            section = f"#{ref.get('section')}" if ref.get("section") else ""
            digest = ref.get("content_sha256", "")
            lines.append(f"- `{ref.get('path', '')}{section}` `{digest}`")
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
