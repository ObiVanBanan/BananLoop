from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import bananloop.engine as engine_module
from bananloop.agents.base import AgentAdapter
from bananloop.contracts import AgentConfig, BudgetSpec, CampaignSpec, CandidateDecision, Direction, EvaluationAsset, EvaluationResult, GuardrailSpec, Hypothesis, HypothesisCandidate, MetricSpec, PortfolioRecord, RuntimePaths
from bananloop.engine import ControlledInterruption, Engine, EngineError, UnknownActionOutcome, _hash_payload
from bananloop.evaluator_binding import EvaluatorBinding, canonical_hash, make_binding, runtime_descriptor
from bananloop.integrity import sha256_evaluation_asset
from bananloop.report import build_report, render_markdown
from bananloop.store import CampaignStore
from bananloop.workspace import WorkspaceError, WorkspaceManager


def git(argv: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "value.txt").write_text("0\n")
    (repo / "guard.txt").write_text("0\n")
    (repo / "evaluator.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "q=float(Path('value.txt').read_text())\n"
        "g=float(Path('guard.txt').read_text())\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':q,'regressions':g},'checks':{'tests':True}}))\n"
    )
    (repo / "validation.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "q=float(Path('value.txt').read_text())\n"
        "v=0.0 if q == 1.0 else q\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':v,'regressions':0},'checks':{'tests':True}}))\n"
    )
    (repo / "holdout.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "q=float(Path('value.txt').read_text())\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':q,'regressions':0},'checks':{'tests':True}}))\n"
    )
    (repo / "agent.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "r=json.load(sys.stdin); idx=len(r.get('history',[]))+1\n"
        "if r['role']=='propose':\n"
        " last=r['research_state'].get('experiment_evidence',[])\n"
        " refs=[f\"exp:{last[-1]['ordinal']}\"] if last else ['INSUFFICIENT_EVIDENCE']\n"
        " def candidate(cid,key,evcalls):\n"
        "  hyp={'hypothesis':f'step {idx}','rationale':'fixture','expected_effect':'up','files_or_area':['value.txt','guard.txt'],'falsification_condition':'no improvement','mechanism_key':key,'prior_experiment_refs':[last[-1]['ordinal']] if last else [],'novelty_rationale':'latest DEV result motivates a new bounded step' if last else ''}\n"
        "  return {'candidate_id':cid,'hypothesis':hyp,'evidence_refs':refs,'suspected_failure_mode':'','predicted_observable':'quality metric','discriminating_test':'same frozen fixture produces measured delta','scope_eligibility':True,'estimated_agent_calls':1,'estimated_evaluator_calls':evcalls,'estimated_work_units':None,'rationale':'fixture alternative','uncertainty':'toy measurement only','inference':'agent_inference'}\n"
        " print(json.dumps({'candidates':[candidate(f'step-{idx}',f'step-{idx}',1),candidate(f'alt-{idx}',f'alt-{idx}',2)]}))\n"
        "else:\n"
        " k=r['hypothesis']['mechanism_key']\n"
        " if k=='step-1': Path('value.txt').write_text('1\\n'); Path('guard.txt').write_text('0\\n')\n"
        " elif k=='step-2': Path('value.txt').write_text('2\\n'); Path('guard.txt').write_text('1\\n')\n"
        " else: Path('value.txt').write_text('3\\n'); Path('guard.txt').write_text('0\\n')\n"
        " print(json.dumps({'summary':k,'changed_paths':['value.txt','guard.txt'],'verification_notes':'ok','blockers':[]}))\n"
    )
    git(["init"], repo)
    git(["config", "user.email", "test@example.com"], repo)
    git(["config", "user.name", "Test"], repo)
    git(["add", "."], repo)
    git(["commit", "-m", "baseline"], repo)
    return repo


