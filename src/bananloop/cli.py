from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .bootstrap import BootstrapError, bootstrap_campaign
from .agent_profiles import AgentProfileError, load_agent_profiles
from .contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, GuardrailSpec, MetricSpec, NonRegressionSpec, PairedComparison, RuntimePaths
from .codex_hook import main as codex_hook_main
from .engine import ControlledInterruption, Engine, EngineError
from .export import export_champion
from .integrity import EvaluationIntegrityError, discover_evaluation_assets, evaluation_fingerprint
from .lock import CampaignLock, CampaignLockError
from .report import build_report, render_markdown
from .replay import replay_dev_action
from .registry import CampaignRegistry, RegistryError, assert_safe_campaign_db
from .store import CampaignStore, StoreError
from .wizard import confirm_spec, discover_project, needs_interactive_init, run_init_wizard
from .workspace import WorkspaceError, WorkspaceManager, assert_clean_source, current_commit


def _guard(value: str) -> GuardrailSpec:
    for op in ("<=", ">=", "==", "<", ">"):
        if op in value:
            name, bound = value.split(op, 1)
            return GuardrailSpec(metric=name.strip(), operator=op, bound=float(bound.strip()))
    raise argparse.ArgumentTypeError("guard must look like metric<=123")


def _non_regression(value: str) -> NonRegressionSpec:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) not in {2, 3} or parts[1] not in {"maximize", "minimize"}:
        raise argparse.ArgumentTypeError(
            "non-regression must look like metric:maximize[:tolerance] or metric:minimize[:tolerance]"
        )
    tolerance = float(parts[2]) if len(parts) == 3 else 0.0
    return NonRegressionSpec(
        metric=parts[0],
        direction=Direction(parts[1]),
        tolerance=tolerance,
    )


def _paths(repo: Path, state_dir: str | None) -> RuntimePaths:
    paths = RuntimePaths.for_repo(repo, Path(state_dir) if state_dir else None)
    paths.ensure()
    assert_safe_campaign_db(paths.db)
    return paths


def _selected_paths(args: argparse.Namespace, *, create: bool = True) -> RuntimePaths:
    repo = Path(args.repo).resolve()
    state_dir = getattr(args, "state_dir", None)
    campaign_id = getattr(args, "campaign_id", None)
    if state_dir and campaign_id:
        raise RegistryError("--state-dir and --campaign-id cannot be used together")
    if state_dir:
        return _paths(repo, state_dir) if create else RuntimePaths.for_repo(repo, Path(state_dir))
    state_root, _ = CampaignRegistry(repo).resolve(campaign_id)
    paths = RuntimePaths.for_repo(repo, state_root)
    if create:
        paths.ensure()
    assert_safe_campaign_db(paths.db)
    return paths


def _agent_config(args: argparse.Namespace) -> AgentConfig:
    profiles = load_agent_profiles(Path(args.agent_profiles) if getattr(args, "agent_profiles", None) else None)
    agent_kind = args.agent or profiles["default"].get("kind") or "codex"
    extra_args: list[str] = []
    if agent_kind == "codex":
        extra_args = args.codex_arg or []
    elif agent_kind == "pi":
        extra_args += args.pi_arg or []
    base = {
        "kind": agent_kind,
        "command": shlex.split(args.agent_cmd) if args.agent_cmd else [],
        "model": args.model,
        "extra_args": extra_args,
        "provider": args.provider,
        "reasoning_effort": args.thinking if agent_kind == "pi" else None,
    }
    base.update({key: value for key, value in profiles["default"].items() if value is not None})
    # Explicit CLI options are more specific than the optional profile file.
    base.update({
        "kind": agent_kind,
        "model": args.model if args.model is not None else base.get("model"),
        "provider": args.provider if args.provider is not None else base.get("provider"),
        "reasoning_effort": args.thinking if args.thinking is not None else base.get("reasoning_effort"),
        "extra_args": extra_args if extra_args else base.get("extra_args", []),
    })
    return AgentConfig(
        **base,
        roles=profiles["roles"],
    )


