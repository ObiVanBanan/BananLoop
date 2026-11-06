from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess

import pytest
from pydantic import ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.capabilities import CapabilityAuthority
from bananloop.contracts import ExplorationCampaignSpecV2
from bananloop.exploration import EvidenceAdapterError, EvidenceOnlyAttemptAdapter, EvidenceOnlyOutputV2
from bananloop.graph_store import GraphRepository
from bananloop.migrations import create_v5_graph_schema
from bananloop.roles import (
    PinnedRoleExecutorV1, RoleDefinitionV1,
    RoleRegistryV1, TeamManifestAuthority,
)
from bananloop.research_ledger import ResearchFindingV1, ResearchLedger, ResearchLedgerError
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


def _role_registry():
    return RoleRegistryV1(
        registry_id="test-exploration",
        version=1,
        roles=(RoleDefinitionV1(
            role_id="researcher", adapter="codex", provider="openai", model="gpt-6-luna",
            model_version="test-pin-v1", prompt_digest="c" * 64,
            capability_policy={
                "policy_version": 1,
                "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
                "read_paths": ["README.md"], "write_paths": ["**"], "backend": "LOCAL",
            },
            context_fields=("campaign_goal", "node_id", "node_phase", "public_task", "evidence_refs", "dev_summary", "budget_remaining"),
            min_instances=1, max_instances=1, calls_per_instance=2, tokens_per_instance=200,
        ),),
    )


def _pinned_executor(queue, graph, campaign_id, roles, job, invoke):
    spec = graph.campaign_spec()
    authority = TeamManifestAuthority(queue.path)
    try:
        manifest = authority.load(campaign_id, spec_hash=spec.approved_hash(),
                                  registry_digest=roles.digest())
    finally:
        authority.close()
    member = manifest.member(job["request"]["role_binding"]["role_instance_id"])
    return manifest, member, PinnedRoleExecutorV1.from_manifest(manifest, member.instance_id, invoke)


