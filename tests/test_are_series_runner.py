from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.autoresearchexam.series_runner import (
    AutoResearchExamSeriesRunner,
    JOB_SCHEMA,
    MAX_ITERATIONS,
    MAX_SECONDS,
    build_command,
    load_selection,
    validate_prepared_job,
)
from benchmarks.autoresearchexam.campaign_binding import require_frozen_optimization_child


TASK_IDS = [
    "budgeted-covtype-dual-market-open",
    "budgeted-imputation-mcar50",
    "carps-star-discrepancy-subset-select",
    "cpu-decoder-graph-executor",
    "cpu-llm-decode-throughput",
    "dctabeval-aeac-pooled-cat-statistics",
    "label-efficient-risk-estimator",
    "shortest-valid-ci-l2-ece",
    "sketched-newton-cov-estimator",
    "sopcc-online-chance-constrained-policy",
]


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return _digest(raw)


def _prepared_jobs(tmp_path: Path):
    selection_path = tmp_path / "selection.json"
    selection_tasks = []
    overlay_tasks = []
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    for task_id in TASK_IDS:
        task_hash = _digest((task_id + " task").encode())
        instruction_bytes = (task_id + " instruction").encode()
        instruction_hash = _digest(instruction_bytes)
        selection_tasks.append({
            "task_id": task_id, "version": "1.1.0",
            "task_sha256": task_hash, "instruction_sha256": instruction_hash,
        })
        overlay_dir = tmp_path / "overlays" / task_id
        overlay_dir.mkdir(parents=True)
        overlay_bytes = f"task = '{task_id}'\n".encode()
        (overlay_dir / "task.toml").write_bytes(overlay_bytes)
        (overlay_dir / "instruction.md").write_bytes(instruction_bytes)
        overlay_hash = _digest(overlay_bytes)
        (overlay_dir / "bananloop-overlay.json").write_text(json.dumps({
            "task_id": task_id, "version": "1.1.0",
            "source_task_sha256": task_hash,
            "source_instruction_sha256": instruction_hash,
            "overlay_task_sha256": overlay_hash,
        }))
        overlay_tasks.append({"task_id": task_id, "version": "1.1.0",
                              "overlay_path": str(overlay_dir),
                              "overlay_task_sha256": overlay_hash})

        bl_config = {
            "task_id": task_id, "task_version": "1.1.0",
            "task_instruction_sha256": instruction_hash,
            "max_candidate_iterations": MAX_ITERATIONS,
            "time_limit_seconds": MAX_SECONDS,
            "model_call_limit": 10,
            "max_steps": 20,
        }
        bl_path = jobs_dir / f"{task_id}-bananloop.json"
        bl_hash = _write_json(bl_path, bl_config)
        harbor_path = jobs_dir / f"{task_id}-harbor.json"
        harbor_config = {
            "job_name": f"job-{task_id}",
            "jobs_dir": str(tmp_path / "harbor-jobs"),
            "n_attempts": 1, "n_concurrent_trials": 1,
            "retry": {"max_retries": 0},
            "tasks": [{"path": str(overlay_dir)}],
            "agents": [{
                "import_path": "benchmarks.autoresearchexam.harbor_agent:AREBananLoopOptimizationAgent",
                "kwargs": {"config_path": str(bl_path)},
                "override_timeout_sec": MAX_SECONDS,
            }],
        }
        harbor_hash = _write_json(harbor_path, harbor_config)
        descriptor = {
            "schema": JOB_SCHEMA, "task_id": task_id,
            "task_version": "1.1.0", "task_instruction_sha256": instruction_hash,
            "overlay_path": str(overlay_dir), "overlay_task_sha256": overlay_hash,
            "bananloop_config_path": str(bl_path), "bananloop_config_sha256": bl_hash,
            "harbor_config_path": str(harbor_path), "harbor_config_sha256": harbor_hash,
        }
        (jobs_dir / f"{task_id}.json").write_text(json.dumps(descriptor))
    _write_json(selection_path, {
        "schema_version": 2, "source_revision": "a" * 40, "tasks": selection_tasks,
    })
    overlay_index_path = tmp_path / "overlay-index.json"
    _write_json(overlay_index_path, {
        "schema": "bananloop-are-safe-harbor-overlay-index-v1",
        "source_revision": "a" * 40, "task_count": 10, "tasks": overlay_tasks,
    })
    return selection_path, overlay_index_path, jobs_dir


