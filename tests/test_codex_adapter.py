from __future__ import annotations

import json
from pathlib import Path

from bananloop.agents.codex import BOOTSTRAP_SCHEMA, CodexAdapter


def test_bootstrap_protocol_manifest_schema_is_strict_and_total():
    properties = BOOTSTRAP_SCHEMA["properties"]
    assert properties["agent_visible_training_paths"]["type"] == "array"
    assert "agent_visible_training_paths" in BOOTSTRAP_SCHEMA["required"]
    manifest_schema = properties["protocol_manifest_commands"]
    assert manifest_schema["required"] == ["dev", "validation", "holdout"]
    assert manifest_schema["additionalProperties"] is False


def test_codex_exec_sends_large_task_via_stdin_not_argv(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter(model="gpt-test", reasoning_effort="high")
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])

    captured = {}

    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        captured["argv"] = list(argv)
        captured["input_text"] = input_text
        result_path = Path(argv[argv.index("-o") + 1])
        result_path.write_text(
            json.dumps(
                {
                    "hypothesis": "bounded change",
                    "rationale": "fixture",
                    "expected_effect": "quality up",
                    "files_or_area": ["value.txt"],
                    "falsification_condition": "no improvement",
                    "mechanism_key": "fixture",
                    "relation": "new",
                    "prior_experiment_refs": [],
                    "novelty_rationale": "new fixture",
                }
            ),
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)

    task = {
        "role": "propose",
        "goal": "x" * 20000,
        "research_state": {"planning_mode": "explore"},
    }
    out = adapter.execute(task, tmp_path, timeout=30)

    assert out["mechanism_key"] == "fixture"
    assert captured["argv"][-1] == "-"
    assert captured["argv"][:2] == ["codex", "exec"]
    assert "--ephemeral" in captured["argv"]
    assert captured["argv"][captured["argv"].index("-m") + 1] == "gpt-test"
    assert captured["argv"][captured["argv"].index("-c") + 1] == "model_reasoning_effort=high"
    assert "x" * 1000 not in " ".join(captured["argv"])
    assert captured["input_text"] is not None
    assert "x" * 20000 in captured["input_text"]
    assert "remove only transient cache artifacts" in captured["input_text"]


def test_implement_prompt_does_not_block_on_supervisor_owned_tests(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    captured = {}

    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        captured["input_text"] = input_text
        Path(argv[argv.index("-o") + 1]).write_text(
            json.dumps({"summary": "implemented", "changed_paths": [], "verification_notes": "pytest unavailable"}),
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)
    adapter.execute(
        {"role": "implement", "hypothesis": {"hypothesis": "bounded change"}},
        tmp_path,
        timeout=30,
    )

    assert "supervisor runs configured evaluator and validation commands independently" in captured["input_text"]

def test_codex_uses_role_scoped_sandbox(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    seen = []

    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        seen.append(list(argv))
        if '"role": "implement"' in (input_text or ""):
            payload = {
                "summary": "ok",
                "changed_paths": [],
                "verification_notes": "",
                "blockers": [],
            }
        else:
            payload = {
                "hypothesis": "x",
                "rationale": "x",
                "expected_effect": "x",
                "files_or_area": [],
                "falsification_condition": "x",
                "mechanism_key": "x",
                "relation": "new",
                "prior_experiment_refs": [],
                "novelty_rationale": "x",
            }
        Path(argv[argv.index("-o") + 1]).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return Result()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)
    adapter.execute({"role": "implement"}, tmp_path, timeout=30)
    adapter.execute({"role": "propose"}, tmp_path, timeout=30)

    assert seen[0][seen[0].index("--sandbox") + 1] == "workspace-write"
    assert seen[1][seen[1].index("--sandbox") + 1] == "read-only"
    assert all("--ephemeral" in argv for argv in seen)
