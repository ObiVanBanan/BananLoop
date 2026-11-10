from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.cli import cmd_v2_explore, cmd_v2_optimize, parser
from bananloop.capabilities import CapabilityPolicyV2
from bananloop.contracts import OptimizationCampaignSpecV2
from bananloop.contracts import RuntimePaths
from bananloop.graph_store import GraphRepository
from bananloop.registry import CampaignRegistry, RegistryError
from bananloop.v2_campaign_cli import (
    V2CampaignCLIError, _aggregate_reported_usage, _required_optimization_evaluator_calls,
    _safe_failure_diagnostics,
    run_v2_explore,
)
from bananloop.v2_optimization_cli import V2OptimizationCLIError, _safe_paths
from bananloop.v2_optimization_cli import run_v2_optimize


def _repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "README.md").write_text("public research task context\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "v2@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "v2 test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "public baseline"], check=True)
    return repo


def test_v2_usage_aggregation_keeps_scalar_tokens_from_nested_siwc_usage():
    calls = [
        {"dispatched": True, "usage": {"input_tokens": 993, "output_tokens": 972,
                                          "total_tokens": 1965, "attribution": {"a": {}}}},
        {"dispatched": True, "usage": {"input_tokens": 1163, "output_tokens": 1136,
                                          "total_tokens": 2299, "attribution": {"b": {}}}},
    ]
    assert _aggregate_reported_usage(calls) == {
        "input_tokens": 2156, "output_tokens": 2108, "total_tokens": 4264,
    }
    assert _aggregate_reported_usage([{"dispatched": True, "usage": "UNKNOWN"}]) == "UNKNOWN"


def test_failure_diagnostics_report_schema_location_without_echoing_model_input():
    class StrictOutput(BaseModel):
        decision: str

    with pytest.raises(ValidationError) as caught:
        StrictOutput.model_validate({"decision": {"private": "untrusted model payload"}})
    diagnostics = _safe_failure_diagnostics(caught.value)
    assert diagnostics
    assert "decision" in diagnostics[0]["location"]
    assert all("private" not in json.dumps(item) for item in diagnostics)


def _args(repo, state_dir, **overrides):
    values = {
        "repo": str(repo), "state_dir": str(state_dir), "request_id": "owner-request-1",
        "goal": "Investigate a controlled toy mechanism", "read_path": ["README.md"],
        "max_agent_calls": 6, "max_model_tokens": 128, "max_iterations": 2,
        "optimization_write_path": [],
        "approve_up_to_calls": 0, "timeout_seconds": 60, "model": "gpt-6-luna",
    }
    values.update(overrides)
    return Namespace(**values)


def test_cli_explore_is_a_separate_v2_route():
    args = parser().parse_args([
        "v2", "explore", "--goal", "test goal", "--read-path", "README.md",
    ])
    assert args.func is cmd_v2_explore
    assert args.approve_up_to_calls == 0
    assert args.model == "gpt-6-luna"
    assert args.max_agent_calls == 8
    assert args.max_evaluator_calls == 16
    assert args.evaluator_fixture is None


@pytest.mark.parametrize(("repeats", "expected"), [(2, 8), (3, 12), (10, 40)])
def test_one_candidate_optimization_budget_covers_all_trusted_tiers(repeats, expected):
    assert _required_optimization_evaluator_calls(repeats) == expected


@pytest.mark.parametrize("repeats", [True, 1, 11, 3.0])
def test_optimization_budget_rejects_invalid_protocol_repeatability(repeats):
    with pytest.raises(V2CampaignCLIError, match="repeatability count is invalid"):
        _required_optimization_evaluator_calls(repeats)


def test_evaluator_fixture_is_frozen_and_persisted_before_any_live_call(tmp_path):
    import base64
    import hashlib

    from bananloop.evaluator_fixture import EvaluatorFixtureV1
    from bananloop.graph_store import GraphRepository

    repo = _repo(tmp_path)
    state_dir = tmp_path / "campaign-state"
    fixture_path = tmp_path / "fixture.json"
    encode = lambda raw: base64.b64encode(raw).decode("ascii")
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": encode(b"evaluator bundle"),
        "entrypoint": ["python3", "{evaluator}/evaluate.py"],
        "tiers": [
            {"tier": tier, "cases": [{"case_id": f"{tier}-1", "input_base64": encode(tier.encode())}]}
            for tier in ("dev", "holdout", "validation")
        ],
    })
    fixture_path.write_bytes(fixture.canonical_bytes())

    result = run_v2_explore(_args(
        repo, state_dir, evaluator_fixture=str(fixture_path),
    ))

    assert result["status"] == "READY_FOR_OWNER_APPROVAL"
    assert result["live_model_called"] is False
    state_root = Path(result["state_dir"])
    graph = GraphRepository.open(str(state_root / "campaign.sqlite3"), campaign_id=result["campaign_id"])
    try:
        spec = graph.campaign_spec()
        assert spec.evaluator_fixture_digest == fixture.content_digest()
        assert (state_root / "trusted-evaluator-fixture.json").is_file()
        published = state_root / "rpdi05" / "published" / fixture.content_digest()
        assert hashlib.sha256(published.read_bytes()).hexdigest() == fixture.content_digest()
    finally:
        graph.close()


