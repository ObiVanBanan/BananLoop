from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .agents import AgentAdapter, AgentError, CodexAdapter, CommandAgentAdapter, PiAdapter
from .contracts import (
    AgentConfig,
    BudgetSpec,
    CampaignSpec,
    EvaluationEvidence,
    GuardrailSpec,
    MetricSpec,
    NonRegressionSpec,
    PairedComparison,
    RuntimePaths,
)
from .evaluator_binding import canonical_hash, runtime_descriptor
from .integrity import discover_evaluation_assets, sha256_evaluation_asset
from .readiness import ReadinessError, run_bootstrap_readiness
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


class BootstrapSignalSanity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["configured", "unavailable"]
    evidence_kind: Literal[
        "known_examples", "controlled_degradation", "metamorphic", "invariants"
    ] | None = None
    metric: str | None = None
    positive_case_ids: list[str] = Field(default_factory=list, max_length=100)
    negative_case_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_signal_evidence(self) -> "BootstrapSignalSanity":
        if self.status == "configured":
            if self.evidence_kind is None or not self.metric:
                raise ValueError("configured signal_sanity requires evidence_kind and metric")
            if not self.positive_case_ids or not self.negative_case_ids:
                raise ValueError("configured signal_sanity requires positive and negative case IDs")
            all_ids = [*self.positive_case_ids, *self.negative_case_ids]
            if any(not case_id.strip() for case_id in all_ids):
                raise ValueError("signal_sanity case IDs must be nonempty")
            if (
                len(self.positive_case_ids) != len(set(self.positive_case_ids))
                or len(self.negative_case_ids) != len(set(self.negative_case_ids))
            ):
                raise ValueError("signal_sanity case IDs must be unique within each group")
            if set(self.positive_case_ids) & set(self.negative_case_ids):
                raise ValueError("signal_sanity positive and negative case IDs must be disjoint")
            if self.reason:
                raise ValueError("configured signal_sanity cannot include an unavailable reason")
        else:
            if not self.reason or not self.reason.strip():
                raise ValueError("unavailable signal_sanity requires an explicit reason")
            if self.evidence_kind is not None or self.metric is not None:
                raise ValueError("unavailable signal_sanity cannot claim evidence or a metric")
            if self.positive_case_ids or self.negative_case_ids:
                raise ValueError("unavailable signal_sanity cannot claim labeled case IDs")
        return self


