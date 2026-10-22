"""Compare portfolio selection to a first-candidate loop under identical caps."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from run_performance_dogfood import BASELINE_SOURCE, EVALUATOR_SOURCE, ROOT, write_workload

_single_sample = """for _ in range(40):
    start = time.perf_counter_ns()
    result = lookup(rows, queries)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
"""
_batched_sample = """for _ in range(40):
    start = time.perf_counter_ns()
    for _inner in range(5):
        result = lookup(rows, queries)
    elapsed_ms = (time.perf_counter_ns() - start) / 5_000_000
"""
if _single_sample not in EVALUATOR_SOURCE:
    raise RuntimeError("source evaluator sampling loop changed; update comparison deliberately")
EVALUATOR_SOURCE = EVALUATOR_SOURCE.replace(_single_sample, _batched_sample)


AGENT_SOURCE = r'''import json, sys
from pathlib import Path

task = json.load(sys.stdin)
def make_candidate(candidate_id, mechanism, statement, rationale):
    return {
        "candidate_id": candidate_id,
        "hypothesis": {
            "hypothesis": statement,
            "rationale": rationale,
            "expected_effect": "lower p95 lookup latency with exact results and no throughput regression",
            "files_or_area": ["src/bench.py"],
            "falsification_condition": "p95 latency does not improve by at least 5 ms or correctness/throughput regresses",
            "mechanism_key": mechanism,
            "relation": "new", "prior_experiment_refs": [], "novelty_rationale": "",
        },
        "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
        "suspected_failure_mode": "repeated nested scan dominates lookup latency",
        "predicted_observable": "lower latency_p95_ms and higher throughput_rps",
        "discriminating_test": "same frozen 1200-row/240-query workload and evaluator",
        "scope_eligibility": True,
        "estimated_agent_calls": 1, "estimated_evaluator_calls": 1,
        "estimated_work_units": None, "rationale": rationale,
        "uncertainty": "synthetic CPU fixture, one local host", "inference": "agent_inference",
    }

if task.get("role") == "propose":
    print(json.dumps({"candidates": [
        make_candidate("a-linear-scan-rewrite", "linear-scan-rewrite",
            "Rewrite the nested loop using a generator expression while retaining a linear scan per query",
            "lowest-effort control candidate; same asymptotic mechanism"),
        make_candidate("z-build-index-once", "single-pass-key-index",
            "Build a key index once per lookup call instead of scanning all rows for every query",
            "change repeated scans to one row pass plus constant-time lookups"),
    ]}))
elif task.get("role") == "implement":
    mechanism = task["hypothesis"]["mechanism_key"]
    if mechanism == "single-pass-key-index":
        source = (
            "from __future__ import annotations\n\n"
            "def lookup(rows: list[dict[str, str]], queries: list[str]) -> list[str | None]:\n"
            "    index = {row['key']: row['value'] for row in rows}\n"
            "    return [index.get(query) for query in queries]\n"
        )
    else:
        source = (
            "from __future__ import annotations\n\n"
            "def lookup(rows: list[dict[str, str]], queries: list[str]) -> list[str | None]:\n"
            "    return [next((row.get('value') for row in rows if row.get('key') == query), None) for query in queries]\n"
        )
    Path("src/bench.py").write_text(source, encoding="utf-8")
    print(json.dumps({"summary": "apply the selected lookup candidate", "changed_paths": ["src/bench.py"],
                      "verification_notes": "frozen evaluator performs measurement", "blockers": []}))
else:
    raise SystemExit("unsupported role")
'''


def run(argv: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, input=input_text, check=True, text=True, capture_output=True)


def make_project(project: Path, env: dict[str, str]) -> str:
    evaluation = project / ".bananloop_eval"
    (project / "src").mkdir(parents=True)
    (evaluation / "data").mkdir(parents=True)
    (project / "src" / "bench.py").write_text(BASELINE_SOURCE, encoding="utf-8")
    (project / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8")
    (evaluation / "evaluate.py").write_text(EVALUATOR_SOURCE, encoding="utf-8")
    for tier, offset in (("dev", 0), ("validation", 3), ("holdout", 7)):
        write_workload(evaluation / "data" / f"{tier}.json", offset=offset)
    run(["git", "init"], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid", "add", "."], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "matched-budget frozen performance fixture"], cwd=project, env=env)
    return run(["git", "rev-parse", "HEAD"], cwd=project, env=env).stdout.strip()


def evaluate(project: Path, env: dict[str, str], tier: str) -> dict[str, Any]:
    eval_env = env.copy()
    eval_env["BANANLOOP_EVALUATION_TIER"] = tier
    return json.loads(run([sys.executable, ".bananloop_eval/evaluate.py"], cwd=project, env=eval_env).stdout)


def result_hash(result: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True, help="new or empty comparison directory")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {work}")
    portfolio_project = work / "portfolio-project"
    naive_project = work / "naive-project"
    state = work / "portfolio-state"
    naive_project.parent.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    initial_commit = make_project(portfolio_project, env)
    shutil.copytree(portfolio_project, naive_project)

    cli = [sys.executable, "-m", "bananloop.cli"]
    init_args = [
        "init", "--repo", str(portfolio_project), "--state-dir", str(state),
        "--goal", "Reduce fixed-workload p95 lookup latency while preserving exact results and throughput",
        "--eval-cmd", "python .bananloop_eval/evaluate.py",
        "--validation-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--holdout-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--eval-asset", ".bananloop_eval/evaluate.py",
        *[arg for tier in ("dev", "validation", "holdout") for arg in ("--eval-asset", f".bananloop_eval/data/{tier}.json")],
        "--agent-hide", ".bananloop_eval/**",
        "--metric", "latency_p95_ms", "--direction", "minimize", "--target", "1.0",
        "--min-improvement", "5.0", "--guard", "correctness>=1",
        "--check", "correctness", "--check", "fixed_repetitions",
        "--non-regression", "throughput_rps:maximize:0",
        "--editable", "src/bench.py", "--protect", "agent.py", "--protect", ".bananloop_eval/**",
        "--max-experiments", "1", "--max-attempts", "2", "--max-agent-calls", "2",
        "--max-evaluator-calls", "6", "--deadline-seconds", "180",
        "--agent", "command", "--agent-cmd", "python agent.py", "--yes",
    ]
    run(cli + init_args, cwd=portfolio_project, env=env)
    run(cli + ["run", "--repo", str(portfolio_project), "--state-dir", str(state)], cwd=portfolio_project, env=env)
    portfolio_status = json.loads(run(cli + ["status", "--repo", str(portfolio_project), "--state-dir", str(state), "--json"], cwd=portfolio_project, env=env).stdout)
    portfolio_report = json.loads(run(cli + ["report", "--repo", str(portfolio_project), "--state-dir", str(state), "--json"], cwd=portfolio_project, env=env).stdout)
    selected_id = portfolio_report["research_state"]["portfolios"][0]["selected_candidate_id"]

    # The simple loop receives the exact same ordered proposal list but executes its first candidate.
    naive_calls = 0
    baseline = {tier: evaluate(naive_project, env, tier) for tier in ("dev", "validation")}
    proposal_task = {"role": "propose", "goal": "reduce p95 latency", "editable_scope": ["src/bench.py"]}
    proposal = json.loads(run([sys.executable, "agent.py"], cwd=naive_project, env=env,
                              input_text=json.dumps(proposal_task)).stdout)
    naive_calls += 1
    selected = proposal["candidates"][0]
    implementation_task = {"role": "implement", "hypothesis": selected["hypothesis"]}
    run([sys.executable, "agent.py"], cwd=naive_project, env=env,
        input_text=json.dumps(implementation_task))
    naive_calls += 1
    candidate = {tier: evaluate(naive_project, env, tier) for tier in ("dev", "validation")}
    candidate_diff = run(["git", "diff", "--binary", "HEAD", "--", "src/bench.py"], cwd=naive_project, env=env).stdout
    threshold_ms = 5.0
    dev_delta = baseline["dev"]["metrics"]["latency_p95_ms"] - candidate["dev"]["metrics"]["latency_p95_ms"]
    validation_delta = baseline["validation"]["metrics"]["latency_p95_ms"] - candidate["validation"]["metrics"]["latency_p95_ms"]
    naive_accepted = (
        dev_delta >= threshold_ms
        and validation_delta >= threshold_ms
        and candidate["dev"]["metrics"]["correctness"] == 1
        and candidate["validation"]["metrics"]["correctness"] == 1
        and candidate["dev"]["checks"]["fixed_repetitions"]
        and candidate["validation"]["checks"]["fixed_repetitions"]
        and candidate["dev"]["metrics"]["throughput_rps"] >= baseline["dev"]["metrics"]["throughput_rps"]
        and candidate["validation"]["metrics"]["throughput_rps"] >= baseline["validation"]["metrics"]["throughput_rps"]
    )
    final_source = naive_project / "src" / "bench.py"
    if not naive_accepted:
        final_source.write_text(BASELINE_SOURCE, encoding="utf-8")
    naive_holdout = evaluate(naive_project, env, "holdout")
    naive_eval_calls = 5

    memory_total_kib = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory_total_kib = int(line.split()[1])
                break
    except OSError:
        pass
    portfolio_dev_measurements = [item for item in portfolio_report["measurements"] if item["tier"] == "dev"]

    comparison = {
        "method": "controlled local comparison: BananLoop core selects from a two-candidate portfolio; naive loop takes the first candidate. Same fixture commit, frozen tier workloads, command agent, evaluator, target/gates, and maximum budgets.",
        "claim_limit": "one synthetic CPU fixture only; this does not estimate general coding-agent success rates",
        "fixture_commit": initial_commit,
        "resources_observed": {
            "cpu_affinity_count": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count(),
            "memory_total_kib": memory_total_kib,
            "free_disk_bytes": shutil.disk_usage(work).free,
        },
        "matched_budget_caps": {"agent_calls": 2, "evaluator_calls": 6, "experiments": 1},
        "portfolio": {
            "status": portfolio_status["status"],
            "stop_reason": portfolio_status.get("stop_reason"),
            "selected_candidate_id": selected_id,
            "agent_calls": portfolio_status["agent_calls"],
            "evaluator_calls": portfolio_status["evaluator_calls"],
            "outcome": portfolio_report["campaign_outcome"],
            "improvement_status": portfolio_report["improvement_status"],
            "champion_was_promoted": portfolio_report["champion_was_promoted"],
            "dev": [item["metrics"] for item in portfolio_status["evaluations"] if item["tier"] == "dev"],
            "dev_aa_calibration": [item["result"]["diagnostics"] for item in portfolio_dev_measurements],
            "validation": portfolio_report["evaluation_protocol"]["validation"],
            "holdout": portfolio_report["evaluation_protocol"]["holdout"],
            "receipts": [item["receipt_hash"] for item in portfolio_report["evaluation_receipts"]],
        },
        "naive_first_candidate_loop": {
            "selected_candidate_id": selected["candidate_id"],
            "agent_calls": naive_calls,
            "evaluator_calls": naive_eval_calls,
            "candidate_accepted": naive_accepted,
            "candidate_diff_sha256": hashlib.sha256(candidate_diff.encode()).hexdigest(),
            "dev_delta_ms": dev_delta,
            "validation_delta_ms": validation_delta,
            "baseline_aa_half_delta_ms": baseline["dev"]["diagnostics"]["aa_half_p95_abs_delta_ms"],
            "candidate_aa_half_delta_ms": candidate["dev"]["diagnostics"]["aa_half_p95_abs_delta_ms"],
            "accepted_despite_baseline_aa_spread_exceeding_threshold": (
                naive_accepted and baseline["dev"]["diagnostics"]["aa_half_p95_abs_delta_ms"] > threshold_ms
            ),
            "rejection_reasons": [
                reason for reason, passed in {
                    "DEV_MIN_IMPROVEMENT": dev_delta >= threshold_ms,
                    "VALIDATION_MIN_IMPROVEMENT": validation_delta >= threshold_ms,
                    "DEV_CORRECTNESS": candidate["dev"]["metrics"]["correctness"] == 1,
                    "VALIDATION_CORRECTNESS": candidate["validation"]["metrics"]["correctness"] == 1,
                    "DEV_THROUGHPUT": candidate["dev"]["metrics"]["throughput_rps"] >= baseline["dev"]["metrics"]["throughput_rps"],
                    "VALIDATION_THROUGHPUT": candidate["validation"]["metrics"]["throughput_rps"] >= baseline["validation"]["metrics"]["throughput_rps"],
                }.items() if not passed
            ],
            "baseline": {tier: result["metrics"] for tier, result in baseline.items()},
            "candidate": {tier: result["metrics"] for tier, result in candidate.items()},
            "measurement_sha256": {
                "baseline": {tier: result_hash(result) for tier, result in baseline.items()},
                "candidate": {tier: result_hash(result) for tier, result in candidate.items()},
            },
            "final_holdout_champion": naive_holdout["metrics"],
            "candidate_correctness": {tier: result["checks"]["correctness"] for tier, result in candidate.items()},
        },
        "real_model_calls": 0,
    }
    print(json.dumps(comparison, indent=2, sort_keys=True))
    if selected_id != "z-build-index-once":
        raise SystemExit("portfolio did not select the expected deterministic index candidate")
    if (not portfolio_report["champion_was_promoted"]
            or portfolio_status["agent_calls"] != naive_calls
            or portfolio_status["evaluator_calls"] != naive_eval_calls):
        raise SystemExit("matched-budget comparison invariant failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
