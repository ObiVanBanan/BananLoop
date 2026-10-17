"""Deterministic legacy-vs-portfolio policy replay on two CPU toy domains."""
from __future__ import annotations

import json
from pathlib import Path

from bananloop.contracts import (
    BudgetSpec, CampaignSpec, DevExperimentEvidence, Direction, Hypothesis,
    HypothesisCandidate, HypothesisPortfolio, MetricSpec, ResearchState,
)
from bananloop.research import candidate_rejection_reasons, select_candidate


def candidate(cid: str, mechanism: str, *, relation: str = "new", prior: list[int] | None = None,
             evidence: list[str] | None = None, evaluator_calls: int = 1) -> HypothesisCandidate:
    return HypothesisCandidate(
        candidate_id=cid,
        hypothesis=Hypothesis(
            hypothesis=f"test {mechanism}", rationale="frozen synthetic case",
            expected_effect="case metric reaches target", files_or_area=["src/model.py"],
            falsification_condition="fixed fixture metric does not reach target",
            mechanism_key=mechanism, relation=relation,
            prior_experiment_refs=prior or [],
            novelty_rationale="latest no-effect receipt motivates a different mechanism" if prior else "",
        ),
        evidence_refs=evidence or ["INSUFFICIENT_EVIDENCE"],
        suspected_failure_mode="repeated mechanism did not alter the DEV metric",
        predicted_observable="fixture metric delta", discriminating_test="same frozen DEV input",
        scope_eligibility=True, estimated_agent_calls=1,
        estimated_evaluator_calls=evaluator_calls, estimated_work_units=None,
        rationale="bounded synthetic alternative", uncertainty="toy evidence only",
    )


def main() -> int:
    scenarios = [
        {"domain": "bugfix", "metric": "correctness", "direction": Direction.MAXIMIZE,
         "baseline": 0.5, "target": 0.6, "alternative": "boundary-condition-fix", "candidate_value": 0.6},
        {"domain": "cpu-performance", "metric": "latency_ms", "direction": Direction.MINIMIZE,
         "baseline": 10.0, "target": 8.0, "alternative": "bounded-parse-cache", "candidate_value": 7.0},
    ]
    rows = []
    for scenario in scenarios:
        spec = CampaignSpec(
            goal=f"Improve toy {scenario['domain']} metric",
            source_repo=".", base_commit="fixture",
            evaluation_command=["python", "evaluate.py"],
            primary_metric=MetricSpec(name=scenario["metric"], direction=scenario["direction"],
                                      target=scenario["target"], min_improvement=0.1),
            editable_scope=["src/**"],
            budget=BudgetSpec(max_experiments=3, max_attempts=4, max_agent_calls=2,
                              max_evaluator_calls=2, plateau_experiments=3),
        )
        repeat = candidate("a-repeat", "known-no-effect", evidence=["exp:2"])
        alternative = candidate("z-alternative", scenario["alternative"], prior=[2], evidence=["exp:2"])
        state = ResearchState(
            planning_mode="reframe", dev_non_improve_streak=2,
            remaining_budget={"experiments": 1, "attempts": 1, "agent_calls": 1,
                              "evaluator_calls": 1},
            experiment_evidence=[
                DevExperimentEvidence(ordinal=1, mechanism_key="known-no-effect", dev_outcome="DEV_NO_IMPROVEMENT"),
                DevExperimentEvidence(ordinal=2, mechanism_key="known-no-effect", dev_outcome="DEV_NO_IMPROVEMENT"),
            ],
        )
        history = [
            {"ordinal": ordinal, "hypothesis_json": json.dumps({
                "mechanism_key": "known-no-effect", "prior_experiment_refs": [],
            })}
            for ordinal in (1, 2)
        ]
        legacy_rejected = candidate_rejection_reasons(repeat, spec=spec, state=state, experiments=history)
        selected = select_candidate(
            HypothesisPortfolio(candidates=[repeat, alternative]), spec=spec, state=state,
            experiments=history, experiment_ordinal=3, action_id="exp:3:propose",
        )
        value = scenario["candidate_value"] if selected.selected_candidate_id == "z-alternative" else scenario["baseline"]
        rows.append({
            "domain": scenario["domain"],
            "seed": 42,
            "fixed_input": f"{scenario['domain']}-dev-v1",
            "budgets": {"agent_calls_total": 2, "evaluator_calls_total": 2},
            "legacy": {
                "candidates_considered": 1, "duplicate_proposals": 1,
                "scientific_trials": 0, "distinct_executed_mechanisms": 0,
                "reframe_candidates_executed": 0,
                "agent_calls": 1, "evaluator_calls": 1,
                "best_dev_value": scenario["baseline"], "stop_reason": "NO_ADMISSIBLE_HYPOTHESIS",
                "core_rejection": legacy_rejected,
            },
            "portfolio": {
                "candidates_considered": 2, "duplicate_proposals": 1,
                "scientific_trials": 1, "distinct_executed_mechanisms": 1,
                "reframe_candidates_executed": int(selected.selected_candidate_id is not None),
                "agent_calls": 2, "evaluator_calls": 2,
                "best_dev_value": value,
                "stop_reason": "GOAL_MET" if value == scenario["target"] or (
                    scenario["direction"] == Direction.MINIMIZE and value <= scenario["target"]
                ) else "BUDGET_EXHAUSTED",
                "selected_candidate_id": selected.selected_candidate_id,
            },
        })
    print(json.dumps({
        "method": "deterministic policy replay; outcomes come from fixed toy evaluator mappings",
        "universal_quality_claim": False,
        "scenarios": rows,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
