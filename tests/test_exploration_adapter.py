from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess

import pytest
from pydantic import ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.contracts import ExplorationCampaignSpecV2
from bananloop.exploration import EvidenceAdapterError, EvidenceOnlyAttemptAdapter, EvidenceOnlyOutputV2
from bananloop.graph_store import GraphRepository
from bananloop.migrations import create_v5_graph_schema
from bananloop.worker_queue import DurableWorkerQueue


def _source_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("frozen source\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "base"], check=True)
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def _graph(path, spec):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)")
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL)")
    conn.execute("INSERT INTO campaigns VALUES(1,?)", (json.dumps(spec.model_dump(mode="json")),))
    graph = GraphRepository(conn)
    graph.mutate({"command_id": "add-node", "idempotency_key": "add-node", "expected_graph_revision": 0,
                  "changes": [{"type": "AddNode", "node_id": "explore", "phase": "EXPLORATION"}]})
    return graph


def test_evidence_adapter_snapshots_allowlist_and_replays_completion(tmp_path):
    source = tmp_path / "source"
    base = _source_repo(source)
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": "map evidence",
        "source_repo": str(source), "base_commit": "HEAD",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1},
        "budget": {}, "role_registry_digest": "b" * 64, "evaluation": None,
    })
    graph = _graph(tmp_path / "campaign.sqlite3", spec)
    registry_path = tmp_path / "registry.sqlite3"
    ledger = BudgetLedger(registry_path)
    ledger.create_root("campaign-test", {"agent_calls": 2})
    ledger.close()
    queue = DurableWorkerQueue(registry_path)
    adapter = EvidenceOnlyAttemptAdapter(graph=graph, queue=queue, campaign_id="campaign-test", state_root=tmp_path)
    try:
        job = adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        assert job["request"]["approved_base_ref"] == "HEAD"
        assert job["request"]["base_commit"] == base

        def execute(request, workspace, artifacts):
            assert (workspace / "README.md").read_text() == "frozen source\n"
            assert not (workspace / ".git").exists()
            content = b"observed evidence\n"
            (artifacts / "observation.txt").write_bytes(content)
            return {"node_id": "explore", "evidence_refs": ["observation:1"],
                    "artifacts": [{"path": "observation.txt", "sha256": hashlib.sha256(content).hexdigest()}],
                    "limitations": ["single observation"], "next_experiment": "repeat with control"}

        adapter.run_one(job_id=job["job_id"], worker_id="worker-test", execute=execute)
        artifact = tmp_path / "rpdi05" / "artifacts" / f"{job['job_id']}-f1" / "observation.txt"
        artifact.write_bytes(b"changed after completion")
        with pytest.raises(EvidenceAdapterError, match="changed after"):
            adapter.publish_completed(job["job_id"])
        artifact.write_bytes(b"observed evidence\n")
        first = adapter.publish_completed(job["job_id"])
        replay = adapter.publish_completed(job["job_id"])
        assert replay == first
        assert graph.graph_revision() == 2
    finally:
        queue.close()
        graph.close()


def test_evidence_only_output_rejects_optimization_scores():
    with pytest.raises(ValidationError):
        EvidenceOnlyOutputV2.model_validate({"node_id": "n", "next_experiment": "x", "score": 0.9})
    with pytest.raises(ValidationError, match="unique"):
        EvidenceOnlyOutputV2.model_validate({"node_id": "n", "next_experiment": "x", "artifacts": [
            {"path": "a.txt", "sha256": "a" * 64}, {"path": "a.txt", "sha256": "b" * 64},
        ]})


def test_evidence_allowlist_rejects_traversal_and_evaluator_paths():
    from bananloop.exploration import EvidenceAdapterError, _safe_relative

    for path in (".", "../outside", "/etc/passwd", "C:/private", ".env", "tests/holdout/cases.json", "evaluator/secret.json"):
        with pytest.raises(EvidenceAdapterError):
            _safe_relative(path)


def test_state_directory_creation_rejects_symlinked_ancestors(tmp_path):
    from bananloop.exploration import EvidenceAdapterError, _ensure_state_dir

    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "rpdi05").symlink_to(outside, target_is_directory=True)
    with pytest.raises(EvidenceAdapterError, match="symlink"):
        _ensure_state_dir(root, "rpdi05", "published")
