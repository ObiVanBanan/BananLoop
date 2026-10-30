from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

from .envfiles import env_keys_from_repo


def _python_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "-c", "import platform; print(platform.python_version())"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    version = result.stdout.strip()
    return version or None


def _parse_py_launcher_paths(output: str) -> list[str]:
    paths: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        # py -0p typically prints "-V:3.13 * C:\\Python313\\python.exe";
        # keep everything from the first absolute drive/UNC path onward.
        drive = next(
            (
                index
                for index in range(len(line) - 2)
                if line[index].isalpha()
                and line[index + 1] == ":"
                and line[index + 2] in {"\\", "/"}
            ),
            None,
        )
        if drive is not None:
            paths.append(line[drive:].strip())
            continue
        if line.startswith("\\\\"):
            paths.append(line)
    return paths


def discover_python_runtimes(repo: Path) -> list[dict[str, str]]:
    candidates: list[tuple[str, str]] = []

    def add(value: str | Path | None, source: str) -> None:
        if not value:
            return
        path = str(value)
        if not any(existing == path for existing, _ in candidates):
            candidates.append((path, source))

    # Project-local environments come first: a host/system Python proving that
    # imports fail says nothing about the target project's installed runtime.
    for relative in (
        ".venv/Scripts/python.exe",
        "venv/Scripts/python.exe",
        ".venv/bin/python",
        "venv/bin/python",
    ):
        path = repo / relative
        if path.is_file():
            add(path.resolve(), "project")

    add(sys.executable, "host")
    for name in ("python", "python3"):
        add(shutil.which(name), "host")

    if os.name == "nt":
        py = shutil.which("py")
        if py:
            try:
                result = subprocess.run(
                    [py, "-0p"],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    for path in _parse_py_launcher_paths(result.stdout):
                        add(path, "host")
            except (OSError, subprocess.SubprocessError):
                pass

        system_drive = os.environ.get("SystemDrive", "C:")
        root = Path(system_drive + "\\")
        try:
            for path in sorted(root.glob("Python3*/python.exe")):
                if path.is_file():
                    add(path, "host")
        except OSError:
            pass

    runtimes: list[dict[str, str]] = []
    for executable, source in candidates:
        version = _python_version(executable)
        if version:
            runtimes.append({
                "executable": executable,
                "version": version,
                "source": source,
            })
    return runtimes


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
    ignored_dirs = {
        ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tmp",
        "build", "dist", "target",
    }
    resources: dict[str, list[str]] = {
        "env_files": [],
        "csv_files": [],
        "compose_files": [],
        "env_keys": [],
        "benchmark_files": [],
        "history_files": [],
        "config_files": [],
        "readme_files": [],
    }
    try:
        for current, dirs, files in os.walk(repo, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(repo).parts)
            dirs[:] = sorted(
                name for name in dirs
                if name not in ignored_dirs and not name.startswith(".bananloop")
            ) if depth < 4 else []
            for filename in files:
                path = current_path / filename
                relative = path.relative_to(repo).as_posix()
                lower = filename.lower()
                if lower == ".env" or lower.startswith(".env."):
                    category = "env_files"
                elif lower.endswith(".csv"):
                    category = "csv_files"
                elif lower in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
                    category = "compose_files"
                elif "readme" in lower and path.suffix.lower() in {".md", ".rst", ".txt"}:
                    category = "readme_files"
                elif any(token in lower for token in ("benchmark", "bench", "evaluator", "eval_", "score")):
                    category = "benchmark_files"
                elif (
                    path.suffix.lower() in {".md", ".json", ".jsonl", ".txt", ".yaml", ".yml"}
                    and any(token in lower for token in ("experiment", "history", "journal", "decision", "campaign"))
                ):
                    category = "history_files"
                elif path.suffix.lower() in {".yaml", ".yml", ".toml", ".ini"} or "config" in lower:
                    category = "config_files"
                else:
                    continue
                resources[category].append(relative)
    except OSError:
        # Discovery is advisory. An unreadable subtree must not prevent a campaign.
        pass
    resources["env_keys"] = env_keys_from_repo(repo)
    for paths in resources.values():
        paths.sort()
    runtimes = discover_python_runtimes(repo)
    preferred = next(
        (runtime for runtime in runtimes if runtime.get("source") == "project"),
        runtimes[0] if runtimes else None,
    )
    return {
        "test_commands": list(dict.fromkeys(commands)),
        "agents": agents,
        "python_runtimes": runtimes,
        "preferred_python_runtime": preferred,
        "resources": resources,
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
