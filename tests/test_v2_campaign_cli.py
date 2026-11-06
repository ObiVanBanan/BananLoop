from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace

import pytest

from bananloop.budget_ledger import BudgetLedger
from bananloop.cli import cmd_v2_explore, parser
from bananloop.contracts import RuntimePaths
from bananloop.graph_store import GraphRepository
from bananloop.registry import CampaignRegistry, RegistryError
from bananloop.v2_campaign_cli import V2CampaignCLIError, run_v2_explore


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


def _args(repo, state_dir, **overrides):
    values = {
        "repo": str(repo), "state_dir": str(state_dir), "request_id": "owner-request-1",
        "goal": "Investigate a controlled toy mechanism", "read_path": ["README.md"],
        "max_agent_calls": 2, "max_model_tokens": 128, "max_iterations": 2,
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
        assert ledger.snapshot(entry.campaign_id)["agent_calls"]["cap"] == 2
        assert ledger.snapshot(entry.campaign_id)["model_tokens"]["cap"] == 256
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


def test_v2_cli_live_allowance_uses_pinned_mock_transport_and_resumes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"
    calls = []

    class MockAuth:
        def status(self):
            return {"connected": True}

    class MockResearcher:
        last_usage = None
        last_request_id = None

        def __init__(self, _auth, *, approved_model, timeout_seconds):
            assert approved_model == "gpt-6-luna" and timeout_seconds == 60

        def check_model(self):
            return None

        def __call__(self, public_input, **pin):
            calls.append(public_input)
            assert pin["provider"] == "openai" and pin["model"] == "gpt-6-luna"
            text = f"controlled public observation {len(calls)}"
            return {
                "next_experiment": f"test a distinct condition after observation {len(calls)}",
                "artifact_text": {"observation.txt": text},
            }

    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanAuth", MockAuth)
    monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanResearcher", MockResearcher)
    args = _args(repo, state_dir, approve_up_to_calls=1)
    first = run_v2_explore(args)
    assert first["status"] == "ITERATION_LIMIT"
    assert first["live_model_called"] is True and len(calls) == 1
    assert calls[0]["protocol"] == "rpdi13r-researcher-one-shot-v1"

    second = run_v2_explore(args)
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
