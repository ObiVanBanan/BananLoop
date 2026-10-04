from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bananloop.bootstrap import BootstrapPlan, _bootstrap_contract_error, _bootstrap_path, _bootstrap_task, bootstrap_campaign
from bananloop.contracts import AgentConfig, RuntimePaths
from bananloop.store import CampaignStore
from bananloop.wizard import _parse_py_launcher_paths, discover_project


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "value.txt").write_text("0\n", encoding="utf-8")
    (repo / "bootstrap_agent.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "root=Path('.bananloop_eval')\n"
        "root.mkdir(exist_ok=True)\n"
        "(root/'evaluator.py').write_text("
        "\"import json\\nprint(json.dumps({'status':'ok','metrics':{'quality':0.0}}))\\n\""
        ")\n"
        "print(json.dumps({"
        "'status':'ready',"
        "'summary':'fixture campaign',"
        "'project_context':'value.txt is the production lever; evaluator.py scores it',"
        "'needs_input':[],"
        "'evaluation_command':[" + repr(sys.executable) + ",'.bananloop_eval/evaluator.py'],"
        "'validation_command':[],"
        "'holdout_command':[],"
        "'primary_metric':{'name':'quality','direction':'maximize','target':1.0,'min_improvement':0.1},"
        "'guardrails':[],"
        "'non_regression':[],"
        "'mandatory_checks':[],"
        "'editable_scope':['value.txt'],"
        "'protected_paths':[],"
        "'agent_visible_metrics':['quality'],"
        "'agent_visible_diagnostics':[],"
        "'paired_comparison':{'enabled':False,'case_count':1,'repetitions':1,'metric':'quality','direction':'maximize','min_wins':1,'worst_case_tolerance':0.0},"
        "'evaluator_timeout_seconds':60"
        "}))\n",
        encoding="utf-8",
    )
    git(["init"], repo)
    git(["config", "user.email", "test@example.com"], repo)
    git(["config", "user.name", "Test"], repo)
    git(["add", "."], repo)
    git(["commit", "-m", "baseline"], repo)
    return repo, git(["rev-parse", "HEAD"], repo)


def test_bootstrap_plan_rejects_ambiguous_needs_input():
    payload = {
        "status": "needs_input",
        "summary": "missing refs",
        "project_context": "",
        "needs_input": [],
        "evaluation_command": [],
        "validation_command": [],
        "holdout_command": [],
        "primary_metric": {
            "name": "quality",
            "direction": "maximize",
            "target": 1.0,
            "min_improvement": 0.1,
        },
        "guardrails": [],
        "non_regression": [],
        "mandatory_checks": [],
        "editable_scope": ["**"],
        "protected_paths": [],
        "agent_visible_metrics": [],
        "agent_visible_diagnostics": [],
        "paired_comparison": {
            "enabled": False,
            "case_count": 1,
            "repetitions": 1,
            "metric": "quality",
            "direction": "maximize",
            "min_wins": 1,
            "worst_case_tolerance": 0.0,
        },
        "evaluator_timeout_seconds": 60,
    }
    try:
        BootstrapPlan.model_validate(payload)
    except ValueError as exc:
        assert "must explain what input is missing" in str(exc)
    else:
        raise AssertionError("needs_input without actionable input must fail")


def test_bootstrap_path_is_restricted_to_private_evaluator_area():
    assert _bootstrap_path(".bananloop_eval/evaluator.py")
    assert _bootstrap_path(".bananloop_eval/config/cases.json")
    assert not _bootstrap_path("src/product.py")
    assert not _bootstrap_path("tests/test_product.py")


