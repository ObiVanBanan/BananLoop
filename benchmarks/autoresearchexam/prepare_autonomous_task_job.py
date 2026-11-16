"""Prepare a frozen time-only Optimization Harbor job for an existing ARE adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from bananloop.contracts import OptimizationCampaignSpecV2, parse_campaign_spec_document
from bananloop.evaluator_fixture import EvaluatorFixtureV1
from bananloop.roles import RoleRegistryV1

from .campaign_binding import require_frozen_optimization_child
from .public_mcp_contract import validate_public_mcp_contract


class PreparationError(ValueError):
    """The frozen child, evaluator, task contract, or Harbor scope disagrees."""


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"missing or unsafe JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreparationError(f"expected a JSON object: {path}")
    return value


def prepare(*, repo: Path, state_dir: Path, campaign_id: str, role_registry: Path,
            source_task: Path, fixture_path: Path, overlay: Path, mcp_contract_path: Path,
            jobs_dir: Path, output_dir: Path, task_id: str,
            deadline_epoch: float) -> dict[str, Any]:
    repo = repo.expanduser().resolve(strict=True)
    state_dir = state_dir.expanduser().resolve(strict=True)
    role_registry = role_registry.expanduser().resolve(strict=True)
    source_task = source_task.expanduser().resolve(strict=True)
    fixture_path = fixture_path.expanduser().resolve(strict=True)
    overlay = overlay.expanduser().resolve(strict=True)
    mcp_contract_path = mcp_contract_path.expanduser().resolve(strict=True)
    jobs_dir = jobs_dir.expanduser().absolute()
    output_dir = output_dir.expanduser().absolute()
    if (isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
            or not math.isfinite(deadline_epoch) or deadline_epoch <= time.time()
            or deadline_epoch > time.time() + 3600.5):
        raise PreparationError("an inherited absolute deadline within one hour is required")
    if output_dir.exists() or output_dir.is_symlink() or output_dir == repo or repo in output_dir.parents:
        raise PreparationError("prepared configs must be new and outside the checkout")
    if output_dir == jobs_dir or output_dir in jobs_dir.parents or jobs_dir in output_dir.parents:
        raise PreparationError("prepared configs and Harbor jobs must not overlap")

    catalogue_path = repo / "benchmarks/autonomous_v01/task-catalogue.json"
    catalogue = _read_json(catalogue_path)
    tasks = [item for item in catalogue.get("tasks", [])
             if isinstance(item, dict) and item.get("task_id") == task_id]
    if len(tasks) != 1:
        raise PreparationError("task is not uniquely pinned in the frozen 12-task catalogue")
    task = tasks[0]
    adapter = task["adapter"]
    instruction_path, task_path = source_task / "instruction.md", source_task / "task.toml"
    if source_task.name != task_id or source_task.is_symlink():
        raise PreparationError("source task root does not match the selected task")
    if (instruction_path.is_symlink() or task_path.is_symlink()
            or hashlib.sha256(instruction_path.read_bytes()).hexdigest() != task["instruction_sha256"]
            or hashlib.sha256(task_path.read_bytes()).hexdigest() != task["task_sha256"]):
        raise PreparationError("source task differs from the frozen catalogue pins")
    if overlay.is_symlink() or not overlay.is_dir() or (overlay / "task.toml").is_symlink():
        raise PreparationError("safe Harbor overlay is missing or unsafe")
    overlay_task = (overlay / "task.toml").read_bytes()
    if b"grader" in overlay_task.lower():
        raise PreparationError("Harbor overlay contains grader service references")

    fixture = EvaluatorFixtureV1.load(fixture_path)
    fixture_digest = fixture.content_digest()
    contract = validate_public_mcp_contract(
        _read_json(mcp_contract_path), task_id=task_id, task_version=task["version"],
        task_sha256=task["task_sha256"],
        task_instruction_sha256=task["instruction_sha256"],
    )
    if fixture.execution_backend_id is None:
        raise PreparationError("task evaluator fixture does not name an execution backend")

    database = state_dir / "campaigns" / campaign_id / "campaign.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise PreparationError("the approved Optimization child is missing")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10) as connection:
        rows = connection.execute("SELECT spec_json FROM campaigns ORDER BY id").fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise PreparationError("Optimization child spec is missing or ambiguous")
    spec = parse_campaign_spec_document(json.loads(rows[0][0]))
    if not isinstance(spec, OptimizationCampaignSpecV2):
        raise PreparationError("campaign is not a v2 Optimization child")
    binding = require_frozen_optimization_child({
        "state_dir": str(state_dir), "campaign_id": campaign_id,
        "benchmark_time_only": True, "max_candidate_iterations": None,
        "time_limit_seconds": 3600, "deadline_epoch": deadline_epoch,
    })
    del binding
    if (spec.source_repo != str(repo)
            or spec.evaluator_fixture_digest != fixture_digest
            or spec.role_registry_digest != RoleRegistryV1.load(role_registry).digest()
            or spec.autonomy.scope is None
            or tuple(spec.autonomy.scope.allowed_paths) != (adapter["candidate_path"],)
            or spec.autonomy.capability_policy is None
            or tuple(spec.autonomy.capability_policy.write_paths) != (adapter["candidate_path"],)
            or spec.evaluation.approval_digest is None):
        raise PreparationError("approved child, fixture, role registry, or candidate scope differs")

    from .optimization_runner import AREPublicEvaluatorRunner
    from .carps_runner import AREPublicCARPSEvaluatorRunner
    from .cpu_llm_runner import AREPublicCPULLMEvaluatorRunner
    from .cpudecoder_runner import AREPublicCPUDecoderEvaluatorRunner
    from .dctabeval_runner import AREPublicDCTABEvalEvaluatorRunner
    from .labelrisk_runner import AREPublicLabelRiskEvaluatorRunner
    from .shortest_ci_runner import AREPublicShortestCIEvaluatorRunner
    from .sketched_newton_runner import AREPublicSketchedNewtonEvaluatorRunner
    from .sopcc_runner import AREPublicSOPCCEvaluatorRunner
    cell_count = {
        "imputation-public-v2": AREPublicEvaluatorRunner.default_dev_cell_count,
        "carps-public-v1": AREPublicCARPSEvaluatorRunner.default_dev_cell_count,
        "cpudecoder-public-v1": AREPublicCPUDecoderEvaluatorRunner.default_dev_cell_count,
        "cpu-llm-public-v1": AREPublicCPULLMEvaluatorRunner.default_dev_cell_count,
        "dctabeval-public-v1": AREPublicDCTABEvalEvaluatorRunner.default_dev_cell_count,
        "labelrisk-public-v1": AREPublicLabelRiskEvaluatorRunner.default_dev_cell_count,
        "shortest-ci-public-v1": AREPublicShortestCIEvaluatorRunner.default_dev_cell_count,
        "sketched-newton-public-v1": AREPublicSketchedNewtonEvaluatorRunner.default_dev_cell_count,
        "sopcc-public-v1": AREPublicSOPCCEvaluatorRunner.default_dev_cell_count,
    }.get(adapter["adapter_id"])
    if cell_count is None:
        raise PreparationError("no existing public MCP Harbor adapter for this task")
    repeatability = 4 if adapter["adapter_id"] == "sopcc-public-v1" else (
        1 if adapter["adapter_id"] == "cpudecoder-public-v1" else 2
    )
    output_dir.mkdir(parents=True, mode=0o700)
    jobs_dir.mkdir(parents=True, mode=0o700)
    config_path = output_dir / "bananloop.json"
    harbor_path = output_dir / "harbor.json"
    submission_db = state_dir / "are-public-ledgers" / f"{campaign_id}-{task_id}.sqlite3"
    bl_config: dict[str, Any] = {
        "repo": str(repo), "state_dir": str(state_dir), "campaign_id": campaign_id,
        "role_registry": str(role_registry), "task_id": task_id,
        "task_version": task["version"],
        "task_instruction_sha256": task["instruction_sha256"],
        "adapter_id": adapter["adapter_id"], "candidate_path": adapter["candidate_path"],
        "public_output_path": adapter["public_output_path"],
        "visible_path": [adapter["candidate_path"]],
        "write_path": [adapter["candidate_path"]],
        "public_task_root": str(source_task),
        "mcp_contract_path": str(mcp_contract_path),
        "mcp_contract_sha256": contract["contract_sha256"],
        "public_dev_image": task["public_dev_image"],
        "public_dev_image_id": task["public_dev_image_id"],
        "public_dev_cell_count": cell_count,
        "repeatability_runs": repeatability,
        "benchmark_time_only": True,
        "technical_call_ceiling": 1000,
        "technical_step_ceiling": 1000,
        "deadline_epoch": float(deadline_epoch),
        "time_limit_seconds": 3600,
        "model_call_limit": 1000,
        "timeout_seconds": 120,
        "max_steps": 1000,
        "public_submission_ledger_path": str(submission_db),
    }
    if adapter["adapter_id"] == "carps-public-v1":
        bl_config["public_dev_timeout_seconds"] = 180
    harbor_config = {
        "job_name": f"are-autonomous-{adapter['adapter_id']}-{campaign_id[2:14]}",
        "jobs_dir": str(jobs_dir), "n_attempts": 1, "n_concurrent_trials": 1,
        "retry": {"max_retries": 0}, "tasks": [{"path": str(overlay)}],
        "agents": [{
            "import_path": "benchmarks.autoresearchexam.harbor_agent:AREBananLoopOptimizationAgent",
            "kwargs": {"config_path": str(config_path)}, "override_timeout_sec": 3600,
        }],
    }
    for path, value in ((config_path, bl_config), (harbor_path, harbor_config)):
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return {
        "task_id": task_id, "adapter_id": adapter["adapter_id"],
        "bananloop_config": str(config_path), "harbor_config": str(harbor_path),
        "bananloop_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "harbor_config_sha256": hashlib.sha256(harbor_path.read_bytes()).hexdigest(),
        "fixture_sha256": fixture_digest,
        "mcp_contract_sha256": contract["contract_sha256"],
        "model_or_harbor_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "state-dir", "role-registry", "source-task", "fixture-path",
                 "overlay", "mcp-contract-path", "jobs-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--deadline-epoch", type=float, required=True)
    args = parser.parse_args()
    result = prepare(
        repo=args.repo, state_dir=args.state_dir, campaign_id=args.campaign_id,
        role_registry=args.role_registry, source_task=args.source_task,
        fixture_path=args.fixture_path, overlay=args.overlay,
        mcp_contract_path=args.mcp_contract_path, jobs_dir=args.jobs_dir,
        output_dir=args.output_dir, task_id=args.task_id,
        deadline_epoch=args.deadline_epoch,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
