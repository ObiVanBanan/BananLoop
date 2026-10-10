from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bananloop.agents.base import AgentAdapter
from bananloop.agents.codex import CodexAdapter
from bananloop.agents.command import CommandAgentAdapter
from bananloop.contracts import AgentConfig, AgentRoleOverride, BudgetSpec, CampaignSpec, Direction, EvaluationAsset, EvaluationResult, GuardrailSpec, Hypothesis, MetricSpec, RuntimePaths
from bananloop.engine import ControlledInterruption, Engine, EngineError, UnknownActionOutcome, _hash_payload
from bananloop.evaluator_binding import EvaluatorBinding, canonical_hash, make_binding, runtime_descriptor
from bananloop.integrity import sha256_evaluation_asset
from bananloop.report import build_report, render_markdown
from bananloop.store import CampaignStore
from bananloop.trace_export import build_trace_package, export_trace
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


def make_engine(tmp_path: Path, *, agent_config: AgentConfig | None = None) -> tuple[CampaignStore, Engine, Path, str]:
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
        agent=agent_config or AgentConfig(kind="command", command=[sys.executable, "agent.py"]),
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


def test_role_dispatch_and_launch_receipt_are_stable_across_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    strong = {"model": "strong-model", "reasoning_effort": "high"}
    roles = {
        role: AgentRoleOverride(**strong)
        for role in ("bootstrap", "propose", "analyze")
    }
    roles["implement"] = AgentRoleOverride(model="fast-model", reasoning_effort="low")
    config = AgentConfig(
        kind="codex",
        model="fallback-model",
        reasoning_effort="medium",
        roles=roles,
    )
    store, engine, _, _ = make_engine(tmp_path, agent_config=config)
    dispatched: list[tuple[str | None, str | None]] = []
    argv_by_role: dict[str, list[str]] = {}
    fixture_adapter = CommandAgentAdapter([sys.executable, "agent.py"])
    monkeypatch.setattr(
        CodexAdapter,
        "probe",
        lambda self: {"available": True, "kind": "codex", "version": "fixture-codex"},
    )

    def execute_fixture(self, task, workspace, timeout):
        role = task["role"]
        self._resolved_command = lambda: ["codex"]
        argv_by_role[role] = self._argv(
            workspace / "schema.json", workspace / "result.json"
        )
        dispatched.append((self.model, self.reasoning_effort))
        return fixture_adapter.execute(task, workspace, timeout)

    monkeypatch.setattr(CodexAdapter, "execute", execute_fixture)
    try:
        assert config.for_role("bootstrap").model == "strong-model"
        assert config.for_role("bootstrap").reasoning_effort == "high"
        monkeypatch.setenv("BANANLOOP_TEST_STOP_AFTER_STAGE", "VERIFY")
        with pytest.raises(ControlledInterruption):
            engine.run()
        first = next(
            json.loads(row["payload_json"])
            for row in store.events()
            if row["event_type"] == "AGENT_LAUNCH_PROFILE"
        )

        monkeypatch.delenv("BANANLOOP_TEST_STOP_AFTER_STAGE")
        db_path = store.path
        workspace = engine.workspace
        store.close()
        store = CampaignStore(db_path)
        engine = Engine(store, workspace)
        assert engine.run(resume=True)["status"] == "GOAL_MET"
        profiles = [
            json.loads(row["payload_json"])
            for row in store.events()
            if row["event_type"] == "AGENT_LAUNCH_PROFILE"
        ]
        assert profiles == [first, first]
        assert first["roles"]["propose"]["model"] == "strong-model"
        assert first["roles"]["propose"]["reasoning_effort"] == "high"
        assert first["roles"]["analyze"]["model"] == "strong-model"
        assert first["roles"]["implement"]["model"] == "fast-model"
        assert first["roles"]["implement"]["reasoning_effort"] == "low"
        assert ("strong-model", "high") in dispatched
        assert ("fast-model", "low") in dispatched
        assert ("fallback-model", "medium") not in dispatched
        assert argv_by_role["propose"][argv_by_role["propose"].index("-m") + 1] == "strong-model"
        assert argv_by_role["propose"][argv_by_role["propose"].index("-c") + 1] == "model_reasoning_effort=high"
        assert argv_by_role["implement"][argv_by_role["implement"].index("-m") + 1] == "fast-model"
        assert argv_by_role["implement"][argv_by_role["implement"].index("-c") + 1] == "model_reasoning_effort=low"
        role_summary = build_trace_package(store)["summary.json"]["model_roles"]
        assert role_summary["propose"]["effective_model"] is None
        assert role_summary["propose"]["effective_model_status"] == "unknown"
        assert role_summary["implement"]["requested_model"] == "fast-model"
        output = tmp_path / "portable-trace"
        export_trace(store, output, Path(store.spec().source_repo))
        exported = [
            json.loads(line)
            for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert sum(event["event_type"] == "AGENT_LAUNCH_PROFILE" for event in exported) == 2
        assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["campaign_outcome"] == "GOAL_MET"
    finally:
        store.close()


def test_candidate_transfer_process_failure_pauses_as_infrastructure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, engine, _, base = make_engine(tmp_path)

    def fail_transfer(_agent_workspace, _hidden_paths):
        raise WorkspaceError("candidate change transfer from agent workspace failed: git apply rejected patch")

    monkeypatch.setattr(engine.workspace, "apply_agent_changes", fail_transfer)
    try:
        with pytest.raises(EngineError, match="implement failed: candidate change transfer"):
            engine.run()

        campaign = store.campaign()
        assert campaign["status"] == "PAUSED_INFRA"
        assert campaign["agent_calls"] == 2
        assert campaign["champion_snapshot"] == base
        experiment = store.experiments()[0]
        assert experiment["failure_category"] == "CANDIDATE_NOT_MATERIALIZED"
        report = build_report(store)
        assert report["experiments"][0]["causal_record"]["scientific_outcome"] == "INFRA_FAILED"
        assert any(event["event_type"] == "CANDIDATE_TRANSFER_FAILED" for event in store.events())
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
        assert report["technical_outcome"] == "GOAL_MET"
        assert report["scientific_assessment"] == "UNKNOWN"
        assert report["evaluator_evidence"]["trust_level"] == "UNVERIFIED"
        assert "Scientific assessment: **UNKNOWN**" in render_markdown(report)
        assert report["evaluation_protocol"]["validation"]["configured"] is True
        assert report["evaluation_protocol"]["validation"]["baseline"] == 0.0
        assert report["evaluation_protocol"]["validation"]["champion"] == 3.0
        assert report["evaluation_protocol"]["holdout"]["evaluated"] is True
        assert report["evaluation_protocol"]["holdout"]["value"] == 3.0
        assert report["evaluation_protocol"]["holdout"]["target_met"] is True
        assert report["evaluator_binding"]["binding_id"] == store.evaluator_binding()["binding_id"]
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
        assert "VALIDATION_REJECTED" not in rendered_task
        assert "INVALID_VALIDATION" not in rendered_task
        assert "HOLDOUT" not in rendered_task
        assert "validation_command" not in json.dumps(task)
        assert "holdout_command" not in json.dumps(task)
        assert task["research_state"]["planning_mode"] in {"exploit", "explore", "reframe"}
        assert "remaining_budget" in task["research_state"]
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
        assert "123456" not in rendered_proposer
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
