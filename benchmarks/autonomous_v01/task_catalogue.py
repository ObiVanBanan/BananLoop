"""Verified curated AutoResearchExam v1.1 task catalogue for benchmark v0.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

SOURCE_REVISION = "23c0d92e11af1dea821f972b5f0a739060958002"
OLD_SELECTION = Path(__file__).parents[1] / "autoresearchexam" / "task-selection.json"
CATALOGUE_PATH = Path(__file__).with_name("task-catalogue.json")
APACHE_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"

NEW_TASKS: dict[str, dict[str, str]] = {
    "act-tensor-sparse-panel-imputation-r2": {
        "adapter_id": "act-tensor-public-v1", "candidate_path": "benchmarks/autoresearchexam/solutions/act_tensor/impute.py",
        "public_output_path": "/app/solution/impute.py", "entrypoint": "impute",
        "metric_id": "imputation_r2", "metric_field": "imputation_r2", "direction": "MAXIMIZE",
        "aggregation": "MEAN", "dev_count": "8", "repeatability_runs": "1",
        "dev_limit_seconds": "240",
    },
    "causalpfn-cate-pehe-ihdp-surfaceb": {
        "adapter_id": "causalpfn-public-v1", "candidate_path": "benchmarks/autoresearchexam/solutions/causalpfn/estimator.py",
        "public_output_path": "/app/output/estimator.py", "entrypoint": "estimate_cate",
        "metric_id": "mean_pehe", "metric_field": "mean_pehe", "direction": "MINIMIZE",
        "aggregation": "MEAN", "dev_count": "100", "repeatability_runs": "1",
        "dev_limit_seconds": "1200",
    },
    "hicard-latent-encoder": {
        "adapter_id": "hicard-public-v1", "candidate_path": "benchmarks/autoresearchexam/solutions/hicard/encoder.py",
        "public_output_path": "/app/output/encoder.py", "entrypoint": "Encoder",
        "metric_id": "onehot_mse_reduction_pct", "metric_field": "onehot_mse_reduction_pct", "direction": "MAXIMIZE",
        "aggregation": "MEAN", "dev_count": "24", "repeatability_runs": "1",
        "dev_limit_seconds": "2400",
    },
}

NEW_IMAGE_TAGS = {
    task_id: f"bananloop-v01-{task_id}:1.1.0" for task_id in NEW_TASKS
}

EXISTING_TASKS: dict[str, dict[str, str]] = {
    "budgeted-imputation-mcar50": {"adapter_id":"imputation-public-v2","candidate_path":"benchmarks/autoresearchexam/solutions/impute.py","public_output_path":"/app/output/impute.py","entrypoint":"impute","metric_id":"imputation_r2","metric_field":"imputation_r2","direction":"MAXIMIZE","aggregation":"MEAN"},
    "carps-star-discrepancy-subset-select": {"adapter_id":"carps-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/carps/select_subset.py","public_output_path":"/app/output/select_subset.py","entrypoint":"select_subset","metric_id":"autoresearchexam.carps.public_task_score","metric_field":"public_task_score","direction":"MAXIMIZE","aggregation":"MEAN"},
    "cpu-decoder-graph-executor": {"adapter_id":"cpudecoder-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/cpu_decoder/executor.py","public_output_path":"/app/output/executor.py","entrypoint":"execute","metric_id":"cpu_decoder_geomean_speedup","metric_field":"geometric_mean_speedup","direction":"MAXIMIZE","aggregation":"MEAN"},
    "cpu-llm-decode-throughput": {"adapter_id":"cpu-llm-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/cpu_llm/engine.py","public_output_path":"/app/output/engine.py","entrypoint":"Engine","metric_id":"cpu_llm_decode_speedup","metric_field":"speedup","direction":"MAXIMIZE","aggregation":"MEDIAN"},
    "dctabeval-aeac-pooled-cat-statistics": {"adapter_id":"dctabeval-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/dctabeval/solution.py","public_output_path":"/app/output/solution.py","entrypoint":"evaluate","metric_id":"roc_auc","metric_field":"roc_auc","direction":"MAXIMIZE","aggregation":"MEAN"},
    "label-efficient-risk-estimator": {"adapter_id":"labelrisk-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/label_risk/estimator.py","public_output_path":"/app/output/estimator.py","entrypoint":"estimate_risk","metric_id":"labelrisk_median_relative_squared_error","metric_field":"median_relative_squared_error_ratio","direction":"MINIMIZE","aggregation":"MEDIAN"},
    "shortest-valid-ci-l2-ece": {"adapter_id":"shortest-ci-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/shortest_valid_ci/interval.py","public_output_path":"/app/output/interval.py","entrypoint":"predict_interval","metric_id":"relative_interval_length","metric_field":"metric","direction":"MINIMIZE","aggregation":"MEAN"},
    "sketched-newton-cov-estimator": {"adapter_id":"sketched-newton-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/sketched_newton/estimator.py","public_output_path":"/app/output/estimator.py","entrypoint":"estimate","metric_id":"task_score","metric_field":"task_score","direction":"MAXIMIZE","aggregation":"MEAN"},
    "sopcc-online-chance-constrained-policy": {"adapter_id":"sopcc-public-v1","candidate_path":"benchmarks/autoresearchexam/solutions/sopcc/policy.py","public_output_path":"/app/solution/policy.py","entrypoint":"policy","metric_id":"sopcc_score","metric_field":"sopcc_score","direction":"MAXIMIZE","aggregation":"MEAN"},
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_public_file(path: Path, root: Path, *, max_bytes: int = 4_000_000) -> bytes:
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise ValueError("public source path escaped its pinned task root") from None
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("public source material contains a symlink")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("public source material is missing or oversized")
    return path.read_bytes()


def _dev_files(task_root: Path, task_id: str, *, required: bool) -> list[dict[str, Any]]:
    if not required:
        return []  # Existing task adapters carry their own complete content pins.
    dev_root = task_root / "environment/app/data/dev"
    if dev_root.is_symlink() or (required and not dev_root.is_dir()):
        raise ValueError(f"{task_id}: public DEV data directory is missing")
    if not dev_root.exists():
        return []
    result = []
    for path in sorted(dev_root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{task_id}: DEV material is not a regular file")
        raw = _read_public_file(path, task_root)
        result.append({"path": path.relative_to(task_root).as_posix(),
                       "size": len(raw), "sha256": _sha(raw)})
    if required and not result:
        raise ValueError(f"{task_id}: public DEV panel is empty")
    return result


def _existing_image_pins() -> dict[str, tuple[str, str]]:
    from benchmarks.autoresearchexam.optimization_runner import AREPublicEvaluatorRunner
    from benchmarks.autoresearchexam.sopcc_runner import AREPublicSOPCCEvaluatorRunner
    from benchmarks.autoresearchexam.carps_runner import AREPublicCARPSEvaluatorRunner
    from benchmarks.autoresearchexam.dctabeval_runner import AREPublicDCTABEvalEvaluatorRunner
    from benchmarks.autoresearchexam.shortest_ci_runner import AREPublicShortestCIEvaluatorRunner
    from benchmarks.autoresearchexam.sketched_newton_runner import AREPublicSketchedNewtonEvaluatorRunner
    from benchmarks.autoresearchexam.labelrisk_runner import AREPublicLabelRiskEvaluatorRunner
    from benchmarks.autoresearchexam.cpudecoder_runner import AREPublicCPUDecoderEvaluatorRunner
    from benchmarks.autoresearchexam.cpu_llm_runner import AREPublicCPULLMEvaluatorRunner
    classes = {
        "budgeted-imputation-mcar50": AREPublicEvaluatorRunner,
        "sopcc-online-chance-constrained-policy": AREPublicSOPCCEvaluatorRunner,
        "carps-star-discrepancy-subset-select": AREPublicCARPSEvaluatorRunner,
        "dctabeval-aeac-pooled-cat-statistics": AREPublicDCTABEvalEvaluatorRunner,
        "shortest-valid-ci-l2-ece": AREPublicShortestCIEvaluatorRunner,
        "sketched-newton-cov-estimator": AREPublicSketchedNewtonEvaluatorRunner,
        "label-efficient-risk-estimator": AREPublicLabelRiskEvaluatorRunner,
        "cpu-decoder-graph-executor": AREPublicCPUDecoderEvaluatorRunner,
        "cpu-llm-decode-throughput": AREPublicCPULLMEvaluatorRunner,
    }
    return {task_id: (cls.default_dev_image, cls.default_dev_image_id)
            for task_id, cls in classes.items()}


def build_catalogue(source_root: Path) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("ARE source root must be a regular directory")
    revision = subprocess.run(["git", "-C", str(source_root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
    if revision != SOURCE_REVISION:
        raise ValueError(f"ARE source revision differs from frozen {SOURCE_REVISION}")
    license_bytes = (source_root / "LICENSE").read_bytes()
    if _sha(license_bytes) != APACHE_LICENSE_SHA256:
        raise ValueError("ARE license differs from the pinned Apache-2.0 license")
    old = json.loads(OLD_SELECTION.read_text(encoding="utf-8"))
    old_tasks = {item["task_id"]: item for item in old["tasks"]
                 if item["task_id"] in EXISTING_TASKS}
    if set(old_tasks) != set(EXISTING_TASKS):
        raise ValueError("pinned existing adapter set is incomplete")
    tasks = []
    mapping = {**EXISTING_TASKS, **NEW_TASKS}
    image_pins = _existing_image_pins()
    for task_id, adapter in mapping.items():
        task_root = source_root / task_id
        task_raw = _read_public_file(task_root / "task.toml", task_root, max_bytes=64_000)
        instruction_raw = _read_public_file(task_root / "instruction.md", task_root, max_bytes=64_000)
        config = tomllib.loads(task_raw.decode("utf-8"))
        version = config.get("task", {}).get("version")
        if version != "1.1.0":
            raise ValueError(f"{task_id}: expected AutoResearchExam task v1.1.0")
        if task_id in old_tasks and (
            _sha(task_raw) != old_tasks[task_id]["task_sha256"]
            or _sha(instruction_raw) != old_tasks[task_id]["instruction_sha256"]
        ):
            raise ValueError(f"{task_id}: source differs from the existing pinned adapter selection")
        env = config.get("environment", {})
        if env.get("gpus") != 0 or env.get("cpus") != 8 or env.get("memory_mb") != 14336:
            raise ValueError(f"{task_id}: task does not match the approved CPU resource class")
        if task_id in NEW_TASKS:
            image_name = NEW_IMAGE_TAGS[task_id]
            image_id = None
        else:
            image_name, image_id = image_pins[task_id]
        dockerfile = _read_public_file(task_root / "environment/Dockerfile", task_root, max_bytes=128_000)
        requirements = _read_public_file(task_root / "environment/requirements.txt", task_root, max_bytes=128_000)
        dockerignore = _read_public_file(task_root / "environment/.dockerignore", task_root, max_bytes=8_000)
        if "are-grader-ctx" not in dockerignore.decode("utf-8"):
            raise ValueError(f"{task_id}: Docker build context does not exclude grader context")
        tasks.append({
            "task_id": task_id, "version": version,
            "task_sha256": _sha(task_raw), "instruction_sha256": _sha(instruction_raw),
            "dockerfile_sha256": _sha(dockerfile), "requirements_sha256": _sha(requirements),
            "dockerignore_sha256": _sha(dockerignore),
            "dev_files": _dev_files(task_root, task_id, required=task_id in NEW_TASKS),
            "adapter": dict(adapter), "resources": {"cpus": 8, "memory_mb": 14336,
                                                        "storage_mb": int(env.get("storage_mb", 0)), "gpus": 0},
            "integration_status": (
                "DEV_SCORER_ONLY_PROTOCOL_FIXTURE_AND_HARBOR_ADAPTER_PENDING"
                if task_id in NEW_TASKS else "PINNED_DEV_VALIDATION_ADAPTER"
            ),
            "license": "Apache-2.0", "license_sha256": APACHE_LICENSE_SHA256,
            "public_dev_image": image_name, "public_dev_image_id": image_id,
        })
    value = {"schema": "bananloop-autonomous-v01-task-catalogue-v1",
             "source_revision": revision, "license_sha256": APACHE_LICENSE_SHA256,
             "task_policy": "CURATED_12_RANDOMIZED_ARM_ORDER", "tasks": tasks}
    value["catalogue_sha256"] = hashlib.sha256(_canonical(value)).hexdigest()
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def write_catalogue(value: dict[str, Any], path: Path = CATALOGUE_PATH) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("catalogue output must be new; refusing to overwrite frozen data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=CATALOGUE_PATH)
    args = parser.parse_args()
    value = build_catalogue(args.source)
    write_catalogue(value, args.out)
    print(json.dumps({"status": "CATALOGUE_BUILT", "catalogue_sha256": value["catalogue_sha256"],
                      "task_count": len(value["tasks"]), "output": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
