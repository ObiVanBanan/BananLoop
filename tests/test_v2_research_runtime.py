"""Offline RPDI-13R integration: real v2 storage, queue, gateway, and ledger.

No subscription, Codex CLI or external LLM is invoked in these tests.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.contracts import ExplorationCampaignSpecV2
from bananloop.exploration import EvidenceAdapterError
from bananloop.graph_store import GraphRepository
from bananloop.research_ledger import ResearchLedger
from bananloop.v2_research_runtime import (
    ResearchHypothesisV1, ResearcherReplyV1, V2ResearchRuntime, V2RuntimeError,
    make_researcher_invoke as _make_researcher_invoke,
)
from bananloop.worker_queue import DurableWorkerQueue, WorkerQueueError

from test_exploration_adapter import _graph, _pinned_executor, _role_registry, _source_repo


def test_structured_next_experiment_is_canonicalized_without_losing_fields():
    structured = {"experiment_id": "primary_association", "question": "test the association"}
    expected = json.dumps(structured, sort_keys=True, separators=(",", ":"))
    hypothesis = ResearchHypothesisV1.model_validate({
        "id": "candidate_a", "hypothesis": "A bounded association exists.",
        "rationale": "The columns measure the target attributes.",
        "next_experiment": structured,
        "falsification_condition": "No association appears.",
        "expected_evidence": "A signed rank estimate.",
    })
    reply = ResearcherReplyV1.model_validate({"next_experiment": structured})
    assert hypothesis.next_experiment == expected
    assert reply.next_experiment == expected

    with pytest.raises(ValidationError):
        ResearcherReplyV1.model_validate({"next_experiment": 42})


def make_researcher_invoke(model_reply):
    """Provide protocol-compliant mock findings and citations for ordinary fixtures."""
    def compliant_reply(public_input, **pin):
        raw = model_reply(public_input, **pin)
        if not isinstance(raw, dict) or "strategy_change_request" in raw:
            return raw
        raw = dict(raw)
        role_context = public_input.get("role_context", {})
        refs = list(role_context.get("evidence_refs", ()))
        summary = str(role_context.get("dev_summary") or "")
        claim = summary.split(" [", 1)[0].splitlines()[0] if summary else "test:mock-memory"
        if refs:
            if raw.get("hypotheses"):
                candidates = [dict(item) for item in raw["hypotheses"]]
                if not any(item.get("evidence_refs") for item in candidates):
                    candidates[0]["evidence_refs"] = [refs[0]]
                    candidates[0]["target_claim_key"] = claim
                raw["hypotheses"] = candidates
            else:
                task = raw.pop("next_experiment", "test an independent follow-up")
                raw["hypotheses"] = [
                    {
                        "id": "memory_followup", "hypothesis": "Test the prior finding with a follow-up.",
                        "rationale": "This fixture follows the available public evidence.",
                        "next_experiment": task,
                        "falsification_condition": "The predicted observation is absent.",
                        "expected_evidence": "A bounded public follow-up observation.",
                        "evidence_refs": [refs[0]], "target_claim_key": claim,
                    },
                    {
                        "id": "independent_alternative", "hypothesis": "Test an alternative condition.",
                        "rationale": "This is an independent mock candidate.",
                        "next_experiment": task + " under an alternative condition",
                        "falsification_condition": "The alternative prediction is absent.",
                        "expected_evidence": "A bounded alternative observation.",
                    },
                ]
                raw["selected_hypothesis_id"] = "memory_followup"
        if not raw.get("research_findings"):
            if not raw.get("artifact_text"):
                raw["artifact_text"] = {"test-finding.txt": "Mock evidence; no outcome is asserted."}
            raw["research_findings"] = [{
                "claim_key": "test:mock-output",
                "statement": "Mock response records an unverified public observation; no outcome is asserted.",
                "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
            }]
        return raw

    return _make_researcher_invoke(compliant_reply)


def _runtime(tmp_path, *, max_agent_calls=3, ledger_agent_calls=None,
             role_calls_per_instance=2):
    source = tmp_path / "source"
    _source_repo(source)
    roles = _role_registry(calls_per_instance=role_calls_per_instance)
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


def test_two_real_exploration_nodes_use_refutation_memory_and_preserve_pins(tmp_path):
    runtime, graph, queue = _runtime(
        tmp_path, max_agent_calls=4, role_calls_per_instance=4,
    )
    observed = []

    def model_reply(public_input, **pin):
        observed.append(public_input)
        assert pin == {
            "provider": "openai", "model": "gpt-6-luna",
            "model_version": "test-pin-v1", "prompt_digest": "c" * 64,
            "max_model_tokens": 50,
        }
        assert set(public_input) == {
            "protocol", "role_context", "visible_documents", "instruction",
            "available_research_memory",
        }
        assert public_input["visible_documents"] == {"README.md": "frozen source\n"}
        assert "source_repo" not in public_input
        assert "capability_grant" not in public_input
        text = f"hypothesis-{len(observed)}"
        sha = hashlib.sha256(text.encode()).hexdigest()
        first_turn = len(observed) == 1
        if first_turn:
            hypotheses = [
                {
                    "id": "matched-control", "hypothesis": "The effect survives a matched control.",
                    "rationale": "A control can falsify persistence of the claimed mechanism.",
                    "next_experiment": "run a matched control",
                    "falsification_condition": "The matched control produces the same outcome.",
                    "expected_evidence": "A controlled observation under matched conditions.",
                },
                {
                    "id": "unrelated-check", "hypothesis": "A separate condition explains the outcome.",
                    "rationale": "An alternate condition can test a different explanation.",
                    "next_experiment": "test an unrelated condition",
                    "falsification_condition": "The unrelated condition has no effect.",
                    "expected_evidence": "A public outcome for the alternate condition.",
                },
            ]
            selected = "matched-control"
            next_experiment = "run a matched control"
        else:
            prior_memory = public_input["available_research_memory"][0]
            prior_evidence = prior_memory["evidence_ids"][0]
            assert "refutes persistence" in public_input["role_context"]["dev_summary"]
            hypotheses = [
                {
                    "id": "model-choice", "hypothesis": "An unrelated condition explains the outcome.",
                    "rationale": "The researcher proposes a fresh direction.",
                    "next_experiment": "test an unrelated condition",
                    "falsification_condition": "The unrelated condition has no effect.",
                    "expected_evidence": "A public outcome for the alternate condition.",
                },
                {
                    "id": "memory-choice", "hypothesis": "A narrowed mechanism responds in an independent condition.",
                    "rationale": "Follow up the refutation by testing a distinct, narrower condition.",
                    "next_experiment": "test an independent condition after the refutation",
                    "falsification_condition": "The narrowed mechanism does not appear under the independent condition.",
                    "expected_evidence": "An independent observation distinguishing the narrowed mechanism.",
                    "evidence_refs": [prior_evidence],
                    "target_claim_key": prior_memory["claim_key"],
                },
            ]
            # Core should overrule this ungrounded preference using the cited refutation.
            selected = "model-choice"
            next_experiment = "test an unrelated condition"
        return {
            "hypotheses": hypotheses,
            "selected_hypothesis_id": selected,
            "next_experiment": next_experiment,
            "artifact_text": {"observation.txt": text},
            "research_findings": [{
                "claim_key": "mechanism:alpha",
                "statement": (
                    "Unverified matched control refutes persistence of mechanism alpha."
                    if first_turn else
                    "Unverified independent condition supports a narrowed mechanism alpha."
                ),
                "stance": "REFUTES" if first_turn else "SUPPORTS",
                "kind": "CAUSAL_INFERENCE",
                "confidence": 0.4,
                "evidence_refs": [f"artifact://sha256/{sha}"],
            }],
        }

    try:
        campaign_result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(model_reply),
            max_iterations=3,
        )
        assert campaign_result["status"] == "CONVERGED_REPEATED_EXPERIMENT"
        assert campaign_result["iterations_this_call"] == 3
        report = json.loads(Path(campaign_result["report_path"]).read_text())
        assert report["status"] == "CONVERGED_REPEATED_EXPERIMENT"
        assert len(report["completed_experiments"]) == 3
        assert "No verified scientific conclusion" in report["scientific_conclusion"]
        one, two, three = campaign_result["completed"]
        assert all(item["status"] == "EXPLORATION_COMPLETED" for item in (one, two, three))
        assert one["profile"] == "FULL"
        assert len(one["research_evidence_ids"]) == 1
        assert queue.get_job(one["job_id"])["state"] == "COMPLETED"
        with pytest.raises(V2RuntimeError, match="not a schedulable"):
            runtime.run_one(
                node_id=one["node_id"], visible_paths=["README.md"],
                invoke=make_researcher_invoke(model_reply),
            )
        assert len(observed) == 3  # replay cannot create another model call

        assert len(observed) == 3
        assert "mechanism:alpha" in observed[1]["role_context"]["dev_summary"]
        assert "REFUTES" in observed[1]["role_context"]["dev_summary"]
        assert "refutes persistence" in observed[1]["role_context"]["dev_summary"]
        assert observed[1]["role_context"]["public_task"] == "run a matched control"
        assert observed[2]["role_context"]["public_task"] == (
            "test an independent condition after the refutation"
        )
        assert "REFUTES" in observed[2]["role_context"]["dev_summary"]
        assert observed[1]["role_context"]["budget_remaining"]["agent_calls"] == 3
        projection = ResearchLedger(graph.conn).project(
            graph=graph, queue=queue,
            campaign_ids=("campaign-test",),
            source_snapshot_id=queue.get_job(one["job_id"])["request"]["base_commit"],
            protocol_digest=ResearchLedger.protocol_digest_for_binding(
                queue.get_job(one["job_id"])["request"]["role_binding"],
            ),
        )
        assert len(projection.evidence) == 3
        assert any(item.stance == "REFUTES" for item in projection.evidence)
        assert projection.causal_memory[0].status == "CONTRADICTED"
        assert len(projection.contradictions) == 1
        assert all(item.verification_status == "UNVERIFIED_ROLE_FINDING" for item in projection.evidence)
    finally:
        queue.close()
        graph.close()


def test_research_lane_stops_when_worker_proposes_protocol_for_independent_review(tmp_path, monkeypatch):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=4)
    calls = []
    monkeypatch.setattr(queue, "get_result", lambda _job_id: {
        "result": {"protocol_draft": {"protocol_id": "frozen-by-core"}},
    })

    def completed_with_protocol(*, node_id, **_kwargs):
        calls.append(node_id)
        return {"job_id": "completed-proposal-job", "status": "EXPLORATION_COMPLETED"}

    monkeypatch.setattr(runtime, "run_one", completed_with_protocol)
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=lambda *_a, **_k: {}, max_iterations=3,
        )
        assert result["status"] == "PROTOCOL_REVIEW_PENDING"
        assert result["iterations_this_call"] == 1
        assert len(calls) == 1
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


def test_malformed_dispatched_response_is_durable_but_never_published(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []

    def malformed_reply(*_args, **_kwargs):
        calls.append("dispatched")
        return {
            "next_experiment": "repeat the observation",
            "score": 1.0,
            "artifact_text": {"observation.txt": "must not be published"},
        }

    try:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            runtime.run_campaign(
                visible_paths=["README.md"],
                invoke=make_researcher_invoke(malformed_reply),
                max_iterations=1,
            )

        assert calls == ["dispatched"]
        job = queue.get_job_for_action("campaign-test", "exploration:explore")
        assert job is not None
        assert job["state"] == "UNKNOWN"
        with pytest.raises(WorkerQueueError, match="no committed result"):
            queue.get_result(job["job_id"])
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] != "COMPLETED"
        assert ResearchLedger(graph.conn).project(
            graph=graph, queue=queue,
            campaign_ids=("campaign-test",),
            source_snapshot_id=job["request"]["base_commit"],
            protocol_digest=ResearchLedger.protocol_digest_for_binding(
                job["request"]["role_binding"],
            ),
        ).evidence == ()
        # The failed dispatch remains in durable accounting; it is not
        # mistaken for an unused call and silently dispatched a second time.
        budget = BudgetLedger(queue.path)
        try:
            agent_calls = budget.snapshot("campaign-test")["agent_calls"]
        finally:
            budget.close()
        assert agent_calls["spent"] + agent_calls["unknown"] >= 1
    finally:
        queue.close()
        graph.close()


def test_malformed_binary_artifact_is_unknown_spend_and_never_published(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []

    def malformed_artifact(*_args, **_kwargs):
        calls.append("dispatched")
        return {
            "next_experiment": "repeat with a valid public artifact",
            "artifact_base64": {"payload.bin": "%%%not-base64%%%"},
        }

    try:
        with pytest.raises(V2RuntimeError, match="base64 artifact is malformed"):
            runtime.run_campaign(
                visible_paths=["README.md"],
                invoke=make_researcher_invoke(malformed_artifact),
                max_iterations=1,
            )
        assert calls == ["dispatched"]
        job = queue.get_job_for_action("campaign-test", "exploration:explore")
        assert job is not None and job["state"] == "UNKNOWN"
        assert not list((tmp_path / "rpdi05" / "published").glob("*"))
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] != "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_expired_dispatched_job_is_not_redriven_after_runtime_restart(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []
    recovered_queue = None
    recovered_graph = None
    try:
        job = runtime.adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        dispatch_time = time.time()
        claim = queue.claim(
            job["job_id"], "crashed-worker", lease_seconds=1, now=dispatch_time,
        )
        queue.mark_dispatched(
            job["job_id"], "crashed-worker", claim["fence"], now=dispatch_time + 0.25,
        )
        assert queue.expire(now=dispatch_time + 2) == {"released": 0, "unknown": 1}

        queue_path = queue.path
        queue.close()
        recovered_queue = DurableWorkerQueue(queue_path)
        recovered_graph = GraphRepository.open(
            str(tmp_path / "campaign.sqlite3"), campaign_id="campaign-test",
        )
        recovered = V2ResearchRuntime(
            graph=recovered_graph, queue=recovered_queue, campaign_id="campaign-test",
            state_root=tmp_path, role_registry=runtime.role_registry,
        )
        with pytest.raises(V2RuntimeError, match="UNKNOWN; uncertain work"):
            recovered.run_campaign(
                visible_paths=["README.md"],
                invoke=make_researcher_invoke(
                    lambda *_args, **_kwargs: calls.append("must-not-dispatch"),
                ),
                max_iterations=1,
            )

        assert calls == []
        assert recovered_queue.get_job(job["job_id"])["state"] == "UNKNOWN"
        assert recovered_graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] != "COMPLETED"
        ledger = BudgetLedger(recovered_queue.path)
        try:
            assert ledger.snapshot("campaign-test")["agent_calls"]["unknown"] == 1
        finally:
            ledger.close()
    finally:
        if recovered_queue is not None:
            recovered_queue.close()
        else:
            queue.close()
        if recovered_graph is not None:
            recovered_graph.close()
        graph.close()


def test_researcher_cannot_complete_without_a_ledger_finding(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        with pytest.raises(V2RuntimeError, match="at least one evidence-linked finding"):
            runtime.run_one(
                node_id="explore", visible_paths=["README.md"],
                invoke=_make_researcher_invoke(lambda *_args, **_kwargs: {
                    "next_experiment": "test a controlled follow-up",
                    "artifact_text": {"proposal.txt": "A proposal without any recorded finding."},
                }),
            )
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] != "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_later_portfolio_must_use_available_research_memory(tmp_path):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=3)
    observed = []
    calls = 0

    def forgetful_model(public_input, **_pin):
        nonlocal calls
        calls += 1
        observed.append(public_input)
        if calls == 1:
            text = "The source proposes a test, but no result is reported."
            digest = hashlib.sha256(text.encode()).hexdigest()
            return {
                "next_experiment": "define and run a bounded follow-up",
                "artifact_text": {"observation.txt": text},
                "research_findings": [{
                    "claim_key": "source:test-status",
                    "statement": "The source proposes a test and reports no outcome; it remains unexecuted.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.9,
                    "evidence_refs": [f"artifact://sha256/{digest}"],
                }],
            }
        return {
            "hypotheses": [{
                "id": "forgot_memory_a", "hypothesis": "Test one alternative.",
                "rationale": "Distinct testable candidate.", "next_experiment": "test alternative A",
                "falsification_condition": "The predicted change is absent.",
                "expected_evidence": "A bounded public observation.",
            }, {
                "id": "forgot_memory_b", "hypothesis": "Test another alternative.",
                "rationale": "Another distinct candidate.", "next_experiment": "test alternative B",
                "falsification_condition": "The second prediction is absent.",
                "expected_evidence": "A bounded public observation.",
            }],
            "selected_hypothesis_id": "forgot_memory_a",
            "artifact_text": {"observation.txt": "The second response omitted prior references."},
            "research_findings": [{
                "claim_key": "source:follow-up", "statement": "Second unverified report.",
                "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.1,
            }],
        }

    try:
        with pytest.raises((V2RuntimeError, EvidenceAdapterError), match="must cite available Research Memory"):
            runtime.run_campaign(
                visible_paths=["README.md"],
                invoke=_make_researcher_invoke(forgetful_model), max_iterations=2,
            )
        assert len(observed) == 2
        assert observed[1]["role_context"]["evidence_refs"]
        assert "source:test-status" in observed[1]["role_context"]["dev_summary"]
        memory = observed[1]["available_research_memory"]
        assert len(memory) == 1
        assert memory[0]["claim_key"] == "source:test-status"
        assert len(memory[0]["evidence_ids"]) == 1
        assert "Do not use artifact:// references as evidence_ids" in observed[1]["instruction"]
        assert graph.conn.execute("SELECT COUNT(*) FROM research_evidence").fetchone()[0] >= 1
    finally:
        queue.close()
        graph.close()


def test_registered_exploration_can_continue_with_unanchored_memory_fallback(tmp_path):
    runtime, graph, queue = _runtime(
        tmp_path, max_agent_calls=2, role_calls_per_instance=2,
    )
    catalog = (
        {"id": "primary_association", "description": "Estimate the primary association."},
        {"id": "permutation_test", "description": "Run a permutation test."},
        {"id": "split_half_robustness", "description": "Compare seeded half samples."},
    )
    executed = []
    responses = 0

    def execute(experiment_id, _context):
        executed.append(experiment_id)
        return {"status": "COMPLETED", "summary": f"Measured public result for {experiment_id}.",
                "stance": "UNCERTAIN", "confidence": 0.9}

    def model_reply(public_input, **_pin):
        nonlocal responses
        responses += 1
        if responses == 1:
            assert public_input["available_research_memory"] == []
            candidates = (("initial_primary", "primary_association"),
                          ("initial_permutation", "permutation_test"))
            selected_id = "initial_primary"
        else:
            assert public_input["available_research_memory"]
            candidates = (("followup_permutation", "permutation_test"),
                          ("followup_split", "split_half_robustness"))
            selected_id = "followup_permutation"
        return {
            "hypotheses": [{
                "id": candidate_id,
                "hypothesis": f"Test the registered {experiment_id} analysis.",
                "rationale": "Use a distinct bounded public diagnostic.",
                "next_experiment": f"Run {experiment_id} and report its measured statistic.",
                "falsification_condition": "The registered statistic does not meet its criterion.",
                "expected_evidence": "A public DEV result record.",
                "experiment_id": experiment_id,
            } for candidate_id, experiment_id in candidates],
            "selected_hypothesis_id": selected_id,
            "research_findings": [{
                "claim_key": f"source:proposal-{responses}",
                "statement": "A bounded public diagnostic is proposed.",
                "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.2,
            }],
        }

    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"],
            invoke=_make_researcher_invoke(
                model_reply, experiment_executor=execute, experiment_catalog=catalog,
            ),
            max_iterations=2,
        )
        assert result["iterations_this_call"] == 2
        assert executed == ["primary_association", "permutation_test"]
        portfolios = list(Path(runtime.state_root).rglob("hypothesis-portfolio.json"))
        assert len(portfolios) == 2
        later = json.loads(max(portfolios, key=lambda path: path.stat().st_mtime).read_text())
        assert later["core_selection"]["method"] == "CORE_MEMORY_AWARE_EXECUTABLE_HYPOTHESIS"
        selected = next(item for item in later["candidates"] if item["id"] == "followup_permutation")
        assert selected["core_evidence_validation"] == "UNANCHORED"
        assert selected["evidence_refs"] == []
        assert selected["target_claim_key"] is None
        completed_nodes = [
            json.loads(row[0]) for row in graph.conn.execute(
                "SELECT payload_json FROM research_nodes WHERE phase='CONSOLIDATION'"
            )
        ]
        assert any(
            any("remains unanchored" in item for item in node["completion_evidence"]["limitations"])
            for node in completed_nodes
        )
    finally:
        queue.close()
        graph.close()


def test_hypothesis_portfolio_is_selected_and_persisted_as_evidence(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    hypotheses = [
        {
            "id": "h1", "hypothesis": "vary the public input size",
            "rationale": "Tests sensitivity to scale.",
            "next_experiment": "vary the public input size",
            "falsification_condition": "The result is invariant across sizes.",
            "expected_evidence": "A measured output change across sizes.",
        },
        {
            "id": "h2", "hypothesis": "hold input size fixed and vary ordering",
            "rationale": "Tests sensitivity to order independently.",
            "next_experiment": "hold input size fixed and vary ordering",
            "falsification_condition": "The result is invariant across orderings.",
            "expected_evidence": "A measured output change across orderings.",
        },
    ]
    try:
        result = runtime.run_one(
            node_id="explore", visible_paths=["README.md"],
            invoke=make_researcher_invoke(lambda *_args, **_kwargs: {
                "hypotheses": hypotheses,
                "selected_hypothesis_id": "h2",
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


def test_core_ranks_later_hypothesis_by_grounded_prior_evidence_not_model_preference(tmp_path):
    runtime, graph, queue = _runtime(
        tmp_path, max_agent_calls=4, role_calls_per_instance=3,
    )
    observed = []

    def model_reply(public_input, **_kwargs):
        observed.append(public_input)
        if len(observed) == 1:
            text = "first durable observation"
            digest = hashlib.sha256(text.encode()).hexdigest()
            return {
                "next_experiment": "test the mechanism with a matched control",
                "artifact_text": {"observation.txt": text},
                "research_findings": [{
                    "claim_key": "mechanism:control",
                    "statement": "The initial public observation supports the control mechanism.",
                    "stance": "SUPPORTS", "kind": "OBSERVATION", "confidence": 0.7,
                    "evidence_refs": [f"artifact://sha256/{digest}"],
                }],
            }
        evidence_id = public_input["role_context"]["evidence_refs"][0]
        return {
            "hypotheses": [
                {
                    "id": "model-choice", "hypothesis": "test a broad unrelated condition",
                    "rationale": "The researcher prefers this candidate.",
                    "next_experiment": "test an unrelated condition",
                    "falsification_condition": "No predicted change is observed.",
                    "expected_evidence": "A public outcome for the unrelated condition.",
                },
                {
                    "id": "evidence-choice", "hypothesis": "test the prior control evidence",
                    "rationale": "This candidate is grounded in the previous durable observation.",
                    "next_experiment": "replicate the matched control observation",
                    "falsification_condition": "The prior control result does not replicate.",
                    "expected_evidence": "A replication outcome tied to the previous finding.",
                    "evidence_refs": [evidence_id],
                    "target_claim_key": "mechanism:control",
                },
            ],
            "selected_hypothesis_id": "model-choice",
            "artifact_text": {"observation.txt": "second durable observation"},
        }

    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(model_reply),
            max_iterations=2,
        )
        assert result["iterations_this_call"] == 2
        assert observed[1]["role_context"]["evidence_refs"]
        nodes = graph.scheduling_snapshot()["nodes"]
        second = next(node for node in nodes if node["payload"].get("iteration") == 1)
        completion = second["payload"]["completion_evidence"]
        assert completion["next_experiment"] == "replicate the matched control observation"
        artifact_root = tmp_path / "rpdi05" / "artifacts"
        portfolio = next(
            json.loads(path.read_text()) for path in artifact_root.rglob("*")
            if path.is_file() and b'"core_selection"' in path.read_bytes()
        )
        assert portfolio["researcher_preferred_hypothesis_id"] == "model-choice"
        assert portfolio["selected_hypothesis_id"] == "evidence-choice"
        assert portfolio["core_selection"]["method"] == "CORE_PUBLIC_DEV_EVIDENCE_COVERAGE"
        assert portfolio["core_selection"]["scores"]["evidence-choice"] == 1
    finally:
        queue.close()
        graph.close()


def test_exploration_stops_when_a_completed_experiment_reappears_after_another_step(tmp_path):
    runtime, graph, queue = _runtime(
        tmp_path, max_agent_calls=3, role_calls_per_instance=3,
    )
    experiments = iter(("vary the input size", "reverse the sample ordering",
                        "  VARY THE INPUT SIZE  "))
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"],
            invoke=make_researcher_invoke(
                lambda *_args, **_kwargs: {
                    "next_experiment": next(experiments),
                    "artifact_text": {"observation.txt": "durable observation"},
                },
            ),
            max_iterations=5,
        )
        assert result["status"] == "CONVERGED_REPEATED_EXPERIMENT"
        assert result["iterations_this_call"] == 3
        assert len(graph.scheduling_snapshot()["nodes"]) == 3
        assert "previously completed task" in json.loads(
            Path(result["report_path"]).read_text()
        )["stop_reason"]
    finally:
        queue.close()
        graph.close()


def test_exploration_detects_conservative_semantic_paraphrase_loop(tmp_path):
    runtime, graph, queue = _runtime(
        tmp_path, max_agent_calls=3, role_calls_per_instance=3,
    )
    experiments = iter(("vary the input size", "change the number of observations"))
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"],
            invoke=make_researcher_invoke(
                lambda *_args, **_kwargs: {
                    "next_experiment": next(experiments),
                    "artifact_text": {"observation.txt": "durable observation"},
                },
            ),
            max_iterations=5,
        )
        assert result["status"] == "CONVERGED_REPEATED_EXPERIMENT"
        assert result["iterations_this_call"] == 2
        assert len(graph.scheduling_snapshot()["nodes"]) == 2
        report = json.loads(Path(result["report_path"]).read_text())
        assert "controlled-variable intent matching" in report["stop_reason"]
    finally:
        queue.close()
        graph.close()


def test_experiment_intent_distinguishes_directions_and_controlled_variables():
    from bananloop.experiment_intent import same_experiment_intent

    assert same_experiment_intent("vary the input size", "change number of observations")
    assert same_experiment_intent("increase sample size", "vary the number of observations")
    assert not same_experiment_intent("increase sample size", "decrease sample size")
    assert not same_experiment_intent("vary sample size", "reverse sample ordering")


def test_hypothesis_portfolio_ignores_conflicting_redundant_top_level_action(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    try:
        result = runtime.run_one(
            node_id="explore", visible_paths=["README.md"],
            invoke=make_researcher_invoke(lambda *_args, **_kwargs: {
                "hypotheses": [{
                    "id": f"h{i}", "hypothesis": f"hypothesis {i}",
                    "rationale": "A distinct testable explanation.",
                    "next_experiment": f"experiment {i}",
                    "falsification_condition": "The predicted difference is absent.",
                    "expected_evidence": "A bounded public observation.",
                } for i in (1, 2)],
                "selected_hypothesis_id": "h2",
                "next_experiment": "experiment 1",
                "artifact_text": {"observation.txt": "observation"},
                "top-level evidence_refs": ["not-a-schema-field"],
                "research_findings": [{
                    "claim_key": "selected_hypothesis",
                    "statement": "The selected hypothesis has a testable next action.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.8,
                }],
            }),
        )
        assert result["status"] == "EXPLORATION_COMPLETED"
        payload = json.loads(graph.conn.execute(
            "SELECT payload_json FROM research_nodes WHERE node_id='explore'"
        ).fetchone()[0])
        assert payload["completion_evidence"]["next_experiment"] == "experiment 2"
        job = queue.conn.execute(
            "SELECT state FROM worker_jobs WHERE campaign_id='campaign-test'",
        ).fetchone()
        assert job[0] == "COMPLETED"
        assert graph.conn.execute(
            "SELECT status FROM research_nodes WHERE node_id='explore'",
        ).fetchone()[0] == "COMPLETED"
    finally:
        queue.close()
        graph.close()


def test_completed_research_job_is_projected_after_runtime_restart_without_redelivery(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []
    frozen_snapshot_id = []

    def model_reply(public_input, **_kwargs):
        calls.append("called")
        assert (
            f"Trusted frozen source_snapshot_id: {frozen_snapshot_id[0]}."
            in public_input["instruction"]
        )
        return {
            "next_experiment": "repeat with a matched control",
            "artifact_text": {"observation.txt": "observed once"},
        }

    try:
        invoke = make_researcher_invoke(model_reply)
        job = runtime.adapter.enqueue(node_id="explore", visible_paths=["README.md"])
        frozen_snapshot_id.append(job["request"]["base_commit"])
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
        assert len(projected["research_evidence_ids"]) == 1

        # Restart after graph publication but before automatic follow-up
        # creation; the durable completion record is sufficient to resume.
        resumed = restarted.run_campaign(
            visible_paths=["README.md"], invoke=invoke, max_iterations=1,
        )
        assert resumed["status"] == "CONVERGED_REPEATED_EXPERIMENT"
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
            "next_experiment": (
                "continue with a distinct controlled condition "
                f"{len(calls)}"
            ),
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


def test_registered_experiment_runs_and_becomes_memory_for_next_iteration(tmp_path):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=4)
    calls = []
    executions = []
    catalog = (
        {"id": "primary_association", "description": "estimate and interval"},
        {"id": "permutation_test", "description": "permutation check"},
    )

    def model(public_input, **_pin):
        calls.append(public_input)
        if len(calls) == 1:
            assert "Include a currently available registered" in public_input["instruction"]
        if len(calls) == 1:
            return {
                "hypotheses": [{
                    "id": "primary", "hypothesis": "Estimate the registered association.",
                    "rationale": "Measure the preregistered relation first.",
                    "next_experiment": "Run the primary association analysis.",
                    "falsification_condition": "The interval does not support the association.",
                    "expected_evidence": "An estimate and uncertainty interval.",
                }, {
                    "id": "alternative", "hypothesis": "Check a different method.",
                    "rationale": "This is a distinct robustness check.",
                    "next_experiment": "Run the permutation test.",
                    "falsification_condition": "The null is not rejected.",
                    "expected_evidence": "A permutation p value.",
                }],
                "selected_hypothesis_id": "primary",
                "experiment_id": "primary_association",
            }
        context = public_input["role_context"]
        assert "core_experiment.primary_association" in context["dev_summary"]
        assert context["evidence_refs"]
        assert {item["id"] for item in public_input["registered_experiments"]} == {"permutation_test"}
        prior_line = next(
            line for line in context["dev_summary"].splitlines()
            if line.startswith("core_experiment.primary_association [")
        )
        prior_claim = prior_line.split(" [", 1)[0]
        prior_refs = prior_line.split("evidence_ids=", 1)[1].split(":", 1)[0].split(",")
        return {
            "hypotheses": [{
                "id": "memory_followup", "hypothesis": "Check robustness of the prior estimate.",
                "rationale": "This directly tests the stored public result.",
                "next_experiment": "Run the registered permutation check against the prior estimate.",
                "falsification_condition": "The permuted null remains compatible.",
                "expected_evidence": "A two-sided permutation p value.",
                "experiment_id": "permutation_test",
                "evidence_refs": prior_refs,
                "target_claim_key": prior_claim,
            }, {
                "id": "unranked", "hypothesis": "Try an ungrounded alternative.",
                "rationale": "No memory cited.",
                "next_experiment": "Run a secondary analysis.",
                "falsification_condition": "No difference.",
                "expected_evidence": "A measurement.",
                "experiment_id": "permutation_test",
            }],
            "selected_hypothesis_id": "unranked",
        }

    def execute(experiment_id, role_context):
        executions.append(experiment_id)
        if experiment_id == "permutation_test":
            assert "core_experiment.primary_association" in role_context["dev_summary"]
        return {"status": "COMPLETED", "summary": f"Observed deterministic result for {experiment_id}.",
                "stance": "UNCERTAIN", "confidence": 0.9,
                "details": {"fixture": "offline", "experiment_id": experiment_id}}

    invoke = _make_researcher_invoke(
        model, experiment_executor=execute, experiment_catalog=catalog,
    )
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=invoke, max_iterations=2,
        )
        assert result["iterations_this_call"] == 2
        assert executions == ["primary_association", "permutation_test"]
        claims = {row[0] for row in graph.conn.execute(
            "SELECT claim_key FROM research_evidence WHERE campaign_id=?",
            (runtime.campaign_id,),
        )}
        assert claims >= {
            "core_experiment.primary_association", "core_experiment.permutation_test",
        }
        assert result["completed"][1]["research_evidence_ids"]
    finally:
        queue.close()
        graph.close()


def test_expired_campaign_deadline_stops_before_researcher_call(tmp_path):
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=2)
    runtime.adapter.campaign_deadline_epoch = time.time() - 1
    called = False

    def model(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("expired campaign must not dispatch a model call")

    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=_make_researcher_invoke(model),
            max_iterations=2,
        )
        assert result["status"] == "DEADLINE_EXCEEDED"
        assert result["iterations_this_call"] == 0
        assert called is False
    finally:
        queue.close()
        graph.close()


def test_boardgames_real_runner_executes_and_feeds_second_v2_iteration(tmp_path):
    import importlib.util

    repo_root = Path(__file__).resolve().parents[1]
    runner_path = repo_root / "benchmarks" / "discoverybench_boardgames" / "runner.py"
    spec = importlib.util.spec_from_file_location("test_registered_boardgames_runner", runner_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task_id = "bg-01-strategies-tournament-win"
    executor = module.BoardGamesExperimentExecutor(repo_root, task_id)
    runtime, graph, queue = _runtime(tmp_path, max_agent_calls=4)
    calls = 0

    def model(public_input, **_pin):
        nonlocal calls
        calls += 1
        if calls == 1:
            selected = "primary_association"
            return {"hypotheses": [{
                "id": "primary", "hypothesis": "Estimate the registered association.",
                "rationale": "Measure the frozen variables first.",
                "next_experiment": "Estimate Spearman rho with paired bootstrap interval.",
                "falsification_condition": "The 95% interval crosses zero.",
                "expected_evidence": "Rho and paired 95% interval.",
                "experiment_id": selected,
            }, {
                "id": "permutation", "hypothesis": "Test the null by permutation.",
                "rationale": "Check significance after the primary estimate.",
                "next_experiment": "Test the registered association under permutation.",
                "falsification_condition": "The permutation test is inconclusive.",
                "expected_evidence": "Two-sided permutation p value.",
                "experiment_id": "permutation_test",
            }], "selected_hypothesis_id": "primary",
                "research_findings": [{
                    "claim_key": "pending_measurement",
                    "statement": "The registered experiment remains unexecuted; its result is unknown.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.8,
                }, {
                    "claim_key": "experiment.split_half_robustness",
                    "statement": "The registered split-half experiment remains unexecuted in this response.",
                    "stance": "UNCERTAIN", "kind": "OBSERVATION", "confidence": 0.8,
                }],
                "artifact_text": {
                    "research/pending.txt": "The registered experiment remains unexecuted.",
                    "research/split-half-status.txt": "The registered split-half experiment remains unexecuted.",
                },
                "limitations": ["No registered experiment has been executed; result is unknown."]}
        context = public_input["role_context"]
        assert "core_experiment.primary_association" in context["dev_summary"]
        assert "rho=" in context["dev_summary"]
        available_ids = {item["id"] for item in public_input["registered_experiments"]}
        assert "primary_association" not in available_ids
        assert "split_half_robustness" in available_ids
        prior_line = next(
            line for line in context["dev_summary"].splitlines()
            if line.startswith("core_experiment.primary_association [")
        )
        prior_claim = prior_line.split(" [", 1)[0]
        prior_refs = prior_line.split("evidence_ids=", 1)[1].split(":", 1)[0].split(",")
        return {"hypotheses": [{
            "id": "memory-grounded", "hypothesis": "Test the primary association under permutation.",
            "rationale": "The stored estimate is the hypothesis being checked.",
            "next_experiment": "Run the two-sided registered permutation test.",
            "falsification_condition": "The two-sided permutation p value is at least 0.05.",
            "expected_evidence": "A deterministic two-sided permutation p value.",
            "experiment_id": "permutation_test",
            "evidence_refs": prior_refs,
            "target_claim_key": prior_claim,
        }, {
            "id": "ungrounded", "hypothesis": "Choose an unrelated test.",
            "rationale": "Alternative candidate.",
            "next_experiment": "Run the quality audit.",
            "falsification_condition": "No effect.",
            "expected_evidence": "A statistic.",
            "evidence_refs": list(context["evidence_refs"]),
            "target_claim_key": prior_claim,
        }, {
            "id": "misbound", "hypothesis": "Use evidence from a different claim.",
            "rationale": "This intentionally misbinds a valid evidence ID.",
            "next_experiment": "Run the registered data quality audit.",
            "falsification_condition": "The audit finds no relevant issue.",
            "expected_evidence": "A data quality summary.",
            "experiment_id": "data_quality_audit",
            "evidence_refs": list(context["evidence_refs"]),
            "target_claim_key": "different.claim",
        }], "selected_hypothesis_id": "ungrounded",
            "next_experiment": "This redundant top-level action intentionally disagrees."}

    invoke = _make_researcher_invoke(
        model, experiment_executor=executor, experiment_catalog=executor.catalog,
    )
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=invoke, max_iterations=2,
        )
        assert result["iterations_this_call"] == 2
        assert calls == 2
        rows = graph.conn.execute(
            "SELECT claim_key,statement FROM research_evidence WHERE campaign_id=?",
            (runtime.campaign_id,),
        ).fetchall()
        by_claim = {row["claim_key"]: row["statement"] for row in rows}
        assert "core_experiment.primary_association" in by_claim
        assert "spearman_rho=" in by_claim["core_experiment.primary_association"]
        assert "core_experiment.permutation_test" in by_claim
        assert "two_sided_p_value=" in by_claim["core_experiment.permutation_test"]
        assert "pending_measurement" not in by_claim
        portfolio_paths = list(Path(runtime.state_root).rglob("hypothesis-portfolio.json"))
        assert len(portfolio_paths) == 2
        latest_portfolio = json.loads(max(portfolio_paths, key=lambda p: p.stat().st_mtime).read_text())
        rejected = next(item for item in latest_portfolio["candidates"] if item["id"] == "misbound")
        assert rejected["core_evidence_validation"] == "REJECTED_INVALID_CLAIM_LINK"
        assert rejected["evidence_refs"] == [] and rejected["target_claim_key"] is None
        initial = json.loads(graph.conn.execute(
            "SELECT payload_json FROM research_nodes WHERE phase='CONSOLIDATION' ORDER BY created_at LIMIT 1"
        ).fetchone()[0])
        limitations = initial["completion_evidence"]["limitations"]
        assert not any("No registered experiment" in item for item in limitations)
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


def test_strategy_change_request_durably_pauses_until_owner_decision(tmp_path):
    runtime, graph, queue = _runtime(tmp_path)
    calls = []

    def proposal(public_input, **_pin):
        calls.append(public_input)
        return {"strategy_change_request": {
            "current_strategy": "test local feature correlations",
            "proposed_strategy": "switch to causal intervention experiments",
            "rationale": "Observational probes cannot distinguish the competing mechanisms.",
            "expected_effect": "Interventions should falsify one mechanism directly.",
            "changed_scope": ["experiment design", "causal identification"],
        }}

    recovered_queue = None
    recovered_graph = None
    try:
        result = runtime.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(proposal),
            max_iterations=4,
        )
        assert result["status"] == "PAUSED_NEEDS_INPUT"
        assert result["iterations_this_call"] == 1 and len(calls) == 1
        assert queue.campaign_control("campaign-test")["state"] == "PAUSED"
        snapshot = graph.scheduling_snapshot()
        completed = next(node for node in snapshot["nodes"] if node["node_id"] == "explore")
        request = completed["payload"]["completion_evidence"]["human_decision_request"]
        assert len(request["request_id"]) == 64
        assert len(snapshot["nodes"]) == 1
        job_id = completed["payload"]["completion_evidence"]["job_id"]
        artifact = queue.get_result(job_id)["result"]["artifacts"][0]
        assert artifact["path"] == "research/strategy-change-request.json"
        assert hashlib.sha256(
            next((tmp_path / "rpdi05" / "artifacts").rglob("strategy-change-request.json")).read_bytes()
        ).hexdigest() == artifact["sha256"]
        report = json.loads(Path(result["report_path"]).read_text())
        assert report["human_decision_requests"] == [{"node_id": "explore", "request": request}]
        assert report["completed_experiments"] == []

        queue_path = queue.path
        queue.close()
        graph.close()
        recovered_queue = DurableWorkerQueue(queue_path)
        recovered_graph = GraphRepository.open(
            str(tmp_path / "campaign.sqlite3"), campaign_id="campaign-test",
        )
        recovered = V2ResearchRuntime(
            graph=recovered_graph, queue=recovered_queue, campaign_id="campaign-test",
            state_root=tmp_path, role_registry=runtime.role_registry,
        )
        replay = recovered.run_campaign(
            visible_paths=["README.md"], invoke=make_researcher_invoke(proposal),
            max_iterations=4,
        )
        assert replay["status"] == "PAUSED_NEEDS_INPUT"
        assert replay["iterations_this_call"] == 0 and len(calls) == 1
        recovered_nodes = recovered_graph.scheduling_snapshot()["nodes"]
        assert len(recovered_nodes) == 1
        recovered_request = recovered_nodes[0]["payload"]["completion_evidence"]["human_decision_request"]
        assert recovered_request == request
    finally:
        if recovered_queue is not None:
            recovered_queue.close()
        else:
            queue.close()
        if recovered_graph is not None:
            recovered_graph.close()
        else:
            graph.close()