def test_repo_goal_bootstrap_creates_campaign_without_touching_source(tmp_path: Path):
    repo, source_base = make_repo(tmp_path)
    paths = RuntimePaths.for_repo(repo, tmp_path / "state")

    plan, spec = bootstrap_campaign(
        repo=repo,
        goal="improve fixture quality",
        paths=paths,
        agent_config=AgentConfig(
            kind="command",
            command=[sys.executable, "bootstrap_agent.py"],
        ),
        max_experiments=2,
        max_attempts=3,
        max_agent_calls=6,
        max_evaluator_calls=4,
        deadline_seconds=600,
    )

    assert plan.status == "ready"
    assert spec is not None
    assert spec.project_context.startswith("value.txt")
    assert spec.base_commit != source_base
    assert ".bananloop_eval/evaluator.py" in {
        asset.path for asset in spec.evaluation_assets
    }
    assert ".bananloop_eval/**" in spec.protected_paths
    assert ".bananloop_eval/**" in spec.agent_hidden_paths

    assert git(["rev-parse", "HEAD"], repo) == source_base
    assert git(["status", "--porcelain"], repo) == ""
    assert not (repo / ".bananloop_eval").exists()

    store = CampaignStore(paths.db)
    try:
        persisted = store.spec()
        assert persisted.approved_hash() == spec.approved_hash()
        assert persisted.primary_metric.name == "quality"
    finally:
        store.close()

    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    assert record["source_base_commit"] == source_base
    assert record["changed_paths"] == [".bananloop_eval/evaluator.py"]
    assert record["bootstrap_request"]["goal"] == "improve fixture quality"
    assert record["bootstrap_request"]["repository"]["detected_python_runtimes"]



def test_py_launcher_output_discovers_windows_python_paths():
    output = """ -V:3.13 *        C:\\Python313\\python.exe
 -V:3.12          C:\\Users\\test\\AppData\\Local\\Programs\\Python\\Python312\\python.exe
"""
    assert _parse_py_launcher_paths(output) == [
        r"C:\Python313\python.exe",
        r"C:\Users\test\AppData\Local\Programs\Python\Python312\python.exe",
    ]