def test_prepared_job_requires_ten_model_calls_and_fifteen_harbor_steps(tmp_path):
    selection, overlay_index, jobs = _prepared_jobs(tmp_path)
    descriptor_path = jobs / f"{TASK_IDS[0]}.json"
    descriptor = json.loads(descriptor_path.read_text())
    config_path = jobs / f"{TASK_IDS[0]}-bananloop.json"
    config = json.loads(config_path.read_text())
    config["model_call_limit"] = 9
    config_path.write_text(json.dumps(config))
    descriptor["bananloop_config_sha256"] = _digest(config_path.read_bytes())
    descriptor_path.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError, match="cannot fund five candidate lifecycles"):
        validate_prepared_job(
            descriptor_path,
            selection=load_selection(selection)[0],
            overlay_index=json.loads(overlay_index.read_text()),
        )


def test_frozen_child_binding_checks_mode_cap_and_budget(tmp_path):
    campaign_id = "c-" + "a" * 32
    state_dir = tmp_path / "state"
    campaign_dir = state_dir / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True)
    spec = {
        "schema_version": 2, "mode": "OPTIMIZATION",
        "goal": "bounded test", "source_repo": "https://example.invalid/repo",
        "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1},
        "budget": {"max_experiments": 10, "max_attempts": 10, "max_agent_calls": 10,
                   "max_evaluator_calls": 20, "deadline_seconds": 3600,
                   "plateau_experiments": 3},
        "role_registry_digest": "a" * 64,
        "evaluation": {"protocol_id": "public-dev", "version": 1,
                       "content_sha256": "b" * 64, "approval_digest": "c" * 64},
        "parent": None, "max_candidate_iterations": 5,
    }
    db = campaign_dir / "campaign.sqlite3"
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE campaigns (id INTEGER PRIMARY KEY, spec_json TEXT NOT NULL)")
        conn.execute("INSERT INTO campaigns VALUES (1, ?)", (json.dumps(spec),))
    config = {"campaign_id": campaign_id, "state_dir": str(state_dir),
              "max_candidate_iterations": 5, "time_limit_seconds": 3600}
    assert require_frozen_optimization_child(config).max_candidate_iterations == 5
    spec["budget"]["max_experiments"] = 4
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE campaigns SET spec_json=?", (json.dumps(spec),))
    with pytest.raises(ValueError, match="budget cannot fund"):
        require_frozen_optimization_child(config)


def _runner(tmp_path, process_runner, task_ids=None):
    selection, overlay_index, jobs = _prepared_jobs(tmp_path)
    return AutoResearchExamSeriesRunner(
        selection_path=selection, overlay_index_path=overlay_index,
        job_config_dir=jobs, series_dir=tmp_path / "series", run_id="fixture-run",
        task_ids=task_ids or TASK_IDS[:2], process_runner=process_runner,
    )


def _write_successful_harbor_receipt(config_path: Path, *, submission_unknown: bool = False) -> bool:
    config = json.loads(config_path.read_text())
    overlay_path = config["tasks"][0]["path"]
    agent_path = Path(config["agents"][0]["kwargs"]["config_path"])
    task_id = json.loads(agent_path.read_text())["task_id"]
    job_dir = Path(config["jobs_dir"]) / config["job_name"]
    trial_dir = job_dir / "trial-001"
    trial_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(json.dumps({
        "id": "job-id", "finished_at": "now", "n_total_trials": 1,
        "stats": {"n_completed_trials": 1, "n_errored_trials": 0,
                  "n_running_trials": 0, "n_pending_trials": 0,
                  "n_cancelled_trials": 0, "n_retries": 0},
    }))
    (trial_dir / "result.json").write_text(json.dumps({
        "id": "trial-id", "finished_at": "now", "task_id": {"path": overlay_path},
        "exception_info": None,
        "agent_result": {"metadata": {"bananloop_are": {
            "task_id": task_id, "campaign_id": "campaign-id",
            "status": "ALL_NODES_COMPLETE", "provider_call_attempts": 2,
            "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            "public_submissions": ([{"state": "SUBMISSION_UNKNOWN"}]
                                   if submission_unknown else []),
        }}},
    }))
    return True


