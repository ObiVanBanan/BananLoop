from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

from bananloop.evaluator_fixture import EvaluatorFixtureV1
from benchmarks.autoresearchexam.build_sopcc_fixture import TASK_ID, TASK_VERSION, build_fixture
from benchmarks.autoresearchexam.sopcc_runner import AREPublicSOPCCEvaluatorRunner
from benchmarks.autoresearchexam.public_mcp_contract import make_public_mcp_contract


ROOT = Path(__file__).parents[1]


def test_sopcc_fixture_binds_public_task_and_backend_without_private_assets(tmp_path):
    selection = json.loads((ROOT / "benchmarks/autoresearchexam/task-selection.json").read_text())
    task = next(item for item in selection["tasks"] if item["task_id"] == TASK_ID)
    contract = make_public_mcp_contract(
        task_id=TASK_ID, task_version=TASK_VERSION,
        task_sha256=task["task_sha256"],
        task_instruction_sha256=task["instruction_sha256"],
        endpoint="http://grader:8000/mcp", protocol_version="2024-11-05",
        server_info={"name": "are", "version": "2.14.7"},
        discovered_tools=[
            {"name": "submit", "description": "submit", "inputSchema": {}, "outputSchema": {}},
            {"name": "result", "description": "result", "inputSchema": {}, "outputSchema": {}},
        ],
    )
    contract_path = tmp_path / "public-mcp-contract.json"
    contract_path.write_text(json.dumps(contract))
    source_task = tmp_path / "public-source-task"
    (source_task / "environment/app/harness").mkdir(parents=True)
    instruction = ROOT / "benchmarks/autoresearchexam/public-task-material/sopcc-instruction-v1.1.0.md"
    (source_task / "instruction.md").write_bytes(instruction.read_bytes())
    from benchmarks.autoresearchexam.build_sopcc_fixture import _TASK_FILES
    for index, relative in enumerate(_TASK_FILES.values()):
        target = source_task / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"public fixture source {index}\n")
    out = tmp_path / "fixture.json"
    result = build_fixture(
        repo=ROOT, source_task=source_task,
        mcp_contract=contract_path, out=out,
    )
    fixture = EvaluatorFixtureV1.load(out)
    assert result["private_grader_accessed"] is False
    assert result["backend_id"] == AREPublicSOPCCEvaluatorRunner.backend_id
    assert [tier.tier for tier in fixture.tiers] == ["dev", "validation"]
    bundle = base64.b64decode(fixture.evaluator_bundle_base64)
    runner = AREPublicSOPCCEvaluatorRunner(
        grader=SimpleNamespace(deadline_epoch=100_000, contract_sha256=contract["contract_sha256"]),
        publish_candidate=lambda _: None, task_id=TASK_ID,
        task_version=TASK_VERSION, contract_sha256=contract["contract_sha256"],
        candidate_path="benchmarks/autoresearchexam/solutions/sopcc/policy.py",
        evaluate_dev=lambda *_: {"status": "GRADED", "dev_score": 0.0, "dev_cells": 4},
    )
    protocol = SimpleNamespace(evaluator_bundle_sha256=result["evaluator_bundle_sha256"])
    runner._verify_backend_bundle(protocol, bundle)

