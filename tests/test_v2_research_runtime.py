"""Offline RPDI-13R integration: real v2 storage, queue, gateway, and ledger.

No subscription, Codex CLI or external LLM is invoked in these tests.
"""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.contracts import ExplorationCampaignSpecV2
from bananloop.exploration import EvidenceAdapterError
from bananloop.research_ledger import ResearchLedger
from bananloop.v2_research_runtime import (
    V2ResearchRuntime, V2RuntimeError, make_researcher_invoke,
)
from bananloop.worker_queue import DurableWorkerQueue

from test_exploration_adapter import _graph, _pinned_executor, _role_registry, _source_repo


def _runtime(tmp_path, *, max_agent_calls=3, ledger_agent_calls=None):
    source = tmp_path / "source"
    _source_repo(source)
    roles = _role_registry()
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION",
        "goal": "Investigate a public mechanism",
        "source_repo": str(source), "base_commit": "HEAD",
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1,
            "capability_policy": {
                "policy_version": 1,
                "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
                "read_paths": ["README.md"], "write_paths": ["**"], "backend": "LOCAL",
            },
        },
        "budget": {"max_agent_calls": max_agent_calls},
        "role_registry_digest": roles.digest(), "evaluation": None,
    })
    graph = _graph(tmp_path / "campaign.sqlite3", spec)
    ledger = BudgetLedger(tmp_path / "registry.sqlite3")
    try:
        ledger.create_root(
            "campaign-test", {"agent_calls": max_agent_calls if ledger_agent_calls is None
                              else ledger_agent_calls},
        )
    finally:
        ledger.close()
    queue = DurableWorkerQueue(tmp_path / "registry.sqlite3")
    runtime = V2ResearchRuntime(
        graph=graph, queue=queue, campaign_id="campaign-test",
        state_root=tmp_path, role_registry=roles,
    )
    return runtime, graph, queue


def test_two_real_exploration_nodes_preserve_public_memory_and_pins(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    observed = []

    def model_reply(public_input, **pin):
        observed.append(public_input)
        assert pin == {
            "provider": "openai", "model": "gpt-6-luna",
            "model_version": "test-pin-v1", "prompt_digest": "c" * 64,
            "max_model_tokens": 100,
        }
        assert set(public_input) == {
            "protocol", "role_context", "visible_documents", "instruction",
        }
        assert public_input["visible_documents"] == {"README.md": "frozen source\n"}
        assert "source_repo" not in public_input
        assert "capability_grant" not in public_input
        text = f"hypothesis-{len(observed)}"
        sha = hashlib.sha256(text.encode()).hexdigest()
        return {
            "next_experiment": "test a new independent condition",
            "artifact_text": {"observation.txt": text},
            "research_findings": [{
                "claim_key": "mechanism:alpha",
                "statement": f"Unverified observation {len(observed)} supports mechanism alpha.",
                "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE",
                "confidence": 0.4,
                "evidence_refs": [f"artifact://sha256/{sha}"],
            }],
        }

    try:
        campaign_result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(model_reply),
            max_iterations=2,
        )
        assert campaign_result["status"] == "ITERATION_LIMIT"
        assert campaign_result["iterations_this_call"] == 2
        one, two = campaign_result["completed"]
        assert all(item["status"] == "EXPLORATION_COMPLETED" for item in (one, two))
        assert one["profile"] == "FULL"
        assert len(one["research_evidence_ids"]) == 1
        assert queue.get_job(one["job_id"])["state"] == "COMPLETED"
        with pytest.raises(V2RuntimeError, match="not a schedulable"):
            runtime.run_one(
                node_id=one["node_id"], visible_paths=["README.md"],
                invoke=make_researcher_invoke(model_reply),
            )
        assert len(observed) == 2  # replay cannot create a third model call

        assert len(observed) == 2
        assert "mechanism:alpha" in observed[1]["role_context"]["dev_summary"]
        assert observed[1]["role_context"]["public_task"] == "test a new independent condition"
        assert observed[1]["role_context"]["budget_remaining"]["agent_calls"] == 2
        projection = ResearchLedger(graph.conn).project(
            graph=graph, queue=queue,
            campaign_ids=("campaign-test",),
            source_snapshot_id=queue.get_job(one["job_id"])["request"]["base_commit"],
            protocol_digest=ResearchLedger.protocol_digest_for_binding(
                queue.get_job(one["job_id"])["request"]["role_binding"],
            ),
        )
        assert len(projection.evidence) == 2
        assert all(item.verification_status == "UNVERIFIED_ROLE_FINDING" for item in projection.evidence)
    finally:
        queue.close()
        graph.close()


