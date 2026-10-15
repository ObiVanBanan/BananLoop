from bananloop.contracts import EvaluationEvidence


def test_newer_evaluation_evidence_metadata_round_trips():
    evidence = EvaluationEvidence.model_validate({
        "technical_readiness": "READY",
        "trust_level": "UNVERIFIED",
        "metric_relative_tolerances": {"quality": 0.0},
        "reference_manifest_sha256": "a" * 64,
        "reference_approved_by": None,
        "reference_receipt_sha256": "b" * 64,
        "reference_receipt_path": "bootstrap-readiness/reference.json",
        "independently_validated_tiers": [],
    })

    restored = EvaluationEvidence.model_validate_json(evidence.model_dump_json())

    assert restored.metric_relative_tolerances == {"quality": 0.0}
    assert restored.reference_manifest_sha256 == "a" * 64
    assert restored.reference_receipt_sha256 == "b" * 64
    assert restored.reference_receipt_path == "bootstrap-readiness/reference.json"
