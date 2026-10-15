from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class CampaignStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED_INFRA = "PAUSED_INFRA"
    PAUSED_NEEDS_INPUT = "PAUSED_NEEDS_INPUT"
    GOAL_MET = "GOAL_MET"
    STOPPED = "STOPPED"


class StopReason(StrEnum):
    GOAL_MET = "GOAL_MET"
    FINAL_HOLDOUT_REJECTED = "FINAL_HOLDOUT_REJECTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    PLATEAU = "PLATEAU"
    NO_HYPOTHESIS = "NO_HYPOTHESIS"
    PAUSED_INFRA = "PAUSED_INFRA"
    PAUSED_NEEDS_INPUT = "PAUSED_NEEDS_INPUT"
    USER_STOPPED = "USER_STOPPED"
    ATTEMPT_LIMIT = "ATTEMPT_LIMIT"


class MetricSpec(BaseModel):
    name: str
    direction: Direction
    target: float
    min_improvement: float = Field(gt=0)
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "MetricSpec":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be <= maximum")
        return self


class GuardrailSpec(BaseModel):
    metric: str
    operator: Literal["<=", ">=", "<", ">", "=="]
    bound: float


class NonRegressionSpec(BaseModel):
    metric: str
    direction: Direction
    tolerance: float = Field(default=0.0, ge=0)


