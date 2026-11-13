from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.autoresearchexam.sopcc_runner as sopcc_runner_module
from benchmarks.autoresearchexam.sopcc_runner import (
    AREPublicSOPCCEvaluatorRunner, normalize_sopcc_dev_result,
)
from benchmarks.autoresearchexam.public_mcp_contract import make_public_mcp_contract
from bananloop.contracts import ProtocolCase, ProtocolManifest
from bananloop.evaluator_verification import EvaluatorProtocolV1, EvaluatorTierManifestV1
from bananloop.trusted_evaluator import TrustedEvaluatorError, _case_protocol_digest


ROOT = Path(__file__).parents[1]
TASK_ID = "sopcc-online-chance-constrained-policy"
TASK_VERSION = "1.1.0"
CONTRACT = make_public_mcp_contract(
    task_id=TASK_ID, task_version=TASK_VERSION, task_sha256="a" * 64,
    task_instruction_sha256="b" * 64, endpoint="http://grader:8000/mcp",
    protocol_version="2024-11-05", server_info={"name": "are", "version": "2.14.7"},
    discovered_tools=[
        {"name": "submit", "description": "submit", "inputSchema": {}, "outputSchema": {}},
        {"name": "result", "description": "result", "inputSchema": {}, "outputSchema": {}},
    ],
)
CONTRACT_SHA = CONTRACT["contract_sha256"]


