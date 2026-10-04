from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from bananloop.bootstrap import BootstrapPairedPlan, BootstrapPlan
from bananloop.readiness import ReadinessError, run_bootstrap_readiness


def plan(command: list[str], *, paired: bool = False, min_improvement: float = 0.1) -> BootstrapPlan:
    return BootstrapPlan.model_validate(
        {
            "status": "ready",
            "summary": "fixture",
            "project_context": "fixture context",
            "needs_input": [],
            "evaluation_command": command,
            "smoke_command": command,
            "validation_command": [],
            "holdout_command": [],
            "primary_metric": {
                "name": "quality",
                "direction": "maximize",
                "target": 1.0,
                "min_improvement": min_improvement,
            },
            "guardrails": [],
            "non_regression": [],
            "mandatory_checks": ["tests"],
            "editable_scope": ["**"],
            "protected_paths": [],
            "agent_visible_metrics": ["quality"],
            "agent_visible_diagnostics": [],
            "paired_comparison": {
                "enabled": paired,
                "case_count": 1,
                "repetitions": 1,
                "metric": "quality",
                "direction": "maximize",
                "min_wins": 1,
                "worst_case_tolerance": 0.0,
            },
            "evaluator_timeout_seconds": 30,
        }
    )


def test_readiness_runs_twice_and_records_stable_inputs(tmp_path: Path):
    script = tmp_path / "eval.py"
    input_hash = hashlib.sha256(b"same-input").hexdigest()
    protocol_hash = hashlib.sha256(b"protocol").hexdigest()
    script.write_text(
        "import json\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},"
        f"'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}',"
        f"'cases':[{{'case_id':'one','repetition':0,'input_sha256':'{input_hash}',"
        "'metrics':{'quality':0.5}}]}))\n",
        encoding="utf-8",
    )

    receipt = run_bootstrap_readiness(
        plan=plan([sys.executable, str(script)], paired=True),
        workspace=tmp_path,
        snapshot="abc123",
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["status"] == "PASSED"
    assert len(receipt["runs"]) == 2
    assert receipt["input_identity_stable"] is True
    assert receipt["primary_values"] == [0.5, 0.5]
    assert receipt["primary_range"] == 0.0
    assert receipt["runs"][0]["case_count"] == 1


def test_readiness_rejects_missing_required_metric(tmp_path: Path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'status':'ok','metrics':{'other':1},'checks':{'tests':True}}))\n",
        encoding="utf-8",
    )

    with pytest.raises(ReadinessError, match="missing required metric"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot="abc123",
            state_root=tmp_path / "state",
            attempt=1,
        )


def test_readiness_rejects_failed_mandatory_check(tmp_path: Path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':0.5},'checks':{'tests':False}}))\n",
        encoding="utf-8",
    )

    with pytest.raises(ReadinessError, match="failed mandatory check"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot="abc123",
            state_root=tmp_path / "state",
            attempt=1,
        )


def test_readiness_rejects_unstable_input_identity(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    protocol_hash = hashlib.sha256(b"protocol").hexdigest()
    script.write_text(
        "import hashlib,json\n"
        "from pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "h=hashlib.sha256(f'input-{n}'.encode()).hexdigest()\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},"
        f"'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}',"
        "'cases':[{'case_id':'one','repetition':0,'input_sha256':h,"
        "'metrics':{'quality':0.5}}]}))\n",
        encoding="utf-8",
    )

    with pytest.raises(ReadinessError, match="changed case/input identity"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)], paired=True),
            workspace=tmp_path,
            snapshot="abc123",
            state_root=tmp_path / "state",
            attempt=1,
        )


def test_readiness_rejects_noise_at_or_above_min_improvement(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "value=0.4 if n == 1 else 0.6\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':value},'checks':{'tests':True}}))\n",
        encoding="utf-8",
    )

    with pytest.raises(ReadinessError, match="A/A smoke noise"):
        run_bootstrap_readiness(
            plan=plan(
                [sys.executable, str(script)],
                min_improvement=0.1,
            ),
            workspace=tmp_path,
            snapshot="abc123",
            state_root=tmp_path / "state",
            attempt=1,
        )
