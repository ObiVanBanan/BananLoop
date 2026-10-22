"""Run a deterministic classifier-quality campaign through the public BananLoop CLI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from examples.stdlib_classification.create_fixture import create_fixture


AGENT_SOURCE = r'''import json, sys
from pathlib import Path

task = json.load(sys.stdin)
if task.get("role") == "propose":
    def candidate(candidate_id, mechanism, hypothesis, rationale, evaluator_calls):
        return {
            "candidate_id": candidate_id,
            "hypothesis": {
                "hypothesis": hypothesis,
                "rationale": rationale,
                "expected_effect": "balanced accuracy reaches at least 0.98",
                "files_or_area": ["model.py"],
                "falsification_condition": "fixed evaluation balanced accuracy remains below 0.98",
                "mechanism_key": mechanism,
                "relation": "new",
                "prior_experiment_refs": [],
                "novelty_rationale": "",
                "target_failure_signal": "",
                "failure_mechanism": "",
            },
            "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
            "suspected_failure_mode": "baseline ignores two additive features",
            "predicted_observable": "balanced accuracy improves on the frozen evaluator",
            "discriminating_test": "run the unchanged evaluator over the same frozen eval split",
            "scope_eligibility": True,
            "estimated_agent_calls": 1,
            "estimated_evaluator_calls": evaluator_calls,
            "estimated_work_units": None,
            "rationale": rationale,
            "uncertainty": "deterministic synthetic classification fixture only",
            "inference": "agent_inference",
        }
    print(json.dumps({"candidates": [
        candidate("a-ignore-features", "ignore-features", "continue using x1 only", "control alternative", 2),
        candidate("z-additive-boundary", "additive-boundary", "include x2 and weighted x3 in the threshold", "minimal model-boundary correction", 1),
    ]}))
else:
    mechanism = task["hypothesis"]["mechanism_key"]
    if mechanism == "additive-boundary":
        Path("model.py").write_text(
            "from __future__ import annotations\n\n"
            "def predict(features: tuple[float, float, float, float]) -> int:\n"
            "    x1, x2, x3, _x4 = features\n"
            "    return int((x1 + x2 + 0.5 * x3) > 0.0)\n",
            encoding="utf-8",
        )
        changed = ["model.py"]
    else:
        changed = []
    print(json.dumps({
        "summary": "bounded classifier boundary change",
        "changed_paths": changed,
        "verification_notes": "local deterministic fixture",
        "blockers": [],
    }))
'''


EVALUATOR_SOURCE = '''import json
from pathlib import Path
from evaluate import balanced_accuracy

score, count = balanced_accuracy(Path(__file__).resolve().parent / "data" / "eval.csv")
print(json.dumps({
    "status": "ok",
    "metrics": {"balanced_accuracy": score, "count": count},
    "checks": {"fixture": True},
}, sort_keys=True))
'''


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="new or empty scratch directory for the fixture and campaign state")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {work}")
    project = work / "project"
    state = work / "state"
    work.mkdir(parents=True, exist_ok=True)

    fixture = create_fixture(project)
    (project / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8", newline="\n")
    (project / "bananloop_evaluator.py").write_text(EVALUATOR_SOURCE, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "agent.py", "bananloop_evaluator.py"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=BananLoop fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "add deterministic BananLoop adapters"],
        cwd=project, check=True, capture_output=True, text=True,
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cli = [sys.executable, "-m", "bananloop.cli"]
    init = run(cli + [
        "init", "--repo", str(project), "--state-dir", str(state),
        "--goal", "Reach balanced_accuracy >= 0.98 on the frozen deterministic classification fixture",
        "--eval-cmd", "python bananloop_evaluator.py",
        "--eval-asset", "bananloop_evaluator.py", "--eval-asset", "data/eval.csv",
        "--agent-hide", "data/eval.csv", "--metric", "balanced_accuracy", "--direction", "maximize",
        "--target", "0.98", "--min-improvement", "0.01", "--check", "fixture",
        "--editable", "model.py", "--protect", "evaluate.py", "--protect", "bananloop_evaluator.py",
        "--protect", "agent.py", "--protect", "data/eval.csv",
        "--max-experiments", "1", "--max-attempts", "2", "--max-agent-calls", "4",
        "--max-evaluator-calls", "3", "--deadline-seconds", "120",
        "--agent", "command", "--agent-cmd", "python agent.py", "--yes",
    ], cwd=project, env=env)
    execution = run(cli + ["run", "--repo", str(project), "--state-dir", str(state)], cwd=project, env=env)
    status = json.loads(run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"],
                            cwd=project, env=env).stdout)
    report = json.loads(run(cli + ["report", "--repo", str(project), "--state-dir", str(state), "--json"],
                            cwd=project, env=env).stdout)
    portfolio = report["research_state"]["portfolios"][0]
    result = status["evaluations"]
    print(json.dumps({
        "domain": "stdlib binary classification",
        "fixture_commit": fixture["commit"],
        "baseline": fixture["baseline"],
        "status": status["status"],
        "stop_reason": status["stop_reason"],
        "experiment_count": status["experiment_count"],
        "agent_calls": status["agent_calls"],
        "evaluator_calls": status["evaluator_calls"],
        "candidates_considered": len(portfolio["candidates"]),
        "selected_candidate_id": portfolio["selected_candidate_id"],
        "dev_metrics": [item["result"]["metrics"]["balanced_accuracy"]["value"] for item in result],
        "eval_rows_each_run": [item["result"]["metrics"]["count"]["value"] for item in result],
        "init_output": init.stdout.strip(),
        "run_output": execution.stdout.strip(),
        "project": str(project),
        "state": str(state),
        "real_model_calls": 0,
        "cloud_or_rag_calls": 0,
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
