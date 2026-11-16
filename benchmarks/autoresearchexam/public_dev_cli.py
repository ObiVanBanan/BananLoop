"""Run one pinned AutoResearchExam public DEV scorer from a task workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def score(*, task_id: str, candidate_path: Path, source_task: Path,
          image: str, image_id: str, timeout_seconds: int,
          docker_binary: str = "docker") -> dict[str, Any]:
    """Use the same local public scorer and pins as the BananLoop Harbor agent."""
    candidate_path = candidate_path.expanduser()
    source_task = source_task.expanduser()
    catalogue_path = Path(__file__).resolve().parents[1] / "autonomous_v01" / "task-catalogue.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    matches = [item for item in catalogue.get("tasks", [])
               if isinstance(item, dict) and item.get("task_id") == task_id]
    if len(matches) != 1:
        raise ValueError("task is not uniquely pinned in the benchmark catalogue")
    task = matches[0]
    expected_candidate = task["adapter"]["candidate_path"]
    workspace = Path.cwd().resolve(strict=True)
    candidate_relative = Path(expected_candidate)
    if (candidate_path.is_absolute() or candidate_relative.is_absolute()
            or candidate_relative.as_posix() != expected_candidate
            or candidate_path.as_posix() != expected_candidate):
        raise ValueError("candidate path differs from the frozen task candidate")
    candidate_path = workspace / candidate_relative
    if (candidate_path.is_symlink() or not candidate_path.is_file()
            or not candidate_path.resolve(strict=True).is_relative_to(workspace)):
        raise ValueError("candidate path is missing or unsafe")
    instruction = source_task / "instruction.md"
    task_file = source_task / "task.toml"
    if (source_task.is_symlink() or not source_task.is_dir()
            or source_task.name != task_id or instruction.is_symlink() or task_file.is_symlink()
            or not instruction.is_file() or not task_file.is_file()
            or hashlib.sha256(instruction.read_bytes()).hexdigest() != task["instruction_sha256"]
            or hashlib.sha256(task_file.read_bytes()).hexdigest() != task["task_sha256"]):
        raise ValueError("public source task differs from frozen catalogue pins")
    if (image != task["public_dev_image"] or image_id != task["public_dev_image_id"]
            or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 3600):
        raise ValueError("image or timeout differs from frozen benchmark pins")
    candidate = candidate_path.read_bytes()
    if not candidate or len(candidate) > 2 * 1024 * 1024:
        raise ValueError("candidate source is empty or exceeds 2 MiB")

    if task_id == "budgeted-imputation-mcar50":
        from .public_devkit import run_public_devkit
        raw = run_public_devkit(candidate, image=image, expected_image_id=image_id,
                                timeout_seconds=timeout_seconds,
                                expected_cell_count=42, docker_binary=docker_binary)
        result = {"dev_score": raw["dev_score"], "dev_cells": raw["dev_cells"]}
    elif task_id == "carps-star-discrepancy-subset-select":
        from .public_carps_dev import evaluate_carps_public_dev
        raw = evaluate_carps_public_dev(
            candidate, public_task_root=source_task, image=image,
            expected_image_id=image_id, total_timeout_seconds=min(300, timeout_seconds),
            docker_binary=docker_binary,
        )
        result = {"dev_score": -float(raw["mean_exact_linf_star_discrepancy"]),
                  "dev_cells": raw["panel_count"]}
    elif task_id == "cpu-decoder-graph-executor":
        from .public_cpudecoder_dev import run_public_cpudecoder_dev
        raw = run_public_cpudecoder_dev(candidate, task_root=source_task, image=image,
                                        expected_image_id=image_id,
                                        timeout_seconds=timeout_seconds)
        result = {"dev_score": raw["metric"], "dev_cells": raw["cell_count"]}
    elif task_id == "cpu-llm-decode-throughput":
        from .public_cpu_llm_dev import run_public_cpullm_dev
        raw = run_public_cpullm_dev(candidate, task_root=source_task, image=image,
                                    expected_image_id=image_id,
                                    timeout_seconds=timeout_seconds)
        result = {"dev_score": raw["metric"], "dev_cells": raw["cell_count"]}
    elif task_id == "dctabeval-aeac-pooled-cat-statistics":
        from .public_dctabeval_dev import run_public_dctabeval_dev
        raw = run_public_dctabeval_dev(candidate, task_root=source_task, image=image,
                                       expected_image_id=image_id,
                                       timeout_seconds=timeout_seconds,
                                       docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["panel_count"]}
    elif task_id == "label-efficient-risk-estimator":
        from .public_labelrisk_dev import run_public_labelrisk_dev
        raw = run_public_labelrisk_dev(candidate, task_root=source_task, image=image,
                                       expected_image_id=image_id,
                                       timeout_seconds=timeout_seconds,
                                       docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["cell_count"]}
    elif task_id == "shortest-valid-ci-l2-ece":
        from .public_shortest_ci_dev import run_public_shortest_ci_dev
        raw = run_public_shortest_ci_dev(candidate, image=image, expected_image_id=image_id,
                                         timeout_seconds=timeout_seconds,
                                         docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["panel_count"]}
    elif task_id == "sketched-newton-cov-estimator":
        from .public_sketched_newton_dev import run_public_newton_dev
        raw = run_public_newton_dev(candidate, task_root=source_task, image=image,
                                    expected_image_id=image_id,
                                    timeout_seconds=min(1200, timeout_seconds),
                                    docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["panel_count"]}
    elif task_id == "sopcc-online-chance-constrained-policy":
        from .public_sopcc_dev import run_public_sopcc_dev
        raw = run_public_sopcc_dev(candidate, image=image, expected_image_id=image_id,
                                   timeout_seconds=timeout_seconds, draws=(0,),
                                   instances_per_draw=1, episodes_per_instance=4,
                                   workers=1, docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["episodes"]}
    elif task_id in {
        "act-tensor-sparse-panel-imputation-r2",
        "causalpfn-cate-pehe-ihdp-surfaceb", "hicard-latent-encoder",
    }:
        from benchmarks.autonomous_v01.public_task_runner import run_public_task_dev
        raw = run_public_task_dev(task_id, candidate, image=image,
                                  expected_image_id=image_id,
                                  timeout_seconds=timeout_seconds,
                                  docker_binary=docker_binary)
        result = {"dev_score": raw["metric"], "dev_cells": raw["dev_cases"]}
    else:
        raise ValueError("task ID has no approved public DEV adapter")

    metric, cells = result.get("dev_score"), result.get("dev_cells")
    if (isinstance(metric, bool) or not isinstance(metric, (int, float))
            or not math.isfinite(float(metric))
            or isinstance(cells, bool) or not isinstance(cells, int) or cells < 1):
        raise ValueError("public DEV adapter returned an invalid aggregate")
    # The agent sees only the same task-level score and panel size BananLoop receives.
    return {"status": "GRADED", "task_id": task_id,
            "dev_score": float(metric), "dev_cells": cells}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("score",))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--docker-binary", default="docker")
    args = parser.parse_args()
    try:
        result = score(task_id=args.task_id, candidate_path=args.candidate,
                       source_task=args.source_task, image=args.image,
                       image_id=args.image_id, timeout_seconds=args.timeout_seconds,
                       docker_binary=args.docker_binary)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "task_id": args.task_id,
                          "failure_type": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
