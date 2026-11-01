"""Run a deterministic CLI pause/resume lifecycle demo without model calls."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _write_slow_first_proposal(agent_path: Path, root: Path, *, first_sleep: int = 60) -> None:
    ready = root / "first-agent-ready"
    calls = root / "agent-calls"
    survived = root / "agent-survived-interrupt"
    agent_path.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        f"ready=Path({str(ready)!r})\n"
        f"calls=Path({str(calls)!r})\n"
        f"survived=Path({str(survived)!r})\n"
        "req=json.load(sys.stdin)\n"
        "n=int(calls.read_text())+1 if calls.exists() else 1\n"
        "calls.write_text(str(n))\n"
        "if req['role'] == 'propose':\n"
        "    if n == 1:\n"
        "        ready.write_text('ready')\n"
        f"        time.sleep({first_sleep})\n"
        "        survived.write_text('child survived interrupt')\n"
        "    idx=len(req.get('history', []))+1\n"
        "    print(json.dumps({'hypothesis':f'fixture step {idx}',"
        "'rationale':'pause/resume lifecycle fixture',"
        "'expected_effect':'quality should increase',"
        "'files_or_area':['value.txt','guard.txt'],"
        "'falsification_condition':'quality does not improve or regressions exceeds zero',"
        "'mechanism_key':f'fixture-step-{idx}'}))\n"
        "else:\n"
        "    hypothesis=req['hypothesis']['mechanism_key']\n"
        "    if hypothesis == 'fixture-step-1':\n"
        "        Path('value.txt').write_text('1\\n'); Path('guard.txt').write_text('0\\n')\n"
        "    elif hypothesis == 'fixture-step-2':\n"
        "        Path('value.txt').write_text('2\\n'); Path('guard.txt').write_text('1\\n')\n"
        "    else:\n"
        "        Path('value.txt').write_text('3\\n'); Path('guard.txt').write_text('0\\n')\n"
        "    print(json.dumps({'summary':hypothesis,"
        "'changed_paths':['value.txt','guard.txt'],"
        "'verification_notes':'lifecycle fixture','blockers':[]}))\n",
        encoding="utf-8",
    )


def _run_cli(
    args: list[str], *, repo: Path, env: dict[str, str], log_dir: Path, name: str,
    timeout: int = 30,
) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "bananloop.cli", *args], cwd=repo, env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    (log_dir / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout


def run(output_dir: Path, *, sigint: bool = False) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=False)
    repo = output_dir / "fixture"
    state = output_dir / "state"
    logs = output_dir / "logs"
    logs.mkdir()
    py_source = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_source) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("create_fixture.py")), str(repo)],
        check=True, capture_output=True, text=True,
    )
    agent = output_dir / "lifecycle_agent.py"
    _write_slow_first_proposal(agent, output_dir, first_sleep=3 if sigint else 60)
    cli = ["--repo", str(repo), "--state-dir", str(state)]
    init_args = [
        "init", *cli, "--goal", "Raise quality to 3 while regressions stay at zero",
        "--eval-cmd", "python evaluator.py", "--metric", "quality", "--direction", "maximize",
        "--target", "3", "--min-improvement", "0.5", "--guard", "regressions<=0",
        "--check", "tests", "--editable", "value.txt", "--editable", "guard.txt",
        "--protect", "evaluator.py", "--protect", "agent.py", "--agent", "command",
        "--agent-cmd", shlex.join([sys.executable, str(agent)]), "--max-experiments", "5",
        "--max-attempts", "6", "--max-agent-calls", "12", "--max-evaluator-calls", "8",
    ]
    _run_cli(init_args, repo=repo, env=env, log_dir=logs, name="init")

    process = subprocess.Popen(
        [sys.executable, "-m", "bananloop.cli", "run", *cli], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
           else {"start_new_session": True}),
    )
    ready = output_dir / "first-agent-ready"
    deadline = time.monotonic() + 15
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not ready.exists():
        stdout, stderr = process.communicate(timeout=5)
        raise RuntimeError(f"agent did not start; stdout={stdout!r}; stderr={stderr!r}")

    if sigint:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        pause_output = "SIGINT sent to active CLI process"
    else:
        pause_output = _run_cli(["pause", *cli], repo=repo, env=env, log_dir=logs, name="pause")
    stdout, stderr = process.communicate(timeout=10)
    (logs / "interrupted-run.stdout.txt").write_text(stdout, encoding="utf-8")
    (logs / "interrupted-run.stderr.txt").write_text(stderr, encoding="utf-8")
    expected_returncode = 130 if sigint else 0
    if process.returncode != expected_returncode:
        raise RuntimeError(f"run after interruption exited {process.returncode}: {stderr[-2000:]}")
    if sigint:
        if "interrupted by user" not in stderr.lower():
            raise RuntimeError("SIGINT did not produce the CLI interruption message")
        time.sleep(3.2)
        if (output_dir / "agent-survived-interrupt").exists():
            raise RuntimeError("the cancelled agent subprocess survived SIGINT")
        with sqlite3.connect(state / "campaign.sqlite3") as conn:
            cancelled = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload_json FROM events WHERE event_type='ACTION_CANCELLED'"
            )]
        if not any(row.get("action_id") == "exp:1:propose" for row in cancelled):
            raise RuntimeError("SIGINT did not persist cancellation for the active agent action")

    paused_status = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="paused-status")
    expected_pause = ("status: PAUSED_INFRA", "stop_reason: PAUSED_INFRA") if sigint else (
        "status: PAUSED_USER", "stop_reason: USER_PAUSED",
    )
    if any(value not in paused_status for value in expected_pause):
        raise RuntimeError("interruption did not persist the expected resumable campaign state")
    _run_cli(["resume", *cli], repo=repo, env=env, log_dir=logs, name="resume", timeout=60)
    status = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="status")
    report = _run_cli(["report", *cli], repo=repo, env=env, log_dir=logs, name="report")
    if "status: GOAL_MET" not in status or "Final assessment: **DEV_ONLY**" not in report:
        raise RuntimeError("fixture did not finish with the expected DEV-only campaign report")
    values = {
        key: int(next(line.split(":", 1)[1].strip() for line in status.splitlines()
                      if line.startswith(f"{label}:")))
        for key, label in (
            ("experiments", "experiment_count"),
            ("agent_calls", "agent_calls"),
            ("evaluator_calls", "evaluator_calls"),
        )
    }
    if values != {"experiments": 3, "agent_calls": 7, "evaluator_calls": 4}:
        raise RuntimeError(f"unexpected campaign counters: {values}")

    summary: dict[str, object] = {
        "pause_output": pause_output.strip(),
        "interruption": "SIGINT" if sigint else "typed_pause",
        "paused_status": "PAUSED_INFRA" if sigint else "PAUSED_USER",
        "status": "GOAL_MET",
        **values,
        "champion": "0.0 -> 3.0",
        "final_assessment": "DEV_ONLY",
        "model_calls": 0,
        "holdout_configured": False,
        "fixture_repo": str(repo),
        "state_dir": str(state),
        "logs": str(logs),
    }
    (output_dir / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        help="new directory for the disposable repo, campaign state and logs (defaults to a new temp directory)",
    )
    parser.add_argument(
        "--sigint", action="store_true",
        help="send a real SIGINT/CTRL_BREAK to the active CLI subprocess, then resume",
    )
    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="bananloop-lifecycle-"))
    print(json.dumps(run(output_dir, sigint=args.sigint), indent=2))


if __name__ == "__main__":
    main()
