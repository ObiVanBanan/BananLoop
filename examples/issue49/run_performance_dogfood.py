"""Run a frozen CPU p95/throughput campaign through the public BananLoop CLI."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


BASELINE_SOURCE = '''from __future__ import annotations

def lookup(rows: list[dict[str, str]], queries: list[str]) -> list[str | None]:
    result = []
    for query in queries:
        found = None
        for row in rows:
            if row["key"] == query:
                found = row["value"]
                break
        result.append(found)
    return result
'''


AGENT_SOURCE = r'''import json, sys
from pathlib import Path

task = json.load(sys.stdin)
if task.get("role") == "propose":
    candidate = {
        "candidate_id": "z-build-index-once",
        "hypothesis": {
            "hypothesis": "Build a key index once per lookup call instead of scanning all rows for every query",
            "rationale": "The fixed workload has many queries over the same rows; an index changes repeated linear scans to one row pass plus constant-time lookups.",
            "expected_effect": "Lower p95 lookup latency and no throughput/correctness regression",
            "files_or_area": ["src/bench.py"],
            "falsification_condition": "p95 latency does not improve by the configured minimum or any correctness check fails",
            "mechanism_key": "single-pass-key-index",
            "relation": "new",
            "prior_experiment_refs": [],
            "novelty_rationale": "The baseline repeats a full row scan for each query.",
        },
        "evidence_refs": ["INSUFFICIENT_EVIDENCE"],
        "suspected_failure_mode": "repeated nested scan dominates lookup latency",
        "predicted_observable": "lower latency_p95_ms and higher throughput_rps",
        "discriminating_test": "same frozen workload, evaluator and runtime before and after the indexed lookup",
        "scope_eligibility": True,
        "estimated_agent_calls": 1,
        "estimated_evaluator_calls": 1,
        "estimated_work_units": None,
        "rationale": "one bounded algorithmic change with a direct timing discriminator",
        "uncertainty": "single local CPU environment; no cross-machine claim",
        "inference": "agent_inference",
    }
    print(json.dumps({"candidates": [candidate]}))
else:
    Path("src/bench.py").write_text(
        "from __future__ import annotations\n\n"
        "def lookup(rows: list[dict[str, str]], queries: list[str]) -> list[str | None]:\n"
        "    index = {row['key']: row['value'] for row in rows}\n"
        "    return [index.get(query) for query in queries]\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "summary": "replace repeated row scans with one-pass dictionary indexing",
        "changed_paths": ["src/bench.py"],
        "verification_notes": "frozen CPU benchmark evaluator runs independently",
        "blockers": [],
    }))
'''


EVALUATOR_SOURCE = '''import json, math, os, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.bench import lookup

tier = os.environ.get("BANANLOOP_EVALUATION_TIER", "dev")
workload = json.loads((Path(__file__).resolve().parent / "data" / f"{tier}.json").read_text(encoding="utf-8"))
rows, queries, expected = workload["rows"], workload["queries"], workload["expected"]
latencies = []
observed = None
for _ in range(40):
    start = time.perf_counter_ns()
    result = lookup(rows, queries)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    latencies.append(elapsed_ms)
    observed = result
correct = observed == expected
p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
first_half_p95 = sorted(latencies[:20])[math.ceil(0.95 * 20) - 1]
second_half_p95 = sorted(latencies[20:])[math.ceil(0.95 * 20) - 1]
throughput = len(queries) / (statistics.fmean(latencies) / 1000.0)
print(json.dumps({
    "status": "ok",
    "metrics": {"latency_p95_ms": p95, "throughput_rps": throughput, "correctness": float(correct)},
    "diagnostics": {"aa_half_p95_abs_delta_ms": abs(first_half_p95 - second_half_p95)},
    "checks": {"correctness": correct, "fixed_repetitions": len(latencies) == 40},
}, sort_keys=True))
'''


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, capture_output=True)


def write_workload(path: Path, *, offset: int) -> None:
    rows = [{"key": f"K{i:05}", "value": f"V{i:05}"} for i in range(1200)]
    queries = [f"K{(i * 13 + offset) % len(rows):05}" for i in range(240)]
    expected = [f"V{int(query[1:]):05}" for query in queries]
    path.write_text(json.dumps({"rows": rows, "queries": queries, "expected": expected}), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True, help="new or empty fixture/state directory")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {work}")
    project, state = work / "project", work / "state"
    evaluation = project / ".bananloop_eval"
    (project / "src").mkdir(parents=True)
    (evaluation / "data").mkdir(parents=True)
    (project / "src" / "bench.py").write_text(BASELINE_SOURCE, encoding="utf-8")
    (project / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8")
    (evaluation / "evaluate.py").write_text(EVALUATOR_SOURCE, encoding="utf-8")
    write_workload(evaluation / "data" / "dev.json", offset=0)
    write_workload(evaluation / "data" / "validation.json", offset=3)
    write_workload(evaluation / "data" / "holdout.json", offset=7)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cli = [sys.executable, "-m", "bananloop.cli"]
    run(["git", "init"], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "add", "."], cwd=project, env=env)
    run(["git", "-c", "user.name=BananLoop dogfood", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "frozen p95 performance fixture"], cwd=project, env=env)

    run(cli + [
        "init", "--repo", str(project), "--state-dir", str(state),
        "--goal", "Reduce frozen workload p95 lookup latency while preserving exact results and throughput",
        "--eval-cmd", "python .bananloop_eval/evaluate.py",
        "--validation-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--holdout-eval-cmd", "python .bananloop_eval/evaluate.py",
        "--eval-asset", ".bananloop_eval/evaluate.py",
        "--eval-asset", ".bananloop_eval/data/dev.json",
        "--eval-asset", ".bananloop_eval/data/validation.json",
        "--eval-asset", ".bananloop_eval/data/holdout.json",
        "--agent-hide", ".bananloop_eval/**",
        "--metric", "latency_p95_ms", "--direction", "minimize", "--target", "1.0",
        "--min-improvement", "0.1", "--guard", "correctness>=1",
        "--check", "correctness", "--check", "fixed_repetitions",
        "--non-regression", "throughput_rps:maximize:0",
        "--editable", "src/bench.py", "--protect", "agent.py", "--protect", ".bananloop_eval/**",
        "--max-experiments", "1", "--max-attempts", "2", "--max-agent-calls", "4",
        "--max-evaluator-calls", "6", "--deadline-seconds", "180",
        "--agent", "command", "--agent-cmd", "python agent.py", "--yes",
    ], cwd=project, env=env)
    run(cli + ["run", "--repo", str(project), "--state-dir", str(state)], cwd=project, env=env)
    status = json.loads(run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    report = json.loads(run(cli + ["report", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project, env=env).stdout)
    dev = [item for item in status["evaluations"] if item.get("tier") == "dev"]
    protocol = report["evaluation_protocol"]
    dev_measurements = [item for item in report["measurements"] if item.get("tier") == "dev"]
    print(json.dumps({
        "domain": "cpu_performance_p95",
        "bananloop_core_commit": run(["git", "rev-parse", "HEAD"], cwd=ROOT, env=env).stdout.strip(),
        "fixture_commit": run(["git", "rev-parse", "HEAD"], cwd=project, env=env).stdout.strip(),
        "campaign_id": status["campaign_id"],
        "status": status["status"], "stop_reason": status.get("stop_reason"),
        "campaign_calls": {"agent": status["agent_calls"], "evaluator": status["evaluator_calls"]},
        "campaign_budget": {"experiments": 1, "agent_calls": 4, "evaluator_calls": 6},
        "dev_metrics": [item["metrics"] for item in dev],
        "dev_aa_calibration": [
            {"experiment_id": item["experiment_id"], "diagnostics": item["result"]["diagnostics"]}
            for item in dev_measurements
        ],
        "validation": protocol["validation"],
        "final_assessment": report.get("final_assessment"),
        "holdout": protocol["holdout"],
        "selected_mechanism": report["research_state"]["portfolios"][0]["selected_candidate_id"],
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
