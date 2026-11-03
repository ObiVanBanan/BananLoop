import hashlib

import pytest

from bananloop.contracts import FeatureFlagsV2, V2_FULL_FEATURES
from benchmarks.v02_full.prereg import (
    CIJob,
    EvaluatorPin,
    GateEvidence,
    PreregError,
    VerificationPin,
    freeze_manifest,
    manifest_digest,
    canonical_bytes,
    validate_run_gate,
    validate_track_pair,
    verify_frozen_manifest,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _raw_manifest(track="OPTIMIZATION", *, task_id="task-opt", suffix="opt"):
    caps = {
        "agent_calls": 20, "evaluator_calls": 12, "experiments": 8,
        "retries": 4, "input_tokens": 500_000, "output_tokens": 100_000,
        "cached_tokens": 100_000, "wall_seconds": 7200, "spend_microusd": 5_000_000,
    }
    arm_common = {
        "provider": "provider-pin", "model": "model-pin", "model_version": "model-version-pin",
        "reasoning_settings_sha256": _sha("reasoning"), "tool_policy_sha256": _sha("tools"),
        "caps": caps,
    }
    return {
        "schema_version": 1,
        "track": track,
        "protocol_id": f"rpdi14-{suffix}",
        "protocol_version": 1,
        "source_sha": _sha("full-source"),
        "full_feature_flags": list(V2_FULL_FEATURES),
        "full_flag_hash": FeatureFlagsV2(profile="FULL").flag_hash(),
        "campaign_spec_sha256": _sha(f"spec-{suffix}"),
        "task_pins": [{"task_id": f"{task_id}-{index}",
                       "task_data_sha256": _sha(f"data-{task_id}-{index}"),
                       "initial_tree_sha256": _sha(f"tree-{task_id}-{index}"),
                       "rights_record_sha256": _sha(f"rights-{task_id}-{index}")}
                      for index in range(12)],
        "arms": [
            {**arm_common, "arm": "BANANLOOP_V02_FULL", "role_roster_sha256": _sha("role-roster"),
             "iteration_policy": {"maximum_iterations": 4, "feedback_source": "APPROVED_DEV_ONLY",
                                  "permits_repair": True, "stopping_rule_sha256": _sha("loop-stop")}},
            {**arm_common, "arm": "CODEX_DIRECT", "role_roster_sha256": None,
             "iteration_policy": {"maximum_iterations": 4, "feedback_source": "APPROVED_DEV_ONLY",
                                  "permits_repair": True, "stopping_rule_sha256": _sha("direct-stop")}},
        ],
        "common_caps": caps,
        "repeats_per_task": 1,
        "evaluator": {"evaluator_sha256": _sha(f"evaluator-{suffix}"),
                      "protocol_sha256": _sha(f"protocol-{suffix}"),
                      "rubric_sha256": _sha(f"rubric-{suffix}"),
                      "validation_set_sha256": _sha(f"validation-{suffix}"),
                      "holdout_set_sha256": _sha(f"holdout-{suffix}")},
        "deterministic_seed": f"seed-{suffix}",
        "inference": {"estimand": f"Mean paired score difference for {suffix} tasks",
                      "primary_outcome": f"{suffix}_primary_score", "direction": "HIGHER_IS_BETTER",
                      "paired_estimator": "MEAN_DIFFERENCE", "interval_method": "PAIRED_T",
                      "coverage_percent": 95, "sidedness": "TWO_SIDED", "minimum_usable_pairs": 12,
                      "repeats_per_task": 1, "planned_pairs": 12, "sample_target_kind": "PRECISION",
                      "assumed_paired_sd": 0.25, "target_interval_half_width": 0.16,
                      "required_pairs": 12, "missing_pair_rule": "retain as missing; no imputation",
                      "tie_rule": "zero paired delta is a tie", "multiplicity_rule": "no joint claim"},
        "matched_controls": ["task snapshot", "DEV feedback", "resource caps", "model capability"],
        "unmatched_control_disclosures": ["BananLoop uses isolated multi-agent role roster"],
        "rights_register_sha256": _sha("rights-register"),
        "spend_authorization_sha256": _sha("spend-authorization"),
        "operator_acceptance_sha256": _sha("operator-acceptance"),
        "owner_approval_sha256": _sha("owner-approval"),
        "frozen_at": "2026-11-03T00:00:00Z",
    }


def _valid_evidence(manifest):
    source = manifest.source_sha
    ci = tuple(CIJob(name=name, conclusion="success", head_sha=source) for name in (
        "pytest (ubuntu-latest, 3.11)", "pytest (ubuntu-latest, 3.12)",
        "pytest (ubuntu-latest, 3.13)", "pytest (windows-latest, 3.12)",
    ))
    return GateEvidence(
        source_sha=source,
        full_flag_hash=manifest.full_flag_hash,
        campaign_spec_sha256=manifest.campaign_spec_sha256,
        rpdi13_e2e=VerificationPin(artifact_sha256=_sha("integrated-e2e-receipt"), subject_sha=source,
                                   conclusion="PASS", reviewer_role="INTEGRATED_E2E_VERIFIER"),
        rpdi13_accessibility=VerificationPin(artifact_sha256=_sha("screen-reader"), subject_sha=source,
                                             conclusion="PASS", reviewer_role="SCREEN_READER_REVIEWER"),
        ci_jobs=ci,
        browser_ci=CIJob(name="dashboard-browser", conclusion="success", head_sha=source),
        technical_review=VerificationPin(artifact_sha256=_sha("review"), subject_sha=source,
                                         conclusion="PASS", reviewer_role="INDEPENDENT_TECHNICAL_REVIEWER"),
        statistical_review=VerificationPin(
            artifact_sha256=_sha("statistical-review"),
            subject_sha=manifest.manifest_sha256,
            conclusion="PASS", reviewer_role="INDEPENDENT_STATISTICAL_REVIEWER",
        ),
        evaluator_isolation=VerificationPin(
            artifact_sha256=_sha("isolation"),
            subject_sha=hashlib.sha256(canonical_bytes(manifest.evaluator)).hexdigest(),
            conclusion="PASS", reviewer_role="INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR",
        ),
        rights_register_sha256=manifest.rights_register_sha256,
        spend_authorization_sha256=manifest.spend_authorization_sha256,
        operator_acceptance_sha256=manifest.operator_acceptance_sha256,
        owner_approval_sha256=manifest.owner_approval_sha256,
        independent_operator=True,
    )


def test_frozen_manifest_is_canonical_and_all_execution_inputs_are_bound():
    frozen = freeze_manifest(_raw_manifest())
    assert frozen.manifest_sha256 == manifest_digest(frozen)
    verify_frozen_manifest(frozen, frozen.manifest_sha256)
    for path, update in (
        (("source_sha",), _sha("other-source")),
        (("full_flag_hash",), _sha("diagnostic")),
        (("campaign_spec_sha256",), _sha("other-spec")),
        (("deterministic_seed",), "new-seed"),
        (("task_pins", 0, "task_data_sha256"), _sha("other-task")),
        (("common_caps", "spend_microusd"), 1),
        (("arms", 0, "model_version"), "other-model-pin"),
        (("evaluator", "protocol_sha256"), _sha("other-evaluator")),
    ):
        changed = frozen.model_dump(mode="json")
        cursor = changed
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = update
        with pytest.raises(PreregError):
            freeze_manifest(changed)


def test_full_flags_and_codex_direct_iteration_are_mandatory():
    raw = _raw_manifest()
    raw["full_feature_flags"] = ["and_or_dag"]
    with pytest.raises(PreregError, match="Full feature set"):
        freeze_manifest(raw)
    raw = _raw_manifest()
    raw["arms"][1]["iteration_policy"]["maximum_iterations"] = 1
    with pytest.raises(PreregError, match="iterative policy"):
        freeze_manifest(raw)


def test_sample_size_and_paired_arm_caps_are_verified_not_asserted():
    raw = _raw_manifest()
    raw["inference"]["required_pairs"] = 11
    with pytest.raises(PreregError, match="required_pairs must equal"):
        freeze_manifest(raw)
    raw = _raw_manifest()
    raw["arms"][1]["caps"] = {**raw["common_caps"], "agent_calls": 19}
    with pytest.raises(PreregError, match="caps must exactly match"):
        freeze_manifest(raw)
    raw = _raw_manifest()
    raw["inference"]["minimum_usable_pairs"] = 8
    with pytest.raises(PreregError, match="minimum_usable_pairs cannot be below"):
        freeze_manifest(raw)
    raw = _raw_manifest()
    raw["task_pins"] = raw["task_pins"][:11]
    with pytest.raises(PreregError, match="task count times"):
        freeze_manifest(raw)


def test_track_pair_requires_disjoint_tasks_and_distinct_outcomes_and_rubrics():
    optimization = freeze_manifest(_raw_manifest())
    exploration = freeze_manifest(_raw_manifest("EXPLORATION", task_id="task-explore", suffix="explore"))
    validate_track_pair(optimization, exploration)
    tampered_optimization = optimization.model_copy(update={"task_pins": (optimization.task_pins[0],)})
    with pytest.raises(PreregError, match="content has changed"):
        validate_track_pair(tampered_optimization, exploration)
    overlap = freeze_manifest(_raw_manifest("EXPLORATION", task_id="task-opt", suffix="explore"))
    with pytest.raises(PreregError, match="disjoint"):
        validate_track_pair(optimization, overlap)


def test_run_gate_requires_exact_head_e2e_ci_review_and_independent_attestations():
    manifest = freeze_manifest(_raw_manifest())
    validate_run_gate(manifest, _valid_evidence(manifest))
    tampered = manifest.model_copy(update={"task_pins": (manifest.task_pins[0],)})
    with pytest.raises(PreregError, match="content has changed"):
        validate_run_gate(tampered, _valid_evidence(manifest))
    incomplete = _valid_evidence(manifest).model_copy(update={"ci_jobs": ()})
    with pytest.raises(PreregError, match="matrix is incomplete"):
        validate_run_gate(manifest, incomplete)
    wrong_head = _valid_evidence(manifest).model_copy(update={
        "rpdi13_e2e": VerificationPin(
            artifact_sha256=_sha("integrated-e2e"), subject_sha=_sha("old-source"),
            conclusion="PASS", reviewer_role="INTEGRATED_E2E_VERIFIER",
        ),
    })
    with pytest.raises(PreregError, match="E2E"):
        validate_run_gate(manifest, wrong_head)
    failed_e2e = _valid_evidence(manifest).model_copy(update={
        "rpdi13_e2e": VerificationPin(
            artifact_sha256=_sha("failed-e2e"), subject_sha=manifest.source_sha,
            conclusion="ACCEPTED", reviewer_role="INTEGRATED_E2E_VERIFIER",
        ),
    })
    with pytest.raises(PreregError, match="E2E did not pass"):
        validate_run_gate(manifest, failed_e2e)
    isolated_wrong_evaluator = _valid_evidence(manifest).model_copy(update={
        "evaluator_isolation": VerificationPin(
            artifact_sha256=_sha("isolation"), subject_sha=_sha("old-evaluator"),
            conclusion="PASS", reviewer_role="INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR",
        ),
    })
    with pytest.raises(PreregError, match="different evaluator"):
        validate_run_gate(manifest, isolated_wrong_evaluator)
    no_screenreader = _valid_evidence(manifest).model_copy(update={
        "rpdi13_accessibility": VerificationPin(
            artifact_sha256=_sha("no-screen-reader"), subject_sha=manifest.source_sha,
            conclusion="ACCEPTED", reviewer_role="SCREEN_READER_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="manual accessibility"):
        validate_run_gate(manifest, no_screenreader)
    non_independent_review = _valid_evidence(manifest).model_copy(update={
        "technical_review": VerificationPin(
            artifact_sha256=_sha("review"), subject_sha=manifest.source_sha,
            conclusion="PASS", reviewer_role="SCREEN_READER_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="technical review must be independent"):
        validate_run_gate(manifest, non_independent_review)
    no_statistical_review = _valid_evidence(manifest).model_copy(update={
        "statistical_review": VerificationPin(
            artifact_sha256=_sha("statistical-review"), subject_sha=manifest.manifest_sha256,
            conclusion="ACCEPTED", reviewer_role="INDEPENDENT_STATISTICAL_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="statistical review"):
        validate_run_gate(manifest, no_statistical_review)
