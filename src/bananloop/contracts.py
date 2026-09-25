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


class BudgetSpec(BaseModel):
    max_experiments: int = Field(default=5, ge=1)
    max_attempts: int = Field(default=8, ge=1)
    max_agent_calls: int = Field(default=20, ge=1)
    max_evaluator_calls: int = Field(default=10, ge=1)
    deadline_seconds: int = Field(default=3600, ge=1)
    plateau_experiments: int = Field(default=3, ge=1)


class EvaluationAsset(BaseModel):
    path: str
    sha256: str


class SamplePlanSpec(BaseModel):
    """Frozen sampling policy shared by baseline/champion/candidates."""

    size: int = Field(ge=1)
    seed: int = 42
    repeats: int = Field(default=1, ge=1)
    policy: Literal["frozen_random", "fixed"] = "frozen_random"


class PairedComparisonSpec(BaseModel):
    """Optional case-level guardrails for noisy/stochastic evaluations."""

    metric: str
    direction: Direction | None = None
    min_improved_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    worst_case_tolerance: float | None = Field(default=None, ge=0.0)
    require_same_cases: bool = True


class EvaluationProtocolSpec(BaseModel):
    """Domain-neutral reproducibility controls for an evaluator."""

    sample_plan: SamplePlanSpec | None = None
    paired: PairedComparisonSpec | None = None


class AgentConfig(BaseModel):
    kind: Literal["codex", "command", "pi"] = "codex"
    command: list[str] = Field(default_factory=list)
    model: str | None = None
    extra_args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def command_required(self) -> "AgentConfig":
        if self.kind == "command" and not self.command:
            raise ValueError("command agent requires command argv")
        return self


class CampaignSpec(BaseModel):
    schema_version: int = 1
    goal: str
    source_repo: str
    base_commit: str
    evaluation_command: list[str]
    evaluator_timeout_seconds: int = Field(default=900, ge=1)
    validation_command: list[str] = Field(default_factory=list)
    validation_timeout_seconds: int | None = Field(default=None, ge=1)
    holdout_command: list[str] = Field(default_factory=list)
    holdout_timeout_seconds: int | None = Field(default=None, ge=1)
    evaluation_assets: list[EvaluationAsset] = Field(default_factory=list)
    evaluation_protocol: EvaluationProtocolSpec = Field(default_factory=EvaluationProtocolSpec)
    agent_hidden_paths: list[str] = Field(default_factory=list)
    agent_visible_metrics: list[str] = Field(default_factory=list)
    agent_visible_diagnostics: list[str] = Field(default_factory=list)
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
        if self.validation_timeout_seconds is not None and not self.validation_command:
            raise ValueError("validation_timeout_seconds requires validation_command")
        if self.holdout_timeout_seconds is not None and not self.holdout_command:
            raise ValueError("holdout_timeout_seconds requires holdout_command")
        minimum_calls = 1 + int(bool(self.validation_command)) + int(bool(self.holdout_command))
        if self.budget.max_evaluator_calls < minimum_calls:
            raise ValueError(
                "max_evaluator_calls is too small for baseline staged evaluation; "
                f"need at least {minimum_calls}"
            )
        return self

    def approved_hash(self) -> str:
        data = self.model_dump(mode="json")
        # Keep hashes compatible with v0.1 specs created before these fields existed.
        if self.evaluator_timeout_seconds == 900:
            data.pop("evaluator_timeout_seconds", None)
        if not self.validation_command:
            data.pop("validation_command", None)
        if self.validation_timeout_seconds is None:
            data.pop("validation_timeout_seconds", None)
        if not self.holdout_command:
            data.pop("holdout_command", None)
        if self.holdout_timeout_seconds is None:
            data.pop("holdout_timeout_seconds", None)
        if not self.evaluation_assets:
            data.pop("evaluation_assets", None)
        if self.evaluation_protocol == EvaluationProtocolSpec():
            data.pop("evaluation_protocol", None)
        if not self.agent_hidden_paths:
            data.pop("agent_hidden_paths", None)
        if not self.agent_visible_metrics:
            data.pop("agent_visible_metrics", None)
        if not self.agent_visible_diagnostics:
            data.pop("agent_visible_diagnostics", None)
        if not self.non_regression:
            data.pop("non_regression", None)
        if not self.forbidden_diff_patterns:
            data.pop("forbidden_diff_patterns", None)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


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


class CaseResult(BaseModel):
    """One opaque evaluation case.

    case_key identifies the frozen sample for pairing. It should not reveal
    hidden labels or sensitive evaluator assets to the adaptive agent.
    """

    case_key: str
    metrics: dict[str, MetricValue] = Field(default_factory=dict)

    @field_validator("metrics", mode="before")
    @classmethod
    def normalize_metrics(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            key: {"value": item} if isinstance(item, (int, float)) else item
            for key, item in value.items()
        }


class ArtifactRef(BaseModel):
    path: str
    kind: str = "file"
    sha256: str | None = None
    visibility: Literal["agent", "analyzer", "evaluator_only", "report"] = "report"


class EvaluationResult(BaseModel):
    schema_version: int = 1
    status: Literal["ok", "invalid", "infra_error", "cancelled"] = "ok"
    metrics: dict[str, MetricValue]
    checks: dict[str, CheckValue] = Field(default_factory=dict)
    diagnostics: dict[str, float] = Field(default_factory=dict)
    failure_signals: list[DevFailureSignal] = Field(default_factory=list, max_length=8)
    failure_examples: list[DevFailureExample] = Field(default_factory=list, max_length=5)
    cases: list[CaseResult] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

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

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [
            {"path": item, "kind": "file", "visibility": "report"}
            if isinstance(item, str)
            else item
            for item in value
        ]


class Decision(BaseModel):
    accepted: bool
    primary_value: float
    champion_value: float
    delta: float
    predicates: dict[str, bool]
    reasons: list[str]
    paired_summary: dict[str, float | int] = Field(default_factory=dict)


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
