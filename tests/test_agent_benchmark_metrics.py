"""Test bounded, deterministic role rubric checks in the offline replay scorer."""
import json

import pytest

from bananloop.agent_benchmark import prepare_demo, run_suite
from bananloop.agent_benchmark_metrics import OracleContractError, score_oracle, validate_oracle


def test_independent_role_rubric_handles_evidence_numeric_and_nested_fields():
    rules = {"checks": [
        {"type": "field_equals", "metric_id": "selected_candidate", "path": ["decision", "choice"], "expected": "h2"},
        {"type": "contains_all", "metric_id": "evidence_grounding", "path": ["evidence_refs"], "required": ["E1", "E2"]},
        {"type": "numeric_close", "metric_id": "measured_reward", "path": ["score"], "expected": 0.42, "absolute_tolerance": 0.01},
        {"type": "nonempty", "metric_id": "reasoning_present", "path": ["decision", "rationale"]},
    ]}
    answer = {"decision": {"choice": "h2", "rationale": "supported"}, "evidence_refs": ["E1", "E2"], "score": 0.415}
    passed, metrics = score_oracle(rules, answer)
    assert passed == 1
    assert [item["value"] for item in metrics] == [1, 1, 1, 1]
    bad, metrics = score_oracle(rules, {**answer, "evidence_refs": ["E1"], "score": 0.9})
    assert bad == 0
    assert [item["value"] for item in metrics] == [1, 0, 0, 1]


@pytest.mark.parametrize("bad", [
    {"checks": [{"type": "exec", "metric_id": "x", "path": ["x"]}]},
    {"checks": [{"type": "numeric_close", "metric_id": "x", "path": ["x"], "expected": 1, "absolute_tolerance": -1}]},
    {"checks": [{"type": "contains_all", "metric_id": "x", "path": ["x"], "required": ["dup", "dup"]}]},
    {"checks": [{"type": "field_equals", "metric_id": "x", "path": ["__private"], "expected": "x"}]},
    {"checks": []},
])
def test_malformed_or_executable_rubrics_are_rejected(bad):
    with pytest.raises(OracleContractError):
        validate_oracle(bad)


def test_replay_scores_one_case_with_multimetric_evaluator(tmp_path):
    root = tmp_path / "demo"
    prepare_demo(root)
    oracle_path = root / "oracle.dev.json"
    oracle = json.loads(oracle_path.read_text())
    oracle["cases"]["demo-00"] = {"checks": [
        {"metric_id": "chosen_unrefuted", "type": "field_equals", "path": ["selected"], "expected": "alternative"},
        {"metric_id": "has_choice", "type": "nonempty", "path": ["selected"]},
    ]}
    oracle_path.write_text(json.dumps(oracle))
    case_path = root / "cases.dev.json"
    suite = json.loads(case_path.read_text())
    from bananloop.agent_benchmark import digest
    for case in suite["cases"]:
        case["evaluator_binding"]["sha256"] = digest(oracle)
    case_path.write_text(json.dumps(suite))
    data = {"demo-00": {"selected": "alternative"}, "demo-01": {"selected": "first"},
            "demo-02": {"result": 9}, "demo-03": {"result": 2},
            "demo-04": {"decision": "PASS"}, "demo-05": {"decision": "REJECT"},
            "demo-06": {"decision": "ACCEPT"}, "demo-07": {"decision": "REJECT"}}
    response_path = root / "responses.json"
    response_path.write_text(json.dumps({"schema_version": 1, "responses": data}))
    result = run_suite(cases_path=case_path, oracle_path=oracle_path,
                       registry_path=root / "registry.json", team_path=root / "team.json",
                       output_dir=root / "results", adapter="replay", responses_path=response_path)
    assert result["roles"]["researcher"]["accuracy"] == 1.0
    records = json.loads((root / "results" / "results.json").read_text())["results"]
    assert [metric["metric_id"] for metric in records[0]["metric_results"]] == [
        "task_success", "chosen_unrefuted", "has_choice"]
