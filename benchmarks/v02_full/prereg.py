"""Validate frozen v0.2 Full benchmark manifests and prerequisite evidence.

This module does not assign tasks, launch arms, or evaluate benchmark outputs.
Those actions require a separately reviewed runner and every gate in the RPDI-14
contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from statistics import NormalDist
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bananloop.contracts import FeatureFlagsV2, V2_FULL_FEATURES

from .evidence import (
    EvidenceArtifactRef, EvidenceError, SignedEvidence, TrustPolicy, verify_evidence_artifact,
)


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ResourceCaps(FrozenModel):
    agent_calls: PositiveInt
    evaluator_calls: PositiveInt
    experiments: PositiveInt
    retries: NonnegativeInt
    input_tokens: PositiveInt
    output_tokens: PositiveInt
    cached_tokens: NonnegativeInt
    wall_seconds: PositiveInt
    spend_microusd: PositiveInt


class TaskPin(FrozenModel):
    task_id: str = Field(min_length=1, max_length=200)
    blinded_pair_id_sha256: Digest
    task_data_sha256: Digest
    initial_tree_sha256: Digest
    rights_record_sha256: Digest


class IterationPolicy(FrozenModel):
    maximum_iterations: PositiveInt
    feedback_source: Literal["APPROVED_DEV_ONLY"]
    permits_repair: bool
    stopping_rule_sha256: Digest


class ArmConfig(FrozenModel):
    arm: Literal["BANANLOOP_V02_FULL", "CODEX_DIRECT"]
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    reasoning_settings_sha256: Digest
    tool_policy_sha256: Digest
    worker_bundle_sha256: Digest
    worker_memory_bytes: Annotated[int, Field(strict=True, ge=128 * 1024 * 1024,
                                                le=2 * 1024 * 1024 * 1024)]
    worker_process_limit: Annotated[int, Field(strict=True, ge=1, le=128)]
    iteration_policy: IterationPolicy
    role_roster_sha256: Digest | None = None
    caps: ResourceCaps

    @model_validator(mode="after")
    def roster_matches_arm(self) -> "ArmConfig":
        if self.arm == "BANANLOOP_V02_FULL" and self.role_roster_sha256 is None:
            raise ValueError("BananLoop Full requires a pinned role roster")
        if self.arm == "CODEX_DIRECT" and self.role_roster_sha256 is not None:
            raise ValueError("Codex Direct cannot claim a BananLoop role roster")
        return self


class EvaluatorPin(FrozenModel):
    evaluator_sha256: Digest
    protocol_sha256: Digest
    bundle_sha256: Digest
    rubric_sha256: Digest
    validation_set_sha256: Digest
    holdout_set_sha256: Digest
    scorer_entrypoint: tuple[str, ...] = Field(min_length=1, max_length=32)
    scorer_timeout_seconds: PositiveInt
    scorer_memory_bytes: Annotated[int, Field(strict=True, ge=128 * 1024 * 1024,
                                               le=2 * 1024 * 1024 * 1024)]
    scorer_process_limit: Annotated[int, Field(strict=True, ge=1, le=128)]
    scorer_runtime_sha256: Digest
    scorer_sandbox_sha256: Digest
    output_anonymizer_sha256: Digest


class ExplorationCampaignAnchor(FrozenModel):
    campaign_id: str = Field(min_length=1, max_length=200)
    campaign_spec_sha256: Digest
    source_sha: Digest
    full_flag_hash: Digest
    verification_gate_receipt_sha256: Digest
    approved_evaluator_protocol_sha256: Digest
    approved_evaluator_bundle_sha256: Digest


CONTROL_KEYS = frozenset({
    "provider", "model", "model_version", "reasoning_settings", "tool_policy",
    "worker_bundle", "worker_memory", "worker_processes", "maximum_iterations", "feedback_source", "permits_repair", "stopping_rule",
    "role_roster", "resource_caps", "approved_dev_information",
})


class ControlComparison(FrozenModel):
    control_key: Literal[
        "provider", "model", "model_version", "reasoning_settings", "tool_policy",
        "worker_bundle", "worker_memory", "worker_processes", "maximum_iterations", "feedback_source", "permits_repair", "stopping_rule",
        "role_roster", "resource_caps", "approved_dev_information",
    ]
    bananloop_value_sha256: Digest
    codex_direct_value_sha256: Digest
    status: Literal["MATCHED", "UNMATCHED"]
    difference_disclosure: str | None = None

    @model_validator(mode="after")
    def classification_is_true(self) -> "ControlComparison":
        equal = self.bananloop_value_sha256 == self.codex_direct_value_sha256
        if (self.status == "MATCHED") != equal:
            raise ValueError("control comparison status does not match the pinned arm values")
        if self.status == "UNMATCHED" and (self.difference_disclosure is None
                                             or not self.difference_disclosure.strip()):
            raise ValueError("every unmatched control requires a disclosure")
        if self.status == "MATCHED" and self.difference_disclosure is not None:
            raise ValueError("matched controls cannot carry an unmatched difference disclosure")
        return self


def _normal_critical(coverage_percent: int, sidedness: str) -> float:
    alpha = 1.0 - coverage_percent / 100.0
    tail_probability = 1.0 - alpha if sidedness == "ONE_SIDED" else 1.0 - alpha / 2.0
    return NormalDist().inv_cdf(tail_probability)


def _student_t_critical_approx(z: float, degrees_freedom: int) -> float:
    """Cornish-Fisher approximation, versioned as part of RPDI-14 prereg v1."""
    v = float(degrees_freedom)
    z2 = z * z
    return (z + (z * z2 + z) / (4 * v)
            + (5 * z * z2 * z2 + 16 * z * z2 + 3 * z) / (96 * v * v)
            + (3 * z * z2 * z2 * z2 + 19 * z * z2 * z2 + 17 * z * z2 - 15 * z)
            / (384 * v * v * v))


def required_pairs_for_power(*, power_percent: int, coverage_percent: int, sidedness: str,
                             assumed_effect: float, assumed_paired_sd: float) -> int:
    """Normal-approximation power calculation for a paired-t mean difference."""
    z_power = NormalDist().inv_cdf(power_percent / 100.0)
    z_critical = _normal_critical(coverage_percent, sidedness)
    target_power = power_percent / 100.0
    for pairs in range(2, 1_000_001):
        critical = _student_t_critical_approx(z_critical, pairs - 1)
        noncentrality = assumed_effect * math.sqrt(pairs) / assumed_paired_sd
        achieved = NormalDist().cdf(noncentrality - critical)
        if sidedness == "TWO_SIDED":
            achieved += NormalDist().cdf(-noncentrality - critical)
        if achieved >= target_power:
            return pairs
    raise ValueError("power target requires more than 1,000,000 pairs")


def required_pairs_for_precision(*, coverage_percent: int, sidedness: str,
                                 assumed_paired_sd: float, target_half_width: float) -> int:
    """Find the smallest pair count meeting the paired-t interval precision target."""
    z_critical = _normal_critical(coverage_percent, sidedness)
    for pairs in range(2, 1_000_001):
        critical = _student_t_critical_approx(z_critical, pairs - 1)
        if critical * assumed_paired_sd / math.sqrt(pairs) <= target_half_width:
            return pairs
    raise ValueError("precision target requires more than 1,000,000 pairs")


class InferencePlan(FrozenModel):
    estimand: str = Field(min_length=1, max_length=2000)
    primary_outcome: str = Field(min_length=1, max_length=300)
    direction: Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER"]
    paired_estimator: Literal["MEAN_DIFFERENCE", "MEDIAN_DIFFERENCE", "WIN_PROBABILITY"]
    interval_method: Literal["PAIRED_T", "PAIRED_BOOTSTRAP", "EXACT_SIGN", "WILCOXON"]
    coverage_percent: Annotated[int, Field(strict=True, gt=0, lt=100)]
    sidedness: Literal["ONE_SIDED", "TWO_SIDED"]
    minimum_usable_pairs: PositiveInt
    repeats_per_task: PositiveInt
    planned_pairs: PositiveInt
    sample_target_kind: Literal["POWER", "PRECISION"]
    power_target_percent: Annotated[int, Field(strict=True, gt=0, lt=100)] | None = None
    assumed_effect: float | None = None
    assumed_paired_sd: float | None = None
    target_interval_half_width: float | None = None
    required_pairs: PositiveInt
    missing_pair_rule: str = Field(min_length=1, max_length=1000)
    tie_rule: str = Field(min_length=1, max_length=1000)
    multiplicity_rule: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def sample_target_is_complete(self) -> "InferencePlan":
        if self.minimum_usable_pairs > self.planned_pairs:
            raise ValueError("minimum_usable_pairs cannot exceed planned_pairs")
        if self.paired_estimator != "MEAN_DIFFERENCE" or self.interval_method != "PAIRED_T":
            raise ValueError("v1 harness supports only mean paired differences with paired-t intervals")
        if self.sample_target_kind == "POWER":
            if self.power_target_percent is None or self.assumed_effect is None or self.assumed_paired_sd is None:
                raise ValueError("POWER target requires power, assumed effect, and paired SD")
            if self.assumed_effect <= 0 or self.assumed_paired_sd <= 0:
                raise ValueError("POWER assumptions must be positive")
            if self.target_interval_half_width is not None:
                raise ValueError("POWER target cannot include target_interval_half_width")
            calculated = required_pairs_for_power(
                power_percent=self.power_target_percent,
                coverage_percent=self.coverage_percent,
                sidedness=self.sidedness,
                assumed_effect=self.assumed_effect,
                assumed_paired_sd=self.assumed_paired_sd,
            )
        else:
            if self.target_interval_half_width is None or self.target_interval_half_width <= 0:
                raise ValueError("PRECISION target requires a positive interval half-width")
            if any(value is not None for value in (self.power_target_percent, self.assumed_effect)):
                raise ValueError("PRECISION target cannot include POWER assumptions")
            if self.assumed_paired_sd is None:
                raise ValueError("PRECISION target requires an assumed paired SD")
            calculated = required_pairs_for_precision(
                coverage_percent=self.coverage_percent,
                sidedness=self.sidedness,
                assumed_paired_sd=self.assumed_paired_sd,
                target_half_width=self.target_interval_half_width,
            )
        if self.required_pairs != calculated:
            raise ValueError(f"required_pairs must equal the frozen calculation result {calculated}")
        if self.minimum_usable_pairs < self.required_pairs:
            raise ValueError("minimum_usable_pairs cannot be below the required sample size")
        if self.planned_pairs < self.required_pairs:
            raise ValueError("planned_pairs do not meet the frozen power/precision target")
        return self


class TrackManifest(FrozenModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    protocol_id: str = Field(min_length=1, max_length=200)
    protocol_version: PositiveInt
    source_sha: Digest
    approved_dev_information_sha256: Digest
    full_feature_flags: tuple[str, ...]
    full_flag_hash: Digest
    campaign_spec_sha256: Digest
    blinding_assignment_sha256: Digest
    task_pins: tuple[TaskPin, ...] = Field(min_length=1)
    arms: tuple[ArmConfig, ArmConfig]
    common_caps: ResourceCaps
    evaluator: EvaluatorPin
    exploration_anchor: ExplorationCampaignAnchor | None = None
    deterministic_seed: str = Field(min_length=1, max_length=200)
    repeats_per_task: PositiveInt
    inference: InferencePlan
    control_comparisons: tuple[ControlComparison, ...] = Field(min_length=len(CONTROL_KEYS))
    rights_register_sha256: Digest
    spend_authorization_sha256: Digest
    operator_acceptance_sha256: Digest
    owner_approval_sha256: Digest
    arm_author_principal_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    arm_operator_principal_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    frozen_at: str = Field(min_length=1, max_length=100)
    manifest_sha256: Digest | None = None

    @model_validator(mode="after")
    def validate_frozen_identity(self) -> "TrackManifest":
        full_features = tuple(V2_FULL_FEATURES)
        if self.full_feature_flags != full_features:
            raise ValueError("full_feature_flags must exactly match the approved v0.2 Full feature set")
        if self.full_flag_hash != FeatureFlagsV2(profile="FULL").flag_hash():
            raise ValueError("full_flag_hash does not match the approved FULL profile")
        if tuple(arm.arm for arm in self.arms) != ("BANANLOOP_V02_FULL", "CODEX_DIRECT"):
            raise ValueError("arms must be ordered BANANLOOP_V02_FULL, CODEX_DIRECT")
        if len({task.task_id for task in self.task_pins}) != len(self.task_pins):
            raise ValueError("task_ids must be unique within a track")
        if len({task.blinded_pair_id_sha256 for task in self.task_pins}) != len(self.task_pins):
            raise ValueError("blind pair IDs must be unique within a track")
        if self.repeats_per_task != 1:
            raise ValueError("v1 sample-size calculations require one independent pair per task")
        expected_pairs = len(self.task_pins) * self.repeats_per_task
        if self.inference.repeats_per_task != self.repeats_per_task:
            raise ValueError("inference repeats_per_task must match the task design")
        if self.inference.planned_pairs != expected_pairs:
            raise ValueError("planned_pairs must equal task count times repeats_per_task")
        for arm in self.arms:
            for dimension in ResourceCaps.model_fields:
                if getattr(arm.caps, dimension) != getattr(self.common_caps, dimension):
                    raise ValueError(f"{arm.arm} caps must exactly match common cap for {dimension}")
        if self.evaluator.scorer_timeout_seconds > self.common_caps.wall_seconds:
            raise ValueError("scorer timeout must fit within the frozen wall-time cap")
        if self.arms[1].iteration_policy.maximum_iterations < 2:
            raise ValueError("Codex Direct must have a preregistered iterative policy")
        if self.track == "OPTIMIZATION":
            if self.exploration_anchor is None:
                raise ValueError("Optimization requires a pinned verified Full Exploration campaign")
            anchor = self.exploration_anchor
            if anchor.source_sha != self.source_sha or anchor.full_flag_hash != self.full_flag_hash:
                raise ValueError("Optimization Exploration anchor source/Full identity differs")
            if (anchor.approved_evaluator_protocol_sha256 != self.evaluator.protocol_sha256
                    or anchor.approved_evaluator_bundle_sha256 != self.evaluator.bundle_sha256):
                raise ValueError("Optimization evaluator protocol/assets differ from the verified Exploration handoff")
        elif self.exploration_anchor is not None:
            raise ValueError("the separate Exploration benchmark track cannot carry an Optimization handoff anchor")
        if {item.control_key for item in self.control_comparisons} != CONTROL_KEYS:
            raise ValueError("control comparisons must explicitly cover every exposed arm control")
        expected_controls = _arm_control_hashes(self.arms, self.approved_dev_information_sha256)
        for comparison in self.control_comparisons:
            expected_full, expected_direct = expected_controls[comparison.control_key]
            if (comparison.bananloop_value_sha256 != expected_full
                    or comparison.codex_direct_value_sha256 != expected_direct):
                raise ValueError(f"control comparison {comparison.control_key} is not bound to the arm configurations")
        return self


class CIJob(FrozenModel):
    name: str = Field(min_length=1, max_length=200)
    conclusion: Literal["success"]
    head_sha: Digest
    evidence_artifact_sha256: Digest


class VerificationPin(FrozenModel):
    artifact_sha256: Digest
    subject_sha: Digest
    conclusion: Literal["PASS", "ACCEPTED"]
    reviewer_role: Literal[
        "INDEPENDENT_TECHNICAL_REVIEWER",
        "INDEPENDENT_STATISTICAL_REVIEWER",
        "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR",
        "EXPLORATION_GATE_VERIFIER",
        "INTEGRATED_E2E_VERIFIER",
        "SCREEN_READER_REVIEWER",
        "BENCHMARK_OPERATOR",
        "INDEPENDENT_SCORER",
    ]


class BlindLabelAssignment(FrozenModel):
    schema_version: Literal[1] = 1
    bananloop_arm_label: Literal["A", "B"]
    codex_direct_arm_label: Literal["A", "B"]
    randomization_method: Literal["CSPRNG_ONE_BIT_V1"] = "CSPRNG_ONE_BIT_V1"
    assignment_sha256: Digest

    @model_validator(mode="after")
    def assignment_is_canonical(self) -> "BlindLabelAssignment":
        expected = blind_label_assignment_digest(
            self.bananloop_arm_label, self.codex_direct_arm_label,
        )
        if self.bananloop_arm_label == self.codex_direct_arm_label or self.assignment_sha256 != expected:
            raise ValueError("blind label assignment does not match its canonical digest")
        return self


class GateEvidence(FrozenModel):
    source_sha: Digest
    full_flag_hash: Digest
    campaign_spec_sha256: Digest
    rpdi13_e2e: VerificationPin
    rpdi13_accessibility: VerificationPin
    ci_jobs: tuple[CIJob, ...] = Field(min_length=1)
    browser_ci: CIJob
    technical_review: VerificationPin
    statistical_review: VerificationPin
    evaluator_isolation: VerificationPin
    blinding_assignment: VerificationPin
    exploration_verification: VerificationPin | None = None
    rights_register_sha256: Digest
    spend_authorization_sha256: Digest
    operator_acceptance_sha256: Digest
    owner_approval_sha256: Digest
    independent_operator: VerificationPin
    evidence_artifacts: tuple[EvidenceArtifactRef, ...] = Field(min_length=1, max_length=64)


REQUIRED_CI_JOBS = frozenset({
    "pytest (ubuntu-latest, 3.11)",
    "pytest (ubuntu-latest, 3.12)",
    "pytest (ubuntu-latest, 3.13)",
    "pytest (windows-latest, 3.12)",
})


class PreregError(ValueError):
    """The preregistration or its evidence cannot support a valid frozen run."""


def canonical_bytes(value: BaseModel | dict) -> bytes:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def blind_label_assignment_digest(bananloop_arm_label: str, codex_direct_arm_label: str) -> str:
    if (bananloop_arm_label not in {"A", "B"} or codex_direct_arm_label not in {"A", "B"}
            or bananloop_arm_label == codex_direct_arm_label):
        raise ValueError("blind assignment must map the two distinct labels A and B")
    return hashlib.sha256(canonical_bytes({
        "schema_version": 1,
        "bananloop_arm_label": bananloop_arm_label,
        "codex_direct_arm_label": codex_direct_arm_label,
    })).hexdigest()


def create_blind_label_assignment() -> BlindLabelAssignment:
    """Choose the Full arm's A/B label with one CSPRNG bit before freezing the manifests."""
    full_label = "A" if secrets.randbits(1) == 0 else "B"
    direct_label = "B" if full_label == "A" else "A"
    return BlindLabelAssignment(
        bananloop_arm_label=full_label, codex_direct_arm_label=direct_label,
        assignment_sha256=blind_label_assignment_digest(full_label, direct_label),
    )


