"""Run an offline, disposable Issue #42 campaign through the public CLI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="new or empty scratch directory for the fixture and BananLoop state")
    args = parser.parse_args()
    root = args.work_dir.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    project, state = root / "project", root / "state"
    project.mkdir()
    (project / "value.txt").write_text("0\n", encoding="utf-8")
    (project / "evaluate.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "print(json.dumps({'status':'ok','metrics':{'quality':float(Path('value.txt').read_text())},'checks':{'fixture':True}}))\n",
        encoding="utf-8",
    )
    (project / "agent.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "task=json.load(sys.stdin)\n"
        "if task['role']=='propose':\n"
        " print(json.dumps({'candidates':[\n"
        "  {'candidate_id':'a-expensive','hypothesis':{'hypothesis':'try a broad rewrite','rationale':'control','expected_effect':'quality up','files_or_area':['value.txt'],'falsification_condition':'quality unchanged','mechanism_key':'broad-rewrite'},'evidence_refs':['INSUFFICIENT_EVIDENCE'],'suspected_failure_mode':'unknown','predicted_observable':'quality','discriminating_test':'same fixed evaluator','scope_eligibility':True,'estimated_agent_calls':1,'estimated_evaluator_calls':2,'estimated_work_units':None,'rationale':'control alternative','uncertainty':'unmeasured','inference':'agent_inference'},\n"
        "  {'candidate_id':'z-direct-fix','hypothesis':{'hypothesis':'set the measured value to target','rationale':'minimal bounded edit','expected_effect':'quality reaches target','files_or_area':['value.txt'],'falsification_condition':'quality stays below target','mechanism_key':'direct-value-fix'},'evidence_refs':['INSUFFICIENT_EVIDENCE'],'suspected_failure_mode':'fixture baseline value is zero','predicted_observable':'quality becomes 1','discriminating_test':'evaluate the same fixture input','scope_eligibility':True,'estimated_agent_calls':1,'estimated_evaluator_calls':1,'estimated_work_units':None,'rationale':'smallest testable candidate','uncertainty':'toy fixture only','inference':'agent_inference'}]}))\n"
        "else:\n"
        " Path('value.txt').write_text('1\\n' if task['hypothesis']['mechanism_key']=='direct-value-fix' else '0\\n')\n"
        " print(json.dumps({'summary':'bounded fixture edit','changed_paths':['value.txt'],'verification_notes':'offline fixture','blockers':[]}))\n",
        encoding="utf-8",
    )
    run(["git", "init"], cwd=project)
    run(["git", "config", "user.name", "BananLoop fixture"], cwd=project)
    run(["git", "config", "user.email", "fixture@example.invalid"], cwd=project)
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "issue 42 deterministic fixture"], cwd=project)

    cli = [sys.executable, "-m", "bananloop.cli"]
    init = run(cli + ["init", "--repo", str(project), "--state-dir", str(state),
        "--goal", "Reach quality 1 on the deterministic fixture", "--eval-cmd", "python evaluate.py",
        "--metric", "quality", "--direction", "maximize", "--target", "1",
        "--min-improvement", "0.1", "--check", "fixture", "--editable", "value.txt",
        "--protect", "evaluate.py", "--protect", "agent.py", "--agent", "command",
        "--agent-cmd", "python agent.py", "--max-experiments", "1", "--max-attempts", "2",
        "--max-agent-calls", "4", "--max-evaluator-calls", "3", "--deadline-seconds", "120"], cwd=project)
    result = run(cli + ["run", "--repo", str(project), "--state-dir", str(state)], cwd=project)
    status = run(cli + ["status", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project)
    report = run(cli + ["report", "--repo", str(project), "--state-dir", str(state), "--json"], cwd=project)
    payload = json.loads(report.stdout)
    state_json = json.loads(status.stdout)
    decisions = [event for event in payload.get("events", []) if event.get("event_type") == "PORTFOLIO_DECISION"]
    print(json.dumps({
        "init": init.stdout.strip(), "run": result.stdout.strip(),
        "status": state_json.get("status"), "stop_reason": state_json.get("stop_reason"),
        "experiment_count": state_json.get("experiment_count"),
        "agent_calls": state_json.get("agent_calls"), "evaluator_calls": state_json.get("evaluator_calls"),
        "selected": payload["research_state"]["portfolios"][0]["selected_candidate_id"],
        "candidates_considered": len(payload["research_state"]["portfolios"][0]["candidates"]),
        "report_contains_portfolio": "Planning portfolios" in run(cli + ["report", "--repo", str(project), "--state-dir", str(state)], cwd=project).stdout,
        "fixture_path": str(project), "state_path": str(state),
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