def _bundle():
    source_names = {
        "public_sopcc_dev.py": ROOT / "benchmarks/autoresearchexam/public_sopcc_dev.py",
        "optimization_runner.py": ROOT / "benchmarks/autoresearchexam/optimization_runner.py",
        "sopcc_runner.py": ROOT / "benchmarks/autoresearchexam/sopcc_runner.py",
        "public_mcp_contract.py": ROOT / "benchmarks/autoresearchexam/public_mcp_contract.py",
        "public_grader.py": ROOT / "benchmarks/autoresearchexam/public_grader.py",
        "are_public_mcp_client.py": ROOT / "benchmarks/autoresearchexam/are_public_mcp_client.py",
    }
    sources = {name: path.read_bytes() for name, path in source_names.items()}
    contract = json.dumps(CONTRACT, sort_keys=True).encode()
    files = {
        "task-Dockerfile": b"FROM pinned public task image\n",
        "task-requirements.txt": b"numpy==2.5.2\n",
        "task-dev_eval.py": b"public dev entrypoint\n",
        "task-runner.py": b"public sandbox runner\n",
        "task-sopcc.py": b"public task dynamics\n",
        "task-policy_worker.py": b"public policy worker\n",
    }
    instruction = b"public instruction\n"
    task_file_sha = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    cls = AREPublicSOPCCEvaluatorRunner
    manifest = {
        "schema_version": 1, "backend_id": cls.backend_id,
        "task_id": TASK_ID, "task_version": TASK_VERSION,
        "mcp_contract_sha256": CONTRACT_SHA,
        "mcp_endpoint": "http://grader:8000/mcp",
        "candidate_path": "benchmarks/autoresearchexam/solutions/sopcc/policy.py",
        "public_output_path": "/app/solution/policy.py",
        "public_runner_sha256": hashlib.sha256(sources["public_sopcc_dev.py"]).hexdigest(),
        "optimization_runner_sha256": hashlib.sha256(sources["optimization_runner.py"]).hexdigest(),
        "sopcc_runner_sha256": hashlib.sha256(sources["sopcc_runner.py"]).hexdigest(),
        "public_mcp_contract_sha256": hashlib.sha256(sources["public_mcp_contract.py"]).hexdigest(),
        "public_grader_sha256": hashlib.sha256(sources["public_grader.py"]).hexdigest(),
        "mcp_client_sha256": hashlib.sha256(sources["are_public_mcp_client.py"]).hexdigest(),
        "task_instruction_sha256": hashlib.sha256(instruction).hexdigest(),
        "task_file_sha256": task_file_sha,
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
    files_to_zip = {
        "are-public-sopcc-backend.json": json.dumps(manifest, sort_keys=True).encode(),
        **sources,
        "public-mcp-contract.json": contract,
        "task-instruction.md": instruction,
        **files,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files_to_zip.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def test_sopcc_dev_result_mapping_matches_shared_runner_contract():
    assert normalize_sopcc_dev_result({
        "status": "GRADED", "metric": 3.25, "episodes": 4,
    }) == {"status": "GRADED", "dev_score": 3.25, "dev_cells": 4}
    for invalid in (
        {"status": "ERROR", "metric": 3.25, "episodes": 4},
        {"status": "GRADED", "metric": float("nan"), "episodes": 4},
        {"status": "GRADED", "metric": 3.25, "episodes": 3},
    ):
        with pytest.raises(ValueError):
            normalize_sopcc_dev_result(invalid)


def test_sopcc_runner_default_dev_path_runs_and_normalizes_public_result(monkeypatch):
    calls = []

    def fake_public_runner(candidate, **kwargs):
        calls.append((candidate, kwargs))
        return {"status": "GRADED", "metric": 3.5, "episodes": 4}

    monkeypatch.setattr(sopcc_runner_module, "run_public_sopcc_dev", fake_public_runner)
    runner = AREPublicSOPCCEvaluatorRunner(
        grader=SimpleNamespace(deadline_epoch=100_000, contract_sha256=CONTRACT_SHA),
        publish_candidate=lambda _: None,
        task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=CONTRACT_SHA,
        candidate_path="benchmarks/autoresearchexam/solutions/sopcc/policy.py",
    )
    assert runner.evaluate_dev(b"candidate", 120) == {
        "status": "GRADED", "dev_score": 3.5, "dev_cells": 4,
    }
    assert calls == [(b"candidate", {
        "image": runner.default_dev_image,
        "expected_image_id": runner.default_dev_image_id,
        "timeout_seconds": 120,
        "draws": (0,), "instances_per_draw": 1,
        "episodes_per_instance": 4, "workers": 1,
    })]


def _runner():
    runner = AREPublicSOPCCEvaluatorRunner.__new__(AREPublicSOPCCEvaluatorRunner)
    runner.task_id = TASK_ID
    runner.task_version = TASK_VERSION
    runner.contract_sha256 = CONTRACT_SHA
    runner.candidate_path = "benchmarks/autoresearchexam/solutions/sopcc/policy.py"
    runner.dev_image = runner.default_dev_image
    runner.dev_image_id = runner.default_dev_image_id
    runner.dev_cell_count = runner.default_dev_cell_count
    runner.grader = SimpleNamespace(contract_sha256=CONTRACT_SHA)
    return runner


def test_sopcc_backend_bundle_is_bound_to_public_sources_and_image():
    runner = _runner()
    bundle = _bundle()
    protocol = SimpleNamespace(
        evaluator_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        metric_id="sopcc_score", metric_field="sopcc_score",
        direction="MAXIMIZE", aggregation="MEAN",
    )
    runner._verify_backend_bundle(protocol, bundle)

    mismatched_protocol = SimpleNamespace(**{
        **vars(protocol), "metric_field": "score",
    })
    with pytest.raises(TrustedEvaluatorError, match="metric"):
        runner._verify_backend_bundle(mismatched_protocol, bundle)


def test_sopcc_backend_refuses_mutated_bundle():
    runner = _runner()
    original = _bundle()
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["task-Dockerfile"] += b"changed"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    bundle = stream.getvalue()
    protocol = SimpleNamespace(
        evaluator_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        metric_id="sopcc_score", metric_field="sopcc_score",
        direction="MAXIMIZE", aggregation="MEAN",
    )
    with pytest.raises(TrustedEvaluatorError, match="bundle"):
        runner._verify_backend_bundle(protocol, bundle)


def test_sopcc_aggregate_case_binds_exact_task_panel_and_metric():
    runner = _runner()
    expected = runner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256=CONTRACT_SHA, dev_image_id=runner.dev_image_id,
        dev_cell_count=runner.dev_cell_count,
    )
    decoded = json.loads(expected)
    assert decoded["metric_field"] == "sopcc_score"
    assert decoded["draws"] == [0]
    assert decoded["episode_count"] == 4
    case = SimpleNamespace(
        case_id=runner.dev_aggregate_case_id, repetition=1,
        input_sha256=hashlib.sha256(expected).hexdigest(),
    )
    runner._validate_aggregate_case(
        (case,), {(case.case_id, case.repetition): expected}, tier="dev",
    )
    with pytest.raises(TrustedEvaluatorError, match="scope differs"):
        runner._validate_aggregate_case(
            (case,), {(case.case_id, case.repetition): expected + b" "}, tier="dev",
        )


def test_sopcc_dev_runs_through_trusted_evaluator_runner_contract(tmp_path):
    bundle = _bundle()
    cls = AREPublicSOPCCEvaluatorRunner
    provisional = EvaluatorProtocolV1(
        protocol_id="are-sopcc-public", version=1, source_snapshot_id="1" * 40,
        evaluator_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        execution_backend_id=cls.backend_id, entrypoint=("python3", "{evaluator}/public_sopcc_dev.py"),
        metric_id="sopcc_score", metric_field="sopcc_score", direction="MAXIMIZE",
        aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="0" * 64, case_count=1),
            EvaluatorTierManifestV1(tier="validation", manifest_sha256="0" * 64, case_count=1),
        ),
        repeatability_runs=2, timeout_seconds=120,
    )
    protocol_sha = _case_protocol_digest(provisional)
    inputs = {}
    tiers = []
    manifests = {}
    for tier_name, case_id in (("dev", cls.dev_aggregate_case_id),
                               ("validation", cls.validation_aggregate_case_id)):
        payload = cls.aggregate_case_input(
            tier=tier_name, task_id=TASK_ID, task_version=TASK_VERSION,
            contract_sha256=CONTRACT_SHA, dev_image_id=cls.default_dev_image_id,
            dev_cell_count=cls.default_dev_cell_count,
        )
        case = ProtocolCase(
            case_id=case_id, repetition=1,
            input_sha256=hashlib.sha256(payload).hexdigest(),
        )
        manifest = ProtocolManifest(schema_version=1, protocol_sha256=protocol_sha, cases=(case,))
        encoded = json.dumps(manifest.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":")).encode()
        manifests[tier_name] = encoded
        tiers.append(EvaluatorTierManifestV1(
            tier=tier_name, manifest_sha256=hashlib.sha256(encoded).hexdigest(), case_count=1,
        ))
        inputs[(case_id, 1)] = payload
    protocol = provisional.model_copy(update={"tiers": tuple(tiers)})
    grader = SimpleNamespace(
        deadline_epoch=10_000.0, contract_sha256=CONTRACT_SHA,
    )
    runner = AREPublicSOPCCEvaluatorRunner(
        grader=grader, publish_candidate=lambda _: pytest.fail("DEV must not publish to grader"),
        task_id=TASK_ID, task_version=TASK_VERSION, contract_sha256=CONTRACT_SHA,
        candidate_path="benchmarks/autoresearchexam/solutions/sopcc/policy.py",
        evaluate_dev=lambda _candidate, _timeout: {
            "status": "GRADED", "dev_score": 3.3136, "dev_cells": 4,
        },
        clock=lambda: 100.0,
    )
    runner._verify_backend_bundle(protocol, bundle)
    execution = runner.run(
        protocol=protocol, approval_digest="2" * 64, campaign_id="c-sopcc",
        spec_hash="3" * 64, candidate_snapshot_id="4" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/sopcc/policy.py": b"candidate"},
        evaluator_bundle=bundle, tier="dev", tier_manifest=manifests["dev"],
        case_inputs={(cls.dev_aggregate_case_id, 1): inputs[(cls.dev_aggregate_case_id, 1)]},
    )
    result = json.loads(execution.private_result_bytes)
    assert execution.tier == "dev"
    assert result["metrics"]["sopcc_score"] == {"value": 3.3136, "unit": "reward", "n": 1}
    assert result["diagnostics"] == {"split": "public_dev"}

