from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bananloop.agents.base import AgentAdapter
from bananloop.contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, EvaluationAsset, EvaluationResult, GuardrailSpec, Hypothesis, MetricSpec, RuntimePaths
from bananloop.engine import ControlledInterruption, Engine, EngineError, UnknownActionOutcome
from bananloop.report import build_report
from bananloop.store import CampaignStore
from bananloop.workspace import WorkspaceManager


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
        " print(json.dumps({'hypothesis':f'step {idx}','rationale':'fixture','expected_effect':'up','files_or_area':['value.txt','guard.txt'],'falsification_condition':'no improvement','mechanism_key':f'step-{idx}'}))\n"
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
        assert git(["rev-parse", "HEAD"], repo) == base
        assert git(["status", "--porcelain"], repo) == ""
    finally:
        store.close()


def test_resume_after_durable_stage_does_not_restart_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        monkeypatch.setenv("BANANLOOP_TEST_STOP_AFTER_STAGE", "VERIFY")
        with pytest.raises(ControlledInterruption):
            engine.run()
        c1 = store.campaign()
        assert c1["stage"] == "VERIFY"
        assert c1["attempt_count"] == 1
        assert c1["agent_calls"] == 2
        monkeypatch.delenv("BANANLOOP_TEST_STOP_AFTER_STAGE")
        out = engine.run(resume=True)
        assert out["status"] == "GOAL_MET"
        assert out["experiment_count"] == 3
        assert out["attempt_count"] == 3
        assert out["agent_calls"] == 6
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
        store.start_action(action_id, experiment_id, "EXECUTE", "agent", request_hash)
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



def test_verify_rejects_frozen_evaluator_drift(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.workspace.ensure(engine.spec.base_commit)
        evaluator_path = engine.workspace.workspace / "evaluator.py"
        expected_hash = hashlib.sha256(evaluator_path.read_bytes()).hexdigest()
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

        engine._stage_verify(attempt_id, experiment_id)

        exp = store.experiment(experiment_id)
        assert store.attempt(attempt_id)["stage"] == "REJECT"
        assert exp["failure_category"] == "EVALUATOR_CHANGED"
        decision = json.loads(exp["decision_json"])
        assert decision["reasons"][0] == "EVALUATOR_CHANGED"
        assert "hash_mismatch:evaluator.py" in decision["reasons"]
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
            sha256=hashlib.sha256(evaluator.read_bytes()).hexdigest(),
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



def test_staged_validation_gates_promotion_and_final_holdout_runs_once(tmp_path: Path):
    store, engine, _, _ = make_staged_engine(tmp_path)
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
    finally:
        store.close()


def test_agent_history_never_contains_validation_payload(tmp_path: Path):
    store, engine, _, _ = make_staged_engine(tmp_path)
    try:
        engine.run()
        history = engine._history()

        assert history
        assert any(row["failure_category"] == "VALIDATION_REJECTED" for row in history)
        for row in history:
            decision = row["decision"]
            if decision is not None:
                assert "validation" not in decision

        task = engine._task_context("propose", 999)
        assert "holdout" not in json.dumps(task).lower()
        assert "validation_command" not in json.dumps(task)
        assert "holdout_command" not in json.dumps(task)
    finally:
        store.close()



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
