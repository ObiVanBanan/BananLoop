"""Create a public-only external-DEV / ARE-VALIDATION CovType fixture.

The external DEV arrays come from independent public UCI rows and use a static
price simulator. The fixture marks this panel as proxy-only; official scoring
is a separate public ARE validation submission. No verifier-side files are
read or packaged.
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
from typing import Any

from bananloop.evaluator_fixture import EvaluatorFixtureV1

from . import public_covtype_dev as dev
from .covtype_runner import (
    BACKEND_ID, BACKEND_MANIFEST, CANDIDATE_PATH, DEV_CASE_ID,
    INSTRUCTION_SHA256, METRIC_AGGREGATION, METRIC_DIRECTION, METRIC_FIELD,
    METRIC_ID, PREPARE_DATA_SHA256, PUBLIC_OUTPUT_PATH, SELECTION_SHA256,
    TASK_ID, TASK_SHA256, TASK_VERSION, VALIDATION_CASE_ID,
    AREPublicCovTypeEvaluatorRunner, _PROXY_FILES, _RUNTIME_FILES,
    _TASK_FILES,
)
from .public_mcp_contract import validate_public_mcp_contract


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _read_file(root: Path, relative: str, max_bytes: int) -> bytes:
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("CovType fixture input contains a symlink")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("CovType fixture input is missing or oversized")
    return path.read_bytes()


def _backend_manifest(contract_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "backend_id": BACKEND_ID,
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "task_sha256": TASK_SHA256,
        "instruction_sha256": INSTRUCTION_SHA256,
        "selection_sha256": SELECTION_SHA256,
        "prepare_data_sha256": PREPARE_DATA_SHA256,
        "proxy_manifest_sha256": dev.PROXY_MANIFEST_SHA256,
        "source_archive_sha256": dev.SOURCE_SHA256,
        "mcp_contract_sha256": contract_sha256,
        "dev_image": dev.DEFAULT_IMAGE,
        "dev_image_id": dev.DEFAULT_IMAGE_ID,
        "candidate_path": CANDIDATE_PATH,
        "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_id": METRIC_ID, "metric_field": METRIC_FIELD,
        "direction": METRIC_DIRECTION, "aggregation": METRIC_AGGREGATION,
        "official_benchmark_compatible": False,
        "price_simulation": "static-public-proxy-v1",
    }


def build_fixture(*, repo: Path, source_task: Path, proxy_root: Path,
                  source_archive: Path, mcp_contract: Path,
                  out: Path) -> dict[str, Any]:
    roots = (repo, source_task, proxy_root, source_archive, mcp_contract)
    if any(path.expanduser().is_symlink() for path in roots):
        raise ValueError("CovType fixture inputs must not be symlinks")
    repo, source_task, proxy_root, source_archive, mcp_contract = (
        path.expanduser().resolve() for path in roots
    )
    if (not repo.is_dir() or source_task.name != TASK_ID or not source_task.is_dir()
            or not proxy_root.is_dir() or not source_archive.is_file()
            or not mcp_contract.is_file()):
        raise ValueError("repository, public task, proxy, archive, or contract is invalid")
    if (dev.DEFAULT_IMAGE_ID == "" or not dev.DEFAULT_IMAGE_ID.startswith("sha256:")
            or len(dev.DEFAULT_IMAGE_ID) != 71):
        raise ValueError("CovType worker image pin is unavailable")

    proxy_manifest = dev.verify_proxy_panel(proxy_root)
    proxy_manifest_raw = (proxy_root / "manifest.json").read_bytes()
    if hashlib.sha256(proxy_manifest_raw).hexdigest() != dev.PROXY_MANIFEST_SHA256:
        raise ValueError("CovType external proxy manifest differs from its frozen pin")
    source_raw = source_archive.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != dev.SOURCE_SHA256:
        raise ValueError("public UCI CoverType source archive differs from its frozen pin")

    selection_raw = _read_file(repo, "benchmarks/autoresearchexam/task-selection.json", 64_000)
    if hashlib.sha256(selection_raw).hexdigest() != SELECTION_SHA256:
        raise ValueError("AutoResearchExam task selection differs from its frozen pin")
    contract_raw = mcp_contract.read_bytes()
    contract = validate_public_mcp_contract(
        json.loads(contract_raw), task_id=TASK_ID, task_version=TASK_VERSION,
        task_sha256=TASK_SHA256, task_instruction_sha256=INSTRUCTION_SHA256,
    )
    if contract.get("public_endpoint") != "http://grader:8000/mcp":
        raise ValueError("public MCP contract endpoint differs from the ARE pin")

    contents: dict[str, bytes] = {
        "task-selection.json": selection_raw,
        "public-mcp-contract.json": contract_raw,
        "uci-covertype.zip": source_raw,
        "proxy-manifest.json": proxy_manifest_raw,
    }
    for bundle_name, relative in _TASK_FILES.items():
        raw = _read_file(source_task, relative, 2_000_000)
        if relative == "task.toml" and hashlib.sha256(raw).hexdigest() != TASK_SHA256:
            raise ValueError("public CovType task manifest differs from its selected task pin")
        if relative == "instruction.md" and hashlib.sha256(raw).hexdigest() != INSTRUCTION_SHA256:
            raise ValueError("public CovType instruction differs from its selected task pin")
        if (relative == "environment/prepare_agent_data.py"
                and hashlib.sha256(raw).hexdigest() != PREPARE_DATA_SHA256):
            raise ValueError("public CovType data preparation differs from its selected pin")
        contents[bundle_name] = raw

    for bundle_name, source_name in _PROXY_FILES.items():
        contents[bundle_name] = _read_file(proxy_root, source_name, 15_000_000)
        if hashlib.sha256(contents[bundle_name]).hexdigest() != proxy_manifest["files_sha256"][source_name]:
            raise ValueError("CovType proxy file differs from its content-addressed manifest")
    for bundle_name, path in _RUNTIME_FILES.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError("CovType runtime fixture source is missing or unsafe")
        contents[bundle_name] = path.read_bytes()
    contents[BACKEND_MANIFEST] = _canonical(_backend_manifest(contract["contract_sha256"]))
    expected_names = {
        BACKEND_MANIFEST, "task-selection.json", "public-mcp-contract.json",
        "uci-covertype.zip", "proxy-manifest.json", *_TASK_FILES,
        *_PROXY_FILES, *_RUNTIME_FILES,
    }
    if set(contents) != expected_names:
        raise ValueError("CovType fixture files differ from the backend allowlist")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(contents.items()):
            archive.writestr(name, raw)
    bundle = buffer.getvalue()
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    dev_input = AREPublicCovTypeEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=dev.DEFAULT_IMAGE_ID, dev_cell_count=1,
    )
    validation_input = AREPublicCovTypeEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=dev.DEFAULT_IMAGE_ID, dev_cell_count=1,
    )
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(bundle),
        "execution_backend_id": BACKEND_ID,
        "entrypoint": ["python3", "{evaluator}/covtype_runner.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": DEV_CASE_ID, "repetition": 1,
                "input_base64": encode(dev_input),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": VALIDATION_CASE_ID, "repetition": 1,
                "input_base64": encode(validation_input),
            }]},
        ],
    })
    output = out.expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError("CovType fixture output already exists; refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(output)
    return {
        "fixture_path": str(output.resolve()),
        "fixture_sha256": fixture.content_digest(),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "bundle_bytes": len(bundle), "backend_id": BACKEND_ID,
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "proxy_manifest_sha256": hashlib.sha256(proxy_manifest_raw).hexdigest(),
        "public_mcp_contract_sha256": contract["contract_sha256"],
        "public_dev_image_id": dev.DEFAULT_IMAGE_ID,
        "official_benchmark_compatible": False,
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True)
    parser.add_argument("--proxy-root", type=Path, default=dev.DEFAULT_PROXY_DIR)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--mcp-contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo, source_task=args.source_task,
        proxy_root=args.proxy_root, source_archive=args.source_archive,
        mcp_contract=args.mcp_contract, out=args.out,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
