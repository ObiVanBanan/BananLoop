import hashlib
import json
import os
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bananloop.contracts import FeatureFlagsV2, V2_FULL_FEATURES
from benchmarks.v02_full.evidence import (
    EvidenceArtifactRef, EvidenceError, SignedEvidence, TrustPolicy, TrustedSigner,
    load_trust_policy, read_content_artifact, sign_evidence, verify_protected_directory,
)
from benchmarks.v02_full.accounting import (
    AccountingResult, ResourceUsage, make_receipt, validate_accounting_chain,
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
    blind_label_assignment_digest,
    create_blind_label_assignment,
    validate_run_gate,
    validate_track_pair,
    verify_frozen_manifest,
)
from benchmarks.v02_full.report import (
    ActionReceiptAttestation, BlindedPairScore, BlindedScorePackage, ReportError, aggregate_blinded_scores,
    pair_score_digest, report_digest, score_package_digest, score_payload_digest,
)
from benchmarks.v02_full.execution import (
    RunStartAuthorization, RunStartError, TaskMaterial, assign_tasks_after_run_authorization,
    authorize_run_start, _validate_assignment_signer,
    gate_evidence_sha256, track_pair_sha256, verify_run_start_authorization,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_blind_label_assignment_is_randomized_and_canonically_committed():
    assignment = create_blind_label_assignment()
    assert assignment.randomization_method == "CSPRNG_ONE_BIT_V1"
    assert {assignment.bananloop_arm_label, assignment.codex_direct_arm_label} == {"A", "B"}
    assert assignment.assignment_sha256 == blind_label_assignment_digest(
        assignment.bananloop_arm_label, assignment.codex_direct_arm_label,
    )


def _raw_manifest(track="OPTIMIZATION", *, task_id="task-opt", suffix="opt"):
    caps = {
        "agent_calls": 20, "evaluator_calls": 12, "experiments": 8,
        "retries": 4, "input_tokens": 500_000, "output_tokens": 100_000,
        "cached_tokens": 100_000, "wall_seconds": 7200, "spend_microusd": 5_000_000,
    }
    arm_common = {
        "provider": "provider-pin", "model": "model-pin", "model_version": "model-version-pin",
        "reasoning_settings_sha256": _sha("reasoning"), "tool_policy_sha256": _sha("tools"),
        "worker_bundle_sha256": _sha(f"worker-bundle-{suffix}"),
        "worker_memory_bytes": 128 * 1024 * 1024,
        "worker_process_limit": 32,
        "caps": caps,
    }
    raw = {
        "schema_version": 1,
        "track": track,
        "protocol_id": f"rpdi14-{suffix}",
        "protocol_version": 1,
        "source_sha": _sha("full-source"),
        "approved_dev_information_sha256": _sha("approved-dev-information"),
        "full_feature_flags": list(V2_FULL_FEATURES),
        "full_flag_hash": FeatureFlagsV2(profile="FULL").flag_hash(),
        "campaign_spec_sha256": _sha(f"spec-{suffix}"),
        "blinding_assignment_sha256": blind_label_assignment_digest("A", "B"),
        "task_pins": [{"task_id": f"{task_id}-{index}",
                       "blinded_pair_id_sha256": _sha(f"blind-pair-{task_id}-{index}"),
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
                      "bundle_sha256": _sha(f"bundle-{suffix}"),
                      "rubric_sha256": _sha(f"rubric-{suffix}"),
                      "validation_set_sha256": _sha(f"validation-{suffix}"),
                      "holdout_set_sha256": _sha(f"holdout-{suffix}"),
                      "scorer_entrypoint": ("/evaluator/score",),
                      "scorer_timeout_seconds": 120,
                      "scorer_memory_bytes": 536870912,
                      "scorer_process_limit": 8,
                      "scorer_runtime_sha256": _sha(f"scorer-runtime-{suffix}"),
                      "scorer_sandbox_sha256": _sha(f"scorer-bwrap-{suffix}"),
                      "output_anonymizer_sha256": _sha(f"output-anonymizer-{suffix}")},
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
        "worker_bundle": (full["worker_bundle_sha256"], direct["worker_bundle_sha256"]),
        "worker_memory": (full["worker_memory_bytes"], direct["worker_memory_bytes"]),
        "worker_processes": (full["worker_process_limit"], direct["worker_process_limit"]),
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
        "approved_dev_information": (raw["approved_dev_information_sha256"],
                                     raw["approved_dev_information_sha256"]),
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
    if track == "OPTIMIZATION":
        raw["exploration_anchor"] = {
            "campaign_id": "verified-exploration-campaign",
            "campaign_spec_sha256": _sha("selected-exploration-spec"),
            "source_sha": raw["source_sha"],
            "full_flag_hash": raw["full_flag_hash"],
            "verification_gate_receipt_sha256": _sha("exploration-gate-receipt"),
            "approved_evaluator_protocol_sha256": raw["evaluator"]["protocol_sha256"],
            "approved_evaluator_bundle_sha256": raw["evaluator"]["bundle_sha256"],
        }
    else:
        raw["exploration_anchor"] = None
    return raw


def _valid_evidence(manifest, evidence_dir, *, duplicate_independent_principal=False):
    source = manifest.source_sha
    roles = {
        "INTEGRATED_E2E_VERIFIER": "principal-e2e",
        "SCREEN_READER_REVIEWER": "principal-accessibility",
        "INDEPENDENT_TECHNICAL_REVIEWER": "principal-technical",
        "INDEPENDENT_STATISTICAL_REVIEWER": "principal-technical" if duplicate_independent_principal else "principal-statistical",
        "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR": "principal-isolation",
        "EXPLORATION_GATE_VERIFIER": "principal-exploration-gate",
        "BENCHMARK_OPERATOR": "principal-operator",
        "INDEPENDENT_OPERATOR": "principal-ledger-reviewer",
        "INDEPENDENT_SCORER": "principal-scorer",
        "INDEPENDENT_ANONYMIZER": "principal-anonymizer",
        "RUN_START_AUTHORIZER": "principal-run-authorizer",
        "TASK_ASSIGNMENT_AUTHORIZER": "principal-task-assignment-authorizer",
        "ACTION_METER_ATTESTOR": "principal-action-meter",
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
        path = f"{manifest.track.lower()}-{manifest.protocol_id}/{name}.json"
        (evidence_dir / f"{manifest.track.lower()}-{manifest.protocol_id}").mkdir(parents=True, exist_ok=True)
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
    blinding_assignment_ref = artifact(
        "blinding-assignment", "BLINDING_ASSIGNMENT", manifest.blinding_assignment_sha256,
        "ACCEPTED", "BENCHMARK_OPERATOR",
        {"assignment_sha256": manifest.blinding_assignment_sha256,
         "bananloop_arm_label": "A", "codex_direct_arm_label": "B",
         "randomization_method": "CSPRNG_ONE_BIT_V1"},
    )
    owner_ref = artifact("owner-approval", "OWNER_APPROVAL", manifest.manifest_sha256,
                         "ACCEPTED", "OWNER_APPROVER",
                         {"approval_sha256": manifest.owner_approval_sha256})
    assert all(ref.artifact_sha256 for ref in (rights_ref, spend_ref, operator_acceptance_ref, owner_ref))
    exploration_verification = None
    if manifest.exploration_anchor is not None:
        anchor_sha = hashlib.sha256(canonical_bytes(manifest.exploration_anchor)).hexdigest()
        exploration_verification = pin("exploration-verification", "EXPLORATION_VERIFICATION",
                                       anchor_sha, "PASS", "EXPLORATION_GATE_VERIFIER")

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
        blinding_assignment=VerificationPin(
            artifact_sha256=blinding_assignment_ref.artifact_sha256,
            subject_sha=manifest.blinding_assignment_sha256,
            conclusion="ACCEPTED", reviewer_role="BENCHMARK_OPERATOR",
        ),
        exploration_verification=exploration_verification,
        rights_register_sha256=manifest.rights_register_sha256,
        spend_authorization_sha256=manifest.spend_authorization_sha256,
        operator_acceptance_sha256=manifest.operator_acceptance_sha256,
        owner_approval_sha256=manifest.owner_approval_sha256,
        independent_operator=pin("independent-operator", "INDEPENDENT_OPERATOR",
                                 manifest.manifest_sha256, "ACCEPTED", "BENCHMARK_OPERATOR"),
        evidence_artifacts=tuple(refs),
    )
    return evidence, policy


def _write_trust_policy(path, policy):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    value = {
        "schema_version": 1,
        "independent_principal_exclusions": sorted(policy.independent_principal_exclusions),
        "signers": [
            {"key_id": signer.key_id, "principal_id": signer.principal_id,
             "public_key_ed25519": signer.public_key.hex(),
             "permitted_roles": sorted(signer.permitted_roles), "revoked": signer.revoked}
            for signer in sorted(policy.signers.values(), key=lambda item: item.key_id)
        ],
    }
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o600)
    return path


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


def test_manifest_freeze_rejects_duplicate_blinded_pair_ids():
    raw = _raw_manifest()
    raw["task_pins"][1]["blinded_pair_id_sha256"] = raw["task_pins"][0]["blinded_pair_id_sha256"]
    with pytest.raises(PreregError, match="blind pair IDs must be unique"):
        freeze_manifest(raw)


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
    raw["approved_dev_information_sha256"] = _sha("different-dev-information")
    with pytest.raises(PreregError, match="approved_dev_information is not bound"):
        freeze_manifest(raw)

    raw = _raw_manifest()
    role_roster = next(item for item in raw["control_comparisons"] if item["control_key"] == "role_roster")
    role_roster["difference_disclosure"] = ""
    with pytest.raises(PreregError, match="requires a disclosure"):
        freeze_manifest(raw)

    raw = _raw_manifest()
    raw["control_comparisons"].pop()
    with pytest.raises(PreregError, match="at least 15 items"):
        freeze_manifest(raw)


def test_optimization_requires_full_exploration_handoff_and_exact_evaluator_assets():
    raw = _raw_manifest()
    raw["exploration_anchor"] = None
    with pytest.raises(PreregError, match="requires a pinned verified Full Exploration"):
        freeze_manifest(raw)
    raw = _raw_manifest()
    raw["exploration_anchor"]["approved_evaluator_bundle_sha256"] = _sha("different-bundle")
    with pytest.raises(PreregError, match="assets differ from the verified Exploration handoff"):
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


@pytest.mark.skipif(os.name != "posix", reason="protected run-start trust root currently requires POSIX openat")
def test_run_start_authorization_requires_both_track_gates_and_signs_exact_inputs(tmp_path):
    optimization = freeze_manifest(_raw_manifest("OPTIMIZATION", suffix="opt"))
    exploration = freeze_manifest(_raw_manifest("EXPLORATION", task_id="task-explore", suffix="explore"))
    evidence_root = tmp_path / "evidence"
    optimization_gate, policy = _valid_evidence(optimization, evidence_root)
    exploration_gate, _ = _valid_evidence(exploration, evidence_root)
    policy_path = _write_trust_policy(tmp_path / "protected" / "trust-policy.json", policy)
    authorizer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-RUN_START_AUTHORIZER")))

    authorization = authorize_run_start(
        optimization, exploration, optimization_gate, exploration_gate,
        evidence_root=str(evidence_root), trust_policy_path=str(policy_path),
        authorization_key_id="key-run_start_authorizer",
        authorization_private_key=authorizer,
    )
    verify_run_start_authorization(
        authorization, optimization=optimization, exploration=exploration,
        optimization_evidence=optimization_gate, exploration_evidence=exploration_gate,
        trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
    )
    assert authorization.document.payload["optimization_manifest_sha256"] == optimization.manifest_sha256
    assert authorization.document.payload["exploration_manifest_sha256"] == exploration.manifest_sha256

    bad_optimization_gate = optimization_gate.model_copy(update={
        "technical_review": VerificationPin(
            artifact_sha256=_sha("missing-review"), subject_sha=optimization.source_sha,
            conclusion="PASS", reviewer_role="INDEPENDENT_TECHNICAL_REVIEWER",
        ),
    })
    with pytest.raises(RunStartError, match="TECHNICAL_REVIEW"):
        authorize_run_start(
            optimization, exploration, bad_optimization_gate, exploration_gate,
            evidence_root=str(evidence_root), trust_policy_path=str(policy_path),
            authorization_key_id="key-run_start_authorizer",
            authorization_private_key=authorizer,
        )
    with pytest.raises(RunStartError, match="Optimization and Exploration"):
        verify_run_start_authorization(
            authorization, optimization=optimization, exploration=optimization,
            optimization_evidence=optimization_gate, exploration_evidence=exploration_gate,
            trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
        )


@pytest.mark.skipif(os.name != "posix", reason="protected run-start trust root currently requires POSIX openat")
@pytest.mark.parametrize("authorizer_principal", ["principal-arm-author", "principal-technical"])
def test_run_start_verifier_rejects_author_and_reviewer_signers(tmp_path, authorizer_principal):
    optimization = freeze_manifest(_raw_manifest("OPTIMIZATION", suffix="opt"))
    exploration = freeze_manifest(_raw_manifest("EXPLORATION", task_id="task-explore", suffix="explore"))
    evidence_root = tmp_path / "evidence"
    optimization_gate, base_policy = _valid_evidence(optimization, evidence_root)
    exploration_gate, _ = _valid_evidence(exploration, evidence_root)
    original = base_policy.signers["key-run_start_authorizer"]
    altered = TrustedSigner(
        key_id=original.key_id, principal_id=authorizer_principal,
        public_key=original.public_key, permitted_roles=original.permitted_roles,
    )
    signers = dict(base_policy.signers)
    signers[original.key_id] = altered
    malicious_policy = TrustPolicy(signers=signers,
                                   independent_principal_exclusions=base_policy.independent_principal_exclusions)
    policy_path = _write_trust_policy(tmp_path / "protected" / "trust-policy.json", malicious_policy)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-RUN_START_AUTHORIZER")))
    pair_sha = track_pair_sha256(optimization, exploration)
    _, policy_sha = load_trust_policy(policy_path)
    payload = {
        "optimization_manifest_sha256": optimization.manifest_sha256,
        "exploration_manifest_sha256": exploration.manifest_sha256,
        "optimization_gate_evidence_sha256": gate_evidence_sha256(optimization_gate),
        "exploration_gate_evidence_sha256": gate_evidence_sha256(exploration_gate),
        "track_pair_sha256": pair_sha,
        "trust_policy_sha256": policy_sha,
    }
    raw = sign_evidence({
        "schema_version": 1, "evidence_type": "RUN_AUTHORIZATION",
        "subject_sha": pair_sha, "conclusion": "ACCEPTED",
        "issuer_role": "RUN_START_AUTHORIZER", "payload": payload,
    }, key_id=original.key_id, principal_id=authorizer_principal, private_key=private_key)
    authorization = RunStartAuthorization(
        document=SignedEvidence.model_validate_json(raw, strict=True), raw_bytes=raw,
        artifact_sha256=hashlib.sha256(raw).hexdigest(), pair_sha256=pair_sha,
        trust_policy_sha256=policy_sha,
    )
    with pytest.raises(RunStartError, match="run-start authorizer"):
        verify_run_start_authorization(
            authorization, optimization=optimization, exploration=exploration,
            optimization_evidence=optimization_gate, exploration_evidence=exploration_gate,
            trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
        )


@pytest.mark.skipif(os.name != "posix", reason="protected run-start trust root currently requires POSIX openat")
def test_trust_policy_loader_rejects_symlink_ancestor(tmp_path):
    signer = TrustedSigner(
        key_id="key", principal_id="principal",
        public_key=Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw),
        permitted_roles=frozenset({"RUN_START_AUTHORIZER"}),
    )
    policy = TrustPolicy(signers={"key": signer}, independent_principal_exclusions=frozenset({"excluded"}))
    protected = _write_trust_policy(tmp_path / "protected" / "trust-policy.json", policy)
    alias = tmp_path / "alias"
    alias.symlink_to(protected.parent, target_is_directory=True)
    with pytest.raises(EvidenceError):
        load_trust_policy(alias / protected.name)
    with pytest.raises(EvidenceError):
        verify_protected_directory(alias)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL/reparse-point contract")
def test_windows_acl_gate_accepts_service_private_root_and_rejects_world_write(tmp_path):
    root = tmp_path / "protected"
    root.mkdir()
    verify_protected_directory(root)

    policy = TrustPolicy(
        signers={"key": TrustedSigner(
            key_id="key", principal_id="principal",
            public_key=Ed25519PrivateKey.generate().public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
            ),
            permitted_roles=frozenset({"RUN_START_AUTHORIZER"}),
        )},
        independent_principal_exclusions=frozenset({"excluded"}),
    )
    policy_path = _write_trust_policy(root / "trust-policy.json", policy)
    loaded, _ = load_trust_policy(policy_path)
    assert loaded.signers["key"].principal_id == "principal"

    subprocess.run(
        ["icacls", str(policy_path), "/grant", "*S-1-1-0:(M)"],
        check=True, capture_output=True, text=True,
    )
    with pytest.raises(EvidenceError, match="writable by an untrusted principal"):
        load_trust_policy(policy_path)

    artifact = root / "evidence.json"
    artifact_bytes = b"bounded public evidence"
    artifact.write_bytes(artifact_bytes)
    artifact_ref = EvidenceArtifactRef(
        path=artifact.name, artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )
    assert read_content_artifact(
        root=root, reference=artifact_ref, expected_sha256=artifact_ref.artifact_sha256,
        max_bytes=len(artifact_bytes),
    ) == artifact_bytes
    with pytest.raises(EvidenceError, match="byte limit"):
        read_content_artifact(
            root=root, reference=artifact_ref, expected_sha256=artifact_ref.artifact_sha256,
            max_bytes=len(artifact_bytes) - 1,
        )
    subprocess.run(
        ["icacls", str(artifact), "/grant", "*S-1-1-0:(M)"],
        check=True, capture_output=True, text=True,
    )
    with pytest.raises(EvidenceError, match="writable by an untrusted principal"):
        read_content_artifact(
            root=root, reference=artifact_ref, expected_sha256=artifact_ref.artifact_sha256,
        )

    subprocess.run(
        ["icacls", str(root), "/grant", "*S-1-1-0:(M)"],
        check=True, capture_output=True, text=True,
    )
    with pytest.raises(EvidenceError, match="writable by an untrusted principal"):
        verify_protected_directory(root)

    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Windows runner does not permit creating a test symlink")
    with pytest.raises(EvidenceError, match="reparse point"):
        verify_protected_directory(alias)