class PairedComparison(BaseModel):
    """Compare fixed case/repetition pairs, then count wins across case means."""
    case_count: int = Field(ge=1)
    repetitions: int = Field(default=1, ge=1)
    metric: str
    direction: Direction = Direction.MAXIMIZE
    min_wins: int = Field(ge=1)
    worst_case_tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    non_regression: list[NonRegressionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_wins(self):
        if self.min_wins > self.case_count:
            raise ValueError("min_wins cannot exceed case_count")
        return self


class BudgetSpec(BaseModel):
    max_evaluation_units: int | None = Field(default=None, ge=1)
    evaluation_units_per_call: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def work_budget(self):
        if (self.max_evaluation_units is None) != (self.evaluation_units_per_call is None):
            raise ValueError("max_evaluation_units and evaluation_units_per_call must be set together")
        return self

    max_experiments: int = Field(default=5, ge=1)
    max_attempts: int = Field(default=8, ge=1)
    max_agent_calls: int = Field(default=20, ge=1)
    max_evaluator_calls: int = Field(default=10, ge=1)
    deadline_seconds: int = Field(default=3600, ge=1)
    plateau_experiments: int = Field(default=3, ge=1)


class EvaluationAsset(BaseModel):
    path: str
    sha256: str


class EvaluationEvidence(BaseModel):
    """Provenance and readiness facts used to qualify scientific conclusions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    technical_readiness: Literal["READY", "FAILED"]
    trust_level: Literal["UNVERIFIED", "CALIBRATED", "INDEPENDENTLY_VALIDATED"]
    case_source: str | None = None
    label_source: str | None = None
    protocol_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    case_input_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime: dict[str, Any] = Field(default_factory=dict)
    runtime_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metric_tolerances: dict[str, float] = Field(default_factory=dict)
    readiness_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    verified_invariants: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    kind: Literal["codex", "command", "pi"] = "codex"
    command: list[str] = Field(default_factory=list)
    model: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    reasoning_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh"] | None = None
    provider: str | None = None
    roles: dict[str, "AgentRoleOverride"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def command_required(self) -> "AgentConfig":
        if self.kind == "command" and not self.command:
            raise ValueError("command agent requires command argv")
        flags = set(self.extra_args)
        if self.kind == "codex":
            if self.model is not None and (
                "-m" in flags or "--model" in flags
                or any(arg.startswith(("-m=", "--model=")) for arg in flags)
            ):
                raise ValueError("Codex model must be configured in one place; remove model overrides from extra_args")
            if (self.model is not None or self.reasoning_effort is not None) and (
                "--profile" in flags or any(arg.startswith("--profile=") for arg in flags)
            ):
                raise ValueError("Codex model/reasoning must not be shadowed by --profile in extra_args")
            if (self.model is not None or self.reasoning_effort is not None) and any(
                arg == "-c" and index + 1 < len(self.extra_args)
                and self.extra_args[index + 1].split("=", 1)[0] in {"model", "model_reasoning_effort"}
                for index, arg in enumerate(self.extra_args)
            ):
                raise ValueError("Codex model/reasoning must be configured in one place; remove conflicting -c overrides")
            if self.reasoning_effort is not None and any(
                arg == "-c" and index + 1 < len(self.extra_args)
                and self.extra_args[index + 1].startswith("model_reasoning_effort=")
                for index, arg in enumerate(self.extra_args)
            ):
                raise ValueError("Codex reasoning effort must be configured in one place; remove its -c override")
        if self.kind == "pi":
            if self.model is not None and ("--model" in flags or any(arg.startswith("--model=") for arg in flags)):
                raise ValueError("Pi model must be configured in one place; remove model overrides from extra_args")
            if self.provider is not None and ("--provider" in flags or any(arg.startswith("--provider=") for arg in flags)):
                raise ValueError("Pi provider must be configured in one place; remove provider overrides from extra_args")
            if self.reasoning_effort is not None and ("--thinking" in flags or any(arg.startswith("--thinking=") for arg in flags)):
                raise ValueError("Pi thinking level must be configured in one place; remove its override from extra_args")
        return self

    def for_role(self, role: str) -> "AgentConfig":
        override = self.roles.get(role)
        if override is None:
            return self.model_copy(update={"roles": {}})
        values = override.model_dump(exclude_unset=True, exclude_none=True)
        merged = self.model_dump(exclude={"roles"})
        merged.update(values)
        return AgentConfig.model_validate(merged)


class AgentRoleOverride(BaseModel):
    kind: Literal["codex", "command", "pi"] | None = None
    model: str | None = None
    reasoning_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh"] | None = None
    provider: str | None = None

    model_config = ConfigDict(extra="forbid")


class CampaignSpec(BaseModel):
    schema_version: int = 1
    goal: str
    source_repo: str
    base_commit: str
    project_context: str = ""
    evaluation_command: list[str]
    evaluator_timeout_seconds: int = Field(default=900, ge=1)
    validation_command: list[str] = Field(default_factory=list)
    validation_timeout_seconds: int | None = Field(default=None, ge=1)
    regression_command: list[str] = Field(default_factory=list)
    regression_timeout_seconds: int | None = Field(default=None, ge=1)
    holdout_command: list[str] = Field(default_factory=list)
    holdout_timeout_seconds: int | None = Field(default=None, ge=1)
    evaluation_assets: list[EvaluationAsset] = Field(default_factory=list)
    agent_hidden_paths: list[str] = Field(default_factory=list)
    agent_visible_metrics: list[str] = Field(default_factory=list)
    agent_visible_diagnostics: list[str] = Field(default_factory=list)
    paired_comparison: PairedComparison | None = None
    # Optional readiness metadata. None is omitted from approved_hash to keep
    # all campaigns created before signal sanity byte-for-byte compatible.
    signal_sanity_status: Literal["DIAGNOSTIC_ONLY", "UNAVAILABLE"] | None = None
    signal_sanity_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evaluation_evidence: EvaluationEvidence | None = None
    primary_metric: MetricSpec
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    non_regression: list[NonRegressionSpec] = Field(default_factory=list)
    mandatory_checks: list[str] = Field(default_factory=list)
    editable_scope: list[str] = Field(default_factory=lambda: ["**"])
    protected_paths: list[str] = Field(default_factory=list)
    forbidden_diff_patterns: list[str] = Field(default_factory=list)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @field_validator("forbidden_diff_patterns")
    @classmethod
    def valid_forbidden_diff_patterns(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid forbidden diff regex {pattern!r}: {exc}") from exc
        return value

    @field_validator("evaluation_command")
    @classmethod
    def evaluator_nonempty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("evaluation_command cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_staged_evaluation_budget(self) -> "CampaignSpec":
        if (self.signal_sanity_status is None) != (self.signal_sanity_receipt_sha256 is None):
            raise ValueError("signal sanity status and receipt digest must be set together")
        if self.validation_timeout_seconds is not None and not self.validation_command:
            raise ValueError("validation_timeout_seconds requires validation_command")
        if self.regression_timeout_seconds is not None and not self.regression_command:
            raise ValueError("regression_timeout_seconds requires regression_command")
        if self.holdout_timeout_seconds is not None and not self.holdout_command:
            raise ValueError("holdout_timeout_seconds requires holdout_command")
        minimum_calls = (
            1 + int(bool(self.validation_command)) + int(bool(self.regression_command))
            + int(bool(self.holdout_command))
        )
        if self.budget.max_evaluator_calls < minimum_calls:
            raise ValueError(
                "max_evaluator_calls is too small for baseline staged evaluation; "
                f"need at least {minimum_calls}"
            )
        if self.budget.max_evaluation_units is not None:
            measured_calls = 1 + int(bool(self.validation_command)) + int(bool(self.holdout_command))
            if self.budget.max_evaluation_units < measured_calls * self.budget.evaluation_units_per_call:
                raise ValueError("evaluation work budget too small for baseline staged evaluation")
        return self

    def approved_hash(self) -> str:
        data = self.model_dump(mode="json")
        if self.paired_comparison is None:
            data.pop("paired_comparison", None)
        if self.signal_sanity_status is None:
            data.pop("signal_sanity_status", None)
        if self.signal_sanity_receipt_sha256 is None:
            data.pop("signal_sanity_receipt_sha256", None)
        # Evidence metadata is additive and must not alter approved/action hashes.
        data.pop("evaluation_evidence", None)
        for key in ("max_evaluation_units", "evaluation_units_per_call"):
            if data["budget"][key] is None:
                data["budget"].pop(key)
        # Keep hashes compatible with v0.1 specs created before these fields existed.
        if not self.project_context:
            data.pop("project_context", None)
        if self.evaluator_timeout_seconds == 900:
            data.pop("evaluator_timeout_seconds", None)
        if not self.validation_command:
            data.pop("validation_command", None)
        if self.validation_timeout_seconds is None:
            data.pop("validation_timeout_seconds", None)
        # Empty regression fields were absent from older campaign documents.
        if not self.regression_command:
            data.pop("regression_command", None)
        if self.regression_timeout_seconds is None:
            data.pop("regression_timeout_seconds", None)
        if not self.holdout_command:
            data.pop("holdout_command", None)
        if self.holdout_timeout_seconds is None:
            data.pop("holdout_timeout_seconds", None)
        if not self.evaluation_assets:
            data.pop("evaluation_assets", None)
        if not self.agent_hidden_paths:
            data.pop("agent_hidden_paths", None)
        if not self.agent_visible_metrics:
            data.pop("agent_visible_metrics", None)
        if not self.agent_visible_diagnostics:
            data.pop("agent_visible_diagnostics", None)
        if not self.non_regression:
            data.pop("non_regression", None)
        # Keep approved hashes stable for campaign specs created before agent profiles.
        agent_data = data["agent"]
        for key in ("reasoning_effort", "provider"):
            if agent_data.get(key) is None:
                agent_data.pop(key, None)
        if not agent_data.get("roles"):
            agent_data.pop("roles", None)
        if not self.forbidden_diff_patterns:
            data.pop("forbidden_diff_patterns", None)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def evaluator_binding_hash(self) -> str:
        """Hash only the evaluator protocol; regression gates do not measure."""
        if self.regression_command and not self.validation_command:
            legacy = self.model_copy(update={
                "validation_command": self.regression_command,
                "validation_timeout_seconds": self.regression_timeout_seconds,
                "regression_command": [],
                "regression_timeout_seconds": None,
            })
            return legacy.approved_hash()
        return self.approved_hash()


class DiagnosticPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["diagnostic_only"]
    question: str = Field(min_length=20)
    competing_causes: list[str] = Field(min_length=2, max_length=4)
    evidence_to_collect: str = Field(min_length=20)
    decision_rule: str = Field(min_length=20)

    @model_validator(mode="after")
    def decision_rule_covers_causes(self):
        rule = self.decision_rule.casefold()
        causes = [cause.strip().casefold() for cause in self.competing_causes]
        if any(len(cause) < 8 for cause in causes):
            raise ValueError("competing causes must be concrete labels")
        if len(set(causes)) < 2 or any(cause not in rule for cause in causes):
            raise ValueError("decision_rule must distinguish every competing cause by name")
        return self


class Hypothesis(BaseModel):
    hypothesis: str
    rationale: str
    expected_effect: str
    files_or_area: list[str] = Field(default_factory=list)
    falsification_condition: str
    mechanism_key: str
    relation: Literal["new", "refine", "retry", "supersede"] = "new"
    prior_experiment_refs: list[int] = Field(default_factory=list)
    novelty_rationale: str = ""
    diagnostic_plan: dict[str, Any] | None = None


class ImplementResult(BaseModel):
    summary: str
    changed_paths: list[str] = Field(default_factory=list)
    verification_notes: str = ""
    blockers: list[str] = Field(default_factory=list)


class DevFailureSignal(BaseModel):
    """Bounded DEV-only failure evidence safe for adaptive analysis.

    The schema intentionally has no case id, expected answer id, product id,
    GOLD label, or arbitrary metadata field. Evaluators should describe the
    behavioural contradiction, not reveal benchmark identities.
    """

    model_config = ConfigDict(extra="forbid")

    failure_type: str
    stage: str = ""
    symptom: str
    expected_behavior: str = ""
    observed_behavior: str = ""
    affected_fields: list[str] = Field(default_factory=list, max_length=16)
    evidence: list[str] = Field(default_factory=list, max_length=8)


class DevFailureExample(BaseModel):
    """Public DEV teacher example visible only to the failure analyzer."""

    model_config = ConfigDict(extra="forbid")

    query: str
    verdict: str
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    pipeline_trace: dict[str, Any] = Field(default_factory=dict)


class FailureCapsule(BaseModel):
    """Generalized causal lesson distilled from one or more DEV failures."""

    model_config = ConfigDict(extra="forbid")

    title: str
    failure_class: str
    stage: str = ""
    affected_fields: list[str] = Field(default_factory=list, max_length=16)
    observation: str
    likely_mechanism: str
    general_rule: str
    suggested_regression: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class FailureAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capsules: list[FailureCapsule] = Field(default_factory=list, max_length=8)


class MetricValue(BaseModel):
    value: float
    unit: str | None = None
    n: int | None = Field(default=None, ge=1)


class CheckValue(BaseModel):
    passed: bool
    executed: int | None = Field(default=None, ge=0)
    skipped: int | None = Field(default=None, ge=0)


class CaseMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=200)
    repetition: int = Field(default=0, ge=0)
    # Covers input, reference, seed and fixed settings; evaluator-owned.
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: dict[str, float]

    @field_validator("metrics")
    @classmethod
    def finite_metrics(cls, value):
        import math
        if not value or not all(math.isfinite(v) for v in value.values()):
            raise ValueError("case measurements must be nonempty and finite")
        return value


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    repetition: int = Field(default=0, ge=0)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: dict[str, float] = Field(default_factory=dict)
    experiment_id: int | None = None
    candidate_snapshot: str | None = None


class EvaluationResult(BaseModel):
    schema_version: int = 1
    status: Literal["ok", "invalid", "infra_error", "cancelled"] = "ok"
    metrics: dict[str, MetricValue]
    checks: dict[str, CheckValue] = Field(default_factory=dict)
    diagnostics: dict[str, float] = Field(default_factory=dict)
    failure_signals: list[DevFailureSignal] = Field(default_factory=list, max_length=8)
    failure_examples: list[DevFailureExample] = Field(default_factory=list, max_length=5)
    artifacts: list[str] = Field(default_factory=list)  # legacy references
    protocol_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cases: list[CaseMeasurement] = Field(default_factory=list, max_length=10000)
    artifact_manifest: list[ArtifactRecord] = Field(default_factory=list, max_length=10000)
    resource_units: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def unique_cases(self):
        keys = [(c.case_id, c.repetition) for c in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate case/repetition")
        if self.cases and self.protocol_sha256 is None:
            raise ValueError("cases require protocol_sha256")
        return self

    @field_validator("metrics", mode="before")
    @classmethod
    def normalize_metrics(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized[key] = {"value": item} if isinstance(item, (int, float)) else item
        return normalized

    @field_validator("checks", mode="before")
    @classmethod
    def normalize_checks(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized[key] = {"passed": item} if isinstance(item, bool) else item
        return normalized


class Decision(BaseModel):
    accepted: bool
    primary_value: float
    champion_value: float
    delta: float
    predicates: dict[str, bool]
    reasons: list[str]


class MechanismEvidence(BaseModel):
    mechanism_key: str
    experiment_ordinals: list[int] = Field(default_factory=list)
    dev_outcomes: dict[str, int] = Field(default_factory=dict)
    last_relation: str | None = None


class ResearchState(BaseModel):
    schema_version: int = 1
    planning_mode: Literal["exploit", "explore", "reframe"] = "exploit"
    dev_non_improve_streak: int = Field(default=0, ge=0)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    mechanisms: list[MechanismEvidence] = Field(default_factory=list)
    recent_lessons: list[str] = Field(default_factory=list)
    recent_failure_capsules: list[FailureCapsule] = Field(default_factory=list, max_length=16)
    remaining_budget: dict[str, int] = Field(default_factory=dict)
    repeated_failure_diagnosis: dict[str, Any] | None = None


class RuntimePaths(BaseModel):
    root: Path
    db: Path
    workspace: Path
    artifacts: Path
    reports: Path

    @classmethod
    def for_repo(cls, repo: Path, override: Path | None = None) -> "RuntimePaths":
        root_repo = repo.resolve()
        if override is None:
            digest = hashlib.sha256(str(root_repo).encode()).hexdigest()[:16]
            root = Path.home() / ".bananloop" / "projects" / digest
        else:
            root = override.resolve()
        return cls(
            root=root,
            db=root / "campaign.sqlite3",
            workspace=root / "workspace",
            artifacts=root / "artifacts",
            reports=root / "reports",
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
