"""Regression coverage for terminal verdicts and crash/retry boundaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import bananloop.workspace as workspace_module

from bananloop.agents.base import AgentError
from bananloop.cli import cmd_stop
from bananloop.contracts import EvaluationEvidence, EvaluationResult, PairedComparison
from bananloop.engine import ControlledInterruption, Engine, EngineError
from bananloop.evaluator_binding import canonical_hash
from bananloop.evaluators.command import EvaluatorError
from bananloop.policy import decide
from bananloop.process import ProcessError, run_process
from bananloop.report import build_report, render_markdown
from bananloop.store import CampaignStore
from bananloop.workspace import WorkspaceError
from test_engine import make_engine, make_staged_engine, git


class FixedHoldout:
    def __init__(self, quality=0):
        self.quality = quality

    def evaluate(self, workspace, timeout):
        return EvaluationResult(metrics={"quality": self.quality, "regressions": 0}, checks={"tests": True})


def test_failed_holdout_never_reports_goal_met_or_reopens_search(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        engine.holdout_evaluator = FixedHoldout()
        result = engine.run()
        assert result["status"] == "STOPPED"
        assert result["stop_reason"] == "FINAL_HOLDOUT_REJECTED"
        assert build_report(store)["final_assessment"] == "REJECTED"
        calls = result["agent_calls"], result["evaluator_calls"]
        assert engine.run(resume=True)["stop_reason"] == "FINAL_HOLDOUT_REJECTED"
        assert calls == (store.campaign()["agent_calls"], store.campaign()["evaluator_calls"])
        assert len(store.evaluations("holdout")) == 1
    finally:
        store.close()


def test_unavailable_signal_sanity_downgrades_target_reaching_holdout_to_dev_only(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        outcome = engine.run()
        assert outcome["status"] == "GOAL_MET"
        spec = store.spec()
        legacy_hash = spec.approved_hash()
        spec.signal_sanity_status = "UNAVAILABLE"
        spec.signal_sanity_receipt_sha256 = "a" * 64
        # Optional None defaults are omitted, preserving the pre-signal hash.
        spec_without_signal = spec.model_copy(deep=True)
        spec_without_signal.signal_sanity_status = None
        spec_without_signal.signal_sanity_receipt_sha256 = None
        assert spec_without_signal.approved_hash() == legacy_hash
        store.update_campaign(
            spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash()
        )
        report = build_report(store)
        assert report["evaluation_protocol"]["holdout"]["target_met"] is True
        assert report["final_assessment"] == "DEV_ONLY"
        assert report["signal_sanity"] == {
            "status": "UNAVAILABLE",
            "receipt_sha256": "a" * 64,
            "evidence_level": "unavailable",
        }
        markdown = render_markdown(report)
        assert "## Evaluator signal sanity" in markdown
        assert "`UNAVAILABLE`" in markdown
        assert "`" + "a" * 64 + "`" in markdown
    finally:
        store.close()


def test_reference_evidence_receipt_is_durable_and_tampering_downgrades_trust(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        assert engine.run()["status"] == "GOAL_MET"
        state = store.path.parent
        evidence_dir = state / "reference-evidence"
        evidence_dir.mkdir()
        manifest = b'{"approved":true}\n'
        (evidence_dir / "operator-reference.json").write_bytes(manifest)
        readiness = b'{"status":"PASSED"}\n'
        (evidence_dir / "readiness-receipt.json").write_bytes(readiness)
        receipt_body = {"schema_version": 1, "checks": {"protocol_matches": True}}
        receipt_body["receipt_sha256"] = canonical_hash(receipt_body)
        receipt_path = evidence_dir / "verification-receipt.json"
        receipt_path.write_text(json.dumps(receipt_body, sort_keys=True, separators=(",", ":")))

        spec = store.spec()
        spec.signal_sanity_status = "REFERENCE_VERIFIED"
        spec.signal_sanity_receipt_sha256 = receipt_body["receipt_sha256"]
        spec.evaluation_evidence = EvaluationEvidence(
            technical_readiness="READY", trust_level="INDEPENDENTLY_VALIDATED",
            case_source="operator fixtures", label_source="operator labels",
            reference_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            reference_approved_by="operator",
            reference_receipt_sha256=receipt_body["receipt_sha256"],
            reference_receipt_path="reference-evidence/verification-receipt.json",
            readiness_receipt_sha256=hashlib.sha256(readiness).hexdigest(),
            independently_validated_tiers=["dev"],
        )
        store.update_campaign(spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash())
        report = build_report(store)
        assert report["evaluator_evidence"]["trust_level"] == "INDEPENDENTLY_VALIDATED"
        assert report["signal_sanity"]["reference_receipt_integrity"] is True

        receipt_path.write_text("{}", encoding="utf-8")
        report = build_report(store)
        assert report["evaluator_evidence"]["trust_level"] == "UNVERIFIED"
        assert report["signal_sanity"]["reference_receipt_integrity"] is False
    finally:
        store.close()


def test_dev_only_independent_validation_does_not_report_scientific_goal_met(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        assert engine.run()["status"] == "GOAL_MET"
        state = store.path.parent
        evidence_dir = state / "reference-evidence"
        evidence_dir.mkdir()
        manifest = b'{"approved":true}\n'
        (evidence_dir / "operator-reference.json").write_bytes(manifest)
        readiness = b'{"status":"PASSED"}\n'
        (evidence_dir / "readiness-receipt.json").write_bytes(readiness)
        receipt_body = {"schema_version": 1, "checks": {"protocol_matches": True}}
        receipt_body["receipt_sha256"] = canonical_hash(receipt_body)
        (evidence_dir / "verification-receipt.json").write_text(
            json.dumps(receipt_body, sort_keys=True, separators=(",", ":"))
        )
        spec = store.spec()
        spec.signal_sanity_status = "REFERENCE_VERIFIED"
        spec.signal_sanity_receipt_sha256 = receipt_body["receipt_sha256"]
        spec.evaluation_evidence = EvaluationEvidence(
            technical_readiness="READY", trust_level="INDEPENDENTLY_VALIDATED",
            case_source="operator fixtures", label_source="operator labels",
            reference_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            reference_approved_by="operator",
            reference_receipt_sha256=receipt_body["receipt_sha256"],
            reference_receipt_path="reference-evidence/verification-receipt.json",
            readiness_receipt_sha256=hashlib.sha256(readiness).hexdigest(),
            independently_validated_tiers=["dev"],
        )
        store.update_campaign(spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash())
        report = build_report(store)
        assert report["campaign_outcome"] == "GOAL_MET"
        assert report["evaluator_evidence"]["trust_level"] == "INDEPENDENTLY_VALIDATED"
        assert report["final_assessment"] == "DEV_ONLY"
        assert report["scientific_assessment"] == "DEV_ONLY"
        assert report["scientific_outcome"] == "UNVERIFIED_EVIDENCE"
    finally:
        store.close()


def test_report_counts_failed_and_unknown_bootstrap_evaluator_attempts(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        assert engine.run()["status"] == "GOAL_MET"
        bootstrap_path = store.path.parent / "bootstrap-plan.json"
        bootstrap_path.write_text(json.dumps({"bootstrap_attempts": [{
            "readiness": {"evaluator_invocations": [
                {"action_id": "smoke-1", "status": "COMPLETED"},
                {"action_id": "smoke-2", "status": "FAILED"},
                {"action_id": "full", "status": "UNKNOWN"},
            ]}
        }]}))
        resources = build_report(store)["resources"]["bootstrap"]
        assert resources["readiness_evaluator_calls"] == 3
        assert resources["readiness_evaluator_attempted"] == 3
        assert resources["readiness_evaluator_completed"] == 1
        assert resources["readiness_evaluator_failed"] == 1
        assert resources["readiness_evaluator_unknown"] == 1
    finally:
        store.close()


def test_bootstrap_controlled_signal_labels_are_diagnostic_not_an_assessment_upgrade(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        assert engine.run()["status"] == "GOAL_MET"
        legacy_assessment = build_report(store)["final_assessment"]
        spec = store.spec()
        spec.signal_sanity_status = "DIAGNOSTIC_ONLY"
        spec.signal_sanity_receipt_sha256 = "b" * 64
        store.update_campaign(spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash())

        report = build_report(store)
        assert report["final_assessment"] == legacy_assessment
        assert report["signal_sanity"]["status"] == "DIAGNOSTIC_ONLY"
        assert report["signal_sanity"]["evidence_level"] == "diagnostic_only_unverified_labels"
    finally:
        store.close()


def test_final_receipt_replays_at_exact_call_limit_without_agent(tmp_path, monkeypatch):
    store, engine, _, _ = make_staged_engine(tmp_path, max_evaluator_calls=8)
    record = store.record_evaluation

    def crash_before_holdout_record(*args, **kwargs):
        if kwargs.get("tier") == "holdout":
            raise ControlledInterruption("receipt exists, tier row does not")
        return record(*args, **kwargs)

    try:
        monkeypatch.setattr(store, "record_evaluation", crash_before_holdout_record)
        with pytest.raises(ControlledInterruption):
            engine.run()
        assert store.campaign()["stage"] == "FINAL_HOLDOUT"
        assert store.campaign()["evaluator_calls"] == 8
        assert store.action("final:holdout")["status"] == "COMPLETED"
        monkeypatch.setattr(store, "record_evaluation", record)
        engine = Engine(store, engine.workspace)
        monkeypatch.setattr(engine.agent, "probe", lambda: pytest.fail("finalization must not probe an agent"))
        monkeypatch.setattr(engine.holdout_evaluator, "evaluate", lambda *a, **k: pytest.fail("duplicate holdout"))
        assert engine.run(resume=True)["status"] == "GOAL_MET"
        assert store.campaign()["evaluator_calls"] == 8
        assert len(store.evaluations("holdout")) == 1
    finally:
        store.close()


def test_final_failure_after_no_hypothesis_resumes_only_holdout(tmp_path, monkeypatch):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        monkeypatch.setattr(engine.agent, "execute", lambda *a, **k: {"status": "NO_HYPOTHESIS"})

        def fail(*args, **kwargs):
            raise EvaluatorError("temporary service failure")

        monkeypatch.setattr(engine.holdout_evaluator, "evaluate", fail)
        with pytest.raises(EngineError, match="final holdout failed"):
            engine.run()
        assert store.campaign()["stage"] == "FINAL_HOLDOUT"
        assert store.campaign()["stop_reason"] == "NO_HYPOTHESIS"
        engine.holdout_evaluator = FixedHoldout()
        monkeypatch.setattr(engine.agent, "probe", lambda: pytest.fail("search reopened after holdout"))
        out = engine.run(resume=True, retry_failed_actions=True)
        assert out["stop_reason"] == "NO_HYPOTHESIS"
        assert out["agent_calls"] == 1
        assert out["attempt_count"] == 1
        assert len(store.evaluations("holdout")) == 1
    finally:
        store.close()


def test_promotion_crash_finishes_accounting_once_before_goal(tmp_path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)

    def crash(stage):
        if stage == "RECORD" and json.loads(store.campaign()["champion_json"])["metrics"]["quality"]["value"] == 3:
            raise ControlledInterruption("after final promotion transaction")

    try:
        monkeypatch.setattr(engine, "_checkpoint", crash)
        with pytest.raises(ControlledInterruption):
            engine.run()
        interrupted = store.campaign()
        assert interrupted["experiment_count"] == 2
        assert interrupted["stage"] == "RECORD"
        engine = Engine(store, engine.workspace)
        out = engine.run(resume=True)
        assert out["status"] == "GOAL_MET"
        assert out["experiment_count"] == 3
        assert out["active_attempt_id"] is None
        assert store.experiments()[-1]["status"] == "ACCEPTED"
        assert sum(e["event_type"] == "CHAMPION_PROMOTED" for e in store.events()) == 2
        store.finish_attempt(interrupted["active_attempt_id"], interrupted["active_experiment_id"],
                             status="ACCEPTED", scientific=True, improved=True)
        assert store.campaign()["experiment_count"] == 3
    finally:
        store.close()


def test_completed_proposal_replays_original_budget_context(tmp_path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    set_stage = store.set_stage

    def crash(attempt, stage):
        if stage == "EXECUTE":
            raise ControlledInterruption("completed proposal before stage transition")
        return set_stage(attempt, stage)

    try:
        monkeypatch.setattr(store, "set_stage", crash)
        with pytest.raises(ControlledInterruption):
            engine.run()
        assert store.action("exp:1:propose")["status"] == "COMPLETED"
        assert store.action("exp:1:propose")["request_json"]
        monkeypatch.setattr(store, "set_stage", set_stage)
        out = Engine(store, engine.workspace).run(resume=True)
        assert out["status"] == "GOAL_MET"
        assert out["agent_calls"] == 6
        assert [row["status"] for row in store.experiments()] == ["ACCEPTED", "REJECTED", "ACCEPTED"]
    finally:
        store.close()


def test_failed_proposal_retries_with_frozen_request(tmp_path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    execute = engine.agent.execute

    def fail(*args, **kwargs):
        raise AgentError("transient transport failure")

    try:
        monkeypatch.setattr(engine.agent, "execute", fail)
        with pytest.raises(EngineError, match="propose failed"):
            engine.run()
        request = store.action("exp:1:propose")["request_json"]
        monkeypatch.setattr(engine.agent, "execute", execute)
        out = engine.run(resume=True, retry_failed_actions=True)
        assert out["status"] == "GOAL_MET"
        assert out["agent_calls"] == 7
        assert store.action("exp:1:propose")["request_json"] == request
    finally:
        store.close()


def test_retry_cannot_spend_final_evaluator_call(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path, max_evaluator_calls=4)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        exp, attempt = store.create_attempt(store.campaign()["champion_snapshot"])
        (engine.workspace.workspace / "value.txt").write_text("1\n")
        snapshot = engine.workspace.snapshot("candidate")
        store.update_experiment(exp, candidate_snapshot=snapshot)
        store.set_stage(attempt, "EVALUATE")

        def fail():
            raise EvaluatorError("failed before measurement")

        with pytest.raises(EvaluatorError):
            engine._action(action_id=f"exp:{exp}:evaluate", experiment_id=exp,
                           stage="EVALUATE", kind="evaluator",
                           request=engine._evaluation_request(snapshot), invoke=fail)
        assert store.campaign()["evaluator_calls"] == 3
        out = engine.run(resume=True, retry_failed_actions=True)
        assert out["stop_reason"] == "BUDGET_EXHAUSTED"
        assert out["evaluator_calls"] == 4
        assert out["active_attempt_id"] is None
        assert store.action(f"exp:{exp}:evaluate")["status"] == "FAILED"
        assert len(store.evaluations("holdout")) == 1
        assert engine.workspace.head() == out["champion_snapshot"]
    finally:
        store.close()


def test_deadline_cancels_live_evaluation_and_does_not_launch_holdout(tmp_path):
    store, engine, _, _ = make_staged_engine(tmp_path)

    class SlowEvaluator:
        def evaluate(self, workspace, timeout):
            run_process([sys.executable, "-c", "import time; time.sleep(30)"], cwd=workspace, timeout=30)
            pytest.fail("deadline failed to cancel evaluator")

    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine.evaluator = SlowEvaluator()
        store.update_campaign(created_at=(datetime.now(timezone.utc) - timedelta(seconds=engine.spec.budget.deadline_seconds - 1)).isoformat())
        started = time.monotonic()
        out = engine.run()
        assert time.monotonic() - started < 5
        assert out["stop_reason"] == "DEADLINE_REACHED"
        assert out["evaluator_calls"] == 1
        assert store.action("baseline:evaluate")["status"] == "CANCELLED"
        assert store.action("final:holdout") is None
        assert store.resource_summary()["open_reservations"] == 0
    finally:
        store.close()


def test_stop_cli_cancels_running_agent_and_preserves_source(tmp_path):
    store, engine, repo, base = make_engine(tmp_path)
    ready = tmp_path / "agent-started"
    script = tmp_path / "blocking-agent.py"
    script.write_text("import time\nfrom pathlib import Path\n" + f"Path({str(ready)!r}).write_text('ready')\n" + "time.sleep(30)\n")
    spec = store.spec()
    spec.agent.command = [sys.executable, str(script)]
    store.update_campaign(spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash())
    root = store.path.parent
    store.close()
    proc = subprocess.Popen([sys.executable, "-m", "bananloop.cli", "run", "--repo", str(repo), "--state-dir", str(root)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "agent did not start"
        assert cmd_stop(argparse.Namespace(repo=str(repo), state_dir=str(root))) == 0
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0, stdout + stderr
        store = CampaignStore(root / "campaign.sqlite3")
        assert store.campaign()["stop_reason"] == "USER_STOPPED"
        assert store.campaign()["active_attempt_id"] is None
        assert store.action("exp:1:propose")["status"] == "CANCELLED"
        assert store.resource_summary()["open_reservations"] == 0
        assert git(["rev-parse", "HEAD"], repo) == base
        assert git(["status", "--porcelain"], repo) == ""
        store.close()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


def test_agent_commits_and_untracked_changes_are_transferred(tmp_path, monkeypatch):
    store, engine, repo, base = make_engine(tmp_path)
    original_run_process = workspace_module.run_process
    diff_commands = []

    def capture_diff_command(argv, **kwargs):
        if argv[:2] == ["git", "diff"] and "--binary" in argv:
            diff_commands.append(list(argv))
        return original_run_process(argv, **kwargs)

    monkeypatch.setattr(workspace_module, "run_process", capture_diff_command)
    try:
        engine.workspace.ensure(base)
        isolated = engine.workspace.prepare_agent_workspace(["evaluator.py"])
        (isolated / "value.txt").write_text("1\n")
        git(["add", "value.txt"], isolated)
        git(["commit", "-m", "agent commits implementation"], isolated)
        (isolated / "new.txt").write_text("also transfer this\n")
        changed = engine.workspace.apply_agent_changes(isolated, ["evaluator.py"])
        assert set(changed) == {"value.txt", "new.txt"}
        assert (engine.workspace.workspace / "value.txt").read_text() == "1\n"
        assert (engine.workspace.workspace / "new.txt").exists()
        assert git(["rev-parse", "HEAD"], repo) == base
        assert diff_commands
        assert all(
            "--no-ext-diff" in argv
            and "--no-textconv" in argv
            and "--src-prefix=a/" in argv
            and "--dst-prefix=b/" in argv
            for argv in diff_commands
        )
    finally:
        engine.workspace.cleanup_agent_workspace()
        store.close()


def test_agent_patch_transfer_ignores_global_git_noprefix(tmp_path, monkeypatch):
    store, engine, _, base = make_engine(tmp_path)
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[diff]\n\tnoprefix = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    try:
        engine.workspace.ensure(base)
        isolated = engine.workspace.prepare_agent_workspace(["evaluator.py"])
        (isolated / "value.txt").write_text("prefix-safe transfer\n", encoding="utf-8")
        binary = isolated / "opaque.bin"
        binary.write_bytes(b"\x00\xFFbinary payload")
        pycache = isolated / "__pycache__"
        pycache.mkdir()
        bytecode = pycache / "slug.cpython-313.pyc"
        bytecode.write_bytes(b"\x00\xFFbinary artifact")
        changed = engine.workspace.apply_agent_changes(isolated, ["evaluator.py"])
        assert set(changed) == {"value.txt", "opaque.bin"}
        assert (engine.workspace.workspace / "value.txt").read_text(encoding="utf-8") == "prefix-safe transfer\n"
        assert (engine.workspace.workspace / binary.name).read_bytes() == binary.read_bytes()
        assert not (engine.workspace.workspace / "__pycache__").exists()
    finally:
        engine.workspace.cleanup_agent_workspace()
        store.close()


def test_agent_patch_transfer_preserves_baseline_bytecode(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    tracked = cache / "tracked.pyc"
    generated = cache / "generated.pyc"
    tracked.write_bytes(b"baseline bytecode")
    generated.write_bytes(b"agent runtime artifact")

    workspace_module.WorkspaceManager._remove_untracked_python_bytecode(
        tmp_path, {"__pycache__/tracked.pyc"}
    )

    assert tracked.read_bytes() == b"baseline bytecode"
    assert not generated.exists()


def test_agent_patch_apply_process_error_is_reported_as_workspace_error(tmp_path, monkeypatch):
    store, engine, _, base = make_engine(tmp_path)
    original_run_process = run_process

    def fail_apply(argv, **kwargs):
        if argv[:2] == ["git", "apply"]:
            raise ProcessError("git apply rejected malformed patch")
        return original_run_process(argv, **kwargs)

    monkeypatch.setattr("bananloop.workspace.run_process", fail_apply)
    try:
        engine.workspace.ensure(base)
        isolated = engine.workspace.prepare_agent_workspace(["evaluator.py"])
        (isolated / "value.txt").write_text("1\n", encoding="utf-8")
        with pytest.raises(WorkspaceError, match="candidate change transfer.*git apply rejected") as error:
            engine.workspace.apply_agent_changes(isolated, ["evaluator.py"])
        assert "changed_paths=value.txt" in str(error.value)
        assert re.search(r"patch_sha256=[0-9a-f]{64}", str(error.value))
    finally:
        engine.workspace.cleanup_agent_workspace()
        store.close()


def test_multiple_paired_bounds_cannot_overwrite_failed_predicate(tmp_path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.paired_comparison = PairedComparison(case_count=1, metric="quality", min_wins=1,
            guardrails=[{"metric": "quality", "operator": ">=", "bound": 2},
                        {"metric": "quality", "operator": "<=", "bound": 5}])

        def result(value):
            return EvaluationResult(metrics={"quality": value, "regressions": 0}, checks={"tests": True},
                protocol_sha256="a" * 64,
                cases=[{"case_id": "one", "input_sha256": "b" * 64, "metrics": {"quality": value}}])

        decision = decide(engine.spec, result(0), result(1))
        assert not decision.accepted
        guards = [value for name, value in decision.predicates.items() if name.startswith("paired:guard:")]
        assert guards == [False, True]
    finally:
        store.close()
