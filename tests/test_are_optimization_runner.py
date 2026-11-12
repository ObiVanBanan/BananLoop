from __future__ import annotations

import json
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from bananloop.contracts import ProtocolCase, ProtocolManifest
from bananloop.evaluator_verification import EvaluatorProtocolV1, EvaluatorTierManifestV1
from bananloop.trusted_evaluator import TrustedEvaluatorError
from bananloop.trusted_evaluator import _case_protocol_digest
from bananloop.v2_optimization_cli import V2OptimizationCLIError, _task_request_timeout
from benchmarks.autoresearchexam.optimization_runner import AREPublicEvaluatorRunner
from benchmarks.autoresearchexam.public_grader import PublicAREGrader, PublicGraderError


def _bundle(task_id="are-task", version="1.1.0", contract_sha="f" * 64,
            candidate_path="benchmarks/autoresearchexam/solutions/impute.py"):
    runner_source = (Path(__file__).parents[1]
                     / "benchmarks/autoresearchexam/public_devkit.py").read_bytes()
    backend_source = (Path(__file__).parents[1]
                      / "benchmarks/autoresearchexam/optimization_runner.py").read_bytes()
    grader_source = (Path(__file__).parents[1]
                     / "benchmarks/autoresearchexam/public_grader.py").read_bytes()
    mcp_client_source = (Path(__file__).parents[1]
                         / "benchmarks/autoresearchexam/are_public_mcp_client.py").read_bytes()
    task_source = b"public task devkit source"
    contract_source = json.dumps({
        "contract_sha256": contract_sha, "task_id": task_id,
        "task_version": version, "public_endpoint": "http://grader:8000/mcp",
        "discovered_tools": [{"name": "submit"}, {"name": "result"}],
    }, sort_keys=True).encode()
    value = {
        "schema_version": 1,
        "backend_id": AREPublicEvaluatorRunner.backend_id,
        "task_id": task_id,
        "task_version": version,
        "mcp_contract_sha256": contract_sha,
        "mcp_endpoint": "http://grader:8000/mcp",
        "candidate_path": candidate_path,
        "public_output_path": "/app/output/impute.py",
        "public_runner_sha256": hashlib.sha256(runner_source).hexdigest(),
        "optimization_runner_sha256": hashlib.sha256(backend_source).hexdigest(),
        "public_grader_sha256": hashlib.sha256(grader_source).hexdigest(),
        "mcp_client_sha256": hashlib.sha256(mcp_client_source).hexdigest(),
        "task_instruction_sha256": hashlib.sha256(b"Public task instruction\n").hexdigest(),
        "cellspec_sha256": hashlib.sha256(b"public cellspec source").hexdigest(),
        "cellrunner_sha256": hashlib.sha256(b"public cellrunner source").hexdigest(),
        "task_devkit_sha256": hashlib.sha256(task_source).hexdigest(),
        "mcp_contract_source_sha256": hashlib.sha256(contract_source).hexdigest(),
        "public_dev_image": AREPublicEvaluatorRunner.default_dev_image,
        "public_dev_image_id": AREPublicEvaluatorRunner.default_dev_image_id,
        "public_dev_cell_count": AREPublicEvaluatorRunner.default_dev_cell_count,
        "dev_execution": {
            "container_network": "none",
            "root_filesystem_read_only": True,
            "mounted_candidate_path": "/app/output/impute.py",
        },
        "aggregate_case_ids": {
            "dev": AREPublicEvaluatorRunner.dev_aggregate_case_id,
            "validation": AREPublicEvaluatorRunner.validation_aggregate_case_id,
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("are-public-backend.json", json.dumps(value, sort_keys=True))
        archive.writestr("public_devkit.py", runner_source)
        archive.writestr("optimization_runner.py", backend_source)
        archive.writestr("public_grader.py", grader_source)
        archive.writestr("are_public_mcp_client.py", mcp_client_source)
        archive.writestr("task-instruction.md", b"Public task instruction\n")
        archive.writestr("cellspec.py", b"public cellspec source")
        archive.writestr("cellrunner.py", b"public cellrunner source")
        archive.writestr("task-devkit.py", task_source)
        archive.writestr("public-mcp-contract.json", contract_source)
    return buffer.getvalue()


def _protocol_and_manifest(bundle=None):
    bundle = bundle or _bundle()
    protocol = EvaluatorProtocolV1(
        protocol_id="are-public-imputation",
        version=1,
        source_snapshot_id="1" * 40,
        evaluator_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        execution_backend_id=AREPublicEvaluatorRunner.backend_id,
        entrypoint=("{evaluator}/dev-evaluate.py",),
        metric_id="imputation_r2",
        metric_field="imputation_r2",
        direction="MAXIMIZE",
        aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="b" * 64, case_count=1),
            EvaluatorTierManifestV1(tier="validation", manifest_sha256="c" * 64, case_count=1),
        ),
        repeatability_runs=2,
        timeout_seconds=300,
    )
    validation_input = AREPublicEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )
    manifest = ProtocolManifest(
        schema_version=1, protocol_sha256=_case_protocol_digest(protocol),
        cases=(ProtocolCase(
            case_id=AREPublicEvaluatorRunner.validation_aggregate_case_id, repetition=1,
            input_sha256=hashlib.sha256(validation_input).hexdigest(),
        ),),
    )
    encoded = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    protocol = protocol.model_copy(update={
        "tiers": tuple(
            item.model_copy(update={"manifest_sha256": hashlib.sha256(encoded).hexdigest()})
            if item.tier == "validation" else item
            for item in protocol.tiers
        ),
    })
    return protocol, encoded


