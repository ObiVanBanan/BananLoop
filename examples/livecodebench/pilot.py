"""LiveCodeBench public-sample pilot for paired BananLoop campaigns.

This is deliberately NOT an official LiveCodeBench grader. It materializes
stdin-style tasks as repos, runs two BananLoop arms, and exports one final code
generation per task to be graded separately with the official checker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

PUBLIC_FIELDS = frozenset({
    "question_id", "question_title", "question_content", "platform",
    "contest_id", "contest_date", "starter_code", "difficulty",
    "public_test_cases", "metadata",
})
ARMS = ("control", "fresh")
ROOT = Path(__file__).resolve().parent


def read_public_records(path: Path) -> list[dict[str, Any]]:
    """Fail closed if private answers are present in the supplied JSONL."""
    records: list[dict[str, Any]] = []
    ids: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"row {number} is not a JSON object")
        if not set(record) <= PUBLIC_FIELDS:
            raise ValueError(f"row {number} contains unapproved fields (possibly private tests)")
        qid = record.get("question_id")
        if not isinstance(qid, str) or not qid.strip() or qid in ids:
            raise ValueError(f"invalid or duplicated question_id on row {number}")
        ids.add(qid)
        if not isinstance(record.get("question_content"), str):
            raise ValueError(f"row {number} has no question_content")
        records.append(record)
    if not records:
        raise ValueError("no public-only LiveCodeBench records")
    return records


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def stdin_samples(record: dict[str, Any]) -> list[dict[str, str]] | None:
    """Return simple stdin/stdout examples; reject LeetCode function-call tasks."""
    metadata = _decoded(record.get("metadata") or "{}")
    if not isinstance(metadata, dict) or metadata.get("func_name"):
        return None
    samples = _decoded(record.get("public_test_cases") or "[]")
    if not isinstance(samples, list) or not samples:
        return None
    clean = []
    for item in samples:
        if not isinstance(item, dict) or not isinstance(item.get("input"), str) \
                or not isinstance(item.get("output"), str):
            return None
        clean.append({"input": item["input"], "output": item["output"]})
    return clean


def _slug(qid: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", qid).strip("-").lower()[:45] or "item"
    return safe + "-" + hashlib.sha256(qid.encode("utf-8")).hexdigest()[:8]


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8",
                          check=True, capture_output=True).stdout.strip()


def materialize(source_jsonl: Path, work_dir: Path, *, limit: int = 10) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if work_dir.exists() and any(work_dir.iterdir()):
        raise ValueError("work directory must be new or empty")
    records = read_public_records(source_jsonl)
    selected: list[tuple[dict[str, Any], list[dict[str, str]]]] = []
    for record in records:
        samples = stdin_samples(record)
        if samples is None:
            continue
        selected.append((record, samples))
        if len(selected) == limit:
            break
    if not selected:
        raise ValueError("no stdin-style tasks with public samples in this input")

    work_dir.mkdir(parents=True, exist_ok=True)
    items = []
    evaluator = (ROOT / "public_eval.py").read_text(encoding="utf-8")
    for record, samples in selected:
        qid = record["question_id"]
        key = _slug(qid)
        repo = work_dir / "projects" / key
        repo.mkdir(parents=True)
        title = str(record.get("question_title") or qid)
        starter = str(record.get("starter_code") or "")
        readme = f"# {title}\n\nLiveCodeBench ID: {qid}\n\n"
        readme += record["question_content"].strip() + "\n"
        if starter.strip():
            readme += "\n## Starter code\n\n" + starter + "\n"
        readme += "\n## Public examples (training feedback only)\n"
        for sample_index, sample in enumerate(samples[:12], 1):
            readme += (
                f"\n### Example {sample_index}\n"
                f"Input:\n{sample['input']}\n"
                f"Expected output:\n{sample['output']}\n"
            )
        (repo / "README.md").write_text(readme, encoding="utf-8", newline="\n")
        (repo / "solution.py").write_text("# Implement stdin -> stdout solution here.\n",
                                           encoding="utf-8", newline="\n")
        (repo / "public_cases.json").write_text(
            json.dumps(samples[:12], ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
        (repo / "public_eval.py").write_text(evaluator, encoding="utf-8", newline="\n")
        _git(["init", "-q"], repo)
        _git(["add", "README.md", "solution.py", "public_cases.json", "public_eval.py"], repo)
        _git(["-c", "user.name=BananLoop pilot",
              "-c", "user.email=pilot@example.invalid",
              "commit", "-qm", "LCB public-only baseline"], repo)
        items.append({
            "question_id": qid, "key": key, "difficulty": record.get("difficulty"),
            "platform": record.get("platform"), "repo_commit": _git(["rev-parse", "HEAD"], repo),
            "public_samples": min(len(samples), 12),
        })
    output = {
        "format": "bananloop-lcb-public-v1",
        "source_sha256": hashlib.sha256(source_jsonl.read_bytes()).hexdigest(),
        "cases": items, "skipped_or_outside_limit": len(records) - len(selected),
    }
    (work_dir / "manifest.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return output


def _manifest(work_dir: Path) -> dict[str, Any]:
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "bananloop-lcb-public-v1":
        raise ValueError("unsupported pilot manifest")
    return manifest


def _run_command(argv: list[str], *, cwd: Path, log: Path, timeout: int) -> None:
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(completed.stdout + "\nSTDERR:\n" + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"command exited {completed.returncode}; see {log}")


def run_arm(work_dir: Path, case: dict[str, Any], arm: str, args: argparse.Namespace) -> None:
    repo = (work_dir / "projects" / case["key"]).resolve()
    state = (work_dir / "states" / case["key"] / arm).resolve()
    if state.exists() and (state / "campaign.sqlite3").exists():
        raise ValueError(f"state exists: {state}; do not overwrite paired trial results")
    state.mkdir(parents=True, exist_ok=True)
    cli = [sys.executable, "-m", "bananloop.cli"]
    goal = (
        f"Implement the correct general Python 3 solution for LiveCodeBench problem "
        f"{case['question_id']} in solution.py. Read README.md. "
        "Use public samples for feedback, never hard-code sample outputs."
    )
    init = cli + [
        "init", "--repo", str(repo), "--state-dir", str(state),
        "--goal", goal, "--eval-cmd", "python public_eval.py",
        "--eval-asset", "public_cases.json", "--eval-asset", "public_eval.py",
        "--agent-hide", "public_cases.json", "--agent-hide", "public_eval.py",
        "--metric", "public_pass_rate", "--direction", "maximize",
        "--target", "1", "--min-improvement", "0.05",
        "--check", "sample_suite",
        "--editable", "solution.py", "--protect", "README.md",
        "--protect", "public_cases.json", "--protect", "public_eval.py",
        "--max-experiments", str(args.max_experiments),
        "--max-attempts", str(args.max_experiments + 2),
        "--max-agent-calls", str(args.max_agent_calls),
        "--max-evaluator-calls", str(args.max_experiments + 3),
        "--deadline-seconds", str(args.deadline_seconds),
        "--plateau", str(args.plateau),
        "--agent", args.agent, "--yes",
    ]
    if arm == "fresh":
        init.append("--fresh-perspective-on-reframe")
    if args.model:
        init.extend(["--model", args.model])
    if args.provider and args.agent == "pi":
        init.extend(["--provider", args.provider])
    if args.agent == "command":
        if not args.agent_cmd:
            raise ValueError("--agent-cmd required for command agent")
        init.extend(["--agent-cmd", args.agent_cmd])
    _run_command(init, cwd=repo, log=state / "pilot-init.log", timeout=120)
    _run_command(cli + ["run", "--repo", str(repo), "--state-dir", str(state)],
                 cwd=repo, log=state / "pilot-run.log",
                 timeout=args.deadline_seconds + 180)


def export_final(work_dir: Path, arm: str) -> dict[str, Any]:
    manifest = _manifest(work_dir)
    predictions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        root = work_dir / "states" / case["key"] / arm
        db = root / "campaign.sqlite3"
        if not db.exists():
            raise ValueError(f"missing run for {case['question_id']} arm={arm}")
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT status, champion_snapshot, agent_calls, evaluator_calls "
                "FROM campaigns WHERE id = 1"
            ).fetchone()
        if not row or row[0] not in {"GOAL_MET", "STOPPED"}:
            raise ValueError(f"campaign not terminal for {case['question_id']}: {row}")
        status, snapshot, agent_calls, eval_calls = row
        code = _git(["show", f"{snapshot}:solution.py"], root / "workspace")
        predictions.append({"question_id": case["question_id"], "code_list": [code]})
        metrics.append({
            "question_id": case["question_id"], "arm": arm,
            "status": status, "agent_calls": agent_calls,
            "evaluator_calls": eval_calls, "champion_snapshot": snapshot,
        })
    (work_dir / f"predictions_{arm}.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / f"accounting_{arm}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"arm": arm, "count": len(predictions),
            "predictions": str(work_dir / f"predictions_{arm}.json"),
            "accounting": str(work_dir / f"accounting_{arm}.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--public-jsonl", type=Path, required=True)
    prep.add_argument("--work-dir", type=Path, required=True)
    prep.add_argument("--limit", type=int, default=10)
    run = sub.add_parser("run")
    run.add_argument("--work-dir", type=Path, required=True)
    run.add_argument("--arm", choices=("both", *ARMS), default="both")
    run.add_argument("--agent", choices=("pi", "codex", "command"), default="pi")
    run.add_argument("--agent-cmd")
    run.add_argument("--provider")
    run.add_argument("--model")
    run.add_argument("--max-experiments", type=int, default=3)
    run.add_argument("--max-agent-calls", type=int, default=10)
    run.add_argument("--deadline-seconds", type=int, default=1500)
    run.add_argument("--plateau", type=int, default=2)
    export = sub.add_parser("export")
    export.add_argument("--work-dir", type=Path, required=True)
    export.add_argument("--arm", choices=ARMS, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = materialize(args.public_jsonl, args.work_dir, limit=args.limit)
    elif args.command == "run":
        if args.max_experiments < 1 or args.max_agent_calls < 3 or args.deadline_seconds < 1:
            raise ValueError("invalid experimental budgets")
        work_dir = args.work_dir.resolve()
        manifest = _manifest(work_dir)
        for case in manifest["cases"]:
            for arm in (ARMS if args.arm == "both" else (args.arm,)):
                print(f"[LiveCodeBench pilot] {case['question_id']} {arm}", flush=True)
                run_arm(work_dir, case, arm, args)
        result = {"completed": len(manifest["cases"]),
                  "arms": list(ARMS if args.arm == "both" else (args.arm,))}
    else:
        result = export_final(args.work_dir.resolve(), args.arm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