def cmd_init(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not args.allow_dirty:
        assert_clean_source(repo)
    base = current_commit(repo)

    interactive = needs_interactive_init(args)
    if interactive:
        if not args.interactive and not sys.stdin.isatty():
            raise ValueError(
                "missing required init fields in non-interactive mode; "
                "provide --goal/--eval-cmd/--metric/--target/--min-improvement or use --interactive"
            )
        run_init_wizard(args, repo, base)

    if args.direction is None:
        args.direction = "maximize"
    agent = _agent_config(args)
    evaluation_command = shlex.split(args.eval_cmd)
    validation_command = (
        shlex.split(args.validation_eval_cmd)
        if args.validation_eval_cmd
        else []
    )
    holdout_command = (
        shlex.split(args.holdout_eval_cmd)
        if args.holdout_eval_cmd
        else []
    )

    discovered_assets = {}
    for command in (evaluation_command, validation_command, holdout_command):
        if not command:
            continue
        for asset in discover_evaluation_assets(repo, command, args.eval_asset):
            discovered_assets[asset.path] = asset
    evaluation_assets = [
        discovered_assets[path] for path in sorted(discovered_assets)
    ]
    frozen_paths = [asset.path for asset in evaluation_assets]
    protected_paths = list(dict.fromkeys([*(args.protect or []), *frozen_paths]))
    spec = CampaignSpec(
        goal=args.goal,
        source_repo=str(repo),
        base_commit=base,
        evaluation_command=evaluation_command,
        evaluator_timeout_seconds=args.evaluator_timeout_seconds,
        validation_command=validation_command,
        validation_timeout_seconds=args.validation_timeout_seconds,
        holdout_command=holdout_command,
        holdout_timeout_seconds=args.holdout_timeout_seconds,
        evaluation_assets=evaluation_assets,
        agent_hidden_paths=args.agent_hide or [],
        agent_visible_metrics=args.agent_metric or [],
        agent_visible_diagnostics=args.agent_diagnostic or [],
        paired_comparison=PairedComparison.model_validate_json(Path(args.paired_policy).read_text()) if args.paired_policy else None,
        primary_metric=MetricSpec(
            name=args.metric,
            direction=Direction(args.direction),
            target=args.target,
            min_improvement=args.min_improvement,
            unit=args.unit,
            minimum=args.metric_min,
            maximum=args.metric_max,
        ),
        guardrails=args.guard or [],
        non_regression=args.non_regression or [],
        mandatory_checks=args.check or [],
        editable_scope=args.editable or ["**"],
        protected_paths=protected_paths,
        forbidden_diff_patterns=args.forbid_diff_regex or [],
        budget=BudgetSpec(
            max_evaluation_units=args.max_evaluation_units,
            evaluation_units_per_call=args.evaluation_units_per_call,
            max_experiments=args.max_experiments,
            max_attempts=args.max_attempts,
            max_agent_calls=args.max_agent_calls,
            max_evaluator_calls=args.max_evaluator_calls,
            deadline_seconds=args.deadline_seconds,
            plateau_experiments=args.plateau,
        ),
        agent=agent,
    )
    if interactive and not args.yes:
        if not confirm_spec(spec.model_dump(mode="json")):
            print("Campaign not approved; no campaign state was created.")
            return 1

    paths = _paths(repo, args.state_dir)
    store = CampaignStore(paths.db)
    try:
        store.initialize(spec)
    finally:
        store.close()
    fingerprint = evaluation_fingerprint(evaluation_assets) or "-"
    print(
        f"Campaign initialized\nstate: {paths.root}\nbase: {base}"
        f"\nspec: {spec.approved_hash()}\nevaluator_fingerprint: {fingerprint}"
    )
    return 0



def cmd_doctor(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    discovery = discover_project(repo)
    registry = None
    active_campaign_id = None
    if args.state_dir:
        paths = RuntimePaths.for_repo(repo, Path(args.state_dir))
    else:
        registry = CampaignRegistry(repo)
        state_root, active_campaign_id = registry.resolve()
        paths = RuntimePaths.for_repo(repo, state_root)
    payload = {
        "repo": str(repo),
        "base_commit": current_commit(repo) if (repo / ".git").exists() else None,
        "state": str(paths.root),
        "campaign_exists": paths.db.exists(),
        "detected_agents": discovery["agents"],
        "detected_test_commands": discovery["test_commands"],
        "detected_python_runtimes": discovery["python_runtimes"],
    }
    if registry is not None:
        entries = registry.list()
        payload["campaign_registry"] = str(registry.path)
        payload["active_campaign_id"] = active_campaign_id
        payload["campaign_count"] = len(entries)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(f"{key}: {value}" for key, value in payload.items()))
    return 0


def cmd_codex_hook(args: argparse.Namespace) -> int:
    """Internal Stop-hook entry point; consumes Codex JSON from stdin."""
    return codex_hook_main()


def cmd_campaigns_list(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    registry = CampaignRegistry(repo)
    payload = []
    for entry, active in registry.list():
        state = registry._validate_state_path(entry.state_path, entry.origin)
        status = "RESERVED"
        db = state / "campaign.sqlite3"
        if db.exists():
            store = CampaignStore(db)
            try:
                status = store.campaign()["status"]
            finally:
                store.close()
        payload.append({
            "campaign_id": entry.campaign_id,
            "goal": entry.goal_summary,
            "goal_fingerprint": entry.goal_fingerprint,
            "origin": entry.origin,
            "status": status,
            "active": active,
        })
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for item in payload:
            marker = " *" if item["active"] else ""
            print(f"{item['campaign_id']}{marker}\t{item['status']}\t{item['origin']}\t{item['goal']}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not args.allow_dirty:
        assert_clean_source(repo)
    if args.state_dir:
        paths = _paths(repo, args.state_dir)
        if paths.db.exists():
            raise StoreError(
                "campaign state already exists; use run/resume or choose a fresh --state-dir"
            )
    else:
        registry = CampaignRegistry(repo)
        entry, state_root, _ = registry.reserve(args.goal, getattr(args, "request_id", None))
        paths = RuntimePaths.for_repo(repo, state_root)
        paths.ensure()
        args.campaign_id = entry.campaign_id

    reused_campaign = False
    if paths.db.exists():
        reused_campaign = True
        store = CampaignStore(paths.db)
        try:
            spec = store.spec()
            campaign = store.campaign()
        finally:
            store.close()
        plan = None
        if not args.prepare_only and campaign["status"] in {"GOAL_MET", "STOPPED"}:
            print(json.dumps({"status": campaign["status"], "state": str(paths.root), "campaign_id": getattr(args, "campaign_id", None)}, indent=2))
            return 0
    else:
        try:
            with CampaignLock(paths.root / "campaign.lock"):
                if paths.db.exists():
                    reused_campaign = True
                    store = CampaignStore(paths.db)
                    try:
                        spec = store.spec()
                        campaign = store.campaign()
                    finally:
                        store.close()
                    plan = None
                    if not args.prepare_only and campaign["status"] in {"GOAL_MET", "STOPPED"}:
                        print(json.dumps({"status": campaign["status"], "state": str(paths.root), "campaign_id": getattr(args, "campaign_id", None)}, indent=2))
                        return 0
                else:
                    plan, spec = bootstrap_campaign(
                        repo=repo,
                        goal=args.goal,
                        paths=paths,
                        agent_config=getattr(args, "_bootstrap_agent_config", None) or _agent_config(args),
                        agent_timeout_seconds=args.bootstrap_timeout_seconds,
                        max_experiments=args.max_experiments,
                        max_attempts=args.max_attempts,
                        max_agent_calls=args.max_agent_calls,
                        max_evaluator_calls=args.max_evaluator_calls,
                        deadline_seconds=args.deadline_seconds,
                        plateau_experiments=args.plateau,
                    )
        except CampaignLockError as exc:
            print(f"BananLoop error: {exc}", file=sys.stderr)
            return 2
    if spec is None:
        if getattr(args, "_human_output", False):
            print(f"[BananLoop] Setup paused: {plan.summary}")
            for need in plan.needs_input:
                print(f"  - {need}")
            print("[BananLoop] Progress saved. Use 'bananloop continue' when ready.")
        else:
            print(json.dumps({
                "status": "PAUSED_NEEDS_INPUT",
                "summary": plan.summary,
                "needs_input": plan.needs_input,
                "bootstrap_plan": str(paths.root / "bootstrap-plan.json"),
            }, indent=2, ensure_ascii=False))
        return 3

    plan_file = paths.root / "bootstrap-plan.json"
    bootstrap_record = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.exists() else {"bootstrap_attempts": []}
    last_attempt = bootstrap_record["bootstrap_attempts"][-1] if bootstrap_record["bootstrap_attempts"] else {}
    readiness = last_attempt.get("readiness")
    if getattr(args, "_human_output", False):
        print("[BananLoop] Evaluator readiness verified. Campaign state saved.")
        print(f"[BananLoop] Primary metric: {spec.primary_metric.name}")
        print(f"[BananLoop] State: {paths.root}")
    else:
        print(json.dumps({
            "status": "READY",
            "summary": plan.summary if plan else "Resuming reserved campaign",
            "primary_metric": spec.primary_metric.model_dump(mode="json"),
            "paired_comparison": spec.paired_comparison.model_dump(mode="json") if spec.paired_comparison else None,
            "preparation": {
                "attempts": len(bootstrap_record["bootstrap_attempts"]),
                "snapshot": last_attempt.get("snapshot"),
                "readiness": readiness,
            },
            "state": str(paths.root),
            "campaign_id": getattr(args, "campaign_id", None),
            "spec": spec.approved_hash(),
        }, indent=2, ensure_ascii=False))
    if args.prepare_only:
        return 0
    return cmd_run(args, resume=reused_campaign)


def _open(args: argparse.Namespace) -> tuple[RuntimePaths, CampaignStore, Engine]:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    store = CampaignStore(paths.db)
    spec = store.spec()
    workspace = WorkspaceManager(Path(spec.source_repo), paths.workspace)
    progress = lambda message: print(f"[BananLoop] {message}", flush=True)
    return paths, store, Engine(store, workspace, progress=progress)


def cmd_run(args: argparse.Namespace, resume: bool = False) -> int:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    try:
        with CampaignLock(paths.root / "campaign.lock"):
            _, store, engine = _open(args)
            try:
                campaign = engine.run(
                    resume=resume,
                    retry_running_actions=bool(getattr(args, "retry_running_action", False)),
                    retry_failed_actions=bool(getattr(args, "retry_failed_action", False)),
                    agent_timeout_seconds=getattr(args, "agent_timeout_seconds", None),
                )
                print(json.dumps({k: campaign[k] for k in ("status", "stop_reason", "stage", "experiment_count", "attempt_count", "agent_calls", "evaluator_calls", "champion_snapshot")}, indent=2))
                return 0 if campaign["status"] != "PAUSED_INFRA" else 2
            except ControlledInterruption as exc:
                print(str(exc), file=sys.stderr)
                return 130
            except KeyboardInterrupt:
                print("BananLoop interrupted by user. Active local agent process was cancelled.", file=sys.stderr)
                return 130
            except EngineError as exc:
                print(f"BananLoop error: {exc}", file=sys.stderr)
                return 2
            finally:
                store.close()
    except CampaignLockError as exc:
        print(f"BananLoop error: {exc}", file=sys.stderr)
        return 2


def cmd_continue(args: argparse.Namespace) -> int:
    """Continue the active campaign, including bootstrap before a DB exists."""
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args, create=False)
    if paths.db.exists():
        store = CampaignStore(paths.db)
        try:
            campaign = store.campaign()
            extension = max(0, int(getattr(args, "extend_budget", 0) or 0))
            if extension:
                if campaign["status"] != "STOPPED" or campaign.get("stop_reason") != "PLATEAU":
                    raise ValueError("--extend-budget is only supported for campaigns stopped by PLATEAU")
                old_spec = store.spec()
                budget = old_spec.budget.model_copy(update={
                    "max_attempts": old_spec.budget.max_attempts + extension,
                    "max_experiments": old_spec.budget.max_experiments + extension,
                    "max_agent_calls": old_spec.budget.max_agent_calls + extension * 2,
                    "max_evaluator_calls": old_spec.budget.max_evaluator_calls + extension * 2,
                    "plateau_experiments": old_spec.budget.plateau_experiments + extension,
                })
                now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
                with store.conn:
                    store.conn.execute(
                        "UPDATE campaigns SET status='RUNNING',stop_reason=NULL,updated_at=? WHERE id=1",
                        (now,),
                    )
                    store.event("PLATEAU_BUDGET_EXTENDED", {
                        "extension": extension,
                        "previous_budget": old_spec.budget.model_dump(mode="json"),
                        "new_budget": budget.model_dump(mode="json"),
                        "effective_budget": budget.model_dump(mode="json"),
                        "approved_spec_hash_unchanged": True,
                        "evaluator_binding_hash_unchanged": True,
                    })
                campaign = store.campaign()
            if getattr(args, "migrate_pytest_validation", False):
                old_spec = store.spec()
                failed_validation = store.action("baseline:validation")
                if (old_spec.regression_command or not old_spec.validation_command
                        or not any("pytest" in token.lower() for token in old_spec.validation_command)
                        or not failed_validation or failed_validation["status"] != "FAILED"
                        or not store.action("baseline:evaluate")
                        or store.action("baseline:evaluate")["status"] != "COMPLETED"):
                    raise ValueError("pytest migration requires a failed baseline:validation, a completed baseline:evaluate, and no existing regression command")
                new_spec = old_spec.model_copy(update={
                    "regression_command": old_spec.validation_command,
                    "regression_timeout_seconds": old_spec.validation_timeout_seconds,
                    "validation_command": [],
                    "validation_timeout_seconds": None,
                })
                if new_spec.evaluator_binding_hash() != old_spec.approved_hash():
                    raise ValueError("migration would alter the immutable baseline evaluator binding")
                now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
                with store.conn:
                    store.conn.execute("UPDATE campaigns SET spec_json=?,spec_hash=?,updated_at=? WHERE id=1",
                                       (new_spec.model_dump_json(), new_spec.approved_hash(), now))
                    store.conn.execute("UPDATE actions SET status='CANCELLED',error=?,updated_at=? WHERE id='baseline:validation'",
                                       ("superseded by explicit pytest regression-gate migration", now))
                    store.event("PYTEST_VALIDATION_MIGRATED_TO_REGRESSION", {
                        "action_id": "baseline:validation", "command": old_spec.validation_command,
                        "baseline_snapshot": campaign.get("baseline_snapshot"),
                        "new_spec_hash": new_spec.approved_hash(),
                    })
                campaign = store.campaign()
            actions = store.actions()
        finally:
            store.close()
        status = campaign["status"]
        print(f"[BananLoop] Existing campaign: {status}")
        if status in {"GOAL_MET", "STOPPED"}:
            if status == "STOPPED" and campaign.get("stop_reason") == "PLATEAU" and getattr(args, "extend_budget", 0):
                pass
            else:
                print("Campaign is finished. Run 'bananloop' to create another goal.")
                return 0
        failed = [action for action in actions if action["status"] == "FAILED"]
        running = [action for action in actions if action["status"] == "RUNNING"]
        if running and not getattr(args, "retry_running_action", False):
            print(
                "A previous action is still marked RUNNING. Verify the old process "
                "has exited, then use 'bananloop continue --retry-running-action'.",
                file=sys.stderr,
            )
            return 3
        retry_failed = bool(failed)
        if failed:
            last = failed[-1]
            print(f"[BananLoop] Last failed action: {last['id']}")
            print(f"[BananLoop] Failure: {str(last.get('error') or 'unknown')[:600]}")
            if not getattr(args, "yes", False):
                if not sys.stdin.isatty():
                    print(
                        "Retry requires confirmation. Use 'bananloop continue --yes' "
                        "after reviewing the failed action.",
                        file=sys.stderr,
                    )
                    return 3
                answer = input("Retry the failed action without restarting the campaign? [y/N] ").strip().lower()
                if answer not in {"y", "yes"}:
                    print("Campaign unchanged.")
                    return 3
        run_args = argparse.Namespace(
            repo=str(repo),
            state_dir=str(paths.root),
            campaign_id=None,
            retry_failed_action=retry_failed,
            retry_running_action=bool(getattr(args, "retry_running_action", False)),
            agent_timeout_seconds=None,
        )
        print("[BananLoop] Reusing completed actions and saved evaluations.")
        return cmd_run(run_args, resume=True)

    plan_file = paths.root / "bootstrap-plan.json"
    if not plan_file.is_file():
        print(
            "No campaign or saved bootstrap was found for this repository. "
            "Run 'bananloop' to create one.",
            file=sys.stderr,
        )
        return 2
    record = json.loads(plan_file.read_text(encoding="utf-8"))
    request = record.get("bootstrap_request") or {}
    goal = request.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise BootstrapError("saved bootstrap has no original goal; cannot recover safely")
    if record.get("source_base_commit") != current_commit(repo):
        raise BootstrapError(
            "repository HEAD differs from the saved bootstrap snapshot. "
            "Switch back to the original branch/commit to continue without rebuilding"
        )
    agent_payload = record.get("operator_agent_config") or request.get("agent")
    if not isinstance(agent_payload, dict):
        raise BootstrapError("saved bootstrap has no agent profile; cannot recover safely")
    config = AgentConfig.model_validate(agent_payload)
    start_args = parser().parse_args([
        "start", "--repo", str(repo), "--state-dir", str(paths.root),
        "--goal", goal, "--allow-dirty",
    ])
    start_args._bootstrap_agent_config = config
    start_args._human_output = True
    option_fields = {
        "agent_timeout_seconds": "bootstrap_timeout_seconds",
        "max_experiments": "max_experiments",
        "max_attempts": "max_attempts",
        "max_agent_calls": "max_agent_calls",
        "max_evaluator_calls": "max_evaluator_calls",
        "deadline_seconds": "deadline_seconds",
        "plateau_experiments": "plateau",
    }
    for saved_name, argument_name in option_fields.items():
        saved = (record.get("bootstrap_options") or {}).get(saved_name)
        if isinstance(saved, int) and not isinstance(saved, bool) and saved > 0:
            setattr(start_args, argument_name, saved)
    print("[BananLoop] Continuing saved bootstrap; original goal and evaluator snapshot retained.")
    return cmd_start(start_args)


def cmd_home(args: argparse.Namespace) -> int:
    """Human-first terminal entry point. Never create a campaign implicitly."""
    if not sys.stdin.isatty():
        print(
            "Interactive mode needs a terminal. Use 'bananloop continue' to resume "
            "or 'bananloop goal \"your goal\"' to start.",
            file=sys.stderr,
        )
        return 2
    repo = Path(args.repo).resolve()
    registry = CampaignRegistry(repo)
    entries = registry.list()
    active = next((entry for entry, is_active in entries if is_active), None)
    print(f"\\nBananLoop · {repo.name}")
    if active:
        paths = registry._validate_state_path(active.state_path, active.origin)
        if (paths / "campaign.sqlite3").exists():
            store = CampaignStore(paths / "campaign.sqlite3")
            try:
                status = store.campaign()["status"]
            finally:
                store.close()
        else:
            status = "BOOTSTRAP_PAUSED" if (paths / "bootstrap-plan.json").is_file() else "RESERVED"
        print(f"Active: {active.goal_summary}")
        print(f"State: {status}")
        choice = input("[Enter] continue  [n] new goal  [q] quit: ").strip().lower()
        if choice in {"", "c", "continue"}:
            return cmd_continue(argparse.Namespace(
                repo=str(repo), state_dir=None, campaign_id=active.campaign_id,
                yes=False, retry_running_action=False,
            ))
        if choice in {"q", "quit"}:
            return 0
        if choice not in {"n", "new"}:
            print("Unknown selection; no changes made.")
            return 2
    goal = input("What should BananLoop improve? ").strip()
    if not goal:
        print("No goal supplied; no campaign created.")
        return 0
    start_args = parser().parse_args(["goal", goal, "--repo", str(repo)])
    start_args._human_output = True
    print("[BananLoop] Preparing a reproducible campaign for this goal.")
    return cmd_start(start_args)


def cmd_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    store = CampaignStore(paths.db)
    try:
        c = store.campaign()
        data = {k: c[k] for k in ("status", "stop_reason", "stage", "baseline_snapshot", "champion_snapshot", "experiment_count", "attempt_count", "agent_calls", "evaluator_calls", "non_improve_streak", "created_at", "updated_at")}
        data["resources"] = store.resource_summary()
        data["evaluations"] = build_report(store)["measurements"]
        research = store.research_state()
        if research:
            data["planning_mode"] = research.get("planning_mode")
        print(json.dumps(data, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in data.items()))
    finally:
        store.close()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    store = CampaignStore(paths.db)
    try:
        store.campaign()  # Fail clearly if the state directory is uninitialized.
        store.request_stop()
        try:
            with CampaignLock(paths.root / "campaign.lock"):
                # No live owner: reconcile a stop without launching an agent.
                spec = store.spec()
                workspace = WorkspaceManager(Path(spec.source_repo), paths.workspace)
                Engine(store, workspace).run()
        except CampaignLockError:
            print("Stop requested. The running supervisor will cancel its local process and preserve the champion.")
            return 0
    finally:
        store.close()
    print("Campaign is terminal. Durable state and champion were preserved.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    store = CampaignStore(paths.db)
    try:
        report = build_report(store)
    finally:
        store.close()
    output = json.dumps(report, indent=2) if args.json else render_markdown(report)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(args.output)
    else:
        print(output)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _selected_paths(args)
    try:
        with CampaignLock(paths.root / "campaign.lock"):
            store = CampaignStore(paths.db)
            try:
                output = export_champion(store, paths.workspace, Path(args.output))
            finally:
                store.close()
    except CampaignLockError as exc:
        raise StoreError("stop or pause the campaign before exporting its champion") from exc
    print(f"Champion patch, manifest and operator reports exported to {output}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    paths = _selected_paths(args, create=False)
    try:
        with CampaignLock(paths.root / "campaign.lock"):
            store = CampaignStore(paths.db)
            try:
                spec = store.spec()
                workspace = WorkspaceManager(Path(spec.source_repo), paths.workspace)
                result = replay_dev_action(store, workspace, args.action)
            finally:
                store.close()
    except CampaignLockError as exc:
        result = {
            "status": "NOT_REPRODUCIBLE_INFRA",
            "reason": f"campaign lock is held: {exc}",
            "source_action_id": args.action,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") == "REPRODUCIBLE":
        return 0
    if result.get("status") == "REPLAY_MISMATCH":
        return 1
    return 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bananloop")
    sub = p.add_subparsers(dest="command", required=True)

    home = sub.add_parser("home", help="guided terminal interface for the active project")
    home.add_argument("--repo", default=".")
    home.set_defaults(func=cmd_home)

    continue_cmd = sub.add_parser("continue", help="resume the active campaign or paused bootstrap without repeating the goal")
    continue_cmd.add_argument("--repo", default=".")
    continue_cmd.add_argument("--campaign-id")
    continue_cmd.add_argument("--state-dir")
    continue_cmd.add_argument("--yes", action="store_true", help="approve retry of known FAILED actions")
    continue_cmd.add_argument("--extend-budget", type=int, default=0, metavar="N", help="resume a PLATEAU campaign with N more attempts, experiments, and plateau tolerance")
    continue_cmd.add_argument("--migrate-pytest-validation", action="store_true", help="move a failed pytest JSON validation command to an exit-code regression gate, preserving the completed baseline")
    continue_cmd.add_argument("--retry-running-action", action="store_true", help="confirm an orphaned RUNNING action after verifying the old process has exited")
    continue_cmd.set_defaults(func=cmd_continue)

    goal = sub.add_parser(
        "goal",
        help="chat-first autopilot: repo + one human goal",
    )
    goal.add_argument(
        "goal",
        help="human-language improvement goal",
    )
    goal.add_argument("--repo", default=".")
    goal.add_argument("--state-dir")
    goal.add_argument("--request-id", help="idempotency key; use a new value to intentionally repeat a goal")
    goal.add_argument("--agent-profiles", help="TOML profile file for default and role-specific agent settings")
    goal.add_argument(
        "--prepare-only",
        action="store_true",
        help="prepare and freeze the evaluator contract without starting optimization",
    )
    goal.set_defaults(
        func=cmd_start,
        agent=None,
        agent_cmd=None,
        model=None,
        provider=None,
        thinking=None,
        codex_arg=None,
        pi_arg=None,
        bootstrap_timeout_seconds=1200,
        agent_timeout_seconds=None,
        max_experiments=4,
        max_attempts=7,
        max_agent_calls=18,
        max_evaluator_calls=16,
        deadline_seconds=14400,
        plateau=3,
        allow_dirty=False,
    )

    doctor = sub.add_parser(
        "doctor",
        help="show the environment BananLoop will use before bootstrap",
    )
    doctor.add_argument("--repo", default=".")
    doctor.add_argument("--state-dir")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    codex_hook = sub.add_parser("codex-hook", help=argparse.SUPPRESS)
    codex_hook.set_defaults(func=cmd_codex_hook)

    start = sub.add_parser(
        "start",
        help="autonomously turn repo + goal into a campaign and run it",
    )
    start.add_argument("--repo", default=".")
    start.add_argument("--state-dir")
    start.add_argument("--request-id", help="idempotency key; use a new value to intentionally repeat a goal")
    start.add_argument("--goal", required=True)
    start.add_argument("--agent", choices=["codex", "command", "pi"])
    start.add_argument("--agent-cmd")
    start.add_argument("--model")
    start.add_argument("--agent-profiles", help="TOML profile file for default and role-specific agent settings")
    start.add_argument("--provider", help="Pi provider, e.g. ollama-cloud")
    start.add_argument(
        "--thinking",
        choices=["off", "minimal", "low", "medium", "high", "xhigh", "max"],
        help="Pi thinking level",
    )
    start.add_argument("--codex-arg", action="append")
    start.add_argument("--pi-arg", action="append")
    start.add_argument(
        "--bootstrap-timeout-seconds",
        type=int,
        default=1200,
        help="maximum time for repository discovery and evaluator construction",
    )
    start.add_argument(
        "--agent-timeout-seconds",
        type=int,
        help="override per-agent-call wall time during the optimization loop",
    )
    start.add_argument("--max-experiments", type=int, default=4)
    start.add_argument("--max-attempts", type=int, default=7)
    start.add_argument("--max-agent-calls", type=int, default=18)
    start.add_argument("--max-evaluator-calls", type=int, default=16)
    start.add_argument("--deadline-seconds", type=int, default=14400)
    start.add_argument("--plateau", type=int, default=3)
    start.add_argument("--allow-dirty", action="store_true")
    start.add_argument(
        "--prepare-only",
        action="store_true",
        help="build and persist CampaignSpec but do not start optimization",
    )
    start.set_defaults(func=cmd_start)

    init = sub.add_parser("init", help="approve and persist a campaign specification")
    init.add_argument("--repo", default=".")
    init.add_argument("--state-dir")
    init.add_argument("--goal")
    init.add_argument(
        "--eval-cmd",
        help="development evaluator command; runs on baseline and every candidate",
    )
    init.add_argument(
        "--validation-eval-cmd",
        help="blind validation evaluator; runs on baseline and dev-accepted candidates only",
    )
    init.add_argument(
        "--holdout-eval-cmd",
        help="final-only holdout evaluator; runs once on the terminal champion",
    )
    init.add_argument(
        "--evaluator-timeout-seconds",
        type=int,
        default=900,
        help="maximum wall time for each evaluator invocation (default: 900)",
    )
    init.add_argument(
        "--validation-timeout-seconds",
        type=int,
        help="wall time for validation evaluator; defaults to --evaluator-timeout-seconds",
    )
    init.add_argument(
        "--holdout-timeout-seconds",
        type=int,
        help="wall time for final holdout evaluator; defaults to --evaluator-timeout-seconds",
    )
    init.add_argument(
        "--eval-asset",
        action="append",
        help="additional evaluator/scoring/config file to freeze; repeatable",
    )
    init.add_argument(
        "--agent-hide",
        action="append",
        help="path/glob hidden from the agent workspace; repeatable",
    )
    init.add_argument(
        "--agent-metric",
        action="append",
        help="metric name visible to the agent in baseline/champion/history; repeatable",
    )
    init.add_argument(
        "--agent-diagnostic",
        action="append",
        help="aggregate DEV diagnostic key visible to adaptive planning; repeatable",
    )
    init.add_argument("--metric")
    init.add_argument("--direction", choices=["maximize", "minimize"])
    init.add_argument("--target", type=float)
    init.add_argument("--min-improvement", type=float)
    init.add_argument("--unit")
    init.add_argument("--metric-min", type=float)
    init.add_argument("--metric-max", type=float)
    init.add_argument("--guard", type=_guard, action="append")
    init.add_argument(
        "--non-regression",
        type=_non_regression,
        action="append",
        help="require candidate metric not to regress vs champion; metric:minimize|maximize[:tolerance]",
    )
    init.add_argument("--check", action="append")
    init.add_argument("--editable", action="append")
    init.add_argument("--protect", action="append")
    init.add_argument(
        "--forbid-diff-regex",
        action="append",
        help=(
            "reject a candidate before evaluation when a newly added diff line "
            "matches this regex; repeatable anti-gaming/leakage guard"
        ),
    )
    init.add_argument("--agent", choices=["codex", "command", "pi"])
    init.add_argument("--agent-cmd")
    init.add_argument("--model")
    init.add_argument("--agent-profiles", help="TOML profile file for default and role-specific agent settings")
    init.add_argument("--provider", help="Pi provider, e.g. ollama-cloud")
    init.add_argument(
        "--thinking",
        choices=["off", "minimal", "low", "medium", "high", "xhigh", "max"],
        help="Pi thinking level",
    )
    init.add_argument("--codex-arg", action="append")
    init.add_argument("--pi-arg", action="append")
    init.add_argument("--paired-policy", help="JSON file with domain-neutral paired comparison policy")
    init.add_argument("--max-evaluation-units", type=int)
    init.add_argument("--evaluation-units-per-call", type=int)
    init.add_argument("--max-experiments", type=int, default=5)
    init.add_argument("--max-attempts", type=int, default=8)
    init.add_argument("--max-agent-calls", type=int, default=20)
    init.add_argument("--max-evaluator-calls", type=int, default=10)
    init.add_argument("--deadline-seconds", type=int, default=3600)
    init.add_argument("--plateau", type=int, default=3)
    init.add_argument("--allow-dirty", action="store_true")
    init.add_argument("--interactive", action="store_true", help="run the interactive init wizard")
    init.add_argument("--yes", action="store_true", help="approve an interactive CampaignSpec without confirmation")
    init.set_defaults(func=cmd_init)

    for name in ("run", "resume", "status", "stop", "report", "export"):
        sp = sub.add_parser(name)
        sp.add_argument("--repo", default=".")
        sp.add_argument("--state-dir")
        sp.add_argument("--campaign-id")
        if name in {"run", "resume"}:
            sp.add_argument(
                "--agent-timeout-seconds",
                type=int,
                help="override per-agent-call wall time for this run/resume",
            )
        if name == "status":
            sp.add_argument("--json", action="store_true")
            sp.set_defaults(func=cmd_status)
        elif name == "stop":
            sp.set_defaults(func=cmd_stop)
        elif name == "report":
            sp.add_argument("--json", action="store_true")
            sp.add_argument("--output")
            sp.set_defaults(func=cmd_report)
        elif name == "export":
            sp.add_argument("--output", required=True, help="new directory for champion.patch, manifest and reports")
            sp.set_defaults(func=cmd_export)
        elif name == "resume":
            sp.add_argument(
                "--retry-running-action",
                action="store_true",
                help="retry an orphaned RUNNING action after confirming the previous local process is gone",
            )
            sp.add_argument(
                "--retry-failed-action",
                action="store_true",
                help="retry a known FAILED action; consumes a new action reservation/call",
            )
            sp.set_defaults(func=lambda a: cmd_run(a, resume=True))
        else:
            sp.set_defaults(func=lambda a: cmd_run(a, resume=False))

    replay = sub.add_parser("replay", help="re-run a stored DEV evaluation in a disposable frozen workspace")
    replay.add_argument("--repo", default=".")
    replay.add_argument("--state-dir")
    replay.add_argument("--campaign-id")
    replay.add_argument("--action", required=True, help="DEV action id or bootstrap:attempt:N:smoke:N")
    replay.set_defaults(func=cmd_replay)

    campaigns = sub.add_parser("campaigns", help="list repository campaigns")
    campaign_sub = campaigns.add_subparsers(dest="campaign_command", required=True)
    list_cmd = campaign_sub.add_parser("list", help="list indexed campaigns")
    list_cmd.add_argument("--repo", default=".")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_campaigns_list)
    return p


def main() -> None:
    try:
        code = parser().parse_args(["home"] if len(sys.argv) == 1 else None)
        raise SystemExit(code.func(code))
    except (BootstrapError, StoreError, WorkspaceError, EvaluationIntegrityError, RegistryError, CampaignLockError, ValueError) as exc:
        print(f"BananLoop error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
