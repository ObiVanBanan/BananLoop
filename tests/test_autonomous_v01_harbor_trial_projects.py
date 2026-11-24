from __future__ import annotations

import json

from benchmarks.autonomous_v01.harbor_trial_projects import (
    identify_trial_compose_projects,
)


def write_config(root, job="job-1", *, trial="Trial.X", steps=None):
    directory = root / job
    directory.mkdir(parents=True)
    config = {
        "trial_name": trial,
        "environment": {"type": "docker"},
        "task": {"verifier": {"environment_mode": "shared"}, "steps": steps or []},
    }
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return directory / "config.json"


def test_derives_harbor_agent_and_environment_compose_names(tmp_path):
    write_config(tmp_path)

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "KNOWN"
    assert result["projects"] == ["trial-x__agent", "trial-x__env"]
    assert result["inventory_complete"] is False


def test_recognizes_harbor_job_config_and_nested_trial_config_layout(tmp_path):
    job = tmp_path / "job-2026-01"
    trial = job / "trial-abc"
    trial.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({
        "job_name": job.name,
        "jobs_dir": str(tmp_path),
        "verifier": {"disable": True},
        "agents": [{"name": "oracle"}],
        "tasks": ["tasks/example"],
    }), encoding="utf-8")
    parent = json.loads((job / "config.json").read_text())
    task_path = str(tmp_path.parent / f"{tmp_path.name}-tasks" / "example")
    parent["tasks"] = [{"path": task_path}]
    (job / "config.json").write_text(json.dumps(parent), encoding="utf-8")
    task_dir = tmp_path.parent / f"{tmp_path.name}-tasks" / "example"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "docker-compose.yaml").write_text("services: {}\n")
    (trial / "config.json").write_text(json.dumps({
        "trial_name": trial.name,
        "job_id": "job-uuid",
        "trials_dir": str(job),
        "task": {"path": task_path},
    }), encoding="utf-8")

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "KNOWN"
    assert result["projects"] == ["trial-abc__agent", "trial-abc__env"]
    assert result["configs_seen"] == 1
    assert result["inventory_complete"] is False


def test_accounts_for_each_explicit_separate_verifier_session(tmp_path):
    write_config(tmp_path, trial="trial-1", steps=[
        {"name": "unit tests", "verifier": {"environment_mode": "separate"}},
        {"name": "integration", "verifier": {"environment": {"type": "docker"}}},
        {"name": "ordinary", "verifier": {"environment_mode": "shared"}},
    ])

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "KNOWN"
    assert "trial-1__verifier__unit_tests" in result["projects"]
    assert "trial-1__verifier__integration" in result["projects"]
    assert not any("ordinary" in project for project in result["projects"])


def test_step_inherits_task_separate_mode_unless_it_overrides_shared(tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps({
        "trial_name": "trial-1",
        "environment": {"type": "docker"},
        "task": {
            "verifier": {"environment_mode": "separate"},
            "steps": [
                {"name": "inherited", "verifier": {}},
                {"name": "shared", "verifier": {"environment_mode": "shared"}},
            ],
        },
    }), encoding="utf-8")

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "KNOWN"
    assert "trial-1__verifier__inherited" in result["projects"]
    assert not any("__verifier__shared" in project for project in result["projects"])


def test_real_harbor_trial_config_without_task_verifier_metadata_is_unknown(tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps({
        "trial_name": "trial-1",
        "task": {"path": "tasks/example"},
        "environment": {},
    }), encoding="utf-8")

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []


def test_bad_or_partial_json_fails_closed_without_partial_authorization(tmp_path):
    write_config(tmp_path, job="good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "config.json").write_text('{"trial_name": "half"', encoding="utf-8")

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []


def test_missing_config_in_job_directory_is_unknown_generation_race(tmp_path):
    write_config(tmp_path, job="complete")
    (tmp_path / "currently-generating").mkdir()

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []


def test_symlink_job_or_config_is_not_followed(tmp_path):
    outside = tmp_path.parent / "outside-trial"
    outside.mkdir()
    write_config(outside)
    (tmp_path / "linked-job").symlink_to(outside, target_is_directory=True)

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []


def test_config_symlink_is_unknown(tmp_path):
    outside = tmp_path.parent / "external-config.json"
    outside.write_text(json.dumps({"trial_name": "external", "task": {"steps": []}}))
    job = tmp_path / "job"
    job.mkdir()
    (job / "config.json").symlink_to(outside)

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []


def test_empty_scan_is_unknown_not_proof_of_no_projects(tmp_path):
    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "UNKNOWN"
    assert result["projects"] == []
    assert result["inventory_complete"] is False


def test_unrelated_top_level_files_are_ignored_but_do_not_complete_inventory(tmp_path):
    write_config(tmp_path)
    (tmp_path / "notes.txt").write_text("untracked job may exist")

    result = identify_trial_compose_projects(tmp_path)

    assert result["status"] == "KNOWN"
    assert result["inventory_complete"] is False


def test_terminal_single_trial_accounts_for_inventory(tmp_path):
    root = tmp_path / "jobs"
    job = root / "job"
    trial = job / "trial-1"
    trial.mkdir(parents=True)
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "docker-compose.yaml").write_text("services: {}")
    (job / "config.json").write_text(json.dumps({
        "job_name": job.name, "jobs_dir": str(root),
        "tasks": [{"path": str(task)}], "verifier": {"disable": True},
    }))
    (trial / "config.json").write_text(json.dumps({
        "trial_name": trial.name, "trials_dir": str(job), "task": {"path": str(task)},
    }))
    (trial / "result.json").write_text(json.dumps({
        "trial_name": trial.name, "finished_at": "2026-11-24T13:46:06Z",
    }))
    result = {
        "finished_at": "2026-11-24T13:46:07Z", "n_total_trials": 1,
        "stats": dict.fromkeys((
            "n_retries", "n_running_trials", "n_pending_trials",
            "n_cancelled_trials", "n_errored_trials",
        ), 0) | {"n_completed_trials": 1},
    }
    path = job / "result.json"
    path.write_text(json.dumps(result))
    assert identify_trial_compose_projects(root)["inventory_complete"] is True

    result["stats"]["n_retries"] = 1
    path.write_text(json.dumps(result))
    assert identify_trial_compose_projects(root)["inventory_complete"] is False
