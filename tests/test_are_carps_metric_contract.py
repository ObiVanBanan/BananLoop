from pathlib import Path

import pytest

from benchmarks.autoresearchexam import carps_runner
from benchmarks.autoresearchexam.carps_runner import AREPublicCARPSEvaluatorRunner
from benchmarks.autoresearchexam import public_carps_dev
from benchmarks.autoresearchexam.public_carps_dev import PublicCARPSCandidateTimeout
from bananloop.trusted_evaluator import TrustedPublicEvaluatorFailure


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


def test_carps_candidate_timeout_failure_surfaces_as_fixed_cli_detail(monkeypatch):
    runner = object.__new__(AREPublicCARPSEvaluatorRunner)
    runner._active_public_task_root = Path("/public/carps")
    runner.total_dev_timeout_seconds = 180
    runner.dev_image = "public-image"
    runner.dev_image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(
        carps_runner,
        "evaluate_carps_public_dev",
        lambda *args, **kwargs: {
            "status": "FAILED", "failure": "PUBLIC_DEV_CANDIDATE_TIMEOUT",
        },
    )

    with pytest.raises(TrustedPublicEvaluatorFailure) as raised:
        runner._evaluate_public_dev(b"candidate", timeout_seconds=60)

    assert raised.value.failure_detail == "PUBLIC_DEV_CANDIDATE_TIMEOUT"
    assert str(raised.value) == "public evaluator failed"
    assert PublicCARPSCandidateTimeout.__bases__[0].__name__ == "PublicCARPSEvaluatorError"


def test_public_dev_maps_only_candidate_wall_cap_to_typed_failure(monkeypatch):
    monkeypatch.setattr(
        public_carps_dev, "_load_public_panel",
        lambda _root: (b"manifest", [("cloud_00.npy", 2436, 30, b"cloud", object())]),
    )
    monkeypatch.setattr(
        public_carps_dev.subprocess, "run",
        lambda *args, **kwargs: type("Inspection", (), {
            "returncode": 0, "stdout": "sha256:" + "a" * 64,
        })(),
    )

    def timeout(**_kwargs):
        raise PublicCARPSCandidateTimeout("opaque internal detail")

    monkeypatch.setattr(public_carps_dev, "_run_one_cloud", timeout)
    response = public_carps_dev.evaluate_carps_public_dev(
        b"candidate", public_task_root=Path("/public/carps"), image="carps",
        expected_image_id="sha256:" + "a" * 64, total_timeout_seconds=30,
    )

    assert response == {"status": "FAILED", "failure": "PUBLIC_DEV_CANDIDATE_TIMEOUT"}


def test_carps_runner_does_not_surface_unknown_public_failure_codes(monkeypatch):
    runner = object.__new__(AREPublicCARPSEvaluatorRunner)
    runner._active_public_task_root = Path("/public/carps")
    runner.total_dev_timeout_seconds = 180
    runner.dev_image = "public-image"
    runner.dev_image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(
        carps_runner,
        "evaluate_carps_public_dev",
        lambda *args, **kwargs: {
            "status": "FAILED", "failure": "untrusted detail must stay hidden",
        },
    )

    with pytest.raises(carps_runner.PublicCARPSEvaluatorError) as raised:
        runner._evaluate_public_dev(b"candidate", timeout_seconds=60)

    assert "untrusted detail" not in str(raised.value)


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
