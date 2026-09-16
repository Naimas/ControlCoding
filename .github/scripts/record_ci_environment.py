"""Write explicit CI runtime/input diagnostics; never dump process environment."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["git", "--no-optional-locks", "rev-parse", "HEAD"],
                            cwd=root, capture_output=True, text=True, check=True)
    inputs = ["controlcoding.verification.json", ".github/requirements-ci.lock",
              ".github/workflows/controlcoding-verification.yml"]
    payload = {
        "schemaVersion": 1,
        "commit": result.stdout.strip(),
        "python": {"executable": sys.executable, "version": sys.version},
        "platform": platform.platform(), "machine": platform.machine(),
        "sqlite": sqlite3.sqlite_version,
        "inputs": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in inputs},
        "distributions": sorted(
            ({"name": d.metadata["Name"], "version": d.version}
             for d in importlib.metadata.distributions() if d.metadata["Name"]),
            key=lambda d: (d["name"].lower(), d["version"])),
        "github": {key: os.environ.get(key, "") for key in (
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_SHA",
            "GITHUB_REF", "GITHUB_EVENT_NAME", "RUNNER_OS", "RUNNER_ARCH")},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
