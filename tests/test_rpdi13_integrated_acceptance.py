"""Cross-layer RPDI-13 acceptance slice with real local authorities.

This test composes real Exploration/ResearchLedger persistence with the
verified protocol handoff into a separate Optimization child, executes its
frozen evaluator and integration verifier, and checks the result through API
and CLI read models.
"""
from __future__ import annotations

import json
import hashlib
import io
import os
import sqlite3
import subprocess
import threading
import urllib.request
import zipfile
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from bananloop.api import create_server
from bananloop.budget_ledger import BudgetLedger
from bananloop.contracts import (
    AutonomyScopeV2, CapabilityPolicyV2, ExplorationCampaignSpecV2,
    ProtocolCase, ProtocolManifest,
)
from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority, EvaluatorProtocolV1, EvaluatorTierManifestV1,
    MetricDiscoveryProposalV1,
)
from bananloop.graph_store import AddNode, GraphRepository, MutateGraph, TransitionNodeStatus
from bananloop.migrations import create_v5_graph_schema
from bananloop.registry import CampaignRegistry
from bananloop.research_ledger import ResearchLedger
from bananloop.cli import parser
from bananloop.roles import RoleDefinitionV1, RoleRegistryV1
from bananloop.trusted_evaluator import TrustedEvaluatorRunner, _case_protocol_digest

from test_exploration_adapter import _role_registry, _source_repo
from test_evaluator_verification import _receipt, build_full_optimization_runtime
from test_v2_pivot_saga import _proposal_for_child


TOKEN = "rpdi13-integrated-test-token"


