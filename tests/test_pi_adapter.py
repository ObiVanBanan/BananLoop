from __future__ import annotations

import sys
from pathlib import Path

import pytest

import bananloop.agents.pi as pi_module
from bananloop.agents.base import AgentError
from bananloop.agents.pi import PiAdapter


FAKE_PI = r"""
import json
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("fake-pi 1.0")
    raise SystemExit(0)

result = None
bad_json = "--bad-json" in sys.argv

for raw in sys.stdin:
    req = json.loads(raw)
    kind = req.get("type")
    if kind == "prompt":
        print(json.dumps({
            "id": req.get("id"),
            "type": "response",
            "command": "prompt",
            "success": True,
        }), flush=True)
        message = req.get("message", "")
        if '"role": "propose"' in message:
            result = {
                "hypothesis": "increase fixture value",
                "rationale": "value drives quality",
                "expected_effect": "quality increases",
                "files_or_area": ["value.txt"],
                "falsification_condition": "quality does not increase",
                "mechanism_key": "increase-value",
            }
        else:
            Path("value.txt").write_text("1\n", encoding="utf-8")
            result = {
                "summary": "increased value",
                "changed_paths": ["value.txt"],
                "verification_notes": "fixture edit completed",
                "blockers": [],
            }
        print(json.dumps({"type": "agent_settled"}), flush=True)
    elif kind == "get_last_assistant_text":
        text = "not-json" if bad_json else json.dumps(result)
        print(json.dumps({
            "id": req.get("id"),
            "type": "response",
            "command": "get_last_assistant_text",
            "success": True,
            "data": {"text": text},
        }), flush=True)
    elif kind == "abort":
        print(json.dumps({
            "id": req.get("id"),
            "type": "response",
            "command": "abort",
            "success": True,
        }), flush=True)
"""


def fake_pi(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_pi.py"
    script.write_text(FAKE_PI, encoding="utf-8")
    return [sys.executable, str(script)]


def test_pi_rpc_propose_and_implement(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "value.txt").write_text("0\n", encoding="utf-8")

    adapter = PiAdapter(
        model="nemotron-3-nano:30b",
        extra_args=["--provider", "ollama-cloud", "--thinking", "off"],
        command=fake_pi(tmp_path),
    )

    propose = adapter.execute(
        {
            "role": "propose",
            "experiment_id": 1,
            "goal": "increase quality",
            "editable_scope": ["value.txt"],
            "protected_paths": [],
        },
        workspace,
        timeout=5,
    )
    assert propose["mechanism_key"] == "increase-value"
    assert (workspace / "value.txt").read_text(encoding="utf-8") == "0\n"

    implement = adapter.execute(
        {
            "role": "implement",
            "experiment_id": 1,
            "goal": "increase quality",
            "hypothesis": propose,
            "editable_scope": ["value.txt"],
            "protected_paths": [],
        },
        workspace,
        timeout=5,
    )
    assert implement["changed_paths"] == ["value.txt"]
    assert (workspace / "value.txt").read_text(encoding="utf-8") == "1\n"


def test_pi_rpc_requires_strict_json_result(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = PiAdapter(
        command=fake_pi(tmp_path),
        extra_args=["--bad-json"],
    )
    with pytest.raises(AgentError, match="strict JSON"):
        adapter.execute(
            {
                "role": "propose",
                "experiment_id": 1,
                "goal": "test",
                "editable_scope": ["**"],
                "protected_paths": [],
            },
            workspace,
            timeout=5,
        )


def test_pi_builds_rpc_command(tmp_path: Path):
    command = fake_pi(tmp_path)
    adapter = PiAdapter(
        model="nemotron-3-nano:30b",
        extra_args=["--provider", "ollama-cloud", "--thinking", "off"],
        command=command,
    )
    argv = adapter._argv()
    assert argv[:2] == command
    assert "--mode" in argv and "rpc" in argv
    assert "--no-session" in argv
    assert "--no-approve" in argv
    assert argv[argv.index("--model") + 1] == "nemotron-3-nano:30b"
    assert argv[argv.index("--provider") + 1] == "ollama-cloud"



def test_pi_wraps_windows_cmd_shim(monkeypatch: pytest.MonkeyPatch):
    shim = r"C:\Users\tester\AppData\Roaming\npm\pi.CMD"
    comspec = r"C:\Windows\System32\cmd.exe"

    monkeypatch.setattr(pi_module.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", comspec)
    monkeypatch.setattr(pi_module.shutil, "which", lambda name: shim if name == "pi" else None)

    adapter = PiAdapter(
        model="nemotron-3-nano:30b",
        extra_args=["--provider", "ollama-cloud", "--thinking", "off"],
    )
    argv = adapter._argv()

    assert argv[:5] == [comspec, "/d", "/s", "/c", shim]
    assert argv[5:9] == ["--mode", "rpc", "--no-session", "--no-approve"]
    assert argv[argv.index("--model") + 1] == "nemotron-3-nano:30b"



def test_pi_normalizes_empty_blocker_sentinels():
    adapter = PiAdapter()
    task = {"role": "implement"}

    assert adapter.normalize_result(task, {"blockers": "None", "verification_notes": None}) == {
        "blockers": [],
        "verification_notes": "",
    }
    assert adapter.normalize_result(task, {"blockers": None})["blockers"] == []
    assert adapter.normalize_result(task, {"blockers": []})["blockers"] == []


def test_pi_does_not_repair_nonempty_blocker_text():
    adapter = PiAdapter()
    data = adapter.normalize_result(
        {"role": "implement"},
        {"blockers": "tests are failing"},
    )
    assert data["blockers"] == "tests are failing"



def test_pi_parses_json_after_closed_think_tag():
    parsed = PiAdapter._parse_final_json(
        'reasoning text</think>{"hypothesis":"set value","rationale":"fixture",'
        '"expected_effect":"quality up","files_or_area":["value.txt"],'
        '"falsification_condition":"quality unchanged","mechanism_key":"set-value"}'
    )
    assert parsed["mechanism_key"] == "set-value"


def test_pi_rejects_prose_before_json_without_think_boundary():
    with pytest.raises(AgentError, match="strict JSON"):
        PiAdapter._parse_final_json(
            'I will return JSON now. {"hypothesis":"set value"}'
        )


def test_pi_rejects_trailing_prose_after_think_json():
    with pytest.raises(AgentError, match="after </think> is not strict JSON"):
        PiAdapter._parse_final_json(
            'reasoning</think>{"hypothesis":"set value"} extra'
        )