def test_evidence_adapter_snapshots_allowlist_and_replays_completion(tmp_path):
    source = tmp_path / "source"
    base = _source_repo(source)
    roles = _role_registry()
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": "map evidence",
        "source_repo": str(source), "base_commit": "HEAD",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1,
                      "capability_policy": {"policy_version": 1,
                                            "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
                                            "read_paths": ["README.md"], "write_paths": ["**"],
                                            "backend": "LOCAL"}},
        "budget": {}, "role_registry_digest": roles.digest(), "evaluation": None,
    })
    graph_path = tmp_path / "campaign.sqlite3"
    graph = _graph(graph_path, spec)
    registry_path = tmp_path / "registry.sqlite3"
    ledger = BudgetLedger(registry_path)
    ledger.create_root("campaign-test", {"agent_calls": 2})
    ledger.close()
    queue = DurableWorkerQueue(registry_path)
    adapter = EvidenceOnlyAttemptAdapter(graph=graph, queue=queue, campaign_id="campaign-test",
                                         state_root=tmp_path, role_registry=roles)
    try:
        job = adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        assert job["request"]["team_manifest_digest"]
        assert job["request"]["role_binding"]["role_id"] == "researcher"
        assert job["request"]["role_context"]["campaign_goal"] == "map evidence"
        manifest, member, _ = _pinned_executor(queue, graph, "campaign-test", roles, job, lambda *_args, **_kwargs: {})
        wrong_model = member.role.model_copy(update={"model": "fallback-model"})
        wrong_member = member.model_copy(update={"role": wrong_model})
        wrong_executor = PinnedRoleExecutorV1(manifest_digest=manifest.manifest_digest,
                                              member=wrong_member, _invoke=lambda *_args, **_kwargs: {})
        with pytest.raises(EvidenceAdapterError, match="pinned team manifest"):
            adapter.run_one(job_id=job["job_id"], worker_id="worker-test", executor=wrong_executor)
        assert queue.get_job(job["job_id"])["state"] == "READY"
        assert job["request"]["approved_base_ref"] == "HEAD"
        assert job["request"]["base_commit"] == base
        assert graph.conn.execute("SELECT status FROM research_nodes WHERE node_id='explore'").fetchone()[0] == "READY"
        graph.mutate({"command_id": "unrelated-node", "idempotency_key": "unrelated-node",
                      "expected_graph_revision": graph.graph_revision(),
                      "changes": [{"type": "AddNode", "node_id": "later", "phase": "EXPLORATION"}]})
        replayed_job = adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        assert replayed_job["job_id"] == job["job_id"]
        assert replayed_job["request_hash"] == job["request_hash"]

        def execute(request, gateway, **pin):
            assert pin == {"adapter": "codex", "provider": "openai", "model": "gpt-6-luna",
                           "model_version": "test-pin-v1", "prompt_digest": "c" * 64,
                           "max_model_tokens": 100}
            assert gateway.read_file("README.md") == "frozen source\n"
            assert gateway.list_files() == ["README.md"]
            content = b"observed evidence\n"
            artifact_sha = hashlib.sha256(content).hexdigest()
            gateway.write_artifact("observation.txt", content)
            return {"node_id": "explore", "evidence_refs": ["observation:1"],
                    "artifacts": [{"path": "observation.txt", "sha256": artifact_sha}],
                    "limitations": ["single observation"], "next_experiment": "repeat with control",
                    "research_findings": [{"claim_key": "effect:retrieval",
                        "statement": "Retrieval evidence supports the proposed effect.",
                        "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE", "confidence": 0.35,
                        "intervention_type": "feature_representation", "algorithmic_layer": "features",
                        "affected_paths": ["src/features.py:encode"],
                        "evidence_refs": [f"artifact://sha256/{artifact_sha}"]}]}

        _, _, executor = _pinned_executor(queue, graph, "campaign-test", roles, job, execute)
        adapter.run_one(job_id=job["job_id"], worker_id="worker-test", executor=executor)
        artifact = tmp_path / "rpdi05" / "artifacts" / f"{job['job_id']}-f1" / "observation.txt"
        artifact.write_bytes(b"changed after completion")
        with pytest.raises(EvidenceAdapterError, match="changed after"):
            adapter.publish_completed(job["job_id"])
        artifact.write_bytes(b"observed evidence\n")
        first = adapter.publish_completed(job["job_id"])
        replay = adapter.publish_completed(job["job_id"])
        assert replay == first
        assert graph.graph_revision() == 4

        ledger = ResearchLedger(graph.conn)
        protocol_digest = ledger.protocol_digest_for_binding(job["request"]["role_binding"])
        projection_before = ledger.project(campaign_ids=("campaign-test",), source_snapshot_id=base,
                                           protocol_digest=protocol_digest)
        assert len(projection_before.evidence) == 1
        assert projection_before.causal_memory[0].status == "UNVERIFIED"
        assert projection_before.evidence[0].causal_status == "UNVERIFIED_INFERENCE"
        assert projection_before.evidence[0].affected_paths == ("src/features.py",)
        assert projection_before.structural_memory[0].status == "UNVERIFIED"
        assert projection_before.structural_memory[0].support_count == 1
        assert ledger.ingest_completed_job(graph, queue, job["job_id"]) == (
            projection_before.evidence[0].evidence_id,
        )
        assert ledger.project(campaign_ids=("campaign-test",), source_snapshot_id=base,
                              protocol_digest=protocol_digest) == projection_before
        with pytest.raises(ResearchLedgerError, match="source snapshot"):
            ledger.ingest_completed_job(graph, queue, job["job_id"],
                                        expected_source_snapshot_id="0" * 40)
        with pytest.raises(ResearchLedgerError, match="different frozen role protocol"):
            ledger.ingest_completed_job(graph, queue, job["job_id"],
                                        expected_protocol_digest="d" * 64)

        graph.close()
        graph = GraphRepository.open(str(graph_path))
        adapter = EvidenceOnlyAttemptAdapter(graph=graph, queue=queue, campaign_id="campaign-test",
                                             state_root=tmp_path, role_registry=roles)
        ledger = ResearchLedger(graph.conn)
        assert ledger.project(campaign_ids=("campaign-test",), source_snapshot_id=base,
                              protocol_digest=protocol_digest) == projection_before

        graph.mutate({"command_id": "add-second", "idempotency_key": "add-second",
                      "expected_graph_revision": graph.graph_revision(),
                      "changes": [{"type": "AddNode", "node_id": "explore-second", "phase": "EXPLORATION"}]})
        second_job = adapter.enqueue(node_id="explore-second", visible_paths=["README.md"])
        assert "effect:retrieval" in second_job["request"]["role_context"]["dev_summary"]

        def contradict(_request, gateway, **_pin):
            content = b"counterevidence\n"
            artifact_sha = hashlib.sha256(content).hexdigest()
            gateway.write_artifact("counterevidence.txt", content)
            return {"node_id": "explore-second", "evidence_refs": ["observation:2"],
                    "artifacts": [{"path": "counterevidence.txt", "sha256": artifact_sha}],
                    "limitations": ["single counter-observation"], "next_experiment": "test both conditions",
                    "research_findings": [{"claim_key": "effect:retrieval",
                        "statement": "Counterevidence disputes the proposed effect.",
                        "stance": "REFUTES", "kind": "CAUSAL_INFERENCE", "confidence": 0.8,
                        "evidence_refs": [f"artifact://sha256/{artifact_sha}"]}]}

        _, _, second_executor = _pinned_executor(queue, graph, "campaign-test", roles, second_job, contradict)
        adapter.run_one(job_id=second_job["job_id"], worker_id="worker-test", executor=second_executor)
        adapter.publish_completed(second_job["job_id"])
        projection = ledger.project(campaign_ids=("campaign-test",), source_snapshot_id=base,
                                    protocol_digest=protocol_digest)
        assert len(projection.evidence) == 2
        assert len(projection.contradictions) == 1
        assert len(projection.contradictions[0].supporting_evidence_ids) == 1
        assert len(projection.contradictions[0].refuting_evidence_ids) == 1
        assert projection.causal_memory[0].status == "CONTRADICTED"
        package = ledger.export_knowledge(campaign_ids=("campaign-test",), source_snapshot_id=base,
                                          protocol_digest=protocol_digest)
        assert ledger.export_knowledge(campaign_ids=("campaign-test",), source_snapshot_id=base,
                                       protocol_digest=protocol_digest) == package
        assert "validation" not in package.model_dump_json().lower()
        assert "holdout" not in package.model_dump_json().lower()
    finally:
        queue.close()
        graph.close()