def _api_json(url: str, *, method="GET", payload=None, key=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if key:
        headers["Idempotency-Key"] = key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise AssertionError(f"API {method} {url} returned {exc.code}: {exc.read().decode()}") from exc


@pytest.mark.parametrize("real_isolation", [False, True], ids=["test-runner", "real-bwrap"])
def test_exploration_research_handoff_child_agrees_through_api_and_cli(
    tmp_path, capsys, monkeypatch, real_isolation,
):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "source"
    _source_repo(repo)
    (repo / "src" / "optimizer" / "model").mkdir(parents=True)
    ranker = repo / "src" / "optimizer" / "model" / "ranker.py"
    ranker.write_text("baseline ranker = 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "src/optimizer/model/ranker.py"], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=RPDI-13 test",
        "-c", "user.email=rpdi13@example.invalid", "commit", "-qm", "baseline optimizer",
    ], check=True)
    base_commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    ranker.write_text("candidate ranker = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "src/optimizer/model/ranker.py"], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=RPDI-13 test",
        "-c", "user.email=rpdi13@example.invalid", "commit", "-qm", "candidate optimizer",
    ], check=True)
    candidate_snapshot_id = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    researcher_roles = _role_registry()
    integration_role = RoleDefinitionV1(
        role_id="integration_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="rpdi13-test-pin",
        prompt_digest="a" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "public_task"), priority=1,
        min_instances=1, max_instances=1, calls_per_instance=3,
        tokens_per_instance=100,
    )
    optimizer_role = RoleDefinitionV1(
        role_id="optimizer", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="rpdi13-test-pin",
        prompt_digest="b" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=("read_file", "write_file", "commit_candidate"),
            read_paths=("src/**",), write_paths=("src/**",), backend="LOCAL",
            network_access=False,
        ),
        context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                        "constraints", "evidence_refs", "dev_summary"),
        priority=1, min_instances=1, max_instances=1,
        calls_per_instance=3, tokens_per_instance=500,
    )
    roles = RoleRegistryV1(
        registry_id="rpdi13-integrated-roles", version=1,
        roles=(*researcher_roles.roles, integration_role, optimizer_role),
    )
    policy = CapabilityPolicyV2(
        tools=("read_file", "list_files", "write_artifact", "commit_candidate"),
        read_paths=("README.md",), write_paths=("**",), backend="LOCAL",
    )
    scope = AutonomyScopeV2(
        version=1, allowed_paths=("src/optimizer",),
        max_child_depth=2, max_child_campaigns=1,
    )
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2,
        "mode": "EXPLORATION",
        "goal": "Collect a bounded observation, then hand off frozen optimization.",
        "source_repo": str(repo),
        "base_commit": base_commit,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": scope.content_digest(),
            "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
            "capability_policy": policy.model_dump(mode="json"),
        },
        "budget": {"max_agent_calls": 10, "max_evaluator_calls": 20,
                   "max_experiments": 5, "max_attempts": 8},
        "role_registry_digest": roles.digest(),
        "evaluation": None,
    })

    registry = CampaignRegistry(repo)
    entry, state_root, created = registry.reserve(spec.goal, "rpdi13-integrated")
    assert created
    database = state_root / "campaign.sqlite3"
    conn = sqlite3.connect(database, isolation_level=None)
    try:
        conn.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        create_v5_graph_schema(conn)
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,"
            "spec_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        created_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO campaigns(id,spec_json,spec_hash,status,created_at,updated_at) VALUES(1,?,?,?, ?,?)",
            (json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
             spec.approved_hash(), "PAUSED_NEEDS_INPUT", created_at, created_at),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    finally:
        conn.close()

    budget = BudgetLedger(registry.path)
    budget.create_root(entry.campaign_id, {
        "agent_calls": 100, "evaluator_calls": 100, "experiments": 50, "attempts": 80,
    })
    budget.close()
    queue = registry.worker_queue()
    graph = GraphRepository.open(str(database), campaign_id=entry.campaign_id)
    server = create_server(str(repo), port=0, token=TOKEN, poll_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root_url = f"http://127.0.0.1:{server.server_port}/v1/campaigns"

    try:
        graph.mutate({
            "command_id": "add-exploration-node", "idempotency_key": "add-exploration-node",
            "expected_graph_revision": graph.graph_revision(),
            "changes": [{"type": "AddNode", "node_id": "explore-1", "phase": "EXPLORATION"}],
        })
        from bananloop.v2_research_runtime import V2ResearchRuntime, make_researcher_invoke

        observed_turns = []

        def mock_researcher(public_input, **_pinned):
            observed_turns.append(public_input)
            turn = len(observed_turns)
            content = f"controlled observation {turn}\n"
            artifact_digest = hashlib.sha256(content.encode()).hexdigest()
            if turn == 1:
                return {
                    "hypotheses": [
                        {
                            "id": "matched-control", "hypothesis": "The effect survives a matched control.",
                            "rationale": "A controlled comparison can expose the mechanism.",
                            "next_experiment": "run a matched control observation",
                            "falsification_condition": "The control has the same baseline response.",
                            "expected_evidence": "A controlled observation under matched conditions.",
                        },
                        {
                            "id": "unrelated-check", "hypothesis": "An unrelated condition explains the outcome.",
                            "rationale": "A separate condition can check an alternative explanation.",
                            "next_experiment": "test an unrelated condition",
                            "falsification_condition": "The unrelated condition does not change the outcome.",
                            "expected_evidence": "A public outcome for the unrelated condition.",
                        },
                    ],
                    "selected_hypothesis_id": "matched-control",
                    "artifact_text": {"observation.txt": content},
                    "limitations": ["one deterministic public observation"],
                    "research_findings": [{
                        "claim_key": "effect:retrieval",
                        "statement": "The deterministic observation supports retrieval effect.",
                        "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE", "confidence": 0.4,
                        "intervention_type": "feature_representation",
                        "algorithmic_layer": "features",
                        "affected_paths": ["src/optimizer/ranker.py"],
                        "evidence_refs": [f"artifact://sha256/{artifact_digest}"],
                    }],
                }
            evidence_id = public_input["role_context"]["evidence_refs"][0]
            return {
                "hypotheses": [
                    {
                        "id": "model-choice", "hypothesis": "An unrelated condition explains the outcome.",
                        "rationale": "The researcher prefers a fresh condition.",
                        "next_experiment": "test an unrelated condition",
                        "falsification_condition": "The unrelated condition has no effect.",
                        "expected_evidence": "A public outcome for the unrelated condition.",
                    },
                    {
                        "id": "evidence-choice", "hypothesis": "The matched-control observation replicates.",
                        "rationale": "This candidate directly follows the durable prior finding.",
                        "next_experiment": "replicate the matched-control observation",
                        "falsification_condition": "The matched-control result does not replicate.",
                        "expected_evidence": "A replication result for the prior claim.",
                        "evidence_refs": [evidence_id],
                        "target_claim_key": "effect:retrieval",
                    },
                ],
                "selected_hypothesis_id": "model-choice",
                "artifact_text": {"observation.txt": content},
                "limitations": ["one deterministic public replication"],
                "research_findings": [{
                    "claim_key": "effect:retrieval",
                    "statement": "A second deterministic observation supports retrieval effect.",
                    "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE", "confidence": 0.5,
                    "intervention_type": "feature_representation",
                    "algorithmic_layer": "features",
                    "affected_paths": ["src/optimizer/ranker.py"],
                    "evidence_refs": [f"artifact://sha256/{artifact_digest}"],
                }],
            }

        research_runtime = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            state_root=state_root, role_registry=roles,
        )
        exploration = research_runtime.run_campaign(
            visible_paths=["README.md"],
            invoke=make_researcher_invoke(mock_researcher), max_iterations=2,
            worker_id="integrated-v2-researcher",
        )
        assert exploration["iterations_this_call"] == 2
        assert len(observed_turns) == 2
        assert observed_turns[1]["role_context"]["evidence_refs"]
        assert "effect:retrieval" in observed_turns[1]["role_context"]["dev_summary"]
        assert observed_turns[1]["role_context"]["public_task"] == (
            "run a matched control observation"
        )
        portfolio_files = (state_root / "rpdi05" / "artifacts").rglob("*")
        portfolios = [
            json.loads(path.read_text(encoding="utf-8")) for path in portfolio_files
            if path.is_file() and b'"core_selection"' in path.read_bytes()
        ]
        assert any(item["researcher_preferred_hypothesis_id"] == "model-choice"
                   and item["selected_hypothesis_id"] == "evidence-choice"
                   for item in portfolios)
        job_id = exploration["completed"][-1]["job_id"]
        job = queue.get_job(job_id)
        artifact_digest = hashlib.sha256(b"controlled observation 2\n").hexdigest()
        ledger = ResearchLedger(graph.conn)
        role_binding = job["request"]["role_binding"]
        protocol_digest = ledger.protocol_digest_for_binding(role_binding)
        evidence_ids = exploration["completed"][-1]["research_evidence_ids"]
        assert len(evidence_ids) == 1

        scope_index = _api_json(root_url + f"/{entry.campaign_id}/research?limit=50")
        assert scope_index["source"] == "PERSISTED"
        assert len(scope_index["scopes"]) >= 1
        public_knowledge = _api_json(
            root_url + f"/{entry.campaign_id}/research?source_snapshot_id={base_commit}"
            f"&protocol_digest={protocol_digest}"
        )
        current_evidence = next(
            item for item in public_knowledge["evidence"]
            if item["evidence_id"] in evidence_ids
        )
        assert current_evidence["verification_status"] == "UNVERIFIED_ROLE_FINDING"
        assert current_evidence["causal_status"] == "UNVERIFIED_INFERENCE"

        input_bytes = b'{"prompt":"closed deterministic evaluator case","label":"pass"}'
        input_digest = hashlib.sha256(input_bytes).hexdigest()
        holdout_input_bytes = b'{"prompt":"separate final holdout case","label":"holdout"}'
        holdout_input_digest = hashlib.sha256(holdout_input_bytes).hexdigest()
        bundle_buffer = io.BytesIO()
        evaluator = b'''import json, os
from pathlib import Path
tier = Path(os.environ["BANANLOOP_TIER"])
manifest = json.loads((tier / "manifest.json").read_text())
cases = json.loads((tier / "input-map.json").read_text())
candidate = Path(os.environ["BANANLOOP_CANDIDATE"])
ranker = (candidate / "src/optimizer/model/ranker.py").read_text()
quality = 0.9 if "ranker = 2" in ranker else 0.8 if "ranker = 1" in ranker else 0.5
print(json.dumps({
    "status": "ok", "metrics": {"quality": quality},
    "protocol_sha256": manifest["protocol_sha256"],
    "cases": [{key: item[key] for key in ("case_id", "repetition", "input_sha256")} for item in cases],
}))
'''
        with zipfile.ZipFile(bundle_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("evaluate.py", evaluator)
        bundle_bytes = bundle_buffer.getvalue()
        bundle_digest = hashlib.sha256(bundle_bytes).hexdigest()
        preliminary = EvaluatorProtocolV1(
            protocol_id="integrated-quality", version=1,
            source_snapshot_id=base_commit,
            evaluator_bundle_sha256=bundle_digest,
            entrypoint=("python3", "{evaluator}/evaluate.py"),
            metric_id="quality", metric_field="quality", direction="MAXIMIZE",
            aggregation="MEAN",
            tiers=(
                EvaluatorTierManifestV1(tier="dev", manifest_sha256="0" * 64, case_count=1),
                EvaluatorTierManifestV1(tier="holdout", manifest_sha256="0" * 64, case_count=1),
                EvaluatorTierManifestV1(tier="validation", manifest_sha256="0" * 64, case_count=1),
            ),
            repeatability_runs=2,
        )
        evaluator_manifest = ProtocolManifest(
            protocol_sha256=_case_protocol_digest(preliminary),
            cases=(ProtocolCase(case_id="integrated-case", repetition=0,
                                input_sha256=input_digest),),
        )
        manifest_bytes = json.dumps(
            evaluator_manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ).encode()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        holdout_manifest = ProtocolManifest(
            protocol_sha256=_case_protocol_digest(preliminary),
            cases=(ProtocolCase(case_id="integrated-holdout-case", repetition=0,
                                input_sha256=holdout_input_digest),),
        )
        holdout_manifest_bytes = json.dumps(
            holdout_manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ).encode()
        holdout_manifest_digest = hashlib.sha256(holdout_manifest_bytes).hexdigest()
        protocol = preliminary.model_copy(update={
            "tiers": (
                EvaluatorTierManifestV1(tier="dev", manifest_sha256=manifest_digest, case_count=1),
                EvaluatorTierManifestV1(tier="holdout", manifest_sha256=holdout_manifest_digest, case_count=1),
                EvaluatorTierManifestV1(tier="validation", manifest_sha256=manifest_digest, case_count=1),
            ),
        })
        completed_job = queue.get_job(job_id)
        completed_result = queue.get_result(job_id)
        role_binding = job["request"]["role_binding"]
        source_event_cursor = max(event["event_id"] for event in queue.events(job_id))
        protocol_proposal_payload = {
            "schema_version": 1, "campaign_id": entry.campaign_id,
            "spec_hash": spec.approved_hash(), "source_snapshot_id": base_commit,
            "source_node_id": job["request"]["node_id"], "source_job_id": job_id,
            "source_fence": completed_job["fence"],
            "source_request_hash": completed_job["request_hash"],
            "source_output_digest": completed_result["output_digest"],
            "source_event_cursor": source_event_cursor,
            "proposer_role_id": role_binding["role_id"],
            "proposer_role_instance_id": role_binding["role_instance_id"],
            "proposer_binding_digest": hashlib.sha256(json.dumps(
                role_binding, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
            "protocol": protocol.model_dump(mode="json"),
            "evidence_refs": (f"artifact://sha256/{artifact_digest}",),
        }
        protocol_proposal_digest = hashlib.sha256(json.dumps(
            protocol_proposal_payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode()).hexdigest()
        protocol_proposal = MetricDiscoveryProposalV1(
            proposal_digest=protocol_proposal_digest, **protocol_proposal_payload,
        )
        receipt = _receipt(protocol_proposal)
        authority = EvaluatorProtocolAuthority(graph.conn)
        authority._append_proposal(protocol_proposal)
        authority._append_verified_receipt(receipt)
        parent_published = state_root / "rpdi05" / "published"
        parent_published.mkdir(parents=True, exist_ok=True)
        for data, digest in (
            (bundle_bytes, bundle_digest), (manifest_bytes, manifest_digest),
            (input_bytes, input_digest), (holdout_manifest_bytes, holdout_manifest_digest),
            (holdout_input_bytes, holdout_input_digest),
        ):
            (parent_published / digest).write_bytes(data)

        setup = {
            "parent_spec": spec, "parent_id": entry.campaign_id,
            "parent_db": database, "repo": repo,
        }
        child_spec = spec.model_dump(mode="json")
        child_spec["mode"] = "OPTIMIZATION"
        child_spec["autonomy"]["capability_policy"] = {
            "policy_version": 1,
            "tools": ["read_file", "write_file", "commit_candidate"],
            "read_paths": ["src/**"], "write_paths": ["src/**"],
            "backend": "LOCAL", "network_access": False,
        }
        child_spec["evaluation"] = {
            "protocol_id": protocol.protocol_id, "version": protocol.version,
            "content_sha256": protocol.content_digest(),
            "approval_digest": receipt.approval_digest,
        }
        proposal = _proposal_for_child(setup, child_spec)
        proposal_receipt = _api_json(
            root_url + f"/{entry.campaign_id}/v2/pivot-proposals", method="POST",
            payload=proposal.model_dump(mode="json"), key=proposal.proposal_id,
        )
        assert proposal_receipt["status"] == "PENDING"
        decision = {
            "campaign_id": entry.campaign_id,
            "request_id": "approve-frozen-optimization-child",
            "proposal_id": proposal.proposal_id, "digest": proposal.digest,
            "decision": "approve",
        }
        _api_json(
            root_url + f"/{entry.campaign_id}/v2/pivot-decisions", method="POST",
            payload=decision, key=decision["request_id"],
        )
        child = _api_json(
            root_url + f"/{entry.campaign_id}/v2/pivot-executions", method="POST",
            payload={"proposal_id": proposal.proposal_id, "digest": proposal.digest},
            key="activate-frozen-optimization-child",
        )
        child_id = child["child_campaign_id"]
        assert child["state"] == "ACTIVE"

        child_state, _ = registry.resolve(child_id, activate=False)
        child_db = child_state / "campaign.sqlite3"
        child_graph = GraphRepository.open(str(child_db), campaign_id=child_id)
        child_queue = registry.worker_queue()
        try:
            real_runner = TrustedEvaluatorRunner() if real_isolation else None
            if real_runner is not None and not real_runner._sandbox_available():
                pytest.skip("host does not permit production bubblewrap namespaces")
            coordinator, trusted, executor = build_full_optimization_runtime(
                graph=child_graph, queue=child_queue, campaign_id=child_id,
                state_root=child_state, source_snapshot_id=base_commit,
                candidate_snapshot_id=candidate_snapshot_id, protocol=protocol,
                manifest=evaluator_manifest, registry=roles, runner=real_runner,
            )
            child_graph.mutate(MutateGraph(
                command_id="add-integrated-child-candidate",
                idempotency_key="add-integrated-child-candidate",
                expected_graph_revision=child_graph.graph_revision(),
                changes=(AddNode(
                    type="AddNode", node_id="integrated-child-candidate", phase="OPTIMIZATION",
                    payload={"source_snapshot_id": base_commit,
                             "candidate_snapshot_id": candidate_snapshot_id},
                ),),
            ))
            child_graph.mutate(MutateGraph(
                command_id="ready-integrated-child-candidate",
                idempotency_key="ready-integrated-child-candidate",
                expected_graph_revision=child_graph.graph_revision(),
                changes=(TransitionNodeStatus(
                    type="TransitionNodeStatus", node_id="integrated-child-candidate",
                    expected_node_revision=0, expected_status="PENDING", to_status="READY",
                    guard="SCHEDULER_READY", actor="CORE",
                ),),
            ))
            baseline_jobs = trusted.enqueue_repeatability(
                candidate_snapshot_id=base_commit, tier="validation",
            )
            for job_id in baseline_jobs:
                trusted.run_one(job_id, worker_id="integrated-baseline")
            baseline_receipt = trusted.finalize_repeatability(baseline_jobs)

            dev_receipt = coordinator.evaluate_candidate(
                node_id="integrated-child-candidate", worker_id="integrated-evaluator",
            )
            assert dev_receipt.tier == "dev"
            assert dev_receipt.candidate_snapshot_id == candidate_snapshot_id
            current_node = next(
                node for node in child_graph.scheduling_snapshot()["nodes"]
                if node["node_id"] == "integrated-child-candidate"
            )
            assert (current_node["phase"], current_node["status"]) == ("VERIFICATION", "PENDING")
            child_graph.mutate(MutateGraph(
                command_id="ready-integrated-child-verification",
                idempotency_key="ready-integrated-child-verification",
                expected_graph_revision=child_graph.graph_revision(),
                changes=(TransitionNodeStatus(
                    type="TransitionNodeStatus", node_id="integrated-child-candidate",
                    expected_node_revision=int(current_node["revision"]),
                    expected_status="PENDING", to_status="READY",
                    guard="SCHEDULER_READY", actor="CORE",
                ),),
            ))
            verification_receipt = coordinator.verify_candidate(
                node_id="integrated-child-candidate",
                baseline_run_receipt_digest=baseline_receipt.receipt_digest,
                executor=executor, worker_id="integrated-verifier",
            )
            assert verification_receipt.decision == "PASS"
            replay = coordinator.verify_candidate(
                node_id="integrated-child-candidate",
                baseline_run_receipt_digest=baseline_receipt.receipt_digest,
                executor=executor, worker_id="integrated-verifier",
            )
            assert replay == verification_receipt
            from bananloop.v2_optimization_runtime import V2OptimizationRuntime

            runtime = V2OptimizationRuntime(
                graph=child_graph, queue=child_queue, campaign_id=child_id,
                state_root=child_state, role_registry=roles,
                trusted_evaluator=trusted,
                integration_verifier=coordinator.integration_verifier,
            )
            child_graph.mutate(MutateGraph(
                command_id="add-v2-optimizer-proposal",
                idempotency_key="add-v2-optimizer-proposal",
                expected_graph_revision=child_graph.graph_revision(),
                changes=(AddNode(
                    type="AddNode", node_id="v2-optimizer-proposal", phase="OPTIMIZATION",
                    payload={"source_snapshot_id": base_commit,
                             "hypothesis": "Improve the bounded deterministic ranker."},
                ),),
            ))

            def optimizer_reply(public_input, **_pinned):
                assert set(public_input) == {
                    "protocol", "role_context", "visible_documents", "instruction",
                }
                assert public_input["visible_documents"]["src/optimizer/model/ranker.py"] == (
                    "baseline ranker = 0\n"
                )
                assert "closed deterministic evaluator case" not in json.dumps(public_input).lower()
                return {
                    "hypothesis_id": "improve-ranker",
                    "rationale": "Apply the research-selected ranker improvement.",
                    "changes": {"src/optimizer/model/ranker.py": "candidate ranker = 2\n"},
                }

            candidate = runtime.run_one(
                visible_paths=["src/optimizer/model/ranker.py"],
                hypothesis_id="improve-ranker", model_reply=optimizer_reply,
                worker_id="integrated-v2-optimizer",
            )
            assert candidate["status"] == "CANDIDATE_CREATED"
            assert candidate["candidate_snapshot_id"] != base_commit
            optimized = runtime.run_campaign(
                visible_paths=["src/optimizer/model/ranker.py"],
                integration_verifier_executor=executor, max_steps=2,
                worker_id="integrated-v2-evaluator",
            )
            assert [step["status"] for step in optimized["steps"]] == [
                "DEV_EVALUATED", "INDEPENDENT_VERIFICATION_PASS",
            ]
            assert optimized["steps"][-1]["champion"]["snapshot_id"] == candidate[
                "candidate_snapshot_id"
            ]
            assert optimized["holdout"]["tier"] == "holdout"
            assert optimized["holdout"]["candidate_snapshot_id"] == candidate["candidate_snapshot_id"]
            assert optimized["holdout"]["status"] == "VERIFIED"
            holdout_metric = optimized["holdout"]["metric"]
            assert holdout_metric["metric_id"] == "quality"
            assert holdout_metric["metric_field"] == "quality"
            assert holdout_metric["direction"] == "MAXIMIZE"
            assert holdout_metric["value"] in {0.8, 0.9}
            assert holdout_metric["unit"] is None
            assert holdout_metric["n"] is None
            final_report = json.loads(Path(optimized["final_report_path"]).read_text())
            assert final_report["campaign_id"] == entry.campaign_id
            assert final_report["optimization_campaign_id"] == child_id
            assert final_report["exploration_report"]["campaign_id"] == entry.campaign_id
            assert final_report["exploration_report"]["status"] == "ITERATION_LIMIT"
            assert len(final_report["exploration_report"]["completed_experiments"]) == 2
            assert final_report["optimization_report"]["champion"]["snapshot_id"] == candidate[
                "candidate_snapshot_id"
            ]
            assert final_report["optimization_report"]["integration_evidence"][-1]["decision"] == "PASS"
            assert {item["tier"] for item in final_report["optimization_report"]["evaluation_receipts"]} == {
                "dev", "validation",
            }
            assert final_report["optimization_report"]["holdout"]["tier_manifest_sha256"] == holdout_manifest_digest
        finally:
            child_queue.close()
            child_graph.close()

        parent_lifecycle = _api_json(root_url + f"/{entry.campaign_id}/lifecycle")
        child_lifecycle = _api_json(root_url + f"/{child_id}/lifecycle")
        assert any(item["child_id"] == child_id for item in parent_lifecycle["children"])
        assert child_lifecycle["available"] is True
        assert len(child_lifecycle["protocol_handoffs"]) == 1
        child_api_graph = _api_json(root_url + f"/{child_id}/graph")
        child_node = next(item for item in child_api_graph["nodes"]
                          if item["node_id"] == "integrated-child-candidate")
        assert (child_node["phase"], child_node["status"]) == ("CONSOLIDATION", "COMPLETED")
        assert child_node["completion_evidence_refs"]
        child_cli_args = parser().parse_args([
            "campaigns", "inspect", "--repo", str(repo),
            "--campaign-id", child_id, "--json",
        ])
        assert child_cli_args.func(child_cli_args) == 0
        child_cli = json.loads(capsys.readouterr().out)
        assert child_cli["campaign_id"] == child_id
        assert child_cli["graph"]["graph_revision"] == child_api_graph["graph_revision"]
        child_cli_node = next(item for item in child_cli["graph"]["nodes"]
                              if item["node_id"] == "integrated-child-candidate")
        assert child_cli_node == child_node
        if os.environ.get("BANANLOOP_BROWSER_LIVE_E2E") == "1":
            browser_probe = Path(__file__).parent / "browser" / "live_dashboard_probe.js"
            browser_result = subprocess.run(
                ["node", str(browser_probe),
                 root_url.rsplit("/v1/campaigns", 1)[0], child_id],
                env={**os.environ, "BANANLOOP_TEST_TOKEN": TOKEN},
                capture_output=True, text=True, timeout=90,
            )
            assert browser_result.returncode == 0, browser_result.stdout + browser_result.stderr
        api_snapshot = _api_json(root_url + f"/{entry.campaign_id}/snapshot")
        api_graph = _api_json(root_url + f"/{entry.campaign_id}/graph")
        api_observability = _api_json(root_url + f"/{entry.campaign_id}/observability")
        api_index = _api_json(root_url + "?limit=50")
        api_campaign = next(item for item in api_index["campaigns"]
                            if item["campaign_id"] == entry.campaign_id)

        cli_args = parser().parse_args([
            "campaigns", "inspect", "--repo", str(repo),
            "--campaign-id", entry.campaign_id, "--json",
        ])
        assert cli_args.func(cli_args) == 0
        cli = json.loads(capsys.readouterr().out)
        assert cli["campaign_id"] == entry.campaign_id == api_snapshot["campaign_id"]
        assert cli["campaign"] == api_campaign
        assert cli["campaign"]["profile"] == "FULL"
        assert cli["snapshot"]["graph_revision"] == api_snapshot["graph_revision"]
        assert cli["graph"]["graph_revision"] == api_graph["graph_revision"]
        assert cli["research_scopes"]["scopes"] == scope_index["scopes"]
        assert cli["observability"]["scheduler"]["reason"] == api_observability["scheduler"]["reason"]
        assert cli["observability"]["scheduler"]["event_cursor"] == api_observability["scheduler"]["event_cursor"]
        assert cli["observability"]["scheduler"]["worker_event_cursor"] == api_observability["scheduler"]["worker_event_cursor"]
        assert cli["lifecycle"]["children"] == parent_lifecycle["children"]
        assert cli["lifecycle"]["protocol_handoffs"] == parent_lifecycle["protocol_handoffs"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        graph.close()
        queue.close()
