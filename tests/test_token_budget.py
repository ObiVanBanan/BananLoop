"""Issue #60: opt-in model token metering, without exposing private benchmark data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bananloop.contracts import BudgetSpec
from bananloop.agents.codex import CodexAdapter
from bananloop.token_budget import parse_codex_usage, model_tokens


def test_codex_usage_counts_cached_input_once():
    payload = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "x"}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 260_000, "output_tokens": 2_044,
            "cached_input_tokens": 220_000}}),
    ])
    usage = parse_codex_usage(payload)
    assert usage == {"input_tokens": 260000, "output_tokens": 2044,
                     "cached_input_tokens": 220000}
    assert model_tokens(usage) == 262044


@pytest.mark.parametrize("payload", [
    "", '{"type":"turn.started"}',
    '{"type":"turn.completed","usage":{}}',
    '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2,"cached_input_tokens":5}}',
    '{"type":"turn.completed","usage":{"input_tokens":-1,"output_tokens":2}}',
])
def test_missing_or_invalid_codex_usage_is_unknown(payload):
    assert parse_codex_usage(payload) is None


def test_budget_is_opt_in_and_reserves_are_strict():
    legacy = BudgetSpec()
    assert legacy.max_model_tokens is None
    capped = BudgetSpec(max_model_tokens=750000,
                        model_token_stage_reserves={"EXECUTE": 300000})
    assert capped.max_model_tokens == 750000
    with pytest.raises(ValueError):
        BudgetSpec(model_token_stage_reserves={"EXECUTE": 0})
    with pytest.raises(ValueError):
        BudgetSpec(model_token_stage_reserves={"UNKNOWN": 500})


def test_codex_jsonl_metering_contract(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter()
    adapter.meter_tokens = True
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    seen = {}

    class Process:
        stdout = json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 290, "output_tokens": 10,
            "cached_input_tokens": 150}}) + "\n"
        stderr = ""
        returncode = 0

    def fake_run(argv, *, cwd, timeout, **kwargs):
        seen["argv"] = argv
        Path(argv[argv.index("-o") + 1]).write_text(
            json.dumps({"summary": "tested", "changed_paths": [],
                        "verification_notes": "", "blockers": []}),
            encoding="utf-8",
        )
        return Process()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run)
    result = adapter.execute({"role": "implement"}, tmp_path, timeout=30)
    assert result["summary"] == "tested"
    assert "--json" in seen["argv"]
    assert adapter.last_token_usage is not None
    assert model_tokens(adapter.last_token_usage) == 300


def test_codex_jsonl_missing_usage_fails_closed(tmp_path: Path, monkeypatch):
    from bananloop.agents.base import AgentError
    adapter = CodexAdapter()
    adapter.meter_tokens = True
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])

    class Process:
        stdout = '{"type":"turn.started"}\n'
        stderr = ""
        returncode = 0

    def fake_run(argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_text("{}", encoding="utf-8")
        return Process()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run)
    with pytest.raises(AgentError, match="usage receipt"):
        adapter.execute({"role": "implement"}, tmp_path, timeout=30)
