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

from test_exploration_adapter import _graph, _role_registry, _source_repo


def _runtime(tmp_path, *, max_agent_calls=3):
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
        ledger.create_root("campaign-test", {"agent_calls": max_agent_calls})
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
        one = runtime.run_one(
            node_id="explore", visible_paths=["README.md"],
            invoke=make_researcher_invoke(model_reply),
        )
        assert one["status"] == "EXPLORATION_COMPLETED"
        assert one["profile"] == "FULL"
        assert len(one["research_evidence_ids"]) == 1
        assert queue.get_job(one["job_id"])["state"] == "COMPLETED"
        with pytest.raises(V2RuntimeError, match="not a schedulable"):
            runtime.run_one(
                node_id="explore", visible_paths=["README.md"],
                invoke=make_researcher_invoke(model_reply),
            )
        assert len(observed) == 1  # replay cannot create a second model call

        graph.mutate({
            "command_id": "add-second", "idempotency_key": "add-second",
            "expected_graph_revision": graph.graph_revision(),
            "changes": [{
                "type": "AddNode", "node_id": "explore-second",
                "phase": "EXPLORATION",
            }],
        })
        two = runtime.run_one(
            node_id="explore-second", visible_paths=["README.md"],
            invoke=make_researcher_invoke(model_reply),
        )
        assert two["status"] == "EXPLORATION_COMPLETED"
        assert len(observed) == 2
        assert "mechanism:alpha" in observed[1]["role_context"]["dev_summary"]
        projection = ResearchLedger(graph.conn).project(
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
    ]
    process = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=90)
    assert process.returncode == 0, process.stdout + "\\n" + process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "OFFLINE_V2_EXPLORATION_PASS"
    assert result["live_model_called"] is False
    assert result["complete_full_pipeline_proven"] is False
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
