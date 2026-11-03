import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bananloop.contracts import FeatureFlagsV2, V2_FULL_FEATURES
from benchmarks.v02_full.evidence import (
    EvidenceArtifactRef, EvidenceError, TrustPolicy, TrustedSigner, sign_evidence,
)
from benchmarks.v02_full.prereg import (
    CIJob,
    EvaluatorPin,
    ControlComparison,
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
    raw = {
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
        "rights_register_sha256": _sha("rights-register"),
        "spend_authorization_sha256": _sha("spend-authorization"),
        "operator_acceptance_sha256": _sha("operator-acceptance"),
        "owner_approval_sha256": _sha("owner-approval"),
        "arm_author_principal_ids": ["principal-arm-author"],
        "arm_operator_principal_ids": ["principal-arm-operator"],
        "frozen_at": "2026-11-03T00:00:00Z",
    }
    full, direct = raw["arms"]
    controls = {
        "provider": (full["provider"], direct["provider"]),
        "model": (full["model"], direct["model"]),
        "model_version": (full["model_version"], direct["model_version"]),
        "reasoning_settings": (full["reasoning_settings_sha256"], direct["reasoning_settings_sha256"]),
        "tool_policy": (full["tool_policy_sha256"], direct["tool_policy_sha256"]),
        "maximum_iterations": (full["iteration_policy"]["maximum_iterations"],
                               direct["iteration_policy"]["maximum_iterations"]),
        "feedback_source": (full["iteration_policy"]["feedback_source"],
                            direct["iteration_policy"]["feedback_source"]),
        "permits_repair": (full["iteration_policy"]["permits_repair"],
                           direct["iteration_policy"]["permits_repair"]),
        "stopping_rule": (full["iteration_policy"]["stopping_rule_sha256"],
                          direct["iteration_policy"]["stopping_rule_sha256"]),
        "role_roster": (full["role_roster_sha256"] or "NO_ROLE_ROSTER",
                        direct["role_roster_sha256"] or "NO_ROLE_ROSTER"),
        "resource_caps": (full["caps"], direct["caps"]),
    }
    raw["control_comparisons"] = []
    for key, values in controls.items():
        digests = [hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=False).encode()).hexdigest()
                   for value in values]
        matched = digests[0] == digests[1]
        raw["control_comparisons"].append({
            "control_key": key,
            "bananloop_value_sha256": digests[0],
            "codex_direct_value_sha256": digests[1],
            "status": "MATCHED" if matched else "UNMATCHED",
            "difference_disclosure": None if matched else f"Deliberate difference in {key}.",
        })
    return raw


