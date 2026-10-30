import json
import pytest

from bananloop.cli import MAX_FROZEN_PORTFOLIO_BYTES, _agent_config, _read_frozen_portfolio, parser


def test_goal_uses_profile_default_backend(tmp_path):
    profiles = tmp_path / "agents.toml"
    profiles.write_text('[default]\nkind="pi"\nmodel="local/model"\n', encoding="utf-8")
    args = parser().parse_args(["goal", "Improve quality", "--agent-profiles", str(profiles)])
    config = _agent_config(args)
    assert config.kind == "pi"
    assert config.model == "local/model"


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


def test_init_cli_parses_frozen_portfolio_and_replicate_label():
    args = parser().parse_args([
        "init", "--frozen-portfolio", "portfolio.json", "--replicate-label", "seed-53",
    ])
    assert args.frozen_portfolio == "portfolio.json"
    assert args.replicate_label == "seed-53"


def test_frozen_portfolio_reader_rejects_oversized_file(tmp_path):
    path = tmp_path / "oversized-portfolio.json"
    path.write_bytes(b" " * (MAX_FROZEN_PORTFOLIO_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds the 1048576-byte limit"):
        _read_frozen_portfolio(path)



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

def test_explicit_state_dir_has_stable_display_campaign_id(tmp_path):
    from argparse import Namespace
    from bananloop.cli import _display_campaign_id
    from bananloop.contracts import RuntimePaths

    paths = RuntimePaths.for_repo(tmp_path, tmp_path / "state")
    args = Namespace(state_dir=str(tmp_path / "state"), campaign_id=None)

    first = _display_campaign_id(args, paths)
    second = _display_campaign_id(args, paths)

    assert first == second
    assert first.startswith("state-")


def test_status_of_reserved_campaign_does_not_create_campaign_db(tmp_path, capsys):
    from argparse import Namespace
    from bananloop.cli import cmd_status
    from bananloop.registry import CampaignRegistry
    from bananloop.contracts import RuntimePaths

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = CampaignRegistry(repo)
    entry, state_root, _ = registry.reserve(
        "improve fixture", "reserved-status-test"
    )
    paths = RuntimePaths.for_repo(repo, state_root)
    assert not paths.db.exists()

    args = Namespace(
        repo=str(repo),
        state_dir=None,
        campaign_id=entry.campaign_id,
        json=True,
    )
    assert cmd_status(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RESERVED"
    assert payload["campaign_id"] == entry.campaign_id
    assert payload["initialized"] is False
    assert not paths.db.exists()


def test_status_evaluation_summary_omits_case_level_records():
    from bananloop.cli import _status_evaluation_summary

    result = _status_evaluation_summary([{
        "experiment_id": 3,
        "tier": "holdout",
        "result": {
            "metrics": {"accuracy": {"value": 0.75}},
            "checks": {"complete": {"passed": True}},
            "cases": [{"case_id": "hidden-case", "metrics": {"label": 1}}],
            "diagnostics": {"private": "detail"},
        },
    }])

    assert result == [{
        "experiment_id": 3,
        "tier": "holdout",
        "metrics": {"accuracy": 0.75},
        "checks": {"complete": True},
    }]

def test_prepare_alias_enables_preflight_without_running_experiments():
    args = parser().parse_args(["prepare", "--repo", ".", "--goal", "Improve PR-AUC"])
    assert args.prepare_only is True
    assert args.goal == "Improve PR-AUC"
    assert args.max_experiments == 4
    assert args.agent is None


def test_opt_in_semantic_selector_cli_modes():
    from bananloop.cli import parser

    for command, prefix in (
        ("start", ["--goal", "improve"]),
        ("goal", ["improve"]),
        ("prepare", ["--goal", "improve"]),
        ("init", ["--goal", "improve", "--eval-cmd", "python eval.py"]),
    ):
        args = parser().parse_args([command, *prefix, "--selector-mode", "llm"])
        assert args.selector_mode == "llm"
    assert parser().parse_args(["goal", "improve"]).selector_mode == "deterministic"


def test_pivot_decision_cli_requires_rejection_choice_and_exact_fields():
    import pytest
    from bananloop.cli import cmd_pivot_decision, parser
    from bananloop.store import StoreError

    cli = parser()
    prefix = ["pivot-decision", "--campaign-id", "c-" + "a" * 32,
              "--proposal-id", "pivot-" + "b" * 24, "--digest", "c" * 64,
              "--request-id", "decision-1"]
    with pytest.raises(StoreError, match="requires --on-reject"):
        cmd_pivot_decision(cli.parse_args([*prefix, "--decision", "reject"]))
    approved = cli.parse_args([*prefix, "--decision", "approve"])
    assert approved.decision == "approve"
    rejected = cli.parse_args([*prefix, "--decision", "reject", "--on-reject", "change_conditions"])
    assert rejected.on_reject == "change_conditions"


def test_pivot_decision_cli_records_operator_choice_without_execution(tmp_path, capsys):
    import json
    from bananloop.cli import cmd_pivot_decision
    from bananloop.contracts import PivotProposal, PivotResourceRequest
    from bananloop.store import CampaignStore
    from test_store_resources import make_store

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    store = make_store(state_dir)
    proposal = PivotProposal.build(
        spec_hash=store.campaign()["spec_hash"], trigger_experiment_ordinal=1,
        current_architecture="old", proposed_architecture="new", rationale="reason",
        expected_effect="effect", changed_scope=["src/new.py"],
        resources=PivotResourceRequest(additional_agent_calls=1, additional_evaluator_calls=1, notes="bounded"),
    )
    store.save_pivot_proposal(proposal, "awaiting operator")
    store.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT")
    before = store.campaign()
    store.close()

    args = parser().parse_args([
        "pivot-decision", "--repo", str(tmp_path), "--state-dir", str(state_dir),
        "--decision", "reject", "--proposal-id", proposal.proposal_id,
        "--digest", proposal.digest, "--request-id", "operator-choice-1",
        "--on-reject", "end_campaign",
    ])
    assert cmd_pivot_decision(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["decision"] == "reject"
    assert receipt["result"]["rejection_action"] == "end_campaign"
    assert receipt["automatic_execution"] is False

    check = CampaignStore(state_dir / "campaign.sqlite3")
    try:
        after = check.campaign()
        assert after["status"] == before["status"] == "PAUSED_NEEDS_INPUT"
        assert after["stop_requested"] == 0
        assert after["agent_calls"] == before["agent_calls"]
        assert after["evaluator_calls"] == before["evaluator_calls"]
    finally:
        check.close()