@pytest.mark.skipif(os.name != "posix", reason="protected run-start trust root currently requires POSIX openat")
def test_task_material_is_loaded_and_assigned_only_after_auth_and_exact_pin_checks(tmp_path):
    def frozen(track, task_id, suffix):
        raw = _raw_manifest(track, task_id=task_id, suffix=suffix)
        materials = {}
        for pin in raw["task_pins"]:
            payload = f"{track}-case-{pin['task_id']}".encode()
            rights = f"rights-for-{track}-{pin['task_id']}".encode()
            pin["task_data_sha256"] = hashlib.sha256(payload).hexdigest()
            pin["rights_record_sha256"] = hashlib.sha256(rights).hexdigest()
            materials[pin["task_id"]] = TaskMaterial(
                task_payload=payload, rights_record=rights,
                initial_tree_sha256=pin["initial_tree_sha256"],
            )
        return freeze_manifest(raw), materials

    optimization, opt_materials = frozen("OPTIMIZATION", "task-opt", "opt")
    exploration, exp_materials = frozen("EXPLORATION", "task-explore", "explore")
    evidence_root = tmp_path / "evidence"
    optimization_gate, policy = _valid_evidence(optimization, evidence_root)
    exploration_gate, _ = _valid_evidence(exploration, evidence_root)
    policy_path = _write_trust_policy(tmp_path / "protected" / "trust-policy.json", policy)
    authorizer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-RUN_START_AUTHORIZER")))
    authorization = authorize_run_start(
        optimization, exploration, optimization_gate, exploration_gate,
        evidence_root=str(evidence_root), trust_policy_path=str(policy_path),
        authorization_key_id="key-run_start_authorizer", authorization_private_key=authorizer,
    )
    loaded_tracks = []
    assigned = []

    def loader(manifest):
        loaded_tracks.append(manifest.track)
        return opt_materials if manifest.track == "OPTIMIZATION" else exp_materials

    result = assign_tasks_after_run_authorization(
        authorization, optimization, exploration, optimization_gate, exploration_gate,
        trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
        assignment_key_id="key-task_assignment_authorizer",
        assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))), material_loader=loader,
        assignment_hook=lambda plan: assigned.append(plan) or len(plan.optimization.tasks),
    )
    assert result == len(optimization.task_pins)
    assert loaded_tracks == ["OPTIMIZATION", "EXPLORATION"]
    assert len(assigned) == 1
    assert assigned[0].authorization_sha256 == authorization.artifact_sha256
    assert {item.task_id for item in assigned[0].optimization.tasks} == set(opt_materials)
    assert {item.task_id for item in assigned[0].exploration.tasks} == set(exp_materials)

    review_pin = optimization_gate.technical_review
    review_ref = next(item for item in optimization_gate.evidence_artifacts
                      if item.artifact_sha256 == review_pin.artifact_sha256)
    review_path = evidence_root / review_ref.path
    original_review = review_path.read_bytes()
    review_path.write_bytes(b"changed after authorization")
    blocked_loads = []
    blocked_assignments = []
    with pytest.raises(RunStartError, match="gate evidence no longer verifies"):
        assign_tasks_after_run_authorization(
            authorization, optimization, exploration, optimization_gate, exploration_gate,
            trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
            assignment_key_id="key-task_assignment_authorizer",
            assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))),
            material_loader=lambda manifest: blocked_loads.append(manifest.track) or {},
            assignment_hook=lambda plan: blocked_assignments.append(plan),
        )
    assert blocked_loads == blocked_assignments == []
    review_path.write_bytes(original_review)

    rejected_loads = []
    rejected_assignments = []
    changed_gate = exploration_gate.model_copy(update={"full_flag_hash": _sha("changed-flags")})
    with pytest.raises(RunStartError, match="different manifests or gate evidence"):
        assign_tasks_after_run_authorization(
            authorization, optimization, exploration, optimization_gate, changed_gate,
            trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
            assignment_key_id="key-task_assignment_authorizer",
            assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))),
            material_loader=lambda manifest: rejected_loads.append(manifest.track) or {},
            assignment_hook=lambda plan: rejected_assignments.append(plan),
        )
    assert rejected_loads == rejected_assignments == []

    mismatched_materials = dict(opt_materials)
    first_task = optimization.task_pins[0].task_id
    mismatched_materials[first_task] = TaskMaterial(
        task_payload=b"tampered", rights_record=b"bad-rights",
        initial_tree_sha256=optimization.task_pins[0].initial_tree_sha256,
    )
    loader_calls = []
    with pytest.raises(RunStartError, match="do not match the frozen task digest"):
        assign_tasks_after_run_authorization(
            authorization, optimization, exploration, optimization_gate, exploration_gate,
            trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
            assignment_key_id="key-task_assignment_authorizer",
            assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))),
            material_loader=lambda manifest: loader_calls.append(manifest.track) or (
                mismatched_materials if manifest.track == "OPTIMIZATION" else exp_materials),
            assignment_hook=lambda plan: rejected_assignments.append(plan),
        )
    assert loader_calls == ["OPTIMIZATION"]
    assert rejected_assignments == []

    for bad_material, message in (
        (TaskMaterial(
            task_payload=opt_materials[first_task].task_payload,
            rights_record=b"wrong-rights", initial_tree_sha256=optimization.task_pins[0].initial_tree_sha256,
        ), "rights record"),
        (TaskMaterial(
            task_payload=opt_materials[first_task].task_payload,
            rights_record=opt_materials[first_task].rights_record, initial_tree_sha256=_sha("wrong-tree"),
        ), "initial tree"),
    ):
        rejected = dict(opt_materials)
        rejected[first_task] = bad_material
        loader_calls.clear()
        with pytest.raises(RunStartError, match=message):
            assign_tasks_after_run_authorization(
                authorization, optimization, exploration, optimization_gate, exploration_gate,
                trust_policy_path=str(policy_path), evidence_root=str(evidence_root),
                assignment_key_id="key-task_assignment_authorizer",
                assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))),
                material_loader=lambda manifest: loader_calls.append(manifest.track) or (
                    rejected if manifest.track == "OPTIMIZATION" else exp_materials),
                assignment_hook=lambda plan: rejected_assignments.append(plan),
            )
        assert loader_calls == ["OPTIMIZATION"]
        assert rejected_assignments == []