def _valid_evidence(manifest, evidence_dir, *, duplicate_independent_principal=False):
    source = manifest.source_sha
    roles = {
        "INTEGRATED_E2E_VERIFIER": "principal-e2e",
        "SCREEN_READER_REVIEWER": "principal-accessibility",
        "INDEPENDENT_TECHNICAL_REVIEWER": "principal-technical",
        "INDEPENDENT_STATISTICAL_REVIEWER": "principal-technical" if duplicate_independent_principal else "principal-statistical",
        "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR": "principal-isolation",
        "BENCHMARK_OPERATOR": "principal-operator",
        "RIGHTS_AUTHORIZER": "principal-rights",
        "SPEND_AUTHORIZER": "principal-spend",
        "OWNER_APPROVER": "principal-owner",
        "CI_ATTESTOR": "principal-ci",
    }
    signers = {
        role: TrustedSigner(key_id=f"key-{role.lower()}", principal_id=principal,
                            public_key=Ed25519PrivateKey.from_private_bytes(
                                bytes.fromhex(_sha(f"secret-{role}"))
                            ).public_key().public_bytes(
                                encoding=serialization.Encoding.Raw,
                                format=serialization.PublicFormat.Raw,
                            ),
                            permitted_roles=frozenset({role}))
        for role, principal in roles.items()
    }
    policy = TrustPolicy(signers={signer.key_id: signer for signer in signers.values()},
                         independent_principal_exclusions=frozenset({
                             "principal-arm-author", "principal-arm-operator",
                         }))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    refs = []

    def artifact(name, evidence_type, subject_sha, conclusion, role, payload=None):
        signer = signers[role]
        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha(f"secret-{role}")))
        raw = sign_evidence({"schema_version": 1, "evidence_type": evidence_type,
                             "subject_sha": subject_sha, "conclusion": conclusion,
                             "issuer_role": role, "payload": payload or {}},
                            key_id=signer.key_id, principal_id=signer.principal_id,
                            private_key=private_key)
        path = f"{name}.json"
        (evidence_dir / path).write_bytes(raw)
        ref = EvidenceArtifactRef(path=path, artifact_sha256=hashlib.sha256(raw).hexdigest())
        refs.append(ref)
        return ref

    ci = []
    for name in (
        "pytest (ubuntu-latest, 3.11)", "pytest (ubuntu-latest, 3.12)",
        "pytest (ubuntu-latest, 3.13)", "pytest (windows-latest, 3.12)",
    ):
        ref = artifact(name, "CI_JOB", source, "SUCCESS", "CI_ATTESTOR",
                       {"job_name": name, "conclusion": "success", "head_sha": source})
        ci.append(CIJob(name=name, conclusion="success", head_sha=source,
                        evidence_artifact_sha256=ref.artifact_sha256))
    browser_ref = artifact("dashboard-browser", "CI_JOB", source, "SUCCESS", "CI_ATTESTOR",
                           {"job_name": "dashboard-browser", "conclusion": "success", "head_sha": source})

    def pin(name, kind, subject, conclusion, role, payload=None):
        ref = artifact(name, kind, subject, conclusion, role, payload)
        return VerificationPin(artifact_sha256=ref.artifact_sha256, subject_sha=subject,
                               conclusion=conclusion, reviewer_role=role)

    rights_ref = artifact("rights-register", "RIGHTS_REGISTER", manifest.manifest_sha256,
                          "ACCEPTED", "RIGHTS_AUTHORIZER",
                          {"register_sha256": manifest.rights_register_sha256})
    spend_ref = artifact("spend-authorization", "SPEND_AUTHORIZATION", manifest.manifest_sha256,
                         "ACCEPTED", "SPEND_AUTHORIZER",
                         {"authorization_sha256": manifest.spend_authorization_sha256})
    operator_acceptance_ref = artifact("operator-acceptance", "OPERATOR_ACCEPTANCE",
                                       manifest.manifest_sha256, "ACCEPTED", "BENCHMARK_OPERATOR",
                                       {"acceptance_sha256": manifest.operator_acceptance_sha256})
    owner_ref = artifact("owner-approval", "OWNER_APPROVAL", manifest.manifest_sha256,
                         "ACCEPTED", "OWNER_APPROVER",
                         {"approval_sha256": manifest.owner_approval_sha256})
    assert all(ref.artifact_sha256 for ref in (rights_ref, spend_ref, operator_acceptance_ref, owner_ref))

    evidence = GateEvidence(
        source_sha=source,
        full_flag_hash=manifest.full_flag_hash,
        campaign_spec_sha256=manifest.campaign_spec_sha256,
        rpdi13_e2e=pin("integrated-e2e", "RPDI13_E2E", source, "PASS", "INTEGRATED_E2E_VERIFIER"),
        rpdi13_accessibility=pin("screen-reader", "RPDI13_ACCESSIBILITY", source, "PASS",
                                 "SCREEN_READER_REVIEWER"),
        ci_jobs=tuple(ci),
        browser_ci=CIJob(name="dashboard-browser", conclusion="success", head_sha=source,
                         evidence_artifact_sha256=browser_ref.artifact_sha256),
        technical_review=pin("technical-review", "TECHNICAL_REVIEW", source, "PASS",
                             "INDEPENDENT_TECHNICAL_REVIEWER"),
        statistical_review=pin("statistical-review", "STATISTICAL_REVIEW", manifest.manifest_sha256,
                               "PASS", "INDEPENDENT_STATISTICAL_REVIEWER"),
        evaluator_isolation=pin("evaluator-isolation", "EVALUATOR_ISOLATION",
                                hashlib.sha256(canonical_bytes(manifest.evaluator)).hexdigest(),
                                "PASS", "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR"),
        rights_register_sha256=manifest.rights_register_sha256,
        spend_authorization_sha256=manifest.spend_authorization_sha256,
        operator_acceptance_sha256=manifest.operator_acceptance_sha256,
        owner_approval_sha256=manifest.owner_approval_sha256,
        independent_operator=pin("independent-operator", "INDEPENDENT_OPERATOR",
                                 manifest.manifest_sha256, "ACCEPTED", "BENCHMARK_OPERATOR"),
        evidence_artifacts=tuple(refs),
    )
    return evidence, policy


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


def test_arm_controls_are_derived_from_pinned_values_and_every_difference_is_disclosed():
    raw = _raw_manifest()
    raw["arms"][1]["model_version"] = "quietly-changed-model"
    with pytest.raises(PreregError, match="not bound to the arm configurations"):
        freeze_manifest(raw)

    raw = _raw_manifest()
    role_roster = next(item for item in raw["control_comparisons"] if item["control_key"] == "role_roster")
    role_roster["difference_disclosure"] = ""
    with pytest.raises(PreregError, match="requires a disclosure"):
        freeze_manifest(raw)

    raw = _raw_manifest()
    raw["control_comparisons"].pop()
    with pytest.raises(PreregError, match="at least 11 items"):
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


