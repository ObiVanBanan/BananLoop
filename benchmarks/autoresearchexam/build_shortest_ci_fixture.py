"""Build a public-only evaluator fixture for shortest-valid-ci-l2-ece.

The builder only opens the selected task manifest, task.toml, instruction,
public Docker build inputs, public panel generator/data, the observed public
MCP contract, and the named local runtime modules. It never reads private
grader context or test files.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import tempfile
import tomllib
import zipfile
from pathlib import Path

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .public_mcp_contract import validate_public_mcp_contract
from .public_shortest_ci_dev import (
    DEFAULT_REPLICATIONS_PER_SETTING,
    DEV_PANEL_SHA256,
    EXPECTED_SETTINGS,
    INSTRUCTION_SHA256,
    PANEL_SOURCE_SHA256,
    ROOT_SEED,
    TASK_ID,
    TASK_SHA256,
    TASK_VERSION,
)
from .shortest_ci_runner import AREPublicShortestCIEvaluatorRunner


VALIDATION_METRIC_SEMANTICS = (
    "public_are.validation_score=relative_interval_length.lower_is_better.v1"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TASK_FILES = {
    "task-Dockerfile": "environment/Dockerfile",
    "task-requirements.txt": "environment/requirements.txt",
    "task-panel.py": "environment/app/panel.py",
    "task-dev-panel.json": "environment/app/data/dev_panel.json",
}
_RUNTIME_FILES = {
    "public_shortest_ci_dev.py": "public_shortest_ci_dev.py",
    "public_devkit.py": "public_devkit.py",
    "optimization_runner.py": "optimization_runner.py",
    "shortest_ci_runner.py": "shortest_ci_runner.py",
    "public_grader.py": "public_grader.py",
    "are_public_mcp_client.py": "are_public_mcp_client.py",
    "public_mcp_contract.py": "public_mcp_contract.py",
}


def _read_public_file(path: Path, *, root: Path, max_bytes: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("fixture input escaped its declared public root") from None
    current = root
    if current.is_symlink():
        raise ValueError("fixture public root must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("fixture public input contains a symlink")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("fixture public input is missing or oversized")
    return path.read_bytes()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _task_file_hashes(task_toml_raw: bytes, task_files: dict[str, bytes]) -> dict[str, str]:
    """Pin task.toml alongside its public Docker inputs in the backend manifest."""
    names = ("task-Dockerfile", "task-requirements.txt")
    if any(not isinstance(task_files.get(name), bytes) for name in names):
        raise ValueError("public task Docker inputs are missing from the fixture")
    return {
        "task.toml": _digest(task_toml_raw),
        **{name: _digest(task_files[name]) for name in names},
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def build_fixture(
    *, repo: Path,
    source_task: Path,
    mcp_contract_path: Path,
    out: Path,
    dev_image: str,
    dev_image_id: str,
    validation_metric_semantics: str,
) -> dict[str, object]:
    repo, source_task, mcp_contract_path = (
        path.expanduser() for path in (repo, source_task, mcp_contract_path)
    )
    if any(path.is_symlink() for path in (repo, source_task, mcp_contract_path)):
        raise ValueError("fixture roots and MCP contract must not be symlinks")
    repo, source_task, mcp_contract_path = (
        path.resolve() for path in (repo, source_task, mcp_contract_path)
    )
    if not repo.is_dir() or not source_task.is_dir() or source_task.name != TASK_ID:
        raise ValueError("repository or selected public task root is invalid")
    if not isinstance(dev_image, str) or not dev_image or any(ch.isspace() for ch in dev_image):
        raise ValueError("public DEV image name is malformed")
    if not isinstance(dev_image_id, str) or not _SHA256.fullmatch(dev_image_id):
        raise ValueError("public DEV image ID must be explicitly pinned by SHA-256")
    if validation_metric_semantics != VALIDATION_METRIC_SEMANTICS:
        raise ValueError(
            "explicit validation metric semantics must equal "
            f"{VALIDATION_METRIC_SEMANTICS!r}"
        )

    selection_raw = _read_public_file(
        repo / "benchmarks/autoresearchexam/task-selection.json",
        root=repo,
        max_bytes=2_000_000,
    )
    selection = json.loads(selection_raw.decode("utf-8"))
    selected = [item for item in selection.get("tasks", [])
                if isinstance(item, dict) and item.get("task_id") == TASK_ID]
    if (len(selected) != 1 or selected[0].get("version") != TASK_VERSION
            or selected[0].get("task_sha256") != TASK_SHA256
            or selected[0].get("instruction_sha256") != INSTRUCTION_SHA256):
        raise ValueError("shortest-CI task is not pinned exactly once in task-selection.json")

    instruction = _read_public_file(source_task / "instruction.md", root=source_task, max_bytes=64_000)
    task_toml_raw = _read_public_file(source_task / "task.toml", root=source_task, max_bytes=64_000)
    task_toml = tomllib.loads(task_toml_raw.decode("utf-8"))
    if (_digest(instruction) != INSTRUCTION_SHA256
            or _digest(task_toml_raw) != TASK_SHA256
            or task_toml.get("task", {}).get("version") != TASK_VERSION):
        raise ValueError("shortest-CI public task instruction or version digest changed")

    contract_raw = _read_public_file(
        mcp_contract_path, root=mcp_contract_path.parent, max_bytes=128_000,
    )
    contract = validate_public_mcp_contract(
        json.loads(contract_raw.decode("utf-8")),
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        task_sha256=TASK_SHA256,
        task_instruction_sha256=INSTRUCTION_SHA256,
    )
    if (contract.get("public_endpoint") != "http://grader:8000/mcp"
            or not {item.get("name") for item in contract.get("discovered_tools", [])
                    if isinstance(item, dict)} >= {"submit", "result"}):
        raise ValueError("observed public MCP contract lacks the pinned public grading tools")

    task_files = {
        name: _read_public_file(source_task / relative, root=source_task, max_bytes=2_000_000)
        for name, relative in _TASK_FILES.items()
    }
    if (_digest(task_files["task-panel.py"]) != PANEL_SOURCE_SHA256
            or _digest(task_files["task-dev-panel.json"]) != DEV_PANEL_SHA256):
        raise ValueError("public shortest-CI DEV panel source or data digest changed")
    runtime = {}
    for archive_name, local_name in _RUNTIME_FILES.items():
        runtime[archive_name] = _read_public_file(
            repo / "benchmarks/autoresearchexam" / local_name,
            root=repo,
            max_bytes=256_000,
        )
    if (_digest(runtime["public_shortest_ci_dev.py"]) != _digest(
            (repo / "benchmarks/autoresearchexam/public_shortest_ci_dev.py").read_bytes())):
        raise ValueError("local public DEV runner changed while building the fixture")

    cls = AREPublicShortestCIEvaluatorRunner
    task_file_hashes = _task_file_hashes(task_toml_raw, task_files)
    runtime_manifest_names = {
        "public_runner_sha256": "public_shortest_ci_dev.py",
        "public_devkit_sha256": "public_devkit.py",
        "optimization_runner_sha256": "optimization_runner.py",
        "backend_runner_sha256": "shortest_ci_runner.py",
        "public_grader_sha256": "public_grader.py",
        "mcp_client_sha256": "are_public_mcp_client.py",
        "mcp_contract_source_sha256": "public_mcp_contract.py",
    }
    manifest = {
        "schema_version": 1,
        "backend_id": cls.backend_id,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "task_sha256": TASK_SHA256,
        "task_instruction_sha256": INSTRUCTION_SHA256,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_source_sha256": _digest(runtime["public_mcp_contract.py"]),
        "mcp_contract_file_sha256": _digest(contract_raw),
        "public_endpoint": "http://grader:8000/mcp",
        "candidate_path": cls.default_candidate_path,
        "public_output_path": cls.public_output_path,
        "metric_id": cls.metric_id,
        "metric_field": cls.metric_field,
        "direction": "MINIMIZE",
        "aggregation": "MEAN",
        "validation_metric_semantics": validation_metric_semantics,
        "public_dev_image": dev_image,
        "public_dev_image_id": dev_image_id,
        "public_dev_panel_count": EXPECTED_SETTINGS,
        "replications_per_setting": DEFAULT_REPLICATIONS_PER_SETTING,
        "root_seed": ROOT_SEED,
        "panel_source_sha256": PANEL_SOURCE_SHA256,
        "dev_panel_sha256": DEV_PANEL_SHA256,
        "task_file_sha256": task_file_hashes,
        "task_instruction_sha256_verified": _digest(instruction),
        "public_dev_panel_execution": {
            "container_network": "none",
            "root_filesystem_read_only": True,
            "candidate_call_timeout_seconds": 2.0,
            "mounted_candidate_path": cls.public_output_path,
        },
        "public_dev_metric_semantics": {
            "metric": "relative_interval_length",
            "direction": "MINIMIZE",
            "coverage_floor": 0.88,
            "below_coverage_score": 1.0,
        },
        "aggregate_case_ids": {
            "dev": cls.dev_aggregate_case_id,
            "validation": cls.validation_aggregate_case_id,
        },
        **{
            key: _digest(runtime[archive_name])
            for key, archive_name in runtime_manifest_names.items()
        },
    }
    bundle_buffer = io.BytesIO()
    archive_files = {
        "are-public-shortest-ci-backend.json": json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
        **runtime,
        "public-mcp-contract.json": contract_raw,
        "task-instruction.md": instruction,
        "task.toml": task_toml_raw,
        **task_files,
    }
    with zipfile.ZipFile(bundle_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in archive_files.items():
            archive.writestr(name, raw)

    dev_case = cls.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=dev_image_id, dev_cell_count=EXPECTED_SETTINGS,
    )
    validation_case = cls.aggregate_case_input(
        tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=dev_image_id, dev_cell_count=EXPECTED_SETTINGS,
    )
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(bundle_buffer.getvalue()),
        "execution_backend_id": cls.backend_id,
        "entrypoint": ["python3", "{evaluator}/public_shortest_ci_dev.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": cls.dev_aggregate_case_id,
                "repetition": 1,
                "input_base64": encode(dev_case),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": cls.validation_aggregate_case_id,
                "repetition": 1,
                "input_base64": encode(validation_case),
            }]},
        ],
    })
    out = out.expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError("fixture output already exists; refusing to overwrite")
    _atomic_write(out, fixture.canonical_bytes())
    return {
        "fixture_path": str(out.resolve()),
        "fixture_sha256": fixture.content_digest(),
        "evaluator_bundle_sha256": _digest(bundle_buffer.getvalue()),
        "backend_id": cls.backend_id,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "metric_id": cls.metric_id,
        "metric_field": cls.metric_field,
        "direction": "MINIMIZE",
        "protocol_tiers": ["dev", "validation"],
        "public_dev_panel_count": EXPECTED_SETTINGS,
        "replications_per_setting": DEFAULT_REPLICATIONS_PER_SETTING,
        "validation_metric_semantics": validation_metric_semantics,
        "mcp_contract_sha256": contract["contract_sha256"],
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--mcp-contract", type=Path, required=True,
                        help="owner-observed public MCP contract for this exact task")
    parser.add_argument("--dev-image", required=True)
    parser.add_argument("--dev-image-id", required=True)
    parser.add_argument("--validation-metric-semantics", required=True,
                        choices=(VALIDATION_METRIC_SEMANTICS,))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build_fixture(
        repo=args.repo,
        source_task=args.source_task,
        mcp_contract_path=args.mcp_contract,
        out=args.out,
        dev_image=args.dev_image,
        dev_image_id=args.dev_image_id,
        validation_metric_semantics=args.validation_metric_semantics,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