def test_assignment_authority_rejects_multipurpose_signer(tmp_path):
    from dataclasses import replace

    manifest = freeze_manifest(_raw_manifest())
    _, policy = _valid_evidence(manifest, tmp_path / "evidence")
    signer = policy.signers["key-task_assignment_authorizer"]
    multipurpose = replace(signer, permitted_roles=frozenset({
        "TASK_ASSIGNMENT_AUTHORIZER", "OWNER_APPROVER",
    }))
    with pytest.raises(EvidenceError, match="exclusively for assignment"):
        _validate_assignment_signer(multipurpose, policy, manifest)


def test_run_gate_requires_exact_head_e2e_ci_review_and_independent_attestations(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    evidence, policy = _valid_evidence(manifest, tmp_path / "bundle")

    def validate(candidate=evidence, *, trust=policy):
        return validate_run_gate(manifest, candidate, evidence_root=str(tmp_path / "bundle"),
                                 trust_policy=trust)

    validate()
    missing_exploration = evidence.model_copy(update={"exploration_verification": None})
    with pytest.raises(PreregError, match="requires verified Full Exploration"):
        validate(missing_exploration)
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


def _receipt_chain(pair_id, arm_label, *, unknown_spend=False):
    receipt = make_receipt(
        pair_id=pair_id, arm_label=arm_label, sequence=1,
        action_id=_sha(f"action-{pair_id}-{arm_label}"), action_kind="MODEL_CALL",
        actor_id_digest=_sha(f"actor-{arm_label}"),
        usage=ResourceUsage(
            agent_calls=1, evaluator_calls=1, experiments=0, retries=0,
            input_tokens=10, output_tokens=5, cached_tokens=0,
            wall_seconds=1, spend_microusd=None if unknown_spend else 2,
        ).model_dump(mode="json"), previous_receipt_sha256=None,
    )
    return (receipt,)


def _report_package(manifest, evidence_root, *, unknown_pair_id=None,
                    forged_eligible_pair_id=None, failed_pair_id=None,
                    missing_head_pair_id=None, meter_is_ledger_operator=False,
                    settlement_state_pair_id=None, settlement_state="SEALED",
                    wrong_arm_configuration_pair_id=None):
    pair_scores = []
    arm_configuration_sha256_a = hashlib.sha256(canonical_bytes(manifest.arms[0])).hexdigest()
    arm_configuration_sha256_b = hashlib.sha256(canonical_bytes(manifest.arms[1])).hexdigest()
    for index, task in enumerate(manifest.task_pins):
        pair_id = task.blinded_pair_id_sha256
        receipts_a = _receipt_chain(pair_id, "A", unknown_spend=pair_id == unknown_pair_id)
        receipts_b = _receipt_chain(pair_id, "B")
        action_evidence_a = tuple({
            "receipt_sha256": receipt.receipt_sha256,
            "evidence_ref": _write_report_attestation(
                evidence_root, name=f"meter-{pair_id}-A-{receipt.sequence}",
                evidence_type="ACTION_RECEIPT", subject_sha=receipt.receipt_sha256,
                role="ACTION_METER_ATTESTOR", principal="principal-action-meter",
                payload={"pair_id_sha256": pair_id, "arm_label": "A",
                         "sequence": str(receipt.sequence), "action_id_sha256": receipt.action_id,
                         "arm_configuration_sha256": (
                             "f" * 64 if pair_id == wrong_arm_configuration_pair_id
                             else arm_configuration_sha256_a
                         ),
                         "usage_sha256": hashlib.sha256(canonical_bytes(receipt.usage)).hexdigest(),
                         "settlement_state": (settlement_state if pair_id == settlement_state_pair_id
                                               else "SEALED")},
            ).model_dump(),
        } for receipt in receipts_a)
        action_evidence_b = tuple({
            "receipt_sha256": receipt.receipt_sha256,
            "evidence_ref": _write_report_attestation(
                evidence_root, name=f"meter-{pair_id}-B-{receipt.sequence}",
                evidence_type="ACTION_RECEIPT", subject_sha=receipt.receipt_sha256,
                role="ACTION_METER_ATTESTOR", principal="principal-action-meter",
                payload={"pair_id_sha256": pair_id, "arm_label": "B",
                         "sequence": str(receipt.sequence), "action_id_sha256": receipt.action_id,
                         "arm_configuration_sha256": arm_configuration_sha256_b,
                         "usage_sha256": hashlib.sha256(canonical_bytes(receipt.usage)).hexdigest(),
                         "settlement_state": (settlement_state if pair_id == settlement_state_pair_id
                                               else "SEALED")},
            ).model_dump(),
        } for receipt in receipts_b)
        pair_a = validate_accounting_chain(
            receipts_a, pair_id=pair_id, arm_label="A", caps=manifest.arms[0].caps,
            expected_final_receipt_sha256=receipts_a[-1].receipt_sha256,
        )
        pair_b = validate_accounting_chain(
            receipts_b, pair_id=pair_id, arm_label="B", caps=manifest.arms[1].caps,
            expected_final_receipt_sha256=receipts_b[-1].receipt_sha256,
        )
        if pair_id == forged_eligible_pair_id:
            pair_a = pair_a.model_copy(update={
                "totals": {**pair_a.totals, "spend_microusd": None},
                "unknown_dimensions": ("spend_microusd",),
            })
        ledger_principal = "principal-action-meter" if meter_is_ledger_operator else "principal-ledger-reviewer"
        ledger_role_args = ({"signing_role": "ACTION_METER_ATTESTOR",
                             "key_id": "key-action_meter_attestor",
                             "key_secret": "secret-ACTION_METER_ATTESTOR"}
                            if meter_is_ledger_operator else {})
        ledger_a = None if pair_id == missing_head_pair_id else _write_report_attestation(
            evidence_root, name=f"ledger-{pair_id}-A", evidence_type="RESOURCE_LEDGER_HEAD",
            subject_sha=manifest.manifest_sha256, role="INDEPENDENT_OPERATOR",
            principal=ledger_principal, **ledger_role_args,
            payload={"pair_id_sha256": pair_id, "arm_label": "A",
                     "action_count": str(len(receipts_a)),
                     "final_receipt_sha256": receipts_a[-1].receipt_sha256},
        )
        ledger_b = None if pair_id == missing_head_pair_id else _write_report_attestation(
            evidence_root, name=f"ledger-{pair_id}-B", evidence_type="RESOURCE_LEDGER_HEAD",
            subject_sha=manifest.manifest_sha256, role="INDEPENDENT_OPERATOR",
            principal=ledger_principal, **ledger_role_args,
            payload={"pair_id_sha256": pair_id, "arm_label": "B",
                     "action_count": str(len(receipts_b)),
                     "final_receipt_sha256": receipts_b[-1].receipt_sha256},
        )
        failed = pair_id == failed_pair_id
        raw_output_a = None if failed else f"raw arm A from provider-pin for {pair_id}\n".encode()
        raw_output_b = None if failed else f"raw arm B from model-pin for {pair_id}\n".encode()
        output_a = None if failed else f"sanitized candidate A {index}\n".encode()
        output_b = None if failed else f"sanitized candidate B {index}\n".encode()
        if output_a is not None:
            (evidence_root / f"output-{pair_id}-A.bin").write_bytes(output_a)
            (evidence_root / f"output-{pair_id}-B.bin").write_bytes(output_b)
            (evidence_root / f"raw-output-{pair_id}-A.bin").write_bytes(raw_output_a)
            (evidence_root / f"raw-output-{pair_id}-B.bin").write_bytes(raw_output_b)
        output_a_sha = hashlib.sha256(output_a).hexdigest() if output_a is not None else None
        output_b_sha = hashlib.sha256(output_b).hexdigest() if output_b is not None else None
        raw_output_a_sha = hashlib.sha256(raw_output_a).hexdigest() if raw_output_a is not None else None
        raw_output_b_sha = hashlib.sha256(raw_output_b).hexdigest() if raw_output_b is not None else None
        anonymization_a = anonymization_b = None
        if not failed:
            anonymizer_payload_a = {
                "pair_id_sha256": pair_id, "arm_label": "A",
                "raw_output_sha256": raw_output_a_sha,
                "sanitized_output_sha256": output_a_sha,
                "anonymizer_sha256": manifest.evaluator.output_anonymizer_sha256,
            }
            anonymizer_payload_b = {
                "pair_id_sha256": pair_id, "arm_label": "B",
                "raw_output_sha256": raw_output_b_sha,
                "sanitized_output_sha256": output_b_sha,
                "anonymizer_sha256": manifest.evaluator.output_anonymizer_sha256,
            }
            anonymization_a = _write_report_attestation(
                evidence_root, name=f"anonymize-{pair_id}-A", evidence_type="OUTPUT_ANONYMIZATION",
                subject_sha=raw_output_a_sha, role="INDEPENDENT_ANONYMIZER",
                principal="principal-anonymizer", payload=anonymizer_payload_a,
            )
            anonymization_b = _write_report_attestation(
                evidence_root, name=f"anonymize-{pair_id}-B", evidence_type="OUTPUT_ANONYMIZATION",
                subject_sha=raw_output_b_sha, role="INDEPENDENT_ANONYMIZER",
                principal="principal-anonymizer", payload=anonymizer_payload_b,
            )
        raw_pair = {
            "pair_id_sha256": pair_id, "status": "FAILED" if failed else "SCORED",
            "arm_a_score": None if failed else 2.0 + index / 100,
            "arm_b_score": None if failed else 1.0,
            "arm_a_output_sha256": output_a_sha,
            "arm_b_output_sha256": output_b_sha,
            "arm_a_output_ref": (EvidenceArtifactRef(
                path=f"output-{pair_id}-A.bin", artifact_sha256=output_a_sha,
            ).model_dump() if output_a_sha else None),
            "arm_b_output_ref": (EvidenceArtifactRef(
                path=f"output-{pair_id}-B.bin", artifact_sha256=output_b_sha,
            ).model_dump() if output_b_sha else None),
            "arm_a_raw_output_sha256": raw_output_a_sha,
            "arm_b_raw_output_sha256": raw_output_b_sha,
            "arm_a_raw_output_ref": (EvidenceArtifactRef(
                path=f"raw-output-{pair_id}-A.bin", artifact_sha256=raw_output_a_sha,
            ).model_dump() if raw_output_a_sha else None),
            "arm_b_raw_output_ref": (EvidenceArtifactRef(
                path=f"raw-output-{pair_id}-B.bin", artifact_sha256=raw_output_b_sha,
            ).model_dump() if raw_output_b_sha else None),
            "arm_a_anonymization_attestation": (anonymization_a.model_dump()
                                                if anonymization_a else None),
            "arm_b_anonymization_attestation": (anonymization_b.model_dump()
                                                if anonymization_b else None),
            "arm_a_receipts": tuple(item.model_dump(mode="json") for item in receipts_a),
            "arm_b_receipts": tuple(item.model_dump(mode="json") for item in receipts_b),
            "arm_a_action_attestations": action_evidence_a,
            "arm_b_action_attestations": action_evidence_b,
            "arm_a_ledger_attestation": ledger_a.model_dump() if ledger_a else None,
            "arm_b_ledger_attestation": ledger_b.model_dump() if ledger_b else None,
            "arm_a_accounting": pair_a.model_dump(),
            "arm_b_accounting": pair_b.model_dump(),
            "reason": "recorded arm failure" if failed else None,
        }
        pair_scores.append(BlindedPairScore(**raw_pair, pair_result_sha256=pair_score_digest(raw_pair)))
    pair_scores.sort(key=lambda item: item.pair_id_sha256)
    raw_package = {
        "schema_version": 1, "track": manifest.track,
        "manifest_sha256": manifest.manifest_sha256,
        "evaluator_pin_sha256": hashlib.sha256(canonical_bytes(manifest.evaluator)).hexdigest(),
        "pairs": tuple(item.model_dump() for item in pair_scores),
    }
    raw_package["score_payload_sha256"] = score_payload_digest(raw_package)
    return BlindedScorePackage(**raw_package, package_sha256=score_package_digest(raw_package))


def _write_report_attestation(root, *, name, evidence_type, subject_sha, role, principal,
                              payload, conclusion="ACCEPTED", signing_role=None, key_id=None,
                              key_secret=None):
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(_sha(key_secret or f"secret-{signing_role or role}")),
    )
    raw = sign_evidence({"schema_version": 1, "evidence_type": evidence_type,
                         "subject_sha": subject_sha, "conclusion": conclusion,
                         "issuer_role": role, "payload": payload},
                        key_id=key_id or f"key-{role.lower()}", principal_id=principal,
                        private_key=private_key)
    path = f"{name}.json"
    (root / path).write_bytes(raw)
    return EvidenceArtifactRef(path=path, artifact_sha256=hashlib.sha256(raw).hexdigest())


