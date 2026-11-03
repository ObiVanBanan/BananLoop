from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from benchmarks.v02_full.prereg import canonical_bytes, freeze_manifest
from benchmarks.v02_full.report import (
    ReportError, aggregate_blinded_scores, score_payload_digest,
)
from benchmarks.v02_full.scorer import (
    EVALUATOR_CONSTITUENT_FILES, BlindScorerOutput, BubblewrapBlindScorer,
    ScorerError, ScorerOutputPair,
    apply_blind_scorer_output, build_blind_scorer_input, evaluator_bundle_fingerprint,
    scorer_output_digest, scorer_runtime_fingerprint, validate_blind_scorer_output,
    verify_evaluator_constituents, verify_no_pinned_arm_identifiers,
)

_spec = __import__("importlib.util", fromlist=["spec_from_file_location"]).spec_from_file_location(
    "prereg_test_helpers", Path(__file__).with_name("test_v02_full_prereg.py"),
)
assert _spec and _spec.loader
_helpers = __import__("importlib.util", fromlist=["module_from_spec"]).module_from_spec(_spec)
sys.modules[_spec.name] = _helpers
_spec.loader.exec_module(_helpers)


def _blind_request(manifest, package, root):
    _, trust_policy = _helpers._valid_evidence(manifest, root / "prereq-evidence")
    return build_blind_scorer_input(
        manifest, package, evidence_root=str(root), trust_policy=trust_policy,
    )


def _scorer_output(request, *, score=1.25):
    pairs = tuple(ScorerOutputPair(
        pair_id_sha256=item.pair_id_sha256, status=item.status,
        arm_a_output_sha256=item.arm_a_output_sha256,
        arm_b_output_sha256=item.arm_b_output_sha256,
        arm_a_score=score if item.status == "SCORED" else None,
        arm_b_score=1.0 if item.status == "SCORED" else None,
    ) for item in request.pairs)
    raw = {
        "schema_version": 1, "track": request.track,
        "manifest_sha256": request.manifest_sha256,
        "evaluator_pin_sha256": request.evaluator_pin_sha256,
        "pairs": tuple(item.model_dump(mode="json") for item in pairs),
    }
    raw["score_payload_sha256"] = scorer_output_digest(raw)
    return canonical_bytes(raw)


def test_scorer_input_contains_only_blind_outputs_and_frozen_evaluator_pins(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(
        manifest, tmp_path, failed_pair_id=manifest.task_pins[0].blinded_pair_id_sha256,
    )
    request = _blind_request(manifest, package, tmp_path)
    payload = json.loads(canonical_bytes(request))

    assert len(request.pairs) == len(manifest.task_pins)
    failed = next(pair for pair in request.pairs if pair.status == "FAILED")
    scored = next(pair for pair in request.pairs if pair.status == "SCORED")
    assert failed.arm_a_output_base64 is None
    assert scored.arm_a_output_base64
    scored_bytes = __import__("base64").b64decode(scored.arm_a_output_base64)
    assert b"sanitized candidate" in scored_bytes
    assert b"provider-pin" not in scored_bytes and b"model-pin" not in scored_bytes
    encoded = canonical_bytes(payload).decode("utf-8")
    for forbidden in ("provider", "model", "role_roster", "arm_a_receipts",
                      "accounting", "unblinding", "task_id", "output-" ):
        assert forbidden not in encoded


def test_scorer_output_joins_scores_without_receipt_disclosure(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    failed_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _helpers._report_package(manifest, tmp_path, failed_pair_id=failed_pair_id)
    request = _blind_request(manifest, package, tmp_path)
    output = validate_blind_scorer_output(_scorer_output(request), request=request)
    merged = apply_blind_scorer_output(package, output)

    assert merged.score_payload_sha256 == score_payload_digest(merged)
    assert any(pair.status == "FAILED" for pair in merged.pairs)
    scored = next(pair for pair in merged.pairs if pair.status == "SCORED")
    before = next(pair for pair in package.pairs if pair.pair_id_sha256 == scored.pair_id_sha256)
    assert scored.arm_a_receipts == before.arm_a_receipts
    assert scored.arm_a_score == 1.25 and scored.arm_b_score == 1.0

    scoring_ref, unblinding_ref = _helpers._attest_score_package(tmp_path, manifest, merged)
    gate_evidence, trust_policy = _helpers._valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, merged, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path), trust_policy=trust_policy,
    )
    assert report.usable_pairs == len(manifest.task_pins) - 1


