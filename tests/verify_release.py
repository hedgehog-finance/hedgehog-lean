"""Reject incomplete or mismatched multi-platform releases before publication."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
for target in ("darwin-arm64", "darwin-x64", "win32-x64", "linux-x64"):
    manifest = json.loads((root / (target + ".json")).read_text())
    assert manifest["target"] == target and manifest["version"] == "1.0.0"
    assert manifest["sourceCommit"] == "f696c556828e9b3ea6695cb4ac41ff233ba5f430"
    assert manifest["filename"] == f"lean-1.0.0-{target}.tar.gz"
    archive = root / manifest["filename"]
    assert archive.stat().st_size == manifest["size"]
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    assert digest.hexdigest() == manifest["sha256"]
print("All four release archives verified")
