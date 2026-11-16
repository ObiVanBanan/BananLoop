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
import shutil
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
    "act-tensor-sparse-panel-imputation-r2": 3600.0,
    "causalpfn-cate-pehe-ihdp-surfaceb": 3600.0,
    "hicard-latent-encoder": 3600.0,
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


def prepare_local_public_dev_task_toml(task_toml: str, *, expected_timeout_sec: float) -> tuple[str, dict[str, Any]]:
    """Disable every task-side private grader hook for a local DEV-only overlay."""
    text, removed = strip_grader_collection(
        task_toml, expected_timeout_sec=expected_timeout_sec,
    )
    mcp_pattern = re.compile(r"(?m)^mcp_servers\s*=\s*\[\n(?:[ \t].*\n)*?\]\n?")
    text, mcp_count = mcp_pattern.subn("", text, count=1)
    if mcp_count != 1:
        raise OverlayError("local DEV overlay could not remove the exact task MCP server declaration")
    # The only grader reference remaining in agent configuration is the exact
    # allowlisted host from the frozen public task file.
    text, host_count = re.subn(r'(?m)^[ \t]*"grader",\n', "", text, count=1)
    if host_count != 1:
        raise OverlayError("local DEV overlay could not remove the grader host allowlist entry")
    try:
        transformed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OverlayError("local DEV task.toml overlay did not parse") from exc
    if ("artifacts" in transformed
            or transformed.get("verifier", {}).get("collect") is not None
            or transformed.get("environment", {}).get("mcp_servers") is not None
            or "grader" in transformed.get("agent", {}).get("allowed_hosts", [])):
        raise OverlayError("local DEV task.toml still exposes a grader hook or endpoint")
    return text, {
        **removed,
        "removed_mcp_servers": [{"name": "are", "url": "http://grader:8000/mcp"}],
        "removed_agent_host": "grader",
        "private_grader_enabled": False,
    }


def local_public_dev_compose(compose_text: str, *, expected_output: str) -> str:
    """Keep only the main task service and candidate volume; never build grader contexts."""
    if ("are-grader-ctx" not in compose_text
            or len(re.findall(r"(?m)^  grader:\n", compose_text)) != 1
            or len(re.findall(r"(?m)^  test-grader:\n", compose_text)) != 1):
        raise OverlayError("source compose file differs from the pinned ARE sidecar layout")
    main = re.search(r"(?ms)^  main:\n.*?(?=^  grader:\n)", compose_text)
    if main is None:
        raise OverlayError("source compose file has no uniquely pinned main service")
    main_text = re.sub(
        r"(?m)^    depends_on:\n      grader:\n        condition: service_healthy\n", "",
        main.group(0), count=1,
    )
    if "depends_on:" in main_text or f"are-deliverable-0:{expected_output}" not in main_text:
        raise OverlayError("main service dependency or deliverable volume differs from the task pin")
    result = "services:\n" + main_text + "\nvolumes:\n  are-deliverable-0: {}\n"
    if "grader" in result or "are-secret" in result or "are-private" in result or "are-grader-ctx" in result:
        raise OverlayError("private grader reference remains in the local DEV compose overlay")
    return result


def prepare_overlay(source_root: Path, task_id: str, output: Path, *,
                    local_public_dev_only: bool = False) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise OverlayError("overlay output path must not already exist")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    catalogue = None
    matches = [task for task in selection.get("tasks", []) if task.get("task_id") == task_id]
    if not matches and task_id in {
        "act-tensor-sparse-panel-imputation-r2",
        "causalpfn-cate-pehe-ihdp-surfaceb", "hicard-latent-encoder",
    }:
        catalogue_path = ROOT / "benchmarks/autonomous_v01/task-catalogue.json"
        catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
        matches = [task for task in catalogue.get("tasks", [])
                   if task.get("task_id") == task_id]
    if len(matches) != 1:
        raise OverlayError("task ID is not uniquely pinned in the pilot shortlist")
    task = matches[0]
    if task_id not in REVIEWED_COLLECT_TIMEOUTS:
        raise OverlayError("task ID has no reviewed grader collect timeout in the safe overlay recipe")
    revision = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    pinned_revision = catalogue["source_revision"] if catalogue is not None else selection["source_revision"]
    if revision != pinned_revision:
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
    if local_public_dev_only:
        if task_id not in {
            "act-tensor-sparse-panel-imputation-r2",
            "causalpfn-cate-pehe-ihdp-surfaceb", "hicard-latent-encoder",
        }:
            raise OverlayError("local DEV-only overlay is allowlisted only for the three added CPU tasks")
        transformed_text, removed = prepare_local_public_dev_task_toml(
            task_bytes.decode("utf-8"),
            expected_timeout_sec=REVIEWED_COLLECT_TIMEOUTS[task_id],
        )
    else:
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
                if name == "environment" and local_public_dev_only:
                    for current, directories, files in os.walk(candidate, followlinks=False):
                        directories[:] = [item for item in directories if item != "are-grader-ctx"]
                        for entry in (*directories, *files):
                            path = Path(current) / entry
                            if path.is_symlink():
                                raise OverlayError("public task environment contains a symlink")
                    shutil.copytree(
                        candidate, output / name,
                        ignore=lambda _directory, names: [item for item in names
                                                          if item == "are-grader-ctx"],
                    )
                    compose_path = output / name / "docker-compose.yaml"
                    if compose_path.is_symlink() or not compose_path.is_file():
                        raise OverlayError("public task compose file is missing or unsafe")
                    expected_output = {
                        "act-tensor-sparse-panel-imputation-r2": "/app/solution",
                        "causalpfn-cate-pehe-ihdp-surfaceb": "/app/output",
                        "hicard-latent-encoder": "/app/output",
                    }[task_id]
                    safe_compose = local_public_dev_compose(
                        compose_path.read_text(encoding="utf-8"),
                        expected_output=expected_output,
                    )
                    compose_path.write_text(safe_compose, encoding="utf-8")
                    if (output / name / "are-grader-ctx").exists():
                        raise OverlayError("private grader build context was copied into the local overlay")
                else:
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
            "local_public_dev_only": local_public_dev_only,
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
    parser.add_argument("--local-public-dev-only", action="store_true",
                        help="build an overlay without private grader services, MCP, or verification")
    args = parser.parse_args()
    try:
        report = prepare_overlay(args.source, args.task_id, args.out,
                                 local_public_dev_only=args.local_public_dev_only)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
