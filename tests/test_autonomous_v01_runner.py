from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from benchmarks.autonomous_v01.runner import (
    CandidateArchive, GlobalCallLedger, VerifiedCandidate, offline_preflight,
    build_manifest, run_fake_series,
)


def test_preflight_blocks_current_nine_task_selection(tmp_path):
    selection = {
        "tasks": [{"task_id": f"task-{i}"} for i in range(9)]
        + [{"task_id": "budgeted-covtype-dual-market-open"}],
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    result = offline_preflight(selection_path=path, codex_help="Usage: codex exec")
    assert result["status"] == "PREP_BLOCKED"
    assert result["live_models_started"] is result["harbor_started"] is False
    assert any("INSUFFICIENT_TASKS" in item for item in result["blockers"])
    assert any("provider-request cap" in item for item in result["blockers"])


def test_global_ledger_enforces_shared_and_per_arm_cap_atomically(tmp_path):
    ledger = GlobalCallLedger(tmp_path / "budget.sqlite", cap=4, arm_cap=3)
    errors = []

    def reserve(arm):
        try:
            ledger.reserve(arm)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=reserve, args=(f"arm-{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ledger.count() == 4
    assert len(errors) == 4
    arm_ledger = GlobalCallLedger(tmp_path / "arm-budget.sqlite", cap=10, arm_cap=3)
    for _ in range(3):
        arm_ledger.reserve("same-arm")
    with pytest.raises(RuntimeError, match="per-arm"):
        arm_ledger.reserve("same-arm")
    assert arm_ledger.count() == 3


def test_candidate_archive_keeps_best_verified_candidate_and_rejects_forgery(tmp_path):
    archive = CandidateArchive(tmp_path / "checkpoints")
    deadline = 100.0
    for source, score in ((b"worse", 0.2), (b"better", 0.8)):
        digest = hashlib.sha256(source).hexdigest()
        archive.save(VerifiedCandidate(
            "arm-a", source, score, 90.0,
            {"candidate_sha256": digest, "evaluator": "offline-fake-v1"},
        ), deadline_epoch=deadline)
    assert archive.best("arm-a")["score"] == 0.8
    source = b"late"
    with pytest.raises(ValueError, match="after arm deadline"):
        archive.save(VerifiedCandidate(
            "arm-a", source, 1.0, 101.0,
            {"candidate_sha256": hashlib.sha256(source).hexdigest()},
        ), deadline_epoch=deadline)
    source = b"unbound"
    with pytest.raises(ValueError, match="does not bind"):
        archive.save(VerifiedCandidate("arm-a", source, 1.0, 90.0, {}), deadline_epoch=deadline)


def test_fake_series_runs_both_isolated_arms_without_external_calls(tmp_path):
    tasks = [{"task_id": f"task-{i}"} for i in range(12)]

    class FakeBackend:
        def __init__(self, system):
            self.system = system

        def run(self, arm_id, task, deadline_epoch, call_budget):
            assert self.system in arm_id
            assert task["task_id"] in arm_id
            assert call_budget == 10
            return {"status": "FAKE_COMPLETE", "deadline_epoch": deadline_epoch}

    result = run_fake_series(tasks, tmp_path, FakeBackend)
    assert len(result["runs"]) == 24
    assert result["model_calls"] == result["harbor_evaluations"] == result["ai_judge_calls"] == 0
    assert result["status"] == "OFFLINE_FAKE_COMPLETE"


def test_manifest_selection_is_pinned_paired_and_reproducible():
    tasks = [{"task_id": f"task-{i:02}", "task_sha256": f"{i:064x}",
              "instruction_sha256": f"{i+1:064x}", "metric": "score",
              "direction": "higher", "adapter_sha256": f"{i+2:064x}"}
             for i in range(14)]
    kwargs = {"eligible_tasks": tasks, "seed": 117, "source_revision": "abc",
              "evaluator_digest": "def", "model": "pinned-chatgpt",
              "reasoning": "high"}
    first = build_manifest(**kwargs)
    second = build_manifest(**kwargs)
    assert first == second
    assert len(first["tasks"]) == 12
    assert sum(len(pair["arms"]) for pair in first["tasks"]) == 24
    assert first["reserved_calls"] + first["reserve_calls"] == 250
    assert all({arm["system"] for arm in pair["arms"]} ==
               {"bananloop_full", "codex_direct"} for pair in first["tasks"])
    with pytest.raises(ValueError, match="INSUFFICIENT_TASKS"):
        build_manifest(**{**kwargs, "eligible_tasks": tasks[:11]})
