from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    evaluation_assets: list[EvaluationAsset] = Field(default_factory=list)
    agent_hidden_paths: list[str] = Field(default_factory=list)
    agent_visible_metrics: list[str] = Field(default_factory=list)
    primary_metric: MetricSpec
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    mandatory_checks: list[str] = Field(default_factory=list)
    editable_scope: list[str] = Field(default_factory=lambda: ["**"])
    protected_paths: list[str] = Field(default_factory=list)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @field_validator("evaluation_command")
    @classmethod
    def evaluator_nonempty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("evaluation_command cannot be empty")
        return value

    def approved_hash(self) -> str:
        data = self.model_dump(mode="json")
        # Keep hashes compatible with v0.1 specs created before these fields existed.
        if self.evaluator_timeout_seconds == 900:
            data.pop("evaluator_timeout_seconds", None)
        if not self.evaluation_assets:
            data.pop("evaluation_assets", None)
        if not self.agent_hidden_paths:
            data.pop("agent_hidden_paths", None)
        if not self.agent_visible_metrics:
            data.pop("agent_visible_metrics", None)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class Hypothesis(BaseModel):
    hypothesis: str
    rationale: str
    expected_effect: str
    files_or_area: list[str] = Field(default_factory=list)
    falsification_condition: str
    mechanism_key: str


class ImplementResult(BaseModel):
    summary: str
    changed_paths: list[str] = Field(default_factory=list)
    verification_notes: str = ""
    blockers: list[str] = Field(default_factory=list)


class MetricValue(BaseModel):
    value: float
    unit: str | None = None
    n: int | None = Field(default=None, ge=1)


class CheckValue(BaseModel):
    passed: bool
    executed: int | None = Field(default=None, ge=0)
    skipped: int | None = Field(default=None, ge=0)


class EvaluationResult(BaseModel):
    schema_version: int = 1
    status: Literal["ok", "invalid", "infra_error", "cancelled"] = "ok"
    metrics: dict[str, MetricValue]
    checks: dict[str, CheckValue] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)

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