def _protocol():
    return _protocol_and_manifest()[0]


def _protocol_for_path(candidate_path):
    bundle = _bundle(candidate_path=candidate_path)
    protocol, manifest = _protocol_and_manifest(bundle)
    return protocol, manifest, bundle


def _manifest():
    return _protocol_and_manifest()[1]


def _validation_inputs():
    return {(
        AREPublicEvaluatorRunner.validation_aggregate_case_id, 1,
    ): AREPublicEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )}


def test_optimizer_model_timeout_is_bounded_by_outer_task_deadline():
    assert _task_request_timeout(110.0, 300, now=100.0) == 10
    assert _task_request_timeout(1000.0, 300, now=100.0) == 300
    with pytest.raises(V2OptimizationCLIError, match="deadline expired before model dispatch"):
        _task_request_timeout(100.9, 300, now=100.0)


def test_public_score_rejects_validation_result_marked_with_grader_error():
    with pytest.raises(TrustedEvaluatorError, match="approved finite score"):
        AREPublicEvaluatorRunner._public_score({
            "status": "GRADED", "validation_score": 0.0, "grader_error": True,
        })


def test_validation_uploads_only_candidate_and_returns_sanitized_public_score(tmp_path):
    calls = []
    next_id = 0

    def tool(name, arguments):
        nonlocal next_id
        calls.append((name, arguments))
        if name == "submit":
            next_id += 1
            return {"id": next_id, "status": "queued"}
        return {"id": arguments["id"], "status": "graded", "validation_score": 0.23,
                "grading_seconds": 5.2, "grader_output_tail": "secret tail"}

    grader = PublicAREGrader(
        tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
        call_tool=tool,
    )
    uploaded = []
    runner = AREPublicEvaluatorRunner(
        grader=grader, publish_candidate=uploaded.append,
        task_id="are-task", task_version="1.1.0", contract_sha256="f" * 64,
        candidate_path="benchmarks/autoresearchexam/solutions/impute.py",
        poll_interval_seconds=0.05, sleep=lambda _: None,
    )
    kwargs = dict(
        protocol=_protocol(), approval_digest="2" * 64, campaign_id="c-1",
        spec_hash="3" * 64,
        evaluator_bundle=_bundle(), tier="validation", tier_manifest=_manifest(),
        case_inputs=_validation_inputs(),
    )
    baseline = runner.run(
        **kwargs, candidate_snapshot_id="1" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"baseline"},
    )
    runner.run(
        **kwargs, candidate_snapshot_id="1" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"baseline"},
    )
    result = runner.run(
        **kwargs, candidate_snapshot_id="4" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"def impute(): pass\n"},
    )
    runner.run(
        **kwargs, candidate_snapshot_id="4" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"def impute(): pass\n"},
    )
    baseline_cached = runner.run(
        **kwargs, candidate_snapshot_id="1" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"baseline"},
    )
    assert baseline.tier == "validation"
    assert baseline_cached.result_digest == baseline.result_digest
    assert uploaded == [b"baseline", b"baseline",
                        b"def impute(): pass\n", b"def impute(): pass\n"]
    assert [name for name, _ in calls] == [
        "submit", "result", "submit", "result",
        "submit", "result", "submit", "result",
    ]
    assert result.tier == "validation"
    assert result.repeatability_runs == 2
    assert result.case_count == 1
    assert b"secret tail" not in result.private_result_bytes
    payload = json.loads(result.private_result_bytes)
    assert payload["metrics"]["imputation_r2"]["value"] == 0.23
    assert payload["cases"][0]["metrics"] == {}
    assert payload["diagnostics"]["split"] == "public_validation"


