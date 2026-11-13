#!/usr/bin/env python3
"""Verify the pinned AutoResearchExam pilot tasks against a local source checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


MANIFEST = Path(__file__).with_name("task-selection.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source: Path) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = source.resolve()
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if revision != manifest["source_revision"]:
        raise ValueError(
            f"source revision {revision} differs from pinned {manifest['source_revision']}"
        )

    verified = []
    for task in manifest["tasks"]:
        task_dir = source / task["task_id"]
        task_file = task_dir / "task.toml"
        instruction_file = task_dir / "instruction.md"
        if not task_file.is_file() or not instruction_file.is_file():
            raise ValueError(f"{task['task_id']}: task.toml or instruction.md is missing")
        task_digest = sha256(task_file)
        instruction_digest = sha256(instruction_file)
        if task_digest != task["task_sha256"]:
            raise ValueError(f"{task['task_id']}: task.toml SHA-256 mismatch")
        if instruction_digest != task["instruction_sha256"]:
            raise ValueError(f"{task['task_id']}: instruction.md SHA-256 mismatch")
        config = tomllib.loads(task_file.read_text(encoding="utf-8"))
        if config.get("task", {}).get("version") != task["version"]:
            raise ValueError(f"{task['task_id']}: task version mismatch")
        verified.append({
            "task_id": task["task_id"],
            "version": task["version"],
            "task_sha256": task_digest,
            "instruction_sha256": instruction_digest,
        })
    return {
        "schema": "bananloop-autoresearchexam-task-selection-verification-v1",
        "source_revision": revision,
        "task_count": len(verified),
        "tasks": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Local AutoResearchExam source checkout")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    try:
        result = verify(args.source)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"selection verification failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Verified {result['task_count']} pinned tasks at {result['source_revision']}.")
        for task in result["tasks"]:
            print(f"{task['task_id']}@{task['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