def _attest_score_package(root, manifest, package, *, scorer_extra=None):
    scorer_ref = _write_report_attestation(
        root, name="blinded-score", evidence_type="BLINDED_SCORE_PACKAGE",
        subject_sha=manifest.manifest_sha256, role="INDEPENDENT_SCORER",
        principal="principal-scorer",
        payload={"score_payload_sha256": package.score_payload_sha256,
                 "evaluator_pin_sha256": package.evaluator_pin_sha256,
                 **(scorer_extra or {})},
    )
    operator_ref = _write_report_attestation(
        root, name="unblinding", evidence_type="UNBLINDING",
        subject_sha=package.package_sha256, role="BENCHMARK_OPERATOR",
        principal="principal-operator",
        payload={"score_package_sha256": package.package_sha256,
                 "bananloop_arm_label": "A", "codex_direct_arm_label": "B",
                 "blinding_assignment_sha256": manifest.blinding_assignment_sha256},
    )
    return scorer_ref, operator_ref


def test_blind_score_aggregation_requires_complete_package_and_separate_signed_unblinding(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    assert report.decision == "BANANLOOP_SUPERIOR"
    assert report.usable_pairs == 12 and report.wins == 12
    assert report.losses == report.ties == 0
    assert report.report_sha256 == report_digest(report)
    assert report.gate_evidence_sha256 == hashlib.sha256(canonical_bytes(gate_evidence)).hexdigest()
    assert report.pair_results[0].pair_id_sha256 in {task.blinded_pair_id_sha256 for task in manifest.task_pins}


def test_blind_score_aggregation_rejects_post_run_mapping_change(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, _ = _attest_score_package(tmp_path, manifest, package)
    unblinding_ref = _write_report_attestation(
        tmp_path, name="changed-unblinding", evidence_type="UNBLINDING",
        subject_sha=package.package_sha256, role="BENCHMARK_OPERATOR",
        principal="principal-operator",
        payload={"score_package_sha256": package.package_sha256,
                 "bananloop_arm_label": "B", "codex_direct_arm_label": "A",
                 "blinding_assignment_sha256": manifest.blinding_assignment_sha256},
    )
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    with pytest.raises(ReportError, match="post-run unblinding differs from the pre-run"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=trust_policy,
        )

    incomplete_payload = _report_package(manifest, tmp_path).model_dump()
    incomplete_payload["pairs"] = incomplete_payload["pairs"][:-1]
    incomplete_payload.pop("package_sha256")
    incomplete_payload["score_payload_sha256"] = score_payload_digest(incomplete_payload)
    incomplete = BlindedScorePackage(
        **incomplete_payload,
        package_sha256=score_package_digest(incomplete_payload),
    )
    with pytest.raises(ReportError, match="preserve every preregistered pair"):
        aggregate_blinded_scores(
            manifest, incomplete, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=trust_policy,
        )


def test_unknown_accounting_pair_is_retained_but_not_scored_as_eligible(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    bad_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(manifest, tmp_path, unknown_pair_id=bad_pair_id)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    assert report.decision == "UNDERPOWERED"
    assert report.usable_pairs == 11
    excluded = next(item for item in report.pair_results if item.pair_id_sha256 == bad_pair_id)
    assert excluded.status == "INELIGIBLE_ACCOUNTING"
    assert excluded.accounting_status == "UNKNOWN"
    assert excluded.oriented_benefit_delta is None


def test_action_attestation_must_bind_the_frozen_arm_configuration(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    bad_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(
        manifest, tmp_path, wrong_arm_configuration_pair_id=bad_pair_id,
    )
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    excluded = next(item for item in report.pair_results if item.pair_id_sha256 == bad_pair_id)
    assert excluded.status == "INELIGIBLE_ACCOUNTING"
    assert report.usable_pairs == len(manifest.task_pins) - 1


@pytest.mark.parametrize("settlement_state", ["UNKNOWN", "OVER_BOUND"])
def test_nonsealed_meter_settlement_is_ineligible_even_when_usage_fits_caps(tmp_path, settlement_state):
    manifest = freeze_manifest(_raw_manifest())
    bad_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(
        manifest, tmp_path, settlement_state_pair_id=bad_pair_id,
        settlement_state=settlement_state,
    )
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    pair = next(item for item in report.pair_results if item.pair_id_sha256 == bad_pair_id)
    assert pair.status == "INELIGIBLE_ACCOUNTING"
    assert pair.accounting_status == "INELIGIBLE"
    assert report.usable_pairs == len(manifest.task_pins) - 1


def test_forged_eligible_accounting_summary_is_not_scored(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    bad_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(manifest, tmp_path, forged_eligible_pair_id=bad_pair_id)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    assert report.usable_pairs == 11
    assert next(item for item in report.pair_results if item.pair_id_sha256 == bad_pair_id).status == "INELIGIBLE_ACCOUNTING"


def test_broken_receipt_chain_is_not_scored(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    pair = package.pairs[0]
    second_receipt = make_receipt(
        pair_id=pair.pair_id_sha256, arm_label="A", sequence=2,
        action_id=_sha(f"action-2-{pair.pair_id_sha256}"), action_kind="MODEL_CALL",
        actor_id_digest=_sha("actor-A"),
        usage=ResourceUsage(
            agent_calls=1, evaluator_calls=1, experiments=0, retries=0,
            input_tokens=10, output_tokens=5, cached_tokens=0,
            wall_seconds=1, spend_microusd=2,
        ).model_dump(mode="json"),
        previous_receipt_sha256=_sha("wrong-parent"),
    )
    changed_pair = pair.model_copy(update={"arm_a_receipts": (*pair.arm_a_receipts, second_receipt)})
    changed_pair = changed_pair.model_copy(update={"pair_result_sha256": pair_score_digest(changed_pair)})
    changed_pairs = tuple(changed_pair if item.pair_id_sha256 == pair.pair_id_sha256 else item for item in package.pairs)
    changed_payload = package.model_dump(mode="json")
    changed_payload["pairs"] = [item.model_dump(mode="json") for item in changed_pairs]
    changed_payload.pop("package_sha256")
    changed_payload["score_payload_sha256"] = score_payload_digest(changed_payload)
    changed_payload["package_sha256"] = score_package_digest(changed_payload)
    package = BlindedScorePackage.model_validate_json(json.dumps(changed_payload), strict=True)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    assert report.usable_pairs == 11
    assert next(item for item in report.pair_results if item.pair_id_sha256 == pair.pair_id_sha256).status == "INELIGIBLE_ACCOUNTING"


def test_failed_pair_retains_verified_resource_accounting(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    failed_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(manifest, tmp_path, failed_pair_id=failed_pair_id)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    failed = next(item for item in report.pair_results if item.pair_id_sha256 == failed_pair_id)
    assert failed.status == "FAILED"
    assert failed.accounting_status == "ELIGIBLE"
    assert failed.bananloop_accounting is not None
    assert failed.codex_direct_accounting is not None


def test_missing_signed_ledger_head_makes_scored_pair_accounting_unknown(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    missing_head_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(manifest, tmp_path, missing_head_pair_id=missing_head_pair_id)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    pair = next(item for item in report.pair_results if item.pair_id_sha256 == missing_head_pair_id)
    assert pair.status == "INELIGIBLE_ACCOUNTING"
    assert pair.accounting_status == "UNKNOWN"
    assert report.usable_pairs == 11


def test_orphan_sealed_receipt_without_canonical_ledger_head_is_not_scored(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    pair = package.pairs[0]
    previous = pair.arm_a_receipts[-1]
    orphan = make_receipt(
        pair_id=pair.pair_id_sha256, arm_label="A", sequence=previous.sequence + 1,
        action_id=_sha(f"orphan-action-{pair.pair_id_sha256}"), action_kind="MODEL_CALL",
        actor_id_digest=_sha("actor-A"),
        usage=ResourceUsage(
            agent_calls=1, evaluator_calls=0, experiments=0, retries=0,
            input_tokens=1, output_tokens=1, cached_tokens=0,
            wall_seconds=1, spend_microusd=1,
        ).model_dump(mode="json"),
        previous_receipt_sha256=previous.receipt_sha256,
    )
    orphan_attestation = _write_report_attestation(
        tmp_path, name=f"orphan-{orphan.receipt_sha256}", evidence_type="ACTION_RECEIPT",
        subject_sha=orphan.receipt_sha256, role="ACTION_METER_ATTESTOR",
        principal="principal-action-meter",
        payload={"pair_id_sha256": pair.pair_id_sha256, "arm_label": "A",
                 "sequence": str(orphan.sequence), "action_id_sha256": orphan.action_id,
                 "arm_configuration_sha256": hashlib.sha256(
                     canonical_bytes(manifest.arms[0])).hexdigest(),
                 "usage_sha256": hashlib.sha256(canonical_bytes(orphan.usage)).hexdigest(),
                 "settlement_state": "SEALED"},
    )
    changed_pair = pair.model_copy(update={
        "arm_a_receipts": (*pair.arm_a_receipts, orphan),
        "arm_a_action_attestations": (*pair.arm_a_action_attestations, ActionReceiptAttestation(
            receipt_sha256=orphan.receipt_sha256, evidence_ref=orphan_attestation,
        )),
    })
    changed_pair = changed_pair.model_copy(update={"pair_result_sha256": pair_score_digest(changed_pair)})
    changed_pairs = tuple(changed_pair if item.pair_id_sha256 == pair.pair_id_sha256 else item
                          for item in package.pairs)
    changed_payload = package.model_dump(mode="json")
    changed_payload["pairs"] = [item.model_dump(mode="json") for item in changed_pairs]
    changed_payload.pop("package_sha256")
    changed_payload["score_payload_sha256"] = score_payload_digest(changed_payload)
    changed_payload["package_sha256"] = score_package_digest(changed_payload)
    package = BlindedScorePackage.model_validate_json(json.dumps(changed_payload), strict=True)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path), trust_policy=trust_policy,
    )
    orphan_pair = next(item for item in report.pair_results if item.pair_id_sha256 == pair.pair_id_sha256)
    assert orphan_pair.status == "INELIGIBLE_ACCOUNTING"
    assert orphan_pair.accounting_status == "UNKNOWN"
    assert report.usable_pairs == len(manifest.task_pins) - 1


def test_unattested_action_receipt_makes_scored_pair_accounting_unknown(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    missing_attestation_pair_id = manifest.task_pins[0].blinded_pair_id_sha256
    package = _report_package(manifest, tmp_path)
    scoring_ref, _ = _attest_score_package(tmp_path, manifest, package)
    pair = next(item for item in package.pairs if item.pair_id_sha256 == missing_attestation_pair_id)
    changed_pair = pair.model_copy(update={"arm_a_action_attestations": ()})
    changed_pair = changed_pair.model_copy(update={"pair_result_sha256": pair_score_digest(changed_pair)})
    changed_pairs = tuple(changed_pair if item.pair_id_sha256 == pair.pair_id_sha256 else item
                          for item in package.pairs)
    changed_payload = package.model_dump(mode="json")
    changed_payload["pairs"] = [item.model_dump(mode="json") for item in changed_pairs]
    changed_payload.pop("package_sha256")
    changed_payload["score_payload_sha256"] = score_payload_digest(changed_payload)
    assert changed_payload["score_payload_sha256"] == package.score_payload_sha256
    changed_payload["package_sha256"] = score_package_digest(changed_payload)
    package = BlindedScorePackage.model_validate_json(json.dumps(changed_payload), strict=True)
    unblinding_ref = _write_report_attestation(
        tmp_path, name="unblinding-after-accounting-change", evidence_type="UNBLINDING",
        subject_sha=package.package_sha256, role="BENCHMARK_OPERATOR", principal="principal-operator",
        payload={"score_package_sha256": package.package_sha256,
                 "bananloop_arm_label": "A", "codex_direct_arm_label": "B",
                 "blinding_assignment_sha256": manifest.blinding_assignment_sha256},
    )
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    pair_result = next(item for item in report.pair_results
                       if item.pair_id_sha256 == missing_attestation_pair_id)
    assert pair_result.status == "INELIGIBLE_ACCOUNTING"
    assert pair_result.accounting_status == "UNKNOWN"


def test_action_meter_signature_must_bind_the_exact_receipt_bytes(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    pair = package.pairs[0]
    attestation = pair.arm_a_action_attestations[0]
    (tmp_path / attestation.evidence_ref.path).write_text("tampered", encoding="utf-8")
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=trust_policy,
    )
    assert report.usable_pairs == 11
    assert next(item for item in report.pair_results if item.pair_id_sha256 == pair.pair_id_sha256).status == "INELIGIBLE_ACCOUNTING"


def test_action_meter_cannot_attest_its_own_ledger_head(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path, meter_is_ledger_operator=True)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, policy = _valid_evidence(manifest, tmp_path)
    meter = policy.signers["key-action_meter_attestor"]
    signers = dict(policy.signers)
    signers[meter.key_id] = TrustedSigner(
        key_id=meter.key_id, principal_id=meter.principal_id, public_key=meter.public_key,
        permitted_roles=meter.permitted_roles | {"INDEPENDENT_OPERATOR"},
    )
    shared_meter_policy = TrustPolicy(
        signers=signers, independent_principal_exclusions=policy.independent_principal_exclusions,
    )
    report = aggregate_blinded_scores(
        manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
        unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
        trust_policy=shared_meter_policy,
    )
    assert report.usable_pairs == 0
    assert all(item.status == "INELIGIBLE_ACCOUNTING" for item in report.pair_results)


def test_scored_output_bytes_must_match_the_frozen_digest(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    first_ref = package.pairs[0].arm_a_output_ref
    (tmp_path / first_ref.path).write_bytes(b"changed after scorer sealed the package")
    with pytest.raises(ReportError, match="output anonymization verification failed"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=trust_policy,
        )


def test_arm_author_cannot_unblind(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, _ = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, policy = _valid_evidence(manifest, tmp_path)
    operator_key = "key-untrusted-author-operator"
    malicious_private = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(_sha("secret-untrusted-author-operator")),
    )
    signers = dict(policy.signers)
    signers[operator_key] = TrustedSigner(
        key_id=operator_key, principal_id="principal-arm-author",
        public_key=malicious_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        ), permitted_roles=frozenset({"BENCHMARK_OPERATOR"}),
    )
    author_policy = TrustPolicy(signers=signers,
                                independent_principal_exclusions=policy.independent_principal_exclusions)
    unblinding_ref = _write_report_attestation(
        tmp_path, name="author-unblinding", evidence_type="UNBLINDING",
        subject_sha=package.package_sha256, role="BENCHMARK_OPERATOR",
        principal="principal-arm-author", signing_role="BENCHMARK_OPERATOR",
            key_id=operator_key, key_secret="secret-untrusted-author-operator",
        payload={"score_package_sha256": package.package_sha256,
                 "bananloop_arm_label": "A", "codex_direct_arm_label": "B",
                 "blinding_assignment_sha256": manifest.blinding_assignment_sha256},
    )
    with pytest.raises(ReportError, match="arm author/operator cannot serve as the unblinding"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=author_policy,
        )


def test_scorer_and_unblinder_cannot_share_signing_key(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, _ = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, policy = _valid_evidence(manifest, tmp_path)
    signers = dict(policy.signers)
    operator_key = "key-unblinder-shared-key"
    shared_private = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(_sha("secret-INDEPENDENT_SCORER")),
    )
    signers[operator_key] = TrustedSigner(
        key_id=operator_key, principal_id="principal-operator",
        public_key=shared_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        ), permitted_roles=frozenset({"BENCHMARK_OPERATOR"}),
    )
    shared_key_policy = TrustPolicy(signers=signers,
                                    independent_principal_exclusions=policy.independent_principal_exclusions)
    unblinding_ref = _write_report_attestation(
        tmp_path, name="shared-key-unblinding", evidence_type="UNBLINDING",
        subject_sha=package.package_sha256, role="BENCHMARK_OPERATOR",
        principal="principal-operator", signing_role="INDEPENDENT_SCORER",
        key_id=operator_key,
        payload={"score_package_sha256": package.package_sha256,
                 "bananloop_arm_label": "A", "codex_direct_arm_label": "B",
                 "blinding_assignment_sha256": manifest.blinding_assignment_sha256},
    )
    with pytest.raises(ReportError, match="distinct signing keys"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=shared_key_policy,
        )


def test_scorer_attestation_cannot_include_unblinding_mapping(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, unblinding_ref = _attest_score_package(
        tmp_path, manifest, package, scorer_extra={"bananloop_arm_label": "A"},
    )
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    with pytest.raises(ReportError, match="must not contain unblinding"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=gate_evidence, scoring_attestation=scoring_ref,
            unblinding_attestation=unblinding_ref, evidence_root=str(tmp_path),
            trust_policy=trust_policy,
        )


def test_report_refuses_to_score_without_verified_rpdi13_accessibility_gate(tmp_path):
    manifest = freeze_manifest(_raw_manifest())
    package = _report_package(manifest, tmp_path)
    scoring_ref, unblinding_ref = _attest_score_package(tmp_path, manifest, package)
    gate_evidence, trust_policy = _valid_evidence(manifest, tmp_path)
    invalid_accessibility = gate_evidence.rpdi13_accessibility.model_copy(update={"conclusion": "ACCEPTED"})
    invalid_gate = gate_evidence.model_copy(update={"rpdi13_accessibility": invalid_accessibility})
    with pytest.raises(ReportError, match="RPDI-13 manual accessibility sign-off is missing"):
        aggregate_blinded_scores(
            manifest, package, gate_evidence=invalid_gate,
            scoring_attestation=scoring_ref, unblinding_attestation=unblinding_ref,
            evidence_root=str(tmp_path), trust_policy=trust_policy,
        )
