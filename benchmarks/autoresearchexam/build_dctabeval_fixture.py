"""Build a public-only DCTAB evaluator fixture for AutoResearchExam v1.1.0.

The fixture contains DEV and public VALIDATION scopes, never HOLDOUT. The
owner-supplied MCP contract must be a previously observed public contract for
this exact task; this builder performs no network calls and does not invent
one. Only public task materials are read. The public dev.csv is hash-pinned
but not copied into the bundle; the runner verifies and stages that exact file
into a networkless evaluator container at execution time.
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

from . import public_dctabeval_dev
from .dctabeval_runner import (
    BACKEND_ID,
    BACKEND_MANIFEST,
    CANDIDATE_PATH,
    DEV_CASE_ID,
    METRIC_AGGREGATION,
    METRIC_DIRECTION,
    METRIC_FIELD,
    PUBLIC_OUTPUT_PATH,
    TASK_ID,
    TASK_VERSION,
    VALIDATION_CASE_ID,
    AREPublicDCTABEvalEvaluatorRunner,
)
from .public_mcp_contract import validate_public_mcp_contract


_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TASK_FILES = {
    "task.toml": "task.toml",
    "task-instruction.md": "instruction.md",
    "local_panel.py": "environment/app/local_panel.py",
    "task-Dockerfile": "environment/Dockerfile",
    "task-requirements.txt": "environment/requirements.txt",
}
_RUNTIME_FILES = {
    "dctabeval_runner.py": "dctabeval_runner.py",
    "public_dctabeval_dev.py": "public_dctabeval_dev.py",
    "public_devkit.py": "public_devkit.py",
    "optimization_runner.py": "optimization_runner.py",
    "public_grader.py": "public_grader.py",
    "are_public_mcp_client.py": "are_public_mcp_client.py",
    "public_mcp_contract.py": "public_mcp_contract.py",
    "task-selection.json": "task-selection.json",
}


def _read_public_file(path: Path, *, root: Path, max_bytes: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("DCTAB fixture input escaped its declared public root") from None
    if root.is_symlink():
        raise ValueError("DCTAB fixture input root must not be a symlink")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("DCTAB fixture input contains a symlink")
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("DCTAB fixture input is missing or oversized")
    return path.read_bytes()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def build_fixture(
    *, repo: Path,
    source_task: Path,
    mcp_contract: Path,
    out: Path,
    dev_image: str,
    dev_image_id: str,
) -> dict[str, Any]:
    repo, source_task, mcp_contract = (
        item.expanduser() for item in (repo, source_task, mcp_contract)
    )
    if any(item.is_symlink() for item in (repo, source_task, mcp_contract)):
        raise ValueError("DCTAB fixture roots and contract must not be symlinks")
    repo, source_task, mcp_contract = (
        item.resolve() for item in (repo, source_task, mcp_contract)
    )
    if not repo.is_dir() or not source_task.is_dir() or not mcp_contract.is_file():
        raise ValueError("DCTAB fixture requires the repo, public task, and observed MCP contract")
    if source_task.name != TASK_ID:
        raise ValueError("public source task path does not match selected DCTAB task")
    if (not isinstance(dev_image, str) or not dev_image or dev_image.startswith("-")
            or any(char.isspace() for char in dev_image)
            or not isinstance(dev_image_id, str) or not _IMAGE_ID_RE.fullmatch(dev_image_id)):
        raise ValueError("DCTAB public DEV image must have a valid immutable image ID pin")

    pins = public_dctabeval_dev.public_dctabeval_pins(source_task)
    contract_raw = _read_public_file(
        mcp_contract, root=mcp_contract.parent, max_bytes=128_000,
    )
    try:
        contract = validate_public_mcp_contract(
            json.loads(contract_raw),
            task_id=TASK_ID,
            task_version=TASK_VERSION,
            task_sha256=pins["task_sha256"],
            task_instruction_sha256=pins["instruction_sha256"],
        )
    except (ValueError, json.JSONDecodeError):
        raise ValueError("observed public MCP contract does not match selected DCTAB task") from None
    if contract.get("public_endpoint") != "http://grader:8000/mcp":
        raise ValueError("DCTAB public MCP endpoint is not pinned")

    task_material = {
        archive_name: _read_public_file(
            source_task / relative, root=source_task,
            max_bytes=64_000 if archive_name in {"task.toml", "task-instruction.md"} else 32_000,
        )
        for archive_name, relative in _TASK_FILES.items()
    }
    if (hashlib.sha256(task_material["task.toml"]).hexdigest() != pins["task_sha256"]
            or hashlib.sha256(task_material["task-instruction.md"]).hexdigest()
            != pins["instruction_sha256"]
            or hashlib.sha256(task_material["local_panel.py"]).hexdigest()
            != pins["local_panel_sha256"]):
        raise ValueError("DCTAB public task material changed after input pin verification")

    runtime: dict[str, bytes] = {}
    for archive_name, relative in _RUNTIME_FILES.items():
        path = repo / "benchmarks/autoresearchexam" / relative
        runtime[archive_name] = _read_public_file(
            path, root=repo, max_bytes=256_000 if archive_name == "task-selection.json" else 128_000,
        )
    if hashlib.sha256(runtime["task-selection.json"]).hexdigest() != pins["selection_sha256"]:
        raise ValueError("frozen AutoResearchExam task selection changed")
    if runtime["public_dctabeval_dev.py"] != Path(
            public_dctabeval_dev.__file__).read_bytes():
        raise ValueError("DCTAB public DEV module changed while building fixture")
    if runtime["dctabeval_runner.py"] != Path(__file__).with_name(
            "dctabeval_runner.py").read_bytes():
        # The import-path read is retained as a second guard against packaging
        # an older on-disk runner than the currently loaded class.
        raise ValueError("DCTAB backend runner changed while building fixture")

    task_material_sha = {
        "task_sha256": hashlib.sha256(task_material["task.toml"]).hexdigest(),
        "instruction_sha256": hashlib.sha256(task_material["task-instruction.md"]).hexdigest(),
        "local_panel_sha256": hashlib.sha256(task_material["local_panel.py"]).hexdigest(),
        "dockerfile_sha256": hashlib.sha256(task_material["task-Dockerfile"]).hexdigest(),
        "requirements_sha256": hashlib.sha256(task_material["task-requirements.txt"]).hexdigest(),
    }
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
        "task_selection_sha256": pins["selection_sha256"],
        "task_sha256": pins["task_sha256"],
        "instruction_sha256": pins["instruction_sha256"],
        "local_panel_sha256": pins["local_panel_sha256"],
        "dev_csv_sha256": pins["dev_csv_sha256"],
        "task_material_sha256": task_material_sha,
        "mcp_contract_sha256": contract["contract_sha256"],
        "mcp_contract_source_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "mcp_endpoint": "http://grader:8000/mcp",
        "public_dev_image": dev_image,
        "public_dev_image_id": dev_image_id,
        "public_dev_panel": {
            "draws": list(public_dctabeval_dev.PANEL_DRAWS),
            "panel_count": public_dctabeval_dev.PANEL_COUNT,
            "eval_rows_per_draw": public_dctabeval_dev.EVAL_ROWS_PER_DRAW,
        },
        "dev_execution": {
            "container_network": "none",
            "root_filesystem_read_only": True,
            "mounted_candidate_path": PUBLIC_OUTPUT_PATH,
            "mounted_public_inputs": ["local_panel.py", "data/dev.csv"],
            "per_draw_timeout_seconds": public_dctabeval_dev.PER_DRAW_TIMEOUT_SECONDS,
        },
        "aggregate_case_ids": {"dev": DEV_CASE_ID, "validation": VALIDATION_CASE_ID},
    }
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BACKEND_MANIFEST, _canonical_json(manifest))
        archive.writestr("public-mcp-contract.json", contract_raw)
        for name, data in {**runtime, **task_material}.items():
            archive.writestr(name, data)

    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(archive_bytes.getvalue()),
        "execution_backend_id": BACKEND_ID,
        "entrypoint": ["python3", "{evaluator}/dctabeval_runner.py"],
        "tiers": [
            {"tier": "dev", "cases": [{
                "case_id": DEV_CASE_ID,
                "repetition": 1,
                "input_base64": encode(AREPublicDCTABEvalEvaluatorRunner.aggregate_case_input(
                    tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
                    contract_sha256=contract["contract_sha256"],
                    dev_image_id=dev_image_id,
                    dev_cell_count=public_dctabeval_dev.PANEL_COUNT,
                )),
            }]},
            {"tier": "validation", "cases": [{
                "case_id": VALIDATION_CASE_ID,
                "repetition": 1,
                "input_base64": encode(AREPublicDCTABEvalEvaluatorRunner.aggregate_case_input(
                    tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
                    contract_sha256=contract["contract_sha256"],
                    dev_image_id=dev_image_id,
                    dev_cell_count=public_dctabeval_dev.PANEL_COUNT,
                )),
            }]},
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
        "evaluator_bundle_sha256": hashlib.sha256(archive_bytes.getvalue()).hexdigest(),
        "backend_id": BACKEND_ID,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "candidate_path": CANDIDATE_PATH,
        "public_output_path": PUBLIC_OUTPUT_PATH,
        "metric_field": METRIC_FIELD,
        "metric_direction": METRIC_DIRECTION,
        "metric_aggregation": METRIC_AGGREGATION,
        "task_selection_sha256": pins["selection_sha256"],
        "task_sha256": pins["task_sha256"],
        "instruction_sha256": pins["instruction_sha256"],
        "local_panel_sha256": pins["local_panel_sha256"],
        "dev_csv_sha256": pins["dev_csv_sha256"],
        "mcp_contract_sha256": contract["contract_sha256"],
        "public_dev_panel_count": public_dctabeval_dev.PANEL_COUNT,
        "protocol_tiers": ["dev", "validation"],
        "private_grader_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-task", type=Path, required=True,
                        help="public dctabeval task source directory")
    parser.add_argument("--mcp-contract", type=Path, required=True,
                        help="observed public MCP contract pinned to this task")
    parser.add_argument("--dev-image", required=True,
                        help="prebuilt public task image name")
    parser.add_argument("--dev-image-id", required=True,
                        help="immutable sha256 Docker image ID")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_fixture(
        repo=args.repo,
        source_task=args.source_task,
        mcp_contract=args.mcp_contract,
        out=args.out,
        dev_image=args.dev_image,
        dev_image_id=args.dev_image_id,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
