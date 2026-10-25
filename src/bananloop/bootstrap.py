from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .agents import AgentAdapter, AgentError, CodexAdapter, CommandAgentAdapter, PiAdapter
from .contracts import (
    AgentConfig,
    BudgetSpec,
    CampaignSpec,
    EvaluationAsset,
    GuardrailSpec,
    MetricSpec,
    NonRegressionSpec,
    PairedComparison,
    ProtocolManifest,
    RuntimePaths,
)
from .integrity import discover_evaluation_assets, sha256_evaluation_asset
from .readiness import ReadinessError, run_bootstrap_readiness
from .store import CampaignStore, StoreError, campaign_db_initialized
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
    protocol_manifest_commands: dict[Literal["dev", "validation", "holdout"], list[str]] = Field(default_factory=dict)
    agent_visible_training_paths: list[str] = Field(default_factory=list)
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
            eval_commands = {
                "dev": self.evaluation_command,
                "validation": self.validation_command,
                "holdout": self.holdout_command,
            }
            configured_manifests = {
                tier: command
                for tier, command in self.protocol_manifest_commands.items()
                if command
            }
            unknown = sorted(set(configured_manifests) - set(eval_commands))
            if unknown:
                raise ValueError("protocol manifest command has unknown tier(s): " + ", ".join(unknown))
            invalid = sorted(
                tier for tier in configured_manifests
                if not eval_commands[tier]
            )
            if invalid:
                raise ValueError("protocol manifest commands require configured evaluator tiers: " + ", ".join(invalid))
            if configured_manifests and any(
                eval_commands[tier] and tier not in configured_manifests
                for tier in eval_commands
            ):
                raise ValueError("every configured evaluator tier requires a protocol manifest command")
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
            "preferred_python_runtime": discovery.get("preferred_python_runtime"),
            "detected_resources": discovery["resources"],
        },
        "agent": agent_config.model_dump(mode="json"),
        "requirements": {
            "operator_input": "repo + human goal only unless essential assets/credentials are truly absent",
            "reuse_discovered_project_resources": True,
            "never_request_a_path_already_present_in_detected_resources": True,
            "do_not_open_secret_env_files_or_echo_secret_values": True,
            "use_detected_environment_variable_names_without_requesting_secret_values": True,
            "report_resource_paths_and_availability_before_requesting_missing_inputs": True,
            "goal_is_authoritative": True,
            "do_not_request_goal_file_when_goal_is_present": True,
            "resolve_conflicting_evaluators_by_goal_semantics": True,
            "use_detected_runtimes_before_requesting_python_environment": True,
            "prefer_project_local_python_runtime": True,
            "core_injects_dotenv_into_evaluator_process_only": True,
            "do_not_require_credentials_to_be_exported_in_bootstrap_agent_process": True,
            "agent_sandbox_network_probe_is_advisory": True,
            "core_readiness_is_authoritative_for_local_service_access": True,
            "reconstruct_same_frozen_benchmark_from_repo_history_and_evaluator_cli": True,
            "deterministic_sample_seed_is_valid_frozen_identity_when_repo_documents_it": True,
            "verify_only_is_not_quality_measurement": True,
            "allowed_bootstrap_writes": [".bananloop_eval/**"],
            "source_checkout_must_remain_unchanged": True,
            "reuse_existing_evaluator_when_trustworthy": True,
            "generated_evaluator_location": ".bananloop_eval/",
            "primary_metric_measures_goal_outcome": True,
            "include_goal_specific_behavioral_probes": True,
            "tests_are_secondary_regression_checks": True,
            "mandatory_checks_are_evaluator_check_keys_only": True,
            "mandatory_checks_must_not_contain_prose_commands_or_metrics": True,
            "use_empty_mandatory_checks_when_no_applicable_check_key_is_known": True,
            "candidate_evaluation_uses_candidate_snapshot": True,
            "do_not_import_or_execute_host_checkout": True,
            "test_temp_files_use_os_tempdir": True,
            "do_not_redirect_test_temp_to_artifact_dir": True,
            "keep_windows_temp_paths_short": True,
            "deterministic_or_paired_measurement": True,
            "fixed_input_identity_is_independent_of_paired_acceptance": True,
            "do_not_enable_paired_comparison_solely_for_a_fixed_input_manifest": True,
            "require_a_scientific_reason_for_paired_comparison": True,
            "freeze_inputs_and_randomness_when_possible": True,
            "no_per_evaluation_llm_planning": True,
            "smoke_test_evaluator_before_ready": True,
            "freeze_case_identities_before_full_scoring": True,
            "provide_protocol_manifest_command_for_fixed_input_tiers": True,
            "protocol_manifest_command_must_enumerate_without_scoring": True,
            "full_evaluator_must_read_and_honor_bananloop_protocol_manifest": True,
            "bootstrap_smoke_must_return_nonempty_manifest_member_case_identities": True,
            "bootstrap_smoke_protocol_sha256_must_match_manifest": True,
            "bootstrap_smoke_subset_must_preserve_primary_metric_semantics": True,
            "use_full_manifest_when_no_valid_semantic_smoke_subset_exists": True,
            "protocol_manifests_are_separate_per_configured_tier": True,
            "no_protocol_manifest_or_case_identity_is_visible_to_implementation_workers": True,
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


def _preferred_project_python(task: dict[str, Any]) -> str | None:
    runtime = task.get("repository", {}).get("preferred_python_runtime")
    if not isinstance(runtime, dict) or runtime.get("source") != "project":
        return None
    executable = runtime.get("executable")
    return executable if isinstance(executable, str) and executable else None


def _bind_python_command(command: list[str], executable: str | None) -> list[str]:
    if not executable or not command:
        return list(command)
    bound = list(command)
    index = 1 if bound and bound[0] == "env" else 0
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    while index < len(bound) and assignment.match(bound[index]):
        index += 1
    if index >= len(bound):
        return bound
    name = Path(bound[index]).name.casefold()
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", name):
        bound[index] = executable
    return bound


def _bind_plan_runtime(plan: BootstrapPlan, task: dict[str, Any]) -> BootstrapPlan:
    executable = _preferred_project_python(task)
    if not executable or plan.status != "ready":
        return plan
    return plan.model_copy(update={
        "evaluation_command": _bind_python_command(plan.evaluation_command, executable),
        "smoke_command": _bind_python_command(plan.smoke_command, executable),
        "protocol_manifest_commands": {
            tier: _bind_python_command(command, executable)
            for tier, command in plan.protocol_manifest_commands.items()
        },
        "validation_command": _bind_python_command(plan.validation_command, executable),
        "holdout_command": _bind_python_command(plan.holdout_command, executable),
    })


def _validate_agent_visible_training_paths(
    paths: list[str],
    runtime_resources: list[str],
) -> list[str]:
    """Allow workers to read explicitly selected training resources, never test splits."""
    runtime_set = set(runtime_resources)
    training_name = re.compile(r"(?i)(?:^|[/_.-])(?:train|training|trainset)(?:$|[/_.-])")
    protected_name = re.compile(
        r"(?i)(?:^|[/_.-])(?:eval|evaluation|test|validation|holdout|gold|reference)(?:$|[/_.-])"
    )
    validated: list[str] = []
    for value in paths:
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
            or normalized not in runtime_set
            or not training_name.search(normalized)
            or protected_name.search(normalized)
        ):
            raise BootstrapError(
                "agent_visible_training_paths must name discovered training resources "
                "and must not name evaluation, test, validation, holdout, GOLD, or reference files"
            )
        if normalized not in validated:
            validated.append(normalized)
    return validated


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
    prerequisite = re.compile(
        r"(credential|api.?key|token|secret|reference|dataset|package|dependenc|module|library|"
        r"version|python\s*[<>=!~\d]|доступ|ключ|токен|секрет|референс|эталон|датасет|"
        r"пакет|зависим|модул|библиотек|верси)",
        re.IGNORECASE,
    )
    secret_prerequisite = re.compile(
        r"(credential|api.?key|token|secret|password|passwd|ключ|токен|секрет|парол)",
        re.IGNORECASE,
    )
    supplied_goal = bool(str(task.get("goal") or "").strip())
    repository = task.get("repository", {})
    runtimes = repository.get("detected_python_runtimes", [])
    resources = repository.get("detected_resources", {})
    env_keys = [str(key) for key in resources.get("env_keys", [])]
    env_keys_upper = {key.upper() for key in env_keys}

    def secret_is_already_bound(message: str) -> bool:
        upper = message.upper()
        if any(key in upper for key in env_keys_upper):
            return True
        provider_keys = {
            "OPENAI": "OPENAI_API_KEY",
            "DEEPSEEK": "DEEPSEEK_API_KEY",
            "QDRANT": "QDRANT_API_KEY",
        }
        return any(
            provider in upper and key in env_keys_upper
            for provider, key in provider_keys.items()
        )

    for item in plan.needs_input:
        if secret_prerequisite.search(item):
            if secret_is_already_bound(item):
                return (
                    "bootstrap requested a credential already declared in the discovered "
                    ".env environment; BananLoop core injects dotenv values only into the "
                    "trusted evaluator process, so do not require the bootstrap agent process "
                    "to export or inspect that secret"
                )
            continue
        resource_claims = (
            ("csv_files", r"\b(csv|catalog|catalogue|dataset|data file|каталог|датасет|таблиц)\b"),
            ("env_files", r"(\.env\b|env file|environment file|файл окружения|файл \.env)"),
            ("benchmark_files", r"\b(benchmark|evaluator|evaluation command|бенчмарк|оценщик)\b"),
            ("history_files", r"\b(frozen|sample|seed|case list|cases|history|journal|эксперимент|истори|выборк|сид|кейс)\b"),
            ("compose_files", r"\b(qdrant|docker compose|compose file|service config|service unavailable|network|localhost|127\.0\.0\.1|конфиг сервиса|сервис|сеть)\b"),
            ("config_files", r"\b(config|configuration|конфигурац)\b"),
        )
        for resource_kind, pattern in resource_claims:
            if resources.get(resource_kind) and re.search(pattern, item, re.IGNORECASE):
                return (
                    f"bootstrap requested {resource_kind} input even though project "
                    "discovery found candidate resources; inspect them and only ask "
                    "for input if they are demonstrably inapplicable or unusable"
                )
        if prerequisite.search(item):
            continue
        if supplied_goal and goal_need.search(item) and request_words.search(item):
            return (
                "bootstrap requested goal/intent input even though the top-level "
                "goal is already present and authoritative"
            )
        if runtimes and re.search(r"python|интерпретатор", item, re.IGNORECASE) and request_words.search(item):
            return "bootstrap requested a Python interpreter/path despite detected working runtimes; try the supplied runtimes first"
    if supplied_goal and goal_need.search(plan.summary or "") and request_words.search(plan.summary or ""):
        return (
            "bootstrap requested goal/intent input even though the top-level "
            "goal is already present and authoritative"
        )
    if runtimes and re.search(r"python|интерпретатор", plan.summary or "", re.IGNORECASE) and request_words.search(plan.summary or ""):
        return "bootstrap requested a Python interpreter/path despite detected working runtimes; try the supplied runtimes first"
    return None


