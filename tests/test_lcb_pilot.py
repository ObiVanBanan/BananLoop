from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from examples.livecodebench.pilot import (
    export_final, materialize, read_public_records, run_arm, stdin_samples,
)


def _row(qid: str = "abc123_a") -> dict:
    return {
        "question_id": qid, "question_title": "Echo value",
        "question_content": "Read a line and print it in uppercase.",
        "platform": "atcoder", "difficulty": "hard", "starter_code": "",
        "metadata": json.dumps({"func_name": None}),
        "public_test_cases": json.dumps([{"input": "hello\n", "output": "HELLO\n"}]),
    }


def _jsonl(path: Path, *items: dict) -> Path:
    path.write_text("".join(json.dumps(x) + "\n" for x in items), encoding="utf-8")
    return path


def test_rejects_private_fields_before_writing_any_project(tmp_path: Path):
    bad = {**_row(), "private_test_cases": "do not leak"}
    path = _jsonl(tmp_path / "in.jsonl", bad)
    with pytest.raises(ValueError, match="unapproved fields"):
        materialize(path, tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_skips_function_tasks_and_unsupported_samples():
    assert stdin_samples({**_row(), "metadata": '{"func_name":"solve"}'}) is None
    assert stdin_samples({**_row(), "public_test_cases": "[]"}) is None


def test_public_only_fixture_runs_dev_proxy_and_hides_private_data(tmp_path: Path):
    path = _jsonl(tmp_path / "public.jsonl", _row())
    root = tmp_path / "pilot"
    manifest = materialize(path, root, limit=1)
    assert manifest["format"] == "bananloop-lcb-public-v1"
    project = root / "projects" / manifest["cases"][0]["key"]
    assert not any("private_test_cases" in f.read_text(encoding="utf-8")
                   for f in project.rglob("*") if f.is_file() and f.suffix in (".py", ".md", ".json"))
    cmd = [sys.executable, "public_eval.py"]
    baseline = subprocess.run(cmd, cwd=project, check=True, text=True, capture_output=True)
    assert json.loads(baseline.stdout)["metrics"]["public_pass_rate"] == 0.0
    (project / "solution.py").write_text(
        "print(input().upper())\n", encoding="utf-8")
    scored = subprocess.run(cmd, cwd=project, check=True, text=True, capture_output=True)
    result = json.loads(scored.stdout)
    assert result["metrics"]["public_pass_rate"] == 1.0
    assert result["checks"]["sample_suite"] is True
    assert len(result["protocol_sha256"]) == 64


def test_export_reads_saved_champion_not_source_checkout(tmp_path: Path):
    root = tmp_path / "pilot"
    manifest = materialize(_jsonl(tmp_path / "in.jsonl", _row()), root)
    case = manifest["cases"][0]
    work = root / "states" / case["key"] / "control" / "workspace"
    work.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    (work / "solution.py").write_text("print(input().upper())\n", encoding="utf-8")
    subprocess.run(["git", "add", "solution.py"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "solution"], cwd=work, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True,
                         capture_output=True, text=True).stdout.strip()
    db = work.parent / "campaign.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE campaigns "
                     "(id INTEGER PRIMARY KEY, status TEXT, champion_snapshot TEXT, "
                     "agent_calls INTEGER, evaluator_calls INTEGER)")
        conn.execute("INSERT INTO campaigns VALUES (1, 'GOAL_MET', ?, 3, 2)", (sha,))
    report = export_final(root, "control")
    assert report["count"] == 1
    saved = json.loads((root / "predictions_control.json").read_text(encoding="utf-8"))
    assert saved == [{"question_id": case["question_id"],
                      "code_list": ["print(input().upper())"]}]


def test_only_fresh_arm_has_opt_in_flag(tmp_path: Path, monkeypatch):
    root = tmp_path / "pilot"
    manifest = materialize(_jsonl(tmp_path / "public.jsonl", _row()), root)
    calls = []
    def fake_run(argv, *, cwd, log, timeout):
        calls.append(argv)
    monkeypatch.setattr("examples.livecodebench.pilot._run_command", fake_run)
    from argparse import Namespace
    args = Namespace(max_experiments=3, max_agent_calls=9, deadline_seconds=120,
                     plateau=2, agent="codex", model=None, provider=None,
                     agent_cmd=None)
    run_arm(root, manifest["cases"][0], "control", args)
    run_arm(root, manifest["cases"][0], "fresh", args)
    assert "--fresh-perspective-on-reframe" not in calls[0]
    assert "--fresh-perspective-on-reframe" in calls[2]
    assert [call[1] for call in calls] == ["init", "run", "init", "run"]


def test_streaming_export_projects_public_fields_only():
    from examples.livecodebench.export_public import sanitized
    raw = {**_row(), "private_test_cases": "SECRET_EXPECTED_OUTPUT"}
    public = sanitized(raw, difficulty="hard")
    assert public is not None
    assert "private_test_cases" not in public
    assert "SECRET_EXPECTED_OUTPUT" not in json.dumps(public)
    assert sanitized(raw, difficulty="easy") is None
