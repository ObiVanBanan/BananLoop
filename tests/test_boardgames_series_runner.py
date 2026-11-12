import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.discoverybench_boardgames import series_runner


def _runner(tmp_path, process_runner):
    return series_runner.BoardGamesSeriesRunner(
        repo=tmp_path / "repo", series_dir=tmp_path / "series", run_id="offline-check-1",
        cli_prefix=("bananloop",), process_runner=process_runner,
        input_preflight=lambda _repo: [],
    )


def test_command_pins_task_and_limits_with_stable_distinct_request_ids(tmp_path):
    tasks = series_runner._load_manifest()[0]["tasks"]
    first = series_runner.build_command(
        cli_prefix=("uv", "run", "bananloop"), repo=tmp_path / "repo",
        series_dir=tmp_path / "series", run_id="run-01", task=tasks[0],
        model="gpt-6-luna", max_agent_calls=8, max_model_tokens=2048,
        approve_up_to_calls=6,
    )
    second = series_runner.build_command(
        cli_prefix=("uv", "run", "bananloop"), repo=tmp_path / "repo",
        series_dir=tmp_path / "series", run_id="run-01", task=tasks[1],
        model="gpt-6-luna", max_agent_calls=8, max_model_tokens=2048,
        approve_up_to_calls=6,
    )
    again = series_runner.build_command(
        cli_prefix=("uv", "run", "bananloop"), repo=tmp_path / "repo",
        series_dir=tmp_path / "series", run_id="run-01", task=tasks[0],
        model="gpt-6-luna", max_agent_calls=8, max_model_tokens=2048,
        approve_up_to_calls=6,
    )
    assert first == again
    assert first[first.index("--request-id") + 1] != second[second.index("--request-id") + 1]
    assert first[first.index("--boardgames-task-id") + 1] == tasks[0]["task_id"]
    assert first[first.index("--goal") + 1] == tasks[0]["question"]
    assert first[first.index("--max-iterations") + 1] == "5"
    assert first[first.index("--deadline-seconds") + 1] == "3600"
    assert first[first.index("--approve-up-to-calls") + 1] == "6"


def test_series_continues_after_cli_failure_and_records_reports_and_usage(tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        task_id = argv[argv.index("--boardgames-task-id") + 1]
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 7, "", "input files are not committed\n")
        result = {
            "status": "ITERATION_LIMIT",
            "campaign_id": f"c-{task_id}",
            "live_model_called": True,
            "request_history": [
                {"dispatched": True, "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}},
                {"dispatched": True, "usage": {"input_tokens": 30, "output_tokens": 7, "total_tokens": 37}},
            ],
            "report_path": f"/state/{task_id}/report.json",
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(result) + "\n", "")

    runner = _runner(tmp_path, fake_run)
    report = runner.run()
    assert len(calls) == 10
    assert report["series_status"] == "COMPLETED_WITH_FAILURES"
    assert report["tasks"][0]["status"] == "FAILED"
    assert report["tasks"][0]["command_status"] == "EXITED_NONZERO"
    assert "not committed" in (runner.series_dir / report["tasks"][0]["stderr_path"]).read_text()
    assert report["tasks"][1]["status"] == "ITERATION_LIMIT"
    assert report["tasks"][1]["command_status"] == "EXITED_ZERO"
    assert report["tasks"][1]["scientific_success"] is False
    assert report["tasks"][1]["campaign_status"] == "ITERATION_LIMIT"
    assert report["tasks"][1]["model_calls"] == 2
    assert report["tasks"][1]["token_usage"] == {
        "input_tokens": 50, "output_tokens": 12, "total_tokens": 62,
    }
    assert report["tasks"][1]["campaign_id"].startswith("c-")
    assert report["tasks"][1]["elapsed_seconds"] >= 0
    assert report["tasks"][1]["started_at"] and report["tasks"][1]["finished_at"]
    assert (runner.series_dir / report["tasks"][1]["result_path"]).is_file()
    assert all(call[1]["timeout"] == 3600 for call in calls)
    assert json.loads(runner.plan_path.read_text()) == report


def test_resume_keeps_identity_and_does_not_replay_interrupted_task(tmp_path):
    calls = 0

    def interrupt_first(argv, **_kwargs):
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    runner = _runner(tmp_path, interrupt_first)
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    on_disk = json.loads(runner.plan_path.read_text())
    first_id = on_disk["tasks"][0]["request_id"]
    assert on_disk["tasks"][0]["status"] == "RUNNING"

    resumed_calls = []

    def succeed(argv, **_kwargs):
        resumed_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "status": "ITERATION_LIMIT", "live_model_called": False,
            "usage": "UNKNOWN", "request_history": [],
        }), "")

    resumed = _runner(tmp_path, succeed)
    report = resumed.run()
    assert report["identity_sha256"] == on_disk["identity_sha256"]
    assert report["tasks"][0]["status"] == "INTERRUPTED_UNKNOWN"
    assert report["tasks"][0]["command_status"] == "INTERRUPTED_UNKNOWN"
    assert report["tasks"][0]["request_id"] == first_id
    assert len(resumed_calls) == 9
    assert report["tasks"][0]["model_calls"] == "UNKNOWN"
    assert report["tasks"][1]["status"] == "ITERATION_LIMIT"
    assert report["series_status"] == "INCOMPLETE"