def test_public_runner_refuses_holdout_and_unpinned_backend(tmp_path):
    protocol, manifest, bundle = _protocol_for_path("solution.py")
    runner = AREPublicEvaluatorRunner(
        grader=PublicAREGrader(
            tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
            call_tool=lambda *_: {},
        ),
        publish_candidate=lambda _: None,
        task_id="are-task", task_version="1.1.0", contract_sha256="f" * 64,
        candidate_path="solution.py",
    )
    kwargs = {
        "protocol": protocol, "approval_digest": "2" * 64,
        "campaign_id": "c-1", "spec_hash": "3" * 64,
        "candidate_snapshot_id": "4" * 40,
        "candidate_files": {"solution.py": b"candidate"},
        "evaluator_bundle": bundle,
        "tier_manifest": manifest, "case_inputs": {},
    }
    with pytest.raises(TrustedEvaluatorError, match="no HOLDOUT"):
        runner.run(**kwargs, tier="holdout")
    wrong_backend = protocol.model_copy(update={"execution_backend_id": "trusted-local"})
    with pytest.raises(TrustedEvaluatorError, match="not pinned"):
        runner.run(**{**kwargs, "protocol": wrong_backend}, tier="validation")


def test_dev_uses_networkless_public_callback_and_returns_only_panel_aggregate(tmp_path):
    protocol, _ = _protocol_and_manifest()
    bundle = _bundle()
    dev_input = AREPublicEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )
    dev_manifest_model = ProtocolManifest(
        schema_version=1,
        protocol_sha256=_case_protocol_digest(protocol),
        cases=(ProtocolCase(
            case_id=AREPublicEvaluatorRunner.dev_aggregate_case_id, repetition=1,
            input_sha256=hashlib.sha256(dev_input).hexdigest(),
        ),),
    )
    dev_manifest = json.dumps(
        dev_manifest_model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    protocol = protocol.model_copy(update={
        "tiers": tuple(
            item.model_copy(update={"manifest_sha256": hashlib.sha256(dev_manifest).hexdigest()})
            if item.tier == "dev" else item for item in protocol.tiers
        ),
    })
    calls = []
    runner = AREPublicEvaluatorRunner(
        grader=PublicAREGrader(
            tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
            call_tool=lambda *_: pytest.fail("DEV must not contact the grader"),
        ),
        publish_candidate=lambda _: pytest.fail("DEV must not upload to the Harbor grader"),
        task_id="are-task", task_version="1.1.0", contract_sha256="f" * 64,
        candidate_path="benchmarks/autoresearchexam/solutions/impute.py",
        evaluate_dev=lambda candidate, timeout: calls.append((candidate, timeout)) or {
            "status": "GRADED", "dev_score": 0.12, "dev_cells": 42,
        },
    )
    result = runner.run(
        protocol=protocol, approval_digest="2" * 64, campaign_id="c-1",
        spec_hash="3" * 64, candidate_snapshot_id="4" * 40,
        candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"candidate"},
        evaluator_bundle=bundle, tier="dev", tier_manifest=dev_manifest,
        case_inputs={(AREPublicEvaluatorRunner.dev_aggregate_case_id, 1): dev_input},
    )
    payload = json.loads(result.private_result_bytes)
    assert result.tier == "dev"
    assert payload["metrics"]["imputation_r2"]["value"] == 0.12
    assert payload["diagnostics"]["split"] == "public_dev"
    assert len(calls) == 1 and calls[0][0] == b"candidate"
    assert calls[0][1] <= 3600


def test_dev_rejects_changed_frozen_input_without_running_container(tmp_path):
    protocol, _ = _protocol_and_manifest()
    bundle = _bundle()
    candidate_input = AREPublicEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64,
        dev_image_id=AREPublicEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicEvaluatorRunner.default_dev_cell_count,
    )
    model = ProtocolManifest(
        schema_version=1, protocol_sha256=_case_protocol_digest(protocol),
        cases=(ProtocolCase(case_id=AREPublicEvaluatorRunner.dev_aggregate_case_id, repetition=1,
                            input_sha256=hashlib.sha256(candidate_input).hexdigest()),),
    )
    encoded = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    protocol = protocol.model_copy(update={"tiers": tuple(
        item.model_copy(update={"manifest_sha256": hashlib.sha256(encoded).hexdigest()})
        if item.tier == "dev" else item for item in protocol.tiers
    )})
    called = []
    runner = AREPublicEvaluatorRunner(
        grader=PublicAREGrader(
            tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
            call_tool=lambda *_: {},
        ),
        publish_candidate=lambda _: None, task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64,
        candidate_path="benchmarks/autoresearchexam/solutions/impute.py",
        evaluate_dev=lambda *_: called.append(True),
    )
    with pytest.raises(TrustedEvaluatorError, match="aggregate scope"):
        runner.run(
            protocol=protocol, approval_digest="2" * 64, campaign_id="c-1",
            spec_hash="3" * 64, candidate_snapshot_id="4" * 40,
            candidate_files={"benchmarks/autoresearchexam/solutions/impute.py": b"candidate"},
            evaluator_bundle=bundle, tier="dev", tier_manifest=encoded,
            case_inputs={(AREPublicEvaluatorRunner.dev_aggregate_case_id, 1): b"tampered"},
        )
    assert called == []