def test_researcher_rejects_score_and_no_evidence(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            runtime.run_one(
                node_id="explore", visible_paths=["README.md"],
                invoke=make_researcher_invoke(
                    lambda *_args, **_kwargs: {
                        "next_experiment": "repeat", "score": 1.0,
                        "artifact_text": {"observation.txt": "invalid"},
                    },
                ),
            )
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'"
        ).fetchone()[0] != "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_hypothesis_portfolio_is_selected_and_persisted_as_evidence(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    hypotheses = [
        {"id": "h1", "hypothesis": "vary the public input size"},
        {"id": "h2", "hypothesis": "hold input size fixed and vary ordering"},
    ]
    try:
        result = runtime.run_one(
            node_id="explore", visible_paths=["README.md"],
            invoke=make_researcher_invoke(lambda *_args, **_kwargs: {
                "hypotheses": hypotheses,
                "selected_hypothesis_id": "h2",
                "next_experiment": "hold input size fixed and vary ordering",
                "artifact_text": {"observation.txt": "two candidates considered"},
            }),
        )
        assert result["status"] == "EXPLORATION_COMPLETED"
        completion = graph.scheduling_snapshot()["nodes"][0]["payload"]["completion_evidence"]
        assert completion["next_experiment"] == "hold input size fixed and vary ordering"
        artifacts = completion["artifact_digests"]
        assert len(artifacts) == 2  # observation plus trusted serialized portfolio
        artifact_root = tmp_path / "rpdi05" / "artifacts"
        serialized = [
            path.read_bytes() for path in artifact_root.rglob("*")
            if path.is_file() and path.read_bytes().startswith(b'{"candidates"')
        ]
        assert len(serialized) == 1
        assert b'"selected_hypothesis_id":"h2"' in serialized[0]
    finally:
        queue.close()
        graph.close()


def test_completed_research_job_is_projected_after_runtime_restart_without_redelivery(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []

    def model_reply(*_args, **_kwargs):
        calls.append("called")
        return {
            "next_experiment": "repeat with a matched control",
            "artifact_text": {"observation.txt": "observed once"},
        }

    try:
        invoke = make_researcher_invoke(model_reply)
        job = runtime.adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        _manifest, _member, executor = _pinned_executor(
            queue, graph, "campaign-test", runtime.role_registry, job, invoke,
        )
        # Simulate process death after queue settlement but before graph and
        # Research Ledger projection.
        runtime.adapter.run_one(job_id=job["job_id"], worker_id="before-crash", executor=executor)
        assert queue.get_job(job["job_id"])["state"] == "COMPLETED"
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] == "READY"
        events_before_recovery = len(queue.events_after(0))

        restarted = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id="campaign-test",
            state_root=tmp_path, role_registry=runtime.role_registry,
        )
        projected = restarted.run_one(
            node_id="explore", visible_paths=["README.md"], invoke=invoke,
        )
        assert projected["status"] == "EXPLORATION_COMPLETED"
        assert calls == ["called"]
        assert len(queue.events_after(0)) == events_before_recovery
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] == "COMPLETED"
        assert len(projected["research_evidence_ids"]) == 0

        # Restart after graph publication but before automatic follow-up
        # creation; the durable completion record is sufficient to resume.
        resumed = restarted.run_campaign(
            visible_paths=["README.md"], invoke=invoke, max_iterations=1,
        )
        assert resumed["status"] == "ITERATION_LIMIT"
        assert resumed["iterations_this_call"] == 1
        assert calls == ["called", "called"]
        assert resumed["completed"][0]["node_id"] != "explore"
    finally:
        queue.close()
        graph.close()


def test_campaign_loop_stops_at_authoritative_agent_budget_and_persists_next_node(tmp_path):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=3, ledger_agent_calls=1)
    calls = []

    def model_reply(*_args, **_kwargs):
        calls.append("one-call")
        return {
            "next_experiment": "follow up after obtaining more budget",
            "artifact_text": {"observation.txt": "one metered observation"},
        }

    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(model_reply),
            max_iterations=5,
        )
        assert result["status"] == "BUDGET_EXHAUSTED"
        assert result["iterations_this_call"] == 1
        assert calls == ["one-call"]
        followups = [node for node in graph.scheduling_snapshot()["nodes"]
                     if node["phase"] == "EXPLORATION" and node["status"] == "PENDING"]
        assert len(followups) == 1
        assert followups[0]["payload"]["hypothesis"] == "follow up after obtaining more budget"
    finally:
        queue.close()
        graph.close()


def test_campaign_loop_reports_pinned_role_allocation_exhaustion(tmp_path):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=5)
    calls = []

    def model_reply(*_args, **_kwargs):
        calls.append("one-call")
        return {
            "next_experiment": "continue while the pinned researcher allocation remains",
            "artifact_text": {"observation.txt": f"observation {len(calls)}"},
        }

    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(model_reply),
            max_iterations=5,
        )
        assert result["status"] == "ROLE_ALLOCATION_EXHAUSTED"
        assert result["iterations_this_call"] == 2
        assert len(calls) == 2
    finally:
        queue.close()
        graph.close()


