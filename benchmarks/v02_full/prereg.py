"""Validate frozen v0.2 Full benchmark manifests and prerequisite evidence.

This module does not assign tasks, launch arms, or evaluate benchmark outputs.
Those actions require a separately reviewed runner and every gate in the RPDI-14
contract.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bananloop.contracts import FeatureFlagsV2, V2_FULL_FEATURES


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
    rubric_sha256: Digest
    validation_set_sha256: Digest
    holdout_set_sha256: Digest


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
    full_feature_flags: tuple[str, ...]
    full_flag_hash: Digest
    campaign_spec_sha256: Digest
    task_pins: tuple[TaskPin, ...] = Field(min_length=1)
    arms: tuple[ArmConfig, ArmConfig]
    common_caps: ResourceCaps
    evaluator: EvaluatorPin
    deterministic_seed: str = Field(min_length=1, max_length=200)
    repeats_per_task: PositiveInt
    inference: InferencePlan
    matched_controls: tuple[str, ...] = Field(min_length=1)
    unmatched_control_disclosures: tuple[str, ...]
    rights_register_sha256: Digest
    spend_authorization_sha256: Digest
    operator_acceptance_sha256: Digest
    owner_approval_sha256: Digest
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
        if self.arms[1].iteration_policy.maximum_iterations < 2:
            raise ValueError("Codex Direct must have a preregistered iterative policy")
        return self


class CIJob(FrozenModel):
    name: str = Field(min_length=1, max_length=200)
    conclusion: Literal["success"]
    head_sha: Digest


class VerificationPin(FrozenModel):
    artifact_sha256: Digest
    subject_sha: Digest
    conclusion: Literal["PASS", "ACCEPTED"]
    reviewer_role: Literal[
        "INDEPENDENT_TECHNICAL_REVIEWER",
        "INDEPENDENT_STATISTICAL_REVIEWER",
        "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR",
        "INTEGRATED_E2E_VERIFIER",
        "SCREEN_READER_REVIEWER",
    ]


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
    rights_register_sha256: Digest
    spend_authorization_sha256: Digest
    operator_acceptance_sha256: Digest
    owner_approval_sha256: Digest
    independent_operator: bool


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


def validate_run_gate(manifest: TrackManifest, evidence: GateEvidence) -> None:
    """Validate prerequisites only; this function never assigns or runs a task."""
    if manifest.manifest_sha256 is None:
        raise PreregError("track manifest must be frozen before gate validation")
    verify_frozen_manifest(manifest, manifest.manifest_sha256)
    identity_pairs = (
        ("source_sha", manifest.source_sha, evidence.source_sha),
        ("full_flag_hash", manifest.full_flag_hash, evidence.full_flag_hash),
        ("campaign_spec_sha256", manifest.campaign_spec_sha256, evidence.campaign_spec_sha256),
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
    if evidence.browser_ci.name != "dashboard-browser" or evidence.browser_ci.head_sha != manifest.source_sha:
        raise PreregError("browser CI is missing or for a different source SHA")
    if not evidence.independent_operator:
        raise PreregError("an independent benchmark operator must accept the frozen protocol")
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

