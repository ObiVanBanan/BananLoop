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
    assert initial_model == {
        "schema": "titanic-linear-v1", "intercept": -0.5,
        "coefficients": {"sex_female": 1.0},
    }
    assert not any((candidate / f"{tier}.json").exists()
                   for tier in ("dev", "validation", "holdout"))
    assert {"dev.json", "validation.json", "holdout.json"} <= {
        path.name for path in private.iterdir()
    }
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
        EvaluatorProtocolAuthority, MetricDiscoveryProposalV1, _digest,
    )
    from bananloop.graph_store import AddNode, GraphRepository, MutateGraph
    from bananloop.migrations import create_v5_graph_schema
    from bananloop.optimization_lifecycle import OptimizationAttemptCoordinator
    from bananloop.roles import RoleDefinitionV1, RoleRegistryV1
    from bananloop.registry import CampaignRegistry
    from bananloop.trusted_evaluator import TrustedEvaluatorRunner
    from bananloop.v2_optimization_runtime import V2OptimizationRuntime
    from bananloop.worker_queue import DurableWorkerQueue

    from test_evaluator_verification import _receipt, build_full_optimization_runtime

    live_mode = os.environ.get("BANANLOOP_TITANIC_LIVE") == "1"
    state_root = tmp_path / "campaign-state"
    live_receipts = []
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

    parent_entry, parent_state, created = CampaignRegistry(source_repo).reserve(
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
                role_id="integration_verifier", adapter="codex", provider="openai",
                model="gpt-6-luna", model_version="titanic-e2e-v1",
                prompt_digest="a" * 64,
                capability_policy=CapabilityPolicyV2(
                    tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
                    network_access=False,
                ),
                context_fields=("campaign_goal", "public_task"),
                min_instances=1, max_instances=1, calls_per_instance=5,
                tokens_per_instance=500,
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
                tokens_per_instance=500,
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
                "tools": [], "read_paths": [], "write_paths": [],
                "backend": "ISOLATED", "network_access": False,
            },
        },
        "budget": {
            "max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
            "max_evaluator_calls": 30, "deadline_seconds": 3600,
            "plateau_experiments": 3,
        },
        "role_registry_digest": roles.digest(), "evaluation": None,
    })

    proposal_payload = {
        "schema_version": 1, "campaign_id": parent_campaign_id,
        "spec_hash": parent_spec.approved_hash(), "source_snapshot_id": source_snapshot,
        "source_node_id": "titanic-protocol-discovery", "source_job_id": "research-job-titanic",
        "source_fence": 1, "source_request_hash": "d" * 64,
        "source_output_digest": "e" * 64, "source_event_cursor": 1,
        "proposer_role_id": "researcher", "proposer_role_instance_id": "agent-" + "2" * 24,
        "proposer_binding_digest": "f" * 64, "protocol": protocol.model_dump(mode="json"),
        "evidence_refs": (f"artifact://sha256/{protocol.evaluator_bundle_sha256}",),
    }
    protocol_proposal = MetricDiscoveryProposalV1(
        proposal_digest=_digest(proposal_payload), **proposal_payload,
    )
    protocol_approval = _receipt(protocol_proposal)

    db_path = state_root / "campaign.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root(child_campaign_id, {
        "agent_calls": 20, "model_tokens": 12000, "evaluator_calls": 30,
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
        authority._append_proposal(protocol_proposal)
        authority._append_verified_receipt(protocol_approval)
        handoff = authority.prepare_child_handoff(
            parent_campaign_id=parent_campaign_id,
            parent_spec_hash=parent_spec.approved_hash(),
            allocation_id="alloc-titanic-e2e",
            protocol_id=protocol.protocol_id, version=protocol.version,
            protocol_digest=protocol.content_digest(),
            approval_digest=protocol_approval.approval_digest,
            published_artifact_root=published,
        )

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
                "max_evaluator_calls": 30, "deadline_seconds": 3600,
                "plateau_experiments": 3,
            },
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

        def offline_optimizer(public_input, **_pinned):
            assert public_input["visible_documents"]["data/train.csv"]
            assert public_input["writable_paths"] == ["src/model.json"]
            assert "validation" not in json.dumps(public_input).lower()
            assert "holdout" not in json.dumps(public_input).lower()
            return {
                "hypothesis_id": "titanic-child-interaction",
                "rationale": "The DEV diagnostic supports a bounded male first-class child interaction; the independent verifier will decide whether it generalizes.",
                "changes": {
                    "src/model.json": json.dumps({
                        "schema": "titanic-linear-v1", "intercept": -0.5,
                        "coefficients": {
                            "sex_female": 1.0, "male_pclass1_child": 1.5,
                        },
                    }, sort_keys=True, separators=(",", ":")),
                },
            }

        if live_mode:
            live_dispatch_count = 0

            def one_shot_live_optimizer(public_input, **pinned):
                nonlocal live_dispatch_count
                if live_dispatch_count:
                    raise RuntimeError(
                        "live smoke refuses a second optimizer dispatch; inspect persisted outcome"
                    )
                live_dispatch_count += 1
                return live_optimizer(public_input, **pinned)

            selected_optimizer = one_shot_live_optimizer
            worker_id = "titanic-live-smoke"
        else:
            selected_optimizer = offline_optimizer
            worker_id = "titanic-offline-e2e"
        try:
            result = runtime.run_campaign(
                visible_paths=["data/train.csv", "src/model.json"],
                write_paths=["src/model.json"],
                model_reply=selected_optimizer,
                integration_verifier_executor=integration_executor,
                max_steps=3, worker_id=worker_id,
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
                        "provider_usage": live_optimizer.last_usage or "UNKNOWN",
                        "provider_response": live_optimizer.last_response,
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
                "provider_usage": live_optimizer.last_usage or "UNKNOWN",
                "provider_response": live_optimizer.last_response,
                "holdout_metric": result.get("holdout", {}).get("metric"),
                "retry_policy": "one optimizer inference maximum; no automatic retry",
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            assert len(live_receipts) == 1
        assert result["status"] == "ALL_NODES_COMPLETE"
        assert result["holdout"]["status"] == "VERIFIED"
        assert result["holdout"]["tier"] == "holdout"
        assert 0.0 <= result["holdout"]["metric"]["value"] <= 1.0
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_run_receipts WHERE campaign_id=?",
            (child_campaign_id,),
        ).fetchone()[0] >= 3
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
            "token_usage": live_optimizer.last_usage if live_mode and live_optimizer.last_usage
                           else "UNKNOWN",
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
                    "provider_usage": live_optimizer.last_usage or "UNKNOWN",
                    "provider_response": live_optimizer.last_response,
                    "holdout_metric": result["holdout"]["metric"],
                    "retry_policy": "one optimizer inference maximum; no automatic retry",
                }, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )
    finally:
        if queue is not None:
            queue.close()
        if graph is not None:
            graph.close()
        elif conn is not None:
            conn.close()
