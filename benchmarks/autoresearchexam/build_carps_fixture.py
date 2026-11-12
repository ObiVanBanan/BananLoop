"""Build a public-only, content-pinned CARPS evaluator fixture.

The builder reads only the selected task identity, public task metadata and
instruction, public example clouds, an already-observed public MCP contract,
and BananLoop's public evaluator runtime modules. It never calls the grader or
reads task tests, hidden arrays, or grader-side assets.
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

from .carps_runner import (
    BACKEND_ID,
    CANDIDATE_PATH,
    DEV_METRIC_SOURCE,
    METRIC_DIRECTION,
    METRIC_FIELD,
    METRIC_ID,
    PUBLIC_OUTPUT_PATH,
    PUBLIC_PANEL_DIGEST,
    PUBLIC_MCP_ENDPOINT,
    TASK_ID,
    TASK_VERSION,
    VALIDATION_METRIC_SOURCE,
    VALIDATION_METRIC_SEMANTICS,
    AREPublicCARPSEvaluatorRunner,
    _BACKEND_FILES,
    _RUNTIME_SOURCES,
)
from .public_carps_dev import (
    EXAMPLE_CLOUD_SHA256,
    EXAMPLES_MANIFEST_SHA256,
    INSTRUCTION_SHA256,
    TASK_TOML_SHA256,
)
from .public_mcp_contract import validate_public_mcp_contract


_PUBLIC_TASK_FILES = {
    "task.toml": "task.toml",
    "task-instruction.md": "instruction.md",
    "task-README.md": "README.md",
    "examples-manifest.json": "environment/app/data/examples/manifest.json",
    **{
        f"examples/{name}": f"environment/app/data/examples/{name}"
        for name in EXAMPLE_CLOUD_SHA256
    },
}
_MAX_BUNDLE_BYTES = 25 * 1024 * 1024
_MAX_FILE_BYTES = 8 * 1024 * 1024
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$")


def _read_public_file(path: Path, *, root: Path, limit: int) -> bytes:
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
            raise ValueError("public fixture input contains a symlink")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("public fixture input is missing or oversized")
    return path.read_bytes()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _panel_digest(manifest_bytes: bytes, cloud_digests: dict[str, str]) -> str:
    body = b"\n".join(
        f"{name}:{cloud_digests[name]}".encode("ascii")
        for name in sorted(cloud_digests)
    )
    return _digest(manifest_bytes + b"\n" + body)


def build_fixture(
    *, repo: Path, source_task: Path, mcp_contract: Path,
    public_dev_image: str, public_dev_image_id: str, out: Path,
) -> dict[str, Any]:
    repo = repo.expanduser()
    source_task = source_task.expanduser()
    mcp_contract = mcp_contract.expanduser()
    if any(path.is_symlink() for path in (repo, source_task, mcp_contract)):
        raise ValueError("fixture roots and contract must not be symlinks")
    repo, source_task, mcp_contract = (path.resolve() for path in (repo, source_task, mcp_contract))
    if not repo.is_dir() or not source_task.is_dir() or not mcp_contract.is_file():
        raise ValueError("repository, public task, or MCP contract is missing")
    if not _IMAGE_REF_RE.fullmatch(public_dev_image) or public_dev_image.startswith("-"):
        raise ValueError("public DEV image reference is malformed")
    if not _IMAGE_ID_RE.fullmatch(public_dev_image_id):
        raise ValueError("public DEV image ID must be a pinned Docker SHA-256 ID")

    selection_raw = _read_public_file(
        repo / "benchmarks/autoresearchexam/task-selection.json",
        root=repo, limit=2_000_000,
    )
    selection = json.loads(selection_raw)
    selected = [item for item in selection.get("tasks", []) if item.get("task_id") == TASK_ID]
    if len(selected) != 1 or selected[0].get("version") != TASK_VERSION:
        raise ValueError("CARPS must be pinned exactly once in the ten-task selection")

    task_files: dict[str, bytes] = {}
    for archive_name, relative_path in _PUBLIC_TASK_FILES.items():
        raw = _read_public_file(source_task / relative_path, root=source_task, limit=_MAX_FILE_BYTES)
        task_files[archive_name] = raw
    task_toml = task_files["task.toml"]
    instruction = task_files["task-instruction.md"]
    readme = task_files["task-README.md"]
    if (_digest(task_toml) != selected[0].get("task_sha256")
            or _digest(task_toml) != TASK_TOML_SHA256
            or _digest(instruction) != selected[0].get("instruction_sha256")
            or _digest(instruction) != INSTRUCTION_SHA256):
        raise ValueError("CARPS public task metadata differs from the frozen selection")
    if not readme.startswith(b"# Select points that cover a cube evenly\n"):
        raise ValueError("CARPS public README has an unexpected task identity")

    examples_manifest = task_files["examples-manifest.json"]
    if _digest(examples_manifest) != EXAMPLES_MANIFEST_SHA256:
        raise ValueError("CARPS public examples manifest differs from its frozen digest")
    cloud_digests = {
        archive_name.removeprefix("examples/"): _digest(task_files[archive_name])
        for archive_name in task_files if archive_name.startswith("examples/")
    }
    if cloud_digests != EXAMPLE_CLOUD_SHA256:
        raise ValueError("CARPS public example cloud bytes differ from the frozen panel")
    if _panel_digest(examples_manifest, cloud_digests) != PUBLIC_PANEL_DIGEST:
        raise ValueError("CARPS public panel aggregate digest differs from its evaluator pin")

    contract_raw = _read_public_file(mcp_contract, root=mcp_contract.parent, limit=128_000)
    try:
        contract = validate_public_mcp_contract(
            json.loads(contract_raw), task_id=TASK_ID, task_version=TASK_VERSION,
            task_sha256=TASK_TOML_SHA256,
            task_instruction_sha256=INSTRUCTION_SHA256,
        )
    except (ValueError, json.JSONDecodeError):
        raise ValueError("observed public MCP contract does not match the selected CARPS task") from None
    if (contract.get("public_endpoint") != PUBLIC_MCP_ENDPOINT
            or not {item.get("name") for item in contract.get("discovered_tools", [])
                    if isinstance(item, dict)} >= {"submit", "result"}):
        raise ValueError("CARPS public MCP contract lacks the pinned endpoint or tools")

    runtime = {
        name: _read_public_file(path, root=repo, limit=2_000_000)
        for name, path in _RUNTIME_SOURCES.items()
    }
    runtime_digests = {name: _digest(raw) for name, raw in runtime.items()}
    task_asset_digests = {
        "task.toml": _digest(task_toml),
        "instruction.md": _digest(instruction),
        "examples/manifest.json": _digest(examples_manifest),
        **{f"examples/{name}": value for name, value in cloud_digests.items()},
    }
    manifest = {
        "schema_version": 1,
        "backend_id": BACKEND_ID,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "task_toml_sha256": TASK_TOML_SHA256,
        "task_instruction_sha256": INSTRUCTION_SHA256,
        "task_readme_sha256": _digest(readme),
        "public_examples_manifest_sha256": EXAMPLES_MANIFEST_SHA256,
        "public_example_cloud_sha256": EXAMPLE_CLOUD_SHA256,
        "public_panel_sha256": PUBLIC_PANEL_DIGEST,
        "mcp_endpoint": PUBLIC_MCP_ENDPOINT,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_source_sha256": _digest(contract_raw),
        "candidate_path": CANDIDATE_PATH,
        "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_id": METRIC_ID,
        "metric_field": METRIC_FIELD,
        "metric_direction": METRIC_DIRECTION,
        "dev_metric_source": DEV_METRIC_SOURCE,
        "dev_metric_transform": "negative mean exact L-infinity star discrepancy",
        "validation_metric_source": VALIDATION_METRIC_SOURCE,
        "validation_metric_semantics": VALIDATION_METRIC_SEMANTICS,
        "validation_result_field": "validation_score",
        "public_dev_image": public_dev_image,
        "public_dev_image_id": public_dev_image_id,
        "public_dev_example_count": 8,
        "per_select_time_budget_seconds": 10,
        "per_select_process_wall_cap_seconds": 15,
        "runtime_source_sha256": runtime_digests,
        "task_assets_sha256": task_asset_digests,
        "dev_execution": {
            "container_network": "none",
            "root_filesystem_read_only": True,
            "mounted_candidate_path": PUBLIC_OUTPUT_PATH,
            "mounted_public_data": "one pinned example cloud per process",
        },
        "aggregate_case_ids": {
            "dev": AREPublicCARPSEvaluatorRunner.dev_aggregate_case_id,
            "validation": AREPublicCARPSEvaluatorRunner.validation_aggregate_case_id,
        },
    }

    files: dict[str, bytes] = {
        **runtime,
        "public-mcp-contract.json": contract_raw,
        "task.toml": task_toml,
        "task-instruction.md": instruction,
        "task-README.md": readme,
        "examples-manifest.json": examples_manifest,
        **{
            archive_name: raw for archive_name, raw in task_files.items()
            if archive_name.startswith("examples/")
        },
    }
    if set(files) | {"are-public-carps-backend.json"} != set(_BACKEND_FILES):
        raise ValueError("CARPS evaluator file allowlist differs from backend requirements")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("are-public-carps-backend.json", _canonical(manifest))
        for name in sorted(files):
            archive.writestr(name, files[name])
    bundle = buffer.getvalue()
    if len(bundle) > _MAX_BUNDLE_BYTES:
        raise ValueError("CARPS public evaluator bundle exceeds the 25 MiB fixture limit")

    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    dev_input = AREPublicCARPSEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=public_dev_image_id, dev_cell_count=8,
    )
    validation_input = AREPublicCARPSEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=contract["contract_sha256"],
        dev_image_id=public_dev_image_id, dev_cell_count=8,
    )
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(bundle),
        "execution_backend_id": BACKEND_ID,
        "entrypoint": ["python3", "{evaluator}/carps_runner.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": AREPublicCARPSEvaluatorRunner.dev_aggregate_case_id,
                "repetition": 1,
                "input_base64": encode(dev_input),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": AREPublicCARPSEvaluatorRunner.validation_aggregate_case_id,
                "repetition": 1,
                "input_base64": encode(validation_input),
            }]},
        ],
    })
    out = out.expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError("output path already exists; refusing to overwrite a pinned CARPS fixture")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=out.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(fixture.canonical_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(out)
    fixture_bytes = fixture.canonical_bytes()
    return {
        "fixture_path": str(out.resolve()),
        "fixture_sha256": _digest(fixture_bytes),
        "backend_id": BACKEND_ID,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "protocol_metric_id": METRIC_ID,
        "protocol_metric_field": METRIC_FIELD,
        "protocol_direction": METRIC_DIRECTION,
        "dev_metric_source": DEV_METRIC_SOURCE,
        "validation_metric_source": VALIDATION_METRIC_SOURCE,
        "public_panel_sha256": PUBLIC_PANEL_DIGEST,
        "public_mcp_contract_sha256": contract["contract_sha256"],
        "public_dev_image_id": public_dev_image_id,
        "protocol_tiers": ["dev", "validation"],
        "evaluator_bundle_bytes": len(bundle),
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-task", type=Path, required=True,
                        help="Public task directory in the pinned AutoResearchExam checkout")
    parser.add_argument("--mcp-contract", type=Path, required=True,
                        help="Previously observed public-only MCP contract JSON for this task")
    parser.add_argument("--public-dev-image", required=True,
                        help="Clean local runtime image reference; must exclude grader/hidden assets")
    parser.add_argument("--public-dev-image-id", required=True,
                        help="Immutable local Docker image ID in sha256:<64 hex> form")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build_fixture(
        repo=args.repo,
        source_task=args.source_task,
        mcp_contract=args.mcp_contract,
        public_dev_image=args.public_dev_image,
        public_dev_image_id=args.public_dev_image_id,
        out=args.out,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
