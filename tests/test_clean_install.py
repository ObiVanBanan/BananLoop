from __future__ import annotations

import os
import shlex
import subprocess
import sys
import venv
from pathlib import Path

import pytest


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_wheel_installs_in_clean_venv_and_runs_cpu_fixture(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    build = _run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheelhouse)],
        cwd=repo_root,
        env=env,
        timeout=240,
    )
    if build.returncode and any(
        marker in build.stderr
        for marker in ("NameResolutionError", "Temporary failure in name resolution")
    ):
        pytest.skip("network is unavailable for setuptools build isolation in this environment")
    assert build.returncode == 0, build.stderr
    wheels = list(wheelhouse.glob("bananloop-*.whl"))
    assert len(wheels) == 1

    clean_env = tmp_path / "clean-venv"
    venv.EnvBuilder(with_pip=True).create(clean_env)
    interpreter = clean_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = _run(
        [str(interpreter), "-m", "pip", "install", str(wheels[0])],
        cwd=tmp_path,
        env=env,
        timeout=240,
    )
    assert install.returncode == 0, install.stderr

    help_result = _run(
        [str(interpreter), "-m", "bananloop.cli", "--help"],
        cwd=tmp_path,
        env=env,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "autonomously turn repo + goal" in help_result.stdout

    fixture = tmp_path / "fixture-project"
    create = _run(
        [str(interpreter), str(repo_root / "examples" / "fixture" / "create_fixture.py"), str(fixture)],
        cwd=tmp_path,
        env=env,
    )
    assert create.returncode == 0, create.stderr

    state = tmp_path / "campaign-state"
    cli = [str(interpreter), "-m", "bananloop.cli"]
    paths = ["--repo", str(fixture), "--state-dir", str(state)]
    init = _run(
        [
            *cli, "init", *paths,
            "--goal", "Raise quality to 3 while regressions stay at zero",
            "--eval-cmd", shlex.join([str(interpreter), "evaluator.py"]),
            "--metric", "quality", "--direction", "maximize", "--target", "3",
            "--min-improvement", "0.5", "--guard", "regressions<=0", "--check", "tests",
            "--editable", "value.txt", "--editable", "guard.txt",
            "--protect", "evaluator.py", "--protect", "agent.py",
            "--agent", "command", "--agent-cmd", shlex.join([str(interpreter), "agent.py"]),
            "--max-experiments", "5", "--max-attempts", "6", "--max-agent-calls", "12",
            "--max-evaluator-calls", "8",
        ],
        cwd=fixture,
        env=env,
    )
    assert init.returncode == 0, init.stderr

    run = _run([*cli, "run", *paths], cwd=fixture, env=env)
    assert run.returncode == 0, run.stderr
    assert '"status": "GOAL_MET"' in run.stdout

    report = _run([*cli, "report", *paths], cwd=fixture, env=env)
    assert report.returncode == 0, report.stderr
    assert "Final assessment: **DEV_ONLY**" in report.stdout
    report.stdout.encode("cp1252")
    assert "Baseline -> final:" in report.stdout
