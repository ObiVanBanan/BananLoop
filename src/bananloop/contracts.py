from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator


class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class CampaignStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED_INFRA = "PAUSED_INFRA"
    PAUSED_NEEDS_INPUT = "PAUSED_NEEDS_INPUT"
    PAUSED_USER = "PAUSED_USER"
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
    USER_PAUSED = "USER_PAUSED"
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
    # Pydantic <2.10 reserves the entire "model_" namespace by default.
    # Retain protection for actual BaseModel methods while allowing model_token_*.
    model_config = ConfigDict(protected_namespaces=("model_validate", "model_dump"))

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
    # Opt-in: no implied token cap for existing campaigns.
    max_model_tokens: int | None = Field(default=None, ge=1)
    model_token_reserve: int = Field(default=300000, ge=1)
    model_token_stage_reserves: dict[str, int] = Field(default_factory=dict)

    @field_validator("model_token_stage_reserves")
    @classmethod
    def valid_model_token_reserves(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"PROPOSE", "SELECT", "EXECUTE", "ANALYZE", "BOOTSTRAP"}
        if any(stage not in allowed or isinstance(amount, bool) or not isinstance(amount, int) or amount < 1
               for stage, amount in value.items()):
            raise ValueError("model_token_stage_reserves must map supported agent stages to positive integers")
        return value

    max_evaluator_calls: int = Field(default=10, ge=1)
    deadline_seconds: int = Field(default=3600, ge=1)
    plateau_experiments: int = Field(default=3, ge=1)


