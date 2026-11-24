from pathlib import Path

from benchmarks.autoresearchexam import carps_runner
from benchmarks.autoresearchexam.carps_runner import AREPublicCARPSEvaluatorRunner


def test_carps_protocol_negates_the_raw_public_discrepancy_once(monkeypatch):
    runner = object.__new__(AREPublicCARPSEvaluatorRunner)
    runner._active_public_task_root = Path("/public/carps")
    runner.total_dev_timeout_seconds = 180
    runner.dev_image = "public-image"
    runner.dev_image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(
        carps_runner,
        "evaluate_carps_public_dev",
        lambda *args, **kwargs: {
            "status": "GRADED", "panel_count": 8,
            "mean_exact_linf_star_discrepancy": 0.25,
        },
    )

    result = runner._evaluate_public_dev(b"candidate", timeout_seconds=60)

    assert result == {"status": "GRADED", "dev_score": -0.25, "dev_cells": 8}
    assert carps_runner.METRIC_DIRECTION == "MAXIMIZE"


def test_carps_score_keeps_execution_identity_and_updates_digest(monkeypatch):
    import hashlib
    import json

    from benchmarks.autoresearchexam.optimization_runner import AREPublicEvaluatorRunner
    from bananloop.trusted_evaluator import EvaluatorExecutionV1

    initial = EvaluatorExecutionV1(
        campaign_id="campaign", spec_hash="spec", approval_digest="approval",
        protocol_id="protocol", protocol_version=1, protocol_digest="protocol-digest",
        source_snapshot_id="baseline", candidate_snapshot_id="candidate", tier="dev",
        evaluator_bundle_sha256="bundle", tier_manifest_sha256="manifest",
        case_count=1, result_digest="old-digest", repeatability_runs=1,
        output_artifact_digests=(), private_result_bytes=b'{"diagnostics":{"split":"public_dev"}}',
    )
    monkeypatch.setattr(
        AREPublicEvaluatorRunner, "_execution_from_score", staticmethod(lambda **_: initial),
    )
    execution = AREPublicCARPSEvaluatorRunner._execution_from_score(
        protocol=None, approval_digest="approval", campaign_id="campaign",
        spec_hash="spec", candidate_snapshot_id="candidate", tier_manifest=b"",
        tier="dev", split="public_dev", score=-0.25,
    )
    diagnostics = json.loads(execution.private_result_bytes)["diagnostics"]
    assert diagnostics["metric_source"] == carps_runner.DEV_METRIC_SOURCE
    assert execution.scope_identity() == initial.scope_identity()
    assert execution.result_digest == hashlib.sha256(execution.private_result_bytes).hexdigest()
