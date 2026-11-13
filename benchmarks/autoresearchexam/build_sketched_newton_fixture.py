"""Build a public-only DEV/VALIDATION fixture for sketched Newton.

Inputs are limited to the selected task manifest, public instruction, task
metadata, public runner, and its two public DEV files. No grader-side files or
hidden instances are read. The MCP contract must have been observed separately
with the read-only public contract probe.
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
import zipfile
from pathlib import Path
from typing import Any

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from .public_mcp_contract import validate_public_mcp_contract
from .sketched_newton_runner import (
    BACKEND_ID, BACKEND_MANIFEST, CANDIDATE_PATH, DEV_CASE_ID,
    METRIC_AGGREGATION, METRIC_DIRECTION, METRIC_FIELD, PUBLIC_OUTPUT_PATH,
    TASK_ID, TASK_VERSION, VALIDATION_CASE_ID,
    AREPublicSketchedNewtonEvaluatorRunner, _BUNDLE_NAMES, _RUNTIME_FILES,
)
from . import public_sketched_newton_dev as dev


_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PUBLIC_FILES = {
    "task.toml": ("task.toml", 64_000),
    "task-instruction.md": ("instruction.md", 64_000),
    "task-Dockerfile": ("environment/Dockerfile", 64_000),
    "task-dockerignore": ("environment/.dockerignore", 8_000),
    "task-requirements.txt": ("environment/requirements.txt", 8_000),
    "public-runner.py": ("environment/app/harness/runner.py", 64_000),
    "dev-instance-0.json": ("environment/app/harness/dev/inst_0.json", 8_000),
    "dev-xi-0.npy": ("environment/app/harness/dev/inst_0_xi_star.npy", 64_000),
}


def _read_beneath(root: Path, relative: str, max_bytes: int) -> bytes:
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("public Newton fixture input contains a symlink")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("public Newton fixture input is missing or oversized")
    return path.read_bytes()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def build_fixture(*, repo: Path, source_task: Path, mcp_contract: Path,
                  out: Path, dev_image: str, dev_image_id: str) -> dict[str, Any]:
    repo, source_task, mcp_contract = (p.expanduser() for p in (repo, source_task, mcp_contract))
    if any(p.is_symlink() for p in (repo, source_task, mcp_contract)):
        raise ValueError("fixture roots and contract must not be symlinks")
    repo, source_task, mcp_contract = (p.resolve() for p in (repo, source_task, mcp_contract))
    if (not repo.is_dir() or not source_task.is_dir() or not mcp_contract.is_file()
            or source_task.name != TASK_ID):
        raise ValueError("repository, observed contract, or public task root is invalid")
    if (dev_image != dev.DEFAULT_IMAGE or dev_image_id != dev.DEFAULT_IMAGE_ID
            or not _IMAGE_ID_RE.fullmatch(dev_image_id)):
        raise ValueError("Newton public DEV image tag or immutable image pin differs from the local build")

    pins = dev.verify_public_newton_inputs(source_task)
    selection_path = repo / "benchmarks/autoresearchexam/task-selection.json"
    selection_bytes = selection_path.read_bytes()
    if hashlib.sha256(selection_bytes).hexdigest() != dev.SELECTION_SHA256:
        raise ValueError("frozen AutoResearchExam selection has changed")
    contract_raw = mcp_contract.read_bytes()
    contract = validate_public_mcp_contract(
        json.loads(contract_raw), task_id=TASK_ID, task_version=TASK_VERSION,
        task_sha256=dev.TASK_SHA256, task_instruction_sha256=dev.INSTRUCTION_SHA256,
    )
    if contract.get("public_endpoint") != "http://grader:8000/mcp":
        raise ValueError("public MCP contract endpoint differs from the ARE pin")

    public: dict[str, bytes] = {}
    for name, (relative, limit) in _PUBLIC_FILES.items():
        public[name] = _read_beneath(source_task, relative, limit)
    expected_public = {
        "task.toml": dev.TASK_SHA256,
        "task-instruction.md": dev.INSTRUCTION_SHA256,
        "public-runner.py": dev.RUNNER_SHA256,
        "dev-instance-0.json": dev.DEV0_JSON_SHA256,
        "dev-xi-0.npy": dev.DEV0_XI_SHA256,
    }
    for name, digest in expected_public.items():
        if hashlib.sha256(public[name]).hexdigest() != digest:
            raise ValueError("public Newton material differs from its frozen digest")
    if public["task-dockerignore"].decode("utf-8").strip() != "are-grader-ctx":
        raise ValueError("public Docker context does not explicitly exclude grader context")

    runtime = {
        name: _read_beneath(repo, f"benchmarks/autoresearchexam/{name}", 2_000_000)
        for name in _RUNTIME_FILES
    }
    for name, path in _RUNTIME_FILES.items():
        if runtime[name] != path.read_bytes():
            raise ValueError("Newton evaluator runtime changed while building its fixture")
    manifest = {
        "schema_version": 1,
        "backend_id": BACKEND_ID,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "candidate_path": CANDIDATE_PATH,
        "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_field": METRIC_FIELD,
        "metric_direction": METRIC_DIRECTION,
        "metric_aggregation": METRIC_AGGREGATION,
        "task_selection_sha256": dev.SELECTION_SHA256,
        "task_sha256": dev.TASK_SHA256,
        "instruction_sha256": dev.INSTRUCTION_SHA256,
        "public_runner_sha256": dev.RUNNER_SHA256,
        "dev_instance_sha256": dev.DEV0_JSON_SHA256,
        "dev_target_sha256": dev.DEV0_XI_SHA256,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_source_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "mcp_endpoint": "http://grader:8000/mcp",
        "public_dev_image": dev_image,
        "public_dev_image_id": dev_image_id,
        "public_dev_scope": {"instance": "dev0", "steps": 300_000, "replications": 1},
        "validation_metric_semantics": "public_are.validation_score=higher_is_better_task_reward.v1",
        "aggregate_case_ids": {"dev": DEV_CASE_ID, "validation": VALIDATION_CASE_ID},
    }
    contents = {**runtime, **public, "task-selection.json": selection_bytes,
                "public-mcp-contract.json": contract_raw,
                BACKEND_MANIFEST: _canonical(manifest)}
    if set(contents) != _BUNDLE_NAMES:
        raise ValueError("Newton evaluator bundle contents differ from the backend allowlist")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            archive.writestr(name, raw)
    bundle = buffer.getvalue()
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(bundle),
        "execution_backend_id": BACKEND_ID,
        "entrypoint": ["python3", "{evaluator}/sketched_newton_runner.py"],
        "tiers": [
            {"tier": "dev", "cases": [{"case_id": DEV_CASE_ID, "repetition": 1,
              "input_base64": encode(AREPublicSketchedNewtonEvaluatorRunner.aggregate_case_input(
                  tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
                  contract_sha256=contract["contract_sha256"], dev_image_id=dev_image_id,
                  dev_cell_count=1))}]},
            {"tier": "validation", "cases": [{"case_id": VALIDATION_CASE_ID, "repetition": 1,
              "input_base64": encode(AREPublicSketchedNewtonEvaluatorRunner.aggregate_case_input(
                  tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
                  contract_sha256=contract["contract_sha256"], dev_image_id=dev_image_id,
                  dev_cell_count=1))}]},
        ],
    })
    output = out.expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError("fixture output already exists; refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(output)
    return {
        "fixture_path": str(output.resolve()),
        "fixture_sha256": fixture.content_digest(),
        "backend_id": BACKEND_ID, "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "public_mcp_contract_sha256": contract["contract_sha256"],
        "public_dev_image_id": dev_image_id,
        "public_dev_scope": manifest["public_dev_scope"],
        "metric_field": METRIC_FIELD, "metric_direction": METRIC_DIRECTION,
        "protocol_tiers": ["dev", "validation"],
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--mcp-contract", type=Path, required=True)
    parser.add_argument("--dev-image", required=True)
    parser.add_argument("--dev-image-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo, source_task=args.source_task,
        mcp_contract=args.mcp_contract, out=args.out,
        dev_image=args.dev_image, dev_image_id=args.dev_image_id,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