def test_manifest_or_config_change_cannot_resume_an_existing_series(tmp_path):
    def succeed(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "status": "ITERATION_LIMIT", "live_model_called": False,
            "usage": "UNKNOWN", "request_history": [],
        }), "")

    runner = _runner(tmp_path, succeed)
    initial = runner._load_or_create()
    assert initial["schema"] == series_runner.SCHEMA
    different = series_runner.BoardGamesSeriesRunner(
        repo=tmp_path / "repo", series_dir=tmp_path / "series", run_id="offline-check-1",
        cli_prefix=("bananloop",), model="different-model", process_runner=succeed,
    )
    with pytest.raises(ValueError, match="identity"):
        different._load_or_create()


def test_uncommitted_pilot_inputs_record_not_started_without_invoking_cli(tmp_path):
    invocations = []

    def forbidden_run(argv, **_kwargs):
        invocations.append(argv)
        raise AssertionError("CLI must not start while source inputs are uncommitted")

    runner = series_runner.BoardGamesSeriesRunner(
        repo=tmp_path / "not-a-git-repo", series_dir=tmp_path / "series",
        run_id="preflight-only", process_runner=forbidden_run,
    )
    report = runner.run()
    assert invocations == []
    assert report["series_status"] == "COMPLETED_WITH_FAILURES"
    assert all(item["status"] == "NOT_STARTED" for item in report["tasks"])
    assert all(item["command_status"] == "NOT_STARTED" for item in report["tasks"])
    assert all(item["command_started"] is False for item in report["tasks"])
    assert all(item["model_calls"] == 0 for item in report["tasks"])
    assert all(item["error_type"] == "PilotInputsNotCommitted" for item in report["tasks"])
    assert all((runner.series_dir / item["stderr_path"]).is_file() for item in report["tasks"])


@pytest.mark.parametrize("campaign_status", [
    "PROTOCOL_REVIEW_PENDING", "CALL_ALLOWANCE_EXHAUSTED",
])
def test_zero_exit_nonterminal_campaign_status_is_not_success(tmp_path, campaign_status):
    calls = 0

    def fake_run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        task_id = argv[argv.index("--boardgames-task-id") + 1]
        result = {
            "status": campaign_status if calls == 1 else "ITERATION_LIMIT",
            "campaign_id": f"c-{task_id}",
            "live_model_called": False,
            "usage": "UNKNOWN",
            "request_history": [],
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    runner = _runner(tmp_path, fake_run)
    report = runner.run()
    first = report["tasks"][0]
    assert first["command_status"] == "EXITED_ZERO"
    assert first["campaign_status"] == campaign_status
    assert first["status"] == "INCOMPLETE"
    assert first["error_type"] == "CampaignDidNotReachAcceptableTerminalStatus"
    assert report["series_status"] == "INCOMPLETE"
    assert all(item["status"] == "ITERATION_LIMIT" for item in report["tasks"][1:])
    assert calls == 10