class BootstrapPlan(BaseModel):
    """Agent-produced setup plan. It is validated before becoming CampaignSpec."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "needs_input"]
    summary: str
    project_context: str
    needs_input: list[str] = Field(default_factory=list)
    evaluation_command: list[str] = Field(default_factory=list)
    smoke_command: list[str] = Field(default_factory=list)
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
    signal_sanity: BootstrapSignalSanity = Field(
        default_factory=lambda: BootstrapSignalSanity(
            status="unavailable",
            reason="Bootstrap supplied no repository-backed positive/negative examples.",
        )
    )
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
        return CodexAdapter(config.model, config.extra_args, config.reasoning_effort)
    if config.kind == "pi":
        return PiAdapter(config.model, config.extra_args, config.command, config.provider, config.reasoning_effort)
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
            "detected_python_runtimes": discovery["python_runtimes"],
        },
        "agent": agent_config.model_dump(mode="json"),
        "requirements": {
            "operator_input": "repo + human goal only unless essential assets/credentials are truly absent",
            "goal_is_authoritative": True,
            "do_not_request_goal_file_when_goal_is_present": True,
            "resolve_conflicting_evaluators_by_goal_semantics": True,
            "use_detected_runtimes_before_requesting_python_environment": True,
            "allowed_bootstrap_writes": [".bananloop_eval/**"],
            "source_checkout_must_remain_unchanged": True,
            "reuse_existing_evaluator_when_trustworthy": True,
            "generated_evaluator_location": ".bananloop_eval/",
            "primary_metric_measures_goal_outcome": True,
            "include_goal_specific_behavioral_probes": True,
            "tests_are_secondary_regression_checks": True,
            "candidate_evaluation_uses_candidate_snapshot": True,
            "do_not_import_or_execute_host_checkout": True,
            "test_temp_files_use_os_tempdir": True,
            "do_not_redirect_test_temp_to_artifact_dir": True,
            "keep_windows_temp_paths_short": True,
            "deterministic_or_paired_measurement": True,
            "freeze_inputs_and_randomness_when_possible": True,
            "no_per_evaluation_llm_planning": True,
            "smoke_test_evaluator_before_ready": True,
            "core_owns_smoke_verdict": True,
            "provide_cheap_smoke_command_when_possible": True,
            "same_inputs_required_across_smoke_repeats": True,
            "aa_noise_must_be_below_min_improvement": True,
            "signal_sanity": {
                "seek_explicit_repository_backed_positive_negative_case_ids": True,
                "evidence_kinds": ["known_examples", "controlled_degradation", "metamorphic", "invariants"],
                "never_invent_labels": True,
                "mark_unavailable_with_reason_when_no_defensible_evidence": True,
                "configured_metric_must_be_primary_metric": True,
            },
            "emit_bananloop_evaluation_contract": True,
            "do_not_invent_missing_reference_data": True,
            "do_not_modify_production_code_during_bootstrap": True,
        },
    }


def _bootstrap_contract_error(
    plan: BootstrapPlan,
    task: dict[str, Any],
) -> str | None:
    if plan.status != "needs_input":
        return None
    goal_need = re.compile(
        r"(\bgoal\b|\bobjective\b|human intent|authoritative intent|\bbrief\b|fix\.md|\bцел[ьиь]\b|описани\w* задач\w*)",
        re.IGNORECASE,
    )
    request_words = re.compile(
        r"(provide|clarify|missing|need|required|not present|not available|not supplied|not found|unavailable|укаж|предостав|уточн|отсутств|не найден|нуж|требу|не задан|не передан)",
        re.IGNORECASE,
    )
    # A working interpreter does not establish package, credential, dataset or
    # version readiness. Preserve genuine external prerequisites, even when a
    # request also mentions the goal or Python.
    prerequisite = re.compile(
        r"(credential|api.?key|token|secret|reference|dataset|package|dependenc|module|library|"
        r"version|python\s*[<>=!~\d]|доступ|ключ|токен|секрет|референс|эталон|датасет|"
        r"пакет|зависим|модул|библиотек|верси)",
        re.IGNORECASE,
    )
    if any(prerequisite.search(item) for item in plan.needs_input):
        return None
    supplied_goal = bool(str(task.get("goal") or "").strip())
    runtimes = task.get("repository", {}).get("detected_python_runtimes", [])
    for item in [*plan.needs_input, plan.summary or ""]:
        if prerequisite.search(item):
            continue
        if supplied_goal and goal_need.search(item) and request_words.search(item):
            return (
                "bootstrap requested goal/intent input even though the top-level "
                "goal is already present and authoritative"
            )
        if runtimes and re.search(r"python|интерпретатор", item, re.IGNORECASE) and request_words.search(item):
            return "bootstrap requested a Python interpreter/path despite detected working runtimes; try the supplied runtimes first"
    return None


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
            assets[relative] = EvaluationAsset(path=relative, sha256=sha256_evaluation_asset(path))
    return [assets[key] for key in sorted(assets)]


def bootstrap_campaign(
    *,
    repo: Path,
    goal: str,
    paths: RuntimePaths,
    agent_config: AgentConfig,
    agent_timeout_seconds: int = 1200,
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

    bootstrap_agent_config = agent_config.for_role("bootstrap")
    adapter = _agent(bootstrap_agent_config)
    probe = adapter.probe()
    if not probe.get("available"):
        raise BootstrapError(f"bootstrap agent unavailable: {probe}")

    task = _bootstrap_task(repo, goal, source_base, bootstrap_agent_config)
    bootstrap_attempts: list[dict[str, Any]] = []
    plan: BootstrapPlan | None = None
    bootstrap_snapshot = source_base
    changed: list[str] = []

    # A missing external prerequisite is a pause, not a discarded bootstrap.
    # Reuse the previously prepared evaluator snapshot on the next invocation.
    record_path = paths.root / "bootstrap-plan.json"
    if record_path.is_file():
        previous = json.loads(record_path.read_text(encoding="utf-8"))
        previous_attempts = previous.get("bootstrap_attempts", [])
        previous_plan = previous.get("plan")
        if (
            previous.get("source_base_commit") == source_base
            and previous_attempts
            and isinstance(previous_plan, dict)
            and previous_plan.get("status") == "needs_input"
        ):
            prior_snapshot = previous.get("bootstrap_snapshot", source_base)
            # Do not trust an arbitrary snapshot from a stale or foreign state.
            if not isinstance(prior_snapshot, str) or not re.fullmatch(r"[0-9a-f]{40}", prior_snapshot):
                raise BootstrapError("invalid saved bootstrap snapshot")
            workspace.checkout(prior_snapshot)
            bootstrap_snapshot = prior_snapshot
            changed = workspace.changed_paths(source_base, prior_snapshot)
            if any(not _bootstrap_path(path) for path in changed):
                raise BootstrapError("saved bootstrap snapshot modifies protected source paths")
            bootstrap_attempts = previous_attempts
            prior = BootstrapPlan.model_validate(previous_plan)
            # Fail immediately for still-missing, explicitly named environment
            # variables. Never persist their values in campaign state.
            named_keys = sorted(set(re.findall(
                r"\\b[A-Z][A-Z0-9_]*(?:_API_KEY|_TOKEN|_SECRET)\\b",
                " ".join(prior.needs_input),
            )))
            missing = [key for key in named_keys if not os.environ.get(key)]
            if missing:
                return prior, None
            task = {
                **task,
                "resume_bootstrap": {
                    "previous_plan": previous_plan,
                    "previous_snapshot": prior_snapshot,
                    "instruction": (
                        "Continue the existing evaluator setup; do not rebuild "
                        "working .bananloop_eval assets. Recheck prerequisites "
                        "and return ready only after successful smoke evaluation."
                    ),
                },
            }

    def persist_record() -> None:
        record = {
            "source_base_commit": source_base,
            "bootstrap_snapshot": bootstrap_snapshot,
            "changed_paths": changed,
            "bootstrap_request": bootstrap_attempts[0]["request"] if bootstrap_attempts else task,
            "bootstrap_attempts": bootstrap_attempts,
            "agent_launch_profile": {
                "role": "bootstrap",
                "kind": bootstrap_agent_config.kind,
                "model": bootstrap_agent_config.model or "external-cli-default",
                "provider": bootstrap_agent_config.provider,
                "reasoning_effort": bootstrap_agent_config.reasoning_effort,
                "cli_version": probe.get("version", "unknown"),
            },
            "plan": plan.model_dump(mode="json") if plan is not None else None,
        }
        (paths.root / "bootstrap-plan.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    for attempt_index in range(3):
        try:
            raw = adapter.execute(
                task,
                workspace.workspace,
                timeout=agent_timeout_seconds,
            )
            raw = adapter.normalize_result(task, raw)
            plan = BootstrapPlan.model_validate(raw)
        except (AgentError, ValueError) as exc:
            failure = {
                "category": "agent_call_failed" if isinstance(exc, AgentError) else "invalid_response",
                "error_type": type(exc).__name__,
            }
            validation_errors: list[dict[str, str]] = []
            if isinstance(exc, ValidationError):
                known_fields = set(BootstrapPlan.model_fields)
                for issue in exc.errors(include_input=False, include_context=False)[:20]:
                    location = issue.get("loc", ())
                    field = location[0] if location and isinstance(location[0], str) else "response"
                    validation_errors.append({
                        "field": field if field in known_fields else "response",
                        "type": str(issue.get("type", "invalid"))[:80],
                    })
                validation_errors = list(
                    {(item["field"], item["type"]): item for item in validation_errors}.values()
                )
                failure["validation_errors"] = validation_errors
            bootstrap_attempts.append({
                "attempt": attempt_index + 1,
                "request": task,
                "snapshot": bootstrap_snapshot,
                "changed_paths": [],
                "failure": failure,
            })
            persist_record()
            workspace.checkout(source_base)
            if isinstance(exc, ValidationError) and attempt_index < 2:
                task = {
                    **task,
                    "repair": {
                        "reason": "The prior structured bootstrap plan failed schema validation.",
                        "validation_errors": validation_errors,
                        "instruction": (
                            "Return a complete plan conforming to the requested schema; "
                            "do not ask the operator to repeat supplied information."
                        ),
                    },
                }
                continue
            public_reason = (
                "agent call failed"
                if isinstance(exc, AgentError)
                else "invalid structured response"
            )
            raise BootstrapError(f"campaign bootstrap failed: {public_reason}") from exc

        contract_error = _bootstrap_contract_error(plan, task)
        bootstrap_snapshot = workspace.snapshot(
            f"bananloop: bootstrap attempt {attempt_index + 1}"
        )
        changed = workspace.changed_paths(source_base, bootstrap_snapshot)
        violations = [path for path in changed if not _bootstrap_path(path)]
        if violations:
            workspace.checkout(source_base)
            raise BootstrapError(
                "bootstrap agent modified files outside .bananloop_eval/: "
                + ", ".join(sorted(violations))
            )

        attempt_record: dict[str, Any] = {
            "attempt": attempt_index + 1,
            "request": task,
            "plan": plan.model_dump(mode="json"),
            "snapshot": bootstrap_snapshot,
            "changed_paths": changed,
            "contract_error": contract_error,
            "readiness": None,
        }
        bootstrap_attempts.append(attempt_record)

        if contract_error is not None:
            persist_record()
            if attempt_index < 2:
                task = {
                    **task,
                    "repair": {
                        "reason": contract_error,
                        "instruction": (
                            "Correct the plan using information already present in "
                            "the request. Do not ask the operator to repeat supplied "
                            "goal/runtime/context."
                        ),
                        "previous_plan": plan.model_dump(mode="json"),
                    },
                }
                continue
            workspace.checkout(source_base)
            raise BootstrapError(
                "bootstrap agent repeatedly violated the input contract: "
                + contract_error
            )

        if plan.status == "needs_input":
            persist_record()
            # Keep the private bootstrap snapshot so retrying after supplying
            # credentials does not repeat evaluator construction.
            return plan, None

        try:
            readiness = run_bootstrap_readiness(
                plan=plan,
                workspace=workspace.workspace,
                snapshot=bootstrap_snapshot,
                state_root=paths.root,
                attempt=attempt_index + 1,
            )
            attempt_record["readiness"] = readiness
            persist_record()
            break
        except ReadinessError as exc:
            attempt_record["readiness"] = exc.receipt or {
                "status": "FAILED",
                "error": str(exc),
            }
            persist_record()
            if attempt_index < 2:
                task = {
                    **task,
                    "repair": {
                        "reason": "core-owned evaluator readiness gate failed",
                        "readiness_error": str(exc),
                        "readiness_receipt": attempt_record["readiness"],
                        "instruction": (
                            "Repair only the measurement adapter/package under "
                            ".bananloop_eval so the core-owned smoke gate passes. "
                            "Do not modify production code or weaken the measurement "
                            "just to satisfy the gate."
                        ),
                        "previous_plan": plan.model_dump(mode="json"),
                    },
                }
                continue
            workspace.checkout(source_base)
            raise BootstrapError(
                "bootstrap evaluator failed readiness after bounded repair attempts: "
                + str(exc)
            ) from exc
    else:
        workspace.checkout(source_base)
        raise BootstrapError("bootstrap exhausted bounded preparation attempts")

    assert plan is not None
    signal_receipt = readiness.get("signal_sanity", {})
    readiness_payload = dict(readiness)
    readiness_receipt_sha256 = hashlib.sha256(
        json.dumps(
            readiness_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    protocol_hashes = readiness.get("protocol_sha256_values", [])
    first_run = readiness.get("runs", [{}])[0]
    case_inputs = sorted(
        first_run.get("case_inputs", []),
        key=lambda item: (item["case_id"], item["repetition"], item["input_sha256"]),
    )
    case_manifest_sha256 = hashlib.sha256(
        json.dumps(case_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    runtime = runtime_descriptor({"dev": plan.smoke_command or plan.evaluation_command})
    runtime_fingerprint = canonical_hash(runtime)
    metric_tolerances = {
        plan.primary_metric.name: float(
            readiness.get("calibration", {}).get("empirical_noise_bound", 0.0)
        )
    }
    paired_noise = readiness.get("calibration", {}).get("paired_empirical_noise_bound")
    if plan.paired_comparison.enabled and paired_noise is not None:
        metric_tolerances[plan.paired_comparison.metric] = float(paired_noise)
    evaluation_evidence = EvaluationEvidence(
        technical_readiness="READY",
        # Bootstrap-authored cases/labels are not independent evidence of
        # business relevance. Keep this unverified until an operator-approved
        # reference or an independent oracle is recorded.
        trust_level="UNVERIFIED",
        case_source="bootstrap_agent_configured",
        label_source=(
            "bootstrap_agent_configured"
            if signal_receipt.get("status") == "DIAGNOSTIC_ONLY"
            else None
        ),
        protocol_sha256=protocol_hashes[0] if protocol_hashes else None,
        case_input_manifest_sha256=case_manifest_sha256,
        runtime=runtime,
        runtime_fingerprint=runtime_fingerprint,
        metric_tolerances=metric_tolerances,
        readiness_receipt_sha256=readiness_receipt_sha256,
        verified_invariants=[
            key for key, passed in (
                ("stable_case_inputs", readiness.get("input_identity_stable") and bool(case_inputs)),
                ("stable_protocol_identity", readiness.get("protocol_identity_stable")),
                ("aa_noise_below_minimum_improvement", readiness.get("calibration", {}).get("noise_gate_passed")),
            ) if passed is True
        ],
        limitations=[
            "bootstrap-authored evaluator cases and labels have no independent provenance",
            "A/A stability and signal separation do not prove business-goal validity",
        ],
    )

    commands = [
        plan.smoke_command,
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
        signal_sanity_status=signal_receipt.get("status"),
        signal_sanity_receipt_sha256=signal_receipt.get("receipt_sha256"),
        evaluation_evidence=evaluation_evidence,
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