def pivot_proposal_digest(payload: dict[str, Any]) -> str:
    """Hash the immutable, operator-reviewable body of a pivot proposal."""
    # The optional execution plan was added after v2 proposals shipped. Keep
    # legacy proposal identity byte-for-byte stable when it is absent.
    if payload.get("execution_plan") is None:
        payload = {key: value for key, value in payload.items() if key != "execution_plan"}
    body = {key: value for key, value in payload.items() if key not in {"proposal_id", "digest"}}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PivotResourceRequest(BaseModel):
    """Non-authoritative estimate; the bound execution plan carries real caps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    additional_agent_calls: int = Field(default=0, ge=0, le=1000)
    additional_evaluator_calls: int = Field(default=0, ge=0, le=1000)
    additional_evaluation_units: float | None = Field(default=None, ge=0, le=1_000_000)
    notes: str = Field(default="", max_length=1000)


class PivotExecutionPlanV2(BaseModel):
    """Immutable, digest-bound child plan for an executable pivot proposal.

    JSON strings are used for nested data so a frozen model cannot be mutated
    through a dict/list reference after approval.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    child_spec_json: str = Field(min_length=2, max_length=200_000)
    changed_paths: tuple[str, ...] = Field(min_length=1, max_length=128)
    resource_vector_json: str = Field(min_length=2, max_length=10_000)

    @field_validator("child_spec_json", "resource_vector_json")
    @classmethod
    def canonical_json(cls, value: str) -> str:
        try:
            parsed = json.loads(value, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("pivot execution plan contains invalid JSON") from exc
        except ValueError as exc:
            raise ValueError("pivot execution plan JSON cannot contain NaN or Infinity") from exc
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if canonical != value:
            raise ValueError("pivot execution plan JSON must be canonical")
        return value

    @field_validator("changed_paths")
    @classmethod
    def canonical_changed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from pathlib import PurePosixPath, PureWindowsPath

        if len(value) != len(set(value)):
            raise ValueError("pivot execution plan paths must be unique")
        for path in value:
            posix = PurePosixPath(path)
            windows = PureWindowsPath(path)
            if (not path or "\\" in path or path.startswith("/") or windows.is_absolute() or windows.drive
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or str(posix) != path):
                raise ValueError("pivot execution plan paths must be canonical repository-relative paths")
        return value

    @classmethod
    def build(cls, *, child_spec: dict[str, Any], changed_paths: list[str] | tuple[str, ...],
              resource_vector: dict[str, Any]) -> "PivotExecutionPlanV2":
        return cls(
            child_spec_json=json.dumps(child_spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
            changed_paths=tuple(changed_paths),
            resource_vector_json=json.dumps(resource_vector, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        )


class PivotProposal(BaseModel):
    """Immutable architecture-pivot proposal awaiting human review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(pattern=r"^pivot-[0-9a-f]{24}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trigger_experiment_ordinal: int = Field(ge=1)
    current_architecture: str = Field(min_length=1, max_length=2000)
    proposed_architecture: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_effect: str = Field(min_length=1, max_length=1000)
    changed_scope: tuple[str, ...] = Field(min_length=1, max_length=32)
    resources: PivotResourceRequest
    execution_plan: PivotExecutionPlanV2 | None = None

    @field_validator("changed_scope")
    @classmethod
    def bounded_scope_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 300 for item in value):
            raise ValueError("pivot changed_scope entries must be nonempty and bounded")
        return value

    @model_validator(mode="after")
    def digest_matches_body(self) -> "PivotProposal":
        if pivot_proposal_digest(self.model_dump(mode="json")) != self.digest:
            raise ValueError("pivot proposal digest does not match its immutable body")
        if self.proposal_id != f"pivot-{self.digest[:24]}":
            raise ValueError("pivot proposal id does not match its digest")
        return self

    @classmethod
    def build(
        cls, *, spec_hash: str, trigger_experiment_ordinal: int,
        current_architecture: str, proposed_architecture: str,
        rationale: str, expected_effect: str, changed_scope: list[str] | tuple[str, ...],
        resources: PivotResourceRequest | dict[str, Any],
        execution_plan: PivotExecutionPlanV2 | dict[str, Any] | None = None,
    ) -> "PivotProposal":
        payload = {
            "spec_hash": spec_hash,
            "trigger_experiment_ordinal": trigger_experiment_ordinal,
            "current_architecture": current_architecture,
            "proposed_architecture": proposed_architecture,
            "rationale": rationale,
            "expected_effect": expected_effect,
            "changed_scope": list(changed_scope),
            "resources": (
                resources.model_dump(mode="json")
                if isinstance(resources, PivotResourceRequest)
                else PivotResourceRequest.model_validate(resources).model_dump(mode="json")
            ),
        }
        if execution_plan is not None:
            payload["execution_plan"] = (
                execution_plan.model_dump(mode="json")
                if isinstance(execution_plan, PivotExecutionPlanV2)
                else PivotExecutionPlanV2.model_validate(execution_plan).model_dump(mode="json")
            )
        digest = pivot_proposal_digest(payload)
        return cls.model_validate({
            **payload,
            "changed_scope": tuple(payload["changed_scope"]),
            "proposal_id": f"pivot-{digest[:24]}",
            "digest": digest,
        })


class PivotDecisionCommand(BaseModel):
    """Explicit operator decision tied to one immutable proposal digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    proposal_id: str = Field(pattern=r"^pivot-[0-9a-f]{24}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]
    rejection_action: Literal["continue_old_plan", "change_conditions", "end_campaign"] | None = None


class SpecLineage(BaseModel):
    """Immutable parent/approval link for a campaign created from a pivot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["pivot"] = "pivot"
    parent_campaign_id: str = Field(pattern=r"^(legacy|c-[0-9a-f]{32})$")
    parent_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str = Field(pattern=r"^pivot-[0-9a-f]{24}$")
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class EvaluationAsset(BaseModel):
    path: str
    sha256: str


class ProtocolCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=200)
    repetition: int = Field(default=0, ge=0)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProtocolManifest(BaseModel):
    """Core-frozen case identities for one evaluation tier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[ProtocolCase, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_cases(self):
        keys = [(case.case_id, case.repetition) for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("protocol manifest contains duplicate case/repetition identities")
        return self


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
    holdout_command: list[str] = Field(default_factory=list)
    holdout_timeout_seconds: int | None = Field(default=None, ge=1)
    evaluation_assets: list[EvaluationAsset] = Field(default_factory=list)
    agent_hidden_paths: list[str] = Field(default_factory=list)
    agent_visible_training_paths: list[str] = Field(default_factory=list)
    agent_visible_context_paths: list[str] = Field(default_factory=list)
    agent_visible_metrics: list[str] = Field(default_factory=list)
    agent_visible_diagnostics: list[str] = Field(default_factory=list)
    paired_comparison: PairedComparison | None = None
    protocol_manifests: dict[Literal["dev", "validation", "holdout"], ProtocolManifest] = Field(default_factory=dict)
    # Optional readiness metadata. None is omitted from approved_hash to keep
    # all campaigns created before signal sanity byte-for-byte compatible.
    signal_sanity_status: Literal["DIAGNOSTIC_ONLY", "UNAVAILABLE"] | None = None
    signal_sanity_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    primary_metric: MetricSpec
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    non_regression: list[NonRegressionSpec] = Field(default_factory=list)
    mandatory_checks: list[str] = Field(default_factory=list)
    editable_scope: list[str] = Field(default_factory=lambda: ["**"])
    protected_paths: list[str] = Field(default_factory=list)
    forbidden_diff_patterns: list[str] = Field(default_factory=list)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    # Opt-in semantic ranking after the existing deterministic admissibility gates.
    # Default is omitted from approved_hash for historical campaign compatibility.
    selection_mode: Literal["deterministic", "llm"] = "deterministic"
    # Optional exact candidate portfolio for controlled selector comparisons.
    # The raw UTF-8 JSON and digest are embedded in the approved campaign spec,
    # so later file edits cannot alter the experiment input.
    frozen_portfolio_json: str | None = None
    frozen_portfolio_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    replicate_label: str | None = Field(
        default=None, min_length=1, max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$",
    )
    # Pivot child lineage is part of the explicitly confirmed child spec hash.
    # Omit it for all existing standalone campaigns to preserve hash compatibility.
    spec_lineage: SpecLineage | None = None

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

    @field_validator("agent_visible_training_paths")
    @classmethod
    def training_paths_are_unambiguous(cls, value: list[str]) -> list[str]:
        training_name = re.compile(r"(?i)(?:^|[/_.-])(?:train|training|trainset)(?:$|[/_.-])")
        protected_name = re.compile(
            r"(?i)(?:^|[/_.-])(?:eval|evaluation|test|validation|holdout|gold|reference)(?:$|[/_.-])"
        )
        for path in value:
            normalized = path.replace("\\", "/")
            if (
                not normalized
                or normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in normalized.split("/"))
                or not training_name.search(normalized)
                or protected_name.search(normalized)
            ):
                raise ValueError(
                    "agent_visible_training_paths may only contain clearly named training files"
                )
        if len(value) != len(set(value)):
            raise ValueError("agent_visible_training_paths must be unique")
        return value

    @field_validator("agent_visible_context_paths")
    @classmethod
    def context_paths_are_safe(cls, value: list[str]) -> list[str]:
        unsafe_name = re.compile(
            r"(?i)(?:^|[/_.-])(?:eval|evaluation|test|validation|holdout|gold|reference|"
            r"query|queries|label|labels|answer|answers|expected|target)(?:$|[/_.-])"
        )
        for path in value:
            normalized = path.replace("\\", "/")
            if (
                not normalized
                or normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in normalized.split("/"))
                or unsafe_name.search(normalized)
            ):
                raise ValueError("agent_visible_context_paths cannot include unsafe or label-bearing paths")
        if len(value) != len(set(value)):
            raise ValueError("agent_visible_context_paths must be unique")
        return value

    @model_validator(mode="after")
    def validate_staged_evaluation_budget(self) -> "CampaignSpec":
        if (self.signal_sanity_status is None) != (self.signal_sanity_receipt_sha256 is None):
            raise ValueError("signal sanity status and receipt digest must be set together")
        if self.validation_timeout_seconds is not None and not self.validation_command:
            raise ValueError("validation_timeout_seconds requires validation_command")
        if self.holdout_timeout_seconds is not None and not self.holdout_command:
            raise ValueError("holdout_timeout_seconds requires holdout_command")
        if self.budget.max_model_tokens is not None:
            for role in ("propose", "select", "implement", "analyze"):
                if self.agent.for_role(role).kind != "codex":
                    raise ValueError("max_model_tokens requires Codex adapter for all model stages")
        minimum_calls = 1 + int(bool(self.validation_command)) + int(bool(self.holdout_command))
        if self.budget.max_evaluator_calls < minimum_calls:
            raise ValueError(
                "max_evaluator_calls is too small for baseline staged evaluation; "
                f"need at least {minimum_calls}"
            )
        if self.budget.max_evaluation_units is not None:
            if self.budget.max_evaluation_units < minimum_calls * self.budget.evaluation_units_per_call:
                raise ValueError("evaluation work budget too small for baseline staged evaluation")
        if (self.frozen_portfolio_json is None) != (self.frozen_portfolio_sha256 is None):
            raise ValueError("frozen portfolio JSON and SHA-256 must be set together")
        if self.replicate_label is not None and self.frozen_portfolio_json is None:
            raise ValueError("replicate_label requires a frozen portfolio")
        if self.frozen_portfolio_json is not None:
            actual_digest = hashlib.sha256(self.frozen_portfolio_json.encode("utf-8")).hexdigest()
            if actual_digest != self.frozen_portfolio_sha256:
                raise ValueError("frozen portfolio SHA-256 mismatch")
            try:
                frozen = json.loads(self.frozen_portfolio_json)
                if not isinstance(frozen, dict) or frozen.get("schema_version") != 1:
                    raise ValueError("frozen portfolio must be a schema_version 1 object")
                candidates = frozen.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    raise ValueError("frozen portfolio requires a non-empty candidates list")
                if frozen.get("candidate_count", len(candidates)) != len(candidates):
                    raise ValueError("frozen portfolio candidate_count mismatch")
                # Validate candidate fields at campaign approval time, while the
                # exact authored JSON remains embedded and hash-bound.
                HypothesisPortfolio.model_validate({"candidates": candidates})
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ValueError(f"invalid frozen portfolio: {exc}") from exc
        return self

    def approved_hash(self) -> str:
        data = self.model_dump(mode="json")
        if self.spec_lineage is None:
            data.pop("spec_lineage", None)
        if self.paired_comparison is None:
            data.pop("paired_comparison", None)
        if not self.protocol_manifests:
            data.pop("protocol_manifests", None)
        if self.signal_sanity_status is None:
            data.pop("signal_sanity_status", None)
        if self.signal_sanity_receipt_sha256 is None:
            data.pop("signal_sanity_receipt_sha256", None)
        for key in ("max_evaluation_units", "evaluation_units_per_call", "max_model_tokens"):
            if data["budget"][key] is None:
                data["budget"].pop(key)
        if self.budget.max_model_tokens is None:
            # Old campaign hashes did not include these opt-in budget fields.
            data["budget"].pop("model_token_reserve", None)
            data["budget"].pop("model_token_stage_reserves", None)
        elif not self.budget.model_token_stage_reserves:
            data["budget"].pop("model_token_stage_reserves", None)
        # Keep hashes compatible with v0.1 specs created before these fields existed.
        if not self.project_context:
            data.pop("project_context", None)
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
        if not self.agent_hidden_paths:
            data.pop("agent_hidden_paths", None)
        if not self.agent_visible_training_paths:
            data.pop("agent_visible_training_paths", None)
        if not self.agent_visible_context_paths:
            data.pop("agent_visible_context_paths", None)
        if not self.agent_visible_metrics:
            data.pop("agent_visible_metrics", None)
        if not self.agent_visible_diagnostics:
            data.pop("agent_visible_diagnostics", None)
        if not self.non_regression:
            data.pop("non_regression", None)
        if self.selection_mode == "deterministic":
            data.pop("selection_mode", None)
        if self.frozen_portfolio_json is None:
            data.pop("frozen_portfolio_json", None)
        if self.frozen_portfolio_sha256 is None:
            data.pop("frozen_portfolio_sha256", None)
        if self.replicate_label is None:
            data.pop("replicate_label", None)
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


CapabilityToolV2 = Literal[
    "read_file", "list_files", "write_file", "write_artifact", "run_command", "commit_candidate"
]
CapabilityPathV2 = Annotated[str, Field(min_length=1, max_length=500)]


class CapabilityPolicyV2(BaseModel):
    """Campaign-level ceiling for v2 worker tools; grants may only narrow it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal[1] = 1
    tools: tuple[CapabilityToolV2, ...] = Field(default=(), max_length=64)
    read_paths: tuple[CapabilityPathV2, ...] = Field(default=(), max_length=256)
    write_paths: tuple[CapabilityPathV2, ...] = Field(default=(), max_length=256)
    backend: Literal["LOCAL", "ISOLATED"] = "ISOLATED"
    network_access: bool = False

    @field_validator("read_paths", "write_paths")
    @classmethod
    def canonical_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        forbidden = {".git", ".bananloop_eval", "holdout", "validation", "evaluator", "evaluation", "secret", "secrets", "credentials"}
        normalized: list[str] = []
        for value in values:
            if (not value or "\\" in value or "\x00" in value
                    or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive):
                raise ValueError("capability paths must be safe POSIX-relative patterns")
            path = PurePosixPath(value)
            if not path.parts or path.as_posix() in {"", "."} or any(part in {".", ".."} for part in path.parts):
                raise ValueError("capability path pattern cannot traverse its workspace")
            lower = [part.lower() for part in path.parts]
            if any(part in forbidden or part.startswith(".env") or
                   re.search(r"(evaluator|evaluation|validation|holdout|secret|credential)", part)
                   for part in lower):
                raise ValueError("capability policy cannot grant protected paths")
            normalized.append(path.as_posix())
        if len(normalized) != len(set(normalized)):
            raise ValueError("capability path patterns must be unique")
        return tuple(sorted(normalized))

    @field_validator("tools")
    @classmethod
    def canonical_tools(cls, values: tuple[CapabilityToolV2, ...]) -> tuple[CapabilityToolV2, ...]:
        if len(values) != len(set(values)):
            raise ValueError("capability tools must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def isolated_commands_only(self) -> "CapabilityPolicyV2":
        if "run_command" in self.tools and self.backend != "ISOLATED":
            raise ValueError("run_command requires the ISOLATED backend")
        return self


class AutonomyScopeV2(BaseModel):
    """Versioned, path-bounded permission envelope for autonomous pivots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    max_child_depth: int = Field(default=0, ge=0, le=32)
    max_child_campaigns: int = Field(default=0, ge=0, le=1000)

    @field_validator("allowed_paths")
    @classmethod
    def canonical_allowed_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            if (not value or value != value.strip() or "\\" in value or "\x00" in value
                    or "*" in value or "?" in value or "[" in value or "]" in value):
                raise ValueError("autonomy scope paths must be concrete relative POSIX path prefixes")
            path = PurePosixPath(value)
            if (path.is_absolute() or PureWindowsPath(value).drive
                    or not path.parts or path.as_posix() != value
                    or any(part in {".", ".."} for part in path.parts)):
                raise ValueError("autonomy scope paths cannot be absolute or traverse their workspace")
            protected = {".git", ".bananloop_eval", "holdout", "validation",
                         "evaluator", "evaluation", "secret", "secrets", "credentials"}
            lower = [part.lower() for part in path.parts]
            if any(part in protected or part.startswith(".env")
                   or re.search(r"(evaluator|evaluation|validation|holdout|secret|credential)", part)
                   for part in lower):
                raise ValueError("autonomy scope cannot include protected evaluator, holdout, or secret paths")
            normalized.append(value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("autonomy scope paths must be unique")
        return tuple(sorted(normalized))

    def content_digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def allows_changed_paths(self, changed_paths: tuple[str, ...] | list[str]) -> bool:
        if not changed_paths:
            return False
        try:
            requested = AutonomyScopeV2(
                version=self.version, allowed_paths=tuple(changed_paths),
            ).allowed_paths
        except (TypeError, ValidationError):
            return False
        return all(any(path == allowed or path.startswith(allowed + "/")
                       for allowed in self.allowed_paths) for path in requested)

    def is_subset_of(self, parent: "AutonomyScopeV2") -> bool:
        return (
            self.version >= parent.version
            and self.max_child_depth <= parent.max_child_depth
            and self.max_child_campaigns <= parent.max_child_campaigns
            and all(any(path == allowed or path.startswith(allowed + "/")
                        for allowed in parent.allowed_paths)
                    for path in self.allowed_paths)
        )


class AutonomySpecV2(BaseModel):
    """Immutable autonomy mode and optional fully materialized pivot scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["FULL_AUTO", "SUPERVISED"]
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_version: int = Field(ge=1)
    # Omitted on earlier v2 specs to preserve their frozen approved hash.
    capability_policy: CapabilityPolicyV2 | None = None
    # Legacy v2 specs retain their original hash and fail closed for auto pivots.
    scope: AutonomyScopeV2 | None = None

    @model_validator(mode="after")
    def scope_digest_matches(self) -> "AutonomySpecV2":
        if self.scope is not None:
            if self.scope.version != self.scope_version:
                raise ValueError("autonomy scope version differs from scope_version")
            if self.scope.content_digest() != self.scope_digest:
                raise ValueError("autonomy scope digest does not match its canonical contents")
        return self

    def authorize_pivot(
        self, changed_paths: tuple[str, ...] | list[str], *,
        current_child_depth: int = 0, current_child_count: int = 0,
    ) -> Literal["AUTO_ALLOWED", "APPROVAL_REQUIRED", "DENIED"]:
        """Return an explicit disposition; prose claims never grant Full Auto."""
        if self.mode == "SUPERVISED":
            return "APPROVAL_REQUIRED"
        if (self.scope is None or isinstance(current_child_depth, bool)
                or isinstance(current_child_count, bool)
                or current_child_depth < 0 or current_child_count < 0
                or current_child_depth >= self.scope.max_child_depth
                or current_child_count >= self.scope.max_child_campaigns
                or not self.scope.allows_changed_paths(changed_paths)):
            return "DENIED"
        return "AUTO_ALLOWED"

    def is_narrower_child_of(self, parent: "AutonomySpecV2") -> bool:
        if parent.mode == "SUPERVISED" and self.mode != "SUPERVISED":
            return False
        if parent.scope is None:
            if self.scope is not None:
                return False
            return (self.scope_digest == parent.scope_digest
                    and self.scope_version == parent.scope_version)
        if self.scope is None or not self.scope.is_subset_of(parent.scope):
            return False
        if parent.capability_policy is None:
            return self.capability_policy is None
        if self.capability_policy is None:
            return False
        parent_policy, child_policy = parent.capability_policy, self.capability_policy
        return (
            set(child_policy.tools) <= set(parent_policy.tools)
            and set(child_policy.read_paths) <= set(parent_policy.read_paths)
            and set(child_policy.write_paths) <= set(parent_policy.write_paths)
            and (not child_policy.network_access or parent_policy.network_access)
            and (parent_policy.backend == "LOCAL" or child_policy.backend == "ISOLATED")
        )


class FrozenEvaluatorRefV2(BaseModel):
    """Content-addressed evaluator reference with an optional verifier receipt pin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Omitted on earlier v2 specs; approved_hash strips the absent value so
    # existing v2 campaign identities remain byte-for-byte stable.
    approval_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ParentCampaignRefV2(BaseModel):
    """Immutable parent allocation link with optional pivot audit lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_campaign_id: str = Field(min_length=1, max_length=200)
    allocation_id: str = Field(min_length=1, max_length=200)
    parent_spec_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pivot_proposal_id: str | None = Field(default=None, pattern=r"^pivot-[0-9a-f]{24}$")
    pivot_proposal_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pivot_decision_id: str | None = Field(default=None, min_length=1, max_length=200)
    approval_request_id: str | None = Field(default=None, min_length=1, max_length=200)
    protocol_handoff_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_pivot_lineage(self) -> "ParentCampaignRefV2":
        lineage = (
            self.parent_spec_hash, self.pivot_proposal_id,
            self.pivot_proposal_digest, self.pivot_decision_id,
        )
        if any(value is not None for value in lineage) and not all(value is not None for value in lineage):
            raise ValueError("pivot child lineage fields must be supplied together")
        if (self.approval_request_id is not None
                and self.approval_request_id != self.pivot_decision_id):
            raise ValueError("approval request must identify the pivot decision")
        return self


class TokenStageReserveV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal["PROPOSE", "SELECT", "EXECUTE", "ANALYZE", "BOOTSTRAP"]
    tokens: int = Field(ge=1)


class BudgetSpecV2(BaseModel):
    """Strict v2 budget snapshot; ledger ownership and settlement are RPDI-03."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_experiments: int = Field(default=5, ge=1)
    max_attempts: int = Field(default=8, ge=1)
    max_agent_calls: int = Field(default=20, ge=1)
    max_evaluator_calls: int = Field(default=10, ge=1)
    deadline_seconds: int = Field(default=3600, ge=1)
    plateau_experiments: int = Field(default=3, ge=1)
    max_evaluation_units: int | None = Field(default=None, ge=1)
    evaluation_units_per_call: int | None = Field(default=None, ge=1)
    max_model_tokens: int | None = Field(default=None, ge=1)
    model_token_reserve: int = Field(default=300000, ge=1)
    model_token_stage_reserves: tuple[TokenStageReserveV2, ...] = ()

    @model_validator(mode="after")
    def validate_reserves(self) -> "BudgetSpecV2":
        if (self.max_evaluation_units is None) != (self.evaluation_units_per_call is None):
            raise ValueError("max_evaluation_units and evaluation_units_per_call must be set together")
        stages = [reserve.stage for reserve in self.model_token_stage_reserves]
        if len(stages) != len(set(stages)):
            raise ValueError("model_token_stage_reserves stages must be unique")
        return self


V2FeatureName = Literal[
    "and_or_dag",
    "api_sse_cli",
    "budget_accounting",
    "capability_gateway",
    "causal_memory",
    "child_campaigns",
    "dashboard",
    "durable_worker_fencing",
    "dynamic_roles",
    "evaluator_verification",
    "exploration",
    "human_steering",
    "integration_verifier",
    "metric_discovery",
    "node_phase_scheduler",
    "observability",
    "optimization",
    "parallel_workers",
    "research_intelligence",
]
V2_FULL_FEATURES: tuple[V2FeatureName, ...] = (
    "and_or_dag",
    "api_sse_cli",
    "budget_accounting",
    "capability_gateway",
    "causal_memory",
    "child_campaigns",
    "dashboard",
    "durable_worker_fencing",
    "dynamic_roles",
    "evaluator_verification",
    "exploration",
    "human_steering",
    "integration_verifier",
    "metric_discovery",
    "node_phase_scheduler",
    "observability",
    "optimization",
    "parallel_workers",
    "research_intelligence",
)


class FeatureFlagsV2(BaseModel):
    """Versioned mechanism set bound into each frozen v2 campaign spec."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    profile: Literal["FULL", "DIAGNOSTIC"] = "FULL"
    enabled_features: tuple[V2FeatureName, ...] = V2_FULL_FEATURES

    @model_validator(mode="after")
    def validate_feature_profile(self) -> "FeatureFlagsV2":
        if tuple(sorted(self.enabled_features)) != self.enabled_features:
            raise ValueError("enabled_features must use canonical sorted order")
        if len(set(self.enabled_features)) != len(self.enabled_features):
            raise ValueError("enabled_features must be unique")
        if self.profile == "FULL" and self.enabled_features != V2_FULL_FEATURES:
            raise ValueError("FULL profile must enable every v2 mechanism")
        return self

    def flag_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _CampaignSpecV2Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    goal: str = Field(min_length=1, max_length=4000)
    source_repo: str = Field(min_length=1, max_length=2000)
    base_commit: str = Field(min_length=1, max_length=200)
    autonomy: AutonomySpecV2
    budget: BudgetSpecV2
    role_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_flags: FeatureFlagsV2 = Field(default_factory=FeatureFlagsV2)
    parent: ParentCampaignRefV2 | None = None

    def approved_hash(self) -> str:
        """Return the v2 canonical digest without modifying v1 hash semantics."""
        data = self.model_dump(mode="json")
        if data["autonomy"].get("capability_policy") is None:
            data["autonomy"].pop("capability_policy", None)
        if data["autonomy"].get("scope") is None:
            data["autonomy"].pop("scope", None)
        if isinstance(data.get("evaluation"), dict) and data["evaluation"].get("approval_digest") is None:
            data["evaluation"].pop("approval_digest", None)
        if isinstance(data.get("parent"), dict):
            data["parent"] = {
                key: value for key, value in data["parent"].items()
                if not (key in {"parent_spec_hash", "pivot_proposal_id",
                                "pivot_proposal_digest", "pivot_decision_id", "approval_request_id",
                                "protocol_handoff_digest"}
                        and value is None)
            }
        payload = json.dumps(data, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExplorationCampaignSpecV2(_CampaignSpecV2Base):
    mode: Literal["EXPLORATION"]
    evaluation: None = None


class OptimizationCampaignSpecV2(_CampaignSpecV2Base):
    mode: Literal["OPTIMIZATION"]
    evaluation: FrozenEvaluatorRefV2


CampaignSpecV2 = Annotated[
    Union[ExplorationCampaignSpecV2, OptimizationCampaignSpecV2],
    Field(discriminator="mode"),
]
_campaign_spec_v2_adapter = TypeAdapter(CampaignSpecV2)


def parse_campaign_spec_document(
    document: dict[str, Any] | str,
) -> CampaignSpec | ExplorationCampaignSpecV2 | OptimizationCampaignSpecV2:
    """Dispatch by schema version before parsing; never let v1 ignore v2 fields."""
    payload = json.loads(document) if isinstance(document, str) else document
    if not isinstance(payload, dict):
        raise ValueError("campaign spec must be a JSON object")
    if "schema_version" not in payload and {
        "mode", "autonomy", "feature_flags", "evaluation", "parent", "role_registry_digest"
    }.intersection(payload):
        raise ValueError("v2 campaign fields require an explicit schema_version=2")
    version = payload.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("campaign spec schema_version must be an integer")
    if version == 1:
        return CampaignSpec.model_validate(payload)
    if version == 2:
        return _campaign_spec_v2_adapter.validate_python(payload)
    raise ValueError(f"unsupported campaign spec schema_version={version}")


def parse_legacy_campaign_spec(document: dict[str, Any] | str) -> CampaignSpec:
    """Parse a spec for v1 Engine/store consumers and fail closed on v2."""
    parsed = parse_campaign_spec_document(document)
    if not isinstance(parsed, CampaignSpec):
        raise ValueError("schema_version=2 requires the v2 campaign adapter")
    return parsed


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
    # The causal failure being addressed is distinct from the proposed fix's
    # mechanism_key. Older durable hypotheses remain valid without these fields.
    target_failure_signal: str = ""
    failure_mechanism: str = ""


class HypothesisCandidate(BaseModel):
    """One bounded adviser proposal; only the core decides admissibility."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=80)
    hypothesis: Hypothesis
    composite_steps: list[Hypothesis] = Field(default_factory=list, max_length=4)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    suspected_failure_mode: str = Field(default="", max_length=300)
    predicted_observable: str = Field(min_length=1, max_length=500)
    discriminating_test: str = Field(min_length=1, max_length=500)
    scope_eligibility: bool
    estimated_agent_calls: int = Field(default=1, ge=0, le=4)
    estimated_evaluator_calls: int = Field(default=1, ge=0, le=4)
    estimated_work_units: float | None = Field(default=None, ge=0, le=1000000)
    rationale: str = Field(min_length=1, max_length=1000)
    uncertainty: str = Field(min_length=1, max_length=500)
    inference: Literal["agent_inference"] = "agent_inference"

    @field_validator("composite_steps")
    @classmethod
    def bounded_composite_steps(cls, value: list[Hypothesis]) -> list[Hypothesis]:
        if value and not 2 <= len(value) <= 4:
            raise ValueError("a composite requires two to four ordered steps")
        return value

    @field_validator("candidate_id")
    @classmethod
    def candidate_id_is_stable_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
            raise ValueError("candidate_id must be a stable lowercase slug")
        return value


class HypothesisPortfolio(BaseModel):
    """At most four alternatives from one bounded proposal action."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[HypothesisCandidate] = Field(default_factory=list, max_length=4)
    malformed_candidates: list[dict[str, str]] = Field(default_factory=list, max_length=4)


class CandidateDecision(BaseModel):
    candidate_id: str
    admissible: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    criterion_scores: dict[str, int] = Field(default_factory=dict)
    selection_reason: str = ""


class PortfolioRecord(BaseModel):
    """Durable, bounded operator-facing record of a planning decision."""

    experiment_ordinal: int
    action_id: str
    strategy: Literal["exploit", "explore", "reframe"]
    trigger_evidence: list[str] = Field(default_factory=list)
    candidates: list[HypothesisCandidate] = Field(default_factory=list, max_length=4)
    decisions: list[CandidateDecision] = Field(default_factory=list, max_length=8)
    invalid_candidates: list[CandidateDecision] = Field(default_factory=list, max_length=4)
    selected_candidate_id: str | None = None
    stop_reason: str = ""


class PortfolioChoice(BaseModel):
    """LLM preference is advisory; the engine verifies the chosen ID and public evidence."""

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    main_risk: str = Field(min_length=1, max_length=500)


class DevResearchLesson(BaseModel):
    """Observed outcome separated from inference and next discriminator."""

    experiment_ordinal: int
    mechanism_key: str
    observation: str
    interpretation: Literal["supported", "refuted", "unknown"]
    inference: str = ""
    next_discriminator: str = ""
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


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
    # Aggregate-only evaluators may emit case identities solely to attest that
    # frozen protocol inputs stayed fixed. Paired comparisons validate the
    # configured per-case metric in readiness before they can use these cases.
    metrics: dict[str, float] = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def finite_metrics(cls, value):
        import math
        if not all(math.isfinite(v) for v in value.values()):
            raise ValueError("case measurements must be finite")
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
    # Evaluators may include categorical context such as `split: "dev"`
    # alongside numeric diagnostics. Consumers that compare diagnostics
    # already select finite numeric values only.
    diagnostics: dict[str, float | str] = Field(default_factory=dict)
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


class DevFailureSummary(BaseModel):
    signal_key: str
    source: Literal["metric", "diagnostic", "capsule"]
    baseline_value: float | None = None
    current_value: float | None = None
    last_candidate_value: float | None = None
    best_candidate_value: float | None = None
    best_experiment_ordinal: int | None = None
    changed_experiment_ordinals: list[int] = Field(default_factory=list)
    occurrences: int = 0
    likely_mechanism: str = ""


class DevExperimentEvidence(BaseModel):
    ordinal: int
    mechanism_key: str
    dev_outcome: str
    primary_delta: float | None = None
    failure_signal_deltas: dict[str, float] = Field(default_factory=dict)
    rejection_reasons: list[str] = Field(default_factory=list)


class ResearchState(BaseModel):
    schema_version: int = 1
    planning_mode: Literal["exploit", "explore", "reframe"] = "exploit"
    dev_non_improve_streak: int = Field(default=0, ge=0)
    champion_stagnation_streak: int = Field(default=0, ge=0)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    mechanisms: list[MechanismEvidence] = Field(default_factory=list)
    recent_lessons: list[str] = Field(default_factory=list)
    recent_failure_capsules: list[FailureCapsule] = Field(default_factory=list, max_length=16)
    failure_signals: list[DevFailureSummary] = Field(default_factory=list, max_length=8)
    experiment_evidence: list[DevExperimentEvidence] = Field(default_factory=list, max_length=8)
    remaining_budget: dict[str, int] = Field(default_factory=dict)
    portfolios: list[PortfolioRecord] = Field(default_factory=list, max_length=16)
    dev_lessons: list[DevResearchLesson] = Field(default_factory=list, max_length=16)
    stop_explanation: str = ""


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
