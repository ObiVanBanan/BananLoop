"""Build public-only DEV/VALIDATION fixture for CPU LLM throughput."""
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
from .cpu_llm_runner import (
    BACKEND_ID, BACKEND_MANIFEST, CANDIDATE_PATH, DEV_CASE_ID,
    METRIC_AGGREGATION, METRIC_DIRECTION, METRIC_FIELD, METRIC_ID,
    PUBLIC_OUTPUT_PATH, TASK_ID, TASK_VERSION, VALIDATION_CASE_ID,
    AREPublicCPULLMEvaluatorRunner, _BUNDLE_NAMES, _PUBLIC_TASK_FILES,
    _RUNTIME_FILES,
)
from .public_cpu_llm_dev import DEFAULT_IMAGE, DEFAULT_IMAGE_ID, PILOT_DRAWS, PILOT_REQUEST_LIMIT
from .public_mcp_contract import validate_public_mcp_contract

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _read_beneath(root: Path, relative: str, limit: int = 4_000_000) -> bytes:
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("public CPU LLM input contains a symlink")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("public CPU LLM input is missing or oversized")
    return path.read_bytes()


def build_fixture(*, repo: Path, source_task: Path, mcp_contract: Path,
                  out: Path, dev_image: str = DEFAULT_IMAGE,
                  dev_image_id: str = DEFAULT_IMAGE_ID) -> dict[str, Any]:
    repo, source_task, mcp_contract = (p.expanduser() for p in (repo, source_task, mcp_contract))
    if any(path.is_symlink() for path in (repo, source_task, mcp_contract)):
        raise ValueError("fixture input roots and contract must not be symlinks")
    repo, source_task, mcp_contract = (p.resolve() for p in (repo, source_task, mcp_contract))
    if (not repo.is_dir() or not source_task.is_dir() or source_task.name != TASK_ID
            or not mcp_contract.is_file()):
        raise ValueError("repository, public task, or MCP contract is invalid")
    if dev_image != DEFAULT_IMAGE or dev_image_id != DEFAULT_IMAGE_ID or not _IMAGE_ID.fullmatch(dev_image_id):
        raise ValueError("CPU LLM image differs from its frozen local image pin")
    from . import public_cpu_llm_dev as dev
    pins = dev.verify_public_inputs(source_task)
    selection_bytes = _read_beneath(repo, "benchmarks/autoresearchexam/task-selection.json", 64_000)
    if hashlib.sha256(selection_bytes).hexdigest() != dev.SELECTION_SHA256:
        raise ValueError("frozen task selection changed")
    contract_raw = mcp_contract.read_bytes()
    contract = validate_public_mcp_contract(
        json.loads(contract_raw), task_id=TASK_ID, task_version=TASK_VERSION,
        task_sha256=dev.TASK_SHA256, task_instruction_sha256=dev.INSTRUCTION_SHA256)
    if contract.get("public_endpoint") != "http://grader:8000/mcp":
        raise ValueError("public MCP endpoint differs from the pinned ARE endpoint")

    contents: dict[str, bytes] = {"task-selection.json": selection_bytes,
                                  "public-mcp-contract.json": contract_raw}
    for name, relative in _PUBLIC_TASK_FILES.items():
        raw = _read_beneath(source_task, relative)
        expected = next(digest for source, digest in dev._PUBLIC_FILES.values() if source == relative)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("public CPU LLM source differs from its frozen digest")
        contents[name] = raw
    for name, path in _RUNTIME_FILES.items():
        if path.is_symlink():
            raise ValueError("runtime fixture source contains a symlink")
        contents[name] = path.read_bytes()
    if set(contents) | {BACKEND_MANIFEST} != _BUNDLE_NAMES:
        raise ValueError("CPU LLM bundle files differ from the backend allowlist")

    manifest = {
        "schema_version": 1, "backend_id": BACKEND_ID,
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "task_sha256": dev.TASK_SHA256,
        "instruction_sha256": dev.INSTRUCTION_SHA256,
        "selection_sha256": dev.SELECTION_SHA256,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_file_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "dev_image": dev_image, "dev_image_id": dev_image_id,
        "candidate_path": CANDIDATE_PATH, "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
        "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
        "public_draws": list(PILOT_DRAWS), "requests_per_draw": PILOT_REQUEST_LIMIT,
        "model_config_sha256": dev.MODEL_CONFIG_SHA256,
        "model_weights_sha256": dev.MODEL_WEIGHTS_SHA256,
    }
    contents[BACKEND_MANIFEST] = _canonical(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            archive.writestr(name, raw)
    bundle = buffer.getvalue()
    enc = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1, "evaluator_bundle_base64": enc(bundle),
        "execution_backend_id": BACKEND_ID,
        "entrypoint": ["python3", "{evaluator}/cpu_llm_runner.py"],
        "tiers": [
            {"tier": "dev", "cases": [{"case_id": DEV_CASE_ID, "repetition": 1,
             "input_base64": enc(AREPublicCPULLMEvaluatorRunner.aggregate_case_input(
                 tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
                 contract_sha256=contract["contract_sha256"], dev_image_id=dev_image_id,
                 dev_cell_count=len(PILOT_DRAWS)))}]},
            {"tier": "validation", "cases": [{"case_id": VALIDATION_CASE_ID, "repetition": 1,
             "input_base64": enc(AREPublicCPULLMEvaluatorRunner.aggregate_case_input(
                 tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
                 contract_sha256=contract["contract_sha256"], dev_image_id=dev_image_id,
                 dev_cell_count=len(PILOT_DRAWS)))}]},
        ],
    })
    output = out.expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError("fixture output already exists; refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(output)
    return {"fixture_path": str(output.resolve()), "fixture_sha256": fixture.content_digest(),
            "backend_id": BACKEND_ID, "task_id": TASK_ID, "task_version": TASK_VERSION,
            "public_mcp_contract_sha256": contract["contract_sha256"],
            "public_dev_image_id": dev_image_id, "public_draws": list(PILOT_DRAWS),
            "requests_per_draw": PILOT_REQUEST_LIMIT,
            "task_sha256": pins["task_sha256"], "private_grader_accessed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--mcp-contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dev-image", default=DEFAULT_IMAGE)
    parser.add_argument("--dev-image-id", default=DEFAULT_IMAGE_ID)
    args = parser.parse_args()
    print(json.dumps(build_fixture(repo=args.repo, source_task=args.source_task,
        mcp_contract=args.mcp_contract, out=args.out, dev_image=args.dev_image,
        dev_image_id=args.dev_image_id), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