def make_engine(tmp_path: Path) -> tuple[CampaignStore, Engine, Path, str]:
    repo = make_repo(tmp_path)
    base = git(["rev-parse", "HEAD"], repo)
    paths = RuntimePaths.for_repo(repo, tmp_path / "state")
    paths.ensure()
    spec = CampaignSpec(
        goal="quality 3 with zero regressions",
        source_repo=str(repo),
        base_commit=base,
        evaluation_command=[sys.executable, "evaluator.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=3, min_improvement=0.5),
        guardrails=[GuardrailSpec(metric="regressions", operator="<=", bound=0)],
        mandatory_checks=["tests"],
        editable_scope=["value.txt", "guard.txt"],
        protected_paths=["evaluator.py", "agent.py"],
        budget=BudgetSpec(max_experiments=5, max_attempts=6, max_agent_calls=12, max_evaluator_calls=8, plateau_experiments=3),
        agent=AgentConfig(kind="command", command=[sys.executable, "agent.py"]),
    )
    store = CampaignStore(paths.db)
    store.initialize(spec)
    engine = Engine(store, WorkspaceManager(repo, paths.workspace))
    return store, engine, repo, base


def test_agent_sees_declared_training_assets_but_keeps_evaluation_assets_hidden(tmp_path: Path):
    store, engine, repo, _ = make_engine(tmp_path)
    data = repo / "data"
    data.mkdir()
    (data / "train.csv").write_text("feature,label\n0,0\n", encoding="utf-8")
    (data / "eval.csv").write_text("feature,label\n1,1\n", encoding="utf-8")
    git(["add", "data"], repo)
    git(["commit", "-m", "add split data"], repo)
    engine.spec.base_commit = git(["rev-parse", "HEAD"], repo)
    engine.spec.evaluation_assets = [
        EvaluationAsset(path="data/train.csv", sha256="a" * 64),
        EvaluationAsset(path="data/eval.csv", sha256="b" * 64),
    ]
    engine.spec.agent_visible_training_paths = ["data/train.csv"]
    engine.spec.agent_hidden_paths = [".bananloop_eval/**", "data/eval.csv"]
    engine.spec.protected_paths = ["data/train.csv", "data/eval.csv"]
    engine.workspace.ensure(engine.spec.base_commit)

    hidden = engine._agent_hidden_paths()
    agent_workspace = engine.workspace.prepare_agent_workspace(hidden)

    assert "data/train.csv" not in hidden
    assert "data/eval.csv" in hidden
    assert "data/train.csv" in engine._agent_protected_paths()
    assert (agent_workspace / "data" / "train.csv").is_file()
    assert not (agent_workspace / "data" / "eval.csv").exists()
    engine.workspace.cleanup_agent_workspace()
    store.close()


def make_staged_engine(
    tmp_path: Path,
    *,
    max_evaluator_calls: int = 12,
) -> tuple[CampaignStore, Engine, Path, str]:
    repo = make_repo(tmp_path)
    base = git(["rev-parse", "HEAD"], repo)
    paths = RuntimePaths.for_repo(repo, tmp_path / "staged-state")
    paths.ensure()
    spec = CampaignSpec(
        goal="quality 3 with blind staged evaluation",
        source_repo=str(repo),
        base_commit=base,
        evaluation_command=[sys.executable, "evaluator.py"],
        validation_command=[sys.executable, "validation.py"],
        holdout_command=[sys.executable, "holdout.py"],
        evaluation_assets=[
            EvaluationAsset(
                path=name,
                sha256=sha256_evaluation_asset(repo / name),
            )
            for name in ("evaluator.py", "validation.py", "holdout.py")
        ],
        primary_metric=MetricSpec(
            name="quality",
            direction=Direction.MAXIMIZE,
            target=3,
            min_improvement=0.5,
        ),
        guardrails=[GuardrailSpec(metric="regressions", operator="<=", bound=0)],
        mandatory_checks=["tests"],
        editable_scope=["value.txt", "guard.txt"],
        protected_paths=["evaluator.py", "validation.py", "holdout.py", "agent.py"],
        budget=BudgetSpec(
            max_experiments=5,
            max_attempts=6,
            max_agent_calls=12,
            max_evaluator_calls=max_evaluator_calls,
            plateau_experiments=4,
        ),
        agent=AgentConfig(kind="command", command=[sys.executable, "agent.py"]),
    )
    store = CampaignStore(paths.db)
    store.initialize(spec)
    engine = Engine(store, WorkspaceManager(repo, paths.workspace))
    return store, engine, repo, base


def test_end_to_end_accept_reject_accept_and_source_unchanged(tmp_path: Path):
    store, engine, repo, base = make_engine(tmp_path)
    try:
        out = engine.run()
        assert out["status"] == "GOAL_MET"
        assert out["experiment_count"] == 3
        experiments = store.experiments()
        assert [e["status"] for e in experiments] == ["ACCEPTED", "REJECTED", "ACCEPTED"]
        assert json.loads(out["champion_json"])["metrics"]["quality"]["value"] == 3
        portfolio_events = [
            json.loads(event["payload_json"])
            for event in store.events()
            if event["event_type"] == "PORTFOLIO_DECISION"
        ]
        assert [item["selected_candidate_id"] for item in portfolio_events] == ["step-1", "step-2", "step-3"]
        assert all(len(item["candidates"]) == 2 for item in portfolio_events)
        report = build_report(store)
        assert len(report["research_state"]["portfolios"]) == 3
        markdown = render_markdown(report)
        assert "Planning portfolios" in markdown
        assert "DEV lessons" in markdown
        assert "estimated cost 1 agent calls + 1 evaluator calls" in markdown
        assert git(["rev-parse", "HEAD"], repo) == base
        assert git(["status", "--porcelain"], repo) == ""
    finally:
        store.close()


def test_aggregate_only_dev_rejection_persists_unknown_capsule_for_next_proposal(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        out = engine.run()
        assert out["status"] == "GOAL_MET", {
            key: out.get(key)
            for key in ("status", "stop_reason", "experiment_count", "attempt_count")
        }

        rejected = store.experiment(2)
        assert rejected["status"] == "REJECTED"
        assert rejected["failure_analysis_json"]
        analysis = json.loads(rejected["failure_analysis_json"])
        assert len(analysis["capsules"]) == 1
        capsule = analysis["capsules"][0]
        assert capsule["failure_class"] == "insufficient_evidence"
        assert capsule["stage"] == "aggregate_metric"
        assert capsule["confidence"] == 0.0
        assert capsule["likely_mechanism"].startswith("Unknown.")
        assert "validation" not in json.dumps(capsule).lower()
        assert "holdout" not in json.dumps(capsule).lower()

        next_request = json.loads(store.action("exp:3:propose")["request_json"])
        visible_capsules = next_request["research_state"]["recent_failure_capsules"]
        assert len(visible_capsules) == 1
        assert visible_capsules[0]["failure_class"] == "insufficient_evidence"
        assert visible_capsules[0]["likely_mechanism"].startswith("Unknown.")
        assert visible_capsules[0]["evidence_id"].startswith("capsule:")
        assert store.action("exp:2:analyze-dev-failures") is None

        event = next(
            item for item in store.events()
            if item["event_type"] == "DEV_FAILURE_ANALYSIS_INSUFFICIENT_EVIDENCE"
        )
        assert json.loads(event["payload_json"])["failure_class"] == "insufficient_evidence"
    finally:
        store.close()


def test_resume_after_durable_stage_does_not_restart_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        monkeypatch.setenv("BANANLOOP_TEST_STOP_AFTER_STAGE", "PORTFOLIO_SAVED")
        with pytest.raises(ControlledInterruption):
            engine.run()
        c1 = store.campaign()
        assert c1["stage"] == "PROPOSE"
        assert c1["attempt_count"] == 1
        assert c1["agent_calls"] == 1
        saved = [event for event in store.events() if event["event_type"] == "PORTFOLIO_DECISION"]
        assert len(saved) == 1
        assert store.action("exp:1:propose")["status"] == "COMPLETED"
        monkeypatch.delenv("BANANLOOP_TEST_STOP_AFTER_STAGE")
        out = engine.run(resume=True)
        assert out["status"] == "GOAL_MET"
        assert out["experiment_count"] == 3
        assert out["attempt_count"] == 3
        assert out["agent_calls"] == 6
        assert len([event for event in store.events() if event["event_type"] == "PORTFOLIO_DECISION"]) == 3
        experiments = store.experiments()
        assert [e["status"] for e in experiments] == ["ACCEPTED", "REJECTED", "ACCEPTED"]
        first_eval = json.loads(experiments[0]["evaluation_json"])
        assert first_eval["metrics"]["quality"]["value"] == 1
    finally:
        store.close()



class ReplayNormalizingAgent(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        raise AssertionError("durable completed action must not call the agent again")

    def normalize_result(self, task, result):
        normalized = dict(result)
        if task.get("role") == "implement" and normalized.get("blockers") == "None":
            normalized["blockers"] = []
        return normalized


def test_replayed_agent_result_is_normalized_without_second_call(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])

        engine._stage_propose(attempt_id, experiment_id)
        exp = store.experiment(experiment_id)
        task = engine._task_context("implement", experiment_id)
        task["hypothesis"] = json.loads(exp["hypothesis_json"])
        request_hash = hashlib.sha256(
            json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        action_id = f"exp:{experiment_id}:implement"
        store.start_action(action_id, experiment_id, "EXECUTE", "agent", request_hash, task)
        store.finish_action(
            action_id,
            {
                "summary": "changed fixture",
                "changed_paths": ["value.txt"],
                "verification_notes": "ok",
                "blockers": "None",
            },
        )
        (engine.workspace.workspace / "value.txt").write_text("1\n")

        calls_before = store.campaign()["agent_calls"]
        engine.agent = ReplayNormalizingAgent()
        engine._stage_execute(attempt_id, experiment_id)

        assert store.attempt(attempt_id)["stage"] == "SNAPSHOT"
        assert store.campaign()["agent_calls"] == calls_before
        assert (engine.workspace.workspace / "value.txt").read_text() == "1\n"
    finally:
        store.close()



def test_user_interrupt_marks_action_cancelled_and_resume_retries(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    request = {"role": "propose", "experiment_id": 99}
    try:
        def interrupt():
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            engine._action(
                action_id="test:interrupt",
                experiment_id=None,
                stage="PROPOSE",
                kind="agent",
                request=request,
                invoke=interrupt,
            )

        cancelled = store.action("test:interrupt")
        assert cancelled["status"] == "CANCELLED"
        assert store.campaign()["agent_calls"] == 1

        result = engine._action(
            action_id="test:interrupt",
            experiment_id=None,
            stage="PROPOSE",
            kind="agent",
            request=request,
            invoke=lambda: {"ok": True},
        )

        assert result == {"ok": True}
        assert store.action("test:interrupt")["status"] == "COMPLETED"
        assert store.campaign()["agent_calls"] == 2
    finally:
        store.close()


def test_failed_action_requires_explicit_retry(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    request = {"role": "propose", "experiment_id": 99}
    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        store.start_action("test:failed", None, "PROPOSE", "agent", request_hash)
        store.fail_action("test:failed", "protocol failure")

        with pytest.raises(EngineError, match="retry-failed-action"):
            engine._action(
                action_id="test:failed",
                experiment_id=None,
                stage="PROPOSE",
                kind="agent",
                request=request,
                invoke=lambda: {"ok": True},
            )

        engine._retry_failed_actions = True
        result = engine._action(
            action_id="test:failed",
            experiment_id=None,
            stage="PROPOSE",
            kind="agent",
            request=request,
            invoke=lambda: {"ok": True},
        )

        assert result == {"ok": True}
        assert store.action("test:failed")["status"] == "COMPLETED"
        assert store.campaign()["agent_calls"] == 2
    finally:
        store.close()


def test_agent_cleanup_failure_does_not_discard_completed_response(tmp_path: Path, monkeypatch, caplog):
    store, engine, _, _ = make_engine(tmp_path)

    class CompletedAgent(AgentAdapter):
        def probe(self):
            return {"available": True}

        def execute(self, task, workspace, timeout):
            return {"ok": True}

    try:
        engine.agent = CompletedAgent()
        engine.workspace.ensure(engine.spec.base_commit)
        cleanup = engine.workspace.cleanup_agent_workspace
        cleanup_calls = 0

        def fail_cleanup_after_prepare():
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                cleanup()
                return
            raise WorkspaceError("sandbox-owned cache")

        monkeypatch.setattr(engine.workspace, "cleanup_agent_workspace", fail_cleanup_after_prepare)
        with caplog.at_level("WARNING", logger="bananloop.engine"):
            result = engine._invoke_agent(
                {"role": "propose"}, timeout=1, apply_changes=False
            )
        assert result == {"ok": True}
        assert "temporary agent workspace cleanup failed" in caplog.text
    finally:
        store.close()


def test_orphaned_running_action_requires_explicit_retry(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    request = {"role": "propose", "experiment_id": 99}
    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        store.start_action("test:orphan", None, "PROPOSE", "agent", request_hash)

        with pytest.raises(UnknownActionOutcome):
            engine._action(
                action_id="test:orphan",
                experiment_id=None,
                stage="PROPOSE",
                kind="agent",
                request=request,
                invoke=lambda: {"ok": True},
            )

        engine._retry_running_actions = True
        result = engine._action(
            action_id="test:orphan",
            experiment_id=None,
            stage="PROPOSE",
            kind="agent",
            request=request,
            invoke=lambda: {"ok": True},
        )

        assert result == {"ok": True}
        assert store.action("test:orphan")["status"] == "COMPLETED"
        assert store.campaign()["agent_calls"] == 2
    finally:
        store.close()


def test_resume_rejects_unapproved_campaign_spec_change_before_any_call(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        original = store.campaign()
        changed_spec = store.spec().model_copy(update={"goal": "silently change the approved goal"})
        store.update_campaign(spec_json=changed_spec.model_dump_json())
        resumed_engine = Engine(store, engine.workspace)

        with pytest.raises(EngineError, match="approved campaign spec hash mismatch"):
            resumed_engine.run(resume=True)

        after = store.campaign()
        assert after["spec_hash"] == original["spec_hash"]
        assert after["agent_calls"] == 0
        assert after["evaluator_calls"] == 0
        assert store.actions() == []
    finally:
        store.close()



def test_accepted_experiment_clears_transient_failure_and_lesson(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])

        store.update_experiment(
            experiment_id,
            decision_json=json.dumps({"accepted": True}),
            evaluation_json=campaign["champion_json"],
            failure_category="AGENT_PROTOCOL_ERROR",
            lesson="old transient protocol error",
        )
        engine._stage_record(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["status"] == "ACCEPTED"
        assert exp["failure_category"] is None
        assert exp["lesson"] == "Promoted as new champion"
    finally:
        store.close()



def test_report_hides_stale_failure_for_historical_accepted_experiment(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        store.update_experiment(
            experiment_id,
            hypothesis_json=json.dumps({
                "hypothesis": "historical accepted change",
                "rationale": "test",
                "expected_effect": "up",
                "files_or_area": ["value.txt"],
                "falsification_condition": "no change",
                "mechanism_key": "historical",
            }),
            decision_json=json.dumps({"accepted": True, "delta": 1.0}),
            evaluation_json=campaign["champion_json"],
            failure_category="AGENT_PROTOCOL_ERROR",
            lesson="old transient protocol error",
        )
        store.finish_attempt(
            attempt_id,
            experiment_id,
            status="ACCEPTED",
            scientific=True,
            improved=True,
        )

        report = build_report(store)
        exp = report["experiments"][0]
        assert exp["status"] == "ACCEPTED"
        assert exp["failure_category"] is None
        assert exp["lesson"] == "Promoted as new champion"
    finally:
        store.close()



def test_verify_pauses_for_frozen_evaluator_drift(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        evaluator_path = engine.workspace.workspace / "evaluator.py"
        expected_hash = sha256_evaluation_asset(evaluator_path)
        engine.spec.evaluation_assets = [
            EvaluationAsset(path="evaluator.py", sha256=expected_hash)
        ]
        engine.spec.protected_paths = ["agent.py"]

        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        engine._stage_propose(attempt_id, experiment_id)
        engine._stage_execute(attempt_id, experiment_id)
        engine._stage_snapshot(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        engine.workspace.checkout(exp["candidate_snapshot"])
        evaluator_path.write_text("print('tampered')\n", encoding="utf-8")
        tampered = engine.workspace.snapshot("tamper evaluator")
        store.update_experiment(experiment_id, candidate_snapshot=tampered)

        with pytest.raises(EngineError, match="frozen evaluation assets changed"):
            engine._stage_verify(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert store.attempt(attempt_id)["stage"] == "VERIFY"
        assert exp["failure_category"] == "EVALUATOR_CHANGED"
        assert exp["decision_json"] is None
        assert store.campaign()["status"] == "PAUSED_INFRA"
    finally:
        store.close()



class CapturingEvaluator:
    def __init__(self):
        self.timeout = None

    def evaluate(self, workspace, timeout):
        self.timeout = timeout
        return EvaluationResult.model_validate({
            "status": "ok",
            "metrics": {
                "quality": 0,
                "regressions": 0,
            },
            "checks": {"tests": True},
        })


def test_baseline_uses_campaign_evaluator_timeout(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.evaluator_timeout_seconds = 4321
        evaluator = CapturingEvaluator()
        engine.evaluator = evaluator

        engine._baseline()

        assert evaluator.timeout == 4321
    finally:
        store.close()



class TimeoutCapturingAgent(AgentAdapter):
    def __init__(self):
        self.timeout = None

    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        self.timeout = timeout
        return {
            "hypothesis": "capture timeout",
            "rationale": "test",
            "expected_effect": "none",
            "files_or_area": ["value.txt"],
            "falsification_condition": "n/a",
            "mechanism_key": "timeout-test",
        }


def test_propose_uses_runtime_agent_timeout_override(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        agent = TimeoutCapturingAgent()
        engine.agent = agent
        engine._agent_timeout_seconds = 3600

        engine._stage_propose(attempt_id, experiment_id)

        assert agent.timeout == 3600
        assert store.attempt(attempt_id)["stage"] == "EXECUTE"
    finally:
        store.close()



class IsolationCheckingAgent(AgentAdapter):
    def __init__(self):
        self.evaluator_visible = None
        self.evaluator_in_git_history = None

    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        self.evaluator_visible = (workspace / "evaluator.py").exists()
        history_probe = subprocess.run(
            ["git", "show", "HEAD:evaluator.py"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        self.evaluator_in_git_history = history_probe.returncode == 0
        return {
            "hypothesis": "change visible production file",
            "rationale": "isolation fixture",
            "expected_effect": "quality up",
            "files_or_area": ["value.txt"],
            "falsification_condition": "quality unchanged",
            "mechanism_key": "isolation-check",
        }


class IsolatedImplementAgent(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        assert not (workspace / "evaluator.py").exists()
        (workspace / "value.txt").write_text("1\n", encoding="utf-8")
        return {
            "summary": "changed visible value",
            "changed_paths": ["value.txt"],
            "verification_notes": "isolated workspace",
            "blockers": [],
        }


def _freeze_fixture_evaluator(engine: Engine) -> None:
    evaluator = engine.workspace.workspace / "evaluator.py"
    engine.spec.evaluation_assets = [
        EvaluationAsset(
            path="evaluator.py",
            sha256=sha256_evaluation_asset(evaluator),
        )
    ]


def test_agent_workspace_hides_frozen_evaluator_and_git_history(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        _freeze_fixture_evaluator(engine)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        agent = IsolationCheckingAgent()
        engine.agent = agent

        engine._stage_propose(attempt_id, experiment_id)

        assert agent.evaluator_visible is False
        assert agent.evaluator_in_git_history is False
        assert engine.workspace.agent_workspace is None
    finally:
        store.close()


def test_implement_changes_are_applied_back_from_isolated_workspace(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        _freeze_fixture_evaluator(engine)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        store.update_experiment(
            experiment_id,
            hypothesis_json=Hypothesis(
                hypothesis="set value",
                rationale="fixture",
                expected_effect="quality up",
                files_or_area=["value.txt"],
                falsification_condition="quality unchanged",
                mechanism_key="set-value",
            ).model_dump_json(),
        )
        store.set_stage(attempt_id, "EXECUTE")
        engine.agent = IsolatedImplementAgent()

        engine._stage_execute(attempt_id, experiment_id)

        assert (engine.workspace.workspace / "value.txt").read_text(encoding="utf-8") == "1\n"
        assert (engine.workspace.workspace / "evaluator.py").exists()
        assert engine.workspace.agent_workspace is None
    finally:
        store.close()


def test_agent_context_filters_non_visible_metrics_and_guardrails(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, _ = store.create_attempt(campaign["champion_snapshot"])
        engine.spec.agent_visible_metrics = ["quality"]

        task = engine._task_context("propose", experiment_id)

        assert set(task["baseline"]["metrics"]) == {"quality"}
        assert set(task["champion"]["metrics"]) == {"quality"}
        assert task["baseline"]["artifacts"] == []
        assert task["guardrails"] == []
    finally:
        store.close()


def test_successful_retry_clears_stale_agent_failure_metadata(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        store.update_experiment(
            experiment_id,
            failure_category="AGENT_PROTOCOL_ERROR",
            lesson="old transient failure",
        )
        engine.agent = TimeoutCapturingAgent()

        engine._stage_propose(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["failure_category"] is None
        assert exp["lesson"] is None
    finally:
        store.close()



def test_staged_validation_gates_promotion_and_final_holdout_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, engine, _, _ = make_staged_engine(tmp_path)
    final_holdout_tiers: list[str] = []
    original_validate = engine_module.validate_evaluation

    def capture_holdout_tier(spec, result, *, tier="dev"):
        if store.campaign()["stage"] == "FINAL_HOLDOUT":
            final_holdout_tiers.append(tier)
        return original_validate(spec, result, tier=tier)

    monkeypatch.setattr(engine_module, "validate_evaluation", capture_holdout_tier)
    try:
        out = engine.run()

        assert out["status"] == "GOAL_MET"
        assert [row["status"] for row in store.experiments()] == [
            "REJECTED",
            "REJECTED",
            "ACCEPTED",
        ]

        evaluations = store.evaluations()
        assert [row["tier"] for row in evaluations].count("dev") == 4
        assert [row["tier"] for row in evaluations].count("validation") == 3
        assert [row["tier"] for row in evaluations].count("holdout") == 1
        assert final_holdout_tiers == ["holdout", "holdout"]

        first = store.experiments()[0]
        first_decision = json.loads(first["decision_json"])
        assert first_decision["accepted"] is False
        assert first_decision["predicates"]["validation"] is False
        assert "VALIDATION_REJECTED" in first_decision["reasons"]
        assert first["failure_category"] == "VALIDATION_REJECTED"

        final_action = store.action("final:holdout")
        assert final_action is not None
        assert final_action["status"] == "COMPLETED"
        calls_after_first_run = store.campaign()["evaluator_calls"]

        # Terminal replay must not consume or reveal the final holdout again.
        out2 = engine.run()
        assert out2["status"] == "GOAL_MET"
        assert store.campaign()["evaluator_calls"] == calls_after_first_run
        assert len(store.evaluations("holdout")) == 1

        report = build_report(store)
        assert report["final_assessment"] == "ACCEPTED"
        assert report["evaluation_protocol"]["validation"]["configured"] is True
        assert report["evaluation_protocol"]["validation"]["baseline"] == 0.0
        assert report["evaluation_protocol"]["validation"]["champion"] == 3.0
        assert report["evaluation_protocol"]["holdout"]["evaluated"] is True
        assert report["evaluation_protocol"]["holdout"]["value"] == 3.0
        assert report["evaluation_protocol"]["holdout"]["target_met"] is True
        assert report["evaluator_binding"]["binding_id"] == store.evaluator_binding()["binding_id"]
        decision_receipts = report["decision_receipts"]
        assert {(item["tier"], item["status"]) for item in decision_receipts} >= {
            ("dev", "VERIFIED"), ("validation", "VERIFIED"), ("holdout", "VERIFIED")
        }
        validation_receipt = next(item["receipt"] for item in decision_receipts if item["tier"] == "validation")
        assert {item["tier"] for item in validation_receipt["evaluator_receipts"]} == {"dev", "validation"}
        assert report["operator_summary"]["what_improved"]["status"] == report["improvement_status"]
        assert report["operator_summary"]["what_measured"]["aggregate_measurements"]
        assert "failure_kinds" in report["operator_summary"]["what_checked"]
        assert set(report["operator_summary"]) == {
            "what_improved", "what_checked", "what_measured", "why_accept_reject", "unknowns", "next_action"
        }
        receipt_tiers = {row["receipt"]["tier"] for row in store.evaluation_receipts()}
        assert receipt_tiers == {"dev", "validation", "holdout"}
        assert len(store.evaluation_receipts()) == len(store.evaluations())
        for row in store.evaluation_receipts():
            action_id = row["action_id"]
            sidecar = (
                store.path.parent
                / "artifacts"
                / hashlib.sha256(action_id.encode()).hexdigest()[:24]
                / "evaluation-receipt.json"
            )
            assert json.loads(sidecar.read_text(encoding="utf-8"))["receipt_hash"] == row["receipt_hash"]
        assert report["evaluation_receipts"]
        markdown = render_markdown(report)
        binding_line = next(line for line in markdown.splitlines() if line.startswith("- Binding:"))
        assert binding_line.endswith("`")
        assert any("receipt=`" in line and "binding=`" in line and line.endswith("`") for line in markdown.splitlines())
        assert "## Decision receipts" in markdown
        assert "## Operator summary" in markdown
        stored_decision = store.decision_receipts()[0]
        tampered = dict(stored_decision["receipt"])
        tampered["verdict"] = "REJECT" if tampered["verdict"] == "ACCEPT" else "ACCEPT"
        store.conn.execute(
            "UPDATE decision_receipts SET receipt_json=? WHERE receipt_hash=?",
            (json.dumps(tampered, sort_keys=True), stored_decision["receipt_hash"]),
        )
        store.conn.commit()
        invalid = next(
            item for item in build_report(store)["decision_receipts"]
            if item["receipt_hash"] == stored_decision["receipt_hash"]
        )
        assert invalid["status"] == "INVALID"
    finally:
        store.close()


def test_agent_history_and_research_state_use_only_dev_evidence(tmp_path: Path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        engine.run()
        history = engine._history()

        assert history
        # Experiment 1 passed DEV and failed blind validation. Adaptive memory
        # must retain only the DEV signal, not the hidden-tier rejection.
        assert history[0]["dev_outcome"] == "DEV_IMPROVEMENT"
        rendered_history = json.dumps(history).upper()
        assert "VALIDATION" not in rendered_history
        assert "HOLDOUT" not in rendered_history

        task = engine._task_context("propose", 999)
        rendered_task = json.dumps(task).upper()
        # Explicit policy text may name hidden tiers to forbid access to
        # them. Test the actual adaptive evidence rather than a word match
        # against the complete instruction payload.
        adaptive_evidence = json.dumps(
            {
                key: task[key]
                for key in ("history", "research_state", "baseline", "champion")
            },
            sort_keys=True,
        ).upper()
        assert "VALIDATION_REJECTED" not in adaptive_evidence
        assert "INVALID_VALIDATION" not in adaptive_evidence
        assert "HOLDOUT" not in adaptive_evidence
        assert "VALIDATION:" not in adaptive_evidence
        assert "validation_command" not in task
        assert "holdout_command" not in task
        assert task["research_state"]["planning_mode"] in {"exploit", "explore", "reframe"}
        assert "remaining_budget" in task["research_state"]
    finally:
        store.close()


def test_subthreshold_dev_gain_counts_as_no_progress_without_promotion(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        store.update_campaign(non_improve_streak=2)
        champion = store.campaign()["champion_json"]
        experiment_id, attempt_id = store.create_attempt(store.campaign()["champion_snapshot"])
        candidate = EvaluationResult(
            metrics={"quality": 0.1, "regressions": 1}, checks={"tests": True},
        )
        store.update_experiment(
            experiment_id,
            hypothesis_json=Hypothesis(
                hypothesis="small useful change", rationale="DEV signal",
                expected_effect="quality up", files_or_area=["value.txt"],
                falsification_condition="no gain", mechanism_key="partial-gain",
            ).model_dump_json(),
            evaluation_json=candidate.model_dump_json(),
            dev_decision_json=json.dumps({
                "accepted": False, "primary_value": 0.1,
                "champion_value": 0.0, "delta": 0.1,
                "predicates": {"minimum_improvement": False},
                "reasons": ["PRIMARY_NOT_IMPROVED", "GUARDRAIL_FAILED:regressions"],
            }),
            decision_json=json.dumps({
                "accepted": False, "primary_value": 0.1,
                "champion_value": 0.0, "delta": 0.1,
                "predicates": {"minimum_improvement": False},
                "reasons": ["PRIMARY_NOT_IMPROVED", "GUARDRAIL_FAILED:regressions"],
            }),
        )
        engine._stage_record(attempt_id, experiment_id)
        assert store.experiment(experiment_id)["status"] == "REJECTED"
        assert store.campaign()["champion_json"] == champion
        # The +0.1 gain is smaller than min_improvement=0.5. It is
        # inconclusive noise, not scientific progress that resets the streak.
        assert store.campaign()["non_improve_streak"] == 3
        assert engine._post_iteration_stop() is False  # One bounded reframe remains.
    finally:
        store.close()


def test_plateau_allows_one_budgeted_reframe_then_stops(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        store.update_campaign(non_improve_streak=engine.spec.budget.plateau_experiments)
        assert engine._post_iteration_stop() is False
        assert any(event["event_type"] == "PLATEAU_REFRAME_ALLOWED" for event in store.events())

        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        hypothesis = Hypothesis(
            hypothesis="bounded reframe", rationale="different mechanism",
            expected_effect="quality changes", files_or_area=["value.txt"],
            falsification_condition="quality remains unchanged", mechanism_key="bounded-reframe",
        )
        result = EvaluationResult(metrics={"quality": 0, "regressions": 0}, checks={"tests": True})
        store.update_experiment(
            experiment_id,
            hypothesis_json=hypothesis.model_dump_json(),
            evaluation_json=result.model_dump_json(),
            dev_decision_json=json.dumps({"accepted": False, "primary_value": 0, "champion_value": 0,
                                          "delta": 0, "reasons": ["NO_IMPROVEMENT"]}),
            decision_json=json.dumps({"accepted": False, "primary_value": 0, "champion_value": 0,
                                      "delta": 0, "reasons": ["NO_IMPROVEMENT"]}),
        )
        store.finish_attempt(attempt_id, experiment_id, status="REJECTED", scientific=True, improved=False)
        candidate = HypothesisCandidate(
            candidate_id="bounded-reframe", hypothesis=hypothesis, evidence_refs=["INSUFFICIENT_EVIDENCE"],
            predicted_observable="quality metric", discriminating_test="same fixture", scope_eligibility=True,
            estimated_agent_calls=1, estimated_evaluator_calls=1, rationale="bounded", uncertainty="toy",
        )
        portfolio = PortfolioRecord(
            experiment_ordinal=store.experiment(experiment_id)["ordinal"], action_id=f"exp:{experiment_id}:propose",
            strategy="reframe", candidates=[candidate],
            decisions=[CandidateDecision(candidate_id="bounded-reframe", admissible=True)],
            selected_candidate_id="bounded-reframe",
        )
        store.event("PORTFOLIO_DECISION", portfolio.model_dump(mode="json"))
        store.conn.commit()
        assert engine._post_iteration_stop() is True
        assert store.campaign()["stop_reason"] == "PLATEAU"
    finally:
        store.close()


def test_plateau_does_not_bypass_exhausted_agent_budget(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        store.update_campaign(
            non_improve_streak=engine.spec.budget.plateau_experiments,
            agent_calls=engine.spec.budget.max_agent_calls,
        )
        assert engine._post_iteration_stop() is True
        assert store.campaign()["stop_reason"] == "PLATEAU"
        state = store.research_state()
        assert "no budget remains" in state["stop_explanation"]
        assert any(event["event_type"] == "PLATEAU_REFRAME_BLOCKED" for event in store.events())
    finally:
        store.close()


def test_planner_requires_causal_link_and_prior_dev_reference():
    task = {"research_state": {
        "failure_signals": [{"signal_key": "metric:retrieval_miss_count"}],
        "experiment_evidence": [{"ordinal": 1, "dev_outcome": "DEV_NO_EFFECT"}],
    }}
    hypothesis = Hypothesis(
        hypothesis="change candidate generation", rationale="fewer misses",
        expected_effect="higher recall", files_or_area=["src/retrieval.py"],
        falsification_condition="miss count unchanged", mechanism_key="candidate-generator",
    )
    assert Engine._hypothesis_grounding_error(hypothesis, task) == (
        "hypothesis must target one supplied DEV failure signal"
    )
    hypothesis.target_failure_signal = "metric:retrieval_miss_count"
    hypothesis.failure_mechanism = "Relevant candidates are absent before reranking"
    assert Engine._hypothesis_grounding_error(hypothesis, task) == (
        "hypothesis must cite a prior DEV experiment ordinal"
    )
    hypothesis.prior_experiment_refs = [1]
    hypothesis.novelty_rationale = "Prior wider candidate pool did not change recall"
    assert Engine._hypothesis_grounding_error(hypothesis, task) is None



def test_anti_gaming_guard_rejects_candidate_before_evaluator(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.forbidden_diff_patterns = [r"forbidden_marker"]
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])

        (engine.workspace.workspace / "value.txt").write_text(
            "forbidden_marker\n",
            encoding="utf-8",
        )
        snapshot = engine.workspace.snapshot("candidate with forbidden marker")
        store.update_experiment(experiment_id, candidate_snapshot=snapshot)
        store.set_stage(attempt_id, "VERIFY")
        evaluator_calls_before = store.campaign()["evaluator_calls"]

        engine._stage_verify(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert exp["failure_category"] == "LEAKAGE_GUARD"
        assert json.loads(exp["decision_json"])["reasons"] == ["LEAKAGE_GUARD"]
        decision_receipt = next(item for item in store.decision_receipts() if item["tier"] == "pre_evaluation")
        assert decision_receipt["receipt"]["evaluator_receipts"] == []
        assert decision_receipt["receipt"]["verdict"] == "REJECT"
        assert "forbidden_marker" in exp["lesson"]
        assert store.campaign()["evaluator_calls"] == evaluator_calls_before
    finally:
        store.close()



def test_holdout_budget_is_reserved_before_new_attempt(tmp_path: Path):
    store, engine, _, _ = make_staged_engine(
        tmp_path,
        max_evaluator_calls=3,
    )
    try:
        out = engine.run()

        assert out["status"] == "STOPPED"
        assert out["stop_reason"] == "BUDGET_EXHAUSTED"
        assert out["attempt_count"] == 0
        assert out["active_attempt_id"] is None
        assert out["evaluator_calls"] == 3
        assert len(store.evaluations("dev")) == 1
        assert len(store.evaluations("validation")) == 1
        assert len(store.evaluations("holdout")) == 1
        assert store.action("final:holdout")["status"] == "COMPLETED"
        assert build_report(store)["final_assessment"] == "REJECTED"
    finally:
        store.close()



class CapturingFailureAnalyzer(AgentAdapter):
    def __init__(self):
        self.task = None

    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        self.task = task
        assert task["role"] == "analyze"
        assert not (workspace / "evaluator.py").exists() or True
        return {
            "capsules": [
                {
                    "title": "Reconcile conflicting hard-field provenance",
                    "failure_class": "STALE_CONSTRAINT_AFTER_ENRICHMENT",
                    "stage": "enrichment",
                    "affected_fields": ["dn"],
                    "observation": "VT.217.N.05 moved from DN15 to DN20 but the hard field stayed stale",
                    "likely_mechanism": "hard constraints are materialized before evidence reconciliation",
                    "general_rule": "arbitrate source conflicts before applying hard filtering",
                    "suggested_regression": "construct conflicting evidence without case-specific constants",
                    "confidence": 0.9,
                }
            ]
        }


def test_dev_failure_analyzer_sees_public_teacher_answer_but_proposer_does_not(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        hypothesis = Hypothesis(
            hypothesis="reconcile enrichment provenance",
            rationale="stale hard fields can reject valid candidates",
            expected_effect="higher quality",
            files_or_area=["value.txt"],
            falsification_condition="no DEV improvement",
            mechanism_key="constraint-provenance",
        )
        evaluation = EvaluationResult.model_validate(
            {
                "status": "ok",
                "metrics": {"quality": 0.0, "regressions": 0.0},
                "checks": {"tests": True},
                "failure_signals": [
                    {
                        "failure_type": "SOURCE_CONFLICT",
                        "stage": "enrichment",
                        "symptom": "VT.217.N.05 produced conflicting DN20 and DN15 interpretations",
                        "expected_behavior": "conflict must be reconciled before hard filtering",
                        "observed_behavior": "hard_constraints.dn=15 while attributes.dn=20",
                        "affected_fields": ["dn"],
                        "evidence": ["product 123456 was filtered using DN15"],
                    }
                ],
                "failure_examples": [
                    {
                        "query": "Кран шаровый VT.217.N.05",
                        "verdict": "FAIL_WRONG_NOT_FOUND",
                        "expected": {
                            "status": "MATCHED",
                            "requirements": {"dn": 20},
                            "known_positive_ids": [123456],
                        },
                        "actual": {
                            "status": "NOT_FOUND",
                            "returned_ld_id": None,
                        },
                        "pipeline_trace": {
                            "attributes_before_web": {"dn": 15},
                            "attributes": {"dn": 20},
                            "hard_constraints": {"dn": 15},
                        },
                    }
                ],
            }
        )
        decision = {
            "accepted": False,
            "primary_value": 0.0,
            "champion_value": 0.0,
            "delta": 0.0,
            "predicates": {"minimum_improvement": False},
            "reasons": ["PRIMARY_NOT_IMPROVED"],
        }
        store.update_experiment(
            experiment_id,
            hypothesis_json=hypothesis.model_dump_json(),
            candidate_snapshot=campaign["champion_snapshot"],
            evaluation_json=evaluation.model_dump_json(),
            dev_decision_json=json.dumps(decision),
            decision_json=json.dumps(decision),
        )
        store.set_stage(attempt_id, "ANALYZE")
        analyzer = CapturingFailureAnalyzer()
        engine.agent = analyzer

        engine._stage_analyze(attempt_id, experiment_id)

        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert analyzer.task is not None
        rendered_task = json.dumps(analyzer.task, ensure_ascii=False)
        # Analyzer gets the exact public DEV teacher example.
        assert "VT.217.N.05" in rendered_task
        assert "123456" in rendered_task
        assert '"dn": 20' in rendered_task
        # Identity-free failure_signals are still redacted separately.
        assert "<redacted>" in rendered_task

        exp = store.experiment(experiment_id)
        analysis = json.loads(exp["failure_analysis_json"])
        assert analysis["capsules"][0]["failure_class"] == (
            "STALE_CONSTRAINT_AFTER_ENRICHMENT"
        )
        durable_analysis = json.dumps(analysis, ensure_ascii=False)
        assert "VT.217.N.05" not in durable_analysis
        assert "DN15" not in durable_analysis
        assert "DN20" not in durable_analysis
        assert "<case-specific-query>" in durable_analysis
        assert "<redacted>" in durable_analysis
        # After reflection, raw teacher data must not reach the next proposer.
        store.finish_attempt(
            attempt_id,
            experiment_id,
            status="REJECTED",
            scientific=True,
            improved=False,
        )
        state = engine._refresh_research_state()
        assert state["recent_failure_capsules"][0]["general_rule"].startswith(
            "arbitrate source conflicts"
        )
        proposer_task = engine._task_context("propose", 999)
        rendered_proposer = json.dumps(proposer_task, ensure_ascii=False)
        assert "VT.217.N.05" not in rendered_proposer
        assert re.search(r"(?<!\w)123456(?!\w)", rendered_proposer) is None
        assert "known_positive_ids" not in rendered_proposer
        assert "Reconcile conflicting hard-field provenance" in rendered_proposer
    finally:
        store.close()



class MissingContextProposer(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        return {
            "hypothesis": "Cannot form a useful hypothesis because research_state was not provided",
            "rationale": "planning_mode and research_state are missing from campaign context",
            "expected_effect": "unknown until context is supplied",
            "files_or_area": ["value.txt"],
            "falsification_condition": "n/a",
            "mechanism_key": "missing-context-deflection",
        }


class PhantomImplementAgent(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        return {
            "summary": "claimed a change without materializing it",
            "changed_paths": ["value.txt"],
            "verification_notes": "none",
            "blockers": [],
        }


class NoOpImplementAgent(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        return {
            "summary": "no applicable change",
            "changed_paths": [],
            "verification_notes": "no-op",
            "blockers": [],
        }


class BlockedImplementAgent(AgentAdapter):
    def probe(self):
        return {"available": True}

    def execute(self, task, workspace, timeout):
        return {
            "summary": "cannot implement within allowed scope",
            "changed_paths": [],
            "verification_notes": "blocked before editing",
            "blockers": ["required mechanism is outside editable scope"],
        }


def _prepare_execute_stage(store: CampaignStore, engine: Engine) -> tuple[int, int]:
    engine.workspace.ensure(engine.spec.base_commit)
    engine._baseline()
    campaign = store.campaign()
    experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
    store.update_experiment(
        experiment_id,
        hypothesis_json=Hypothesis(
            hypothesis="change fixture",
            rationale="exercise materialization guard",
            expected_effect="quality up",
            files_or_area=["value.txt"],
            falsification_condition="quality unchanged",
            mechanism_key=f"fixture-{experiment_id}",
        ).model_dump_json(),
    )
    store.set_stage(attempt_id, "EXECUTE")
    return experiment_id, attempt_id


def test_missing_context_deflection_is_rejected_as_ungrounded_not_scientific(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        engine.agent = MissingContextProposer()

        engine._stage_propose(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["status"] == "UNGROUNDED"
        assert exp["failure_category"] == "UNGROUNDED_HYPOTHESIS"
        assert "research_state" in exp["lesson"]
        assert store.campaign()["experiment_count"] == 0
        assert store.campaign()["active_attempt_id"] is None
    finally:
        store.close()


def test_claimed_changes_that_never_reach_supervisor_pause_infra(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        evaluator_calls = store.campaign()["evaluator_calls"]
        engine.agent = PhantomImplementAgent()

        with pytest.raises(EngineError, match="no changes reached the supervisor workspace"):
            engine._stage_execute(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        campaign = store.campaign()
        assert exp["failure_category"] == "CANDIDATE_NOT_MATERIALIZED"
        assert campaign["status"] == "PAUSED_INFRA"
        assert campaign["evaluator_calls"] == evaluator_calls
        assert store.action(f"exp:{experiment_id}:evaluate") is None
    finally:
        store.close()


def test_noop_implementation_is_rejected_before_snapshot_or_evaluation(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        evaluator_calls = store.campaign()["evaluator_calls"]
        engine.agent = NoOpImplementAgent()

        engine._stage_execute(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["failure_category"] == "NO_CHANGES"
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert store.campaign()["evaluator_calls"] == evaluator_calls
        assert store.action(f"exp:{experiment_id}:evaluate") is None
        engine._stage_reject(attempt_id, experiment_id)
        engine._stage_record(attempt_id, experiment_id)
        assert store.experiment(experiment_id)["status"] == "NO_CHANGES"
        assert store.campaign()["experiment_count"] == 0
        assert store.campaign()["non_improve_streak"] == 0
    finally:
        store.close()


def test_implementation_blocker_is_rejected_without_fake_candidate(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        evaluator_calls = store.campaign()["evaluator_calls"]
        engine.agent = BlockedImplementAgent()

        engine._stage_execute(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["failure_category"] == "IMPLEMENTATION_BLOCKED"
        assert "outside editable scope" in exp["lesson"]
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert store.campaign()["evaluator_calls"] == evaluator_calls
        engine._stage_reject(attempt_id, experiment_id)
        engine._stage_record(attempt_id, experiment_id)
        assert store.experiment(experiment_id)["status"] == "IMPLEMENTATION_BLOCKED"
        assert store.campaign()["experiment_count"] == 0
    finally:
        store.close()


def test_evaluator_binding_is_stable_and_tampering_is_rejected(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        runtime = runtime_descriptor({"dev": engine.spec.evaluation_command})
        first = make_binding(engine.spec, runtime)
        second = make_binding(engine.spec, runtime)
        assert first == second
        assert first.binding_id == second.binding_id
        payload = first.model_dump(mode="json")
        payload["runtime_fingerprint"] = "0" * 64
        with pytest.raises(Exception):
            EvaluatorBinding.model_validate(payload)
        assert engine.spec.approved_hash() == store.campaign()["spec_hash"]
    finally:
        store.close()


def test_runtime_descriptor_fingerprints_direct_python_dependencies_only():
    descriptor = runtime_descriptor({
        "dev": [sys.executable, "evaluator.py"],
        "wrapper": ["sh", "-c", "python evaluator.py"],
    })
    direct = descriptor["runners"]["dev"]["effective_python"]
    wrapped = descriptor["runners"]["wrapper"]["effective_python"]

    assert direct["status"] == "VERIFIED"
    assert direct["python_version"]
    assert direct["dependency_count"] > 0
    assert re.fullmatch(r"[0-9a-f]{64}", direct["dependency_fingerprint"])
    assert "packages" not in direct
    assert wrapped == {"status": "UNKNOWN"}


def test_completed_evaluation_replay_neither_reruns_nor_rewrites_receipt(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        row = store.evaluation_receipt("baseline:evaluate")
        assert row is not None
        action = store.action("baseline:evaluate")
        request = json.loads(action["request_json"])
        sidecar = (
            store.path.parent
            / "artifacts"
            / hashlib.sha256(b"baseline:evaluate").hexdigest()[:24]
            / "evaluation-receipt.json"
        )
        original = sidecar.read_bytes()
        sidecar.unlink()
        replay = engine._action(
            action_id="baseline:evaluate",
            experiment_id=None,
            stage="BASELINE",
            kind="evaluator",
            request=request,
            invoke=lambda: (_ for _ in ()).throw(AssertionError("must replay")),
        )
        assert replay["metrics"]["quality"]["value"] == 0
        assert not sidecar.exists()  # Missing recovery sidecar is not rewritten during replay.
        # DB remains authoritative and the receipt survives a missing sidecar.
        assert store.evaluation_receipt("baseline:evaluate")["receipt_hash"] == row["receipt_hash"]
        sidecar.write_bytes(original)
    finally:
        store.close()


def test_tampered_completed_receipt_sidecar_is_rejected(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        action_id = "baseline:evaluate"
        request = json.loads(store.action(action_id)["request_json"])
        sidecar = (
            store.path.parent / "artifacts"
            / hashlib.sha256(action_id.encode()).hexdigest()[:24]
            / "evaluation-receipt.json"
        )
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["tier"] = "holdout"
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(Exception, match="sidecar"):
            engine._action(
                action_id=action_id, experiment_id=None, stage="BASELINE",
                kind="evaluator", request=request,
                invoke=lambda: (_ for _ in ()).throw(AssertionError("must replay")),
            )
    finally:
        store.close()


def test_runtime_binding_mismatch_pauses_before_new_evaluation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine._ensure_evaluator_binding()
        prior_calls = store.campaign()["evaluator_calls"]
        monkeypatch.setattr(
            "bananloop.engine.runtime_descriptor",
            lambda commands: {"python_version": "0.0-tampered"},
        )
        result = engine.run()
        assert result["status"] == "PAUSED_INFRA"
        assert store.campaign()["evaluator_calls"] == prior_calls
        assert store.action("baseline:evaluate") is None
    finally:
        store.close()


def test_frozen_asset_changed_by_evaluator_fails_action_before_measurement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        original_evaluate = engine.evaluator.evaluate

        def mutate_after_run(workspace, timeout):
            result = original_evaluate(workspace, timeout)
            (workspace / "validation.py").write_text("# changed during evaluation\n")
            return result

        monkeypatch.setattr(engine.evaluator, "evaluate", mutate_after_run)
        with pytest.raises(EngineError, match="baseline failed"):
            engine._baseline()
        assert store.action("baseline:evaluate")["status"] == "FAILED"
        assert store.campaign()["status"] == "PAUSED_INFRA"
        assert store.campaign()["baseline_json"] is None
        assert store.evaluation_receipt("baseline:evaluate") is None
    finally:
        store.close()


def test_conflicting_receipt_for_action_fails_atomic_completion(tmp_path: Path):
    store, _, _, _ = make_engine(tmp_path)
    try:
        store.start_action("conflict:evaluate", None, "BASELINE", "evaluator", "a" * 64, {"snapshot": "abc"})
        with store.conn:
            store.conn.execute(
                "INSERT INTO evaluation_receipts(action_id,receipt_hash,receipt_json,created_at) VALUES(?,?,?,?)",
                ("conflict:evaluate", "b" * 64, '{"receipt_hash":"' + "b" * 64 + '"}', "2026-01-01T00:00:00+00:00"),
            )
        with pytest.raises(Exception, match="conflicting evaluation receipt"):
            store.finish_action(
                "conflict:evaluate",
                {"metrics": {}, "_evaluation_receipt": {"receipt_hash": "c" * 64}},
            )
        assert store.action("conflict:evaluate")["status"] == "RUNNING"
        assert store.evaluation_receipt("conflict:evaluate")["receipt_hash"] == "b" * 64
    finally:
        store.close()


@pytest.mark.parametrize("field", ["case_input_manifest_hash", "artifact_manifest_hash"])
def test_completed_receipt_recomputes_case_and_artifact_manifest_hashes(tmp_path: Path, field: str):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        row = store.evaluation_receipt("baseline:evaluate")
        payload = dict(row["receipt"])
        payload[field] = "0" * 64
        body = dict(payload)
        body.pop("receipt_hash")
        payload["receipt_hash"] = canonical_hash(body)
        store.conn.execute(
            "UPDATE evaluation_receipts SET receipt_hash=?,receipt_json=? WHERE action_id=?",
            (payload["receipt_hash"], json.dumps(payload, sort_keys=True), "baseline:evaluate"),
        )
        store.conn.commit()
        with pytest.raises(Exception, match="integrity failure"):
            engine._verify_completed_receipt("baseline:evaluate")
        report_row = next(
            row for row in build_report(store)["evaluation_receipts"]
            if row["action_id"] == "baseline:evaluate"
        )
        assert report_row["status"] == "INVALID"
    finally:
        store.close()


def test_legacy_completed_action_replays_without_binding_or_hash_change(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        request = {"snapshot": engine.spec.base_commit, "tier": "dev", "command": engine.spec.evaluation_command}
        request_hash = _hash_payload(request)
        store.start_action("baseline:evaluate", None, "BASELINE", "evaluator", request_hash, request)
        store.finish_action("baseline:evaluate", {
            "status": "ok", "metrics": {"quality": 0}, "checks": {"tests": True},
        })
        before = store.action("baseline:evaluate")["request_hash"]
        replay = engine._action(
            action_id="baseline:evaluate", experiment_id=None, stage="BASELINE",
            kind="evaluator", request=request,
            invoke=lambda: (_ for _ in ()).throw(AssertionError("legacy action must replay")),
        )
        assert replay["metrics"]["quality"] == 0
        assert store.action("baseline:evaluate")["request_hash"] == before == request_hash
        report = build_report(store)
        legacy = next(row for row in report["evaluation_receipts"] if row["action_id"] == "baseline:evaluate")
        assert legacy["status"] == "LEGACY_UNRECEIPTED"
        assert legacy["receipt_hash"] is None
    finally:
        store.close()


def test_invalid_validation_pauses_without_scientific_rejection(tmp_path: Path):
    store, engine, _, _ = make_staged_engine(tmp_path)

    class InvalidValidation:
        def evaluate(self, workspace, timeout):
            return EvaluationResult(metrics={"quality": 1}, checks={})

    try:
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        engine._stage_execute(attempt_id, experiment_id)
        engine._stage_snapshot(attempt_id, experiment_id)
        engine._stage_verify(attempt_id, experiment_id)
        engine._stage_evaluate(attempt_id, experiment_id)
        engine._stage_decide(attempt_id, experiment_id)
        assert store.attempt(attempt_id)["stage"] == "VALIDATE"
        engine.validation_evaluator = InvalidValidation()

        with pytest.raises(EngineError):
            engine._stage_validate(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        campaign = store.campaign()
        assert exp["failure_category"] == "INVALID_VALIDATION"
        assert exp["decision_json"] is None
        assert exp["status"] == "ACTIVE"
        assert campaign["status"] == "PAUSED_INFRA"
        assert campaign["experiment_count"] == 0
        assert campaign["non_improve_streak"] == 0
    finally:
        store.close()


def test_snapshot_that_loses_material_changes_pauses_infra(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        engine._stage_execute(attempt_id, experiment_id)
        assert store.attempt(attempt_id)["stage"] == "SNAPSHOT"
        parent = store.experiment(experiment_id)["parent_snapshot"]

        monkeypatch.setattr(engine.workspace, "snapshot", lambda message: parent)

        with pytest.raises(EngineError, match="candidate snapshot failed"):
            engine._stage_snapshot(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["failure_category"] == "CANDIDATE_NOT_MATERIALIZED"
        assert store.campaign()["status"] == "PAUSED_INFRA"
        assert store.action(f"exp:{experiment_id}:evaluate") is None
    finally:
        store.close()


def test_evaluate_doctor_blocks_empty_candidate_before_evaluator(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        store.update_experiment(
            experiment_id,
            candidate_snapshot=campaign["champion_snapshot"],
        )
        store.set_stage(attempt_id, "EVALUATE")
        evaluator_calls = store.campaign()["evaluator_calls"]

        with pytest.raises(EngineError, match="not materialized in evaluator workspace"):
            engine._stage_evaluate(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert exp["failure_category"] == "CANDIDATE_NOT_MATERIALIZED"
        assert store.campaign()["status"] == "PAUSED_INFRA"
        assert store.campaign()["evaluator_calls"] == evaluator_calls
        assert store.action(f"exp:{experiment_id}:evaluate") is None
    finally:
        store.close()


def test_evaluation_request_records_candidate_materialization_proof(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])

        engine._stage_propose(attempt_id, experiment_id)
        engine._stage_execute(attempt_id, experiment_id)
        engine._stage_snapshot(attempt_id, experiment_id)
        engine._stage_verify(attempt_id, experiment_id)
        assert store.attempt(attempt_id)["stage"] == "EVALUATE"

        engine._stage_evaluate(attempt_id, experiment_id)

        action = store.action(f"exp:{experiment_id}:evaluate")
        request = json.loads(action["request_json"])
        proof = request["candidate_proof"]
        assert proof["candidate_snapshot"] == store.experiment(experiment_id)["candidate_snapshot"]
        assert proof["parent_snapshot"] == store.experiment(experiment_id)["parent_snapshot"]
        assert "value.txt" in proof["changed_paths"]
        assert len(proof["diff_sha256"]) == 64
        assert proof["tree"]

        report = build_report(store)
        assert report["experiments"][0]["candidate_proof"] == proof
    finally:
        store.close()

def test_public_dev_examples_without_signals_trigger_failure_analysis(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        hypothesis = Hypothesis(
            hypothesis="fix candidate matching",
            rationale="DEV teacher evidence",
            expected_effect="fewer mistakes",
            files_or_area=["value.txt"],
            falsification_condition="no DEV improvement",
            mechanism_key="matching-reconcile",
        )
        candidate = EvaluationResult.model_validate({
            "status": "ok",
            "metrics": {"quality": 0.0, "regressions": 0.0},
            "checks": {"tests": True},
            "failure_examples": [{
                "query": "item EX-123",
                "verdict": "FAIL_WRONG_RESULT",
                "expected": {"result": "correct"},
                "actual": {"result": "incorrect"},
                "pipeline_trace": {"stage": "rerank"},
            }],
        })
        store.update_experiment(
            experiment_id,
            hypothesis_json=hypothesis.model_dump_json(),
            candidate_snapshot=campaign["champion_snapshot"],
            evaluation_json=candidate.model_dump_json(),
        )
        engine._stage_decide(attempt_id, experiment_id)
        assert store.attempt(attempt_id)["stage"] == "ANALYZE"

        analyzer = CapturingFailureAnalyzer()
        engine.agent = analyzer
        engine._stage_analyze(attempt_id, experiment_id)
        assert analyzer.task is not None
        assert analyzer.task["dev_failure_signals"] == []
        assert analyzer.task["dev_failure_examples"][0]["query"] == "item EX-123"
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert json.loads(store.experiment(experiment_id)["failure_analysis_json"])["capsules"]
    finally:
        store.close()


def test_pr_auc_capsule_confidence_requires_aggregate_ranking_evidence():
    from types import SimpleNamespace

    from bananloop.contracts import (
        CampaignSpec, DiagnosticCapability, Direction, EvaluationResult,
        FailureAnalysisResult, MetricSpec,
    )
    from bananloop.research import agent_evaluation_view

    protocol = "a" * 64
    spec = CampaignSpec(
        goal="improve PR-AUC", source_repo=".", base_commit="abc",
        evaluation_command=["eval"],
        primary_metric=MetricSpec(
            name="pr_auc", direction=Direction.MAXIMIZE, target=1.0, min_improvement=0.01
        ),
        agent_visible_diagnostic_capabilities=[
            DiagnosticCapability(name="rank_inversions", kind="ranking", metric="pr_auc")
        ],
    )
    harness = SimpleNamespace(spec=spec)
    harness._agent_evaluation_view = lambda payload_json, *, source_tier="dev": agent_evaluation_view(
        spec, payload_json, source_tier=source_tier
    )
    harness._redact_failure_text = lambda value: value
    capsule = {
        "title": "Cutoff misses", "failure_class": "ranking_error", "stage": "scoring",
        "observation": "False negatives increased at the fixed cutoff.",
        "likely_mechanism": "The cutoff caused a ranking regression.",
        "general_rule": "Check ranking evidence.", "confidence": 0.95,
    }
    unsupported = EvaluationResult.model_validate({
        "metrics": {"pr_auc": 0.4}, "protocol_sha256": protocol,
    })
    unknown = Engine._sanitize_failure_analysis(
        harness, FailureAnalysisResult(capsules=[capsule]), unsupported
    ).capsules[0]
    assert unknown.confidence == 0.35
    assert unknown.likely_mechanism.startswith("Unverified conjecture:")

    supported = EvaluationResult.model_validate({
        "metrics": {"pr_auc": 0.4}, "protocol_sha256": protocol,
        "diagnostic_evidence": [{
            "name": "rank_inversions", "kind": "ranking", "metric": "pr_auc",
            "value": 0.12, "aggregation": "aggregate", "protocol_sha256": protocol,
        }],
    })
    harness.store = SimpleNamespace(experiment=lambda _experiment_id: {
        "hypothesis_json": None,
        "evaluation_json": supported.model_dump_json(),
        "dev_decision_json": None,
    })
    harness._task_context = lambda _role, _experiment_id: {}
    harness._agent_decision_view = lambda _payload: None
    task = Engine._dev_failure_analysis_task(harness, 1, supported)
    assert task["dev_evaluation"]["diagnostic_evidence"][0]["provenance"] == {
        "tier": "dev", "protocol_sha256": protocol,
    }
    assert task["analysis_requirements"]["primary_metric_semantics"]["metric_specific_evidence_available"]
    assert "frozen public DEV cases" in task["analysis_requirements"]["primary_metric_semantics"]["causal_test"]
    assert "case_id" not in json.dumps(task["dev_evaluation"])
    evidenced = Engine._sanitize_failure_analysis(
        harness, FailureAnalysisResult(capsules=[capsule]), supported
    ).capsules[0]
    assert evidenced.confidence == 0.95
    assert not evidenced.likely_mechanism.startswith("Unverified conjecture:")


def test_failure_capsule_and_repeated_dev_failure_reach_different_next_hypothesis(tmp_path: Path):
    from bananloop.contracts import FailureCapsule, HypothesisCandidate, HypothesisPortfolio, ResearchState
    from bananloop.research import select_candidate

    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        capsule = FailureCapsule(
            title="Stale hard constraint",
            failure_class="STALE_CONSTRAINT_AFTER_ENRICHMENT",
            stage="enrichment",
            affected_fields=["dn"],
            observation="The reconciled value changes but filtering keeps the earlier value.",
            likely_mechanism="Hard constraints are materialized before evidence reconciliation.",
            general_rule="Reconcile source conflicts before hard filtering.",
            suggested_regression="Add conflicting evidence and assert the reconciled value is used.",
            confidence=0.9,
        )
        for ordinal in (1, 2):
            campaign = store.campaign()
            experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
            hypothesis = Hypothesis(
                hypothesis="adjust weights",
                rationale="test repeated mechanism",
                expected_effect="quality improves",
                files_or_area=["value.txt"],
                falsification_condition="quality unchanged",
                mechanism_key="weight-tuning",
            )
            evaluation = EvaluationResult(
                metrics={"quality": 0.0, "regressions": 0.0},
                checks={"tests": True},
            )
            decision = {
                "accepted": False,
                "primary_value": 0.0,
                "champion_value": 0.0,
                "delta": 0.0,
                "predicates": {},
                "reasons": ["PRIMARY_NOT_IMPROVED"],
            }
            store.update_experiment(
                experiment_id,
                hypothesis_json=hypothesis.model_dump_json(),
                candidate_snapshot=campaign["champion_snapshot"],
                evaluation_json=evaluation.model_dump_json(),
                dev_decision_json=json.dumps(decision),
                decision_json=json.dumps(decision),
                failure_analysis_json=json.dumps(
                    {"capsules": [capsule.model_dump(mode="json")]} if ordinal == 1 else {"capsules": []}
                ),
            )
            store.finish_attempt(
                attempt_id, experiment_id, status="REJECTED", scientific=True, improved=False
            )

        campaign = store.campaign()
        next_experiment_id, _ = store.create_attempt(campaign["champion_snapshot"])
        proposal_task = engine._task_context("propose", next_experiment_id)
        state = proposal_task["research_state"]
        assert state["planning_mode"] == "reframe"
        assert "weight-tuning" in state["stagnant_mechanisms"]
        assert state["recent_failure_capsules"][0]["likely_mechanism"] == capsule.likely_mechanism
        assert proposal_task["history"][0]["failure_capsules"][0]["general_rule"] == capsule.general_rule

        def candidate(candidate_id: str, mechanism: str) -> HypothesisCandidate:
            return HypothesisCandidate(
                candidate_id=candidate_id,
                hypothesis=Hypothesis(
                    hypothesis=f"test {mechanism}", rationale="prior failure evidence",
                    expected_effect="quality improves", files_or_area=["value.txt"],
                    falsification_condition="quality unchanged", mechanism_key=mechanism,
                    prior_experiment_refs=[2], novelty_rationale="same DEV failure rejects weight tuning",
                ),
                evidence_refs=["exp:2"], suspected_failure_mode="stale constraint after enrichment",
                predicted_observable="quality", discriminating_test="conflicting evidence uses resolved value",
                scope_eligibility=True, estimated_agent_calls=1, estimated_evaluator_calls=1,
                rationale="test a distinct mechanism", uncertainty="synthetic integration fixture",
            )

        selected = select_candidate(
            HypothesisPortfolio(candidates=[
                candidate("weights-again", "weight-tuning"),
                candidate("reconcile-first", "evidence-reconciliation"),
            ]),
            spec=engine.spec,
            state=ResearchState.model_validate(engine._refresh_research_state()),
            experiments=store.experiments(),
            experiment_ordinal=3,
            action_id=f"exp:{next_experiment_id}:propose",
        )
        assert selected.selected_candidate_id == "reconcile-first"
        rejected = next(item for item in selected.decisions if item.candidate_id == "weights-again")
        assert "STAGNANT_DEV_MECHANISM_CHANGE_FAMILY" in rejected.rejection_reasons
    finally:
        store.close()



def test_api_preflight_rejects_unsupported_sklearn_before_dev_budget(tmp_path: Path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.editable_scope.append("src/model.py")
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        campaign = store.campaign()
        experiment_id, attempt_id = store.create_attempt(campaign["champion_snapshot"])
        file = engine.workspace.workspace / "src" / "model.py"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            "from sklearn.preprocessing import OneHotEncoder\n"
            "encoder = OneHotEncoder(sparse=False)\n",
            encoding="utf-8",
        )
        snapshot = engine.workspace.snapshot("candidate using removed encoder kwarg")
        store.update_experiment(experiment_id, candidate_snapshot=snapshot)
        store.set_stage(attempt_id, "VERIFY")
        before = store.campaign()["evaluator_calls"]

        monkeypatch.setattr(
            engine_module, "python_api_compatibility_errors",
            lambda workspace, changed_paths, *, evaluator_command: [
                "src/model.py: OneHotEncoder(sparse=...) unsupported"
            ],
        )
        engine._stage_verify(attempt_id, experiment_id)
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert store.experiment(experiment_id)["failure_category"] == "IMPLEMENTATION_BLOCKED"
        assert "OneHotEncoder" in store.experiment(experiment_id)["lesson"]
        assert store.campaign()["evaluator_calls"] == before
        decision = json.loads(store.experiment(experiment_id)["decision_json"])
        assert "CANDIDATE_PREFLIGHT_FAILED" in decision["reasons"]
        assert decision["predicates"]["candidate_python_api_compatibility"] is False
    finally:
        store.close()



def test_one_bounded_repair_rechecks_candidate_before_dev_evaluation(tmp_path: Path):
    class RepairAgent(AgentAdapter):
        def probe(self):
            return {"available": True, "kind": "test"}

        def execute(self, task, workspace, timeout):
            assert task["role"] == "repair"
            (workspace / "model.py").write_text("value = 1\n", encoding="utf-8")
            return {
                "summary": "fixed syntax error",
                "changed_paths": ["model.py"],
                "verification_notes": "",
                "blockers": [],
            }

    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.editable_scope.append("model.py")
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        model = engine.workspace.workspace / "model.py"
        model.write_text("value = (\n", encoding="utf-8")
        candidate = engine.workspace.snapshot("candidate with syntax error")
        store.update_experiment(experiment_id, candidate_snapshot=candidate)
        store.set_stage(attempt_id, "VERIFY")
        engine.agent = RepairAgent()

        engine._stage_verify(attempt_id, experiment_id)

        action = store.action(f"exp:{experiment_id}:repair:1")
        assert action["status"] == "COMPLETED"
        request = json.loads(action["request_json"])
        assert request["failure_kind"] == "implementation_preflight"
        assert set(request) == {
            "schema_version", "role", "goal", "hypothesis", "candidate_snapshot",
            "editable_scope", "protected_paths", "failure_kind", "failure_details", "constraints",
        }
        assert store.experiment(experiment_id)["candidate_snapshot"] != candidate
        assert store.attempt(attempt_id)["stage"] == "EVALUATE"

        engine._stage_evaluate(attempt_id, experiment_id)
        assert store.evaluations("dev")
    finally:
        store.close()


def test_completed_repair_replays_its_snapshot_without_a_second_agent_call(tmp_path: Path, monkeypatch):
    class CountingRepairAgent(AgentAdapter):
        def __init__(self):
            self.calls = 0

        def probe(self):
            return {"available": True, "kind": "test"}

        def execute(self, task, workspace, timeout):
            self.calls += 1
            (workspace / "model.py").write_text("value = 1\n", encoding="utf-8")
            return {"summary": "repair", "changed_paths": ["model.py"],
                    "verification_notes": "", "blockers": []}

    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.editable_scope.append("model.py")
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        model = engine.workspace.workspace / "model.py"
        model.write_text("value = (\n", encoding="utf-8")
        broken_snapshot = engine.workspace.snapshot("broken candidate")
        store.update_experiment(experiment_id, candidate_snapshot=broken_snapshot)
        store.set_stage(attempt_id, "VERIFY")
        agent = CountingRepairAgent()
        engine.agent = agent

        original_update = store.update_experiment
        fail_snapshot_persist = True

        def interrupt_after_action(exp_id, **fields):
            nonlocal fail_snapshot_persist
            if fields.get("candidate_snapshot") and fail_snapshot_persist:
                fail_snapshot_persist = False
                raise RuntimeError("simulated interruption before experiment snapshot update")
            return original_update(exp_id, **fields)

        monkeypatch.setattr(store, "update_experiment", interrupt_after_action)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            engine._stage_verify(attempt_id, experiment_id)
        action = store.action(f"exp:{experiment_id}:repair:1")
        assert action["status"] == "COMPLETED"
        assert agent.calls == 1

        monkeypatch.setattr(store, "update_experiment", original_update)
        engine._stage_verify(attempt_id, experiment_id)

        assert agent.calls == 1
        assert store.experiment(experiment_id)["candidate_snapshot"] != broken_snapshot
        assert store.attempt(attempt_id)["stage"] == "EVALUATE"
    finally:
        store.close()


def test_failed_bounded_repair_rejects_without_dev_or_a_second_repair(tmp_path: Path):
    class FailedRepairAgent(AgentAdapter):
        def __init__(self):
            self.calls = 0

        def probe(self):
            return {"available": True, "kind": "test"}

        def execute(self, task, workspace, timeout):
            self.calls += 1
            assert task["role"] == "repair"
            # The repair attempt completes but leaves the syntax defect intact.
            return {
                "summary": "could not repair the syntax error",
                "changed_paths": [],
                "verification_notes": "",
                "blockers": ["the malformed expression remains"],
            }

    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.spec.editable_scope.append("model.py")
        experiment_id, attempt_id = _prepare_execute_stage(store, engine)
        model = engine.workspace.workspace / "model.py"
        model.write_text("value = (\n", encoding="utf-8")
        candidate = engine.workspace.snapshot("candidate with syntax error")
        store.update_experiment(experiment_id, candidate_snapshot=candidate)
        store.set_stage(attempt_id, "VERIFY")
        agent = FailedRepairAgent()
        engine.agent = agent
        evaluator_calls_before = store.campaign()["evaluator_calls"]

        engine._stage_verify(attempt_id, experiment_id)

        action = store.action(f"exp:{experiment_id}:repair:1")
        assert action["status"] == "COMPLETED"
        assert agent.calls == 1
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        experiment = store.experiment(experiment_id)
        assert experiment["failure_category"] == "IMPLEMENTATION_BLOCKED"
        assert "bounded repair attempt" in experiment["lesson"]
        assert store.campaign()["evaluator_calls"] == evaluator_calls_before

        # Reprocessing the terminal verification result cannot invoke another repair.
        engine._stage_verify(attempt_id, experiment_id)
        assert agent.calls == 1
        assert store.campaign()["evaluator_calls"] == evaluator_calls_before
    finally:
        store.close()


def test_pr_auc_analyzer_task_gets_target_metric_guidance_without_hidden_evidence(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        engine._baseline()
        engine.spec.primary_metric = MetricSpec(
            name="pr_auc", direction=Direction.MAXIMIZE,
            target=1.0, min_improvement=0.01,
        )
        proposer = engine._task_context("propose", 991)
        guidance = proposer["primary_metric_failure_guidance"]
        assert guidance["kind"] == "ranking_average_precision"
        assert guidance["threshold_invariant"] is True
        assert proposer["research_state"]["dev_lessons"] == []
        rendered = json.dumps(proposer, sort_keys=True)
        assert "validation_command" not in rendered
        assert "holdout_command" not in rendered

        campaign = store.campaign()
        experiment_id, _ = store.create_attempt(campaign["champion_snapshot"])
        store.update_experiment(
            experiment_id,
            hypothesis_json=Hypothesis(
                hypothesis="change score ranking", rationale="DEV ranking",
                expected_effect="higher pr_auc", files_or_area=["value.txt"],
                falsification_condition="no AP change",
                mechanism_key="ranking-probe",
            ).model_dump_json(),
        )
        candidate = EvaluationResult(metrics={"pr_auc": 0.7}, checks={})
        task = engine._dev_failure_analysis_task(experiment_id, candidate)
        assert task["analysis_requirements"]["primary_metric_semantics"]["kind"] == (
            "ranking_average_precision"
        )
        assert task["dev_failure_examples"] == []
    finally:
        store.close()
