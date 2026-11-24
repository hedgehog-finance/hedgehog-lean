"""Reject incomplete or mismatched multi-platform releases before publication."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
packages = []
for target in ("darwin-arm64", "darwin-x64", "win32-x64", "linux-x64"):
    manifest = json.loads((root / (target + ".json")).read_text())
    assert manifest["target"] == target and manifest["version"] == "1.0.1"
    assert manifest["sourceCommit"] == "f696c556828e9b3ea6695cb4ac41ff233ba5f430"
    assert manifest["filename"] == f"lean-1.0.1-{target}.tar.gz"
    archive = root / manifest["filename"]
    assert archive.stat().st_size == manifest["size"]
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    assert digest.hexdigest() == manifest["sha256"]
    packages.append(manifest)
if os.environ.get("GITHUB_SHA"):
    commit, run = os.environ["GITHUB_SHA"], os.environ["GITHUB_RUN_ID"]
    assert re.fullmatch(r"[a-f0-9]{40}", commit) and run.isdecimal()
    (root / "BUILD_PROVENANCE.json").write_text(json.dumps({
        "version": "1.0.1", "integrationCommit": commit,
        "buildRun": f"https://github.com/hedgehog-finance/hedgehog-lean/actions/runs/{run}",
        "verification": ["relocated-native-backtests", "market-rules-and-volume", "durable-cancellation", "paper-cancel-and-owner-exit", "file-feed-partial-writes"],
        "packages": packages,
    }, indent=2) + "\n")
print("All four release archives verified")
