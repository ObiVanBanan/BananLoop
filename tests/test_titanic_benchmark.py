from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from benchmarks.titanic_survival.prepare import (
    FEATURES,
    MODEL_FEATURES,
    TitanicPreparationError,
    _read_arff,
    _split_groups,
    prepare,
)
from benchmarks.titanic_survival.evaluator_assets import freeze_titanic_evaluator


DATASET = Path(__file__).resolve().parents[1] / "benchmarks" / "titanic_survival" / "openml-40945.arff"


def test_titanic_benchmark_prepares_separate_grouped_private_tiers(tmp_path):
    output = tmp_path / "benchmark-state"
    result = prepare(DATASET, output, seed=2026)

    assert result["dataset"] == "OpenML Titanic 40945 v1"
    assert result["source_md5"] == "60ac7205eee0ba5045c90b3bba95b1c4"
    assert result["target"] == "survived"
    assert result["features"] == list(FEATURES)
    assert {"boat", "body", "name", "ticket", "cabin", "home.dest"} <= set(
        result["excluded_fields"]
    )
    assert result["stretch_target"] == 0.95
    assert result["target_is_guaranteed"] is False
    assert sum(item["rows"] for item in result["split"].values()) == 1309
    assert result["evaluator_tiers"]["dev"]["rows"] > 0
    assert result["evaluator_tiers"]["validation"]["rows"] > 0
    assert result["evaluator_tiers"]["holdout"]["rows"] > 0

    candidate = output / "candidate"
    private = output / "evaluator-private"
    with (candidate / "data" / "train.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == [*FEATURES, "survived"]
        training_rows = list(reader)
    assert len(training_rows) == result["split"]["train"]["rows"]
    assert "boat" not in training_rows[0] and "body" not in training_rows[0]
    initial_model = json.loads((candidate / "src" / "model.json").read_text(encoding="utf-8"))
    assert initial_model["schema"] == "titanic-linear-v1"
    assert initial_model["intercept"] == -0.5
    assert set(initial_model["coefficients"]) == set(MODEL_FEATURES)
    assert initial_model["coefficients"]["sex_female"] == 1.0
    assert all(weight == 0.0 for feature, weight in initial_model["coefficients"].items()
               if feature != "sex_female")
    assert not any((candidate / f"{tier}.json").exists()
                   for tier in ("dev", "validation", "holdout"))
    assert {"dev.json", "validation.json", "holdout.json"} <= {
        path.name for path in private.iterdir()
    }
    # Windows does not expose NTFS ACLs through chmod mode bits; pytest's
    # private root inherits the runner account ACL there. POSIX must enforce
    # owner-only permissions explicitly.
    if os.name != "nt":
        assert (private.stat().st_mode & 0o777) == 0o700
    for tier in ("dev", "validation", "holdout"):
        payload = json.loads((private / f"{tier}.json").read_text(encoding="utf-8"))
        assert set(payload) == {"rows", "labels"}
        assert len(payload["rows"]) == result["evaluator_tiers"][tier]["rows"]
        assert len(payload["labels"]) == len(payload["rows"])
        assert all(set(row) == set(FEATURES) for row in payload["rows"])

    _, rows = _read_arff(DATASET)
    first = _split_groups(rows, 2026)
    second = _split_groups(rows, 2026)
    assert first == second
    ticket_owners = {}
    for tier, tier_rows in first.items():
        for row in tier_rows:
            ticket = row["ticket"].strip()
            if ticket == "?":
                continue
            assert ticket not in ticket_owners or ticket_owners[ticket] == tier
            ticket_owners[ticket] = tier


def test_titanic_benchmark_rejects_wrong_source_and_repo_local_output(tmp_path):
    corrupted = tmp_path / "corrupted.arff"
    shutil.copyfile(DATASET, corrupted)
    corrupted.write_bytes(corrupted.read_bytes() + b"% changed\n")
    with pytest.raises(TitanicPreparationError, match="checksum"):
        prepare(corrupted, tmp_path / "state")

    repo_local_output = Path(__file__).resolve().parents[1] / "benchmarks" / "titanic_survival" / "unsafe-output"
    with pytest.raises(TitanicPreparationError, match="outside the candidate source repository"):
        prepare(DATASET, repo_local_output)
    assert not repo_local_output.exists()


def test_titanic_evaluator_runs_in_trusted_bubblewrap_on_dev_only(tmp_path):
    from bananloop.evaluator_verification import EvaluatorProtocolV1
    from bananloop.trusted_evaluator import TrustedEvaluatorRunner

    output = tmp_path / "benchmark-state"
    prepare(DATASET, output, seed=2026)
    frozen = freeze_titanic_evaluator(output, "a" * 40)
    protocol = EvaluatorProtocolV1.model_validate(frozen["protocol"])
    published = Path(frozen["published_dir"])
    tier_ref = next(item for item in protocol.tiers if item.tier == "dev")
    manifest_bytes = (published / tier_ref.manifest_sha256).read_bytes()
    from bananloop.contracts import ProtocolManifest

    manifest = ProtocolManifest.model_validate_json(manifest_bytes)
    inputs = {
        (case.case_id, case.repetition): (published / case.input_sha256).read_bytes()
        for case in manifest.cases
    }
    bundle = (published / protocol.evaluator_bundle_sha256).read_bytes()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        compile(archive.read("evaluate.py"), "titanic-evaluator", "exec")
    candidate_model = json.dumps({
        "schema": "titanic-linear-v1",
        "intercept": -0.5,
        "coefficients": {"sex_female": 1.0, "pclass_1": 0.1},
    }, sort_keys=True).encode()
    runner = TrustedEvaluatorRunner()
    if not runner._sandbox_available():
        pytest.skip("host does not permit production bubblewrap namespaces")
    result = runner.run(
        protocol=protocol,
        approval_digest="b" * 64,
        campaign_id="titanic-asset-e2e",
        spec_hash="c" * 64,
        candidate_snapshot_id="d" * 40,
        candidate_files={"src/model.json": candidate_model},
        evaluator_bundle=bundle,
        tier="dev",
        tier_manifest=manifest_bytes,
        case_inputs=inputs,
    )

    assert result.tier == "dev"
    assert result.case_count == len(manifest.cases) > 0
    private_sentinel = next(iter(inputs.values()))
    assert hashlib.sha256(private_sentinel).hexdigest() in {
        case.input_sha256 for case in manifest.cases
    }
    private_result = json.loads(result.private_result_bytes)
    accuracy = private_result["metrics"]["accuracy"]["value"]
    assert 0.0 <= accuracy <= 1.0
    assert private_result["protocol_sha256"] == frozen["protocol_sha256"]
    # Public run receipts are content-only and never contain the tier score.
    assert not hasattr(result, "metric")


def test_titanic_v2_optimization_campaign_runs_to_terminal_holdout(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    import subprocess
    from bananloop.chatgpt_plan import ChatGPTPlanAuth, SIWCError
    from bananloop.chatgpt_responses import ChatGPTPlanOptimizer, OPTIMIZER_INSTRUCTIONS
    from bananloop.budget_ledger import BudgetLedger
    from bananloop.contracts import (
        AutonomyScopeV2, CapabilityPolicyV2, ProtocolManifest,
        parse_campaign_spec_document,
    )
    from bananloop.evaluator_verification import (
        EvaluatorProtocolAuthority, EvaluatorProtocolDraftV1,
        EvaluatorVerificationAdapter, ProtocolVerifierOutputV1,
    )
    from bananloop.graph_store import AddNode, GraphRepository, MutateGraph
    from bananloop.migrations import create_v5_graph_schema
    from bananloop.optimization_lifecycle import OptimizationAttemptCoordinator
    from bananloop.roles import (
        PinnedRoleExecutorV1, RoleDefinitionV1, RoleRegistryV1, TeamManifestAuthority,
    )
    from bananloop.registry import CampaignRegistry
    from bananloop.trusted_evaluator import TrustedEvaluatorRunner
    from bananloop.v2_optimization_runtime import V2OptimizationRuntime
    from bananloop.worker_queue import DurableWorkerQueue

    from bananloop.v2_research_runtime import V2ResearchRuntime, make_researcher_invoke

    from test_evaluator_verification import build_full_optimization_runtime
    from test_v2_pivot_saga import _campaign_db

    live_mode = os.environ.get("BANANLOOP_TITANIC_LIVE") == "1"
    live_epochs = int(os.environ.get("BANANLOOP_TITANIC_LIVE_EPOCHS", "1")) if live_mode else 1
    if live_mode and live_epochs not in {1, 2}:
        pytest.fail("live smoke permits only one or two explicitly selected epochs")
    state_root = tmp_path / "campaign-state"
    live_receipts = []
    live_responses = []
    live_optimizer = None
    if live_mode:
        auth = ChatGPTPlanAuth()
        try:
            if not auth.status().get("connected"):
                raise SIWCError("SIWC account is not connected")
            live_optimizer = ChatGPTPlanOptimizer(
                auth, approved_model="gpt-6-luna", timeout_seconds=120,
                call_observer=lambda receipt: live_receipts.append(dict(receipt)),
            )
            live_optimizer.check_model()
        except Exception as exc:
            state_root.mkdir(parents=True, exist_ok=True)
            (state_root / "live-smoke-result.json").write_text(json.dumps({
                "schema": "bananloop-titanic-live-smoke-v1",
                "status": "NOT_DISPATCHED", "reason": str(exc),
                "error_type": type(exc).__name__,
                "inference_calls": 0,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            pytest.fail(f"live smoke stopped before inference: {exc}")
    # Keep all BananLoop registry state in pytest's temporary home. In live
    # mode SIWCVault has already captured the owner's real vault path above.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "test-home")

    optimizer_model_version = "siwc-responses-v1" if live_mode else "titanic-e2e-v1"
    optimizer_prompt_digest = (
        hashlib.sha256(OPTIMIZER_INSTRUCTIONS.encode("utf-8")).hexdigest()
        if live_mode else "b" * 64
    )
    prep = prepare(DATASET, state_root, seed=2026)
    source_repo = tmp_path / "candidate-source"
    shutil.copytree(state_root / "candidate", source_repo)
    subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
    subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Titanic E2E"], check=True)
    subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "titanic-e2e@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source_repo), "add", "data/train.csv", "src/model.json"], check=True)
    subprocess.run(["git", "-C", str(source_repo), "commit", "-qm", "frozen Titanic baseline"], check=True)
    source_snapshot = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    frozen = freeze_titanic_evaluator(state_root, source_snapshot)
    protocol = __import__("bananloop.evaluator_verification", fromlist=["EvaluatorProtocolV1"]).EvaluatorProtocolV1.model_validate(
        frozen["protocol"],
    )
    published = Path(frozen["published_dir"])
    dev_ref = next(tier for tier in protocol.tiers if tier.tier == "dev")
    dev_manifest = ProtocolManifest.model_validate_json(
        (published / dev_ref.manifest_sha256).read_bytes(),
    )

    registry = CampaignRegistry(source_repo)
    parent_entry, parent_state, created = registry.reserve(
        "Freeze a leakage-aware Titanic survival evaluator",
        request_id="titanic-parent-e2e", activate=False,
    )
    assert created
    parent_campaign_id = parent_entry.campaign_id
    child_campaign_id = "c-" + "2" * 32
    roles = RoleRegistryV1(
        registry_id="titanic-v2-e2e", version=1,
        roles=(
            RoleDefinitionV1(
                role_id="researcher", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="titanic-protocol-e2e-v1",
                prompt_digest="9" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=("read_file", "list_files", "write_artifact", "commit_candidate"),
                    read_paths=("data/**",), write_paths=("**",),
                    backend="LOCAL", network_access=False,
                ),
                context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                                "evidence_refs", "budget_remaining"),
                min_instances=1, max_instances=1, calls_per_instance=2,
                tokens_per_instance=200,
            ),
            RoleDefinitionV1(
                role_id="independent_verifier", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="titanic-protocol-e2e-v1",
                prompt_digest="8" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
                    network_access=False,
                ),
                context_fields=("campaign_goal", "public_task"),
                min_instances=1, max_instances=1, calls_per_instance=2,
                tokens_per_instance=200,
            ),
            RoleDefinitionV1(
                role_id="integration_verifier", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="titanic-e2e-v1",
                prompt_digest="a" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
                    network_access=False,
                ),
                context_fields=("campaign_goal", "public_task"),
                min_instances=1, max_instances=1, calls_per_instance=5,
                tokens_per_instance=300,
            ),
            RoleDefinitionV1(
                role_id="optimizer", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version=optimizer_model_version,
                prompt_digest=optimizer_prompt_digest,
                capability_policy=CapabilityPolicyV2(
                    tools=("read_file", "write_file", "commit_candidate"),
                    read_paths=("data/**", "src/**"), write_paths=("src/**",),
                    backend="LOCAL", network_access=False,
                ),
                context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                                "constraints", "evidence_refs", "dev_summary"),
                min_instances=1, max_instances=1, calls_per_instance=5,
                # Provider-side output caps are unavailable, so reserve a
                # realistic amount and settle against the observed receipt.
                tokens_per_instance=60_000,
            ),
        ),
    )
    parent_spec = parse_campaign_spec_document({
        "schema_version": 2, "mode": "EXPLORATION",
        "goal": "Freeze a leakage-aware Titanic survival evaluator",
        "source_repo": str(source_repo), "base_commit": source_snapshot,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "c" * 64, "scope_version": 1,
            "capability_policy": {
                "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
                "read_paths": ["data/**"], "write_paths": ["**"],
                "backend": "LOCAL", "network_access": False,
            },
        },
        "budget": {
            "max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
            "max_evaluator_calls": 30, "deadline_seconds": 3600,
            "plateau_experiments": 3,
        },
        "role_registry_digest": roles.digest(), "evaluation": None,
    })

    _campaign_db(parent_state / "campaign.sqlite3", parent_spec)
    parent_budget = BudgetLedger(registry.path)
    parent_budget.create_root(parent_campaign_id, {
        "agent_calls": 20, "evaluator_calls": 30, "experiments": 8, "attempts": 16,
    })
    parent_budget.close()
    parent_graph = GraphRepository.open(
        str(parent_state / "campaign.sqlite3"), campaign_id=parent_campaign_id,
    )
    parent_queue = registry.worker_queue()
    parent_handoff = None
    try:
        parent_graph.mutate(MutateGraph(
            command_id="add-titanic-protocol-discovery",
            idempotency_key="add-titanic-protocol-discovery",
            expected_graph_revision=parent_graph.graph_revision(),
            changes=(AddNode(
                type="AddNode", node_id="titanic-protocol-discovery", phase="EXPLORATION",
                payload={"source_snapshot_id": source_snapshot},
            ),),
        ))
        protocol_evidence_refs = (f"artifact://sha256/{protocol.evaluator_bundle_sha256}",)
        protected_input_digests = {
            case.input_sha256
            for tier in protocol.tiers if tier.tier in {"validation", "holdout"}
            for case in ProtocolManifest.model_validate_json(
                (published / tier.manifest_sha256).read_bytes(),
            ).cases
        }

        def protocol_researcher(public_input, **_pinned):
            assert set(public_input["visible_documents"]) == {"data/train.csv"}
            encoded_public_input = json.dumps(public_input)
            assert all(digest not in encoded_public_input for digest in protected_input_digests)
            return {
                "next_experiment": "review the frozen Titanic metric contract",
                "artifact_text": {
                    "research/protocol-rationale.txt": (
                        "Use the frozen source, grouped split, bounded features, and accuracy metric."
                    ),
                },
                "limitations": ("protocol assets were frozen by trusted Core before research dispatch",),
                "research_findings": ({
                    "claim_key": "benchmark:protocol-integrity",
                    "statement": "The public campaign context is sufficient to name the frozen accuracy contract.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
                },),
                "protocol_draft": EvaluatorProtocolDraftV1(
                    protocol=protocol, evidence_refs=protocol_evidence_refs,
                ).model_dump(mode="json"),
            }

        research = V2ResearchRuntime(
            graph=parent_graph, queue=parent_queue, campaign_id=parent_campaign_id,
            state_root=state_root, role_registry=roles,
            trusted_artifact_digests=tuple(frozen["asset_digests"]),
        ).run_campaign(
            visible_paths=["data/train.csv"],
            invoke=make_researcher_invoke(protocol_researcher), max_iterations=1,
            worker_id="titanic-protocol-researcher",
        )
        assert research["iterations_this_call"] == 1
        protocol_job_id = research["completed"][0]["job_id"]
        protocol_job_result = parent_queue.get_result(protocol_job_id)["result"]
        assert set(protocol_job_result["core_artifact_digests"]) == set(frozen["asset_digests"])
        source_completion = next(
            json.loads(row["payload_json"])
            for row in parent_graph.conn.execute(
                "SELECT payload_json FROM events WHERE event_type='EXPLORATION_COMPLETED'"
            )
            if json.loads(row["payload_json"]).get("job_id") == protocol_job_id
        )
        assert set(source_completion["artifact_digests"]) >= set(frozen["asset_digests"])
        assert not (
            set(source_completion["evidence_refs"])
            & {f"artifact://sha256/{digest}" for digest in protected_input_digests}
        )
        authority = EvaluatorProtocolAuthority(parent_graph.conn)
        protocol_proposal = authority.record_completed_proposal(
            parent_graph, parent_queue, protocol_job_id,
        )
        assert protocol_proposal.source_job_id == protocol_job_id
        assert protocol_proposal.proposer_role_id == "researcher"
        verification = EvaluatorVerificationAdapter(
            graph=parent_graph, queue=parent_queue,
            campaign_id=parent_campaign_id, published_artifact_root=published,
        )
        verifier_job = verification.enqueue_verifier(protocol_proposal.proposal_digest)
        public_review = verifier_job["request"]["role_context"]["public_task"]
        holdout_ref = next(tier for tier in protocol.tiers if tier.tier == "holdout")
        holdout_manifest = ProtocolManifest.model_validate_json(
            (published / holdout_ref.manifest_sha256).read_bytes(),
        )
        private_input_digest = holdout_manifest.cases[0].input_sha256
        assert "titanic-holdout-" not in public_review
        assert private_input_digest not in public_review
        assert "separate final holdout case" not in public_review
        team_authority = TeamManifestAuthority(parent_queue.path)
        try:
            pinned_team = team_authority.load(
                parent_campaign_id, spec_hash=parent_spec.approved_hash(),
                registry_digest=roles.digest(),
            )
        finally:
            team_authority.close()
        verifier_instance_id = verifier_job["request"]["role_binding"]["role_instance_id"]
        verifier_member = pinned_team.member(verifier_instance_id)
        assert verifier_member.role.role_id == "independent_verifier"

        def verify_titanic_protocol(request, _gateway, **_pinned):
            return ProtocolVerifierOutputV1(
                proposal_digest=request["proposal_digest"], decision="APPROVE",
                checks=(
                    "protocol_identity", "evaluator_bundle_review", "metric_contract",
                    "tier_manifests", "frozen_case_scope", "no_hidden_data_dependency",
                    "fair_comparison", "error_semantics",
                ),
                rationale="Pinned verifier reviewed the frozen public evaluator source and tier contract.",
            ).model_dump(mode="json")

        verifier_executor = PinnedRoleExecutorV1.from_manifest(
            pinned_team, verifier_instance_id, verify_titanic_protocol,
        )
        protocol_approval = verification.run_verifier_one(
            verifier_job_id=verifier_job["job_id"],
            worker_id="titanic-protocol-verifier", executor=verifier_executor,
        )
        assert protocol_approval.decision == "APPROVE"
        assert protocol_approval.verifier_role_instance_id != protocol_proposal.proposer_role_instance_id
        parent_handoff = authority.prepare_child_handoff(
            parent_campaign_id=parent_campaign_id,
            parent_spec_hash=parent_spec.approved_hash(),
            allocation_id="alloc-titanic-e2e",
            protocol_id=protocol.protocol_id, version=protocol.version,
            protocol_digest=protocol.content_digest(),
            approval_digest=protocol_approval.approval_digest,
            published_artifact_root=published,
        )
    finally:
        parent_queue.close()
        parent_graph.close()
    assert parent_handoff is not None

    db_path = state_root / "campaign.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root(child_campaign_id, {
        "agent_calls": 20, "model_tokens": 65_000, "evaluator_calls": 30,
        "experiments": 8, "attempts": 16,
    })
    ledger.close()
    conn = sqlite3.connect(db_path, isolation_level=None)
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
    conn.execute(
        "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,"
        "spec_hash TEXT NOT NULL,status TEXT NOT NULL)"
    )
    conn.commit()
    graph = None
    queue = None
    try:
        authority = EvaluatorProtocolAuthority(conn)
        handoff = parent_handoff

        scope = AutonomyScopeV2(
            version=1, allowed_paths=("data", "src"),
            max_child_depth=1, max_child_campaigns=1,
        )
        child_spec = parse_campaign_spec_document({
            "schema_version": 2, "mode": "OPTIMIZATION",
            "goal": "Improve Titanic accuracy toward the 0.95 stretch target using only frozen training features; report actual results without accessing protected evaluation tiers",
            "source_repo": str(source_repo), "base_commit": source_snapshot,
            "autonomy": {
                "mode": "SUPERVISED", "scope_digest": scope.content_digest(),
                "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
                "capability_policy": CapabilityPolicyV2(
                    tools=("read_file", "write_file", "commit_candidate"),
                    read_paths=("data/**", "src/**"), write_paths=("src/**",),
                    backend="LOCAL", network_access=False,
                ).model_dump(mode="json"),
            },
            "budget": {
                "max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
                "max_model_tokens": 65_000,
                "max_evaluator_calls": 30, "deadline_seconds": 3600,
                "plateau_experiments": 3,
            },
            "max_candidate_iterations": live_epochs if live_mode else 5,
            "role_registry_digest": roles.digest(),
            "evaluation": {
                "protocol_id": protocol.protocol_id, "version": protocol.version,
                "content_sha256": protocol.content_digest(),
                "approval_digest": protocol_approval.approval_digest,
            },
            "parent": {
                "parent_campaign_id": parent_campaign_id,
                "allocation_id": "alloc-titanic-e2e",
                "parent_spec_hash": parent_spec.approved_hash(),
                "pivot_proposal_id": "pivot-" + "3" * 24,
                "pivot_proposal_digest": "4" * 64,
                "pivot_decision_id": "decision-titanic-e2e",
                "approval_request_id": "decision-titanic-e2e",
                "protocol_handoff_digest": handoff["handoff_digest"],
            },
        })
        conn.execute(
            "INSERT INTO campaigns(id,spec_json,spec_hash,status) VALUES(1,?,?,?)",
            (json.dumps(child_spec.model_dump(mode="json"), sort_keys=True,
                        separators=(",", ":")), child_spec.approved_hash(), "ACTIVE"),
        )
        authority.install_child_handoff(
            handoff, child_campaign_id=child_campaign_id, child_spec=child_spec,
        )
        conn.commit()
        graph = GraphRepository(conn, campaign_id=child_campaign_id)
        queue = DurableWorkerQueue(db_path)
        runner = TrustedEvaluatorRunner()
        if not runner._sandbox_available():
            pytest.skip("host does not permit production bubblewrap namespaces")
        coordinator, trusted, integration_executor = build_full_optimization_runtime(
            graph=graph, queue=queue, campaign_id=child_campaign_id,
            state_root=state_root, source_snapshot_id=source_snapshot,
            candidate_snapshot_id=source_snapshot, protocol=protocol,
            manifest=dev_manifest, registry=roles, runner=runner,
            available_model_tokens=65_000,
        )
        graph.mutate(MutateGraph(
            command_id="add-titanic-optimization-node",
            idempotency_key="add-titanic-optimization-node",
            expected_graph_revision=0,
            changes=(AddNode(
                type="AddNode", node_id="titanic-model-search", phase="OPTIMIZATION",
                payload={
                    "source_snapshot_id": source_snapshot,
                    "hypothesis_id": "titanic-child-interaction",
                    "hypothesis": "The female-only baseline misses some young male passengers; add a bounded child interaction.",
                    "scheduler": {"priority": 5},
                },
            ),),
        ))
        runtime = V2OptimizationRuntime(
            graph=graph, queue=queue, campaign_id=child_campaign_id,
            state_root=state_root, role_registry=roles,
            trusted_evaluator=trusted,
            integration_verifier=coordinator.integration_verifier,
        )

        def assert_optimizer_input_is_candidate_only(public_input):
            assert set(public_input["visible_documents"]) == {
                "data/train.csv", "src/model.json",
            }
            assert public_input["writable_paths"] == ["src/model.json"]
            assert "validation" not in json.dumps(public_input).lower()
            assert "holdout" not in json.dumps(public_input).lower()

        offline_dispatch_count = 0

        def offline_optimizer(public_input, **_pinned):
            nonlocal offline_dispatch_count
            offline_dispatch_count += 1
            assert_optimizer_input_is_candidate_only(public_input)
            assert public_input["visible_documents"]["data/train.csv"]
            if offline_dispatch_count == 1:
                return {
                    "hypothesis_id": "titanic-child-interaction",
                    "rationale": "Deliberately poor candidate to verify rejection and research-memory handoff.",
                    "changes": {
                        "src/model.json": json.dumps({
                            "schema": "titanic-linear-v1", "intercept": 100.0,
                            "coefficients": {"sex_female": 0.0},
                        }, sort_keys=True, separators=(",", ":")),
                    },
                    "limitations": [
                        "This candidate intentionally predicts one class for every passenger."
                    ],
                }
            if offline_dispatch_count == 2:
                assert "previous candidate did not pass independent verification" in json.dumps(
                    public_input["role_context"], ensure_ascii=False,
                ).lower()
            else:
                dev_summary = public_input["role_context"].get("dev_summary", "")
                assert "public_dev_metric" in str(dev_summary)
                assert '"only_public_dev_evidence":true' in str(dev_summary)
            return {
                "hypothesis_id": "titanic-child-interaction",
                "rationale": "The DEV diagnostic supports a bounded male first-class child interaction; the independent verifier will decide whether it generalizes.",
                "changes": {
                    "src/model.json": json.dumps({
                        "schema": "titanic-linear-v1",
                        "intercept": -0.5 + 0.05 * (offline_dispatch_count - 2),
                        "coefficients": {
                            "sex_female": 1.0,
                            "male_pclass1_child": 1.5 + 0.05 * (offline_dispatch_count - 2),
                        },
                    }, sort_keys=True, separators=(",", ":")),
                },
            }

        if live_mode:
            live_dispatch_count = 0
            forced_first_verifier_rejection = False

            def one_shot_live_optimizer(public_input, **pinned):
                nonlocal live_dispatch_count
                assert_optimizer_input_is_candidate_only(public_input)
                if live_dispatch_count >= live_epochs:
                    raise RuntimeError(
                        "live smoke reached its explicit optimizer inference ceiling"
                    )
                dispatch_index = live_dispatch_count
                context_json = json.dumps(public_input["role_context"], ensure_ascii=False).lower()
                if dispatch_index == 1:
                    if "previous candidate did not pass independent verification" not in context_json:
                        raise AssertionError("second live epoch lacks persisted rejection evidence")
                    public_input = dict(public_input)
                    public_input["instruction"] += (
                        " The prior candidate was rejected by independent verification. For this "
                        "bounded diagnostic, return the known DEV-improving model configuration "
                        'exactly as complete JSON text: {"schema":"titanic-linear-v1",'
                        '"intercept":-0.5,"coefficients":{"sex_female":1.0,'
                        '"male_pclass1_child":1.5}}. Keep the selected hypothesis id unchanged.'
                    )
                live_dispatch_count += 1
                try:
                    response = live_optimizer(public_input, **pinned)
                    live_responses.append(response)
                    return response
                finally:
                    # make_optimizer_invoke reads usage from the callable
                    # that it dispatched, so preserve the SIWC receipt on
                    # this one-shot guard as well.
                    one_shot_live_optimizer.last_usage = live_optimizer.last_usage

            one_shot_live_optimizer.last_usage = None

            selected_optimizer = one_shot_live_optimizer
            worker_id = "titanic-live-smoke"
            selected_integration_executor = integration_executor
            if live_epochs == 2:
                def reject_first_optimizer_candidate(request, gateway, **_pinned):
                    nonlocal forced_first_verifier_rejection
                    output = integration_executor.dispatch(request, gateway)
                    # Inject one deterministic negative verifier result only
                    # when the first real optimizer candidate reaches this
                    # separate review stage. Keep its actual proposal intact.
                    if live_dispatch_count == 1 and not forced_first_verifier_rejection:
                        output = dict(output)
                        output["decision"] = "FAIL"
                        forced_first_verifier_rejection = True
                    return output

                selected_integration_executor = PinnedRoleExecutorV1(
                    manifest_digest=integration_executor.manifest_digest,
                    member=integration_executor.member,
                    _invoke=reject_first_optimizer_candidate,
                )
        else:
            selected_optimizer = offline_optimizer
            worker_id = "titanic-offline-e2e"
            selected_integration_executor = integration_executor
        try:
            result = runtime.run_campaign(
                visible_paths=["data/train.csv", "src/model.json"],
                write_paths=["src/model.json"],
                model_reply=selected_optimizer,
                integration_verifier_executor=selected_integration_executor,
                max_steps=(3 * live_epochs) if live_mode else 20,
                worker_id=worker_id,
            )
        except Exception as exc:
            if live_mode:
                (state_root / "live-smoke-result.json").write_text(
                    json.dumps({
                        "schema": "bananloop-titanic-live-smoke-v1",
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                        "provider_request_receipts": live_receipts,
                        "provider_usage": [r.get("usage", "UNKNOWN") for r in live_receipts],
                        "provider_responses": live_responses,
                        "forced_first_verifier_rejection": forced_first_verifier_rejection,
                        "retry_policy": "no automatic or test-level retry",
                    }, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                )
            raise
        if live_mode:
            (state_root / "live-smoke-result.json").write_text(json.dumps({
                "schema": "bananloop-titanic-live-smoke-v1",
                "status": "RETURNED",
                "campaign_status": result.get("status"),
                "provider_request_receipts": live_receipts,
                "provider_usage": [r.get("usage", "UNKNOWN") for r in live_receipts],
                "provider_responses": live_responses,
                "forced_first_verifier_rejection": forced_first_verifier_rejection,
                "holdout_metric": result.get("holdout", {}).get("metric"),
                "retry_policy": f"{live_epochs} optimizer inference maximum; no automatic retry",
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            assert len(live_receipts) == live_epochs
        assert result["status"] == "ALL_NODES_COMPLETE"
        if not live_mode:
            assert offline_dispatch_count == 5
            assert sum(item.get("status") == "CANDIDATE_CREATED"
                       for item in result["steps"]) == 5
            assert sum(item.get("status") == "INDEPENDENT_VERIFICATION_PASS"
                       for item in result["steps"]) == 4
        assert result["holdout"]["status"] == "VERIFIED"
        assert result["holdout"]["tier"] == "holdout"
        assert 0.0 <= result["holdout"]["metric"]["value"] <= 1.0
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_run_receipts WHERE campaign_id=?",
            (child_campaign_id,),
        ).fetchone()[0] >= 3
        optimization_jobs = graph.conn.execute(
            "SELECT r.response_json FROM worker_results r JOIN worker_jobs j USING(job_id) "
            "WHERE j.campaign_id=? AND j.resource='agent_calls' AND j.job_id LIKE 'opt-%' "
            "ORDER BY j.created_at ASC",
            (child_campaign_id,),
        ).fetchall()
        assert optimization_jobs
        optimization_results = [json.loads(row["response_json"]) for row in optimization_jobs]
        optimization_result = optimization_results[-1]
        actual_usages = [item.get("actual_usage", {}).get("model_tokens")
                         for item in optimization_results]
        assert all("model_tokens" in item.get("actual_usage", {}) for item in optimization_results)
        if live_mode:
            assert len(optimization_results) == live_epochs
            assert all(isinstance(tokens, int) and 0 < tokens <= 20_000
                       for tokens in actual_usages)
            total_actual_tokens = sum(actual_usages)
            assert total_actual_tokens <= 65_000
            assert all(item["budget_breached_by_resource"]["model_tokens"] is False
                       for item in optimization_results)
            token_account = graph.conn.execute(
                "SELECT cap,spent,unknown FROM budget_accounts "
                "WHERE campaign_id=? AND resource='model_tokens'",
                (child_campaign_id,),
            ).fetchone()
            assert token_account is not None
            assert (token_account["cap"], token_account["spent"], token_account["unknown"]) == (
                65_000, total_actual_tokens, 0,
            )
        report = json.loads(Path(result["final_report_path"]).read_text(encoding="utf-8"))
        assert report["optimization_report"]["holdout"]["metric"]["metric_id"] == "accuracy"
        assert report["optimization_report"]["champion"]["snapshot_id"]
        assert not any("holdout" in ref.lower() for node in report["optimization_report"]["nodes"]
                       for ref in node["evidence_refs"])
        benchmark_result = {
            "schema": "bananloop-titanic-mini-benchmark-result-v1",
            "dataset_md5": prep["source_md5"],
            "seed": prep["seed"],
            "source_snapshot_id": source_snapshot,
            "campaign_id": child_campaign_id,
            "status": result["status"],
            "holdout": result["holdout"],
            "champion_snapshot_id": report["optimization_report"]["champion"]["snapshot_id"],
            "final_report_path": result["final_report_path"],
            "live_model_calls": len(live_receipts),
            "provider_request_receipts": live_receipts,
            "token_usage": [r.get("usage", "UNKNOWN") for r in live_receipts]
                           if live_mode else "UNKNOWN",
        }
        (state_root / "benchmark-result.json").write_text(
            json.dumps(benchmark_result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        if live_mode:
            (state_root / "live-smoke-result.json").write_text(
                json.dumps({
                    "schema": "bananloop-titanic-live-smoke-v1",
                    "status": "COMPLETED",
                    "campaign_status": result["status"],
                    "provider_request_receipts": live_receipts,
                    "provider_usage": [r.get("usage", "UNKNOWN") for r in live_receipts],
                    "provider_responses": live_responses,
                    "forced_first_verifier_rejection": forced_first_verifier_rejection,
                    "holdout_metric": result["holdout"]["metric"],
                    "budget_actual_usage": {
                        "model_tokens": sum(actual_usages),
                        "agent_calls": len(actual_usages),
                    },
                    "budget_breached": optimization_result.get("budget_breached"),
                    "retry_policy": f"{live_epochs} optimizer inference maximum; no automatic retry",
                }, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )
    finally:
        if queue is not None:
            queue.close()
        if graph is not None:
            graph.close()
        elif conn is not None:
            conn.close()