def test_scorer_output_rejects_changed_or_extra_pairs(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(manifest, tmp_path)
    request = _blind_request(manifest, package, tmp_path)
    raw = json.loads(_scorer_output(request))
    raw["pairs"][0]["arm_a_output_sha256"] = "0" * 64
    raw["score_payload_sha256"] = scorer_output_digest(raw)
    with pytest.raises(ScorerError, match="changed pair identity"):
        validate_blind_scorer_output(canonical_bytes(raw), request=request)

    raw = json.loads(_scorer_output(request))
    raw["pairs"].append(raw["pairs"][-1])
    raw["score_payload_sha256"] = scorer_output_digest(raw)
    with pytest.raises(ScorerError, match="malformed"):
        validate_blind_scorer_output(canonical_bytes(raw), request=request)


def test_accounting_changes_do_not_change_the_score_attestation_payload(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(manifest, tmp_path)
    request = _blind_request(manifest, package, tmp_path)
    output = validate_blind_scorer_output(_scorer_output(request), request=request)
    merged = apply_blind_scorer_output(package, output)
    original_score_digest = merged.score_payload_sha256
    original_package_digest = merged.package_sha256
    pair = merged.pairs[0]
    changed_pair = pair.model_copy(update={"arm_a_action_attestations": ()})
    from benchmarks.v02_full.report import pair_score_digest, score_package_digest
    changed_pair = changed_pair.model_copy(update={"pair_result_sha256": pair_score_digest(changed_pair)})
    changed_pairs = tuple(changed_pair if item.pair_id_sha256 == pair.pair_id_sha256 else item
                          for item in merged.pairs)
    raw_package = merged.model_dump(mode="json")
    raw_package["pairs"] = [item.model_dump(mode="json") for item in changed_pairs]
    raw_package["score_payload_sha256"] = score_payload_digest(raw_package)
    raw_package.pop("package_sha256")
    raw_package["package_sha256"] = score_package_digest(raw_package)
    assert raw_package["score_payload_sha256"] == original_score_digest
    assert raw_package["package_sha256"] != original_package_digest


def test_score_attestation_binds_failed_pair_classification(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(manifest, tmp_path)
    request = _blind_request(manifest, package, tmp_path)
    output = validate_blind_scorer_output(_scorer_output(request), request=request)
    merged = apply_blind_scorer_output(package, output)
    payload = merged.model_dump(mode="json")
    pair = next(item for item in payload["pairs"] if item["status"] == "SCORED")
    pair.update(status="FAILED", arm_a_score=None, arm_b_score=None, reason="changed after scoring")
    pair.pop("pair_result_sha256")
    pair["pair_result_sha256"] = __import__(
        "benchmarks.v02_full.report", fromlist=["pair_score_digest"],
    ).pair_score_digest(pair)
    assert score_payload_digest(payload) != merged.score_payload_sha256


def test_aggregate_rechecks_stale_score_digest_even_after_model_copy(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(manifest, tmp_path)
    request = _blind_request(manifest, package, tmp_path)
    output = validate_blind_scorer_output(_scorer_output(request), request=request)
    merged = apply_blind_scorer_output(package, output)
    pair = next(item for item in merged.pairs if item.status == "SCORED")
    changed = pair.model_copy(update={
        "status": "FAILED", "arm_a_score": None, "arm_b_score": None,
        "reason": "changed after scoring",
    })
    from benchmarks.v02_full.report import pair_score_digest, score_package_digest
    changed = changed.model_copy(update={"pair_result_sha256": pair_score_digest(changed)})
    pairs = tuple(changed if item.pair_id_sha256 == pair.pair_id_sha256 else item
                  for item in merged.pairs)
    bad_payload = merged.model_dump(mode="json")
    bad_payload["pairs"] = [item.model_dump(mode="json") for item in pairs]
    bad_payload.pop("package_sha256")
    bad_payload["package_sha256"] = score_package_digest(bad_payload)
    tampered = merged.model_copy(update={
        "pairs": pairs, "package_sha256": bad_payload["package_sha256"],
    })
    scoring_ref, unblinding_ref = _helpers._attest_score_package(tmp_path, manifest, merged)
    gate_evidence, trust_policy = _helpers._valid_evidence(manifest, tmp_path)
    with pytest.raises(ReportError, match="scorer-attested score projection"):
        aggregate_blinded_scores(
            manifest, tampered, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=trust_policy,
        )


def test_evaluator_bundle_fingerprint_is_content_and_mode_bound(tmp_path):
    bundle = tmp_path / "evaluator"
    bundle.mkdir()
    executable = bundle / "score"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    first = evaluator_bundle_fingerprint(bundle)
    assert evaluator_bundle_fingerprint(bundle) == first
    executable.chmod(0o500)
    assert evaluator_bundle_fingerprint(bundle) != first


def test_blind_scorer_does_not_report_available_without_isolation(tmp_path):
    scorer = BubblewrapBlindScorer(cgroup_root=tmp_path, bwrap_binary="/missing/bwrap")
    assert not scorer.available()


def test_evaluator_constituents_are_checked_against_individual_pins(tmp_path):
    from benchmarks.v02_full.prereg import freeze_manifest

    manifest = freeze_manifest(_helpers._raw_manifest())
    fields = {}
    for field, filename in EVALUATOR_CONSTITUENT_FILES.items():
        content = f"pinned {filename}".encode()
        (tmp_path / filename).write_bytes(content)
        fields[field] = hashlib.sha256(content).hexdigest()
    pin = manifest.evaluator.model_copy(update=fields)
    verify_evaluator_constituents(tmp_path, pin)
    (tmp_path / "holdout-set.bin").write_bytes(b"changed holdout")
    with pytest.raises(ScorerError, match="constituent digest differs"):
        verify_evaluator_constituents(tmp_path, pin)


def test_scorer_runtime_fingerprint_binds_runtime_and_sandbox(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "library.so").write_bytes(b"runtime-v1")
    sandbox = tmp_path / "bwrap"
    sandbox.write_bytes(b"sandbox-v1")
    sandbox.chmod(0o700)
    interpreter = tmp_path / "python"
    interpreter.write_bytes(b"python-v1")
    launcher = tmp_path / "launcher.py"
    launcher.write_bytes(b"launcher-v1")
    first = scorer_runtime_fingerprint(
        sandbox_binary=sandbox, interpreter=interpreter, launcher=launcher,
        system_roots=(runtime,),
    )
    (runtime / "library.so").write_bytes(b"runtime-v2")
    second = scorer_runtime_fingerprint(
        sandbox_binary=sandbox, interpreter=interpreter, launcher=launcher,
        system_roots=(runtime,),
    )
    assert first[0] != second[0]
    assert first[1] == second[1]


def test_scorer_input_rejects_literal_arm_and_model_identifiers():
    manifest = freeze_manifest(_helpers._raw_manifest())
    with pytest.raises(ScorerError, match="pinned arm/provider/model identifier"):
        verify_no_pinned_arm_identifiers(
            b"The output was generated by model-pin", manifest,
        )
    verify_no_pinned_arm_identifiers(b"A neutral candidate result", manifest)


def test_scorer_input_requires_matching_independent_anonymization_evidence(tmp_path):
    manifest = freeze_manifest(_helpers._raw_manifest())
    package = _helpers._report_package(manifest, tmp_path)
    pair = next(item for item in package.pairs if item.status == "SCORED")
    wrong_attestation = pair.arm_b_anonymization_attestation
    changed_pair = pair.model_copy(update={"arm_a_anonymization_attestation": wrong_attestation})
    from benchmarks.v02_full.report import pair_score_digest
    changed_pair = changed_pair.model_copy(update={"pair_result_sha256": pair_score_digest(changed_pair)})
    pairs = tuple(changed_pair if item.pair_id_sha256 == pair.pair_id_sha256 else item
                  for item in package.pairs)
    changed_package = package.model_copy(update={"pairs": pairs})
    _, trust_policy = _helpers._valid_evidence(manifest, tmp_path / "gates")
    with pytest.raises(ScorerError, match="output anonymization evidence is invalid"):
        build_blind_scorer_input(
            manifest, changed_package, evidence_root=str(tmp_path), trust_policy=trust_policy,
        )
