import json

import pytest

from benchmarks.autonomous_v01 import full_arm_series as series


def _inputs(tmp_path):
    tasks = []
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for index in range(12):
        task_id = f"task-{index}"
        pair = {"pair_id": f"pair-{index}", "task": {"task_id": task_id},
                "arms": [{"system": "bananloop_full", "deadline_seconds": 3600}]}
        tasks.append(pair)
        (runtime / f"{task_id}.json").write_text(json.dumps({"candidate_path": "candidate.py"}))
    manifest = {"tasks": tasks, "candidate_source_sha": "a" * 40,
                "manifest_sha256": "b" * 64, "model": "gpt-6-luna"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, runtime


def test_series_runs_in_order_and_persists_each_result(tmp_path, monkeypatch):
    manifest, runtime = _inputs(tmp_path)
    calls = []

    def launch(**kwargs):
        calls.append(kwargs["task_id"])
        return {"result": {"status": "PROTOCOL_REJECTED"}, "cleanup_status": "CLEANED"}

    monkeypatch.setattr(series, "launch_full_arm", launch)
    receipt = series.run_full_series(
        manifest_path=manifest, preflight_path=tmp_path / "preflight.json",
        candidate_checkout=tmp_path, runtime_root=runtime,
        expected_manifest_sha256="b" * 64, candidate_source_sha="a" * 40,
        series_root=tmp_path / "series", python_binary=tmp_path / "python",
        harbor_binary=tmp_path / "harbor",
    )
    assert calls == [f"task-{index}" for index in range(12)]
    assert receipt["status"] == "COMPLETED"
    assert len(json.loads((tmp_path / "series" / "series.json").read_text())["arms"]) == 12


def test_series_stops_and_never_replays_unknown_arm(tmp_path, monkeypatch):
    manifest, runtime = _inputs(tmp_path)
    calls = []

    def launch(**kwargs):
        calls.append(kwargs["task_id"])
        return {"result": {"status": "INTERRUPTED_UNKNOWN"}, "cleanup_status": "UNKNOWN"}

    monkeypatch.setattr(series, "launch_full_arm", launch)
    arguments = dict(
        manifest_path=manifest, preflight_path=tmp_path / "preflight.json",
        candidate_checkout=tmp_path, runtime_root=runtime,
        expected_manifest_sha256="b" * 64, candidate_source_sha="a" * 40,
        series_root=tmp_path / "series", python_binary=tmp_path / "python",
        harbor_binary=tmp_path / "harbor",
    )
    receipt = series.run_full_series(**arguments)
    assert receipt["status"] == "STOPPED_UNKNOWN"
    assert calls == ["task-0"]
    again = series.run_full_series(**arguments)
    assert again["status"] == "STOPPED_UNKNOWN"
    assert calls == ["task-0"]
