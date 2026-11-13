"""Build a public-only DEV/VALIDATION fixture for label-efficient-risk-estimator."""
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

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .labelrisk_runner import (
    BACKEND_ID, BACKEND_MANIFEST, CANDIDATE_PATH, METRIC_FIELD, METRIC_ID,
    PUBLIC_OUTPUT_PATH, VALIDATION_METRIC_SEMANTICS,
    AREPublicLabelRiskEvaluatorRunner, _BUNDLE_NAMES,
    _DEV_FILE_NAMES, _PUBLIC_TASK_FILES, _RUNTIME_FILES,
)
from .public_labelrisk_dev import (
    DEFAULT_IMAGE, DEFAULT_IMAGE_ID, INSTRUCTION_SHA256,
    LABEL_BUDGETS, PUBLIC_DEV_MANIFEST_SHA256, PUBLIC_POOL_IDS,
    ROOT_SEED, SEEDS_PER_CELL, SELECTION_SHA256, TASK_DOCKERFILE_SHA256,
    TASK_REQUIREMENTS_SHA256, TASK_SHA256, TASK_VERSION,
    verify_public_inputs,
)
from .public_mcp_contract import validate_public_mcp_contract


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(root: Path, relative: str, *, cap: int) -> bytes:
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("public fixture contains a symlink")
    if not path.is_file() or path.stat().st_size > cap:
        raise ValueError("public fixture file is missing or oversized")
    return path.read_bytes()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def build_fixture(*, repo: Path, source_task: Path, contract_path: Path,
                  output: Path) -> dict[str, object]:
    repo, source_task, contract_path, output = (
        value.expanduser() for value in (repo, source_task, contract_path, output)
    )
    if any(path.is_symlink() for path in (repo, source_task, contract_path, output)):
        raise ValueError("fixture paths must not be symlinks")
    repo, source_task, contract_path = (
        value.resolve() for value in (repo, source_task, contract_path)
    )
    if not repo.is_dir() or source_task.name != "label-efficient-risk-estimator":
        raise ValueError("repository or public task root is invalid")
    pins = verify_public_inputs(source_task)
    contract_raw = _read(contract_path.parent, contract_path.name, cap=128_000)
    contract = validate_public_mcp_contract(
        json.loads(contract_raw), task_id="label-efficient-risk-estimator",
        task_version=TASK_VERSION, task_sha256=TASK_SHA256,
        task_instruction_sha256=INSTRUCTION_SHA256,
    )
    tools = {item.get("name") for item in contract.get("discovered_tools", []) if isinstance(item, dict)}
    if contract.get("public_endpoint") != "http://grader:8000/mcp" or not {"submit", "result"} <= tools:
        raise ValueError("public MCP contract is missing its submit/result endpoints")

    selection = _read(repo, "benchmarks/autoresearchexam/task-selection.json", cap=2_000_000)
    if _digest(selection) != SELECTION_SHA256:
        raise ValueError("selected task manifest differs from its frozen pin")
    task_files = {
        name: _read(source_task, relative, cap=128_000)
        for name, relative in _PUBLIC_TASK_FILES.items()
    }
    expected_task_hashes = {
        "task.toml": TASK_SHA256,
        "task-instruction.md": INSTRUCTION_SHA256,
        "task-Dockerfile": TASK_DOCKERFILE_SHA256,
        "task-requirements.txt": TASK_REQUIREMENTS_SHA256,
    }
    if any(_digest(task_files[name]) != digest for name, digest in expected_task_hashes.items()):
        raise ValueError("public task files differ from their frozen pins")

    runtime = {
        name: _read(repo, f"benchmarks/autoresearchexam/{path.name}", cap=512_000)
        for name, path in _RUNTIME_FILES.items()
    }
    data_files = {}
    dev_root = source_task / "environment/app/data/dev"
    for pool_id in PUBLIC_POOL_IDS:
        for filename in _DEV_FILE_NAMES:
            member = f"dev/{pool_id}/{filename}"
            data_files[member] = _read(dev_root, f"{pool_id}/{filename}", cap=2_000_000)
    entries = []
    for pool_id in PUBLIC_POOL_IDS:
        for filename in _DEV_FILE_NAMES:
            data = data_files[f"dev/{pool_id}/{filename}"]
            entries.append({"path": f"{pool_id}/{filename}", "sha256": _digest(data), "size": len(data)})
    if _digest(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()) != PUBLIC_DEV_MANIFEST_SHA256:
        raise ValueError("public label-risk DEV arrays differ from their frozen manifest")

    cls = AREPublicLabelRiskEvaluatorRunner
    manifest = {
        "schema_version": 1, "backend_id": BACKEND_ID,
        "task_id": "label-efficient-risk-estimator", "task_version": TASK_VERSION,
        "task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
        "dev_manifest_sha256": PUBLIC_DEV_MANIFEST_SHA256,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_file_sha256": _digest(contract_raw),
        "public_dev_image": DEFAULT_IMAGE, "public_dev_image_id": DEFAULT_IMAGE_ID,
        "candidate_path": CANDIDATE_PATH, "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
        "direction": "MINIMIZE", "aggregation": "MEDIAN",
        "pools": list(PUBLIC_POOL_IDS), "budgets": list(LABEL_BUDGETS),
        "seeds_per_cell": SEEDS_PER_CELL, "root_seed": ROOT_SEED,
    }
    parts = {BACKEND_MANIFEST: json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
             "public-mcp-contract.json": contract_raw,
             "task-selection.json": selection,
             **task_files, **runtime, **data_files}
    if set(parts) != _BUNDLE_NAMES:
        raise ValueError("fixture bundle file set does not match the backend allowlist")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, parts[name])
    bundle = buffer.getvalue()
    if len(bundle) > 24_000_000:
        raise ValueError("public label-risk fixture exceeds the trusted artifact budget")

    cls_kwargs = {
        "task_id": "label-efficient-risk-estimator", "task_version": TASK_VERSION,
        "contract_sha256": contract["contract_sha256"],
        "dev_image_id": DEFAULT_IMAGE_ID,
        "dev_cell_count": cls.default_dev_cell_count,
    }
    dev_input = cls.aggregate_case_input(tier="dev", **cls_kwargs)
    validation_input = cls.aggregate_case_input(tier="validation", **cls_kwargs)
    fixture = EvaluatorFixtureV1(
        evaluator_bundle_base64=base64.b64encode(bundle).decode("ascii"),
        execution_backend_id=BACKEND_ID,
        entrypoint=("python3", "{evaluator}/labelrisk_runner.py"),
        tiers=(
            {"tier": "dev", "cases": [{"case_id": cls.dev_aggregate_case_id,
                                        "repetition": 1,
                                        "input_base64": base64.b64encode(dev_input).decode("ascii")}]},
            {"tier": "validation", "cases": [{"case_id": cls.validation_aggregate_case_id,
                                                 "repetition": 1,
                                                 "input_base64": base64.b64encode(validation_input).decode("ascii")}]},
        ),
    )
    _atomic_write(output, fixture.canonical_bytes())
    return {
        "fixture_path": str(output), "fixture_digest": fixture.content_digest(),
        "evaluator_bundle_sha256": _digest(bundle), "bundle_bytes": len(bundle),
        "task_id": "label-efficient-risk-estimator", "task_version": TASK_VERSION,
        "protocol_tiers": ["dev", "validation"],
        "public_pool_count": len(PUBLIC_POOL_IDS), "public_cell_count": cls.default_dev_cell_count,
        "seeds_per_cell": SEEDS_PER_CELL,
        "task_sha256": pins["task_sha256"],
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(repo=args.repo, source_task=args.source_task,
                                   contract_path=args.contract, output=args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
