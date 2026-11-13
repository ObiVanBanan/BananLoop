"""Build a public-only, owner-pinned evaluator fixture for the ARE imputation task.

Only the task's public instruction, MCP contract, environment devkit, and the
local aggregate runner are read. The grader sidecar and private tests are never
opened by this builder.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from .optimization_runner import AREPublicEvaluatorRunner
from bananloop.evaluator_fixture import EvaluatorFixtureV1


TASK_ID = "budgeted-imputation-mcar50"
TASK_VERSION = "1.1.0"


def _read_public_file(path: Path, *, root: Path, max_bytes: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("public fixture input escaped its declared root") from None
    current = root
    if current.is_symlink():
        raise ValueError("public fixture root must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"public fixture input contains a symlink: {current.name}")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError(f"public fixture input is missing or unsafe: {path.name}")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError(f"public fixture input is not text: {path.name}")
    return raw


def build_fixture(*, repo: Path, source_task: Path, out: Path) -> dict[str, object]:
    repo = repo.expanduser()
    source_task = source_task.expanduser()
    if repo.is_symlink() or source_task.is_symlink():
        raise ValueError("repository and public source task roots must not be symlinks")
    repo = repo.resolve()
    source_task = source_task.resolve()
    if not repo.is_dir() or not source_task.is_dir():
        raise ValueError("repository and public source task must be directories")
    selection = json.loads(_read_public_file(
        repo / "benchmarks/autoresearchexam/task-selection.json", root=repo, max_bytes=2_000_000,
    ))
    tasks = [item for item in selection.get("tasks", []) if item.get("task_id") == TASK_ID]
    if len(tasks) != 1 or tasks[0].get("version") != TASK_VERSION:
        raise ValueError("imputation task is not uniquely pinned in task-selection.json")
    contract_raw = _read_public_file(
        repo / "benchmarks/autoresearchexam/public-mcp-contract-budgeted-imputation-mcar50.json",
        root=repo, max_bytes=128_000,
    )
    contract = json.loads(contract_raw)
    contract_digest = contract.get("contract_sha256")
    if (not isinstance(contract_digest, str)
            or len(contract_digest) != 64
            or any(char not in "0123456789abcdef" for char in contract_digest)):
        raise ValueError("public MCP contract has no valid pinned digest")
    instruction = _read_public_file(
        source_task / "instruction.md", root=source_task, max_bytes=64_000,
    )
    instruction_hash = hashlib.sha256(instruction).hexdigest()
    if (contract.get("task_id") != TASK_ID
            or contract.get("task_version") != TASK_VERSION
            or contract.get("task_instruction_sha256") != tasks[0].get("instruction_sha256")
            or instruction_hash != tasks[0].get("instruction_sha256")
            or contract.get("task_sha256") != tasks[0].get("task_sha256")
            or not {item.get("name") for item in contract.get("discovered_tools", [])
                    if isinstance(item, dict)} >= {"submit", "result"}):
        raise ValueError("public MCP contract does not match the selected task and tool allowlist")

    runner_source = _read_public_file(
        repo / "benchmarks/autoresearchexam/public_devkit.py", root=repo, max_bytes=32_000,
    )
    backend_source = _read_public_file(
        repo / "benchmarks/autoresearchexam/optimization_runner.py", root=repo,
        max_bytes=32_000,
    )
    grader_source = _read_public_file(
        repo / "benchmarks/autoresearchexam/public_grader.py", root=repo,
        max_bytes=32_000,
    )
    mcp_client_source = _read_public_file(
        repo / "benchmarks/autoresearchexam/are_public_mcp_client.py", root=repo,
        max_bytes=24_000,
    )
    task_devkit = _read_public_file(
        source_task / "environment/app/devkit.py", root=source_task, max_bytes=32_000,
    )
    cell_spec = _read_public_file(
        source_task / "environment/app/cellspec.py", root=source_task, max_bytes=16_000,
    )
    cell_runner = _read_public_file(
        source_task / "environment/app/cellrunner.py", root=source_task, max_bytes=4_000,
    )
    runner_hash = hashlib.sha256(runner_source).hexdigest()
    backend_hash = hashlib.sha256(backend_source).hexdigest()
    grader_hash = hashlib.sha256(grader_source).hexdigest()
    mcp_client_hash = hashlib.sha256(mcp_client_source).hexdigest()
    task_devkit_hash = hashlib.sha256(task_devkit).hexdigest()
    cell_spec_hash = hashlib.sha256(cell_spec).hexdigest()
    cell_runner_hash = hashlib.sha256(cell_runner).hexdigest()
    instruction_hash = hashlib.sha256(instruction).hexdigest()
    manifest = {
        "schema_version": 1,
        "backend_id": AREPublicEvaluatorRunner.backend_id,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "mcp_contract_sha256": contract_digest,
        "mcp_endpoint": "http://grader:8000/mcp",
        "candidate_path": "benchmarks/autoresearchexam/solutions/impute.py",
        "public_output_path": "/app/output/impute.py",
        "public_runner_sha256": runner_hash,
        "optimization_runner_sha256": backend_hash,
        "public_grader_sha256": grader_hash,
        "mcp_client_sha256": mcp_client_hash,
        "task_devkit_sha256": task_devkit_hash,
        "task_instruction_sha256": instruction_hash,
        "cellspec_sha256": cell_spec_hash,
        "cellrunner_sha256": cell_runner_hash,
        "mcp_contract_source_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "public_dev_image": AREPublicEvaluatorRunner.default_dev_image,
        "public_dev_image_id": AREPublicEvaluatorRunner.default_dev_image_id,
        "public_dev_cell_count": AREPublicEvaluatorRunner.default_dev_cell_count,
        "dev_execution": {
            "container_network": "none",
            "root_filesystem_read_only": True,
            "mounted_candidate_path": "/app/output/impute.py",
        },
        "aggregate_case_ids": {
            "dev": AREPublicEvaluatorRunner.dev_aggregate_case_id,
            "validation": AREPublicEvaluatorRunner.validation_aggregate_case_id,
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("are-public-backend.json", json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
        ))
        archive.writestr("public_devkit.py", runner_source)
        archive.writestr("task-devkit.py", task_devkit)
        archive.writestr("public-mcp-contract.json", contract_raw)
        archive.writestr("optimization_runner.py", backend_source)
        archive.writestr("public_grader.py", grader_source)
        archive.writestr("are_public_mcp_client.py", mcp_client_source)
        archive.writestr("task-instruction.md", instruction)
        archive.writestr("cellspec.py", cell_spec)
        archive.writestr("cellrunner.py", cell_runner)
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    dev_input = AREPublicEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract_digest,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )
    validation_input = AREPublicEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract_digest,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(buffer.getvalue()),
        "execution_backend_id": AREPublicEvaluatorRunner.backend_id,
        "entrypoint": ["python3", "{evaluator}/public_devkit.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": AREPublicEvaluatorRunner.dev_aggregate_case_id,
                "repetition": 1, "input_base64": encode(dev_input),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": AREPublicEvaluatorRunner.validation_aggregate_case_id,
                "repetition": 1, "input_base64": encode(validation_input),
            }]},
        ],
    })
    out = out.expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError("output path already exists; refusing to overwrite a pinned fixture")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=out.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(out)
    return {
        "fixture_path": str(out.resolve()),
        "fixture_sha256": hashlib.sha256(fixture.canonical_bytes()).hexdigest(),
        "backend_id": AREPublicEvaluatorRunner.backend_id,
        "task_id": TASK_ID,
        "protocol_tiers": ["dev", "validation"],
        "public_runner_sha256": runner_hash,
        "optimization_runner_sha256": backend_hash,
        "public_grader_sha256": grader_hash,
        "mcp_client_sha256": mcp_client_hash,
        "task_devkit_sha256": task_devkit_hash,
        "task_instruction_sha256": instruction_hash,
        "cellspec_sha256": cell_spec_hash,
        "cellrunner_sha256": cell_runner_hash,
        "evaluator_bundle_bytes": len(buffer.getvalue()),
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True,
                        help="public budgeted-imputation-mcar50 task directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo, source_task=args.source_task, out=args.out,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
