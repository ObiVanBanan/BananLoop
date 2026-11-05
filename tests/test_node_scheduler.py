from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from bananloop.graph_store import GraphRepository, GraphStoreError
from bananloop.migrations import create_v5_graph_schema
from bananloop.scheduler import HybridScheduler


def _repo():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)")
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    return conn, GraphRepository(conn)


def _command(graph, key, *changes):
    return graph.mutate({"command_id": key, "idempotency_key": key, "expected_graph_revision": graph.graph_revision(),
                         "changes": list(changes)})


def _node(node_id, phase="EXPLORATION", payload=None):
    return {"type": "AddNode", "node_id": node_id, "phase": phase, "payload": payload or {}}


def _status(graph, node_id, from_status, to_status, guard, actor, evidence=()):
    row = graph.conn.execute("SELECT revision FROM research_nodes WHERE node_id=?", (node_id,)).fetchone()
    return {"type": "TransitionNodeStatus", "node_id": node_id,
            "expected_node_revision": int(row["revision"]), "expected_status": from_status,
            "to_status": to_status, "guard": guard, "actor": actor, "evidence_refs": list(evidence)}


def _budget(*, agent_calls=10, experiments=10, evaluator_calls=10):
    return {
        "agent_calls": {"cap": agent_calls, "spent": 0, "reserved": 0, "unknown": 0},
        "experiments": {"cap": experiments, "spent": 0, "reserved": 0, "unknown": 0},
        "evaluator_calls": {"cap": evaluator_calls, "spent": 0, "reserved": 0, "unknown": 0},
    }


