"""Build a public-only two-tier fixture for one of the three v0.1 CPU tasks."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .public_task_evaluator import AREPublicTaskEvaluatorRunner


_ADAPTERS = {
    "act-tensor-sparse-panel-imputation-r2": "act-tensor-public-v1",
    "causalpfn-cate-pehe-ihdp-surfaceb": "causalpfn-public-v1",
    "hicard-latent-encoder": "hicard-public-v1",
}


def build_fixture(*, repo: Path, source_task: Path,
                  out: Path, adapter_id: str) -> dict[str, Any]:
    repo = repo.expanduser().resolve(strict=True)
    source_task_arg = source_task.expanduser()
    if source_task_arg.is_symlink() or not source_task_arg.is_dir():
        raise ValueError("pinned public task directory is missing or unsafe")
    source_task = source_task_arg.resolve(strict=True)
    catalogue_path = repo / "benchmarks/autonomous_v01/task-catalogue.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    rows = [item for item in catalogue["tasks"] if item.get("task_id") == source_task.name]
    if len(rows) != 1 or _ADAPTERS.get(source_task.name) != adapter_id:
        raise ValueError("task root and adapter ID are not the frozen curated pair")
    task = rows[0]
    task_path = source_task / "task.toml"
    instruction_path = source_task / "instruction.md"
    for path, digest_key in ((task_path, "task_sha256"),
                             (instruction_path, "instruction_sha256")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("public task source file is missing or unsafe")
        if hashlib.sha256(path.read_bytes()).hexdigest() != task[digest_key]:
            raise ValueError("public task source differs from its frozen catalogue pin")
    spec = AREPublicTaskEvaluatorRunner.spec(adapter_id)
    dev_files = AREPublicTaskEvaluatorRunner._public_dev_manifest(source_task, spec["count"])
    task_contract_sha256 = AREPublicTaskEvaluatorRunner.task_contract_digest(
        adapter_id=adapter_id, task_version=task["version"],
        task_sha256=task["task_sha256"],
        instruction_sha256=task["instruction_sha256"], dev_files=dev_files,
    )
    bundle = AREPublicTaskEvaluatorRunner.build_bundle(
        adapter_id=adapter_id, task_id=task["task_id"], task_version=task["version"],
        contract_sha256=task_contract_sha256,
        dev_image_id=spec["image_id"], task_sha256=task["task_sha256"],
        instruction_sha256=task["instruction_sha256"], dev_files=dev_files,
    )
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    tiers = []
    for tier, case_id in (("dev", f"{task['task_id']}-public-dev"),
                          ("validation", f"{task['task_id']}-public-validation")):
        case_input = AREPublicTaskEvaluatorRunner.aggregate_case_input(
            adapter_id=adapter_id, tier=tier, task_id=task["task_id"],
            task_version=task["version"],
            contract_sha256=task_contract_sha256,
            dev_image_id=spec["image_id"], dev_cell_count=spec["count"],
            task_sha256=task["task_sha256"],
            instruction_sha256=task["instruction_sha256"],
        )
        tiers.append({"tier": tier, "cases": [{"case_id": case_id, "repetition": 1,
                                                 "input_base64": encode(case_input)}]})
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1, "evaluator_bundle_base64": encode(bundle),
        "execution_backend_id": f"autoresearchexam-{adapter_id}",
        "entrypoint": ["python3", "{evaluator}/public_task_runner.py"],
        "tiers": tiers,
    })
    out = out.expanduser().absolute()
    if out.exists() or out.is_symlink() or out == repo or repo in out.parents:
        raise ValueError("fixture output must be a new file outside the repository")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=out.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(out)
    return {
        "fixture_path": str(out.resolve()), "fixture_sha256": fixture.content_digest(),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "adapter_id": adapter_id, "backend_id": fixture.execution_backend_id,
        "task_id": task["task_id"], "task_sha256": task["task_sha256"],
        "instruction_sha256": task["instruction_sha256"],
        "public_task_contract_sha256": task_contract_sha256,
        "evaluation_tiers": ["public_dev", "public_dev_reused_for_lifecycle_validation"],
        "public_dev_image": spec["image"], "public_dev_image_id": spec["image_id"],
        "public_dev_cases": spec["count"], "public_dev_files": dev_files,
        "metric_field": spec["metric"], "metric_direction": spec["direction"],
        "private_grader_accessed": False,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--adapter-id", choices=tuple(_ADAPTERS.values()), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo, source_task=args.source_task,
        adapter_id=args.adapter_id,
        out=args.out,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
