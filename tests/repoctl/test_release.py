from __future__ import annotations

import json
import tarfile
from pathlib import Path

from tools.repoctl.release import build_release_archive


def test_build_release_archive_uses_manifest_managed_paths(tmp_path: Path) -> None:
    root = tmp_path / "source"
    out = tmp_path / "dist"
    (root / "scripts").mkdir(parents=True)
    (root / "docs/workflows").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("tool\n", encoding="utf-8")
    (root / "docs/workflows/repo-metadata.md").write_text("workflow\n", encoding="utf-8")
    (root / "repoctl-upgrade-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "agent-workspace-control-plane",
                "version": "0.1.0",
                "replace_paths": ["scripts/repoctl"],
                "create_paths": ["docs/workflows/repo-metadata.md"],
                "preserve_paths": ["repo/**", "docs/BOARD.md"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    archive_path = build_release_archive(root, out)

    assert archive_path.name == "agent-workspace-control-plane-0.1.0.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "agent-workspace-control-plane-0.1.0/repoctl-upgrade-manifest.json" in names
    assert "agent-workspace-control-plane-0.1.0/scripts/repoctl" in names
    assert "agent-workspace-control-plane-0.1.0/docs/workflows/repo-metadata.md" in names
