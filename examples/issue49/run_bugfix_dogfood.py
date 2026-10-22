"""Run a real baseline-failing regression fix through the public CLI, offline."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


AGENT_SOURCE = r'''import json, sys
from pathlib import Path

task = json.load(sys.stdin)
if task.get("role") == "propose":
    hypothesis = {
        "hypothesis": "Subtract the percentage discount from the original price",
        "rationale": "The frozen DEV regression expects a 10% discount on 100 to produce 90; current behavior returns the discount amount.",
        "expected_effect": "All tier-specific discount regression cases pass without changing the tests",
        "files_or_area": ["src/discount.py"],
        "falsification_condition": "The same frozen regression tests still fail",
        "mechanism_key": "percentage-discount-arithmetic",
        "relation": "new", "prior_experiment_refs": [], "novelty_rationale": "",
    }
    candidate = {
        "candidate_id": "z-correct-discount-arithmetic", "hypothesis": hypothesis,
        "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
        "suspected_failure_mode": "function returns price * percent instead of price reduced by percent",
        "predicted_observable": "fixed DEV regression passes",
        "discriminating_test": "run unchanged frozen unit test against identical source snapshot",
        "scope_eligibility": True, "estimated_agent_calls": 1, "estimated_evaluator_calls": 1,
        "estimated_work_units": None, "rationale": "minimal arithmetic correction",
        "uncertainty": "three deterministic fixture cases only", "inference": "agent_inference",
    }
    print(json.dumps({"candidates": [candidate]}))
else:
    Path("src/discount.py").write_text(
        "def apply_discount(price: float, percent: float) -> float:\n"
        "    return price * (1.0 - percent / 100.0)\n",
        encoding="utf-8",
    )
    print(json.dumps({"summary": "correct percentage discount calculation", "changed_paths": ["src/discount.py"],
                      "verification_notes": "independent frozen regression suites run by BananLoop", "blockers": []}))
'''


EVALUATOR_SOURCE = '''import json, os, re, subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
tier = os.environ.get("BANANLOOP_EVALUATION_TIER", "dev")
env = os.environ.copy()
env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", f"tests/{tier}"],
    cwd=root, env=env, text=True, capture_output=True, timeout=20,
)
combined = result.stdout + "\\n" + result.stderr
match = re.search(r"Ran (\\d+) tests?", combined)
total = int(match.group(1)) if match else 0
passed = total if result.returncode == 0 else max(0, total - 1)
rate = passed / total if total else 0.0
print(json.dumps({
    "status": "ok" if total else "error",
    "metrics": {"pass_rate": rate, "test_count": total},
    "checks": {"regression_suite": result.returncode == 0},
    "diagnostics": {"tier": tier, "failure_count": int(result.returncode != 0)},
    "resource_units": 1,
}, sort_keys=True))
'''


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, capture_output=True)


def write_test(path: Path, price: float, percent: float, expected: float) -> None:
    path.write_text(
        "import unittest\nfrom src.discount import apply_discount\n\n"
        "class DiscountRegression(unittest.TestCase):\n"
        f"    def test_discount(self):\n        self.assertAlmostEqual(apply_discount({price}, {percent}), {expected}, places=6)\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True, help="new or empty fixture/state directory")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {work}")
    project, state = work / "project", work / "state"
    (project / "src").mkdir(parents=True)
    for tier in ("dev", "validation", "holdout"):
        folder = project / "tests" / tier
        folder.mkdir(parents=True)
        write_test(folder / "test_discount.py", *{
            "dev": (100.0, 10.0, 90.0),
            "validation": (50.0, 20.0, 40.0),
            "holdout": (80.0, 25.0, 60.0),
        }[tier])
    (project / "src" / "discount.py").write_text(
        "def apply_discount(price: float, percent: float) -> float:\n"
        "    return price * percent / 100.0\n",
        encoding="utf-8",
    )
    evaluator_dir = project / ".bananloop_eval"
    evaluator_dir.mkdir()
    (evaluator_dir / "evaluate.py").write_text(EVALUATOR_SOURCE, encoding="utf-8")
    (project / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    run(["git", "init"], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "add", "."], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "frozen discount bugfix fixture"], cwd=project, env=env)
    cli = [sys.executable, "-m", "bananloop.cli"]
    run(cli + [
        "init", "--repo", str(project), "--state-dir", str(state),
        "--goal", "Fix apply_discount so price is reduced by the requested percentage",
        "--eval-cmd", "python .bananloop_eval/evaluate.py",
        "--validation-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--holdout-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--eval-asset", ".bananloop_eval/evaluate.py",
        *[arg for tier in ("dev", "validation", "holdout") for arg in ("--eval-asset", f"tests/{tier}/test_discount.py")],
        "--agent-hide", "tests/validation/**", "--agent-hide", "tests/holdout/**",
        "--metric", "pass_rate", "--direction", "maximize", "--target", "1",
        "--min-improvement", "0.5", "--check", "regression_suite",
        "--editable", "src/discount.py", "--protect", "agent.py", "--protect", ".bananloop_eval/**",
        "--protect", "tests/**", "--max-experiments", "1", "--max-attempts", "2",
        "--max-agent-calls", "4", "--max-evaluator-calls", "6", "--deadline-seconds", "180",
        "--agent", "command", "--agent-cmd", "python agent.py", "--yes",
    ], cwd=project, env=env)
    run(cli + ["run", "--repo", str(project), "--state-dir", str(state)], cwd=project, env=env)
    status = json.loads(run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    report = json.loads(run(cli + ["report", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    measurements = report["measurements"]
    print(json.dumps({
        "domain": "bugfix_regression_reduction",
        "bananloop_core_commit": run(["git", "rev-parse", "HEAD"], cwd=ROOT, env=env).stdout.strip(),
        "fixture_commit": run(["git", "rev-parse", "HEAD"], cwd=project, env=env).stdout.strip(),
        "campaign_id": status["campaign_id"],
        "baseline_fails_dev_regression": measurements[0]["result"]["metrics"]["pass_rate"]["value"] == 0,
        "status": status["status"], "stop_reason": status.get("stop_reason"),
        "campaign_calls": {"agent": status["agent_calls"], "evaluator": status["evaluator_calls"]},
        "campaign_budget": {"experiments": 1, "attempts": 2, "agent_calls": 4, "evaluator_calls": 6},
        "measurements": [
            {"tier": item["tier"], "experiment_id": item["experiment_id"],
             "pass_rate": item["result"]["metrics"]["pass_rate"]["value"],
             "regression_suite": item["result"]["checks"]["regression_suite"]["passed"]}
            for item in measurements
        ],
        "validation": report["evaluation_protocol"]["validation"],
        "holdout": report["evaluation_protocol"]["holdout"],
        "final_assessment": report["final_assessment"],
        "changed_paths": report["experiments"][0]["candidate_proof"]["changed_paths"],
        "dev_receipts": [
            {"action_id": item["action_id"], "receipt_hash": item["receipt_hash"]}
            for item in report["evaluation_receipts"] if item["tier"] == "dev"
        ],
        "evaluation_receipts": [
            {"tier": item["tier"], "action_id": item["action_id"], "receipt_hash": item["receipt_hash"]}
            for item in report["evaluation_receipts"]
        ],
        "real_model_calls": 0,
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
