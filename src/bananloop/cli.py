from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, GuardrailSpec, MetricSpec, RuntimePaths
from .engine import ControlledInterruption, Engine, EngineError
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
    evaluation_assets = discover_evaluation_assets(repo, evaluation_command, args.eval_asset)
    frozen_paths = [asset.path for asset in evaluation_assets]
    protected_paths = list(dict.fromkeys([*(args.protect or []), *frozen_paths]))
    spec = CampaignSpec(
        goal=args.goal,
        source_repo=str(repo),
        base_commit=base,
        evaluation_command=evaluation_command,
        evaluator_timeout_seconds=args.evaluator_timeout_seconds,
        evaluation_assets=evaluation_assets,
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
        mandatory_checks=args.check or [],
        editable_scope=args.editable or ["**"],
        protected_paths=protected_paths,
        budget=BudgetSpec(
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
        print(json.dumps(data, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in data.items()))
    finally:
        store.close()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    paths = _paths(repo, args.state_dir)
    store = CampaignStore(paths.db)
    try:
        store.update_campaign(status="STOPPED", stop_reason="USER_STOPPED", stage=None)
        store.event("USER_STOPPED", {})
        store.conn.commit()
    finally:
        store.close()
    print("Campaign stopped. Durable state and champion were preserved.")
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bananloop")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="approve and persist a campaign specification")
    init.add_argument("--repo", default=".")
    init.add_argument("--state-dir")
    init.add_argument("--goal")
    init.add_argument("--eval-cmd")
    init.add_argument(
        "--evaluator-timeout-seconds",
        type=int,
        default=900,
        help="maximum wall time for each evaluator invocation (default: 900)",
    )
    init.add_argument(
        "--eval-asset",
        action="append",
        help="additional evaluator/scoring/config file to freeze; repeatable",
    )
    init.add_argument("--metric")
    init.add_argument("--direction", choices=["maximize", "minimize"])
    init.add_argument("--target", type=float)
    init.add_argument("--min-improvement", type=float)
    init.add_argument("--unit")
    init.add_argument("--metric-min", type=float)
    init.add_argument("--metric-max", type=float)
    init.add_argument("--guard", type=_guard, action="append")
    init.add_argument("--check", action="append")
    init.add_argument("--editable", action="append")
    init.add_argument("--protect", action="append")
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

    for name in ("run", "resume", "status", "stop", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--repo", default=".")
        sp.add_argument("--state-dir")
        if name == "status":
            sp.add_argument("--json", action="store_true")
            sp.set_defaults(func=cmd_status)
        elif name == "stop":
            sp.set_defaults(func=cmd_stop)
        elif name == "report":
            sp.add_argument("--json", action="store_true")
            sp.add_argument("--output")
            sp.set_defaults(func=cmd_report)
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
    except (StoreError, WorkspaceError, EvaluationIntegrityError, ValueError) as exc:
        print(f"BananLoop error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
