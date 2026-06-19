from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .markdown import find_section
from .tasks import LIVE, Problem, Task, live_tasks

BOARD_ITEM_RE = re.compile(r"^- (docs/tasks/T-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*\.md)$")


@dataclass(frozen=True)
class BacklogItem:
    id: str
    title: str
    raw: str
    line_start: int
    line_end: int
    start: int
    end: int

    def to_dict(self) -> dict[str, str | int]:
        return {"id": self.id, "title": self.title, "raw": self.raw, "line_start": self.line_start, "line_end": self.line_end}


def parse_board(text: str) -> list[str]:
    section = find_section(text, "Board")
    body = text[section.body_start : section.end]
    items: list[str] = []
    for line in body.splitlines():
        match = BOARD_ITEM_RE.match(line.strip())
        if match:
            items.append(match.group(1))
    return items


def board_format_problems(text: str) -> list[Problem]:
    section = find_section(text, "Board")
    body = text[section.body_start : section.end]
    problems: list[Problem] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.endswith("-->"):
            continue
        if stripped.startswith("-") and not BOARD_ITEM_RE.match(stripped):
            problems.append(Problem("error", "invalid_board_item", "Board items must be '- docs/tasks/T-...--slug.md' only", "docs/BOARD.md"))
    return problems


def check_board(root: Path, board_paths: list[str], tasks: list[Task], board_text: str) -> list[Problem]:
    problems = board_format_problems(board_text)
    task_by_rel = {task.rel_path: task for task in tasks if not task.archived}
    live_paths = {task.rel_path for task in live_tasks(tasks)}
    board_set = set(board_paths)
    for path in sorted(board_set - set(task_by_rel)):
        problems.append(Problem("error", "board_missing_file", "Board item points to missing task file", path))
    for path in sorted(live_paths - board_set):
        problems.append(Problem("error", "board_missing_live_task", "live task is missing from Board", path))
    for path in sorted(board_set & set(task_by_rel)):
        task = task_by_rel[path]
        if task.status not in LIVE:
            problems.append(Problem("error", "board_non_live_task", "Board contains done/canceled task", path))
    return problems


def render_board(board_text: str, live_paths: list[str]) -> str:
    section = find_section(board_text, "Board")
    body = "".join(f"- {path}\n" for path in sorted(live_paths))
    return board_text[: section.body_start] + "\n" + body + board_text[section.end :]


def _backlog_id(raw: str) -> str:
    return "BL-" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def read_backlog_items(board_text: str) -> list[BacklogItem]:
    section = find_section(board_text, "Backlog")
    items: list[BacklogItem] = []
    current_start: int | None = None
    current_end: int | None = None
    current_line_start = 0
    current_line_end = 0
    offset = section.body_start
    line_no = board_text.count("\n", 0, section.body_start) + 1

    def flush() -> None:
        if current_start is None or current_end is None:
            return
        block = board_text[current_start:current_end]
        raw = block.rstrip("\n")
        if not raw:
            return
        first = raw.splitlines()[0].strip()
        title = first[2:].strip() if first.startswith("- ") else first
        line_end = current_line_start + len(raw.splitlines()) - 1
        items.append(BacklogItem(_backlog_id(raw), title, raw, current_line_start, line_end, current_start, current_end))

    for line in board_text[section.body_start : section.end].splitlines(keepends=True):
        if line.startswith("- ") and line.strip():
            flush()
            current_start = offset
            current_end = offset + len(line)
            current_line_start = line_no
            current_line_end = line_no
        elif current_start is not None and (line.startswith((" ", "\t")) or not line.strip()):
            current_end = offset + len(line)
            current_line_end = line_no
        elif current_start is not None:
            flush()
            current_start = None
            current_end = None
        offset += len(line)
        line_no += 1
    flush()
    return items


def backlog_warnings(items: list[BacklogItem]) -> list[dict[str, str]]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.id] = counts.get(item.id, 0) + 1
    return [
        {"code": "duplicate_backlog_id", "message": f"Backlog raw block id is ambiguous: {item_id}"}
        for item_id, count in sorted(counts.items())
        if count > 1
    ]


def resolve_backlog_item(board_text: str, backlog_id: str) -> BacklogItem:
    matches = [item for item in read_backlog_items(board_text) if item.id == backlog_id]
    if not matches:
        from .io import RepoctlError

        raise RepoctlError(f"backlog item not found: {backlog_id}", code="backlog_not_found")
    if len(matches) > 1:
        from .io import RepoctlError

        raise RepoctlError(f"backlog item id is ambiguous: {backlog_id}", code="duplicate_backlog_id")
    return matches[0]


def append_backlog_item(board_text: str, title: str, body: str = "") -> str:
    section = find_section(board_text, "Backlog")
    raw = f"- {title.strip()}"
    body = body.strip("\n")
    if body:
        raw += "\n" + "\n".join(f"  {line}" if line else "" for line in body.splitlines())
    insert = raw + "\n"
    prefix = board_text[: section.end]
    suffix = board_text[section.end :]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix[section.body_start :] and not prefix.endswith("\n\n"):
        insert = "\n" + insert
    return prefix + insert + suffix


def remove_backlog_item(board_text: str, backlog_id: str) -> tuple[str, BacklogItem]:
    item = resolve_backlog_item(board_text, backlog_id)
    return board_text[: item.start] + board_text[item.end :], item