def test_project_discovery_always_reports_current_python(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    discovery = discover_project(repo)

    assert any(
        runtime["executable"] == sys.executable
        for runtime in discovery["python_runtimes"]
    )
    assert all(runtime["version"] for runtime in discovery["python_runtimes"])


def test_bootstrap_task_makes_cli_goal_authoritative(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = _bootstrap_task(
        repo,
        "improve similarity to reference images",
        "deadbeef",
        AgentConfig(kind="codex"),
    )

    assert task["goal"] == "improve similarity to reference images"
    assert task["requirements"]["goal_is_authoritative"] is True
    assert task["requirements"]["do_not_request_goal_file_when_goal_is_present"] is True
    assert task["requirements"]["resolve_conflicting_evaluators_by_goal_semantics"] is True
    assert task["requirements"]["use_detected_runtimes_before_requesting_python_environment"] is True
    assert task["repository"]["detected_python_runtimes"]



def _bootstrap_payload(*, status: str, needs_input: list[str], summary: str) -> dict:
    return {
        "status": status,
        "summary": summary,
        "project_context": "fixture context" if status == "ready" else "",
        "needs_input": needs_input,
        "evaluation_command": [sys.executable, ".bananloop_eval/evaluator.py"] if status == "ready" else [],
        "validation_command": [],
        "holdout_command": [],
        "primary_metric": {
            "name": "quality" if status == "ready" else "",
            "direction": "maximize",
            "target": 1.0 if status == "ready" else 0.0,
            "min_improvement": 0.1,
        },
        "guardrails": [],
        "non_regression": [],
        "mandatory_checks": [],
        "editable_scope": ["value.txt"] if status == "ready" else [],
        "protected_paths": [],
        "agent_visible_metrics": ["quality"] if status == "ready" else [],
        "agent_visible_diagnostics": [],
        "paired_comparison": {
            "enabled": False,
            "case_count": 1,
            "repetitions": 1,
            "metric": "quality" if status == "ready" else "",
            "direction": "maximize",
            "min_wins": 1,
            "worst_case_tolerance": 0.0,
        },
        "evaluator_timeout_seconds": 60,
    }


def test_bootstrap_contract_rejects_request_to_repeat_supplied_goal(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = _bootstrap_task(
        repo,
        "improve similarity to reference images",
        "deadbeef",
        AgentConfig(kind="codex"),
    )
    plan = BootstrapPlan.model_validate(
        _bootstrap_payload(
            status="needs_input",
            needs_input=["Provide the authoritative human goal text."],
            summary="The human goal is not present.",
        )
    )

    error = _bootstrap_contract_error(plan, task)

    assert error is not None
    assert "goal" in error


def test_bootstrap_auto_repairs_false_goal_request(tmp_path: Path):
    repo, source_base = make_repo(tmp_path)
    (repo / "repair_agent.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "marker=Path('.bananloop_eval/repair_seen.txt')\n"
        "marker.parent.mkdir(exist_ok=True)\n"
        "if not marker.exists():\n"
        " marker.write_text('1')\n"
        " print(json.dumps(" + repr(_bootstrap_payload(
            status="needs_input",
            needs_input=["Provide the authoritative human goal text."],
            summary="The human goal is not present.",
        )) + "))\n"
        "else:\n"
        " Path('.bananloop_eval/evaluator.py').write_text("
        "\"import json\\nprint(json.dumps({'status':'ok','metrics':{'quality':0.0}}))\\n\""
        ")\n"
        " print(json.dumps(" + repr(_bootstrap_payload(
            status="ready",
            needs_input=[],
            summary="goal recovered from bootstrap request",
        )) + "))\n",
        encoding="utf-8",
    )
    git(["add", "repair_agent.py"], repo)
    git(["commit", "-m", "add repair fixture"], repo)
    source_base = git(["rev-parse", "HEAD"], repo)
    paths = RuntimePaths.for_repo(repo, tmp_path / "repair-state")

    plan, spec = bootstrap_campaign(
        repo=repo,
        goal="improve similarity to reference images",
        paths=paths,
        agent_config=AgentConfig(
            kind="command",
            command=[sys.executable, "repair_agent.py"],
        ),
        max_experiments=1,
        max_attempts=2,
        max_agent_calls=4,
        max_evaluator_calls=2,
        deadline_seconds=300,
    )

    assert plan.status == "ready"
    assert spec is not None
    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    assert len(record["bootstrap_attempts"]) == 2
    assert record["bootstrap_attempts"][0]["contract_error"]
    assert record["bootstrap_attempts"][1]["contract_error"] is None
    assert record["bootstrap_request"]["goal"] == "improve similarity to reference images"
    assert git(["rev-parse", "HEAD"], repo) == source_base



def test_bootstrap_repairs_failed_core_owned_smoke(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    payload = _bootstrap_payload(
        status="ready",
        needs_input=[],
        summary="fixture evaluator",
    )
    payload["smoke_command"] = [sys.executable, ".bananloop_eval/evaluator.py"]
    agent = repo / "smoke_repair_agent.py"
    agent.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "task=json.load(sys.stdin)\n"
        "root=Path('.bananloop_eval'); root.mkdir(exist_ok=True)\n"
        "if task.get('repair',{}).get('readiness_error'):\n"
        " root.joinpath('evaluator.py').write_text("
        "\"import json\\nprint(json.dumps({'status':'ok','metrics':{'quality':0.2},'checks':{}}))\\n\""
        ")\n"
        "else:\n"
        " root.joinpath('evaluator.py').write_text("
        "\"import json\\nprint(json.dumps({'status':'ok','metrics':{'wrong':0.2},'checks':{}}))\\n\""
        ")\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    git(["add", "smoke_repair_agent.py"], repo)
    git(["commit", "-m", "add smoke repair fixture"], repo)
    source_base = git(["rev-parse", "HEAD"], repo)
    paths = RuntimePaths.for_repo(repo, tmp_path / "smoke-repair-state")

    plan, spec = bootstrap_campaign(
        repo=repo,
        goal="improve fixture quality",
        paths=paths,
        agent_config=AgentConfig(
            kind="command",
            command=[sys.executable, "smoke_repair_agent.py"],
        ),
        max_experiments=1,
        max_attempts=2,
        max_agent_calls=4,
        max_evaluator_calls=2,
        deadline_seconds=300,
    )

    assert plan.status == "ready"
    assert spec is not None
    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    assert len(record["bootstrap_attempts"]) == 2
    assert record["bootstrap_attempts"][0]["readiness"]["status"] == "FAILED"
    assert "missing required metric" in record["bootstrap_attempts"][0]["readiness"]["error"]
    assert record["bootstrap_attempts"][1]["readiness"]["status"] == "PASSED"
    assert record["bootstrap_attempts"][1]["readiness"]["primary_values"] == [0.2, 0.2]
    assert spec.base_commit == record["bootstrap_snapshot"]
    assert git(["rev-parse", "HEAD"], repo) == source_base
    assert git(["status", "--porcelain"], repo) == ""
