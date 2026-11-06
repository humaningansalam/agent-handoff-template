#!/usr/bin/env python3
"""Rebuild and verify the manifest-defined release asset from this Git tree."""
from __future__ import annotations
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
manifest = json.loads((ROOT / "ARTIFACT-MANIFEST.json").read_text(encoding="utf-8"))
assert manifest["status"] == "final"
package, version = manifest["package"], manifest["package_version"]
assert package in {"agent-handoff-template", "repoctl"}
assert version == ("1.0.0" if package == "agent-handoff-template" else "0.16.0")
prefix = f"{package}-{version}"
archive_name = f"{prefix}.tar.gz"
contents: dict[str, bytes] = {}
for entry in manifest["files"]:
    name = entry["path"]
    relative = Path(name)
    assert name and not relative.is_absolute() and ".." not in relative.parts
    assert name not in contents
    source = ROOT / relative
    assert source.is_file() and not source.is_symlink()
    raw = source.read_bytes()
    assert len(raw) == entry["size"]
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    contents[name] = raw
contents["ARTIFACT-MANIFEST.json"] = (ROOT / "ARTIFACT-MANIFEST.json").read_bytes()
out_dir = ROOT / "dist"
out_dir.mkdir(exist_ok=True)
with tempfile.NamedTemporaryFile(dir=out_dir, prefix=f".{prefix}.", suffix=".tmp", delete=False) as output:
    temporary = Path(output.name)
    try:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for name, raw in sorted(contents.items()):
                    info = tarfile.TarInfo(f"{prefix}/{name}")
                    info.size = len(raw)
                    info.mode = 0o755 if name == "scripts/repoctl" else 0o644
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.pax_headers = {}
                    archive.addfile(info, io.BytesIO(raw))
        os.replace(temporary, out_dir / archive_name)
    finally:
        temporary.unlink(missing_ok=True)
actual = hashlib.sha256((out_dir / archive_name).read_bytes()).hexdigest()
lines = (ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
expected = [line.split()[0] for line in lines if line.split()[-1] == archive_name]
assert len(expected) == 1 and actual == expected[0], f"release SHA mismatch: {actual} != {expected}"
print(f"{actual}  {archive_name}")
