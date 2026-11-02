from __future__ import annotations

import json
import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from bananloop.graph_store import (
    GraphIdempotencyConflict,
    GraphRepository,
    GraphRevisionConflict,
    GraphStoreError,
    MutateGraph,
)
from bananloop.migrations import create_v5_graph_schema


def make_repository():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    return conn, GraphRepository(conn)


def create_file_database(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()


def node(node_id: str, phase: str = "EXPLORATION"):
    return {"type": "AddNode", "node_id": node_id, "phase": phase}


def edge(source: str, target: str, edge_type: str = "REQUIRES"):
    return {
        "type": "AddEdge",
        "source_id": source,
        "target_id": target,
        "edge_type": edge_type,
    }


def command(command_id: str, key: str, revision: int, *changes):
    return {
        "command_id": command_id,
        "idempotency_key": key,
        "expected_graph_revision": revision,
        "changes": list(changes),
    }


def test_graph_command_commits_nodes_edges_group_revision_and_event_once():
    conn, graph = make_repository()
    try:
        first = command(
            "cmd-1", "request-1", 0,
            node("a"), node("b"), node("goal", "OPTIMIZATION"),
            edge("a", "goal"), edge("b", "goal", "DERIVED_FROM"),
            {
                "type": "AddGroup", "group_id": "alternatives", "owner_node_id": "goal",
                "kind": "OR", "member_node_ids": ["a", "b"],
            },
        )
        first["evidence_refs"] = ["ev-2", "ev-1", "ev-2"]
        equivalent_replay = {**first, "evidence_refs": ["ev-1", "ev-2"]}
        response = graph.mutate(first)

        assert response["graph_revision"] == 1
        assert response["evidence_refs"] == ["ev-1", "ev-2"]
        assert graph.graph_revision() == 1
        assert graph.group_satisfied("alternatives", verified_completed_node_ids={"b"})
        assert not graph.group_satisfied("alternatives", verified_completed_node_ids=set())
        assert graph.mutate(equivalent_replay) == response
        event = conn.execute(
            "SELECT payload_json FROM events WHERE event_type='GRAPH_MUTATED'"
        ).fetchone()[0]
        assert json.loads(event)["evidence_refs"] == ["ev-1", "ev-2"]
        assert conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='GRAPH_MUTATED'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_edges").fetchone()[0] == 2
    finally:
        graph.close()


def test_and_group_requires_every_verified_member():
    _, graph = make_repository()
    try:
        graph.mutate(command(
            "cmd-and", "key-and", 0,
            node("a"), node("b"), node("owner"),
            {
                "type": "AddGroup", "group_id": "both", "owner_node_id": "owner",
                "kind": "AND", "member_node_ids": ["a", "b"],
            },
        ))
        assert not graph.group_satisfied("both", verified_completed_node_ids={"a"})
        assert graph.group_satisfied("both", verified_completed_node_ids={"a", "b"})
    finally:
        graph.close()


def test_requires_graph_accepts_multi_parent_dags_and_rejects_cycles_atomically():
    conn, graph = make_repository()
    try:
        graph.mutate(command(
            "cmd-dag", "key-dag", 0,
            *(node(f"n{i}") for i in range(5)),
            edge("n0", "n3"), edge("n1", "n3"), edge("n2", "n4"), edge("n3", "n4"),
        ))
        before = tuple(
            conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("research_nodes", "research_edges", "commands", "events")
        )

        with pytest.raises(GraphStoreError, match="acyclic"):
            graph.mutate(command(
                "cmd-cycle", "key-cycle", 1, edge("n4", "n0")
            ))

        assert graph.graph_revision() == 1
        after = tuple(
            conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("research_nodes", "research_edges", "commands", "events")
        )
        assert after == before
    finally:
        graph.close()


def test_nonblocking_knowledge_edges_may_form_cycles_and_forward_references_work():
    _, graph = make_repository()
    try:
        graph.mutate(command(
            "cmd-knowledge", "key-knowledge", 0,
            edge("a", "b", "DERIVED_FROM"),
            edge("b", "a", "RELATED_TO"),
            node("a"), node("b"),
        ))
        assert graph.graph_revision() == 1
    finally:
        graph.close()


def test_random_topological_requires_graphs_are_accepted():
    randomizer = random.Random(19)
    for case in range(20):
        conn, graph = make_repository()
        try:
            count = randomizer.randint(2, 16)
            changes = [node(f"n{i}") for i in range(count)]
            for source in range(count):
                for target in range(source + 1, count):
                    if randomizer.random() < 0.12:
                        changes.append(edge(f"n{source}", f"n{target}"))
            graph.mutate(command(f"cmd-{case}", f"key-{case}", 0, *changes))
            assert graph.graph_revision() == 1
        finally:
            graph.close()


def test_concurrent_graph_proposals_cannot_both_commit_the_same_revision(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    create_file_database(path)
    barrier = threading.Barrier(2)

    def propose(suffix):
        repository = GraphRepository.open(str(path))
        barrier.wait(timeout=5)
        try:
            return repository.mutate(command(
                f"cmd-{suffix}", f"key-{suffix}", 0, node(f"node-{suffix}")
            ))
        finally:
            repository.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(propose, "first"),
            pool.submit(propose, "second"),
        ]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result(timeout=10))
            except GraphRevisionConflict as exc:
                errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    verify = GraphRepository.open(str(path))
    try:
        assert verify.graph_revision() == 1
        assert verify.conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert verify.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert verify.conn.execute("SELECT COUNT(*) FROM research_nodes").fetchone()[0] == 1
    finally:
        verify.close()


def test_idempotency_payload_mismatch_and_stale_revision_do_not_mutate():
    conn, graph = make_repository()
    try:
        original = command("cmd-1", "same-key", 0, node("a"))
        response = graph.mutate(original)
        with pytest.raises(GraphIdempotencyConflict):
            graph.mutate(command("cmd-2", "same-key", 1, node("b")))
        with pytest.raises(GraphIdempotencyConflict):
            graph.mutate(command("cmd-1", "different-key", 1, node("b")))
        with pytest.raises(GraphRevisionConflict) as error:
            graph.mutate(command("cmd-stale", "key-stale", 0, node("c")))
        assert error.value.actual == response["graph_revision"] == 1
        assert graph.graph_revision() == 1
        assert conn.execute("SELECT COUNT(*) FROM research_nodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        graph.close()


def test_graph_change_models_reject_unknown_fields_and_duplicate_group_members():
    with pytest.raises(ValueError):
        MutateGraph.from_document(json.dumps(command(
            "cmd", "key", 0, {**node("a"), "untrusted": "ignored"}
        )))
    with pytest.raises(ValueError):
        MutateGraph.from_document(command(
            "cmd", "key", 0,
            {
                "type": "AddGroup", "group_id": "g", "owner_node_id": "a",
                "kind": "OR", "member_node_ids": ["a", "a"],
            },
        ))
