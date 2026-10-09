import json

from bananloop.cli import _agent_config, _agent_safe_decision, parser


def test_goal_uses_profile_default_backend(tmp_path):
    profiles = tmp_path / "agents.toml"
    profiles.write_text('[default]\nkind="pi"\nmodel="local/model"\n', encoding="utf-8")
    args = parser().parse_args(["goal", "Improve quality", "--agent-profiles", str(profiles)])
    config = _agent_config(args)
    assert config.kind == "pi"
    assert config.model == "local/model"


def test_explain_and_inspect_commands_accept_deterministic_json_options():
    explain = parser().parse_args(["explain", "--json", "--state-dir", ".state"])
    inspect = parser().parse_args(["inspect", "--json", "--experiment", "2"])
    assert explain.command == "explain"
    assert explain.json is True
    assert explain.state_dir == ".state"
    assert inspect.command == "inspect"
    assert inspect.experiment == 2


def test_agent_facing_explanation_strips_validation_payloads():
    safe = _agent_safe_decision({
        "accepted": False,
        "validation": {"value": "hidden-label"},
        "predicates": {"validation": False, "minimum_improvement": True},
        "reasons": ["VALIDATION:case=secret", "PRIMARY_NOT_IMPROVED"],
    })
    encoded = json.dumps(safe)
    assert "hidden-label" not in encoded
    assert "secret" not in encoded
    assert safe["predicates"] == {"minimum_improvement": True}


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
    assert config.provider == "ollama-cloud"
    assert config.reasoning_effort == "off"
    assert config.extra_args == [
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



def test_init_cli_accepts_repeatable_anti_gaming_patterns():
    args = parser().parse_args([
        "init",
        "--forbid-diff-regex", r"\\bharness_gold\\b",
        "--forbid-diff-regex", r"gold_q\\d+",
    ])

    assert args.forbid_diff_regex == [
        r"\\bharness_gold\\b",
        r"gold_q\\d+",
    ]



def test_start_cli_requires_only_repo_goal_for_default_codex_flow():
    args = parser().parse_args([
        "start",
        "--repo", ".",
        "--goal", "improve measured quality",
    ])

    assert args.command == "start"
    assert args.agent is None
    assert _agent_config(args).kind == "codex"
    assert args.goal == "improve measured quality"
    assert args.max_experiments == 4
    assert args.deadline_seconds == 14400


def test_start_cli_can_prepare_without_running():
    args = parser().parse_args([
        "start",
        "--goal", "improve measured quality",
        "--prepare-only",
        "--model", "gpt-5.6-codex",
    ])

    assert args.prepare_only is True
    assert args.model == "gpt-5.6-codex"


def test_replay_cli_requires_action_id():
    args = parser().parse_args(["replay", "--action", "baseline:evaluate"])
    assert args.action == "baseline:evaluate"



def test_goal_cli_is_minimal_chat_first_surface():
    args = parser().parse_args([
        "goal",
        "improve generated image quality against repository reference evidence",
    ])

    assert args.command == "goal"
    assert args.goal.startswith("improve generated image quality")
    assert args.repo == "."
    assert args.agent is None
    assert _agent_config(args).kind == "codex"
    assert args.model is None
    assert args.max_experiments == 4
    assert args.max_evaluator_calls == 16


def test_goal_cli_can_prepare_only():
    args = parser().parse_args([
        "goal",
        "improve measured quality",
        "--repo", "project",
        "--prepare-only",
    ])

    assert args.repo == "project"
    assert args.prepare_only is True


def test_doctor_cli_has_json_mode():
    args = parser().parse_args([
        "doctor",
        "--repo", ".",
        "--json",
    ])

    assert args.command == "doctor"
    assert args.repo == "."
    assert args.json is True
