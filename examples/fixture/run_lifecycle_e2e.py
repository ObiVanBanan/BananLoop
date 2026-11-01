"""Run a deterministic CLI pause/resume lifecycle demo without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
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



def _write_fake_codex_cli(bin_dir: Path, markers: Path) -> None:
    """Create an offline Codex CLI stand-in for process-tree recovery tests."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "fake_codex.py"
    fake.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        f"root=Path({str(markers)!r})\n"
        "if '--version' in sys.argv:\n"
        "    print('codex-cli fake-lifecycle')\n"
        "    raise SystemExit(0)\n"
        "args=sys.argv[1:]\n"
        "out=Path(args[args.index('-o')+1])\n"
        "prompt=sys.stdin.read()\n"
        "task=json.loads(prompt.rsplit('\\n\\n',1)[-1])\n"
        "role=task['role']\n"
        "with (root/'calls.jsonl').open('a',encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'role':role})+'\\n')\n"
        "if role == 'propose':\n"
        "    counter=root/'proposals'\n"
        "    count=int(counter.read_text())+1 if counter.exists() else 1\n"
        "    counter.write_text(str(count))\n"
        "    if count == 1:\n"
        "        (root/'codex-propose-ready').write_text('ready')\n"
        "        time.sleep(4)\n"
        "        (root/'codex-survived-cancel').write_text('survived')\n"
        "    hypothesis={\n"
        "      'hypothesis':'Raise quality to the target while preserving the guard',\n"
        "      'rationale':'deterministic local fake CLI fixture',\n"
        "      'expected_effect':'quality reaches 3 with zero regressions',\n"
        "      'files_or_area':['value.txt','guard.txt'],\n"
        "      'falsification_condition':'quality remains below 3 or regressions exceed zero',\n"
        "      'mechanism_key':'fake-codex-quality-target',\n"
        "      'relation':'new','prior_experiment_refs':[],\n"
        "      'novelty_rationale':'single controlled fixture mechanism',\n"
        "      'target_failure_signal':'','failure_mechanism':''}\n"
        "    candidate={'candidate_id':'fake-codex-target','hypothesis':hypothesis,\n"
        "      'evidence_refs':['INSUFFICIENT_EVIDENCE'],'suspected_failure_mode':'',\n"
        "      'predicted_observable':'quality reaches 3','discriminating_test':'evaluate the fixture',\n"
        "      'scope_eligibility':True,'estimated_agent_calls':1,'estimated_evaluator_calls':1,\n"
        "      'estimated_work_units':None,'rationale':'local fake CLI','uncertainty':'fixture only',\n"
        "      'inference':'agent_inference'}\n"
        "    result={'candidates':[candidate]}\n"
        "    result.update({key:None for key in ('hypothesis','rationale','expected_effect',\n"
        "      'files_or_area','falsification_condition','mechanism_key','relation',\n"
        "      'prior_experiment_refs','novelty_rationale','target_failure_signal','failure_mechanism')})\n"
        "elif role == 'implement':\n"
        "    Path('value.txt').write_text('3\\n',encoding='utf-8')\n"
        "    Path('guard.txt').write_text('0\\n',encoding='utf-8')\n"
        "    result={'summary':'set fixture target','changed_paths':['value.txt','guard.txt'],\n"
        "      'verification_notes':'offline fake Codex CLI','blockers':[]}\n"
        "else:\n"
        "    raise SystemExit('unexpected fake Codex role: '+role)\n"
        "out.write_text(json.dumps(result),encoding='utf-8')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        (bin_dir / "codex.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" "%~dp0fake_codex.py" %*\r\n',
            encoding="utf-8",
        )
    else:
        launcher = bin_dir / "codex"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)


