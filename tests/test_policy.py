from bananloop.contracts import CampaignSpec, Direction, EvaluationResult, GuardrailSpec, MetricSpec, NonRegressionSpec
from bananloop.policy import decide, target_met


def spec() -> CampaignSpec:
    return CampaignSpec(
        goal="test",
        source_repo="/tmp/x",
        base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=3, min_improvement=0.5),
        guardrails=[GuardrailSpec(metric="regressions", operator="<=", bound=0)],
        mandatory_checks=["tests"],
    )


def result(q: float, regressions: float = 0) -> EvaluationResult:
    return EvaluationResult(metrics={"quality": q, "regressions": regressions}, checks={"tests": True})


def test_accept_and_target():
    d = decide(spec(), result(1), result(3))
    assert d.accepted
    assert d.delta == 2
    assert target_met(spec(), result(3))


def test_guardrail_rejects_even_when_primary_improves():
    d = decide(spec(), result(1), result(2, regressions=1))
    assert not d.accepted
    assert "GUARDRAIL_FAILED:regressions" in d.reasons



def test_non_regression_rejects_candidate_that_improves_primary_but_worsens_secondary():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(
            metric="regressions",
            direction=Direction.MINIMIZE,
        )
    ]

    d = decide(s, result(1, regressions=0), result(2, regressions=1))

    assert not d.accepted
    assert "REGRESSION:regressions" in d.reasons


def test_non_regression_accepts_equal_or_better_secondary():
    s = spec()
    s.guardrails = []
    s.non_regression = [
        NonRegressionSpec(
            metric="regressions",
            direction=Direction.MINIMIZE,
        )
    ]

    d = decide(s, result(1, regressions=1), result(2, regressions=1))

    assert d.accepted
