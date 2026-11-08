"""Cross-layer RPDI-13 acceptance slice with real local authorities.

This test composes real Exploration/ResearchLedger persistence with the
verified protocol handoff into a separate Optimization child, executes its
frozen evaluator and integration verifier, and checks the result through API
and CLI read models.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import threading
import urllib.request
import zipfile
from urllib.error import HTTPError

import pytest

from bananloop.api import create_server
from bananloop.contracts import (
    AutonomyScopeV2, CapabilityPolicyV2, ExplorationCampaignSpecV2,
    ProtocolCase, ProtocolManifest,
)
from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority, EvaluatorProtocolDraftV1, EvaluatorProtocolV1,
    EvaluatorTierManifestV1, EvaluatorVerificationAdapter, IntegrationVerifierOutputV1,
    ProtocolVerifierOutputV1,
)
from bananloop.graph_store import AddNode, GraphRepository, MutateGraph
from bananloop.registry import CampaignRegistry
from bananloop.research_ledger import ResearchLedger
from bananloop.cli import cmd_v2_explore, parser
from bananloop.roles import (
    PinnedRoleExecutorV1, RoleDefinitionV1, RoleRegistryV1, TeamManifestAuthority,
)
from bananloop.trusted_evaluator import (
    TrustedEvaluatorAdapter, TrustedEvaluatorRunner, _case_protocol_digest,
)

from test_exploration_adapter import _role_registry, _source_repo


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
    researcher_roles = _role_registry(calls_per_instance=3)
    researcher_role = researcher_roles.roles[0].model_copy(update={
        "min_instances": 0,
        "capability_policy": researcher_roles.roles[0].capability_policy.model_copy(
            update={"read_paths": ("README.md", "src/optimizer/model/ranker.py")},
        ),
    })
    integration_role = RoleDefinitionV1(
        role_id="integration_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="rpdi13-test-pin",
        prompt_digest="a" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "public_task"), priority=1,
        min_instances=0, max_instances=1, calls_per_instance=3,
        tokens_per_instance=100,
    )
    protocol_verifier_role = RoleDefinitionV1(
        role_id="independent_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="rpdi13-test-pin",
        prompt_digest="c" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "public_task"), priority=2,
        min_instances=0, max_instances=1, calls_per_instance=3,
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
        priority=1, min_instances=0, max_instances=1,
        calls_per_instance=3, tokens_per_instance=500,
    )
    roles = RoleRegistryV1(
        registry_id="rpdi13-integrated-roles", version=1,
        roles=(researcher_role, integration_role, protocol_verifier_role, optimizer_role),
    )
    policy = CapabilityPolicyV2(
        tools=("read_file", "list_files", "write_artifact", "commit_candidate"),
        read_paths=("README.md", "src/optimizer/model/ranker.py"),
        write_paths=("**",), backend="LOCAL",
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
        "budget": {"max_agent_calls": 20, "max_evaluator_calls": 20,
                   "max_experiments": 5, "max_attempts": 8},
        "role_registry_digest": roles.digest(),
        "evaluation": None,
    })

    monkeypatch.setattr(
        "bananloop.v2_campaign_cli._build_spec",
        lambda *args, **kwargs: (spec, roles, policy),
    )
    registry = CampaignRegistry(repo)
    explore_command = [
        "v2", "explore", "--repo", str(repo), "--request-id", "rpdi13-integrated",
        "--goal", spec.goal, "--read-path", "README.md",
        "--read-path", "src/optimizer/model/ranker.py",
        "--optimization-write-path", "src/optimizer/model/ranker.py",
        "--max-agent-calls", "20", "--max-model-tokens", "128",
        "--max-iterations", "3", "--timeout-seconds", "60",
    ]
    preflight_args = parser().parse_args([*explore_command, "--approve-up-to-calls", "0"])
    assert preflight_args.func is not None and preflight_args.func(preflight_args) == 0
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["status"] == "READY_FOR_OWNER_APPROVAL"
    assert preflight["live_model_called"] is False
    entry, state_root, created = registry.reserve(spec.goal, "rpdi13-integrated")
    assert not created and entry.campaign_id == preflight["campaign_id"]
    database = state_root / "campaign.sqlite3"
    queue = registry.worker_queue()
    graph = GraphRepository.open(str(database), campaign_id=entry.campaign_id)
    server = create_server(str(repo), port=0, token=TOKEN, poll_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root_url = f"http://127.0.0.1:{server.server_port}/v1/campaigns"

    try:
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
quality = 0.1 if "regression candidate" in ranker else 0.8 if "accepted candidate" in ranker else 0.5
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
        protocol_artifacts = {
            "bundle.dat": bundle_bytes,
            "tier1_manifest.json": manifest_bytes,
            "tier1_input.json": input_bytes,
            "tier3_manifest.json": holdout_manifest_bytes,
            "tier3_input.json": holdout_input_bytes,
        }
        protocol_artifact_digests = {
            name: hashlib.sha256(content).hexdigest()
            for name, content in protocol_artifacts.items()
        }
        protocol_evidence_refs = tuple(
            f"artifact://sha256/{protocol_artifact_digests[name]}"
            for name in ("bundle.dat", "tier1_manifest.json", "tier1_input.json")
        )
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
                        "statement": "The matched control refutes persistence of the retrieval effect.",
                        "stance": "REFUTES", "kind": "CAUSAL_INFERENCE", "confidence": 0.4,
                        "intervention_type": "feature_representation",
                        "algorithmic_layer": "features",
                        "affected_paths": ["src/optimizer/ranker.py"],
                        "evidence_refs": [f"artifact://sha256/{artifact_digest}"],
                    }],
                }
            evidence_id = public_input["role_context"]["evidence_refs"][0]
            response = {
                "hypotheses": [
                    {
                        "id": "model-choice", "hypothesis": "An unrelated condition explains the outcome.",
                        "rationale": "The researcher prefers a fresh condition.",
                        "next_experiment": "test an unrelated condition",
                        "falsification_condition": "The unrelated condition has no effect.",
                        "expected_evidence": "A public outcome for the unrelated condition.",
                    },
                    {
                        "id": "evidence-choice",
                        "hypothesis": "The refutation is limited to the matched control; an independent condition may preserve the retrieval effect.",
                        "rationale": "This candidate tests the scope of the durable prior refutation.",
                        "next_experiment": "test a distinct retrieval condition after the matched-control refutation",
                        "falsification_condition": "The independent condition also refutes the retrieval effect.",
                        "expected_evidence": "A bounded result that narrows the refutation scope.",
                        "evidence_refs": [evidence_id],
                        "target_claim_key": "effect:retrieval",
                    },
                ],
                "selected_hypothesis_id": "model-choice",
                "artifact_text": {"observation.txt": content},
                "limitations": ["one deterministic public replication"],
            }
            if len(observed_turns) == 2:
                response["research_findings"] = [{
                    "claim_key": "effect:retrieval",
                    "statement": "A second matched-control observation refutes persistence of the retrieval effect.",
                    "stance": "REFUTES", "kind": "CAUSAL_INFERENCE", "confidence": 0.5,
                    "intervention_type": "feature_representation",
                    "algorithmic_layer": "features",
                    "affected_paths": ["src/optimizer/ranker.py"],
                    "evidence_refs": [f"artifact://sha256/{artifact_digest}"],
                }]
            else:
                response.update({
                    "artifact_base64": {
                        name: base64.b64encode(data).decode("ascii")
                        for name, data in protocol_artifacts.items()
                    },
                    "protocol_draft": {
                        "protocol": protocol.model_dump(mode="json"),
                        "evidence_refs": protocol_evidence_refs,
                    },
                    "research_findings": [{
                        "claim_key": "effect:retrieval:independent_condition",
                        "statement": "A distinct retrieval condition supports a narrower conditional effect.",
                        "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE", "confidence": 0.5,
                        "intervention_type": "feature_representation",
                        "algorithmic_layer": "features",
                        "affected_paths": ["src/optimizer/ranker.py"],
                        "evidence_refs": [f"artifact://sha256/{artifact_digest}"],
                    }],
                })
            return response

        class OfflineAuth:
            def status(self):
                return {"connected": True}

        class OfflineSIWCResearcher:
            last_usage = "UNKNOWN"
            last_request_id = "offline-integrated-researcher"

            def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer=None):
                assert approved_model == "gpt-6-luna" and timeout_seconds == 60
                self.call_observer = call_observer
                self.request_history = []

            def check_model(self):
                return None

            def __call__(self, public_input, **_pinned):
                receipt = {"request_id": self.last_request_id, "status": "COMPLETED",
                           "dispatched": True, "usage": "UNKNOWN"}
                self.request_history.append(receipt)
                if self.call_observer:
                    self.call_observer(receipt)
                return mock_researcher(public_input)

        protocol_checks = (
            "protocol_identity", "evaluator_bundle_review", "metric_contract",
            "tier_manifests", "frozen_case_scope", "no_hidden_data_dependency",
            "fair_comparison", "error_semantics",
        )

        class OfflineSIWCProtocolVerifier:
            def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer=None):
                assert approved_model == "gpt-6-luna" and timeout_seconds == 60
                self.call_observer = call_observer
                self.request_history = []

            def executor(self, manifest, instance_id):
                def dispatch(request, gateway, **_pinned):
                    assert gateway is None
                    public_review = json.dumps(request["role_context"].get("public_task", ""))
                    assert "closed deterministic evaluator case" not in public_review
                    assert "separate final holdout case" not in public_review
                    output = ProtocolVerifierOutputV1(
                        proposal_digest=request["proposal_digest"], decision="APPROVE",
                        checks=protocol_checks,
                        rationale="Independent pinned review of the public protocol bundle.",
                    ).model_dump(mode="json")
                    receipt = {"request_id": "offline-verifier", "status": "COMPLETED",
                               "dispatched": True, "usage": "UNKNOWN"}
                    self.request_history.append(receipt)
                    if self.call_observer:
                        self.call_observer(receipt)
                    return output

                return PinnedRoleExecutorV1.from_manifest(manifest, instance_id, dispatch)

        monkeypatch.setattr("bananloop.v2_campaign_cli.ChatGPTPlanAuth", OfflineAuth)
        monkeypatch.setattr(
            "bananloop.v2_campaign_cli.ChatGPTPlanResearcher", OfflineSIWCResearcher,
        )
        monkeypatch.setattr(
            "bananloop.v2_campaign_cli.ChatGPTPlanProtocolVerifier", OfflineSIWCProtocolVerifier,
        )
        explore_args = parser().parse_args([*explore_command, "--approve-up-to-calls", "4"])
        assert explore_args.func is cmd_v2_explore
        assert explore_args.func(explore_args) == 0
        exploration = json.loads(capsys.readouterr().out)
        assert exploration["iterations_this_call"] == 3
        assert len(observed_turns) == 3
        assert observed_turns[1]["role_context"]["evidence_refs"]
        assert "effect:retrieval" in observed_turns[1]["role_context"]["dev_summary"]
        assert observed_turns[1]["role_context"]["public_task"] == "run a matched control observation"
        assert "REFUTES" in observed_turns[1]["role_context"]["dev_summary"]
        assert observed_turns[2]["role_context"]["public_task"] == (
            "test a distinct retrieval condition after the matched-control refutation"
        )
        assert "REFUTES" in observed_turns[2]["role_context"]["dev_summary"]
        assert observed_turns[2]["role_context"]["evidence_refs"]
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
        refutation_ids = exploration["completed"][0]["research_evidence_ids"]
        second_refutation_ids = exploration["completed"][1]["research_evidence_ids"]
        assert len(refutation_ids) == len(second_refutation_ids) == 1

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
        refutation = next(
            item for item in public_knowledge["evidence"]
            if item["evidence_id"] in refutation_ids
        )
        second_refutation = next(
            item for item in public_knowledge["evidence"]
            if item["evidence_id"] in second_refutation_ids
        )
        assert refutation["stance"] == "REFUTES"
        assert second_refutation["stance"] == "REFUTES"
        assert current_evidence["stance"] == "SUPPORTS"
        assert current_evidence["verification_status"] == "UNVERIFIED_ROLE_FINDING"
        assert current_evidence["causal_status"] == "UNVERIFIED_INFERENCE"

        protocol_authority = EvaluatorProtocolAuthority(graph.conn)
        protocol_proposal = protocol_authority.record_completed_proposal(
            graph, queue, job_id,
        )
        assert protocol_proposal.protocol == protocol
        assert protocol_proposal.proposer_role_instance_id == job["request"]["role_binding"][
            "role_instance_id"
        ]
        parent_published = state_root / "rpdi05" / "published"
        verification = EvaluatorVerificationAdapter(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            published_artifact_root=parent_published,
        )
        verifier_job = verification.enqueue_verifier(protocol_proposal.proposal_digest)
        public_review = verifier_job["request"]["role_context"]["public_task"]
        assert "closed deterministic evaluator case" not in public_review
        assert "separate final holdout case" not in public_review
        assert input_digest not in public_review
        assert holdout_input_digest not in public_review
        manifest_authority = TeamManifestAuthority(queue.path)
        try:
            pinned_team = manifest_authority.load(
                entry.campaign_id, spec_hash=spec.approved_hash(),
                registry_digest=roles.digest(),
            )
        finally:
            manifest_authority.close()
        verifier_instance = verifier_job["request"]["role_binding"]["role_instance_id"]
        verifier_member = pinned_team.member(verifier_instance)
        assert verifier_member.role.role_id == "independent_verifier"
        from bananloop.v2_campaign_cli import _complete_pending_protocol_reviews
        review_result = _complete_pending_protocol_reviews(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            published_root=parent_published, auth=object(), model="gpt-6-luna",
            timeout_seconds=60, remaining_calls=1, call_observer=None,
        )
        assert review_result["calls_used"] == 0  # the parsed CLI already dispatched this pinned verifier
        assert review_result["pending_proposal_digests"] == []
        assert any(item["role_id"] == "independent_verifier"
                   for item in exploration["request_history"])
        receipt = verification.authority.record_completed_verifier_job(
            queue, verifier_job["job_id"],
        )
        assert receipt.decision == "APPROVE"
        assert receipt.verifier_role_instance_id == verifier_instance
        assert receipt.verifier_role_instance_id != protocol_proposal.proposer_role_instance_id

        from bananloop.contracts import PivotProposal
        pivot_ready = exploration["optimization_pivot"][0]
        assert pivot_ready["status"] == "PENDING_OWNER_PIVOT_DECISION"
        assert Path(pivot_ready["role_registry_path"]).is_file()
        proposal_row = sqlite3.connect(database).execute(
            "SELECT proposal_json FROM v2_pivot_proposals WHERE proposal_id=?",
            (pivot_ready["proposal_id"],),
        ).fetchone()
        proposal = PivotProposal.model_validate_json(proposal_row[0])
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
            if real_runner is None:
                class DeterministicRunner(TrustedEvaluatorRunner):
                    def __init__(self):
                        super().__init__(bwrap_binary="not-used-in-test")
                        self.protocol_hash = evaluator_manifest.protocol_sha256

                    def _sandbox_available(self):
                        return True

                    def _execute_isolated(
                        self, argv, *, readonly, output, candidate_snapshot_id,
                        tier, timeout,
                    ):
                        private_map = json.loads(
                            (readonly / "tier" / "input-map.json").read_text(encoding="utf-8")
                        )
                        measured_cases = [{
                            "case_id": item["case_id"], "repetition": item["repetition"],
                            "input_sha256": item["input_sha256"], "metrics": {},
                        } for item in private_map]
                        if candidate_snapshot_id == base_commit:
                            score = 0.6
                        else:
                            candidate_file = (
                                child_state / "v2_optimization" / "snapshots"
                                / child_id / candidate_snapshot_id / "src"
                                / "optimizer" / "model" / "ranker.py"
                            )
                            score = (
                                0.1 if "regression candidate" in candidate_file.read_text()
                                else 0.8
                            )
                        return json.dumps({
                            "status": "ok", "metrics": {"quality": score},
                            "protocol_sha256": self.protocol_hash, "cases": measured_cases,
                            "diagnostics": {},
                        })

                evaluator_runner = DeterministicRunner()
            else:
                evaluator_runner = real_runner
            monkeypatch.setattr(
                "bananloop.v2_optimization_cli.TrustedEvaluatorAdapter",
                lambda **kwargs: TrustedEvaluatorAdapter(**kwargs, runner=evaluator_runner),
            )
            monkeypatch.setattr(
                "bananloop.v2_optimization_cli.ChatGPTPlanAuth",
                lambda: type("MockAuth", (), {
                    "status": lambda self: {"connected": True},
                })(),
            )

            optimizer_epochs = []

            class MockOptimizer:
                def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer):
                    assert approved_model == "gpt-6-luna" and timeout_seconds == 60
                    self.call_observer = call_observer
                    self.request_history = []

                def check_model(self):
                    return None

                def __call__(self, public_input, **_pin):
                    assert set(public_input) == {
                        "protocol", "role_context", "selected_hypothesis_id", "visible_documents",
                        "writable_paths", "instruction",
                    }
                    assert public_input["selected_hypothesis_id"] == "improve-ranker"
                    assert public_input["visible_documents"]["src/optimizer/model/ranker.py"] == (
                        "baseline ranker = 0\n"
                    )
                    assert "closed deterministic evaluator case" not in json.dumps(public_input).lower()
                    receipt = {
                        "request_id": f"resp_integrated_optimizer_{len(optimizer_epochs) + 1}",
                        "status": "COMPLETED",
                        "dispatched": True, "usage": {"total_tokens": 31},
                    }
                    self.request_history.append(receipt)
                    self.call_observer(receipt)
                    optimizer_epochs.append(public_input["role_context"])
                    epoch = len(optimizer_epochs)
                    content = (
                        f"epoch {epoch} regression candidate\n" if epoch < 3
                        else f"epoch {epoch} accepted candidate\n"
                    )
                    return {
                        "hypothesis_id": "improve-ranker",
                        "rationale": (
                            f"Epoch {epoch}: "
                            + ("DEV evidence did not justify promotion; retry with a distinct candidate."
                               if epoch < 3 else "DEV and independent validation support promotion.")
                        ),
                        "changes": {"src/optimizer/model/ranker.py": content},
                    }

            class MockVerifier:
                def __init__(self, _auth, *, approved_model, timeout_seconds, call_observer):
                    assert approved_model == "gpt-6-luna" and timeout_seconds == 60
                    self.call_observer = call_observer
                    self.request_history = []

                def check_model(self):
                    return None

                def _dispatch_verifier(self, request, gateway, **_pin):
                    assert gateway is None
                    assert request["role_binding"]["role_id"] == "integration_verifier"
                    public_evidence = json.loads(request["role_context"]["public_task"])
                    assert "closed deterministic evaluator case" not in json.dumps(public_evidence).lower()
                    output = IntegrationVerifierOutputV1(
                        candidate_snapshot_id=request["candidate_snapshot_id"],
                        protocol_digest=request["protocol_digest"],
                        decision=public_evidence["decision"],
                        checks=(
                            "candidate_identity", "protocol_identity", "evaluation_receipt",
                            "budget_receipt", "artifact_integrity", "regression_policy",
                        ),
                    ).model_dump(mode="json")
                    receipt = {
                        "request_id": "resp_integrated_verifier", "status": "COMPLETED",
                        "dispatched": True, "usage": {"total_tokens": 19},
                    }
                    self.request_history.append(receipt)
                    self.call_observer(receipt)
                    return output

            monkeypatch.setattr("bananloop.v2_optimization_cli.ChatGPTPlanOptimizer", MockOptimizer)
            monkeypatch.setattr(
                "bananloop.v2_optimization_cli.ChatGPTPlanIntegrationVerifier", MockVerifier,
            )
            child_graph.mutate(MutateGraph(
                command_id="add-v2-optimizer-proposal",
                idempotency_key="add-v2-optimizer-proposal",
                expected_graph_revision=child_graph.graph_revision(),
                changes=(AddNode(
                    type="AddNode", node_id="v2-optimizer-proposal", phase="OPTIMIZATION",
                    payload={"source_snapshot_id": base_commit,
                             "hypothesis_id": "improve-ranker",
                             "hypothesis": "Improve the bounded deterministic ranker."},
                ),),
            ))

            def run_optimization(*, max_steps, approved_calls):
                optimize_args = parser().parse_args([
                    "v2", "optimize", "--repo", str(repo), "--campaign-id", child_id,
                    "--role-registry", pivot_ready["role_registry_path"],
                    "--visible-path", "src/optimizer/model/ranker.py",
                    "--write-path", "src/optimizer/model/ranker.py",
                    "--max-steps", str(max_steps), "--approve-up-to-calls", str(approved_calls),
                    "--timeout-seconds", "60",
                ])
                assert optimize_args.func(optimize_args) == 0
                return json.loads(capsys.readouterr().out)

            # Two complete failed epochs consume two optimizer and two verifier
            # calls. The third epoch is interrupted after its durable candidate
            # publication, then recovered by a fresh CLI/runtime invocation.
            first_run = run_optimization(max_steps=6, approved_calls=4)
            assert first_run["status"] == "STEP_LIMIT"
            assert [step["status"] for step in first_run["steps"]] == [
                "CANDIDATE_CREATED", "DEV_EVALUATED", "INDEPENDENT_VERIFICATION_FAIL",
                "CANDIDATE_CREATED", "DEV_EVALUATED", "INDEPENDENT_VERIFICATION_FAIL",
            ]
            assert len(optimizer_epochs) == 2
            middle_run = run_optimization(max_steps=1, approved_calls=1)
            assert middle_run["status"] == "STEP_LIMIT"
            assert [step["status"] for step in middle_run["steps"]] == ["CANDIDATE_CREATED"]
            assert len(optimizer_epochs) == 3

            # The CLI closes its queue and graph before returning; reopen the
            # durable campaign state before continuing epoch three.
            child_queue.close()
            child_graph.close()
            child_graph = GraphRepository.open(str(child_db), campaign_id=child_id)
            child_queue = registry.worker_queue()
            third_candidate = next(step["candidate_snapshot_id"] for step in middle_run["steps"]
                                   if step["status"] == "CANDIDATE_CREATED")
            assert child_graph.conn.execute(
                "SELECT COUNT(*) FROM evaluator_run_receipts "
                "WHERE campaign_id=? AND candidate_snapshot_id=? AND json_extract(receipt_json,'$.tier')='dev'",
                (child_id, third_candidate),
            ).fetchone()[0] == 0
            from bananloop.budget_ledger import BudgetLedger
            budget_before_resume = BudgetLedger(child_queue.path)
            try:
                before_resume = budget_before_resume.snapshot(child_id)
            finally:
                budget_before_resume.close()

            optimized = run_optimization(max_steps=8, approved_calls=1)
            assert optimized["status"] == "ALL_NODES_COMPLETE"
            assert optimized["live_model_called"] is True
            assert optimized["provider"] == "chatgpt_plan"
            assert optimized["provider_call_attempts"] == 1
            assert [step["status"] for step in optimized["steps"]] == [
                "DEV_EVALUATED", "INDEPENDENT_VERIFICATION_PASS",
            ]
            assert len(optimizer_epochs) == 3
            candidate = next(step for step in middle_run["steps"]
                             if step["status"] == "CANDIDATE_CREATED")
            assert optimized["steps"][-1]["champion"]["snapshot_id"] == candidate[
                "candidate_snapshot_id"
            ]
            assert child_graph.conn.execute(
                "SELECT COUNT(*) FROM evaluator_run_receipts "
                "WHERE campaign_id=? AND candidate_snapshot_id=? AND json_extract(receipt_json,'$.tier')='dev'",
                (child_id, third_candidate),
            ).fetchone()[0] == 1
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
            assert final_report["exploration_report"]["status"] == "CONVERGED_REPEATED_EXPERIMENT"
            assert len(final_report["exploration_report"]["completed_experiments"]) == 3
            assert final_report["optimization_report"]["champion"]["snapshot_id"] == candidate[
                "candidate_snapshot_id"
            ]
            assert final_report["optimization_report"]["integration_evidence"][-1]["decision"] == "PASS"
            candidate_trace = final_report["optimization_report"]["decision_trace"]
            assert len(candidate_trace) == 3
            candidate_trace.sort(key=lambda node: node["candidate_rationale"])
            assert [node["decision"] for node in candidate_trace] == [
                "REJECTED", "REJECTED", "ACCEPTED",
            ]
            assert [node["guard"] for node in candidate_trace] == [
                "REGRESSION_FAILED", "REGRESSION_FAILED", "VERIFIED_RESULT",
            ]
            assert all(node["candidate_rationale"] and node["changed_paths"]
                       and node["evidence_refs"] for node in candidate_trace)
            budget_after_resume = BudgetLedger(child_queue.path)
            try:
                after_resume = budget_after_resume.snapshot(child_id)
            finally:
                budget_after_resume.close()
            assert after_resume["agent_calls"]["spent"] - before_resume["agent_calls"]["spent"] == 1
            assert {item["tier"] for item in final_report["optimization_report"]["evaluation_receipts"]} == {
                "dev", "validation",
            }
            assert final_report["optimization_report"]["holdout"]["tier_manifest_sha256"] == holdout_manifest_digest

            # Replaying the completed campaign after another runtime restart
            # must reuse the durable final HOLDOUT receipt and spend no budget.
            calls_before_replay = len(optimizer_epochs)
            receipts_before_replay = child_graph.conn.execute(
                "SELECT COUNT(*) FROM evaluator_run_receipts WHERE campaign_id=?", (child_id,),
            ).fetchone()[0]
            budget_before_replay = BudgetLedger(child_queue.path)
            try:
                spent_before_replay = budget_before_replay.snapshot(child_id)["agent_calls"]["spent"]
            finally:
                budget_before_replay.close()
            replayed = run_optimization(max_steps=8, approved_calls=1)
            assert replayed["status"] == "ALL_NODES_COMPLETE"
            assert replayed["live_model_called"] is False
            assert len(optimizer_epochs) == calls_before_replay
            assert child_graph.conn.execute(
                "SELECT COUNT(*) FROM evaluator_run_receipts WHERE campaign_id=?", (child_id,),
            ).fetchone()[0] == receipts_before_replay
            budget_after_replay = BudgetLedger(child_queue.path)
            try:
                assert budget_after_replay.snapshot(child_id)["agent_calls"]["spent"] == spent_before_replay
            finally:
                budget_after_replay.close()
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
                          if item["node_id"] == candidate["candidate_node_id"])
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
                              if item["node_id"] == candidate["candidate_node_id"])
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
