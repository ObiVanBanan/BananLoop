"""Implement three preregistered portfolio hypotheses, then score frozen CPU DEV."""
from __future__ import annotations

import difflib
import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

from bananloop.agents.codex import CodexAdapter

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "selector_calibration_v1"
FIXTURE = HERE / "fixture"
PORTFOLIO = json.loads((HERE / "results/reframe-1/normalized_portfolio.json").read_text())
PICKS = {
    "old-synonym": "synonym-canonicalization",
    "new-ordered-composition": "ordered-composition",
    "alternative-negation": "negation-polarity",
}


def score(workspace: Path) -> tuple[int, str, str]:
    code = (
        "import runpy; runpy.run_path(" + repr(str(FIXTURE / "evaluator.py"))
        + ", run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=workspace, text=True,
        capture_output=True, timeout=60, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return result.returncode, result.stdout, result.stderr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=RESULTS / "dev_runs")
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    report = []
    baseline = (FIXTURE / "solution.py").read_text(encoding="utf-8")
    public_task = (FIXTURE / "TASK.md").read_text(encoding="utf-8")

    for label, candidate_id in PICKS.items():
        candidate = next(item for item in PORTFOLIO["candidates"]
                         if item["candidate_id"] == candidate_id)
        folder = args.run_dir / label
        workspace = folder / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(FIXTURE / "solution.py", workspace / "solution.py")
        shutil.copyfile(FIXTURE / "TASK.md", workspace / "TASK.md")
        task = {
            "role": "implement",
            "goal": "Improve exact-match technical position matching on the public CPU DEV task.",
            "project_context": "CPU-only Python matcher; editable production file is solution.py.",
            "editable_scope": ["solution.py"],
            "protected_paths": ["evaluator.py"],
            "hypothesis": candidate,
            "task_document": {"status": "available", "path": "TASK.md", "content": public_task},
        }
        (folder / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
        started = time.monotonic()
        response = None
        error = None
        try:
            response = CodexAdapter(model="gpt-6-luna").execute(task, workspace, 900)
            (folder / "response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:  # retain the exact failure and continue independent candidates
            error = f"{type(exc).__name__}: {exc}"
            (folder / "error.txt").write_text(error + "\n")
        elapsed = time.monotonic() - started
        changed = (workspace / "solution.py").read_text(encoding="utf-8")
        diff = "".join(difflib.unified_diff(
            baseline.splitlines(keepends=True), changed.splitlines(keepends=True),
            fromfile="baseline/solution.py", tofile=f"{label}/solution.py",
        ))
        (folder / "solution.diff.json").write_text(
            json.dumps({"unified_diff": diff}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        unexpected = sorted(path.name for path in workspace.iterdir()
                            if path.name not in {"TASK.md", "solution.py"})
        dev_reward = None
        evaluator_error = None
        evaluator_stdout = ""
        evaluator_stderr = ""
        if error is None and not unexpected:
            code, evaluator_stdout, evaluator_stderr = score(workspace)
            if code == 0:
                try:
                    dev_reward = float(evaluator_stdout.strip().split("quality=")[-1])
                except ValueError:
                    evaluator_error = "Could not parse quality from frozen evaluator stdout"
            else:
                evaluator_error = f"evaluator exit code {code}"
        else:
            evaluator_error = "implementer failed" if error else f"unexpected workspace files: {unexpected}"
        (folder / "evaluator.stdout.txt").write_text(evaluator_stdout)
        (folder / "evaluator.stderr.txt").write_text(evaluator_stderr)
        metrics = {
            "hypothesis": candidate_id,
            "mechanism": candidate["hypothesis"]["mechanism_key"],
            "implementer_calls": 1 if response is not None else 0,
            "implementer_response": response,
            "tokens": None,
            "tokens_unavailable_reason": "CodexAdapter structured implementation response does not expose token usage.",
            "dev_reward": dev_reward,
            "elapsed_seconds": elapsed,
            "error": error,
            "evaluator_error": evaluator_error,
            "unexpected_workspace_files": unexpected,
        }
        (folder / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
        report.append(metrics)

    (RESULTS / "dev_smoke.json").write_text(json.dumps({"runs": report}, ensure_ascii=False, indent=2) + "\n")
    lines = ["# DEV implementation smoke", "", "Each candidate used a separate copy of the same baseline. The implementer received only `TASK.md`, `solution.py`, and one frozen hypothesis; `evaluator.py` stayed outside its workspace.", "", "| Hypothesis | Mechanism | DEV reward | Calls | Tokens | Time (s) | Error |", "|---|---|---:|---:|---:|---:|---|"]
    for item in report:
        lines.append(f"| {item['hypothesis']} | {item['mechanism']} | {item['dev_reward']} | {item['implementer_calls']} | unavailable | {item['elapsed_seconds']:.1f} | {item['error'] or item['evaluator_error'] or ''} |")
    lines += ["", "The evaluator has 8 cases, so accuracy changes in steps of 0.125. These results are a smoke check, not reliable generalization estimates. No candidate was selected or revised after seeing DEV results.", ""]
    (RESULTS / "DEV_SMOKE.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"runs": len(report), "results": [
        {key: item[key] for key in ("hypothesis", "dev_reward", "implementer_calls", "tokens", "elapsed_seconds", "error", "evaluator_error")}
        for item in report
    ]}, indent=2))


if __name__ == "__main__":
    main()
