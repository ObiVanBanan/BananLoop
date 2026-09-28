from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .bootstrap import BootstrapError, bootstrap_campaign
from .contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, GuardrailSpec, MetricSpec, NonRegressionSpec, PairedComparison, RuntimePaths
from .engine import ControlledInterruption, Engine, EngineError
from .export import export_champion
from .integrity import EvaluationIntegrityError, discover_evaluation_assets, evaluation_fingerprint
from .lock import CampaignLock, CampaignLockError
from .report import build_report, render_markdown
from .store import CampaignStore, StoreError
from .wizard import confirm_spec, needs_interactive_init, run_init_wizard
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
    return paths


def _agent_config(args: argparse.Namespace) -> AgentConfig:
    extra_args: list[str] = []
    if args.agent == "codex":
        extra_args = args.codex_arg or []
    elif args.agent == "pi":
        if args.provider:
            extra_args += ["--provider", args.provider]
        if args.thinking:
            extra_args += ["--thinking", args.thinking]
        extra_args += args.pi_arg or []
    return AgentConfig(
        kind=args.agent,
        command=shlex.split(args.agent_cmd) if args.agent_cmd else [],
        model=args.model,
        extra_args=extra_args,
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
    if args.agent is None:
        args.agent = "codex"

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


def cmd_start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not args.allow_dirty:
        assert_clean_source(repo)
    if args.agent is None:
        args.agent = "codex"

    paths = _paths(repo, args.state_dir)
    plan, spec = bootstrap_campaign(
        repo=repo,
        goal=args.goal,
        paths=paths,
        agent_config=_agent_config(args),
        agent_timeout_seconds=args.bootstrap_timeout_seconds,
        max_experiments=args.max_experiments,
        max_attempts=args.max_attempts,
        max_agent_calls=args.max_agent_calls,
        max_evaluator_calls=args.max_evaluator_calls,
        deadline_seconds=args.deadline_seconds,
        plateau_experiments=args.plateau,
    )
    if spec is None:
        print(json.dumps({
            "status": "PAUSED_NEEDS_INPUT",
            "summary": plan.summary,
            "needs_input": plan.needs_input,
            "bootstrap_plan": str(paths.root / "bootstrap-plan.json"),
        }, indent=2, ensure_ascii=False))
        return 3

    print(json.dumps({
        "status": "READY",
        "summary": plan.summary,
        "primary_metric": spec.primary_metric.model_dump(mode="json"),
        "paired_comparison": spec.paired_comparison.model_dump(mode="json") if spec.paired_comparison else None,
        "state": str(paths.root),
        "spec": spec.approved_hash(),
    }, indent=2, ensure_ascii=False))
    if args.prepare_only:
        return 0
    return cmd_run(args, resume=False)


def _open(args: argparse.Namespace) -> tuple[RuntimePaths, CampaignStore, Engine]:
    repo = Path(args.repo).resolve()
    paths = _paths(repo, args.state_dir)
    store = CampaignStore(paths.db)
    spec = store.spec()
    workspace = WorkspaceManager(Path(spec.source_repo), paths.workspace)
    progress = lambda message: print(f"[BananLoop] {message}", flush=True)
    return paths, store, Engine(store, workspace, progress=progress)


def cmd_run(args: argparse.Namespace, resume: bool = False) -> int:
    repo = Path(args.repo).resolve()
    paths = _paths(repo, args.state_dir)
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


def cmd_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _paths(repo, args.state_dir)
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
    paths = _paths(repo, args.state_dir)
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
    paths = _paths(repo, args.state_dir)
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
    paths = _paths(repo, args.state_dir)
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bananloop")
    sub = p.add_subparsers(dest="command", required=True)
    start = sub.add_parser(
        "start",
        help="autonomously turn repo + goal into a campaign and run it",
    )
    start.add_argument("--repo", default=".")
    start.add_argument("--state-dir")
    start.add_argument("--goal", required=True)
    start.add_argument("--agent", choices=["codex", "command", "pi"], default="codex")
    start.add_argument("--agent-cmd")
    start.add_argument("--model")
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
    return p


def main() -> None:
    try:
        code = parser().parse_args()
        raise SystemExit(code.func(code))
    except (BootstrapError, StoreError, WorkspaceError, EvaluationIntegrityError, ValueError) as exc:
        print(f"BananLoop error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