def test_validation_rejects_casewise_manifest_for_one_aggregate_score(tmp_path):
    bundle = _bundle(candidate_path="solution.py")
    protocol, _ = _protocol_and_manifest(bundle)
    aggregate_input = next(iter(_validation_inputs().values()))
    digest = hashlib.sha256(aggregate_input).hexdigest()
    cases = tuple(
        ProtocolCase(
            case_id=AREPublicEvaluatorRunner.validation_aggregate_case_id,
            repetition=rep, input_sha256=digest,
        )
        for rep in (1, 2)
    )
    manifest_model = ProtocolManifest(
        schema_version=1, protocol_sha256=_case_protocol_digest(protocol), cases=cases,
    )
    encoded = json.dumps(
        manifest_model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    protocol = protocol.model_copy(update={"tiers": tuple(
        item.model_copy(update={
            "manifest_sha256": hashlib.sha256(encoded).hexdigest(), "case_count": 2,
        }) if item.tier == "validation" else item for item in protocol.tiers
    )})
    grader_calls = []
    runner = AREPublicEvaluatorRunner(
        grader=PublicAREGrader(
            tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
            call_tool=lambda *args: grader_calls.append(args) or {},
        ),
        publish_candidate=lambda _: None, task_id="are-task", task_version="1.1.0",
        contract_sha256="f" * 64, candidate_path="solution.py",
    )
    with pytest.raises(TrustedEvaluatorError, match="exactly one aggregate case"):
        runner.run(
            protocol=protocol, approval_digest="2" * 64, campaign_id="c-1",
            spec_hash="3" * 64, candidate_snapshot_id="4" * 40,
            candidate_files={"solution.py": b"candidate"}, evaluator_bundle=bundle,
            tier="validation", tier_manifest=encoded,
            case_inputs={
                (AREPublicEvaluatorRunner.validation_aggregate_case_id, 1): aggregate_input,
                (AREPublicEvaluatorRunner.validation_aggregate_case_id, 2): aggregate_input,
            },
        )
    assert grader_calls == []


def test_public_submission_resumes_by_pinned_id_without_duplicate_submit(tmp_path):
    protocol, manifest, bundle = _protocol_for_path("solution.py")
    now = [100.0]
    calls = []

    def tool(name, arguments):
        calls.append((name, arguments))
        if name == "submit":
            return {"id": 12, "status": "queued"}
        if len([call for call in calls if call[0] == "result"]) == 1:
            return {"id": 12, "status": "grading"}
        return {"id": 12, "status": "graded", "validation_score": 0.2,
                "grading_seconds": 2.0, "grader_output_tail": "not retained"}

    grader = PublicAREGrader(
        tmp_path / "ledger.sqlite3", task_id="are-task", contract_sha256="f" * 64,
        call_tool=tool, time_limit_seconds=10, clock=lambda: 100.0,
    )
    common = dict(
        grader=grader, publish_candidate=lambda _: None, task_id="are-task",
        task_version="1.1.0", contract_sha256="f" * 64, candidate_path="solution.py",
        poll_interval_seconds=0.05, clock=lambda: now[0],
    )
    first = AREPublicEvaluatorRunner(**common, sleep=lambda _: now.__setitem__(0, 111.0))
    args = dict(
        protocol=protocol, approval_digest="2" * 64, campaign_id="c-1",
        spec_hash="3" * 64, candidate_snapshot_id="1" * 40,
        candidate_files={"solution.py": b"baseline"}, evaluator_bundle=bundle,
        tier="validation", tier_manifest=manifest, case_inputs=_validation_inputs(),
    )
    with pytest.raises(PublicGraderError, match="deadline expired while public validation was pending"):
        first.run(**args)
    assert grader.pending_snapshot("1" * 40)["submission_id"] == 12

    now[0] = 102.0
    resumed = AREPublicEvaluatorRunner(**common, sleep=lambda _: None)
    result = resumed.run(**args)
    assert result.tier == "validation"
    assert [name for name, _ in calls].count("submit") == 1
    assert [name for name, _ in calls].count("result") == 2
