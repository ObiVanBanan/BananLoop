"""Prepare a local public-DEV Harbor bridge job; never starts Harbor or a model."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .public_task_evaluator import AREPublicTaskEvaluatorRunner


class PreparationError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(*, repo: Path, state_dir: Path, campaign_id: str, role_registry: Path,
            source_task: Path, fixture_path: Path, overlay: Path, jobs_dir: Path,
            output_dir: Path, adapter_id: str, max_candidate_iterations: int = 5
            ) -> dict[str, Any]:
    repo = repo.expanduser().resolve(strict=True)
    state_dir = state_dir.expanduser().resolve(strict=True)
    role_registry = role_registry.expanduser().resolve(strict=True)
    source_task = source_task.expanduser().resolve(strict=True)
    fixture_path = fixture_path.expanduser().resolve(strict=True)
    overlay = overlay.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().absolute()
    jobs_dir = jobs_dir.expanduser().absolute()
    if output_dir.exists() or output_dir.is_symlink() or output_dir == repo or repo in output_dir.parents:
        raise PreparationError("output directory must be new and outside the checkout")
    if output_dir == jobs_dir or output_dir in jobs_dir.parents or jobs_dir in output_dir.parents:
        raise PreparationError("Harbor jobs and prepared configs must not overlap")
    if not campaign_id or not campaign_id.startswith("c-"):
        raise PreparationError("prepared Optimization child campaign ID is required")
    if type(max_candidate_iterations) is not int or not 1 <= max_candidate_iterations <= 5:
        raise PreparationError("Optimization iteration cap must be from one to five")
    spec = AREPublicTaskEvaluatorRunner.spec(adapter_id)
    if source_task.name != spec["task_id"] or not source_task.is_dir():
        raise PreparationError("public task root differs from selected adapter")
    if overlay.is_symlink() or not overlay.is_dir() or not (overlay / "task.toml").is_file():
        raise PreparationError("safe local-public-DEV overlay is missing")
    if "grader" in (overlay / "task.toml").read_text(encoding="utf-8"):
        raise PreparationError("local overlay still mentions grader services")
    fixture = EvaluatorFixtureV1.load(fixture_path)
    bundle = base64.b64decode(fixture.evaluator_bundle_base64, validate=True)
    if fixture.execution_backend_id != f"autoresearchexam-{adapter_id}":
        raise PreparationError("fixture backend does not match adapter")
    task_file, instruction_file = source_task / "task.toml", source_task / "instruction.md"
    if task_file.is_symlink() or instruction_file.is_symlink():
        raise PreparationError("source task files must not be symlinks")
    dev_files = AREPublicTaskEvaluatorRunner._public_dev_manifest(source_task, spec["count"])
    from . import public_task_evaluator as evaluator
    contract = evaluator.AREPublicTaskEvaluatorRunner.task_contract_digest(
        adapter_id=adapter_id, task_version="1.1.0",
        task_sha256=_sha(task_file), instruction_sha256=_sha(instruction_file),
        dev_files=dev_files,
    )
    if contract != json.loads(bundle).get("contract_sha256"):
        raise PreparationError("fixture bundle differs from public task source contract")
    output_dir.mkdir(parents=True, mode=0o700)
    candidate_path = spec["candidate_path"]
    safe_id = campaign_id[2:14]
    config_path = output_dir / "bananloop.json"
    bl_config = {
        "repo": str(repo), "state_dir": str(state_dir), "campaign_id": campaign_id,
        "role_registry": str(role_registry), "task_id": spec["task_id"],
        "task_version": "1.1.0", "task_instruction_sha256": _sha(instruction_file),
        "adapter_id": adapter_id, "candidate_path": candidate_path,
        "public_output_path": spec["output_path"],
        "visible_path": [candidate_path], "write_path": [candidate_path],
        "local_public_dev_only": True, "public_task_root": str(source_task),
        "public_task_fixture_path": str(fixture_path),
        "public_task_fixture_sha256": fixture.content_digest(),
        "public_task_bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "public_task_contract_sha256": contract,
        "public_dev_image": spec["image"], "public_dev_image_id": spec["image_id"],
        "public_dev_cell_count": spec["count"], "repeatability_runs": 1,
        "max_candidate_iterations": max_candidate_iterations,
        "time_limit_seconds": 3600,
        "model_call_limit": 2 * max_candidate_iterations,
        "timeout_seconds": 120, "max_steps": 20,
    }
    harbor_config = {
        "job_name": f"are-public-{adapter_id}-{safe_id}", "jobs_dir": str(jobs_dir),
        "n_attempts": 1, "n_concurrent_trials": 1,
        "retry": {"max_retries": 0}, "tasks": [{"path": str(overlay)}],
        "agents": [{"import_path": "benchmarks.autoresearchexam.harbor_agent:AREBananLoopOptimizationAgent",
                    "kwargs": {"config_path": str(config_path)}, "override_timeout_sec": 3600}],
    }
    (output_dir / "bananloop.json").write_text(
        json.dumps(bl_config, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_dir / "harbor.json").write_text(
        json.dumps(harbor_config, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for path in (output_dir / "bananloop.json", output_dir / "harbor.json"):
        path.chmod(0o600)
    return {"task_id": spec["task_id"], "adapter_id": adapter_id,
            "bananloop_config": str(config_path), "bananloop_config_sha256": _sha(config_path),
            "harbor_config": str(output_dir / "harbor.json"),
            "harbor_config_sha256": _sha(output_dir / "harbor.json"),
            "fixture_sha256": fixture.content_digest(), "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "overlay_task_sha256": _sha(overlay / "task.toml"),
            "candidate_path": candidate_path, "public_output_path": spec["output_path"],
            "model_calls_are_not_started": True, "harbor_is_not_started": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "state-dir", "role-registry", "source-task", "fixture",
                 "overlay", "jobs-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--adapter-id", choices=("act-tensor-public-v1", "causalpfn-public-v1", "hicard-public-v1"), required=True)
    parser.add_argument("--max-candidate-iterations", type=int, default=5)
    args = parser.parse_args()
    result = prepare(repo=args.repo, state_dir=args.state_dir, campaign_id=args.campaign_id,
                     role_registry=args.role_registry, source_task=args.source_task,
                     fixture_path=args.fixture, overlay=args.overlay, jobs_dir=args.jobs_dir,
                     output_dir=args.output_dir, adapter_id=args.adapter_id,
                     max_candidate_iterations=args.max_candidate_iterations)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
