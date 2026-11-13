"""Prepare one frozen Sketched Newton Harbor job without invoking a model or grader.

This is deliberately a single-task handoff, not a replacement for the ten-task
AutoResearchExam series runner. All generated files live outside the checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bananloop.roles import RoleRegistryV1

from .campaign_binding import require_frozen_optimization_child
from .public_mcp_contract import validate_public_mcp_contract
from .public_sketched_newton_dev import (
    DEFAULT_IMAGE, DEFAULT_IMAGE_ID, verify_public_newton_inputs,
)
from .sketched_newton_runner import CANDIDATE_PATH, PUBLIC_OUTPUT_PATH, TASK_ID, TASK_VERSION


class PreparationError(ValueError):
    """The prepared job cannot be bound to the frozen task and child."""


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"missing or symlinked input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreparationError(f"expected JSON object: {path}")
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_configs(
    *,
    repo: Path,
    state_dir: Path,
    campaign_id: str,
    role_registry: Path,
    source_task: Path,
    mcp_contract: Path,
    overlay: Path,
    jobs_dir: Path,
    output_dir: Path,
    instruction_sha256: str,
    contract_sha256: str,
    candidate_iterations: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure configuration builder; does not create a child or dispatch work."""
    if type(candidate_iterations) is not int or not 1 <= candidate_iterations <= 5:
        raise PreparationError("frozen candidate count must be 1..5")
    calls = 2 * candidate_iterations  # Optimizer + Integration Verifier per candidate
    safe_id = campaign_id.replace("c-", "")[:12]
    job_name = f"are-newton-{safe_id}"
    config_path = output_dir / "bananloop.json"
    bananloop_config = {
        "repo": str(repo), "state_dir": str(state_dir),
        "campaign_id": campaign_id, "role_registry": str(role_registry),
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "task_instruction_sha256": instruction_sha256,
        "adapter_id": "sketched-newton-public-v1",
        "candidate_path": CANDIDATE_PATH,
        "public_output_path": PUBLIC_OUTPUT_PATH,
        "visible_path": [CANDIDATE_PATH], "write_path": [CANDIDATE_PATH],
        "public_task_root": str(source_task),
        "mcp_contract_path": str(mcp_contract),
        "mcp_contract_sha256": contract_sha256,
        "public_dev_image": DEFAULT_IMAGE,
        "public_dev_image_id": DEFAULT_IMAGE_ID,
        "public_dev_cell_count": 1,
        # The frozen Newton fixture has one aggregate case and repetition=1.
        "repeatability_runs": 1,
        "max_candidate_iterations": candidate_iterations,
        "time_limit_seconds": 3600,
        "model_call_limit": calls,
        "timeout_seconds": 120,
        "max_steps": 20,
        # Keep at-most-once submissions isolated per campaign/task.
        "public_submission_ledger_path": str(
            state_dir / "are-public-ledgers" / f"{campaign_id}-newton.sqlite3"
        ),
    }
    harbor_config = {
        "job_name": job_name,
        "jobs_dir": str(jobs_dir),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "tasks": [{"path": str(overlay)}],
        "agents": [{
            "import_path": (
                "benchmarks.autoresearchexam.harbor_agent:"
                "AREBananLoopOptimizationAgent"
            ),
            "kwargs": {"config_path": str(config_path)},
            "override_timeout_sec": 3600,
        }],
    }
    return bananloop_config, harbor_config


