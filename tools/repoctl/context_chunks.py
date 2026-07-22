from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .context_model import ContextSectionKind, ContextSourceRef
from .document_roles import DocumentRole, source_document_role


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class DocumentChunk:
    source_ref: ContextSourceRef
    text: str
    title: str
    document_role: DocumentRole = DocumentRole.UNSPECIFIED


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_markdown_file(
    root: Path,
    path: Path,
    *,
    kind: str = "document",
    document_role: DocumentRole = DocumentRole.UNSPECIFIED,
) -> list[DocumentChunk]:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(root).as_posix()
    role = source_document_role(kind=kind, path=rel, assigned=document_role)
    digest = sha256_text(text)
    lines = text.splitlines()
    chunks: list[DocumentChunk] = []
    section_kind = ContextSectionKind.TASK if kind == "task_artifact" else ContextSectionKind.DOCUMENT
    current_heading = path.name
    start_index = 0

    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        if index > start_index:
            chunks.append(
                _chunk(
                    rel,
                    kind,
                    current_heading,
                    lines[start_index:index],
                    start_index + 1,
                    index,
                    digest,
                    section_kind=section_kind,
                    document_role=role,
                )
            )
        current_heading = match.group(2).strip()
        start_index = index

    if lines:
        chunks.append(
            _chunk(
                rel,
                kind,
                current_heading,
                lines[start_index:],
                start_index + 1,
                len(lines),
                digest,
                section_kind=section_kind,
                document_role=role,
            )
        )
    elif not chunks:
        chunks.append(
            _chunk(
                rel,
                kind,
                current_heading,
                [],
                1,
                1,
                digest,
                section_kind=section_kind,
                document_role=role,
            )
        )
    return [chunk for chunk in chunks if chunk.text.strip()]


def chunk_text_source(
    root: Path,
    rel: str,
    text: str,
    *,
    kind: str,
    section: str,
    section_kind: ContextSectionKind = ContextSectionKind.UNSPECIFIED,
    document_role: DocumentRole = DocumentRole.UNSPECIFIED,
) -> DocumentChunk:
    digest = sha256_text(text)
    line_count = max(1, len(text.splitlines()))
    return _chunk(
        rel,
        kind,
        section,
        text.splitlines(),
        1,
        line_count,
        digest,
        section_kind=section_kind,
        document_role=source_document_role(kind=kind, path=rel, assigned=document_role),
    )


def _chunk(
    rel: str,
    kind: str,
    heading: str,
    lines: list[str],
    line_start: int,
    line_end: int,
    digest: str,
    *,
    section_kind: ContextSectionKind,
    document_role: DocumentRole,
) -> DocumentChunk:
    body = "\n".join(lines).strip()
    return DocumentChunk(
        source_ref=ContextSourceRef(
            kind=kind,
            path=rel,
            section=heading,
            section_kind=section_kind,
            line_start=line_start,
            line_end=line_end,
            content_sha256=digest,
        ),
        text=body,
        title=heading,
        document_role=document_role,
    )