def test_node_phase_transition_matrix_and_status_are_orthogonal():
    conn, graph = _repo()
    try:
        _command(graph, "add", _node("metric"))
        _command(graph, "ready", _status(graph, "metric", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "complete", _status(graph, "metric", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:phase-1"]))
        transition = {"type": "TransitionNodePhase", "node_id": "metric", "expected_node_revision": 0,
                      "from_phase": "EXPLORATION", "to_phase": "METRIC_DISCOVERY",
                      "guard": "METRIC_PROPOSAL", "actor": "CORE", "evidence_refs": ["evidence:1"]}
        transition["expected_node_revision"] = 2
        _command(graph, "phase", transition)
        row = conn.execute("SELECT phase,status,revision,payload_json FROM research_nodes WHERE node_id='metric'").fetchone()
        assert (row["phase"], row["status"], row["revision"]) == ("METRIC_DISCOVERY", "PENDING", 3)
        assert json.loads(row["payload_json"])["last_phase_transition"]["evidence_refs"] == ["evidence:1"]

        bad = {**transition, "node_id": "metric", "expected_node_revision": 3,
               "from_phase": "METRIC_DISCOVERY", "to_phase": "OPTIMIZATION", "guard": "METRIC_PROPOSAL"}
        before = (graph.graph_revision(), conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        with pytest.raises(GraphStoreError, match="transition matrix"):
            _command(graph, "bad-phase", bad)
        assert (graph.graph_revision(), conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]) == before
    finally:
        graph.close()


@pytest.mark.parametrize(
    ("from_phase", "to_phase", "guard", "actor", "expected_status"),
    [
        ("EXPLORATION", "METRIC_DISCOVERY", "METRIC_PROPOSAL", "CORE", "PENDING"),
        ("OPTIMIZATION", "EXPLORATION", "REFRAME", "CORE", "PENDING"),
    ],
)
def test_every_declared_phase_transition_is_revision_guarded(from_phase, to_phase, guard, actor, expected_status):
    _, graph = _repo()
    try:
        _command(graph, "add", _node("n", from_phase))
        _command(graph, "ready", _status(graph, "n", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "done", _status(graph, "n", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:phase"]))
        _command(graph, "phase", {"type": "TransitionNodePhase", "node_id": "n", "expected_node_revision": 2,
                                   "from_phase": from_phase, "to_phase": to_phase, "guard": guard,
                                   "actor": actor, "evidence_refs": ["evidence:transition"]})
        row = graph.conn.execute("SELECT phase,status FROM research_nodes WHERE node_id='n'").fetchone()
        assert tuple(row) == (to_phase, expected_status)
    finally:
        graph.close()


def test_hybrid_scheduler_runs_simultaneous_nodes_in_distinct_phases():
    _, graph = _repo()
    try:
        _command(graph, "parallel", _node("explore", "EXPLORATION"), _node("optimize", "OPTIMIZATION"),
                 _node("verify", "VERIFICATION"))
        plan = HybridScheduler(graph).plan(budget=_budget())
        routes = {item.node_id: (item.phase, item.lane, item.resource) for item in plan.candidates}
        assert routes == {
            "explore": ("EXPLORATION", "EVIDENCE_WORKER", "agent_calls"),
            "optimize": ("OPTIMIZATION", "LEGACY_ENGINE_ADAPTER", "experiments"),
            "verify": ("VERIFICATION", "INDEPENDENT_VERIFIER", "evaluator_calls"),
        }
        assert plan.reason == "CONTINUE"
    finally:
        graph.close()


def test_requires_and_and_or_groups_use_persisted_verified_completion():
    conn, graph = _repo()
    try:
        _command(graph, "dag", _node("a"), _node("b"), _node("and-owner", "OPTIMIZATION"),
                 _node("or-owner", "METRIC_DISCOVERY"),
                 {"type": "AddEdge", "source_id": "a", "target_id": "and-owner", "edge_type": "REQUIRES"},
                 {"type": "AddGroup", "group_id": "must-all", "owner_node_id": "and-owner", "kind": "AND", "member_node_ids": ["a", "b"]},
                 {"type": "AddGroup", "group_id": "one-option", "owner_node_id": "or-owner", "kind": "OR", "member_node_ids": ["a", "b"]})
        plan = HybridScheduler(graph).plan(budget=_budget())
        assert {item.node_id for item in plan.candidates} == {"a", "b"}
        assert dict(plan.blocked_nodes) == {"and-owner": ("requires:a", "and:must-all"),
                                             "or-owner": ("or:one-option",)}
        revision_before_blocked_ready = graph.graph_revision()
        with pytest.raises(GraphStoreError, match="dependency-blocked"):
            _command(graph, "blocked-ready", _status(graph, "and-owner", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        assert graph.graph_revision() == revision_before_blocked_ready

        _command(graph, "a-ready", _status(graph, "a", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "a-complete", _status(graph, "a", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:a"]))
        plan = HybridScheduler(graph).plan(budget=_budget())
        assert {item.node_id for item in plan.candidates} == {"b", "or-owner"}

        _command(graph, "b-ready", _status(graph, "b", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "b-complete", _status(graph, "b", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:b"]))
        plan = HybridScheduler(graph).plan(budget=_budget())
        assert {item.node_id for item in plan.candidates} == {"and-owner", "or-owner"}
        assert graph.group_satisfied("must-all", verified_completed_node_ids={"a", "b"})
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='GRAPH_MUTATED'").fetchone()[0] == graph.graph_revision()
    finally:
        graph.close()


def test_scheduler_budget_priority_has_stable_order_and_does_not_overbook_plan():
    _, graph = _repo()
    try:
        _command(graph, "priority", _node("low", payload={"scheduler": {"priority": 0, "upper_bound": 1}}),
                 _node("high-b", payload={"scheduler": {"priority": 5, "upper_bound": 1}}),
                 _node("high-a", payload={"scheduler": {"priority": 5, "upper_bound": 1}}))
        budget = _budget(agent_calls=1)
        scheduler = HybridScheduler(graph)
        first = scheduler.plan(budget=budget)
        second = scheduler.plan(budget=budget)
        assert [item.node_id for item in first.candidates] == ["high-a"]
        assert first == second
        assert first.reason == "CONTINUE"
    finally:
        graph.close()


def test_scheduler_adaptive_stop_distinguishes_budget_blocked_complete_and_requested():
    _, graph = _repo()
    try:
        _command(graph, "node", _node("n"))
        scheduler = HybridScheduler(graph)
        assert scheduler.plan(budget=_budget(agent_calls=0)).reason == "BUDGET_EXHAUSTED"
        assert scheduler.plan(budget=_budget(), stop_requested=True).reason == "STOP_REQUESTED"
        _command(graph, "ready", _status(graph, "n", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "complete", _status(graph, "n", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:n"]))
        result = scheduler.plan(budget=_budget())
        assert result.reason == "ALL_NODES_COMPLETE"
        assert result.candidates == ()
    finally:
        graph.close()


def test_late_dependency_cannot_make_scheduled_node_impossible():
    _, graph = _repo()
    try:
        _command(graph, "nodes", _node("source"), _node("target"))
        _command(graph, "target-ready", _status(graph, "target", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        with pytest.raises(GraphStoreError, match="after scheduling"):
            _command(graph, "late-edge", {"type": "AddEdge", "source_id": "source", "target_id": "target", "edge_type": "REQUIRES"})
        assert graph.graph_revision() == 2
    finally:
        graph.close()


def test_scheduler_fails_closed_on_corrupt_persisted_node_status():
    conn, graph = _repo()
    try:
        _command(graph, "node", _node("n"))
        conn.execute("UPDATE research_nodes SET status='MYSTERY' WHERE node_id='n'")
        with pytest.raises(GraphStoreError, match="unknown status"):
            HybridScheduler(graph).plan(budget=_budget())
    finally:
        graph.close()


def test_protocol_freeze_and_integration_phase_guards_require_durable_verifier_receipts():
    _, graph = _repo()
    try:
        _command(graph, "add-metric", _node("metric", "METRIC_DISCOVERY"))
        _command(graph, "ready-metric", _status(graph, "metric", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "done-metric", _status(graph, "metric", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:proposal"]))
        request = {"type": "TransitionNodePhase", "node_id": "metric", "expected_node_revision": 2,
                   "from_phase": "METRIC_DISCOVERY", "to_phase": "OPTIMIZATION",
                   "guard": "PROTOCOL_FROZEN", "actor": "VERIFIER",
                   "evidence_refs": ["verifier://approval/" + "a" * 64]}
        with pytest.raises(GraphStoreError, match="Optimization spec pinned to verifier approval"):
            _command(graph, "fake-freeze", request)
        row = graph.conn.execute("SELECT phase,status,revision FROM research_nodes WHERE node_id='metric'").fetchone()
        assert tuple(row) == ("METRIC_DISCOVERY", "COMPLETED", 2)

        _command(graph, "add-verification", _node("verify", "VERIFICATION"))
        _command(graph, "ready-verification", _status(graph, "verify", "PENDING", "READY", "SCHEDULER_READY", "CORE"))
        _command(graph, "done-verification", _status(graph, "verify", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE", ["evidence:candidate"]))
        integration = {"type": "TransitionNodePhase", "node_id": "verify", "expected_node_revision": 2,
                       "from_phase": "VERIFICATION", "to_phase": "CONSOLIDATION",
                       "guard": "VERIFIED_RESULT", "actor": "VERIFIER",
                       "evidence_refs": ["integration://receipt/" + "b" * 64]}
        with pytest.raises(GraphStoreError, match="pinned Optimization evaluator"):
            _command(graph, "fake-integration-pass", integration)
    finally:
        graph.close()


def test_graph_campaign_identity_is_explicit_and_immutable():
    conn, graph = _repo()
    try:
        assert graph.campaign_id is None
        graph.bind_campaign_id("campaign-one")
        assert graph.campaign_id == "campaign-one"
        with pytest.raises(GraphStoreError, match="different campaign"):
            graph.bind_campaign_id("campaign-two")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE graph_campaign_identity SET campaign_id='campaign-two'")
    finally:
        graph.close()
def test_candidate_ready_transition_requires_exact_durable_evaluator_receipt():
    from bananloop.contracts import OptimizationCampaignSpecV2

    conn, graph = _repo()
    graph.bind_campaign_id("campaign-1")
    spec = OptimizationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "OPTIMIZATION", "goal": "optimize",
        "source_repo": "/tmp/source", "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1},
        "budget": {}, "role_registry_digest": "b" * 64,
        "evaluation": {"protocol_id": "quality", "version": 1, "content_sha256": "c" * 64,
                       "approval_digest": "d" * 64},
    })
    conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL)")
    conn.execute("INSERT INTO campaigns VALUES(1,?)", (json.dumps(spec.model_dump(mode="json")),))
    try:
        _command(graph, "add-optimization", _node("candidate", "OPTIMIZATION", {
            "source_snapshot_id": "e" * 40, "candidate_snapshot_id": "f" * 40,
        }))
        _command(graph, "candidate-ready", _status(
            graph, "candidate", "PENDING", "READY", "SCHEDULER_READY", "CORE",
        ))
        _command(graph, "candidate-complete", _status(
            graph, "candidate", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE",
            ["candidate:build"],
        ))
        revision = graph.graph_revision()
        with pytest.raises(GraphStoreError, match="durable evaluator run receipt"):
            _command(graph, "candidate-transition", {
                "type": "TransitionNodePhase", "node_id": "candidate",
                "expected_node_revision": 2, "from_phase": "OPTIMIZATION",
                "to_phase": "VERIFICATION", "guard": "CANDIDATE_READY", "actor": "CORE",
                "evidence_refs": ["evaluator://receipt/" + "1" * 64],
            })
        row = conn.execute(
            "SELECT phase,status,revision FROM research_nodes WHERE node_id='candidate'",
        ).fetchone()
        assert tuple(row) == ("OPTIMIZATION", "COMPLETED", 2)
        assert graph.graph_revision() == revision
    finally:
        graph.close()

@pytest.mark.parametrize(("tier", "accepted"), [("dev", True), ("validation", False), ("holdout", False)])
def test_candidate_ready_accepts_dev_only_receipt(monkeypatch, tier, accepted):
    from bananloop.contracts import OptimizationCampaignSpecV2

    conn, graph = _repo()
    graph.bind_campaign_id("campaign-tier")
    spec = OptimizationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "OPTIMIZATION", "goal": "optimize",
        "source_repo": "/tmp/source", "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1},
        "budget": {}, "role_registry_digest": "b" * 64,
        "evaluation": {"protocol_id": "quality", "version": 1, "content_sha256": "c" * 64,
                       "approval_digest": "d" * 64},
    })
    conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL)")
    conn.execute("INSERT INTO campaigns VALUES(1,?)", (json.dumps(spec.model_dump(mode="json")),))
    run = SimpleNamespace(tier=tier, source_snapshot_id="e" * 40,
                          protocol_id="quality", protocol_version=1)

    class _ReceiptAuthority:
        def require_run_receipt(self, *_args, **_kwargs):
            return run

    monkeypatch.setattr(GraphRepository, "evaluator_protocols", property(lambda _self: _ReceiptAuthority()))
    try:
        _command(graph, "add-optimization", _node("candidate", "OPTIMIZATION", {
            "source_snapshot_id": "e" * 40, "candidate_snapshot_id": "f" * 40,
        }))
        _command(graph, "candidate-ready", _status(
            graph, "candidate", "PENDING", "READY", "SCHEDULER_READY", "CORE",
        ))
        _command(graph, "candidate-complete", _status(
            graph, "candidate", "READY", "COMPLETED", "VERIFIED_COMPLETION", "CORE",
            ["candidate:build"],
        ))
        transition = {
            "type": "TransitionNodePhase", "node_id": "candidate",
            "expected_node_revision": 2, "from_phase": "OPTIMIZATION",
            "to_phase": "VERIFICATION", "guard": "CANDIDATE_READY", "actor": "CORE",
            "evidence_refs": ["evaluator://receipt/" + "1" * 64],
        }
        if accepted:
            _command(graph, "candidate-transition", transition)
            row = conn.execute("SELECT phase,status FROM research_nodes WHERE node_id='candidate'").fetchone()
            assert tuple(row) == ("VERIFICATION", "PENDING")
        else:
            revision = graph.graph_revision()
            with pytest.raises(GraphStoreError, match="DEV evaluator evidence"):
                _command(graph, "candidate-transition", transition)
            assert graph.graph_revision() == revision
    finally:
        graph.close()
