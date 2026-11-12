from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from bananloop.evaluator_fixture import EvaluatorFixtureV1
from bananloop.evaluator_verification import EvaluatorProtocolIntentV1, _build_protocol_review_material
from benchmarks.autoresearchexam.build_public_fixture import TASK_ID, TASK_VERSION, build_fixture


def test_are_public_fixture_builder_pins_public_runtime_and_omits_private_sidecar(tmp_path):
    source_root = Path(__file__).parents[1] / "benchmarks/autoresearchexam"
    repo = tmp_path / "repo"
    benchmark = repo / "benchmarks/autoresearchexam"
    benchmark.mkdir(parents=True)
    (benchmark / "public_devkit.py").write_bytes((source_root / "public_devkit.py").read_bytes())
    (benchmark / "optimization_runner.py").write_bytes(
        (source_root / "optimization_runner.py").read_bytes()
    )
    source_task = tmp_path / "task"
    (source_task / "environment/app").mkdir(parents=True)
    instruction = b"Public benchmark instruction\n"
    devkit = b"# public devkit\ndef main(): pass\n"
    (source_task / "instruction.md").write_bytes(instruction)
    (source_task / "environment/app/devkit.py").write_bytes(devkit)
    (source_task / "are-grader-ctx").mkdir()
    (source_task / "are-grader-ctx/private.txt").write_text("private sentinel")
    instruction_digest = hashlib.sha256(instruction).hexdigest()
    contract_digest = "d" * 64
    (benchmark / "task-selection.json").write_text(json.dumps({"tasks": [{
        "task_id": TASK_ID, "version": TASK_VERSION,
        "instruction_sha256": instruction_digest, "task_sha256": "c" * 64,
    }]}))
    (benchmark / "public-mcp-contract-budgeted-imputation-mcar50.json").write_text(
        json.dumps({
            "contract_sha256": contract_digest, "task_id": TASK_ID,
            "task_version": TASK_VERSION, "task_instruction_sha256": instruction_digest,
            "task_sha256": "c" * 64, "public_endpoint": "http://grader:8000/mcp",
            "discovered_tools": [{"name": "submit"}, {"name": "result"}],
        }),
    )
    out = tmp_path / "fixture.json"

    result = build_fixture(repo=repo, source_task=source_task, out=out)

    fixture = EvaluatorFixtureV1.load(out)
    assert result["private_grader_accessed"] is False
    assert fixture.execution_backend_id == "autoresearchexam-public-v2"
    assert tuple(tier.tier for tier in fixture.tiers) == ("dev", "validation")
    bundle = base64.b64decode(fixture.evaluator_bundle_base64)
    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {
            "are-public-backend.json", "public_devkit.py", "task-devkit.py",
            "public-mcp-contract.json", "optimization_runner.py",
        }
        assert archive.read("task-devkit.py") == devkit
        assert b"private sentinel" not in b"".join(archive.read(name) for name in archive.namelist())

    intent = EvaluatorProtocolIntentV1(
        protocol_id="are-public-imputation", version=1,
        metric_id="imputation_r2", metric_field="imputation_r2",
        direction="MAXIMIZE", aggregation="MEAN", repeatability_runs=2,
    )
    draft, artifacts = fixture.bind(intent, source_snapshot_id="a" * 40)
    published = tmp_path / "published"
    published.mkdir()
    allowed = set()
    for content in artifacts.values():
        digest = hashlib.sha256(content).hexdigest()
        (published / digest).write_bytes(content)
        allowed.add(digest)
    attestation = (source_root / "public-evaluator-attestation-v1.json").read_bytes()
    attestation_digest = hashlib.sha256(attestation).hexdigest()
    (published / attestation_digest).write_bytes(attestation)
    allowed.add(attestation_digest)
    proposal = SimpleNamespace(
        protocol=draft.protocol, researcher_intent=intent,
        evidence_refs=draft.evidence_refs, proposal_digest="b" * 64,
        source_job_id="job-1", source_output_digest="c" * 64,
    )
    material = _build_protocol_review_material(
        proposal, published_artifact_root=published,
        allowed_artifact_digests=allowed,
        trusted_public_evidence_digests=(attestation_digest,),
    )
    reviewed = {item["path"]: item["content"] for item in material["evaluator_source_files"]}
    assert set(reviewed) == {
        "are-public-backend.json", "public_devkit.py", "task-devkit.py",
        "public-mcp-contract.json", "optimization_runner.py",
    }
    assert reviewed["public_devkit.py"].encode() == (source_root / "public_devkit.py").read_bytes()
    assert reviewed["optimization_runner.py"].encode() == (
        source_root / "optimization_runner.py"
    ).read_bytes()
    assert reviewed["task-devkit.py"].encode() == devkit
    assert json.loads(reviewed["public-mcp-contract.json"])["contract_sha256"] == contract_digest
    assert material["public_attestations"]
    assert len(json.dumps(material, separators=(",", ":"))) <= 64_000