def test_run_gate_requires_exact_head_e2e_ci_review_and_independent_attestations(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    evidence, policy = _valid_evidence(manifest, tmp_path / "bundle")

    def validate(candidate=evidence, *, trust=policy):
        return validate_run_gate(manifest, candidate, evidence_root=str(tmp_path / "bundle"),
                                 trust_policy=trust)

    validate()
    tampered = manifest.model_copy(update={"task_pins": (manifest.task_pins[0],)})
    with pytest.raises(PreregError, match="content has changed"):
        validate_run_gate(tampered, evidence, evidence_root=str(tmp_path / "bundle"), trust_policy=policy)
    ci_hashes = {item.evidence_artifact_sha256 for item in evidence.ci_jobs}
    incomplete = evidence.model_copy(update={
        "ci_jobs": (),
        "evidence_artifacts": tuple(ref for ref in evidence.evidence_artifacts
                                    if ref.artifact_sha256 not in ci_hashes),
    })
    with pytest.raises(PreregError, match="matrix is incomplete"):
        validate(incomplete)
    wrong_head = evidence.model_copy(update={
        "rpdi13_e2e": VerificationPin(
            artifact_sha256=_sha("integrated-e2e"), subject_sha=_sha("old-source"),
            conclusion="PASS", reviewer_role="INTEGRATED_E2E_VERIFIER",
        ),
    })
    with pytest.raises(PreregError, match="E2E"):
        validate(wrong_head)
    failed_e2e = evidence.model_copy(update={
        "rpdi13_e2e": VerificationPin(
            artifact_sha256=_sha("failed-e2e"), subject_sha=manifest.source_sha,
            conclusion="ACCEPTED", reviewer_role="INTEGRATED_E2E_VERIFIER",
        ),
    })
    with pytest.raises(PreregError, match="E2E did not pass"):
        validate(failed_e2e)
    isolated_wrong_evaluator = evidence.model_copy(update={
        "evaluator_isolation": VerificationPin(
            artifact_sha256=_sha("isolation"), subject_sha=_sha("old-evaluator"),
            conclusion="PASS", reviewer_role="INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR",
        ),
    })
    with pytest.raises(PreregError, match="different evaluator"):
        validate(isolated_wrong_evaluator)
    no_screenreader = evidence.model_copy(update={
        "rpdi13_accessibility": VerificationPin(
            artifact_sha256=_sha("no-screen-reader"), subject_sha=manifest.source_sha,
            conclusion="ACCEPTED", reviewer_role="SCREEN_READER_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="manual accessibility"):
        validate(no_screenreader)
    non_independent_review = evidence.model_copy(update={
        "technical_review": VerificationPin(
            artifact_sha256=_sha("review"), subject_sha=manifest.source_sha,
            conclusion="PASS", reviewer_role="SCREEN_READER_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="technical review must be independent"):
        validate(non_independent_review)
    no_statistical_review = evidence.model_copy(update={
        "statistical_review": VerificationPin(
            artifact_sha256=_sha("statistical-review"), subject_sha=manifest.manifest_sha256,
            conclusion="ACCEPTED", reviewer_role="INDEPENDENT_STATISTICAL_REVIEWER",
        ),
    })
    with pytest.raises(PreregError, match="statistical review"):
        validate(no_statistical_review)

    technical_key = next(key for key, value in policy.signers.items()
                         if "INDEPENDENT_TECHNICAL_REVIEWER" in value.permitted_roles)
    untrusted = TrustPolicy(signers={key: value for key, value in policy.signers.items()
                                     if key != technical_key},
                            independent_principal_exclusions=policy.independent_principal_exclusions)
    with pytest.raises(PreregError, match="unknown or revoked"):
        validate(trust=untrusted)

    incomplete_exclusions = TrustPolicy(
        signers=policy.signers,
        independent_principal_exclusions=frozenset({"principal-arm-author"}),
    )
    with pytest.raises(PreregError, match="does not exclude every frozen"):
        validate(trust=incomplete_exclusions)

    first_job = evidence.ci_jobs[0].model_copy(update={"evidence_artifact_sha256": _sha("forged-ci")})
    forged_ci = evidence.model_copy(update={"ci_jobs": (first_job, *evidence.ci_jobs[1:])})
    with pytest.raises(PreregError, match="no signed result artifact"):
        validate(forged_ci)

    statistical_ref = next(item for item in evidence.evidence_artifacts
                           if item.artifact_sha256 == evidence.statistical_review.artifact_sha256)
    (tmp_path / "bundle" / statistical_ref.path).write_text("tampered")
    with pytest.raises(PreregError, match="artifact bytes do not match"):
        validate()


def test_independent_roles_require_distinct_trusted_principals(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    evidence, policy = _valid_evidence(manifest, tmp_path / "bundle",
                                       duplicate_independent_principal=True)
    with pytest.raises(PreregError, match="signers must use distinct principals"):
        validate_run_gate(manifest, evidence, evidence_root=str(tmp_path / "bundle"), trust_policy=policy)
