from __future__ import annotations

import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable


def discover_project(repo: Path) -> dict[str, Any]:
    commands: list[str] = []
    if (repo / "pyproject.toml").exists() or (repo / "pytest.ini").exists() or (repo / "tests").is_dir():
        commands.append("python -m pytest -q")
    if (repo / "package.json").exists():
        commands.append("npm test")
    if (repo / "Makefile").exists():
        commands.append("make test")
    if (repo / "tox.ini").exists():
        commands.append("tox")

    agents = [name for name in ("codex", "pi") if shutil.which(name)]
    return {
        "test_commands": list(dict.fromkeys(commands)),
        "agents": agents,
    }


def _ask(
    label: str,
    current: Any,
    *,
    input_fn: Callable[[str], str],
    cast: Callable[[str], Any] | None = None,
    default: Any = None,
) -> Any:
    if current is not None:
        return current
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input_fn(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if not raw:
            print("Value is required.")
            continue
        if cast is None:
            return raw
        try:
            return cast(raw)
        except (TypeError, ValueError):
            print(f"Invalid value: {raw!r}")


def needs_interactive_init(args: Namespace) -> bool:
    required = ("goal", "eval_cmd", "metric", "target", "min_improvement")
    return bool(getattr(args, "interactive", False) or any(getattr(args, name, None) is None for name in required))


def run_init_wizard(
    args: Namespace,
    repo: Path,
    base_commit: str,
    *,
    input_fn: Callable[[str], str] = input,
) -> bool:
    discovery = discover_project(repo)
    print("BananLoop init")
    print(f"repo: {repo}")
    print(f"commit: {base_commit}")
    print(f"detected agents: {', '.join(discovery['agents']) or '-'}")
    print(f"detected test commands: {', '.join(discovery['test_commands']) or '-'}")
    print("Evaluator must emit BananLoop JSON metrics; detected test commands are suggestions only.")

    args.goal = _ask("Goal", getattr(args, "goal", None), input_fn=input_fn)
    args.eval_cmd = _ask("Evaluator command", getattr(args, "eval_cmd", None), input_fn=input_fn)
    args.metric = _ask("Primary metric", getattr(args, "metric", None), input_fn=input_fn)
    args.direction = _ask(
        "Direction (maximize/minimize)",
        getattr(args, "direction", None),
        input_fn=input_fn,
        default="maximize",
    )
    args.target = _ask("Target", getattr(args, "target", None), input_fn=input_fn, cast=float)
    args.min_improvement = _ask(
        "Minimum improvement",
        getattr(args, "min_improvement", None),
        input_fn=input_fn,
        cast=float,
    )

    if getattr(args, "agent", None) is None:
        default_agent = discovery["agents"][0] if discovery["agents"] else "codex"
        args.agent = _ask(
            "Agent (codex/pi/command)",
            None,
            input_fn=input_fn,
            default=default_agent,
        )
    if args.agent == "command" and not getattr(args, "agent_cmd", None):
        args.agent_cmd = _ask("Agent wrapper command", None, input_fn=input_fn)

    return True


def confirm_spec(spec_payload: dict[str, Any], *, input_fn: Callable[[str], str] = input) -> bool:
    print("\nApproved CampaignSpec preview:")
    print(json.dumps(spec_payload, indent=2, ensure_ascii=False))
    answer = input_fn("Approve this campaign? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}
