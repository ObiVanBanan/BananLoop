from bananloop.cli import _agent_config, parser


def test_pi_cli_builds_agent_config():
    args = parser().parse_args([
        "init",
        "--goal", "test",
        "--eval-cmd", "python evaluator.py",
        "--metric", "quality",
        "--target", "1",
        "--min-improvement", "0.1",
        "--agent", "pi",
        "--provider", "ollama-cloud",
        "--model", "nemotron-3-nano:30b",
        "--thinking", "off",
        "--pi-arg=--exclude-tools",
        "--pi-arg=web_search",
    ])

    config = _agent_config(args)

    assert config.kind == "pi"
    assert config.model == "nemotron-3-nano:30b"
    assert config.extra_args == [
        "--provider", "ollama-cloud",
        "--thinking", "off",
        "--exclude-tools", "web_search",
    ]



def test_init_cli_accepts_agent_isolation_controls():
    args = parser().parse_args([
        "init",
        "--agent-hide", "harness_rag/**",
        "--agent-hide", "data/**",
        "--agent-metric", "public_hard_pass_rate",
        "--agent-metric", "public_false_match_rate",
    ])

    assert args.agent_hide == ["harness_rag/**", "data/**"]
    assert args.agent_metric == [
        "public_hard_pass_rate",
        "public_false_match_rate",
    ]



def test_init_cli_parses_non_regression_guardrail():
    args = parser().parse_args([
        "init",
        "--non-regression", "public_human_reject_rate:minimize",
        "--non-regression", "coverage:maximize:0.01",
    ])

    assert args.non_regression[0].metric == "public_human_reject_rate"
    assert args.non_regression[0].direction.value == "minimize"
    assert args.non_regression[0].tolerance == 0.0
    assert args.non_regression[1].metric == "coverage"
    assert args.non_regression[1].direction.value == "maximize"
    assert args.non_regression[1].tolerance == 0.01



def test_init_cli_accepts_staged_evaluation_commands():
    args = parser().parse_args([
        "init",
        "--validation-eval-cmd", "python validation.py --aggregate-only",
        "--holdout-eval-cmd", "python holdout.py --final-only",
        "--validation-timeout-seconds", "1200",
        "--holdout-timeout-seconds", "3600",
    ])

    assert args.validation_eval_cmd == "python validation.py --aggregate-only"
    assert args.holdout_eval_cmd == "python holdout.py --final-only"
    assert args.validation_timeout_seconds == 1200
    assert args.holdout_timeout_seconds == 3600
