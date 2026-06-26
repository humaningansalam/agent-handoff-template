from __future__ import annotations
from tests.repoctl.workspace.test_check import write_workspace

import json
from hashlib import sha256
from pathlib import Path

from tools.repoctl.cli import main



def test_backlog_list_returns_freeform_items_without_interpreting_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    title = "Add percentage discount support"
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n## Backlog\n\n"
        f"- {title}\n"
        "  - Area: backend\n"
        "  - Repo ref: repo\n"
        "  - Likely files: `repos/src/pricing.py`, `repos/tests/test_pricing.py`\n"
        "  - Expected behavior: add apply_discount and reject invalid percentages\n"
        "  - Validation: `cd repos && python -m unittest tests/test_pricing.py`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    raw = "\n".join(
        [
            f"- {title}",
            "  - Area: backend",
            "  - Repo ref: repo",
            "  - Likely files: `repos/src/pricing.py`, `repos/tests/test_pricing.py`",
            "  - Expected behavior: add apply_discount and reject invalid percentages",
            "  - Validation: `cd repos && python -m unittest tests/test_pricing.py`",
        ]
    )
    assert payload["ok"] is True
    assert payload["command"] == "backlog list"
    assert payload["warnings"] == []
    assert payload["data"]["items"] == [
        {"id": "BL-" + sha256(raw.encode("utf-8")).hexdigest()[:12], "title": title, "raw": raw, "line_start": 7, "line_end": 12}
    ]


def test_backlog_add_show_remove_manage_raw_blocks(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n\n<!-- backlog -->\n", encoding="utf-8")
    body = tmp_path / "backlog.md"
    body.write_text("Area: backend\nLikely files: free text only\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Add discount support", "--body-file", str(body), "--json"]) == 0
    added = json.loads(capsys.readouterr().out)["data"]["item"]
    assert added["raw"] == "- Add discount support\n  Area: backend\n  Likely files: free text only"

    assert main(["backlog", "show", added["id"], "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)["data"]["item"]
    assert shown == added

    assert main(["backlog", "remove", added["id"], "--json"]) == 0
    removed = json.loads(capsys.readouterr().out)["data"]["removed"]
    assert removed == added
    board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert "Add discount support" not in board
    assert "- docs/tasks/T-20260609184046Z--alpha.md" in board.split("## Board", 1)[1].split("## Backlog", 1)[0]


def test_backlog_add_rejects_multiline_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Bad\nTitle", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_title"


def test_backlog_add_body_file_keeps_bullets_inside_raw_block(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("- looks like another item\nplain note\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Parent backlog", "--body-file", str(body), "--json"]) == 0

    added = json.loads(capsys.readouterr().out)["data"]["item"]
    assert added["raw"] == "- Parent backlog\n  - looks like another item\n  plain note"
    assert main(["backlog", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["data"]["items"]) == 1
    assert payload["data"]["items"][0] == added


def test_backlog_list_does_not_attach_unindented_comments_to_raw_block(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n## Backlog\n\n- First item\n<!-- separator comment -->\n- Second item\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["raw"] for item in payload["data"]["items"]] == ["- First item", "- Second item"]
    assert payload["data"]["items"][0]["line_end"] == payload["data"]["items"][0]["line_start"]


def test_backlog_duplicate_raw_blocks_warn_and_cannot_be_removed_by_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n\n- Duplicate item\n- Duplicate item\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    backlog_id = payload["data"]["items"][0]["id"]
    assert payload["data"]["items"][0]["line_start"] == payload["data"]["items"][0]["line_end"]
    assert payload["warnings"] == [{"code": "duplicate_backlog_id", "message": f"Backlog raw block id is ambiguous: {backlog_id}"}]

    assert main(["backlog", "remove", backlog_id, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["problems"][0]["code"] == "duplicate_backlog_id"

