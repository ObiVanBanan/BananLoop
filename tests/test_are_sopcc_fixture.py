from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from bananloop.evaluator_fixture import EvaluatorFixtureV1
from bananloop.evaluator_verification import (
    EvaluatorProtocolIntentV1, _build_protocol_review_material,
)
from bananloop.roles import RoleContextV1
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
        target.write_text(
            ("# public evaluator source padding\n" * 1000)
            if relative.endswith("/runner.py")
            else f"public fixture source {index}\n"
        )
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

    # The real SOPCC adapter's complete public source is larger than the old
    # 88k independent-verifier limit. Prove the full bounded source still fits.
    intent = EvaluatorProtocolIntentV1(
        protocol_id="sopcc-public-dev-score", version=1,
        metric_id="sopcc_score", metric_field="sopcc_score",
        direction="MAXIMIZE", aggregation="MEAN", repeatability_runs=2,
        timeout_seconds=120,
    )
    draft, artifacts = fixture.bind(intent, source_snapshot_id="a" * 40)
    published = tmp_path / "published"
    published.mkdir()
    allowed = set()
    for content in artifacts.values():
        digest = hashlib.sha256(content).hexdigest()
        (published / digest).write_bytes(content)
        allowed.add(digest)
    proposal = SimpleNamespace(
        protocol=draft.protocol, researcher_intent=intent,
        evidence_refs=draft.evidence_refs, proposal_digest="b" * 64,
        source_job_id="job-sopcc", source_output_digest="c" * 64,
    )
    review = _build_protocol_review_material(
        proposal, published_artifact_root=published,
        allowed_artifact_digests=allowed,
    )
    reviewed_sources = review["evaluator_source_files"]
    assert any(item["path"] == "task-runner.py" for item in reviewed_sources)
    sopcc_source = next(item["content"] for item in reviewed_sources
                        if item["path"] == "sopcc_runner.py")
    assert "def normalize_sopcc_dev_result" in sopcc_source
    assert '"dev_score": float(score), "dev_cells": episodes' in sopcc_source
    assert sum(len(item["content"]) for item in reviewed_sources) > 88_000
    context = json.dumps(review, separators=(",", ":"))
    assert len(context) <= 180_000
    RoleContextV1(
        role_id="independent_verifier", campaign_goal="Review public evaluator",
        public_task=context,
    )


def test_sopcc_public_validation_attestation_pins_task_and_official_sources():
    attestation_path = ROOT / "benchmarks/autoresearchexam/public-evaluator-attestation-sopcc-v1.json"
    attestation = json.loads(attestation_path.read_text())
    assert attestation["scope"]["task_id"] == TASK_ID
    assert attestation["scope"]["task_version"] == TASK_VERSION
    assert attestation["scope"]["not_claimed"] == (
        "cryptographic_proof_of_server_implementation_or_split_enforcement"
    )
    assert len(attestation_path.read_bytes()) <= 4096
    assert attestation["sources"][0]["commit"] == "23c0d92e11af1dea821f972b5f0a739060958002"