def test_v2_exploration_budget_reserves_one_tool_free_protocol_review_call(tmp_path):
    from bananloop.v2_campaign_cli import _build_spec

    repo = _repo(tmp_path)
    spec, roles, _policy = _build_spec(
        repo, "Inspect a controlled task", ("README.md",),
        max_agent_calls=6, max_model_tokens=128, model="gpt-6-luna",
    )
    researcher = roles.role("researcher")
    verifier = roles.role("independent_verifier")
    optimizer = roles.role("optimizer")
    integration_verifier = roles.role("integration_verifier")
    assert sum(role.calls_per_instance for role in roles.roles) <= spec.budget.max_agent_calls
    assert sum(role.tokens_per_instance for role in roles.roles) <= spec.budget.max_model_tokens
    assert researcher.calls_per_instance == 2
    assert verifier.calls_per_instance == 1
    assert optimizer.calls_per_instance == 1
    assert integration_verifier.calls_per_instance == 1
    assert researcher.min_instances == verifier.min_instances == 0
    assert optimizer.min_instances == integration_verifier.min_instances == 0
    assert verifier.capability_policy.tools == ()
    assert verifier.capability_policy.read_paths == ()
    assert verifier.capability_policy.write_paths == ()
    assert verifier.capability_policy.network_access is False


def test_v2_default_call_budget_preserves_exploration_and_funds_two_paired_epochs(tmp_path):
    from bananloop.v2_campaign_cli import _build_spec

    repo = _repo(tmp_path)
    spec, roles, _policy = _build_spec(
        repo, "Explore then optimize", ("README.md",),
        max_agent_calls=8, max_model_tokens=128, model="gpt-6-luna",
    )
    assert spec.budget.max_agent_calls == 8
    assert spec.budget.max_evaluator_calls == 16
    assert roles.role("researcher").calls_per_instance == 2
    assert roles.role("independent_verifier").calls_per_instance == 1
    assert roles.role("optimizer").calls_per_instance == 2
    assert roles.role("integration_verifier").calls_per_instance == 2
    assert sum(role.calls_per_instance for role in roles.roles) == 7


def test_v2_nine_call_budget_funds_three_paired_optimization_epochs(tmp_path):
    from bananloop.v2_campaign_cli import _build_spec

    repo = _repo(tmp_path)
    spec, roles, _policy = _build_spec(
        repo, "Explore then optimize", ("README.md",),
        max_agent_calls=9, max_model_tokens=128, model="gpt-6-luna",
    )
    assert spec.budget.max_agent_calls == 9
    assert roles.role("researcher").calls_per_instance == 2
    assert roles.role("independent_verifier").calls_per_instance == 1
    assert roles.role("optimizer").calls_per_instance == 3
    assert roles.role("integration_verifier").calls_per_instance == 3
    assert sum(role.calls_per_instance for role in roles.roles) == 9


def test_cli_optimize_is_a_separate_owner_approved_v2_route():
    args = parser().parse_args([
        "v2", "optimize", "--campaign-id", "child-123", "--role-registry", "roles.json",
        "--visible-path", "data/train.csv", "--visible-path", "src/model.json",
        "--write-path", "src/model.json",
    ])
    assert args.func is cmd_v2_optimize
    assert args.approve_up_to_calls == 0
    assert args.max_steps == 8


