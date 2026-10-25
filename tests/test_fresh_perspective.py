from __future__ import annotations

import copy

from bananloop.contracts import (
    BudgetSpec, CampaignSpec, Direction, Hypothesis, HypothesisCandidate,
    HypothesisPortfolio, MetricSpec,
)
from bananloop.fresh_perspective import (
    build_fresh_proposal_task, grounding_task_for_candidate,
    merge_proposal_portfolios, should_request_fresh,
)


def _candidate(key: str) -> HypothesisCandidate:
    return HypothesisCandidate(
        candidate_id=key,
        hypothesis=Hypothesis(
            hypothesis="try " + key, rationale="test",
            expected_effect="measurable gain", files_or_area=["src"],
            falsification_condition="no gain", mechanism_key=key,
        ),
        evidence_refs=["INSUFFICIENT_EVIDENCE"],
        predicted_observable="metric increases",
        discriminating_test="compare a fixed DEV sample",
        scope_eligibility=True,
        estimated_agent_calls=1,
        estimated_evaluator_calls=1,
        rationale="independent idea",
        uncertainty="untested",
        inference="agent_inference",
    )


def test_opt_in_only_on_plateau_with_implementer_budget():
    task = {"role": "propose", "planning_mode": "reframe"}
    assert not should_request_fresh(task, enabled=False, remaining_agent_calls=5)
    assert not should_request_fresh({**task, "planning_mode": "explore"},
                                    enabled=True, remaining_agent_calls=5)
    assert not should_request_fresh(task, enabled=True, remaining_agent_calls=1)
    assert should_request_fresh(task, enabled=True, remaining_agent_calls=2)
    assert should_request_fresh(task, enabled=True, remaining_agent_calls=0,
                                action_already_reserved=True)


def test_fresh_context_strips_history_dev_evidence_and_holdout():
    normal = {
        "role": "propose", "planning_mode": "reframe",
        "goal": "improve throughput", "editable_scope": ["src"],
        "history": [{"dev": "previous false assumption"}],
        "research_state": {
            "failure_signals": [{"secret": "DEV diagnostics"}],
            "experiment_evidence": [{"ordinal": 9}],
            "recent_failure_capsules": [{"observation": "old failed hypothesis"}],
            "holdout": "never visible",
        },
        "portfolio_contract": {"max_candidates": 4},
        "validation": {"secret": "hidden"},
    }
    original = copy.deepcopy(normal)
    fresh = build_fresh_proposal_task(normal)
    assert normal == original
    assert fresh["goal"] == normal["goal"]
    assert fresh["editable_scope"] == ["src"]
    assert fresh["history"] == []
    assert fresh["research_state"]["failure_signals"] == []
    assert fresh["research_state"]["experiment_evidence"] == []
    assert "validation" not in fresh
    assert "holdout" not in repr(fresh)
    assert "previous false assumption" not in repr(fresh)


def test_merges_one_novel_candidate_preserving_cap_and_fallback():
    a = HypothesisPortfolio(candidates=[_candidate("a"), _candidate("b"),
                                        _candidate("c"), _candidate("d")])
    fresh = HypothesisPortfolio(candidates=[_candidate("b"), _candidate("different")])
    merged = merge_proposal_portfolios(a, fresh)
    assert [x.candidate_id for x in merged.candidates] == ["a", "b", "c", "different"]
    assert [x.candidate_id for x in merge_proposal_portfolios(
        a, HypothesisPortfolio(candidates=[_candidate("b")])).candidates
    ] == ["a", "b", "c", "d"]


def test_old_spec_hash_unchanged_and_experimental_mode_is_approved():
    kwargs = dict(
        goal="quality", source_repo=".", base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(
            name="accuracy", direction=Direction.MAXIMIZE, target=1.0,
            min_improvement=0.01,
        ),
        budget=BudgetSpec(),
    )
    baseline = CampaignSpec(**kwargs)
    restored = CampaignSpec.model_validate(
        {key: value for key, value in baseline.model_dump(mode="json").items()
         if key != "fresh_perspective_on_reframe"}
    )
    experiment = CampaignSpec(**kwargs, fresh_perspective_on_reframe=True)
    assert baseline.approved_hash() == restored.approved_hash()
    assert experiment.approved_hash() != baseline.approved_hash()


def test_only_fresh_candidate_may_omit_historic_failure_signal():
    task = {"role": "propose", "research_state": {
        "planning_mode": "reframe",
        "failure_signals": [{"signal_key": "dev-error"}],
        "experiment_evidence": [{"ordinal": 1}],
    }}
    fresh = grounding_task_for_candidate(
        task, mechanism_key="new-algorithm",
        independent_mechanism_key="new-algorithm",
    )
    other = grounding_task_for_candidate(
        task, mechanism_key="old-mechanism",
        independent_mechanism_key="new-algorithm",
    )
    assert fresh["research_state"]["failure_signals"] == []
    assert fresh["research_state"]["experiment_evidence"] == [{"ordinal": 1}]
    assert other is task
    assert task["research_state"]["failure_signals"] == [{"signal_key": "dev-error"}]
