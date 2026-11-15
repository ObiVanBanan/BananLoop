from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

import pytest

from benchmarks.autonomous_v01.runner import (
    ARM_SECONDS,
    CATALOGUE,
    TASK_IDS,
    CandidateArchive,
    CodexDirectBackend,
    UsageLedger,
    VerifiedCandidate,
    build_manifest,
    offline_preflight,
    parse_codex_jsonl_usage,
    run_fake_series,
)


def test_manifest_freezes_exact_curated_tasks_and_randomizes_only_arm_order():
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    manifest = build_manifest(catalogue=catalogue, seed=91, model="pinned-model",
                              reasoning="high", codex_version="0.169.0",
                              full_feature_flags={"mode": "Full", "benchmark_pivot_policy": "v1"})
    replay = build_manifest(catalogue=catalogue, seed=91, model="pinned-model",
                            reasoning="high", codex_version="0.169.0",
                            full_feature_flags={"mode": "Full", "benchmark_pivot_policy": "v1"})
    assert manifest == replay
    assert tuple(item["task"]["task_id"] for item in manifest["tasks"]) == TASK_IDS
    assert len(manifest["tasks"]) == 12
    assert sum(len(pair["arms"]) for pair in manifest["tasks"]) == 24
    assert all(arm["deadline_seconds"] == ARM_SECONDS
               for pair in manifest["tasks"] for arm in pair["arms"])
    assert manifest["call_limit_policy"] == "OBSERVE_ONLY"
    assert "reserved_calls" not in manifest


def test_manifest_rejects_missing_or_duplicate_catalogue_entries():
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="content digest"):
        build_manifest(catalogue={**catalogue, "tasks": catalogue["tasks"][:-1]}, seed=1,
                       model="m", reasoning="r", codex_version="v", full_feature_flags={"full": True})
    with pytest.raises(ValueError, match="integer"):
        build_manifest(catalogue=catalogue, seed=True, model="m", reasoning="r",
                       codex_version="v", full_feature_flags={"full": True})


def test_preflight_reports_missing_content_pins_without_launching_anything():
    report = offline_preflight(codex_auth_status="Logged in using ChatGPT",
                               codex_version="0.169.0", image_inspector=lambda _: None)
    assert report["status"] == "PREP_BLOCKED"
    assert report["planned_distinct_tasks"] == 12
    assert report["arms"] == 24
    assert report["call_limit_policy"] == "OBSERVE_ONLY"
    assert report["live_models_started"] is False
    assert report["harbor_started"] is False
    assert report["ai_judge_started"] is False
    assert any("content-pinned" in item for item in report["blockers"])
    assert sum("trusted fixture/protocol/Harbor adapter is incomplete" in item
               for item in report["blockers"]) == 3
    assert report["capacity"]["per_task_requested_cpus"] == 8
    assert report["capacity"]["per_task_requested_memory_mb"] == 14336