def test_v2_optimization_cli_paths_preserve_read_write_boundary():
    visible, writable = _safe_paths(
        ["src/model.json", "data/train.csv"], ["src/model.json"],
    )
    assert visible == ["data/train.csv", "src/model.json"]
    assert writable == ["src/model.json"]

    with pytest.raises(V2OptimizationCLIError, match="members of visible paths"):
        _safe_paths(["data/train.csv"], ["src/model.json"])


def test_v2_optimization_cli_preflight_uses_frozen_spec_without_provider_auth(tmp_path, monkeypatch):
    import json

    from bananloop.roles import RoleDefinitionV1, RoleRegistryV1
    from bananloop.graph_store import AddNode, GraphRepository, MutateGraph
    from test_v2_pivot_saga import _campaign_db

    repo = _repo(tmp_path)
    (repo / "data").mkdir()
    (repo / "src").mkdir()
    (repo / "data" / "train.csv").write_text("x,target\n1,0\n", encoding="utf-8")
    (repo / "src" / "model.json").write_text('{"weight":0}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "data/train.csv", "src/model.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "bounded optimization source"], check=True)
    state_dir = tmp_path / "state"
    paths = RuntimePaths.for_repo(repo, state_dir)
    registry = CampaignRegistry(repo, paths=paths)
    goal = "Improve the bounded toy ranker using the frozen evaluator"
    entry, state_root, _ = registry.reserve(goal, "v2-optimize-preflight", activate=False)
    roles = RoleRegistryV1(
        registry_id="v2-optimize-preflight", version=1,
        roles=(
            RoleDefinitionV1(
                role_id="optimizer", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="offline-v1", prompt_digest="a" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=("read_file", "write_file", "commit_candidate"),
                    read_paths=("data/**", "src/**"), write_paths=("src/**",),
                    backend="LOCAL", network_access=False,
                ),
                context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                                "constraints", "evidence_refs", "dev_summary", "budget_remaining"),
                min_instances=0, max_instances=1,
                calls_per_instance=4, tokens_per_instance=400,
            ),
            RoleDefinitionV1(
                role_id="integration_verifier", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="offline-v1", prompt_digest="b" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
                    network_access=False,
                ),
                context_fields=("campaign_goal", "public_task"),
                min_instances=0, max_instances=1,
                calls_per_instance=4, tokens_per_instance=400,
            ),
        ),
    )
    spec = OptimizationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "OPTIMIZATION", "goal": goal,
        "source_repo": str(repo),
        "base_commit": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "c" * 64, "scope_version": 1,
            "capability_policy": {
                "tools": ["read_file", "write_file", "commit_candidate"],
                "read_paths": ["data/**", "src/**"], "write_paths": ["src/**"],
                "backend": "LOCAL", "network_access": False,
            },
        },
        "budget": {"max_agent_calls": 12, "max_evaluator_calls": 20},
        "role_registry_digest": roles.digest(),
        "evaluation": {
            "protocol_id": "preflight-protocol", "version": 1,
            "content_sha256": "d" * 64, "approval_digest": "e" * 64,
        },
    })
    _campaign_db(state_root / "campaign.sqlite3", spec)
    graph = GraphRepository.open(str(state_root / "campaign.sqlite3"), campaign_id=entry.campaign_id)
    try:
        graph.mutate(MutateGraph(
            command_id="add-cli-optimization-node", idempotency_key="add-cli-optimization-node",
            expected_graph_revision=graph.graph_revision(),
            changes=(AddNode(
                type="AddNode", node_id="cli-optimization", phase="OPTIMIZATION",
                payload={"source_snapshot_id": spec.base_commit,
                         "hypothesis_id": "improve-weight", "hypothesis": "Adjust the bounded model weight."},
            ),),
        ))
    finally:
        graph.close()
    budget = registry.budget_ledger()
    try:
        budget.create_root(entry.campaign_id, {
            "agent_calls": 12, "evaluator_calls": 20, "experiments": 5, "attempts": 8,
        })
    finally:
        budget.close()
    role_path = tmp_path / "roles.json"
    role_path.write_text(roles.model_dump_json(), encoding="utf-8")

    class NoAuthAllowed:
        def __init__(self):
            raise AssertionError("preflight must not read ChatGPT auth or dispatch a call")

    monkeypatch.setattr("bananloop.v2_optimization_cli.ChatGPTPlanAuth", NoAuthAllowed)
    frozen_bindings = []

    def accept_frozen_fixture(self, *args, **kwargs):
        frozen_bindings.append((args, kwargs))

    monkeypatch.setattr(
        "bananloop.v2_optimization_cli.EvaluatorProtocolAuthority.require_frozen_reference",
        accept_frozen_fixture,
    )
    result = run_v2_optimize(Namespace(
        repo=str(repo), state_dir=str(state_dir), campaign_id=entry.campaign_id,
        role_registry=str(role_path), visible_path=["data/train.csv", "src/model.json"],
        write_path=["src/model.json"], approve_up_to_calls=0, max_steps=8,
        timeout_seconds=60,
    ))
    assert result["status"] == "READY_FOR_OWNER_APPROVAL"
    assert result["scheduler_status"] != "NO_REMAINING_WORK"
    assert result["live_model_called"] is False
    assert frozen_bindings[0][1]["campaign_id"] == entry.campaign_id
    assert frozen_bindings[0][1]["spec_hash"] == spec.approved_hash()

    class MockAuth:
        def status(self):
            return {"connected": True}

    class MockOptimizer:
        def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer):
            assert approved_model == "gpt-6-luna" and timeout_seconds == 60
            self.call_observer = call_observer
            self.request_history = []

        def check_model(self):
            return None

        def __call__(self, public_input, **_pin):
            assert public_input == {"offline_cli_check": True}
            self.last_usage = {"total_tokens": 31}
            receipt = {
                "request_id": "resp_offline_cli_optimizer", "status": "COMPLETED",
                "dispatched": True, "usage": {"total_tokens": 31},
            }
            self.request_history.append(receipt)
            self.call_observer(receipt)
            return {
                "hypothesis_id": "improve-weight", "rationale": "Bounded offline CLI integration check.",
                "changes": {"src/model.json": '{"weight":1}\n'},
            }

    class MockVerifier:
        def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer):
            assert approved_model == "gpt-6-luna" and timeout_seconds == 60
            self.request_history = []

        def check_model(self):
            return None

        def _dispatch_verifier(self, *_args, **_kwargs):
            pytest.fail("the shared one-call allowance must stop before verifier dispatch")

    class RuntimeAfterPreflight:
        def __init__(self, **_kwargs):
            pass

        def plan(self):
            return SimpleNamespace(reason="READY")

        def run_campaign(self, **kwargs):
            kwargs["model_reply"](
                {"offline_cli_check": True}, adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="offline-v1",
                prompt_digest="a" * 64, max_model_tokens=100,
            )
            assert kwargs["model_reply"].last_usage == {"total_tokens": 31}
            executor = kwargs["integration_verifier_executor"]
            member = executor.member
            kwargs["integration_verifier_executor"].dispatch({
                "role_binding": {
                    "team_manifest_digest": executor.manifest_digest,
                    "role_instance_id": member.instance_id,
                    "role_id": member.role.role_id, "provider": member.role.provider,
                    "model": member.role.model, "model_version": member.role.model_version,
                    "adapter": member.role.adapter, "prompt_digest": member.role.prompt_digest,
                },
            }, None)
            return {"status": "SHOULD_NOT_REACH"}

    monkeypatch.setattr("bananloop.v2_optimization_cli.ChatGPTPlanAuth", MockAuth)
    monkeypatch.setattr("bananloop.v2_optimization_cli.V2OptimizationRuntime", RuntimeAfterPreflight)
    approved = run_v2_optimize(Namespace(
        repo=str(repo), state_dir=str(state_dir), campaign_id=entry.campaign_id,
        role_registry=str(role_path), visible_path=["data/train.csv", "src/model.json"],
        write_path=["src/model.json"], approve_up_to_calls=1, max_steps=1,
        timeout_seconds=60,
    ), optimizer_factory=MockOptimizer, verifier_factory=MockVerifier)
    assert approved["status"] == "OWNER_APPROVAL_REQUIRED"
    assert approved["provider_call_attempts"] == 1
    assert approved["live_model_called"] is True
    assert approved["automatic_replay"] is False
    assert Path(approved["report_path"]).is_file()
    assert approved["request_history"]["optimizer"][0]["request_id"] == "resp_offline_cli_optimizer"
    conn = __import__("sqlite3").connect(state_root / "campaign.sqlite3")
    try:
        receipt = json.loads(conn.execute(
            "SELECT payload_json FROM events WHERE event_type='V2_PROVIDER_CALL'",
        ).fetchone()[0])
        assert receipt["role_id"] == "optimizer"
        assert receipt["request_id"] == "resp_offline_cli_optimizer"
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='V2_PROVIDER_CALL'",
        ).fetchone()[0] == 1
    finally:
        conn.close()

    # Simulate a process/reporting failure after the runtime durably completed
    # the graph. Recovery must finalize from the saved trusted evidence without
    # reconstructing provider clients or dispatching another request.
    saved_report = {
        "campaign_id": entry.campaign_id,
        "status": "ALL_NODES_COMPLETE",
        "baseline_candidate_delta": {"status": "VERIFIED"},
        "verification": [{"status": "INDEPENDENT_VERIFICATION_PASS"}],
        "holdout": {"status": "VERIFIED"},
    }
    reports_dir = state_root / "reports"
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / f"v2-optimization-{entry.campaign_id}.json").write_text(
        json.dumps(saved_report), encoding="utf-8",
    )
    reset_conn = __import__("sqlite3").connect(state_root / "campaign.sqlite3")
    try:
        reset_conn.execute("ALTER TABLE campaigns ADD COLUMN updated_at TEXT")
        reset_conn.execute("UPDATE campaigns SET status='READY' WHERE id=1")
        reset_conn.commit()
    finally:
        reset_conn.close()

    class CompletedRuntime:
        def __init__(self, **_kwargs):
            pass

        def plan(self):
            return SimpleNamespace(reason="ALL_NODES_COMPLETE")

        def run_campaign(self, **kwargs):
            assert "model_reply" not in kwargs
            assert "integration_verifier_executor" not in kwargs
            return {"status": "ALL_NODES_COMPLETE", "campaign_id": entry.campaign_id}

    monkeypatch.setattr("bananloop.v2_optimization_cli.ChatGPTPlanAuth", NoAuthAllowed)
    monkeypatch.setattr("bananloop.v2_optimization_cli.V2OptimizationRuntime", CompletedRuntime)
    recovered = run_v2_optimize(Namespace(
        repo=str(repo), state_dir=str(state_dir), campaign_id=entry.campaign_id,
        role_registry=str(role_path), visible_path=["data/train.csv", "src/model.json"],
        write_path=["src/model.json"], approve_up_to_calls=0, max_steps=8,
        timeout_seconds=60,
    ))
    assert recovered["status"] == "ALL_NODES_COMPLETE"
    assert recovered["recovery_mode"] == "FINALIZATION_ONLY"
    assert recovered["provider_call_attempts"] == 0
    assert recovered["live_model_called"] is False
    assert recovered["campaign_terminal_status"] == "GOAL_MET"
    assert Path(recovered["recovery_report_path"]).is_file()
    terminal_conn = __import__("sqlite3").connect(state_root / "campaign.sqlite3")
    try:
        assert terminal_conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()[0] == "GOAL_MET"
    finally:
        terminal_conn.close()


