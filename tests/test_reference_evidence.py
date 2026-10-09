import hashlib
import json

import pytest

from bananloop.contracts import CaseMeasurement, Direction, EvaluationResult, MetricSpec
from bananloop.reference_evidence import (
    ReferenceManifestError,
    parse_reference_manifest,
    verify_reference_results,
)


def _fixture():
    protocol = "a" * 64
    cases = [
        {"case_id": "good-a", "repetition": 0, "input_sha256": "1" * 64, "label": "positive", "expected_metric_value": 1.0},
        {"case_id": "good-b", "repetition": 0, "input_sha256": "2" * 64, "label": "positive", "expected_metric_value": 1.0},
        {"case_id": "bad-a", "repetition": 0, "input_sha256": "3" * 64, "label": "negative", "expected_metric_value": 0.0},
        {"case_id": "bad-b", "repetition": 0, "input_sha256": "4" * 64, "label": "negative", "expected_metric_value": 0.0},
    ]
    raw = json.dumps({
        "schema_version": 1,
        "protocol_sha256": protocol,
        "primary_metric": "quality",
        "direction": "maximize",
        "case_source": "operator-reviewed fixtures",
        "label_source": "approved acceptance criteria",
        "reference_basis": "known-good and known-bad examples",
        "cases": cases,
    }, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = parse_reference_manifest(raw, digest)

    def result(values):
        return EvaluationResult(
            metrics={"quality": {"value": sum(values.values()) / len(values)}},
            protocol_sha256=protocol,
            cases=[CaseMeasurement(
                case_id=case["case_id"], repetition=0,
                input_sha256=case["input_sha256"], metrics={"quality": values[case["case_id"]]},
            ) for case in cases if case["case_id"] in values],
        )

    full = result({case["case_id"]: case["expected_metric_value"] for case in cases})
    smoke = result({"good-a": 1.0, "bad-a": 0.0})
    metric = MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1, min_improvement=0.1)
    return raw, digest, manifest, full, smoke, metric


def test_operator_reference_manifest_verifies_full_and_smoke_results():
    _, digest, manifest, full, smoke, metric = _fixture()
    receipt = verify_reference_results(
        manifest=manifest, manifest_sha256=digest, approved_by="operator@example.test",
        primary_metric=metric, full_result=full, smoke_results=[smoke, smoke],
    )
    assert receipt["status"] == "INDEPENDENTLY_VALIDATED"
    assert receipt["approved_by"] == "operator@example.test"
    assert receipt["case_count"] == 4
    assert receipt["checks"]["positive_negative_separation"] is True


def test_manifest_digest_and_reference_mismatches_are_rejected():
    raw, digest, manifest, full, smoke, metric = _fixture()
    with pytest.raises(ReferenceManifestError, match="SHA-256"):
        parse_reference_manifest(raw, "0" * 64)
    changed = full.model_copy(update={"protocol_sha256": "b" * 64})
    with pytest.raises(ReferenceManifestError, match="protocol"):
        verify_reference_results(manifest=manifest, manifest_sha256=digest, approved_by="op", primary_metric=metric, full_result=changed, smoke_results=[smoke])
    changed = full.model_copy(update={"cases": full.cases[:-1]})
    with pytest.raises(ReferenceManifestError, match="case/repetition set"):
        verify_reference_results(manifest=manifest, manifest_sha256=digest, approved_by="op", primary_metric=metric, full_result=changed, smoke_results=[smoke])


def test_smoke_must_match_approved_per_case_values():
    _, digest, manifest, full, _, metric = _fixture()
    failed_smoke = full.model_copy(update={"cases": [
        full.cases[0],
        full.cases[2].model_copy(update={"metrics": {"quality": 1.0}}),
    ]})
    with pytest.raises(ReferenceManifestError, match="approved reference"):
        verify_reference_results(manifest=manifest, manifest_sha256=digest, approved_by="op", primary_metric=metric, full_result=full, smoke_results=[failed_smoke])