def test_command_disables_harbor_verification_and_never_retries():
    command = build_command(harbor_binary="/bin/harbor", harbor_config=Path("/tmp/job.json"))
    assert command == ["/bin/harbor", "run", "--config", "/tmp/job.json", "--disable-verification"]


def test_dispatch_continues_after_failure_and_does_not_replay_terminal_tasks(tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(calls) > 1:
            _write_successful_harbor_receipt(Path(argv[3]))
        return SimpleNamespace(returncode=17 if len(calls) == 1 else 0,
                               stdout="", stderr="synthetic")

    runner = _runner(tmp_path, fake_run)
    first = runner.run()
    assert [r["status"] for r in first["tasks"]] == ["FAILED", "COMPLETED"]
    assert first["tasks"][1]["bananloop_status"] == "ALL_NODES_COMPLETE"
    assert first["tasks"][1]["model_calls"] == 2
    assert first["tasks"][1]["token_usage"]["total_tokens"] == 150
    assert len(calls) == 2
    assert all(call[1]["timeout"] == MAX_SECONDS for call in calls)
    assert all("--disable-verification" in call[0] for call in calls)
    resumed = runner.run()
    assert [r["attempt_count"] for r in resumed["tasks"]] == [1, 1]
    assert len(calls) == 2
    assert resumed["series_status"] == "COMPLETED_WITH_FAILURES"


def test_running_status_becomes_unknown_and_is_never_replayed(tmp_path):
    calls = []
    runner = _runner(tmp_path, lambda *a, **k: (
        calls.append(a) or (_write_successful_harbor_receipt(Path(a[0][3]))
                            and SimpleNamespace(returncode=0, stdout="", stderr=""))
    ))
    report = runner._initial_report()
    report["tasks"][0]["status"] = "RUNNING"
    runner.series_dir.mkdir(parents=True)
    runner._save(report)

    resumed = runner.run()
    assert resumed["tasks"][0]["status"] == "INTERRUPTED_UNKNOWN"
    assert resumed["tasks"][0]["model_calls"] == "UNKNOWN"
    assert len(calls) == 1
    assert "budgeted-imputation-mcar50-harbor.json" in calls[0][0][3]
    assert resumed["tasks"][1]["status"] == "COMPLETED"


def test_timeout_is_terminal_unknown_and_not_replayed(tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"partial")

    runner = _runner(tmp_path, fake_run)
    report = runner.run()
    assert report["tasks"][0]["status"] == "TIMED_OUT"
    assert report["tasks"][0]["model_calls"] == "UNKNOWN"
    assert report["tasks"][1]["status"] == "TIMED_OUT"
    assert len(calls) == 2
    runner.run()
    assert len(calls) == 2


def test_changed_prepared_config_digest_blocks_resume_or_launch(tmp_path):
    runner = _runner(tmp_path, lambda *a, **k: (
        _write_successful_harbor_receipt(Path(a[0][3]))
        and SimpleNamespace(returncode=0, stdout="", stderr="")
    ))
    first = runner.run()
    assert first["tasks"][0]["status"] == "COMPLETED"
    bl_path = Path(first["tasks"][0]["bananloop_config_path"])
    bl_path.write_text("{}")
    with pytest.raises(ValueError, match="does not match its prepared digest"):
        runner.run()


def test_unknown_public_submission_is_terminal_and_never_replayed(tmp_path):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if len(calls) == 1:
            _write_successful_harbor_receipt(Path(argv[3]), submission_unknown=True)
        else:
            _write_successful_harbor_receipt(Path(argv[3]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner = _runner(tmp_path, fake_run)
    first = runner.run()
    assert first["tasks"][0]["status"] == "INTERRUPTED_UNKNOWN"
    assert first["tasks"][0]["public_submission_unknown"] is True
    assert first["tasks"][1]["status"] == "COMPLETED"
    runner.run()
    assert len(calls) == 2
