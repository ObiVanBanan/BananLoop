"""Offline E2E and negative tests for the role-capability diagnostic runner."""
import json
from pathlib import Path

import pytest

from bananloop.agent_benchmark import BenchmarkError, digest, prepare_demo, run_suite


def _run(root: Path, **overrides):
    options = dict(cases_path=root / "cases.dev.json", oracle_path=root / "oracle.dev.json",
                   registry_path=root / "registry.json", team_path=root / "team.json",
                   output_dir=root / "results", adapter="fixture")
    options.update(overrides)
    return run_suite(**options)


def test_four_roles_execute_and_report_without_provider_calls(tmp_path):
    root = tmp_path / "demo"
    assert prepare_demo(root)["case_count"] == 8
    result = _run(root)
    assert set(result["roles"]) == {"researcher", "optimizer", "independent_verifier", "integration_verifier"}
    assert all(row == {"attempted": 2, "completed": 2, "exact_matches": 2,
                       "accuracy": 1.0, "failures": 0} for row in result["roles"].values())
    assert result["qualification"] == "DEV_DIAGNOSTIC_ONLY"
    report = (root / "results" / "report.md").read_text(encoding="utf-8")
    assert "no cross-role aggregate winner" in report
    records = json.loads((root / "results" / "results.json").read_text())["results"]
    assert all(rec["resource_usage"]["agent_calls"] == 0 for rec in records)
    assert all(rec["resource_usage"]["cost_status"] == "NOT_APPLICABLE" for rec in records)
    assert len({rec["role_identity"]["role_id"] for rec in records}) == 4
    assert all(rec["role_identity"]["role_definition_sha256"] for rec in records)


def test_replay_scores_recorded_outputs_with_unknown_cost(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    records = {f"demo-{i:02d}": answer for i, answer in enumerate((
        {"selected": "alternative"}, {"selected": "second"},
        {"result": 9}, {"result": 2},
        {"decision": "PASS"}, {"decision": "REJECT"},
        {"decision": "ACCEPT"}, {"decision": "REJECT"},
    ))}
    responses = root / "responses.json"
    responses.write_text(json.dumps({"schema_version": 1, "responses": records}))
    summary = _run(root, adapter="replay", responses_path=responses)
    assert summary["roles"]["researcher"]["accuracy"] == 0.5
    assert summary["roles"]["optimizer"]["accuracy"] == 1.0
    results = json.loads((root / "results" / "results.json").read_text())["results"]
    assert all(row["resource_usage"]["cost_usd"] is None for row in results)
    assert all(row["resource_usage"]["cost_status"] == "UNKNOWN" for row in results)


@pytest.mark.parametrize("mutator", [
    lambda doc: doc["cases"][0].update(split="HOLDOUT"),
    lambda doc: doc["cases"][0].update(role_id="proposer"),
    lambda doc: doc["cases"][0]["agent_input"].update(refuted=[]),
    lambda doc: doc["cases"][0]["provenance"].update(license_status="PENDING"),
    lambda doc: doc["cases"].append(dict(doc["cases"][0])),
])
def test_invalid_or_unlicensed_cases_are_rejected_before_execution(tmp_path, mutator, monkeypatch):
    root = tmp_path / "demo"
    prepare_demo(root)
    path = root / "cases.dev.json"
    document = json.loads(path.read_text())
    mutator(document)
    path.write_text(json.dumps(document))
    monkeypatch.setattr("bananloop.agent_benchmark._fixture_reply",
                        lambda _case: pytest.fail("agent invoked before preflight validation"))
    with pytest.raises(BenchmarkError):
        _run(root)
    assert not (root / "results").exists()


def test_oracle_tampering_fails_before_execution(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    path = root / "oracle.dev.json"
    data = json.loads(path.read_text())
    data["cases"]["demo-00"]["expected_output"]["selected"] = "repeat"
    path.write_text(json.dumps(data))
    with pytest.raises(BenchmarkError, match="evaluator pin"):
        _run(root)


def test_registry_and_team_binding_mismatch_is_rejected(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    path = root / "registry.json"
    data = json.loads(path.read_text())
    data["registry_id"] = "changed"
    path.write_text(json.dumps(data))
    with pytest.raises(BenchmarkError, match="pinned role registry"):
        _run(root)


def test_replay_requires_all_frozen_case_ids(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    path = root / "responses.json"
    path.write_text(json.dumps({"schema_version": 1, "responses": {"demo-00": {"selected": "a"}}}))
    with pytest.raises(BenchmarkError, match="cover exactly"):
        _run(root, adapter="replay", responses_path=path)


def test_runner_does_not_overwrite_artifact_directory(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    _run(root)
    with pytest.raises(BenchmarkError, match="already exists"):
        _run(root)


def test_public_input_and_oracle_are_separate_files(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    public = json.loads((root / "cases.dev.json").read_text())
    private = json.loads((root / "oracle.dev.json").read_text())
    assert private["cases"]["demo-00"]["expected_output"] == {"selected": "alternative"}
    assert "expected_output" not in json.dumps(public)
    assert all(c["evaluator_binding"]["sha256"] == digest(private) for c in public["cases"])