def _arm_control_hashes(arms: tuple[ArmConfig, ArmConfig],
                        approved_dev_information_sha256: str) -> dict[str, tuple[str, str]]:
    full, direct = arms
    values = {
        "provider": (full.provider, direct.provider),
        "model": (full.model, direct.model),
        "model_version": (full.model_version, direct.model_version),
        "reasoning_settings": (full.reasoning_settings_sha256, direct.reasoning_settings_sha256),
        "tool_policy": (full.tool_policy_sha256, direct.tool_policy_sha256),
        "worker_bundle": (full.worker_bundle_sha256, direct.worker_bundle_sha256),
        "worker_memory": (full.worker_memory_bytes, direct.worker_memory_bytes),
        "worker_processes": (full.worker_process_limit, direct.worker_process_limit),
        "maximum_iterations": (full.iteration_policy.maximum_iterations,
                               direct.iteration_policy.maximum_iterations),
        "feedback_source": (full.iteration_policy.feedback_source,
                            direct.iteration_policy.feedback_source),
        "permits_repair": (full.iteration_policy.permits_repair,
                           direct.iteration_policy.permits_repair),
        "stopping_rule": (full.iteration_policy.stopping_rule_sha256,
                          direct.iteration_policy.stopping_rule_sha256),
        "role_roster": (full.role_roster_sha256 or "NO_ROLE_ROSTER",
                        direct.role_roster_sha256 or "NO_ROLE_ROSTER"),
        "resource_caps": (full.caps.model_dump(mode="json"), direct.caps.model_dump(mode="json")),
        "approved_dev_information": (approved_dev_information_sha256,
                                     approved_dev_information_sha256),
    }
    return {key: tuple(hashlib.sha256(canonical_bytes(value)).hexdigest() for value in pair)
            for key, pair in values.items()}


