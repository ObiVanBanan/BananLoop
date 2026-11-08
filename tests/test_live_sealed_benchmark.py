"""No-network tests of sealed private oracles and opt-in live Codex preflight."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bananloop.agent_benchmark import BenchmarkError, digest, prepare_demo, read_json, _write
from bananloop.benchmark_sealed import seal_suite, score_sealed, evaluator_commitment
from bananloop.benchmark_generate import generate_private_smoke
from bananloop.benchmark_live_codex import (
    LiveBenchmarkError, _preflight, parse_codex_events, run_live, validate_role_reply,
)
from bananloop.roles import RoleRegistryV1, TeamFactoryV1, TeamRequestV1


def _author_from_demo(root: Path):
    prepare_demo(root)
    public, oracle = read_json(root / "cases.dev.json"), read_json(root / "oracle.dev.json")
    authored = []
    for i, case in enumerate(public["cases"]):
        authored.append({
            "case_id": case["case_id"], "role_id": case["role_id"],
            "split": ("DEV", "VALIDATION", "HOLDOUT")[i % 3],
            "agent_input": case["agent_input"],
            "oracle": oracle["cases"][case["case_id"]],
            "source_id": "synthetic-private-fixture", "source_revision": "test-only",
            "license_status": "INTERNAL_SANITIZED",
            "resource_budget": case["resource_budget"],
            "task_family": "offline_contract",
        })
    private_source = root / "private-input.json"
    _write(private_source, {"schema_version": 1, "cases": authored})
    return private_source, public


def _pinned_codex_one_case(root: Path):
    prepare_demo(root)
    registry = RoleRegistryV1.model_validate(read_json(root / "registry.json"))
    revised = tuple(r.model_copy(update={"adapter": "codex", "provider": "openai",
                                         "model": "gpt-6-luna", "model_version": "test-pin"})
                    for r in registry.roles)
    pinned = RoleRegistryV1(registry_id="test-pinned-codex", roles=revised)
    req = TeamRequestV1(campaign_id="test-role-model", spec_hash="f" * 64,
                        available_agent_calls=4, available_model_tokens=128)
    team = TeamFactoryV1.build(pinned, req, expected_registry_digest=pinned.digest())
    public = read_json(root / "cases.dev.json")
    public["cases"] = public["cases"][:1]
    _write(root / "cases-codex.json", public)
    _write(root / "registry-codex.json", pinned.model_dump(mode="json"))
    _write(root / "team-codex.json", team.model_dump(mode="json"))
    auth = {
        "schema_version": 1, "mode": "CODEX_SUBSCRIPTION_NONQUALIFYING_DIAGNOSTIC",
        "suite_sha256": digest(public), "registry_sha256": pinned.digest(),
        "team_manifest_sha256": team.manifest_digest, "max_cases": 1,
        "max_observed_input_tokens": 3000, "max_observed_output_tokens": 300,
        "max_wall_seconds_per_call": 10,
        "acknowledge_subscription_usage_unverified": True,
        "acknowledge_token_caps_observational": True,
    }
    _write(root / "owner-authorization.json", auth)
    return public, pinned, team, auth


def test_sealed_private_oracle_is_not_copied_to_codex_public_suite(tmp_path):
    source, public_source = _author_from_demo(tmp_path / "seed")
    public_dir, private_dir = tmp_path / "public", tmp_path / "operator-only"
    pins = seal_suite(authored_suite_path=source,
                      registry_path=source.parent / "registry.json",
                      team_path=source.parent / "team.json",
                      public_dir=public_dir, private_dir=private_dir)
    public = read_json(public_dir / "cases.json")
    oracle = read_json(private_dir / "oracle.json")
    nonce = read_json(private_dir / "nonce.json")
    assert len(public["cases"]) == 8
    assert {c["split"] for c in public["cases"]} == {"DEV", "VALIDATION", "HOLDOUT"}
    assert "expected_output" not in (public_dir / "cases.json").read_text()
    assert "nonce" not in (public_dir / "cases.json").read_text()
    assert pins["evaluator_commitment_sha256"] == evaluator_commitment(nonce["nonce"], oracle)
    assert pins["independently_verified"] is False


def test_second_operator_scores_sealed_holdout_after_model_response(tmp_path):
    source, public_source = _author_from_demo(tmp_path / "seed")
    public, private = tmp_path / "public", tmp_path / "private"
    seal_suite(authored_suite_path=source,
               registry_path=source.parent / "registry.json",
               team_path=source.parent / "team.json",
               public_dir=public, private_dir=private)
    import importlib.util
    worker_file = Path(__file__).resolve().parents[1] / "benchmarks" / "agent_capability_v0.2" / "fixture_worker.py"
    spec = importlib.util.spec_from_file_location("benchmark_fixture_worker", worker_file)
    worker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(worker)
    responses = {c["case_id"]: worker.respond(c["role_id"], c["agent_input"])
                 for c in read_json(public / "cases.json")["cases"]}
    replies = tmp_path / "responses.json"
    _write(replies, {"schema_version": 1, "responses": responses})
    summary = score_sealed(public_dir=public, private_dir=private,
                           responses_path=replies, output_dir=tmp_path / "scored")
    assert summary["qualification"] == "SEALED_LOCAL_DIAGNOSTIC_NOT_RPDI14"
    assert set(k.split(":")[0] for k in summary["splits"]) == {"DEV", "VALIDATION", "HOLDOUT"}
    assert all(v["passed"] == v["completed"] == v["total"] for v in summary["splits"].values())
    assert summary["release_eligible"] is False


def test_tampered_private_oracle_rejected_before_creating_scoring_directory(tmp_path):
    source, _ = _author_from_demo(tmp_path / "seed")
    public, private = tmp_path / "public", tmp_path / "private"
    seal_suite(authored_suite_path=source, registry_path=source.parent / "registry.json",
               team_path=source.parent / "team.json", public_dir=public, private_dir=private)
    oracle = read_json(private / "oracle.json")
    oracle["cases"]["demo-00"]["expected_output"] = {"selected": "forged"}
    _write(private / "oracle.json", oracle)
    responses = tmp_path / "responses.json"
    _write(responses, {"schema_version": 1, "responses": {}})
    with pytest.raises(BenchmarkError, match="replaced since sealing"):
        score_sealed(public_dir=public, private_dir=private,
                     responses_path=responses, output_dir=tmp_path / "results")
    assert not (tmp_path / "results").exists()


def test_fresh_random_private_generator_has_all_four_roles_and_splits(tmp_path):
    private = tmp_path / "author.json"
    result = generate_private_smoke(path=private, per_role=10)
    assert result["private_case_count"] == 40
    assert result["official_benchmark"] is False
    data = read_json(private)["cases"]
    assert len({c["case_id"] for c in data}) == 40
    assert {c["role_id"] for c in data} == {
        "researcher", "optimizer", "independent_verifier", "integration_verifier"}
    assert {c["split"] for c in data} == {"DEV", "VALIDATION", "HOLDOUT"}
    assert len({c["oracle"]["checks"][0]["metric_id"] for c in data}) >= 3


def test_codex_jsonl_has_measured_usage_and_one_structured_reply():
    lines = [
        {"type": "thread.started", "thread_id": "uuid"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"research_findings":[]}'}},
        {"type": "turn.completed", "usage": {
            "input_tokens": 500, "cached_input_tokens": 100, "output_tokens": 32}},
    ]
    answer, usage = parse_codex_events(b"\n".join(json.dumps(x).encode() for x in lines))
    assert answer == {"research_findings": []}
    assert usage["input_tokens"] == 500


@pytest.mark.parametrize("bad", [
    {"type": "item.completed", "item": {"type": "command_execution", "command": "cat /secret"}},
    {"type": "turn.failed", "error": "provider"},
    {"type": "turn.completed", "usage": {"input_tokens": None, "cached_input_tokens": 0, "output_tokens": 1}},
])
def test_codex_missing_or_untrusted_receipt_fails_closed(bad):
    with pytest.raises(LiveBenchmarkError):
        parse_codex_events(json.dumps(bad).encode())


def test_live_run_requires_exact_owner_approval_before_invoking_model(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    public, registry, team, auth = _pinned_codex_one_case(root)
    auth["acknowledge_token_caps_observational"] = False
    with pytest.raises(LiveBenchmarkError, match="authorization"):
        _preflight(public, registry, team, auth)
    seen = []
    def fake_model(case, model_id, **kwargs):
        seen.append(model_id)
        return {"research_findings": [{"claim": "safe synthetic evidence"}]}, {
            "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
        }
    result = run_live(
        cases_path=root / "cases-codex.json",
        registry_path=root / "registry-codex.json",
        team_path=root / "team-codex.json",
        authorization_path=root / "owner-authorization.json",
        output_dir=root / "usage-results",
        execute=fake_model,
    )
    assert result["status"] == "COMPLETED_DIAGNOSTIC"
    assert result["actual_provider_cost_usd"] is None
    assert seen == ["gpt-6-luna"]
    assert read_json(root / "usage-results" / "usage.observed.json")["usage_authority"] == (
        "UNSIGNED_OBSERVATION_NOT_PROVIDER_RECEIPT")


def test_live_refuses_bad_role_reply_without_automatic_retry(tmp_path):
    root = tmp_path / "demo"
    _pinned_codex_one_case(root)
    seen = []
    def malformed(case, model_id, **kwargs):
        seen.append(case["case_id"])
        return {"not_a_valid": "ResearcherReplyV1"}, {
            "input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 2,
        }
    result = run_live(cases_path=root / "cases-codex.json",
                      registry_path=root / "registry-codex.json",
                      team_path=root / "team-codex.json",
                      authorization_path=root / "owner-authorization.json",
                      output_dir=root / "usage-results", execute=malformed)
    assert len(seen) == 1
    assert result["status"] == "STOPPED_UNKNOWN_USAGE_OR_INVALID_OUTPUT"
    assert result["release_eligible"] is False