def run_fake_codex_recovery(output_dir: Path) -> dict[str, object]:
    """Interrupt and resume the actual CLI -> CodexAdapter -> fake CLI path."""
    output_dir.mkdir(parents=True, exist_ok=False)
    repo = output_dir / "fixture"
    state = output_dir / "state"
    logs = output_dir / "logs"
    fake_bin = output_dir / "fake-bin"
    logs.mkdir()
    py_source = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_source) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    _write_fake_codex_cli(fake_bin, output_dir)
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("create_fixture.py")), str(repo)],
        check=True, capture_output=True, text=True,
    )
    cli = ["--repo", str(repo), "--state-dir", str(state)]
    init_args = [
        "init", *cli, "--goal", "Raise quality to 3 while regressions stay at zero",
        "--eval-cmd", "python evaluator.py", "--metric", "quality", "--direction", "maximize",
        "--target", "3", "--min-improvement", "0.5", "--guard", "regressions<=0",
        "--check", "tests", "--editable", "value.txt", "--editable", "guard.txt",
        "--protect", "evaluator.py", "--protect", "agent.py", "--agent", "codex",
        "--max-experiments", "1", "--max-attempts", "2", "--max-agent-calls", "8",
        "--max-evaluator-calls", "5", "--selector-mode", "deterministic",
    ]
    _run_cli(init_args, repo=repo, env=env, log_dir=logs, name="init")

    process = subprocess.Popen(
        [sys.executable, "-m", "bananloop.cli", "run", *cli], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
           else {"start_new_session": True}),
    )
    ready = output_dir / "codex-propose-ready"
    deadline = time.monotonic() + 20
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not ready.exists():
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(f"fake Codex did not start; stdout={stdout!r}; stderr={stderr!r}")

    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    stdout, stderr = process.communicate(timeout=15)
    (logs / "interrupted-run.stdout.txt").write_text(stdout, encoding="utf-8")
    (logs / "interrupted-run.stderr.txt").write_text(stderr, encoding="utf-8")
    if process.returncode != 130:
        raise RuntimeError(f"interrupted Codex campaign exited {process.returncode}: {stderr[-2000:]}")
    if "interrupted by user" not in stderr.lower():
        raise RuntimeError("signal did not reach the BananLoop CLI interruption handler")
    time.sleep(4.2)
    if (output_dir / "codex-survived-cancel").exists():
        raise RuntimeError("fake Codex subprocess survived process-control cancellation")
    with sqlite3.connect(state / "campaign.sqlite3") as conn:
        action = conn.execute(
            "SELECT status FROM actions WHERE id='exp:1:propose'"
        ).fetchone()
        cancelled = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM events WHERE event_type='ACTION_CANCELLED'"
        )]
    if action is None or action[0] != "CANCELLED" or not any(
        row.get("action_id") == "exp:1:propose" for row in cancelled
    ):
        raise RuntimeError("active Codex proposal cancellation was not durably recorded")

    paused = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="paused-status")
    if "status: PAUSED_INFRA" not in paused:
        raise RuntimeError("Codex interruption did not persist PAUSED_INFRA")
    _run_cli(["resume", *cli], repo=repo, env=env, log_dir=logs, name="resume", timeout=90)
    status = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="status")
    report = _run_cli(["report", *cli], repo=repo, env=env, log_dir=logs, name="report")
    if "status: GOAL_MET" not in status or "Final assessment: **DEV_ONLY**" not in report:
        raise RuntimeError("resumed fake-Codex campaign did not finish with a DEV-only report")
    with sqlite3.connect(state / "campaign.sqlite3") as conn:
        completed_action = conn.execute(
            "SELECT status FROM actions WHERE id='exp:1:propose'"
        ).fetchone()
        proposal_reservations = [row[0] for row in conn.execute(
            "SELECT status FROM reservations "
            "WHERE action_id='exp:1:propose' AND resource='agent_call' ORDER BY id"
        )]
    if completed_action is None or completed_action[0] != "COMPLETED":
        raise RuntimeError("resumed proposal action did not complete under its original ID")
    if proposal_reservations != ["CANCELLED", "CONSUMED"]:
        raise RuntimeError(f"proposal call reservations were not cancelled then consumed: {proposal_reservations}")
    calls = [json.loads(line) for line in (output_dir / "calls.jsonl").read_text().splitlines()]
    if [row["role"] for row in calls].count("propose") != 2 or [
        row["role"] for row in calls
    ].count("implement") != 1:
        raise RuntimeError(f"resume repeated or skipped a fake Codex action: {calls}")
    summary: dict[str, object] = {
        "interruption": "SIGINT" if os.name != "nt" else "CTRL_BREAK",
        "status": "GOAL_MET",
        "paused_status": "PAUSED_INFRA",
        "final_assessment": "DEV_ONLY",
        "model_calls": 0,
        "holdout_configured": False,
        "cancelled_action": "exp:1:propose",
        "proposal_invocations": 2,
        "implementation_invocations": 1,
        "logs": str(logs),
    }
    (output_dir / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_hil_pivot_cli(output_dir: Path) -> dict[str, object]:
    """Exercise operator decision, digest-confirmed child, execution and report."""
    from bananloop.contracts import (
        AgentConfig, BudgetSpec, CampaignSpec, Direction, MetricSpec,
        PivotProposal, PivotResourceRequest, RuntimePaths,
    )
    from bananloop.registry import CampaignRegistry
    from bananloop.store import CampaignStore
    from bananloop.workspace import current_commit

    output_dir.mkdir(parents=True, exist_ok=False)
    repo = output_dir / "fixture"
    state = output_dir / "state"
    logs = output_dir / "logs"
    logs.mkdir()
    py_source = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_source) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    isolated_home = tempfile.TemporaryDirectory(prefix="bl-hil-")
    env["HOME"] = isolated_home.name
    env["USERPROFILE"] = isolated_home.name
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("create_fixture.py")), str(repo)],
        check=True, capture_output=True, text=True,
    )
    agent = output_dir / "hil_agent.py"
    _write_slow_first_proposal(agent, output_dir, first_sleep=0)
    project_digest = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
    registry_paths = RuntimePaths.for_repo(
        repo, Path(isolated_home.name) / ".bananloop" / "projects" / project_digest,
    )
    registry = CampaignRegistry(repo, registry_paths)
    entry, campaign_root, _ = registry.reserve("Raise quality to 3", "hil-parent")
    spec = CampaignSpec(
        goal="Raise quality to 3 while regressions stay at zero",
        source_repo=str(repo),
        base_commit=current_commit(repo),
        evaluation_command=[sys.executable, "evaluator.py"],
        primary_metric=MetricSpec(
            name="quality", direction=Direction.MAXIMIZE, target=3.0, min_improvement=0.5,
        ),
        mandatory_checks=["tests"],
        editable_scope=["value.txt", "guard.txt"],
        protected_paths=["evaluator.py", "agent.py"],
        agent=AgentConfig(kind="command", command=[sys.executable, str(agent)]),
        budget=BudgetSpec(max_experiments=5, max_attempts=6, max_agent_calls=12,
                          max_evaluator_calls=8),
    )
    parent = CampaignStore(campaign_root / "campaign.sqlite3")
    parent.initialize(spec)
    parent.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT")
    proposal = PivotProposal.build(
        spec_hash=spec.approved_hash(), trigger_experiment_ordinal=1,
        current_architecture="single stage", proposed_architecture="bounded two stage",
        rationale="operator-reviewable fixture change", expected_effect="reach target safely",
        changed_scope=["value.txt", "guard.txt"],
        resources=PivotResourceRequest(additional_agent_calls=2, additional_evaluator_calls=2,
                                       notes="bounded local fixture"),
    )
    parent.save_pivot_proposal(proposal, "operator approval required")
    parent.close()

    cli = ["--repo", str(repo), "--campaign-id", entry.campaign_id]
    stopped = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="parent-status")
    if "status: PAUSED_NEEDS_INPUT" not in stopped or "stop_reason: PAUSED_NEEDS_INPUT" not in stopped:
        raise RuntimeError("operator CLI did not show the pending stop and reason")
    operator_view = json.loads(_run_cli(
        ["operator-snapshot", *cli], repo=repo, env=env, log_dir=logs,
        name="parent-operator-snapshot",
    ))
    pivot_review = operator_view.get("pivot_review") or {}
    displayed_proposal = pivot_review.get("proposal") or {}
    proposal_id = displayed_proposal.get("proposal_id")
    proposal_digest = displayed_proposal.get("digest")
    if (
        not pivot_review.get("integrity_valid")
        or pivot_review.get("status") != "PENDING"
        or proposal_id != proposal.proposal_id
        or proposal_digest != proposal.digest
    ):
        raise RuntimeError("operator snapshot did not display the intact pending proposal identity")
    decision_args = [
        "pivot-decision", *cli, "--decision", "approve", "--proposal-id", proposal_id,
        "--digest", proposal_digest, "--request-id", "operator-approval-1",
    ]
    first_decision = _run_cli(decision_args, repo=repo, env=env, log_dir=logs, name="approve")
    repeated_decision = _run_cli(
        decision_args, repo=repo, env=env, log_dir=logs, name="approve-replay",
    )
    first_receipt = json.loads(first_decision)
    repeated_receipt = json.loads(repeated_decision)
    if first_receipt != repeated_receipt or first_receipt["status"] != "APPROVED":
        raise RuntimeError("operator approval or its idempotent replay failed")

    child_spec = spec.model_copy(update={"goal": "Approved pivot child: " + spec.goal})
    spec_file = output_dir / "child-spec.json"
    spec_file.write_text(child_spec.model_dump_json(), encoding="utf-8")
    child_args = [
        "pivot-child", *cli, "--proposal-id", proposal_id,
        "--digest", proposal_digest, "--spec-file", str(spec_file),
    ]
    preview = json.loads(_run_cli(child_args, repo=repo, env=env, log_dir=logs, name="child-preview"))
    if preview["status"] != "CONFIRMATION_REQUIRED" or preview["child_initialized"]:
        raise RuntimeError("child spec preview unexpectedly initialized a campaign")
    created = json.loads(_run_cli(
        [*child_args, "--confirmed-child-hash", preview["child_spec_hash"]],
        repo=repo, env=env, log_dir=logs, name="child-confirm",
    ))
    if created["status"] != "READY" or created["automatic_execution"]:
        raise RuntimeError("confirmed child creation did not stop for explicit run")

    child_cli = ["--repo", str(repo), "--campaign-id", created["campaign_id"]]
    _run_cli(["run", *child_cli], repo=repo, env=env, log_dir=logs, name="child-run", timeout=90)
    status = _run_cli(["status", *child_cli], repo=repo, env=env, log_dir=logs, name="child-status")
    report = _run_cli(["report", *child_cli], repo=repo, env=env, log_dir=logs, name="child-report")
    if "status: GOAL_MET" not in status or "Final assessment: **DEV_ONLY**" not in report:
        raise RuntimeError("approved child did not finish with its DEV-only report")
    agent_calls_before_resume = int((output_dir / "agent-calls").read_text(encoding="utf-8"))
    repeated_run = _run_cli(
        ["resume", *child_cli], repo=repo, env=env, log_dir=logs, name="child-resume-replay",
        timeout=30,
    )
    agent_calls_after_resume = int((output_dir / "agent-calls").read_text(encoding="utf-8"))
    final_status = _run_cli(
        ["status", *child_cli], repo=repo, env=env, log_dir=logs, name="child-final-status",
    )
    if "status: GOAL_MET" not in final_status or agent_calls_after_resume != agent_calls_before_resume:
        raise RuntimeError("idempotent child resume changed its terminal result or repeated agent work")
    child_store = CampaignStore(Path(created["state_dir"]) / "campaign.sqlite3")
    try:
        lineage = child_store.spec().spec_lineage
        child_counts = child_store.campaign()
        decision_count = int(parent_decision_count(campaign_root / "campaign.sqlite3"))
    finally:
        child_store.close()
    if lineage is None or lineage.decision_request_id != "operator-approval-1" or decision_count != 1:
        raise RuntimeError("approval lineage or idempotent decision receipt was not preserved")
    summary: dict[str, object] = {
        "status": "GOAL_MET",
        "operator_stop_visible": True,
        "proposal_identity_displayed": True,
        "approval_request_id": "operator-approval-1",
        "approval_replay": "same_receipt",
        "decision_receipts": decision_count,
        "child_campaign_id": created["campaign_id"],
        "child_lineage_parent": lineage.parent_campaign_id,
        "child_status": "GOAL_MET",
        "child_experiments": child_counts["experiment_count"],
        "agent_calls_before_terminal_resume": agent_calls_before_resume,
        "agent_calls_after_terminal_resume": agent_calls_after_resume,
        "final_assessment": "DEV_ONLY",
        "model_calls": 0,
        "holdout_configured": False,
        "resume_replay_output": repeated_run.strip(),
        "logs": str(logs),
    }
    (output_dir / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parent_decision_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM pivot_decisions").fetchone()[0])


def _write_holdout_evaluator(
    path: Path,
    *,
    calls: Path,
    ready: Path,
    survived: Path,
    marker: str,
) -> None:
    path.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        f"calls=Path({str(calls)!r})\n"
        f"ready=Path({str(ready)!r})\n"
        f"survived=Path({str(survived)!r})\n"
        f"marker={marker!r}\n"
        "count=int(calls.read_text())+1 if calls.exists() else 1\n"
        "calls.write_text(str(count))\n"
        "if count == 1:\n"
        "    ready.write_text('ready')\n"
        "    time.sleep(6)\n"
        "    survived.write_text('holdout process survived interruption')\n"
        "value=float(Path('value.txt').read_text().strip())\n"
        "regression=float(Path('guard.txt').read_text().strip())\n"
        "print(json.dumps({'schema_version':1,'status':'ok',"
        "'metrics':{'quality':value,'regressions':regression},"
        "'checks':{'tests':True},'diagnostics':{'private_holdout_marker':marker}}))\n",
        encoding="utf-8",
    )


