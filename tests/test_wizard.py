from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import bananloop.wizard as wizard
from bananloop.cli import parser
from bananloop.wizard import confirm_spec, discover_project, run_init_wizard


def test_bare_init_parser_allows_wizard_mode():
    args = parser().parse_args(["init"])
    assert args.goal is None
    assert args.eval_cmd is None
    assert args.metric is None
    assert args.target is None
    assert args.min_improvement is None


def test_discovery_reports_common_tests_and_installed_agents(tmp_path: Path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        wizard.shutil,
        "which",
        lambda name: f"/bin/{name}" if name == "pi" else None,
    )

    found = discover_project(tmp_path)

    assert "python -m pytest -q" in found["test_commands"]
    assert "npm test" in found["test_commands"]
    assert found["agents"] == ["pi"]


def test_init_wizard_collects_minimum_campaign_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    answers = iter([
        "Improve fixture quality",
        "python evaluator.py",
        "quality",
        "",
        "1",
        "0.1",
        "",
    ])
    args = Namespace(
        goal=None,
        eval_cmd=None,
        metric=None,
        direction=None,
        target=None,
        min_improvement=None,
        agent=None,
        agent_cmd=None,
    )

    run_init_wizard(
        args,
        tmp_path,
        "abc123",
        input_fn=lambda prompt: next(answers),
    )

    assert args.goal == "Improve fixture quality"
    assert args.eval_cmd == "python evaluator.py"
    assert args.metric == "quality"
    assert args.direction == "maximize"
    assert args.target == 1.0
    assert args.min_improvement == 0.1
    assert args.agent == "codex"


def test_confirm_spec_requires_explicit_yes():
    assert confirm_spec({"goal": "x"}, input_fn=lambda prompt: "yes")
    assert not confirm_spec({"goal": "x"}, input_fn=lambda prompt: "")



def test_init_parser_accepts_evaluator_timeout():
    args = parser().parse_args(["init", "--evaluator-timeout-seconds", "14400"])
    assert args.evaluator_timeout_seconds == 14400



def test_resume_parser_accepts_failed_action_retry():
    args = parser().parse_args(["resume", "--retry-failed-action"])
    assert args.retry_failed_action is True