def prepare(
    *,
    repo: Path,
    state_dir: Path,
    campaign_id: str,
    role_registry: Path,
    source_task: Path,
    mcp_contract: Path,
    overlay_index: Path,
    jobs_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Fail closed on mismatched pins before creating any output files."""
    repo = repo.expanduser().resolve(strict=True)
    state_dir = state_dir.expanduser().resolve(strict=True)
    role_registry = role_registry.expanduser().resolve(strict=True)
    source_task = source_task.expanduser().resolve(strict=True)
    mcp_contract = mcp_contract.expanduser().resolve(strict=True)
    overlay_index = overlay_index.expanduser().resolve(strict=True)
    jobs_dir = jobs_dir.expanduser().absolute()
    output_dir = output_dir.expanduser().absolute()
    if (not repo.is_dir() or not state_dir.is_dir() or not source_task.is_dir()
            or source_task.name != TASK_ID):
        raise PreparationError("source repository, state root or Newton task is invalid")
    if output_dir.exists() or output_dir.is_symlink():
        raise PreparationError("output directory must be new (no overwriting/replaying)")
    if output_dir == repo or repo in output_dir.parents:
        raise PreparationError("prepared job files must live outside the checkout")
    if output_dir == jobs_dir or output_dir in jobs_dir.parents or jobs_dir in output_dir.parents:
        raise PreparationError("Harbor jobs and prepared configs must not overlap")
    if source_task.is_symlink() or role_registry.is_symlink() or mcp_contract.is_symlink():
        raise PreparationError("source task, registry and MCP contract must not be symlinks")

    selection = _json(repo / "benchmarks/autoresearchexam/task-selection.json")
    matches = [t for t in selection.get("tasks", []) if t.get("task_id") == TASK_ID]
    if len(matches) != 1 or matches[0].get("version") != TASK_VERSION:
        raise PreparationError("Newton is not uniquely pinned in the selection")
    task = matches[0]
    if _sha((source_task / "instruction.md").read_bytes()) != task["instruction_sha256"]:
        raise PreparationError("Newton public task instructions differ from selection")
    if _sha((source_task / "task.toml").read_bytes()) != task["task_sha256"]:
        raise PreparationError("Newton task manifest differs from selection")
    # Also validate the frozen public runner and dev input arrays.
    verify_public_newton_inputs(source_task)

    overlay_data = _json(overlay_index)
    overlays = [o for o in overlay_data.get("tasks", []) if o.get("task_id") == TASK_ID]
    if len(overlays) != 1:
        raise PreparationError("safe Newton overlay is missing or ambiguous")
    overlay = Path(overlays[0]["overlay_path"]).expanduser().resolve(strict=True)
    metadata = _json(overlay / "bananloop-overlay.json")
    if (not overlay.is_dir() or metadata.get("task_id") != TASK_ID
            or metadata.get("version") != TASK_VERSION
            or metadata.get("source_task_sha256") != task["task_sha256"]
            or metadata.get("source_instruction_sha256") != task["instruction_sha256"]
            or metadata.get("overlay_task_sha256") != overlays[0].get("overlay_task_sha256")
            or _sha((overlay / "task.toml").read_bytes()) != metadata["overlay_task_sha256"]
            or _sha((overlay / "instruction.md").read_bytes()) != task["instruction_sha256"]):
        raise PreparationError("safe Newton overlay differs from pinned metadata")

    contract = validate_public_mcp_contract(
        _json(mcp_contract), task_id=TASK_ID, task_version=TASK_VERSION,
        task_sha256=task["task_sha256"],
        task_instruction_sha256=task["instruction_sha256"],
    )
    if contract.get("public_endpoint") != "http://grader:8000/mcp":
        raise PreparationError("public MCP endpoint differs from the task pin")

    registry = RoleRegistryV1.load(role_registry)
    # The child must already exist and freeze a verified evaluator protocol.
    child_probe = {
        "state_dir": str(state_dir), "campaign_id": campaign_id,
        "max_candidate_iterations": 1, "time_limit_seconds": 3600,
    }
    # Resolve the frozen count first; do not choose a new experiment budget.
    import sqlite3
    from bananloop.contracts import OptimizationCampaignSpecV2, parse_campaign_spec_document
    database = state_dir / "campaigns" / campaign_id / "campaign.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise PreparationError("approved Optimization child is not present; execute existing pivot first")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT spec_json FROM campaigns ORDER BY id").fetchall()
    if len(rows) != 1:
        raise PreparationError("Optimization child spec is missing or ambiguous")
    frozen = parse_campaign_spec_document(json.loads(rows[0][0]))
    if not isinstance(frozen, OptimizationCampaignSpecV2):
        raise PreparationError("selected campaign is not an Optimization child")
    candidate_iterations = frozen.max_candidate_iterations or 1
    child_probe["max_candidate_iterations"] = candidate_iterations
    require_frozen_optimization_child(child_probe)
    if registry.digest() != frozen.role_registry_digest:
        raise PreparationError("role registry digest differs from the frozen child")
    if (jobs_dir / f"are-newton-{campaign_id.replace('c-', '')[:12]}").exists():
        raise PreparationError("Harbor job output already exists; never implicitly replay")
    ledger = state_dir / "are-public-ledgers" / f"{campaign_id}-newton.sqlite3"
    if ledger.exists() or ledger.is_symlink():
        raise PreparationError("public submission ledger already exists; reconcile, do not replay")

    bl, harbor = build_configs(
        repo=repo, state_dir=state_dir, campaign_id=campaign_id,
        role_registry=role_registry, source_task=source_task,
        mcp_contract=mcp_contract, overlay=overlay, jobs_dir=jobs_dir,
        output_dir=output_dir, instruction_sha256=task["instruction_sha256"],
        contract_sha256=contract["contract_sha256"],
        candidate_iterations=candidate_iterations,
    )
    output_dir.mkdir(parents=True, mode=0o700)
    _write(output_dir / "bananloop.json", bl)
    _write(output_dir / "harbor.json", harbor)
    result = {
        "status": "PREPARED_NO_PROVIDER_OR_GRADER_CALLS",
        "task_id": TASK_ID, "campaign_id": campaign_id,
        "frozen_candidate_iterations": candidate_iterations,
        "max_model_calls_if_authorized": bl["model_call_limit"],
        "role_registry_digest": registry.digest(),
        "public_dev_image_id": DEFAULT_IMAGE_ID,
        "harbor_command": [
            "harbor", "run", "--config", str(output_dir / "harbor.json"),
            "--disable-verification",
        ],
        "note": "Review frozen pins, Docker image and authorization before running.",
    }
    _write(output_dir / "preparation.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "state-dir", "campaign-id", "role-registry",
                 "source-task", "mcp-contract", "overlay-index", "jobs-dir", "out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        result = prepare(
            repo=Path(args.repo), state_dir=Path(args.state_dir),
            campaign_id=args.campaign_id,
            role_registry=Path(args.role_registry),
            source_task=Path(args.source_task),
            mcp_contract=Path(args.mcp_contract),
            overlay_index=Path(args.overlay_index),
            jobs_dir=Path(args.jobs_dir), output_dir=Path(args.out),
        )
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
