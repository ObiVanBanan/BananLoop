import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402


def test_frozen_context_has_three_positive_unpromoted_dev_trials():
    spec, campaign, rows = runner.frozen_context()
    state = runner.build_research_state(spec, campaign, rows)
    assert [json.loads(row["decision_json"])["delta"] for row in rows] == [0.0002, 0.0003, 0.0004]
    assert [row["status"] for row in rows] == ["REJECTED"] * 3
    assert state.dev_non_improve_streak == 0
    assert state.champion_stagnation_streak == 3
    assert state.planning_mode == "reframe"
    assert json.loads(campaign["champion_json"])["metrics"]["quality"] == 0.5


def test_cpu_fixture_baseline_is_half():
    sys.path.insert(0, str(runner.FIXTURE))
    try:
        evaluator = runpy.run_path(str(runner.FIXTURE / "evaluator.py"))
        assert evaluator["evaluate"]() == 0.5
    finally:
        sys.path.pop(0)


def test_condition_requests_change_only_the_planning_mode():
    exploit, _, state = runner.proposer_request("exploit")
    reframe, _, _ = runner.proposer_request("reframe")
    assert exploit["task_document"]["status"] == "available"
    assert exploit["task_document"]["content"] == (runner.FIXTURE / "TASK.md").read_text()
    assert exploit["task_document"]["sha256"] == hashlib.sha256(exploit["task_document"]["content"].encode()).hexdigest()
    assert state.champion_stagnation_streak == 3
    assert state.planning_mode == "exploit"
    assert reframe["research_state"]["planning_mode"] == "reframe"
    exploit["planning_mode"] = reframe["planning_mode"]
    exploit["research_state"]["planning_mode"] = reframe["research_state"]["planning_mode"]
    assert exploit == reframe


class FakeAdapter:
    def __init__(self, seen):
        self.seen = seen

    def execute(self, task, workspace, timeout):
        self.seen.append((task, workspace, timeout))
        assert (workspace / "TASK.md").is_file()
        assert (workspace / "solution.py").is_file()
        assert not (workspace / "evaluator.py").exists()
        return {"candidates": []}


def test_runner_makes_six_isolated_adapter_calls_and_persists_requests(tmp_path):
    seen = []
    result = runner.run(tmp_path / "out", adapter_factory=lambda: FakeAdapter(seen))
    assert result["calls"] == 6
    assert result["completed"] == 6
    assert len(seen) == 6
    assert len({str(workspace) for _, workspace, _ in seen}) == 6
    assert all(timeout == 900 for _, _, timeout in seen)
    run_files = list((tmp_path / "out").glob("*/run.json"))
    assert len(run_files) == 6
    assert all(json.loads(p.read_text())["error"] is None for p in run_files)
    assert len(list((tmp_path / "out").glob("*/request.json"))) == 6
    assert len(list((tmp_path / "out").glob("*/selection.json"))) == 6
    assert {json.loads(p.read_text())["strategy"] for p in (tmp_path / "out").glob("*/selection.json")} == {"exploit", "reframe"}
    assert (tmp_path / "out" / "rubric.json").is_file()