def test_cli_entrypoint_creates_persisted_v2_campaign_without_auth(tmp_path):
    repo = _repo(tmp_path)
    command = [
        sys.executable, "-m", "bananloop.cli", "v2", "explore",
        "--repo", str(repo), "--state-dir", str(tmp_path / "state"),
        "--request-id", "cli-entrypoint", "--goal", "Test a controlled toy question",
        "--read-path", "README.md",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY_FOR_OWNER_APPROVAL"
    assert payload["profile"] == "FULL" and payload["live_model_called"] is False
    assert (tmp_path / "state" / "campaigns" / payload["campaign_id"] / "campaign.sqlite3").is_file()


def test_v2_cli_prepares_full_campaign_without_provider_call_and_resumes_same_spec(tmp_path):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"
    args = _args(repo, state_dir)
    registry = CampaignRegistry(repo, paths=RuntimePaths.for_repo(repo, state_dir))
    active_entry, _, _ = registry.reserve("Keep existing selected campaign", "existing-active")
    first = run_v2_explore(args)
    assert first["status"] == "READY_FOR_OWNER_APPROVAL"
    assert first["profile"] == "FULL" and first["live_model_called"] is False

    registry_conn = registry._connect()
    try:
        assert registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0] == active_entry.campaign_id
    finally:
        registry_conn.close()
    entry, state_root, _ = registry.reserve(args.goal, args.request_id)
    graph = GraphRepository.open(str(state_root / "campaign.sqlite3"), campaign_id=entry.campaign_id)
    queue = registry.worker_queue()
    ledger = registry.budget_ledger()
    try:
        assert graph.campaign_spec().approved_hash() == first["spec_hash"]
        nodes = graph.scheduling_snapshot()["nodes"]
        assert len(nodes) == 1 and nodes[0]["node_id"] == "explore-initial"
        assert queue.campaign_control(entry.campaign_id)["state"] == "RUNNING"
        assert ledger.snapshot(entry.campaign_id)["agent_calls"]["cap"] == 6
        assert ledger.snapshot(entry.campaign_id)["model_tokens"]["cap"] == 768
    finally:
        ledger.close()
        queue.close()
        graph.close()

    replay = run_v2_explore(args)
    assert replay["campaign_id"] == first["campaign_id"]
    assert replay["spec_hash"] == first["spec_hash"]