def test_revocation_blocks_exploration_graph_commit(tmp_path):
    source = tmp_path / "source"
    _source_repo(source)
    roles = _role_registry()
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": "map evidence",
        "source_repo": str(source), "base_commit": "HEAD",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1,
                      "capability_policy": {"policy_version": 1,
                                            "tools": ["read_file", "write_artifact", "commit_candidate"],
                                            "read_paths": ["README.md"], "write_paths": ["**"],
                                            "backend": "LOCAL"}},
        "budget": {}, "role_registry_digest": roles.digest(), "evaluation": None,
    })
    graph = _graph(tmp_path / "campaign.sqlite3", spec)
    registry_path = tmp_path / "registry.sqlite3"
    ledger = BudgetLedger(registry_path)
    ledger.create_root("campaign-test", {"agent_calls": 2})
    ledger.close()
    queue = DurableWorkerQueue(registry_path)
    adapter = EvidenceOnlyAttemptAdapter(graph=graph, queue=queue, campaign_id="campaign-test",
                                         state_root=tmp_path, role_registry=roles)
    try:
        job = adapter.enqueue(node_id="explore", visible_paths=["README.md"])

        def execute(_request, gateway, **pin):
            assert pin["model"] == "gpt-6-luna"
            content = b"evidence"
            gateway.write_artifact("evidence.txt", content)
            return {"node_id": "explore", "artifacts": [{"path": "evidence.txt", "sha256": hashlib.sha256(content).hexdigest()}],
                    "next_experiment": "repeat"}

        _, _, executor = _pinned_executor(queue, graph, "campaign-test", roles, job, execute)
        adapter.run_one(job_id=job["job_id"], worker_id="worker-test", executor=executor)
        grant = job["request"]["capability_grant"]
        authority = CapabilityAuthority(registry_path)
        try:
            authority.revoke(grant["grant_id"])
        finally:
            authority.close()
        with pytest.raises(EvidenceAdapterError, match="revoked"):
            adapter.publish_completed(job["job_id"])
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
    with pytest.raises(ValidationError):
        ResearchFindingV1.model_validate({
            "claim_key": "hidden", "statement": "must not enter the ledger",
            "stance": "SUPPORTS", "kind": "OBSERVATION", "evidence_refs": ["artifact://sha256/x"],
            "classification": "HOLDOUT",
        })


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
