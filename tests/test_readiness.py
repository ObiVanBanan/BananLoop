from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from bananloop.bootstrap import BootstrapPairedPlan, BootstrapPlan
from bananloop import readiness
from bananloop.contracts import EvaluationResult
from bananloop.readiness import (
    ReadinessError,
    _compare_smoke_full,
    _relative_noise_bound,
    run_bootstrap_readiness,
)




def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitignore").write_text(
        "state/\ncounter.txt\ncase-count\n.smoke-state/\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True, text=True)


def snapshot(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def plan(
    command: list[str],
    *,
    smoke_command: list[str] | None = None,
    paired: bool = False,
    min_improvement: float = 0.1,
    paired_tolerance: float = 0.0,
    signal_sanity: dict | None = None,
    direction: str = "maximize",
) -> BootstrapPlan:
    return BootstrapPlan.model_validate(
        {
            "status": "ready",
            "summary": "fixture",
            "project_context": "fixture context",
            "needs_input": [],
            "evaluation_command": command,
            "smoke_command": smoke_command or command,
            "validation_command": [],
            "holdout_command": [],
            "primary_metric": {
                "name": "quality",
                "direction": direction,
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
                "worst_case_tolerance": paired_tolerance,
            },
            **({"signal_sanity": signal_sanity} if signal_sanity is not None else {}),
            "evaluator_timeout_seconds": 30,
        }
    )


def test_readiness_runs_three_times_and_records_stable_inputs(tmp_path: Path):
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
    init_repo(tmp_path)

    receipt = run_bootstrap_readiness(
        plan=plan([sys.executable, str(script)], paired=True),
        workspace=tmp_path,
        snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["status"] == "PASSED"
    assert len(receipt["runs"]) == 3
    assert receipt["repeat_count"] == 3
    assert receipt["input_identity_stable"] is True
    assert receipt["protocol_identity_stable"] is True
    assert receipt["smoke_full_equivalence"]["status"] == "PASSED"
    assert receipt["smoke_full_equivalence"]["comparison_mode"] == "per_case"
    assert receipt["primary_values"] == [0.5, 0.5, 0.5]
    assert receipt["primary_range"] == 0.0
    assert receipt["calibration"] == {
        "repeat_count": 3,
        "primary_metric": "quality",
        "median": 0.5,
        "empirical_noise_bound": 0.0,
        "metric_noise_bounds": {"quality": 0.0},
        "metric_relative_noise_bounds": {"quality": 0.0},
        "min_improvement": 0.1,
        "noise_gate_passed": True,
        "paired_metric": "quality",
        "paired_empirical_noise_bound": 0.0,
        "paired_noise_threshold": 0.0,
        "paired_noise_gate_passed": True,
    }
    assert receipt["runs"][0]["case_count"] == 1


def test_readiness_records_aa_noise_bounds_for_every_aggregate_metric(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    protocol_hash = hashlib.sha256(b"multi-metric protocol").hexdigest()
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "latency=[1.0,1.5,1.2][(n-1)%3]\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5,'latency':{{'value':latency,'unit':'ms','n':3}}}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}'}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    receipt = run_bootstrap_readiness(
        plan=plan([sys.executable, str(script)], min_improvement=0.1),
        workspace=tmp_path,
        snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["calibration"]["metric_noise_bounds"] == {
        "latency": pytest.approx(0.5),
        "quality": 0.0,
    }
    assert receipt["calibration"]["metric_relative_noise_bounds"] == {
        "latency": pytest.approx(0.5 / 1.2),
        "quality": 0.0,
    }
    assert receipt["smoke_full_equivalence"]["status"] == "INCONCLUSIVE"
    assert receipt["smoke_full_equivalence"]["comparison_mode"] == "aggregate_contract_only"


def test_relative_noise_bound_disables_unstable_near_zero_tolerance():
    assert _relative_noise_bound(noise=0.2, center=0.1) == 0.0
    assert _relative_noise_bound(noise=0.2, center=0.0) == 0.0
    assert _relative_noise_bound(noise=0.2, center=1.0) == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("failure_tier", "completed_before_failure"),
    [("bootstrap_smoke", 0), ("dev", 3)],
)
def test_readiness_persists_failed_smoke_and_full_evaluator_invocations(
    tmp_path: Path,
    failure_tier: str,
    completed_before_failure: int,
    monkeypatch: pytest.MonkeyPatch,
):
    protocol_hash = hashlib.sha256(b"readiness invocation protocol").hexdigest()
    persisted_receipts: list[dict] = []

    def run_evaluator(**kwargs):
        invocation = kwargs["on_evaluator_start"]()
        tier = kwargs["tier"]
        if tier == ("bootstrap_smoke" if failure_tier == "bootstrap_smoke" else "dev"):
            kwargs["on_evaluator_finish"](invocation, "FAILED", "evaluator_error")
            raise readiness.EvaluatorError("forced readiness evaluator failure")
        kwargs["on_evaluator_finish"](invocation, "COMPLETED", None)
        result = EvaluationResult.model_validate(
            {
                "status": "ok",
                "metrics": {"quality": {"value": 0.5, "unit": "score", "n": 1}},
                "checks": {"tests": {"passed": True, "executed": 1, "skipped": 0}},
                "protocol_sha256": protocol_hash,
                "cases": [],
            }
        )
        return result, {"replay": {}, "case_count": 0}

    monkeypatch.setattr(readiness, "_run_readiness_command", run_evaluator)

    with pytest.raises(ReadinessError) as exc:
        run_bootstrap_readiness(
            plan=plan([sys.executable, "fake-evaluator.py"]),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
            on_receipt_update=lambda receipt: persisted_receipts.append(
                json.loads(json.dumps(receipt))
            ),
        )

    invocations = exc.value.receipt["evaluator_invocations"]
    assert len(invocations) == completed_before_failure + 1
    assert [item["status"] for item in invocations[:-1]] == [
        "COMPLETED"
    ] * completed_before_failure
    assert invocations[-1]["status"] == "FAILED"
    assert invocations[-1]["tier"] == (
        "bootstrap_smoke" if failure_tier == "bootstrap_smoke" else "bootstrap_full"
    )
    assert persisted_receipts[-1]["evaluator_invocations"] == invocations


def test_readiness_rejects_smoke_full_metric_drift(tmp_path: Path):
    protocol_hash = hashlib.sha256(b"shared protocol").hexdigest()
    input_hash = hashlib.sha256(b"shared input").hexdigest()
    evaluator = tmp_path / "eval.py"
    evaluator.write_text(
        "import json,sys\n"
        "smoke='--smoke' in sys.argv\n"
        "value=0.4 if smoke else 0.5\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':value}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}','cases':[{{'case_id':'one','repetition':0,'input_sha256':'{input_hash}','metrics':{{'quality':value}}}}]}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="not equivalent") as exc:
        run_bootstrap_readiness(
            plan=plan(
                [sys.executable, str(evaluator)],
                smoke_command=[sys.executable, str(evaluator), "--smoke"],
            ),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
        )

    assert exc.value.receipt["smoke_full_equivalence"]["status"] == "FAILED"
    assert any("metric 'quality' differs" in item for item in exc.value.receipt["smoke_full_equivalence"]["mismatches"])


def test_readiness_accepts_smoke_subset_when_shared_cases_match(tmp_path: Path):
    protocol_hash = hashlib.sha256(b"shared subset protocol").hexdigest()
    input_a = hashlib.sha256(b"input a").hexdigest()
    input_b = hashlib.sha256(b"input b").hexdigest()
    evaluator = tmp_path / "eval.py"
    evaluator.write_text(
        "import json,sys\n"
        "smoke='--smoke' in sys.argv\n"
        f"cases=[{{'case_id':'a','repetition':0,'input_sha256':'{input_a}','metrics':{{'quality':0.5}}}}]\n"
        f"if not smoke: cases.append({{'case_id':'b','repetition':0,'input_sha256':'{input_b}','metrics':{{'quality':0.7}}}})\n"
        "value=0.5 if smoke else 0.6\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':value}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}','cases':cases}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    receipt = run_bootstrap_readiness(
        plan=plan(
            [sys.executable, str(evaluator)],
            smoke_command=[sys.executable, str(evaluator), "--smoke"],
        ),
        workspace=tmp_path,
        snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["smoke_full_equivalence"]["status"] == "PASSED"
    assert receipt["smoke_full_equivalence"]["smoke_case_count"] == 1
    assert receipt["smoke_full_equivalence"]["full_case_count"] == 2
    assert receipt["smoke_full_equivalence"]["compared_case_count"] == 1


def test_smoke_full_equivalence_uses_per_case_aa_noise():
    protocol = hashlib.sha256(b"case noise protocol").hexdigest()
    input_hash = hashlib.sha256(b"case noise input").hexdigest()
    smoke = EvaluationResult.model_validate({
        "status": "ok",
        "metrics": {"quality": 0.5},
        "protocol_sha256": protocol,
        "cases": [{"case_id": "noisy", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": 0.1}}],
    })
    full = EvaluationResult.model_validate({
        "status": "ok",
        "metrics": {"quality": 0.5},
        "protocol_sha256": protocol,
        "cases": [{"case_id": "noisy", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": 0.3}}],
    })

    comparison = _compare_smoke_full(
        smoke,
        full,
        metric_noise_bounds={"quality": 0.0},
        metric_relative_noise_bounds={"quality": 0.0},
        case_metric_noise_bounds={("noisy", 0): {"quality": 0.2}},
        case_metric_relative_noise_bounds={("noisy", 0): {"quality": 1.0}},
    )

    assert comparison["status"] == "PASSED"


@pytest.mark.parametrize("repeats", [1, 6])
def test_readiness_repeat_count_must_stay_bounded(tmp_path: Path, repeats: int):
    with pytest.raises(ValueError, match="bounded between 2 and 5"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, "unused-evaluator.py"]),
            workspace=tmp_path,
            snapshot="snapshot",
            state_root=tmp_path / "state",
            attempt=1,
            repeats=repeats,
        )


def test_readiness_rejects_missing_required_metric(tmp_path: Path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'status':'ok','metrics':{'other':1},'checks':{'tests':True}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="missing required metric"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
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
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="failed mandatory check"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
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
        f"protocol='{protocol_hash}'\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},"
        "'checks':{'tests':True},'protocol_sha256':protocol,"
        "'cases':[{'case_id':'one','repetition':0,'input_sha256':h,"
        "'metrics':{'quality':0.5}}]}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="changed case/input identity"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)], paired=True),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
        )


def test_readiness_rejects_noise_at_or_above_min_improvement(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"protocol='{hashlib.sha256(b'protocol').hexdigest()}'\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "value=0.4 if n == 1 else 0.6\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':value},'checks':{'tests':True},'protocol_sha256':protocol}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="A/A smoke noise"):
        run_bootstrap_readiness(
            plan=plan(
                [sys.executable, str(script)],
                min_improvement=0.1,
            ),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
        )


def test_readiness_rejects_unstable_protocol_identity(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "protocol=('a' if n == 1 else 'b')*64\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':0.5},'checks':{'tests':True},'protocol_sha256':protocol}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="stable protocol identity") as error:
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
        )

    assert error.value.receipt["protocol_identity_stable"] is False
    assert len(error.value.receipt["runs"]) == 3


@pytest.mark.parametrize("direction", ["maximize", "minimize"])
def test_signal_sanity_requires_directed_separation_in_every_aa_run(tmp_path: Path, direction: str):
    script = tmp_path / "eval_signal.py"
    protocol_hash = hashlib.sha256(b"signal protocol").hexdigest()
    input_hash = hashlib.sha256(b"same input").hexdigest()
    cases = [
        {"case_id": "good-a", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": 0.9 if direction == "maximize" else 0.1}},
        {"case_id": "good-b", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": 0.8 if direction == "maximize" else 0.2}},
        {"case_id": "bad-a", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": 0.2 if direction == "maximize" else 0.8}},
    ]
    script.write_text(
        "import json\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}','cases':{json.dumps(cases)}}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)
    ready_plan = plan(
        [sys.executable, str(script)],
        signal_sanity={
            "status": "configured",
            "evidence_kind": "known_examples",
            "metric": "quality",
            "positive_case_ids": ["good-a", "good-b"],
            "negative_case_ids": ["bad-a"],
        },
        direction=direction,
    )
    receipt = run_bootstrap_readiness(
        plan=ready_plan, workspace=tmp_path, snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state", attempt=1,
    )
    signal = receipt["signal_sanity"]
    assert signal["status"] == "DIAGNOSTIC_ONLY"
    assert len(signal["repeat_results"]) == 3
    assert all(run["directed_separation"] >= 0.1 for run in signal["repeat_results"])
    assert len(signal["receipt_sha256"]) == 64


@pytest.mark.parametrize(
    ("metric", "positive", "negative", "included"),
    [
        ("other", 0.9, 0.1, True),  # contract points at a non-primary metric
        ("quality", 0.1, 0.9, True),  # inverted scorer
        ("quality", 0.5, 0.5, True),  # constant scorer
        ("quality", 0.9, 0.1, False),  # a configured case is absent
    ],
)
def test_signal_sanity_rejects_wrong_inverted_constant_or_missing(
    tmp_path: Path, metric: str, positive: float, negative: float, included: bool
):
    script = tmp_path / "eval_signal_bad.py"
    protocol_hash = hashlib.sha256(b"signal protocol").hexdigest()
    input_hash = hashlib.sha256(b"same input").hexdigest()
    cases = [
        {"case_id": "good", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": positive}},
        {"case_id": "bad", "repetition": 0, "input_sha256": input_hash, "metrics": {"quality": negative}},
    ]
    if not included:
        cases.pop()
    script.write_text(
        "import json\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}','cases':{json.dumps(cases)}}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)
    bad_plan = plan(
        [sys.executable, str(script)],
        signal_sanity={
            "status": "configured", "evidence_kind": "known_examples", "metric": metric,
            "positive_case_ids": ["good"], "negative_case_ids": ["bad"],
        },
    )
    with pytest.raises(ReadinessError, match="signal sanity"):
        run_bootstrap_readiness(
            plan=bad_plan, workspace=tmp_path, snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state", attempt=1,
        )


def test_unavailable_signal_sanity_is_explicit_and_does_not_fabricate_examples(tmp_path: Path):
    script = tmp_path / "eval_unavailable.py"
    protocol_hash = hashlib.sha256(b"signal protocol").hexdigest()
    input_hash = hashlib.sha256(b"same input").hexdigest()
    script.write_text(
        "import json\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}','cases':[{{'case_id':'ordinary','repetition':0,'input_sha256':'{input_hash}','metrics':{{'quality':0.5}}}}]}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)
    unavailable_plan = plan(
        [sys.executable, str(script)],
        signal_sanity={
            "status": "unavailable", "reason": "No defensible labeled examples exist.",
            "evidence_kind": None, "metric": None, "positive_case_ids": [], "negative_case_ids": [],
        },
    )
    receipt = run_bootstrap_readiness(
        plan=unavailable_plan, workspace=tmp_path, snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state", attempt=1,
    )
    signal = receipt["signal_sanity"]
    assert signal["status"] == "UNAVAILABLE"
    assert signal["reason"] == "No defensible labeled examples exist."
    assert signal["positive_case_ids"] == signal["negative_case_ids"] == []
    assert signal["receipt_sha256"]


def test_readiness_rejects_evaluator_changing_snapshot(tmp_path: Path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json,subprocess\n"
        "from pathlib import Path\n"
        "Path('snapshot-change.txt').write_text('changed')\n"
        "subprocess.run(['git','add','snapshot-change.txt'],check=True)\n"
        "subprocess.run(['git','commit','-m','evaluator changed HEAD'],check=True,capture_output=True)\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{'c' * 64}'}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)
    expected_snapshot = snapshot(tmp_path)

    with pytest.raises(ReadinessError, match="changed workspace snapshot"):
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)]),
            workspace=tmp_path,
            snapshot=expected_snapshot,
            state_root=tmp_path / "state",
            attempt=1,
        )

    assert snapshot(tmp_path) == expected_snapshot
    assert not (tmp_path / "snapshot-change.txt").exists()


def test_readiness_receipt_compares_empirical_noise_to_min_improvement(tmp_path: Path):
    counter = tmp_path / "counter.txt"
    script = tmp_path / "eval.py"
    protocol_hash = hashlib.sha256(b"protocol").hexdigest()
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "value=[0.45,0.5,0.55][n-1]\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':value}},'checks':{{'tests':True}},'protocol_sha256':'{protocol_hash}'}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    with pytest.raises(ReadinessError, match="A/A smoke noise") as error:
        run_bootstrap_readiness(
            plan=plan([sys.executable, str(script)], min_improvement=0.1),
            workspace=tmp_path,
            snapshot=snapshot(tmp_path),
            state_root=tmp_path / "state",
            attempt=1,
        )

    calibration = error.value.receipt["calibration"]
    assert calibration["repeat_count"] == 3
    assert calibration["median"] == 0.5
    assert calibration["empirical_noise_bound"] == pytest.approx(0.1)
    assert calibration["min_improvement"] == 0.1
    assert calibration["noise_gate_passed"] is False


def test_readiness_repeats_do_not_share_ignored_workspace_state(tmp_path: Path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "state=Path('.smoke-state/count')\n"
        "state.parent.mkdir(exist_ok=True)\n"
        "n=int(state.read_text())+1 if state.exists() else 1\n"
        "state.write_text(str(n))\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5 if n == 1 else 0.1}},'checks':{{'tests':True}},'protocol_sha256':'{'d' * 64}'}}))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    receipt = run_bootstrap_readiness(
        plan=plan([sys.executable, str(script)]),
        workspace=tmp_path,
        snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["status"] == "PASSED"
    assert receipt["primary_values"] == [0.5, 0.5, 0.5]
    assert not (tmp_path / ".smoke-state").exists()


def test_readiness_keeps_tracked_text_evaluator_clean_across_crlf_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = tmp_path / "eval.py"
    script.write_bytes(
        b"import json\n"
        + f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{'9' * 64}'}}))\n".encode()
    )
    init_repo(tmp_path)
    expected_snapshot = snapshot(tmp_path)

    # Git commits this LF text blob while leaving the agent-written worktree in
    # LF. A Windows clone materializes the same tracked blob as CRLF.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    receipt = run_bootstrap_readiness(
        plan=plan([sys.executable, "eval.py"]),
        workspace=tmp_path,
        snapshot=expected_snapshot,
        state_root=tmp_path / "state",
        attempt=1,
    )

    assert receipt["status"] == "PASSED"
    assert snapshot(tmp_path) == expected_snapshot
    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == ""


def test_paired_noise_uses_its_own_scale_tolerance(tmp_path: Path):
    counter = tmp_path / "external-case-count.txt"
    script = tmp_path / "eval.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"count=Path({str(counter)!r})\n"
        "n=int(count.read_text())+1 if count.exists() else 1\n"
        "count.write_text(str(n))\n"
        "case_quality=[100.0,100.2,99.9][(n-1)%3]\n"
        f"payload={{'status':'ok','metrics':{{'quality':0.5}},'checks':{{'tests':True}},'protocol_sha256':'{'e' * 64}'}}\n"
        f"payload['cases']=[{{'case_id':'one','repetition':0,'input_sha256':'{'f' * 64}','metrics':{{'quality':case_quality}}}}]\n"
        "print(json.dumps(payload))\n",
        encoding="utf-8",
    )
    init_repo(tmp_path)

    receipt = run_bootstrap_readiness(
        plan=plan(
            [sys.executable, str(script)],
            paired=True,
            min_improvement=0.001,
            paired_tolerance=0.5,
        ),
        workspace=tmp_path,
        snapshot=snapshot(tmp_path),
        state_root=tmp_path / "state",
        attempt=1,
    )

    calibration = receipt["calibration"]
    assert calibration["paired_empirical_noise_bound"] == pytest.approx(0.3)
    assert calibration["paired_noise_threshold"] == 0.5
    assert calibration["paired_noise_gate_passed"] is True


def test_readonly_external_symlink_is_not_chmodded_during_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sentinel = tmp_path / "external-sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    sentinel.chmod(stat.S_IREAD)
    link = tmp_path / "smoke-workspace" / "external-sentinel.txt"
    link.parent.mkdir()
    try:
        link.symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    original_mode = stat.S_IMODE(sentinel.stat().st_mode)

    def simulate_rmtree_permission_error(path: Path, *, onerror) -> None:
        error = PermissionError("simulated read-only symlink unlink failure")
        onerror(os.unlink, str(link), (PermissionError, error, None))

    monkeypatch.setattr(readiness.shutil, "rmtree", simulate_rmtree_permission_error)

    with pytest.raises(PermissionError, match="refusing to change permissions"):
        readiness._remove_smoke_workspace(link.parent)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert stat.S_IMODE(sentinel.stat().st_mode) == original_mode
