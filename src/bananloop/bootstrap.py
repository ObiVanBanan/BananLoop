from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agents import AgentAdapter, AgentError, CodexAdapter, CommandAgentAdapter, PiAdapter
from .contracts import (
    AgentConfig,
    BudgetSpec,
    CampaignSpec,
    GuardrailSpec,
    MetricSpec,
    NonRegressionSpec,
    PairedComparison,
    RuntimePaths,
)
from .integrity import discover_evaluation_assets, sha256_file
from .store import CampaignStore, StoreError
from .wizard import discover_project
from .workspace import WorkspaceManager, current_commit


class BootstrapError(RuntimeError):
    pass


class BootstrapPairedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    case_count: int = Field(default=1, ge=1)
    repetitions: int = Field(default=1, ge=1)
    metric: str = "quality"
    direction: Literal["maximize", "minimize"] = "maximize"
    min_wins: int = Field(default=1, ge=1)
    worst_case_tolerance: float = Field(default=0.0, ge=0)


class BootstrapPlan(BaseModel):
    """Agent-produced setup plan. It is validated before becoming CampaignSpec."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "needs_input"]
    summary: str
    project_context: str
    needs_input: list[str] = Field(default_factory=list)
    evaluation_command: list[str] = Field(default_factory=list)
    validation_command: list[str] = Field(default_factory=list)
    holdout_command: list[str] = Field(default_factory=list)
    primary_metric: MetricSpec
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    non_regression: list[NonRegressionSpec] = Field(default_factory=list)
    mandatory_checks: list[str] = Field(default_factory=list)
    editable_scope: list[str] = Field(default_factory=lambda: ["**"])
    protected_paths: list[str] = Field(default_factory=list)
    agent_visible_metrics: list[str] = Field(default_factory=list)
    agent_visible_diagnostics: list[str] = Field(default_factory=list)
    paired_comparison: BootstrapPairedPlan = Field(default_factory=BootstrapPairedPlan)
    evaluator_timeout_seconds: int = Field(default=900, ge=1)

    @model_validator(mode="after")
    def ready_has_executable_contract(self) -> "BootstrapPlan":
        if self.status == "ready":
            if self.needs_input:
                raise ValueError("ready bootstrap plan cannot also require operator input")
            if not self.evaluation_command:
                raise ValueError("ready bootstrap plan requires evaluation_command")
            if not self.project_context.strip():
                raise ValueError("ready bootstrap plan requires project_context")
        elif not self.needs_input:
            raise ValueError("needs_input bootstrap plan must explain what input is missing")
        return self


def _agent(config: AgentConfig) -> AgentAdapter:
    if config.kind == "codex":
        return CodexAdapter(config.model, config.extra_args)
    if config.kind == "pi":
        return PiAdapter(config.model, config.extra_args)
    return CommandAgentAdapter(config.command)


def _bootstrap_task(
    repo: Path,
    goal: str,
    base_commit: str,
    agent_config: AgentConfig,
) -> dict[str, Any]:
    discovery = discover_project(repo)
    return {
        "schema_version": 1,
        "role": "bootstrap",
        "goal": goal,
        "repository": {
            "path": str(repo),
            "base_commit": base_commit,
            "detected_test_commands": discovery["test_commands"],
        },
        "agent": agent_config.model_dump(mode="json"),
        "requirements": {
            "operator_input": "repo + human goal only unless essential assets/credentials are truly absent",
            "allowed_bootstrap_writes": [".bananloop_eval/**"],
            "source_checkout_must_remain_unchanged": True,
            "reuse_existing_evaluator_when_trustworthy": True,
            "generated_evaluator_location": ".bananloop_eval/",
            "deterministic_or_paired_measurement": True,
            "freeze_inputs_and_randomness_when_possible": True,
            "no_per_evaluation_llm_planning": True,
            "emit_bananloop_evaluation_contract": True,
            "do_not_invent_missing_reference_data": True,
            "do_not_modify_production_code_during_bootstrap": True,
        },
    }


def _bootstrap_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized == ".bananloop_eval" or normalized.startswith(".bananloop_eval/")


def _freeze_assets(workspace: Path, commands: list[list[str]]) -> list:
    assets = {}
    for command in commands:
        if not command:
            continue
        for asset in discover_evaluation_assets(workspace, command):
            assets[asset.path] = asset

    generated = workspace / ".bananloop_eval"
    if generated.is_dir():
        for path in sorted(generated.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace).as_posix()
            from .contracts import EvaluationAsset
            assets[relative] = EvaluationAsset(path=relative, sha256=sha256_file(path))
    return [assets[key] for key in sorted(assets)]


def bootstrap_campaign(
    *,
    repo: Path,
    goal: str,
    paths: RuntimePaths,
    agent_config: AgentConfig,
    agent_timeout_seconds: int = 1800,
    max_experiments: int = 4,
    max_attempts: int = 7,
    max_agent_calls: int = 18,
    max_evaluator_calls: int = 16,
    deadline_seconds: int = 14400,
    plateau_experiments: int = 3,
) -> tuple[BootstrapPlan, CampaignSpec | None]:
    """Turn repo + goal into an approved CampaignSpec without touching source checkout."""

    if paths.db.exists():
        raise StoreError(
            "campaign state already exists; use run/resume or choose a fresh --state-dir"
        )

    paths.ensure()
    source_base = current_commit(repo)
    workspace = WorkspaceManager(repo, paths.workspace)
    workspace.ensure(source_base)
    workspace.checkout(source_base)

    adapter = _agent(agent_config)
    probe = adapter.probe()
    if not probe.get("available"):
        raise BootstrapError(f"bootstrap agent unavailable: {probe}")

    task = _bootstrap_task(repo, goal, source_base, agent_config)
    try:
        raw = adapter.execute(task, workspace.workspace, timeout=agent_timeout_seconds)
        raw = adapter.normalize_result(task, raw)
        plan = BootstrapPlan.model_validate(raw)
    except (AgentError, ValueError) as exc:
        workspace.checkout(source_base)
        raise BootstrapError(f"campaign bootstrap failed: {exc}") from exc

    bootstrap_snapshot = workspace.snapshot("bananloop: bootstrap evaluator")
    changed = workspace.changed_paths(source_base, bootstrap_snapshot)
    violations = [path for path in changed if not _bootstrap_path(path)]
    if violations:
        workspace.checkout(source_base)
        raise BootstrapError(
            "bootstrap agent modified files outside .bananloop_eval/: "
            + ", ".join(sorted(violations))
        )

    record = {
        "source_base_commit": source_base,
        "bootstrap_snapshot": bootstrap_snapshot,
        "changed_paths": changed,
        "plan": plan.model_dump(mode="json"),
    }
    (paths.root / "bootstrap-plan.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if plan.status == "needs_input":
        workspace.checkout(source_base)
        return plan, None

    commands = [
        plan.evaluation_command,
        plan.validation_command,
        plan.holdout_command,
    ]
    assets = _freeze_assets(workspace.workspace, commands)
    frozen_paths = [asset.path for asset in assets]
    protected = list(
        dict.fromkeys(
            [
                *plan.protected_paths,
                ".bananloop_eval/**",
                *frozen_paths,
            ]
        )
    )

    paired = None
    if plan.paired_comparison.enabled:
        paired = PairedComparison(
            case_count=plan.paired_comparison.case_count,
            repetitions=plan.paired_comparison.repetitions,
            metric=plan.paired_comparison.metric,
            direction=plan.paired_comparison.direction,
            min_wins=plan.paired_comparison.min_wins,
            worst_case_tolerance=plan.paired_comparison.worst_case_tolerance,
        )

    spec = CampaignSpec(
        goal=goal,
        source_repo=str(repo),
        base_commit=bootstrap_snapshot,
        project_context=plan.project_context,
        evaluation_command=plan.evaluation_command,
        evaluator_timeout_seconds=plan.evaluator_timeout_seconds,
        validation_command=plan.validation_command,
        holdout_command=plan.holdout_command,
        evaluation_assets=assets,
        agent_hidden_paths=[".bananloop_eval/**"],
        agent_visible_metrics=(
            plan.agent_visible_metrics or [plan.primary_metric.name]
        ),
        agent_visible_diagnostics=plan.agent_visible_diagnostics,
        paired_comparison=paired,
        primary_metric=plan.primary_metric,
        guardrails=plan.guardrails,
        non_regression=plan.non_regression,
        mandatory_checks=plan.mandatory_checks,
        editable_scope=plan.editable_scope or ["**"],
        protected_paths=protected,
        budget=BudgetSpec(
            max_experiments=max_experiments,
            max_attempts=max_attempts,
            max_agent_calls=max_agent_calls,
            max_evaluator_calls=max_evaluator_calls,
            deadline_seconds=deadline_seconds,
            plateau_experiments=plateau_experiments,
        ),
        agent=agent_config,
    )

    store = CampaignStore(paths.db)
    try:
        store.initialize(spec)
        store.event(
            "CAMPAIGN_BOOTSTRAPPED",
            {
                "source_base_commit": source_base,
                "bootstrap_snapshot": bootstrap_snapshot,
                "changed_paths": changed,
                "summary": plan.summary,
            },
        )
        store.conn.commit()
    finally:
        store.close()
    return plan, spec