def test_v2_cli_requires_exact_owner_call_approval_before_auth_or_inference(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"

    class NoAuthAllowed:
        def __init__(self):
            raise AssertionError("auth must not be read without explicit call approval")

    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanAuth", NoAuthAllowed)
    result = run_v2_explore(_args(repo, state_dir))
    assert result["status"] == "READY_FOR_OWNER_APPROVAL"


def test_v2_cli_live_allowance_uses_pinned_mock_transport_and_resumes(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"
    calls = []

    class MockAuth:
        def status(self):
            return {"connected": True}

    class MockResearcher:
        last_usage = None
        last_request_id = None

        def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer=None):
            assert approved_model == "gpt-6-luna" and timeout_seconds == 60
            self.call_observer = call_observer
            self.request_history = []

        def check_model(self):
            return None

        def __call__(self, public_input, **pin):
            calls.append(public_input)
            receipt = {"request_id": None, "status": "COMPLETED",
                       "dispatched": True, "usage": "UNKNOWN"}
            self.request_history.append(receipt)
            if self.call_observer:
                self.call_observer(receipt)
            assert pin["provider"] == "openai" and pin["model"] == "gpt-6-luna"
            text = f"controlled public observation {len(calls)}"
            if public_input["role_context"].get("evidence_refs"):
                evidence_id = public_input["role_context"]["evidence_refs"][0]
                return {
                    "hypotheses": [{
                        "id": "evidence_followup", "hypothesis": "Follow up on the prior observation.",
                        "rationale": "This candidate is tied to the available public finding.",
                        "next_experiment": f"test a distinct condition after observation {len(calls)}",
                        "falsification_condition": "The predicted follow-up observation is absent.",
                        "expected_evidence": "A bounded follow-up result.",
                        "evidence_refs": [evidence_id], "target_claim_key": "mock:observation",
                    }, {
                        "id": "independent_option", "hypothesis": "Test another public condition.",
                        "rationale": "An independently bounded alternative.",
                        "next_experiment": "test an independent condition",
                        "falsification_condition": "The alternative prediction is absent.",
                        "expected_evidence": "A bounded independent observation.",
                    }],
                    "selected_hypothesis_id": "evidence_followup",
                    "artifact_text": {"observation.txt": text},
                    "research_findings": [{
                        "claim_key": "mock:observation",
                        "statement": "An unverified mock follow-up observation; no result is asserted.",
                        "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
                    }],
                }
            return {
                "next_experiment": f"test a distinct condition after observation {len(calls)}",
                "artifact_text": {"observation.txt": text},
                "research_findings": [{
                    "claim_key": "mock:observation",
                    "statement": "An unverified mock observation is recorded; no outcome is asserted.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
                }],
            }

    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanAuth", MockAuth)
    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanResearcher", MockResearcher)
    args = _args(repo, state_dir, approve_up_to_calls=1)

    def invoke_cli():
        argv = [
            "v2", "explore", "--repo", args.repo, "--state-dir", args.state_dir,
            "--request-id", args.request_id, "--goal", args.goal,
            "--read-path", "README.md", "--max-agent-calls", str(args.max_agent_calls),
            "--max-model-tokens", str(args.max_model_tokens), "--max-iterations",
            str(args.max_iterations), "--approve-up-to-calls", str(args.approve_up_to_calls),
            "--timeout-seconds", str(args.timeout_seconds), "--model", args.model,
        ]
        parsed = parser().parse_args(argv)
        assert parsed.func is cmd_v2_explore
        assert parsed.func(parsed) == 0
        return json.loads(capsys.readouterr().out)

    first = invoke_cli()
    assert first["status"] == "ITERATION_LIMIT"
    assert first["live_model_called"] is True and len(calls) == 1
    assert calls[0]["protocol"] == "rpdi13r-researcher-one-shot-v1"

    second = invoke_cli()
    assert second["status"] == "ITERATION_LIMIT"
    assert second["campaign_id"] == first["campaign_id"]
    assert len(calls) == 2
    assert calls[1]["role_context"]["public_task"].startswith("test a distinct condition")

    registry = CampaignRegistry(repo, paths=RuntimePaths.for_repo(repo, state_dir))
    _entry, state_root, _ = registry.reserve(args.goal, args.request_id)
    queue = registry.worker_queue()
    try:
        assert queue.conn.execute(
            "SELECT COUNT(*) FROM worker_results WHERE job_id IN "
            "(SELECT job_id FROM worker_jobs WHERE campaign_id=?)",
            (first["campaign_id"],),
        ).fetchone()[0] == 2
        assert queue.conn.execute(
            "SELECT COUNT(*) FROM worker_events e JOIN worker_jobs j USING(job_id) "
            "WHERE j.campaign_id=? AND e.event_type='JOB_COMPLETED'",
            (first["campaign_id"],),
        ).fetchone()[0] == 2
    finally:
        queue.close()


def test_v2_cli_persists_provider_receipt_when_runtime_rejects_reply(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"

    class MockAuth:
        def status(self):
            return {"connected": True}

    class MockResearcher:
        last_usage = {"total_tokens": 17}
        last_request_id = "resp_schema_rejected"

        def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer=None):
            receipt = {"request_id": "resp_schema_rejected", "status": "COMPLETED",
                       "dispatched": True, "usage": {"total_tokens": 17}}
            self.request_history = [receipt]
            if call_observer:
                call_observer(receipt)

        def check_model(self):
            return None

        def __call__(self, *_args, **_kwargs):
            return {}

    def fail_campaign(self, **_kwargs):
        raise ValueError("invalid researcher reply")

    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanAuth", MockAuth)
    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanResearcher", MockResearcher)
    monkeypatch.setattr("bananloop.v2_campaign_cli.V2ResearchRuntime.run_campaign", fail_campaign)
    result = run_v2_explore(_args(repo, state_dir, approve_up_to_calls=1))
    assert result["status"] == "RUN_FAILED"
    assert result["failure_type"] == "ValueError", result
    assert result["automatic_replay"] is False
    report = json.loads(__import__("pathlib").Path(result["report_path"]).read_text())
    assert report["failure_type"] == "ValueError"
    assert "invalid researcher reply" not in json.dumps(report)
    assert report["request_history"][0]["request_id"] == "resp_schema_rejected"

    campaign_db = __import__("pathlib").Path(result["state_dir"]) / "campaign.sqlite3"
    conn = __import__("sqlite3").connect(campaign_db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='V2_PROVIDER_CALL'",
        ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["../outside.txt", ".env", "tests/evaluator.py", "/etc/passwd"])
def test_v2_cli_rejects_protected_or_traversing_read_paths_before_campaign_creation(tmp_path, path):
    repo = _repo(tmp_path)
    with pytest.raises(V2CampaignCLIError, match="read path"):
        run_v2_explore(_args(repo, tmp_path / "state", read_path=[path]))
    assert not (tmp_path / "state").exists()


def test_v2_cli_rejects_request_reuse_with_different_goal(tmp_path):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"
    first = run_v2_explore(_args(repo, state_dir))
    assert first["status"] == "READY_FOR_OWNER_APPROVAL"
    with pytest.raises(RegistryError, match="different goal"):
        run_v2_explore(_args(repo, state_dir, goal="A different frozen campaign objective"))