def test_visibility_denied_before_model_call(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    invoked = []
    try:
        with pytest.raises(EvidenceAdapterError):
            runtime.run_one(
                node_id="explore", visible_paths=["../gold.txt"],
                invoke=make_researcher_invoke(
                    lambda *_args, **_kwargs: invoked.append(True)
                ),
            )
        assert not invoked
    finally:
        queue.close()
        graph.close()


def test_disallowed_artifact_path_fails_through_gateway(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        with pytest.raises(Exception, match="path"):
            runtime.run_one(
                node_id="explore", visible_paths=["README.md"],
                invoke=make_researcher_invoke(
                    lambda *_args, **_kwargs: {
                        "next_experiment": "repeat",
                        "artifact_text": {"../private-answer.txt": "secret"},
                    },
                ),
            )
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'"
        ).fetchone()[0] != "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_one_command_owner_demo_uses_disposable_v2_state(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    command = [
        sys.executable, str(repo / "examples" / "v2_research_diagnostic" / "run_offline.py"),
        "--work-dir", str(tmp_path / "owner-smoke"),
        "--goal", "Investigate whether a controlled condition changes the toy outcome",
    ]
    process = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=90)
    assert process.returncode == 0, process.stdout + "\\n" + process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "OFFLINE_V2_EXPLORATION_PASS"
    assert result["goal"] == "Investigate whether a controlled condition changes the toy outcome"
    assert result["live_model_called"] is False
    assert result["complete_full_pipeline_proven"] is False
    assert result["campaign_loop_status"] == "ITERATION_LIMIT"
    assert len(result["jobs"]) == 2
    assert result["research_evidence_counts"] == [1, 1]
    assert result["second_turn_received_public_memory"] is True


def test_trusted_core_binds_artifact_hash_to_claim(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        result = runtime.run_one(
            node_id="explore", visible_paths=["README.md"],
            invoke=make_researcher_invoke(
                lambda *_args, **_kwargs: {
                    "next_experiment": "repeat with new data",
                    "artifact_text": {"trial.txt": "observed controlled trial"},
                    "research_findings": [{
                        "claim_key": "mechanism:trusted-hash",
                        "statement": "The record is an unverified observation",
                        "kind": "OBSERVATION", "stance": "UNCERTAIN",
                        "confidence": 0.2,
                    }],
                },
            ),
        )
        published = queue.get_result(result["job_id"])
        finding = published["result"]["research_findings"][0]
        expected = hashlib.sha256(b"observed controlled trial").hexdigest()
        assert finding["evidence_refs"] == ["artifact://sha256/" + expected]
    finally:
        queue.close()
        graph.close()


def test_trusted_core_rejects_nonexistent_artifact_ref(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        with pytest.raises(V2RuntimeError, match="not produced"):
            runtime.run_one(
                node_id="explore", visible_paths=["README.md"],
                invoke=make_researcher_invoke(
                    lambda *_args, **_kwargs: {
                        "next_experiment": "repeat",
                        "artifact_text": {"trial.txt": "controlled observation"},
                        "research_findings": [{
                            "claim_key": "mechanism:wrong-hash",
                            "statement": "Unverified observation",
                            "kind": "OBSERVATION", "stance": "UNCERTAIN",
                            "confidence": 0.1,
                            "evidence_refs": ["artifact://sha256/" + "0" * 64],
                        }],
                    },
                ),
            )
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'"
        ).fetchone()[0] != "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_campaign_pause_and_resume_survive_runtime_restart(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    graph.mutate({
        "command_id": "pause-test-node", "idempotency_key": "pause-test-node",
        "expected_graph_revision": graph.graph_revision(),
        "changes": [{"type": "AddNode", "node_id": "pause-research", "phase": "EXPLORATION"}],
    })
    calls = []

    def reply(public_input, **_pin):
        calls.append(public_input)
        return {
            "next_experiment": "resume with a distinct observation",
            "artifact_text": {"resume.txt": "campaign resumed after durable pause"},
        }

    recovered_queue = None
    try:
        assert runtime.pause(reason="test pause")["state"] == "PAUSED"
        paused = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(reply), max_iterations=1,
        )
        assert paused["status"] == "PAUSED" and calls == []
        queue_path = queue.path
        queue.close()

        recovered_queue = DurableWorkerQueue(queue_path)
        recovered = V2ResearchRuntime(
            graph=graph, queue=recovered_queue, campaign_id="campaign-test",
            state_root=tmp_path, role_registry=runtime.role_registry,
        )
        assert recovered.plan().reason == "PAUSED"
        assert recovered.resume()["state"] == "RUNNING"
        resumed = recovered.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(reply), max_iterations=1,
        )
        assert resumed["status"] == "ITERATION_LIMIT"
        assert resumed["iterations_this_call"] == 1 and len(calls) == 1
        assert recovered_queue.get_job(resumed["completed"][0]["job_id"])["state"] == "COMPLETED"
    finally:
        if recovered_queue is not None:
            recovered_queue.close()
        else:
            queue.close()
        graph.close()