def test_usage_ledger_is_observational_and_does_not_enforce_a_call_cap(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    for _ in range(260):
        ledger.record("arm-a", "fake", {"model_turns_observed": 1,
                                           "usage": {"input_tokens": 2, "output_tokens": 3}})
    result = ledger.summary()
    assert result["observed_events"] == result["model_turns_observed"] == 260
    assert result["input_tokens"] == 520
    assert result["output_tokens"] == 780


def test_archive_selects_best_verified_in_deadline_candidate_and_rejects_forgery(tmp_path):
    archive = CandidateArchive(tmp_path / "archive")
    for source, score in ((b"worse", 0.2), (b"best", 0.8)):
        digest = hashlib.sha256(source).hexdigest()
        archive.save(VerifiedCandidate("arm-a", source, score, 90.0,
                                       {"candidate_sha256": digest, "evaluator": "fake-v1"}),
                     deadline_epoch=100.0)
    assert archive.best("arm-a")["score"] == 0.8
    late = b"late"
    with pytest.raises(ValueError, match="after arm deadline"):
        archive.save(VerifiedCandidate("arm-a", late, 1.0, 101.0,
                                       {"candidate_sha256": hashlib.sha256(late).hexdigest()}),
                     deadline_epoch=100.0)
    with pytest.raises(ValueError, match="does not bind"):
        archive.save(VerifiedCandidate("arm-a", b"forged", 1.0, 90.0, {}), deadline_epoch=100.0)


def test_fake_series_runs_24_isolated_arms_without_external_calls(tmp_path):
    tasks = [{"task_id": task_id, "arm_order": ["bananloop_full", "codex_direct"]}
             for task_id in TASK_IDS]

    class FakeBackend:
        def __init__(self, system):
            self.system = system

        def run(self, arm_id, task, arm_root, deadline_epoch, usage):
            assert self.system in arm_id
            assert task["task_id"] in arm_id
            assert arm_root.is_dir()
            assert deadline_epoch > 0
            return {"status": "FAKE_COMPLETE"}

    report = run_fake_series(tasks, tmp_path / "series", FakeBackend)
    assert report["status"] == "OFFLINE_FAKE_COMPLETE"
    assert len(report["runs"]) == 24
    assert report["usage"]["observed_events"] == 0
    assert report["harbor_evaluations"] == report["ai_judge_calls"] == 0
    roots = [tmp_path / "series" / run["arm_id"] for run in report["runs"]]
    assert len(set(roots)) == 24
    assert all(root.is_dir() and (root / "result.json").is_file() for root in roots)
    assert (tmp_path / "series" / "summary.json").is_file()


def test_fake_series_requires_new_state_root_and_exactly_12_distinct_tasks(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    with pytest.raises(ValueError, match="must be new"):
        run_fake_series([{"task_id": key} for key in TASK_IDS], root, lambda _: None)
    with pytest.raises(ValueError, match="exactly 12"):
        run_fake_series([{"task_id": key} for key in TASK_IDS[:-1]], tmp_path / "new", lambda _: None)


def test_codex_jsonl_counts_turns_as_observed_not_provider_calls(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}),
    ]) + "\n", encoding="utf-8")
    usage = parse_codex_jsonl_usage(path)
    assert usage == {"model_turns_observed": 2,
                     "usage": {"input_tokens": 13, "output_tokens": 6},
                     "usage_unknown": False}


def test_codex_jsonl_marks_malformed_tail_as_unknown(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":1}}\n{bad',
                    encoding="utf-8")
    usage = parse_codex_jsonl_usage(path)
    assert usage["model_turns_observed"] == 1
    assert usage["usage_unknown"] is True
    assert usage["usage"] is None


def test_codex_direct_backend_uses_fake_cli_and_enforces_deadline(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    subprocess.run(["git", "init", "-q", str(initial)], check=True)
    subprocess.run(["git", "-C", str(initial), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(initial), "config", "user.name", "Offline Test"], check=True)
    (initial / "README.md").write_text("initial tree\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(initial), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(initial), "commit", "-qm", "initial"], check=True)
    base_sha = subprocess.run(["git", "-C", str(initial), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()
    instruction = tmp_path / "instruction.md"
    instruction.write_text("fake task", encoding="utf-8")
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys,time\n"
        "prompt=sys.stdin.read()\n"
        "if 'sleep past deadline' in prompt: time.sleep(5)\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':3}}),flush=True)\n",
        encoding="utf-8")
    fake_codex.chmod(0o755)
    backend = CodexDirectBackend(
        codex_binary=str(fake_codex), model="fake-model", reasoning="high",
        task_instruction=instruction, dev_command=["python", "fake_dev.py"],
        initial_repo=initial, base_sha=base_sha,
    )
    usage = UsageLedger(tmp_path / "usage.sqlite3")
    complete_root = tmp_path / "complete"
    complete_root.mkdir()
    completed = backend.run("arm-fake", {"task_id": "task-fake"}, complete_root,
                            time.time() + 10, usage)
    assert completed["status"] == "COMPLETED"
    assert completed["model_turns_observed"] == 1
    assert (complete_root / "repo" / "README.md").read_text(encoding="utf-8") == "initial tree\n"

    (instruction).write_text("sleep past deadline", encoding="utf-8")
    timeout_root = tmp_path / "timeout"
    timeout_root.mkdir()
    timed_out = backend.run("arm-timeout", {"task_id": "task-fake"}, timeout_root,
                            time.time() + 0.5, usage)
    assert timed_out["status"] == "TIMED_OUT"
    assert timed_out["model_turns_observed"] == 0
