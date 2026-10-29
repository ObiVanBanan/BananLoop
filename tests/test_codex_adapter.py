from __future__ import annotations

import json
from pathlib import Path

from bananloop.agents.codex import BOOTSTRAP_SCHEMA, HYPOTHESIS_SCHEMA, CodexAdapter, SELECTION_SCHEMA, selection_schema_for_task


def test_bootstrap_protocol_manifest_schema_is_strict_and_total():
    properties = BOOTSTRAP_SCHEMA["properties"]
    assert properties["agent_visible_training_paths"]["type"] == "array"
    assert "agent_visible_training_paths" in BOOTSTRAP_SCHEMA["required"]
    manifest_schema = properties["protocol_manifest_commands"]
    assert manifest_schema["required"] == ["dev", "validation", "holdout"]
    assert manifest_schema["additionalProperties"] is False


def test_codex_propose_schema_accepts_portfolio_and_legacy_hypothesis(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    captured = {}

    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        captured["schema"] = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        Path(argv[argv.index("-o") + 1]).write_text('{"candidates":[]}', encoding="utf-8")
        return Result()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)
    out = adapter.execute({"role": "propose"}, tmp_path, timeout=30)

    assert captured["schema"] == HYPOTHESIS_SCHEMA
    assert captured["schema"]["type"] == "object"
    assert "oneOf" not in captured["schema"]
    assert captured["schema"]["properties"]["candidates"]["type"] == ["array", "null"]
    assert "estimated_evaluator_calls" in captured["schema"]["properties"]["candidates"]["items"]["required"]
    assert set(captured["schema"]["required"]) == set(captured["schema"]["properties"])
    assert out == {"candidates": []}


def test_codex_strict_schema_legacy_null_slots_normalize_as_legacy_hypothesis():
    from bananloop.research import normalize_portfolio

    payload = {
        "candidates": None,
        "hypothesis": "preserve blank lines",
        "rationale": "current formatter collapses them",
        "expected_effect": "blank-line count is preserved",
        "files_or_area": ["formatter.py"],
        "falsification_condition": "blank-line count still changes",
        "mechanism_key": "newline-handling",
        "relation": "new",
        "prior_experiment_refs": [],
        "novelty_rationale": "new mechanism",
        "target_failure_signal": "",
        "failure_mechanism": "",
    }
    portfolio = normalize_portfolio(payload)
    assert len(portfolio.candidates) == 1
    assert portfolio.candidates[0].hypothesis.mechanism_key == "newline-handling"


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


def test_semantic_selection_schema_restricts_model_to_eligible_ids_and_exact_refs():
    request = {"role": "select", "eligible_candidates": [
        {"candidate_id": "negation", "evidence_refs": ["exp:1", "exp:2"]},
        {"candidate_id": "synonym", "evidence_refs": ["exp:3", "INSUFFICIENT_EVIDENCE"]},
    ]}
    schema = selection_schema_for_task(request)
    assert schema["properties"]["selected_candidate_id"]["enum"] == ["negation", "synonym"]
    assert schema["properties"]["evidence_refs"]["items"]["enum"] == [
        "INSUFFICIENT_EVIDENCE", "exp:1", "exp:2", "exp:3"
    ]
    assert "enum" not in SELECTION_SCHEMA["properties"]["selected_candidate_id"]
    assert "enum" not in SELECTION_SCHEMA["properties"]["evidence_refs"]["items"]


def test_codex_select_prompt_and_schema_allow_exact_refs_not_annotated_prose(tmp_path: Path, monkeypatch):
    adapter = CodexAdapter(model="gpt-6-luna")
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    recorded = {}

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        recorded["prompt"] = input_text
        recorded["schema"] = json.loads(
            Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8")
        )
        recorded["sandbox"] = argv[argv.index("--sandbox") + 1]
        Path(argv[argv.index("-o") + 1]).write_text(
            json.dumps({
                "selected_candidate_id": "negation",
                "reason": "An unresolved polarity mismatch is plausible.",
                "evidence_refs": ["exp:1"],
                "main_risk": "Regression on positive queries.",
            }),
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)
    response = adapter.execute({
        "role": "select",
        "eligible_candidates": [
            {"candidate_id": "negation", "evidence_refs": ["exp:1"]},
            {"candidate_id": "synonyms", "evidence_refs": ["exp:2"]},
        ],
        "selection_contract": {
            "allowed_evidence_refs_by_candidate": {
                "negation": ["exp:1"], "synonyms": ["exp:2"]
            },
        },
    }, tmp_path, timeout=30)
    assert response["evidence_refs"] == ["exp:1"]
    assert recorded["sandbox"] == "read-only"
    assert "MACHINE-READABLE" in recorded["prompt"]
    assert "exp:1: ..." in recorded["prompt"]
    assert recorded["schema"]["properties"]["selected_candidate_id"]["enum"] == ["negation", "synonyms"]
    assert recorded["schema"]["properties"]["evidence_refs"]["items"]["enum"] == ["exp:1", "exp:2"]
