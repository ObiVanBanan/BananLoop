#!/usr/bin/env python3
"""Create a Harbor task overlay that cannot collect ARE grader artifacts.

The source task declares a grader artifact and a verifier collection hook. Both
run outside Harbor's `--disable-verification` switch, so the overlay removes
them while preserving the task instruction and container environment. The
overlay is for BananLoop's public-MCP path, not an official Harbor score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SELECTION = Path(__file__).with_name("task-selection.json")
EXPECTED_ARTIFACT = {"source": "/var/lib/are", "service": "grader"}
REVIEWED_COLLECT_TIMEOUTS = {
    "budgeted-covtype-dual-market-open": 4000.0,
    "budgeted-imputation-mcar50": 3600.0,
    "carps-star-discrepancy-subset-select": 3600.0,
    "cpu-decoder-graph-executor": 3600.0,
    "cpu-llm-decode-throughput": 4800.0,
    "dctabeval-aeac-pooled-cat-statistics": 3600.0,
    "label-efficient-risk-estimator": 5400.0,
    "shortest-valid-ci-l2-ece": 3600.0,
    "sketched-newton-cov-estimator": 3600.0,
    "sopcc-online-chance-constrained-policy": 3600.0,
}
EXPECTED_COLLECTOR_COMMAND = "/opt/are/venv/bin/python /opt/are/drain.py"


class OverlayError(ValueError):
    """The selected source task does not match the known safe overlay recipe."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_grader_collection(
    task_toml: str, *, expected_timeout_sec: float = 3600.0,
) -> tuple[str, dict[str, Any]]:
    """Remove only the exact artifact and collector entries pinned above."""
    try:
        config = tomllib.loads(task_toml)
    except tomllib.TOMLDecodeError as exc:
        raise OverlayError("source task.toml is not valid TOML") from exc
    artifacts = config.get("artifacts")
    collector = config.get("verifier", {}).get("collect")
    expected_collector = {
        "service": "grader", "command": EXPECTED_COLLECTOR_COMMAND,
        "timeout_sec": expected_timeout_sec,
    }
    if artifacts != [EXPECTED_ARTIFACT] or collector != [expected_collector]:
        raise OverlayError("task artifact or verifier collector differs from the reviewed ARE config")

    artifact_pattern = re.compile(
        r"(?m)^artifacts\s*=\s*\[\n(?:[ \t].*\n)*?\]\n?"
    )
    text, artifact_count = artifact_pattern.subn("", task_toml, count=1)
    if artifact_count != 1:
        raise OverlayError("could not remove the exact root-level artifact declaration")
    collector_pattern = re.compile(
        r"(?ms)^\[\[verifier\.collect\]\]\n.*?(?=^\[verifier\.environment\]\n)"
    )
    text, collector_count = collector_pattern.subn("", text, count=1)
    if collector_count != 1:
        raise OverlayError("could not remove the exact verifier.collect block")
    try:
        transformed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OverlayError("safe task.toml overlay did not parse") from exc
    if ("artifacts" in transformed
            or transformed.get("verifier", {}).get("collect") is not None):
        raise OverlayError("grader artifact collection remains enabled in the overlay")
    return text, {
        "removed_artifacts": [EXPECTED_ARTIFACT],
        "removed_verifier_collect": [expected_collector],
        "verification_must_remain_disabled": True,
    }


def prepare_overlay(source_root: Path, task_id: str, output: Path) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise OverlayError("overlay output path must not already exist")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    matches = [task for task in selection.get("tasks", []) if task.get("task_id") == task_id]
    if len(matches) != 1:
        raise OverlayError("task ID is not uniquely pinned in the pilot shortlist")
    task = matches[0]
    if task_id not in REVIEWED_COLLECT_TIMEOUTS:
        raise OverlayError("task ID has no reviewed grader collect timeout in the safe overlay recipe")
    revision = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if revision != selection["source_revision"]:
        raise OverlayError("AutoResearchExam source revision differs from the pinned shortlist")
    source = source_root / task_id
    task_path = source / "task.toml"
    instruction_path = source / "instruction.md"
    environment_path = source / "environment"
    for path in (task_path, instruction_path, environment_path):
        if path.is_symlink() or not path.exists():
            raise OverlayError("source task, instruction, or environment is missing or a symlink")
    task_bytes = task_path.read_bytes()
    instruction_bytes = instruction_path.read_bytes()
    if sha256(task_bytes) != task["task_sha256"]:
        raise OverlayError("source task.toml differs from its pinned digest")
    if sha256(instruction_bytes) != task["instruction_sha256"]:
        raise OverlayError("source instruction differs from its pinned digest")
    config = tomllib.loads(task_bytes.decode("utf-8"))
    if config.get("task", {}).get("version") != task["version"]:
        raise OverlayError("source task version differs from its pinned version")
    transformed_text, removed = strip_grader_collection(
        task_bytes.decode("utf-8"),
        expected_timeout_sec=REVIEWED_COLLECT_TIMEOUTS[task_id],
    )
    transformed_bytes = transformed_text.encode("utf-8")

    output.mkdir(parents=True, mode=0o700)
    try:
        (output / "task.toml").write_bytes(transformed_bytes)
        os.chmod(output / "task.toml", 0o600)
        for name in ("instruction.md", "README.md", "hints", "environment"):
            candidate = source / name
            if candidate.exists() and not candidate.is_symlink():
                (output / name).symlink_to(candidate, target_is_directory=candidate.is_dir())
        report = {
            "schema": "bananloop-are-safe-harbor-task-overlay-v1",
            "task_id": task_id,
            "version": task["version"],
            "source_revision": revision,
            "source_task_sha256": sha256(task_bytes),
            "source_instruction_sha256": sha256(instruction_bytes),
            "overlay_task_sha256": sha256(transformed_bytes),
            "overlay_path": str(output),
            "public_environment_symlink": str(environment_path),
            **removed,
            "qualification": "local BananLoop public-MCP adapter overlay; not an official Harbor task digest",
        }
        (output / "bananloop-overlay.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        os.chmod(output / "bananloop-overlay.json", 0o600)
        return report
    except Exception:
        # Keep failures recoverable and avoid deleting a caller-selected path.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Pinned AutoResearchExam source checkout")
    parser.add_argument("--task-id", default="budgeted-imputation-mcar50")
    parser.add_argument("--out", type=Path, required=True,
                        help="New, nonexisting output directory for the local task overlay")
    args = parser.parse_args()
    try:
        report = prepare_overlay(args.source, args.task_id, args.out)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
