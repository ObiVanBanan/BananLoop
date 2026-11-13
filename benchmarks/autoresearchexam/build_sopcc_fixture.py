"""Build the owner-pinned public evaluator fixture for SOPCC v1.1.0.

Inputs are restricted to the public task instruction/environment and a
separately observed public MCP tool contract. The task's private verifier
context is never opened.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .optimization_runner import AREPublicEvaluatorRunner
from .public_mcp_contract import validate_public_mcp_contract
from .sopcc_runner import AREPublicSOPCCEvaluatorRunner


TASK_ID = "sopcc-online-chance-constrained-policy"
TASK_VERSION = "1.1.0"
_TASK_FILES = {
    "task-Dockerfile": "environment/Dockerfile",
    "task-requirements.txt": "environment/requirements.txt",
    "task-dev_eval.py": "environment/app/harness/dev_eval.py",
    "task-runner.py": "environment/app/harness/runner.py",
    "task-sopcc.py": "environment/app/harness/sopcc.py",
    "task-policy_worker.py": "environment/app/harness/policy_worker.py",
}


def _read(path: Path, *, root: Path, limit: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("SOPCC public fixture input escaped its declared root") from None
    current = root
    if current.is_symlink():
        raise ValueError("SOPCC fixture root must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("SOPCC fixture input contains a symlink")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("SOPCC public fixture input is missing or oversized")
    return path.read_bytes()


def build_fixture(*, repo: Path, source_task: Path, mcp_contract: Path,
                  out: Path) -> dict[str, object]:
    repo, source_task, mcp_contract = (p.expanduser() for p in (repo, source_task, mcp_contract))
    if any(path.is_symlink() for path in (repo, source_task, mcp_contract)):
        raise ValueError("SOPCC fixture inputs must not be symlinks")
    repo, source_task, mcp_contract = (p.resolve() for p in (repo, source_task, mcp_contract))
    selection = json.loads(_read(
        repo / "benchmarks/autoresearchexam/task-selection.json", root=repo, limit=2_000_000,
    ))
    selected = [item for item in selection.get("tasks", []) if item.get("task_id") == TASK_ID]
    if len(selected) != 1 or selected[0].get("version") != TASK_VERSION:
        raise ValueError("SOPCC is not pinned exactly once in the selected task manifest")

    instruction = _read(source_task / "instruction.md", root=source_task, limit=64_000)
    contract_raw = _read(mcp_contract, root=mcp_contract.parent, limit=128_000)
    contract = validate_public_mcp_contract(json.loads(contract_raw))
    instruction_sha = hashlib.sha256(instruction).hexdigest()
    contract_sha = contract.get("contract_sha256")
    if (contract.get("schema_version") != 1
            or contract.get("task_id") != TASK_ID
            or contract.get("task_version") != TASK_VERSION
            or contract.get("task_sha256") != selected[0].get("task_sha256")
            or contract.get("task_instruction_sha256") != instruction_sha
            or instruction_sha != selected[0].get("instruction_sha256")
            or contract.get("public_endpoint") != "http://grader:8000/mcp"
            or not isinstance(contract_sha, str) or len(contract_sha) != 64
            or any(c not in "0123456789abcdef" for c in contract_sha)
            or not {item.get("name") for item in contract.get("discovered_tools", [])
                    if isinstance(item, dict)} >= {"submit", "result"}):
        raise ValueError("observed public MCP contract does not match selected SOPCC task")

    task_files = {
        name: _read(source_task / relative, root=source_task, limit=128_000)
        for name, relative in _TASK_FILES.items()
    }
    runtime_paths = {
        "public_sopcc_dev.py": repo / "benchmarks/autoresearchexam/public_sopcc_dev.py",
        "optimization_runner.py": repo / "benchmarks/autoresearchexam/optimization_runner.py",
        "sopcc_runner.py": repo / "benchmarks/autoresearchexam/sopcc_runner.py",
        "public_mcp_contract.py": repo / "benchmarks/autoresearchexam/public_mcp_contract.py",
        "public_grader.py": repo / "benchmarks/autoresearchexam/public_grader.py",
        "are_public_mcp_client.py": repo / "benchmarks/autoresearchexam/are_public_mcp_client.py",
    }
    runtime = {
        name: _read(path, root=repo, limit=64_000)
        for name, path in runtime_paths.items()
    }
    cls = AREPublicSOPCCEvaluatorRunner
    candidate_path = "benchmarks/autoresearchexam/solutions/sopcc/policy.py"
    task_file_digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in task_files.items()}
    manifest = {
        "schema_version": 1, "backend_id": cls.backend_id,
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "mcp_contract_sha256": contract_sha,
        "mcp_endpoint": "http://grader:8000/mcp",
        "candidate_path": candidate_path,
        "public_output_path": "/app/solution/policy.py",
        "public_runner_sha256": hashlib.sha256(runtime["public_sopcc_dev.py"]).hexdigest(),
        "optimization_runner_sha256": hashlib.sha256(runtime["optimization_runner.py"]).hexdigest(),
        "sopcc_runner_sha256": hashlib.sha256(runtime["sopcc_runner.py"]).hexdigest(),
        "public_mcp_contract_sha256": hashlib.sha256(runtime["public_mcp_contract.py"]).hexdigest(),
        "public_grader_sha256": hashlib.sha256(runtime["public_grader.py"]).hexdigest(),
        "mcp_client_sha256": hashlib.sha256(runtime["are_public_mcp_client.py"]).hexdigest(),
        "task_instruction_sha256": instruction_sha,
        "task_file_sha256": task_file_digests,
        "public_dev_image": cls.default_dev_image,
        "public_dev_image_id": cls.default_dev_image_id,
        "public_dev_episode_count": cls.default_dev_cell_count,
        "public_dev_panel": {
            "draws": list(cls.dev_draws),
            "instances_per_draw": cls.dev_instances_per_draw,
            "episodes_per_instance": cls.dev_episodes_per_instance,
        },
        "metric_contract": {
            "metric_id": "sopcc_score", "metric_field": "sopcc_score",
            "unit": "reward", "direction": "MAXIMIZE", "aggregation": "MEAN",
        },
        "dev_execution": {
            "container_network": "none", "root_filesystem_read_only": True,
            "mounted_candidate_path": "/app/solution/policy.py",
        },
        "aggregate_case_ids": {
            "dev": cls.dev_aggregate_case_id,
            "validation": cls.validation_aggregate_case_id,
        },
    }
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in {
            "are-public-sopcc-backend.json": json.dumps(
                manifest, sort_keys=True, separators=(",", ":"),
            ).encode(),
            **runtime, "public-mcp-contract.json": contract_raw,
            "task-instruction.md": instruction, **task_files,
        }.items():
            archive.writestr(name, raw)

    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(archive_bytes.getvalue()),
        "execution_backend_id": cls.backend_id,
        "entrypoint": ["python3", "{evaluator}/public_sopcc_dev.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": cls.dev_aggregate_case_id, "repetition": 1,
                "input_base64": encode(cls.aggregate_case_input(
                    tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
                    contract_sha256=contract_sha, dev_image_id=cls.default_dev_image_id,
                    dev_cell_count=cls.default_dev_cell_count,
                )),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": cls.validation_aggregate_case_id, "repetition": 1,
                "input_base64": encode(cls.aggregate_case_input(
                    tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
                    contract_sha256=contract_sha, dev_image_id=cls.default_dev_image_id,
                    dev_cell_count=cls.default_dev_cell_count,
                )),
            }]},
        ],
    })
    out = out.expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError("SOPCC fixture output already exists; refusing to overwrite")
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
        "evaluator_bundle_sha256": hashlib.sha256(archive_bytes.getvalue()).hexdigest(),
        "backend_id": cls.backend_id, "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "protocol_tiers": ["dev", "validation"],
        "private_grader_accessed": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--mcp-contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo, source_task=args.source_task,
        mcp_contract=args.mcp_contract, out=args.out,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