def _campaign_counts(db: Path) -> dict[str, int | str | None]:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT status,stage,stop_reason,experiment_count,attempt_count,agent_calls "
            "FROM campaigns WHERE id=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("campaign database was not initialized")
        return {
            "status": row[0],
            "stage": row[1],
            "stop_reason": row[2],
            "experiment_count": int(row[3]),
            "attempt_count": int(row[4]),
            "agent_calls": int(row[5]),
            "agent_actions": int(conn.execute(
                "SELECT COUNT(*) FROM actions WHERE kind='agent'"
            ).fetchone()[0]),
            "dev_evaluations": int(conn.execute(
                "SELECT COUNT(*) FROM evaluations WHERE tier='dev'"
            ).fetchone()[0]),
        }


def run_final_holdout_recovery(output_dir: Path) -> dict[str, object]:
    """Interrupt a real final HOLDOUT subprocess and prove resume cannot replan."""
    output_dir.mkdir(parents=True, exist_ok=False)
    repo = output_dir / "fixture"
    state = output_dir / "state"
    logs = output_dir / "logs"
    logs.mkdir()
    py_source = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_source) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("create_fixture.py")), str(repo)],
        check=True, capture_output=True, text=True,
    )
    marker = f"PRIVATE_HOLDOUT_CANARY_{uuid.uuid4().hex}"
    holdout_script = repo / "holdout_evaluator.py"
    holdout_calls = output_dir / "holdout-calls"
    holdout_ready = output_dir / "holdout-ready"
    holdout_survived = output_dir / "holdout-survived-interrupt"
    _write_holdout_evaluator(
        holdout_script,
        calls=holdout_calls,
        ready=holdout_ready,
        survived=holdout_survived,
        marker=marker,
    )
    subprocess.run(
        ["git", "add", "holdout_evaluator.py"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "freeze final holdout fixture"],
        cwd=repo, check=True, capture_output=True, text=True,
    )

    agent = output_dir / "lifecycle_agent.py"
    _write_slow_first_proposal(agent, output_dir, first_sleep=0)
    cli = ["--repo", str(repo), "--state-dir", str(state)]
    init_args = [
        "init", *cli, "--goal", "Raise quality to 3 while regressions stay at zero",
        "--eval-cmd", "python evaluator.py", "--holdout-eval-cmd",
        shlex.join([
            sys.executable, "holdout_evaluator.py", str(holdout_calls),
            str(holdout_ready), str(holdout_survived), marker,
        ]),
        "--holdout-timeout-seconds", "60",
        "--metric", "quality", "--direction", "maximize", "--target", "3",
        "--min-improvement", "0.5", "--guard", "regressions<=0",
        "--check", "tests", "--editable", "value.txt", "--editable", "guard.txt",
        "--protect", "evaluator.py", "--protect", "agent.py",
        "--protect", "holdout_evaluator.py", "--agent-hide", "holdout_evaluator.py",
        "--agent", "command", "--agent-cmd", shlex.join([sys.executable, str(agent)]),
        "--max-experiments", "5", "--max-attempts", "6", "--max-agent-calls", "12",
        "--max-evaluator-calls", "8",
    ]
    _run_cli(init_args, repo=repo, env=env, log_dir=logs, name="init")

    process = subprocess.Popen(
        [sys.executable, "-m", "bananloop.cli", "run", *cli], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
           else {"start_new_session": True}),
    )
    deadline = time.monotonic() + 90
    ready_for_interrupt = False
    while process.poll() is None and time.monotonic() < deadline:
        if holdout_ready.exists():
            counts = _campaign_counts(state / "campaign.sqlite3")
            with sqlite3.connect(state / "campaign.sqlite3") as conn:
                action = conn.execute(
                    "SELECT status FROM actions WHERE id='final:holdout'"
                ).fetchone()
            if (
                counts["stage"] == "FINAL_HOLDOUT"
                and counts["stop_reason"] == "GOAL_MET"
                and action is not None
                and action[0] == "RUNNING"
            ):
                ready_for_interrupt = True
                break
        time.sleep(0.05)
    if not ready_for_interrupt:
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(
            "campaign did not enter its active final HOLDOUT; "
            f"stdout={stdout[-2000:]!r}; stderr={stderr[-2000:]!r}"
        )

    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    stdout, stderr = process.communicate(timeout=15)
    (logs / "interrupted-holdout.stdout.txt").write_text(stdout, encoding="utf-8")
    (logs / "interrupted-holdout.stderr.txt").write_text(stderr, encoding="utf-8")
    if process.returncode != 130 or "interrupted by user" not in stderr.lower():
        raise RuntimeError(f"HOLDOUT interruption was not persisted: {stderr[-2000:]}")

    pending = _campaign_counts(state / "campaign.sqlite3")
    with sqlite3.connect(state / "campaign.sqlite3") as conn:
        action_status = conn.execute(
            "SELECT status FROM actions WHERE id='final:holdout'"
        ).fetchone()
        holdout_evaluations = int(conn.execute(
            "SELECT COUNT(*) FROM evaluations WHERE tier='holdout'"
        ).fetchone()[0])
        holdout_receipts = int(conn.execute(
            "SELECT COUNT(*) FROM evaluation_receipts WHERE action_id='final:holdout'"
        ).fetchone()[0])
    if (
        pending["status"] != "RUNNING"
        or pending["stage"] != "FINAL_HOLDOUT"
        or pending["stop_reason"] != "GOAL_MET"
        or action_status is None
        or action_status[0] != "CANCELLED"
        or holdout_evaluations != 0
        or holdout_receipts != 0
    ):
        raise RuntimeError(f"unexpected state after interrupted HOLDOUT: {pending}")
    time.sleep(7.2)
    if holdout_survived.exists():
        raise RuntimeError("the cancelled HOLDOUT subprocess survived the interruption")

    calls_before_resume = int(holdout_calls.read_text(encoding="utf-8"))
    agent_count_before_resume = int((output_dir / "agent-calls").read_text(encoding="utf-8"))
    _run_cli(["resume", *cli], repo=repo, env=env, log_dir=logs, name="resume", timeout=90)
    status = _run_cli(["status", *cli], repo=repo, env=env, log_dir=logs, name="status")
    report = _run_cli(["report", *cli], repo=repo, env=env, log_dir=logs, name="report")
    if "status: GOAL_MET" not in status or "Final assessment: **ACCEPTED**" not in report:
        raise RuntimeError("resumed final HOLDOUT did not complete with an accepted report")

    final_counts = _campaign_counts(state / "campaign.sqlite3")
    with sqlite3.connect(state / "campaign.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        final_action = conn.execute(
            "SELECT status,response_json FROM actions WHERE id='final:holdout'"
        ).fetchone()
        receipt = conn.execute(
            "SELECT receipt_json FROM evaluation_receipts WHERE action_id='final:holdout'"
        ).fetchone()
        holdout_result = conn.execute(
            "SELECT result_json FROM evaluations WHERE tier='holdout'"
        ).fetchone()
        holdout_evaluations = int(conn.execute(
            "SELECT COUNT(*) FROM evaluations WHERE tier='holdout'"
        ).fetchone()[0])
        recorded = int(conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='FINAL_HOLDOUT_RECORDED'"
        ).fetchone()[0])
        agent_rows = [dict(row) for row in conn.execute(
            "SELECT request_json,response_json FROM actions WHERE kind='agent'"
        )]
    if final_action is None or final_action["status"] != "COMPLETED":
        raise RuntimeError("final HOLDOUT action did not complete")
    action_result = json.loads(final_action["response_json"])
    holdout_result_json = json.loads(holdout_result["result_json"]) if holdout_result is not None else {}
    if (
        int(holdout_calls.read_text(encoding="utf-8")) != 2
        or calls_before_resume != 1
        or holdout_evaluations != 1
        or receipt is None
        or holdout_result is None
        or recorded != 1
        or action_result.get("diagnostics", {}).get("private_holdout_marker") != marker
        or holdout_result_json.get("diagnostics", {}).get("private_holdout_marker") != marker
        or marker in json.dumps(agent_rows)
        or int((output_dir / "agent-calls").read_text(encoding="utf-8")) != agent_count_before_resume
        or any(final_counts[key] != pending[key] for key in (
            "experiment_count", "attempt_count", "agent_calls", "agent_actions", "dev_evaluations",
        ))
    ):
        raise RuntimeError("final HOLDOUT replay leaked feedback or re-entered adaptive planning")

    summary: dict[str, object] = {
        "status": "GOAL_MET",
        "final_assessment": "ACCEPTED",
        "interrupted_stage": "FINAL_HOLDOUT",
        "pending_stop_reason": "GOAL_MET",
        "cancelled_holdout_calls": calls_before_resume,
        "total_holdout_invocations": int(holdout_calls.read_text(encoding="utf-8")),
        "completed_holdout_evaluations": holdout_evaluations,
        "completed_holdout_receipts": 1,
        "final_holdout_recorded_events": recorded,
        "agent_calls_after_terminal_intent": 0,
        "experiment_count": final_counts["experiment_count"],
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
    parser.add_argument(
        "--fake-codex-recovery", action="store_true",
        help="interrupt and resume through CodexAdapter using a local fake Codex executable",
    )
    parser.add_argument(
        "--interrupt-final-holdout", action="store_true",
        help="interrupt a real active FINAL_HOLDOUT evaluator, then resume without replanning",
    )
    parser.add_argument(
        "--hil-pivot-cli", action="store_true",
        help="exercise CLI stop visibility, idempotent approval, confirmed child, run and report",
    )
    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="bananloop-lifecycle-"))
    if sum((args.sigint, args.fake_codex_recovery, args.interrupt_final_holdout, args.hil_pivot_cli)) > 1:
        parser.error("lifecycle scenario options are mutually exclusive")
    result = (
        run_hil_pivot_cli(output_dir)
        if args.hil_pivot_cli
        else run_final_holdout_recovery(output_dir)
        if args.interrupt_final_holdout
        else run_fake_codex_recovery(output_dir)
        if args.fake_codex_recovery
        else run(output_dir, sigint=args.sigint)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