def manifest_digest(manifest: TrackManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def freeze_manifest(value: dict) -> TrackManifest:
    """Validate and return the immutable, content-addressed track manifest."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False)
        candidate = TrackManifest.model_validate_json(encoded, strict=True)
        expected = manifest_digest(candidate)
        if candidate.manifest_sha256 is not None and candidate.manifest_sha256 != expected:
            raise PreregError("manifest_sha256 does not match the canonical manifest")
        return candidate.model_copy(update={"manifest_sha256": expected})
    except PreregError:
        raise
    except Exception as exc:
        raise PreregError(str(exc)) from exc


def verify_frozen_manifest(manifest: TrackManifest, expected_sha256: str) -> None:
    """Reject any post-freeze change, including task, model, flag, or budget edits."""
    if manifest.manifest_sha256 != expected_sha256:
        raise PreregError("manifest identity differs from the frozen digest")
    if manifest_digest(manifest) != expected_sha256:
        raise PreregError("frozen manifest content has changed")


def validate_run_gate(
    manifest: TrackManifest,
    evidence: GateEvidence,
    *,
    evidence_root: str,
    trust_policy: TrustPolicy,
) -> BlindLabelAssignment:
    """Authenticate prerequisite artifacts; never assigns or runs a task."""
    if manifest.manifest_sha256 is None:
        raise PreregError("track manifest must be frozen before gate validation")
    verify_frozen_manifest(manifest, manifest.manifest_sha256)
    identity_pairs = (
        ("source_sha", manifest.source_sha, evidence.source_sha),
        ("full_flag_hash", manifest.full_flag_hash, evidence.full_flag_hash),
        ("campaign_spec_sha256", manifest.campaign_spec_sha256, evidence.campaign_spec_sha256),
        ("blinding_assignment_sha256", manifest.blinding_assignment_sha256,
         evidence.blinding_assignment.subject_sha),
        ("rights_register_sha256", manifest.rights_register_sha256, evidence.rights_register_sha256),
        ("spend_authorization_sha256", manifest.spend_authorization_sha256, evidence.spend_authorization_sha256),
        ("operator_acceptance_sha256", manifest.operator_acceptance_sha256, evidence.operator_acceptance_sha256),
        ("owner_approval_sha256", manifest.owner_approval_sha256, evidence.owner_approval_sha256),
    )
    for label, frozen, supplied in identity_pairs:
        if frozen != supplied:
            raise PreregError(f"gate evidence {label} differs from the frozen manifest")
    if evidence.rpdi13_e2e.subject_sha != manifest.source_sha:
        raise PreregError("RPDI-13 integrated E2E must verify the frozen source SHA")
    if evidence.rpdi13_e2e.conclusion != "PASS":
        raise PreregError("RPDI-13 integrated E2E did not pass")
    if evidence.rpdi13_e2e.reviewer_role != "INTEGRATED_E2E_VERIFIER":
        raise PreregError("RPDI-13 E2E pin must identify the integration verifier")
    if evidence.rpdi13_accessibility.subject_sha != manifest.source_sha:
        raise PreregError("RPDI-13 accessibility evidence is for a different source SHA")
    if evidence.rpdi13_accessibility.conclusion != "PASS":
        raise PreregError("RPDI-13 manual accessibility sign-off is missing")
    if evidence.rpdi13_accessibility.reviewer_role != "SCREEN_READER_REVIEWER":
        raise PreregError("RPDI-13 accessibility evidence must identify a screen-reader reviewer")
    if evidence.technical_review.subject_sha != manifest.source_sha:
        raise PreregError("technical review is for a different source SHA")
    if evidence.technical_review.conclusion != "PASS":
        raise PreregError("technical review did not pass")
    if evidence.technical_review.reviewer_role != "INDEPENDENT_TECHNICAL_REVIEWER":
        raise PreregError("technical review must be independent")
    if (evidence.statistical_review.reviewer_role != "INDEPENDENT_STATISTICAL_REVIEWER"
            or evidence.statistical_review.subject_sha != manifest.manifest_sha256
            or evidence.statistical_review.conclusion != "PASS"):
        raise PreregError("statistical review must independently approve this frozen manifest")
    evaluator_pin_sha = hashlib.sha256(canonical_bytes(manifest.evaluator)).hexdigest()
    if evidence.evaluator_isolation.subject_sha != evaluator_pin_sha:
        raise PreregError("evaluator isolation is for a different evaluator")
    if evidence.evaluator_isolation.conclusion != "PASS":
        raise PreregError("evaluator isolation was not verified")
    if evidence.evaluator_isolation.reviewer_role != "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR":
        raise PreregError("evaluator isolation review must be independent")
    if (evidence.blinding_assignment.reviewer_role != "BENCHMARK_OPERATOR"
            or evidence.blinding_assignment.conclusion != "ACCEPTED"):
        raise PreregError("blind arm-label assignment must be frozen by the benchmark operator before run start")
    if evidence.browser_ci.name != "dashboard-browser" or evidence.browser_ci.head_sha != manifest.source_sha:
        raise PreregError("browser CI is missing or for a different source SHA")
    if len({item.path for item in evidence.evidence_artifacts}) != len(evidence.evidence_artifacts):
        raise PreregError("signed evidence artifact paths must be unique")
    if len({item.artifact_sha256 for item in evidence.evidence_artifacts}) != len(evidence.evidence_artifacts):
        raise PreregError("signed evidence artifact digests must be unique")
    artifact_by_digest = {item.artifact_sha256: item for item in evidence.evidence_artifacts}
    used_artifacts: set[str] = set()

    def verify_pin(pin: VerificationPin, evidence_type: str):
        reference = artifact_by_digest.get(pin.artifact_sha256)
        if reference is None:
            raise PreregError(f"missing signed artifact for {evidence_type}")
        used_artifacts.add(reference.artifact_sha256)
        try:
            return verify_evidence_artifact(
                root=evidence_root, reference=reference, policy=trust_policy,
                evidence_type=evidence_type, subject_sha=pin.subject_sha,
                conclusion=pin.conclusion, issuer_role=pin.reviewer_role,
            ).document
        except EvidenceError as exc:
            raise PreregError(f"invalid {evidence_type} evidence: {exc}") from exc

    reviewer_documents = [
        verify_pin(evidence.rpdi13_e2e, "RPDI13_E2E"),
        verify_pin(evidence.rpdi13_accessibility, "RPDI13_ACCESSIBILITY"),
        verify_pin(evidence.technical_review, "TECHNICAL_REVIEW"),
        verify_pin(evidence.statistical_review, "STATISTICAL_REVIEW"),
        verify_pin(evidence.evaluator_isolation, "EVALUATOR_ISOLATION"),
        verify_pin(evidence.independent_operator, "INDEPENDENT_OPERATOR"),
    ]
    blinding_document = verify_pin(evidence.blinding_assignment, "BLINDING_ASSIGNMENT")
    expected_blinding_payload = {
        "assignment_sha256": manifest.blinding_assignment_sha256,
        "bananloop_arm_label": "A" if blinding_document.payload.get("bananloop_arm_label") == "A" else "B",
        "codex_direct_arm_label": "B" if blinding_document.payload.get("bananloop_arm_label") == "A" else "A",
        "randomization_method": "CSPRNG_ONE_BIT_V1",
    }
    if (set(blinding_document.payload) != set(expected_blinding_payload)
            or blinding_document.payload != expected_blinding_payload
            or blind_label_assignment_digest(
                blinding_document.payload["bananloop_arm_label"],
                blinding_document.payload["codex_direct_arm_label"],
            ) != manifest.blinding_assignment_sha256):
        raise PreregError("signed blind label assignment differs from its pre-run commitment")
    if manifest.track == "OPTIMIZATION":
        if evidence.exploration_verification is None or manifest.exploration_anchor is None:
            raise PreregError("Optimization run gate requires verified Full Exploration campaign evidence")
        anchor_sha = hashlib.sha256(canonical_bytes(manifest.exploration_anchor)).hexdigest()
        if evidence.exploration_verification.subject_sha != anchor_sha:
            raise PreregError("Exploration verification is for a different campaign/spec/evaluator handoff")
        reviewer_documents.append(verify_pin(evidence.exploration_verification,
                                             "EXPLORATION_VERIFICATION"))
    elif evidence.exploration_verification is not None:
        raise PreregError("separate Exploration benchmark runs cannot import an Optimization handoff receipt")
    distinct_principal_roles = {
        "INDEPENDENT_TECHNICAL_REVIEWER", "INDEPENDENT_STATISTICAL_REVIEWER",
        "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR", "EXPLORATION_GATE_VERIFIER",
        "BENCHMARK_OPERATOR",
    }
    independent_docs = [doc for doc in reviewer_documents if doc.issuer_role in distinct_principal_roles]
    principals = [doc.issuer_principal_id for doc in independent_docs]
    key_ids = [doc.issuer_key_id for doc in independent_docs]
    key_fingerprints = [hashlib.sha256(trust_policy.signers[key].public_key).hexdigest()
                        for key in key_ids]
    if (len(principals) != len(set(principals)) or len(key_ids) != len(set(key_ids))
            or len(key_fingerprints) != len(set(key_fingerprints))):
        raise PreregError("independent technical, statistical, isolation and operator signers must use distinct principals and keys")
    if set(principals) & trust_policy.independent_principal_exclusions:
        raise PreregError("an arm author/operator cannot act as an independent gate reviewer or operator")
    required_exclusions = set(manifest.arm_author_principal_ids) | set(manifest.arm_operator_principal_ids)
    if not required_exclusions <= trust_policy.independent_principal_exclusions:
        raise PreregError("trust policy does not exclude every frozen arm author and operator")

    pin_values = (
        ("RIGHTS_REGISTER", "RIGHTS_AUTHORIZER", "register_sha256", manifest.rights_register_sha256),
        ("SPEND_AUTHORIZATION", "SPEND_AUTHORIZER", "authorization_sha256", manifest.spend_authorization_sha256),
        ("OPERATOR_ACCEPTANCE", "BENCHMARK_OPERATOR", "acceptance_sha256", manifest.operator_acceptance_sha256),
        ("OWNER_APPROVAL", "OWNER_APPROVER", "approval_sha256", manifest.owner_approval_sha256),
    )
    docs_by_sha = {item.artifact_sha256: item for item in evidence.evidence_artifacts}
    signed_gate_documents: dict[str, SignedEvidence] = {}
    for evidence_type, role, payload_key, bound_sha in pin_values:
        matches = []
        for reference in evidence.evidence_artifacts:
            try:
                document = verify_evidence_artifact(
                    root=evidence_root, reference=reference, policy=trust_policy,
                    evidence_type=evidence_type, subject_sha=manifest.manifest_sha256,
                    conclusion="ACCEPTED", issuer_role=role,
                    expected_payload={payload_key: bound_sha},
                ).document
                matches.append((reference, document))
            except EvidenceError:
                continue
        if len(matches) != 1:
            raise PreregError(f"{evidence_type} must have exactly one authenticated matching artifact")
        used_artifacts.add(matches[0][0].artifact_sha256)
        signed_gate_documents[evidence_type] = matches[0][1]
    independent_operator_document = reviewer_documents[5]
    operator_acceptance_document = signed_gate_documents["OPERATOR_ACCEPTANCE"]
    if (operator_acceptance_document.issuer_principal_id != independent_operator_document.issuer_principal_id
            or operator_acceptance_document.issuer_key_id != independent_operator_document.issuer_key_id):
        raise PreregError("operator acceptance and independent-operator sign-off must come from the same trusted signer")

    for job in (*evidence.ci_jobs, evidence.browser_ci):
        reference = docs_by_sha.get(job.evidence_artifact_sha256)
        if reference is None:
            raise PreregError(f"CI job {job.name} has no signed result artifact")
        used_artifacts.add(reference.artifact_sha256)
        try:
            verify_evidence_artifact(
                root=evidence_root, reference=reference, policy=trust_policy,
                evidence_type="CI_JOB", subject_sha=manifest.source_sha,
                conclusion="SUCCESS", issuer_role="CI_ATTESTOR",
                expected_payload={"job_name": job.name, "conclusion": job.conclusion,
                                  "head_sha": job.head_sha},
            )
        except EvidenceError as exc:
            raise PreregError(f"CI job {job.name} evidence is invalid: {exc}") from exc
    if used_artifacts != set(artifact_by_digest):
        raise PreregError("evidence bundle contains unreferenced signed artifacts")
    required = REQUIRED_CI_JOBS
    jobs: dict[str, CIJob] = {}
    for job in evidence.ci_jobs:
        if job.name in jobs:
            raise PreregError(f"duplicate CI job {job.name}")
        jobs[job.name] = job
        if job.head_sha != manifest.source_sha:
            raise PreregError(f"CI job {job.name} is for a different source SHA")
    if not required.issubset(jobs):
        raise PreregError("exact-head CI matrix is incomplete")
    try:
        return BlindLabelAssignment.model_validate({
            **blinding_document.payload,
            "schema_version": 1,
        }, strict=True)
    except Exception as exc:
        raise PreregError("signed blind label assignment is malformed") from exc


def validate_track_pair(optimization: TrackManifest, exploration: TrackManifest) -> None:
    """Require disjoint task sets and independent outcomes/evaluators."""
    for manifest in (optimization, exploration):
        if manifest.manifest_sha256 is None:
            raise PreregError("both track manifests must be frozen before pair validation")
        verify_frozen_manifest(manifest, manifest.manifest_sha256)
    if optimization.track != "OPTIMIZATION" or exploration.track != "EXPLORATION":
        raise PreregError("track pair must contain Optimization and Exploration manifests")
    if optimization.protocol_id == exploration.protocol_id:
        raise PreregError("tracks require distinct protocol IDs")
    if optimization.source_sha != exploration.source_sha or optimization.full_flag_hash != exploration.full_flag_hash:
        raise PreregError("tracks must use the same frozen v0.2 Full source and feature-flag identity")
    if optimization.campaign_spec_sha256 == exploration.campaign_spec_sha256:
        raise PreregError("tracks require distinct campaign spec hashes")
    optimization_tasks = {task.task_id for task in optimization.task_pins}
    exploration_tasks = {task.task_id for task in exploration.task_pins}
    if optimization_tasks & exploration_tasks:
        raise PreregError("Optimization and Exploration task sets must be disjoint")
    if optimization.inference.primary_outcome == exploration.inference.primary_outcome:
        raise PreregError("tracks require distinct primary outcomes")
    if optimization.evaluator == exploration.evaluator:
        raise PreregError("tracks require separate evaluator/protocol/rubric/data pins")
    if (optimization.arm_author_principal_ids != exploration.arm_author_principal_ids
            or optimization.arm_operator_principal_ids != exploration.arm_operator_principal_ids):
        raise PreregError("paired tracks must bind the same excluded authors and operators")
    if optimization.blinding_assignment_sha256 != exploration.blinding_assignment_sha256:
        raise PreregError("paired tracks must bind the same pre-run blind arm-label assignment")