def _bootstrap_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized == ".bananloop_eval" or normalized.startswith(".bananloop_eval/")


_RUNTIME_RESOURCE_KINDS = (
    "csv_files",
    "benchmark_files",
    "history_files",
    "config_files",
    "compose_files",
)


def _materialize_runtime_resources(
    source_repo: Path,
    managed_workspace: Path,
    discovery: dict[str, Any],
) -> list[str]:
    """Copy discovered non-secret gitignored resources into the trusted evaluator workspace."""
    bound: list[str] = []
    resources = discovery.get("resources", {})
    for kind in _RUNTIME_RESOURCE_KINDS:
        for relative in resources.get(kind, []):
            normalized = str(relative).replace("\\", "/").lstrip("/")
            if not normalized or normalized.startswith("../"):
                continue
            source = (source_repo / normalized).resolve()
            try:
                source.relative_to(source_repo.resolve())
            except ValueError:
                continue
            if not source.is_file():
                continue
            target = managed_workspace / normalized
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            bound.append(normalized)
    return sorted(set(bound))


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
    fresh_perspective_on_reframe: bool = False,
) -> tuple[BootstrapPlan, CampaignSpec | None]:
    """Turn repo + goal into an approved CampaignSpec without touching source checkout."""

    if campaign_db_initialized(paths.db):
        raise StoreError(
            "campaign state already exists; use run/resume or choose a fresh --state-dir"
        )

    paths.ensure()
    source_base = current_commit(repo)
    workspace = WorkspaceManager(repo, paths.workspace)
    workspace.ensure(source_base)
    workspace.checkout(source_base)
    discovery = discover_project(repo)
    runtime_bindings = _materialize_runtime_resources(
        repo, workspace.workspace, discovery
    )

    bootstrap_agent_config = agent_config.for_role("bootstrap")
    adapter = _agent(bootstrap_agent_config)
    probe = adapter.probe()
    if not probe.get("available"):
        raise BootstrapError(f"bootstrap agent unavailable: {probe}")

    task = _bootstrap_task(repo, goal, source_base, bootstrap_agent_config)
    task["repository"]["bound_runtime_resources"] = runtime_bindings
    bootstrap_attempts: list[dict[str, Any]] = []
    plan: BootstrapPlan | None = None
    bootstrap_snapshot = source_base
    changed: list[str] = []

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
            plan = _bind_plan_runtime(plan, task)
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
                            "the request or detected repository resources. Inspect "
                            "already discovered files before requesting their paths. "
                            "Do not ask the operator to repeat supplied goal/runtime/context."
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
            workspace.checkout(source_base)
            return plan, None

        try:
            readiness = run_bootstrap_readiness(
                plan=plan,
                workspace=workspace.workspace,
                snapshot=bootstrap_snapshot,
                state_root=paths.root,
                attempt=attempt_index + 1,
                source_repo=repo,
                runtime_resources=runtime_bindings,
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
                            "If readiness timed out, make smoke_command a cheap deterministic "
                            "subset/probe rather than rerunning the full evaluation. Preserve "
                            "the full evaluation command and its scientific checks unchanged. "
                            "Do not modify production code or weaken the measurement just to "
                            "satisfy the gate."
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
    visible_training_paths = _validate_agent_visible_training_paths(
        plan.agent_visible_training_paths,
        runtime_bindings,
    )
    signal_receipt = readiness.get("signal_sanity", {})

    commands = [
        plan.smoke_command,
        plan.evaluation_command,
        *plan.protocol_manifest_commands.values(),
        plan.validation_command,
        plan.holdout_command,
    ]
    assets = _freeze_assets(workspace.workspace, commands)
    asset_map = {asset.path: asset for asset in assets}
    for relative in runtime_bindings:
        path = workspace.workspace / relative
        if path.is_file():
            asset_map[relative] = EvaluationAsset(
                path=relative,
                sha256=sha256_evaluation_asset(path),
            )
    assets = [asset_map[key] for key in sorted(asset_map)]
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
        agent_hidden_paths=list(
            dict.fromkeys([
                ".bananloop_eval/**",
                *(path for path in runtime_bindings if path not in visible_training_paths),
            ])
        ),
        agent_visible_training_paths=visible_training_paths,
        agent_visible_metrics=(
            plan.agent_visible_metrics or [plan.primary_metric.name]
        ),
        agent_visible_diagnostics=plan.agent_visible_diagnostics,
        paired_comparison=paired,
        protocol_manifests={
            tier: ProtocolManifest.model_validate(manifest)
            for tier, manifest in (readiness.get("protocol_manifests") or {}).items()
        },
        signal_sanity_status=signal_receipt.get("status"),
        signal_sanity_receipt_sha256=signal_receipt.get("receipt_sha256"),
        primary_metric=plan.primary_metric,
        guardrails=plan.guardrails,
        non_regression=plan.non_regression,
        mandatory_checks=plan.mandatory_checks,
        editable_scope=plan.editable_scope or ["**"],
        protected_paths=protected,
        fresh_perspective_on_reframe=fresh_perspective_on_reframe,
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
