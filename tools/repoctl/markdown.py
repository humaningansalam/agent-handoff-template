from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .io import RepoctlError


@dataclass(frozen=True)
class SectionRange:
    heading: str
    start: int
    body_start: int
    end: int


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip('"').strip("'") for part in inner.split(",")]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        raise RepoctlError("frontmatter closing delimiter missing")
    raw = text[4:end]
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            data[key] = _parse_scalar(value)
    return data, text[end + len("\n---\n") :]


def replace_frontmatter_line(text: str, key: str, value: str) -> str:
    if not text.startswith("---\n"):
        raise RepoctlError("frontmatter missing")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise RepoctlError("frontmatter closing delimiter missing")
    front = text[:end]
    rest = text[end:]
    lines = front.splitlines()
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}"
            return "\n".join(lines) + rest
    raise RepoctlError(f"frontmatter key missing: {key}")


def _heading_ranges(text: str) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    offset = 0
    in_fence = False
    fence_marker = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
        if not in_fence and line.startswith("## "):
            ranges.append((line.strip(), offset, offset + len(line)))
        offset += len(line)
    return ranges


def find_section(text: str, heading: str) -> SectionRange:
    headings = _heading_ranges(text)
    wanted = f"## {heading}" if not heading.startswith("## ") else heading
    for index, (found, start, body_start) in enumerate(headings):
        if found == wanted:
            end = headings[index + 1][1] if index + 1 < len(headings) else len(text)
            return SectionRange(wanted, start, body_start, end)
    raise RepoctlError(f"section missing: {wanted}", code="missing_section")


def replace_section(text: str, heading: str, body: str) -> str:
    section = find_section(text, heading)
    normalized = body if body.endswith("\n") else body + "\n"
    suffix = text[section.end :]
    if suffix and not normalized.endswith("\n\n"):
        normalized += "\n"
    return text[: section.body_start] + "\n" + normalized + suffix


def append_section_entry(text: str, heading: str, entry: str) -> str:
    section = find_section(text, heading)
    body = text[section.body_start : section.end].rstrip()
    suffix = "\n" if body else ""
    entry_text = body + suffix + entry.rstrip() + "\n"
    if text[section.end :] and not entry_text.endswith("\n\n"):
        entry_text += "\n"
    return text[: section.body_start] + entry_text + text[section.end :]
