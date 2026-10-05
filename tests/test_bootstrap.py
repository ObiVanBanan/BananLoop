from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bananloop.bootstrap import BootstrapError, BootstrapPlan, BootstrapSignalSanity, _bootstrap_contract_error, _bootstrap_path, _bootstrap_task, bootstrap_campaign
from bananloop.contracts import AgentConfig, RuntimePaths
from bananloop.store import CampaignStore
from bananloop.wizard import _parse_py_launcher_paths, discover_project


PROTOCOL_SHA256 = "a" * 64


def test_signal_sanity_contract_rejects_fabricated_or_incomplete_labels():
    with pytest.raises(ValueError, match="explicit reason"):
        BootstrapSignalSanity.model_validate({"status": "unavailable"})
    with pytest.raises(ValueError, match="positive and negative case IDs"):
        BootstrapSignalSanity.model_validate({
            "status": "configured", "evidence_kind": "known_examples", "metric": "quality",
            "positive_case_ids": ["good"], "negative_case_ids": [],
        })
    with pytest.raises(ValueError, match="cannot claim labeled case IDs"):
        BootstrapSignalSanity.model_validate({
            "status": "unavailable", "reason": "No examples.",
            "positive_case_ids": ["good"],
        })
    with pytest.raises(ValueError, match="unique within each group"):
        BootstrapSignalSanity.model_validate({
            "status": "configured", "evidence_kind": "known_examples", "metric": "quality",
            "positive_case_ids": ["good", "good"], "negative_case_ids": ["bad"],
        })


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
        f"\"import json\\nprint(json.dumps({{'status':'ok','metrics':{{'quality':0.0}},'protocol_sha256':'{PROTOCOL_SHA256}'}}))\\n\""
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
    assert record["bootstrap_attempts"][-1]["readiness"]["signal_sanity"]["status"] == "UNAVAILABLE"
    assert record["bootstrap_attempts"][-1]["readiness"]["signal_sanity"]["reason"]
    assert record["bootstrap_request"]["goal"] == "improve fixture quality"
    assert record["bootstrap_request"]["repository"]["detected_python_runtimes"]


def test_bootstrap_agent_failure_persists_sanitized_attempt_receipt(tmp_path, monkeypatch):
    import bananloop.bootstrap as bootstrap_module
    from bananloop.agents.base import AgentError

    repo, _ = make_repo(tmp_path)
    paths = RuntimePaths.for_repo(repo, tmp_path / "state")

    class FailingAgent:
        def probe(self):
            return {"available": True, "kind": "fixture", "version": "1"}

        def execute(self, task, workspace, timeout):
            raise AgentError("provider failure with secret=must-not-persist")

    monkeypatch.setattr(bootstrap_module, "_agent", lambda _config: FailingAgent())
    with pytest.raises(BootstrapError, match="agent call failed") as exc_info:
        bootstrap_campaign(
            repo=repo,
            goal="fix the fixture",
            paths=paths,
            agent_config=AgentConfig(kind="codex"),
        )

    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    failure = record["bootstrap_attempts"][0]["failure"]
    assert failure == {"category": "agent_call_failed", "error_type": "AgentError"}
    assert "must-not-persist" not in json.dumps(record)
    assert "must-not-persist" not in str(exc_info.value)
    assert not paths.db.exists()



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
    assert task["requirements"]["primary_metric_measures_goal_outcome"] is True
    assert task["requirements"]["include_goal_specific_behavioral_probes"] is True
    assert task["requirements"]["tests_are_secondary_regression_checks"] is True
    assert task["requirements"]["candidate_evaluation_uses_candidate_snapshot"] is True
    assert task["requirements"]["do_not_import_or_execute_host_checkout"] is True
    assert task["requirements"]["test_temp_files_use_os_tempdir"] is True
    assert task["requirements"]["do_not_redirect_test_temp_to_artifact_dir"] is True
    assert task["requirements"]["keep_windows_temp_paths_short"] is True
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


def test_bootstrap_repairs_schema_invalid_plan_without_persisting_untrusted_keys(
    tmp_path: Path, monkeypatch
):
    import bananloop.bootstrap as bootstrap_module

    repo, _ = make_repo(tmp_path)
    paths = RuntimePaths.for_repo(repo, tmp_path / "schema-repair-state")
    secret = "INVALID_RESPONSE_SECRET_SENTINEL"
    requests: list[dict] = []

    class RepairingAgent:
        def probe(self):
            return {"available": True, "kind": "fixture", "version": "1"}

        def normalize_result(self, task, result):
            return result

        def execute(self, task, workspace, timeout):
            requests.append(task)
            if len(requests) == 1:
                return {"status": "ready", f"untrusted_{secret}": secret}
            evaluator = workspace / ".bananloop_eval" / "evaluator.py"
            evaluator.parent.mkdir(parents=True, exist_ok=True)
            evaluator.write_text(
                "import json\n"
                f"print(json.dumps({{'status':'ok','metrics':{{'quality':0.0}},'protocol_sha256':'{PROTOCOL_SHA256}'}}))\n",
                encoding="utf-8",
            )
            return _bootstrap_payload(
                status="ready", needs_input=[], summary="repaired structured plan"
            )

    monkeypatch.setattr(bootstrap_module, "_agent", lambda _config: RepairingAgent())
    plan, spec = bootstrap_campaign(
        repo=repo,
        goal="improve fixture quality",
        paths=paths,
        agent_config=AgentConfig(kind="codex"),
        max_experiments=1,
        max_attempts=1,
        max_agent_calls=1,
        max_evaluator_calls=1,
        deadline_seconds=120,
    )

    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    first_failure = record["bootstrap_attempts"][0]["failure"]
    assert plan.status == "ready"
    assert spec is not None
    assert len(requests) == 2
    assert requests[1]["repair"]["validation_errors"]
    assert first_failure["category"] == "invalid_response"
    assert first_failure["validation_errors"] == requests[1]["repair"]["validation_errors"]
    assert secret not in json.dumps(record)
    assert secret not in json.dumps(requests[1])


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


@pytest.mark.parametrize(
    "message_text",
    [
        "Provide a Python interpreter path.",
        "The Python runtime is unavailable.",
        "Укажите путь к интерпретатору Python.",
    ],
)
def test_bootstrap_contract_repairs_request_for_detected_runtime(tmp_path: Path, message_text: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = _bootstrap_task(repo, "Improve quality", "deadbeef", AgentConfig(kind="codex"))
    task["repository"]["detected_python_runtimes"] = [
        {"executable": "C:/Python313/python.exe", "version": "3.13.1"}
    ]
    plan = BootstrapPlan.model_validate(
        _bootstrap_payload(status="needs_input", needs_input=[message_text], summary=message_text)
    )

    assert _bootstrap_contract_error(plan, task) is not None


@pytest.mark.parametrize(
    "message_text",
    [
        "Install the missing package pillow.",
        "Provide the API token.",
        "Provide the missing reference images.",
        "Require Python >=3.14 for this project.",
    ],
)
def test_bootstrap_contract_preserves_real_prerequisites(tmp_path: Path, message_text: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = _bootstrap_task(repo, "Improve quality", "deadbeef", AgentConfig(kind="codex"))
    task["repository"]["detected_python_runtimes"] = [
        {"executable": "C:/Python313/python.exe", "version": "3.13.1"}
    ]
    plan = BootstrapPlan.model_validate(
        _bootstrap_payload(status="needs_input", needs_input=[message_text], summary=message_text)
    )

    assert _bootstrap_contract_error(plan, task) is None


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
        f"\"import json\\nprint(json.dumps({{'status':'ok','metrics':{{'quality':0.0}},'protocol_sha256':'{PROTOCOL_SHA256}'}}))\\n\""
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
    assert spec.signal_sanity_status == "UNAVAILABLE"
    assert len(spec.signal_sanity_receipt_sha256) == 64
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
        f"\"import json\\nprint(json.dumps({{'status':'ok','metrics':{{'quality':0.2}},'checks':{{}},'protocol_sha256':'{PROTOCOL_SHA256}'}}))\\n\""
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
    approved_hash = spec.approved_hash()
    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    assert len(record["bootstrap_attempts"]) == 2
    assert record["bootstrap_attempts"][0]["readiness"]["status"] == "FAILED"
    assert "missing required metric" in record["bootstrap_attempts"][0]["readiness"]["error"]
    assert record["bootstrap_attempts"][1]["readiness"]["status"] == "PASSED"
    assert record["bootstrap_attempts"][1]["readiness"]["primary_values"] == [0.2, 0.2, 0.2]
    assert record["bootstrap_attempts"][1]["readiness"]["calibration"]["repeat_count"] == 3
    assert "calibration" not in spec.model_dump(mode="json")
    store = CampaignStore(paths.db)
    try:
        assert store.spec().approved_hash() == approved_hash
    finally:
        store.close()
    assert spec.base_commit == record["bootstrap_snapshot"]
    assert git(["rev-parse", "HEAD"], repo) == source_base
    assert git(["status", "--porcelain"], repo) == ""


def test_noisy_readiness_diagnostics_reach_bounded_repair_and_exhaust_without_campaign(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    counter = tmp_path / "external-run-count.txt"
    agent = repo / "noisy_agent.py"
    evaluator_source = (
        "import json\nfrom pathlib import Path\n"
        f"p=Path({str(counter)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "value=0.4 if n % 2 else 0.6\n"
        f"print(json.dumps({{'status':'ok','metrics':{{'quality':value}},'checks':{{}},'protocol_sha256':'{PROTOCOL_SHA256}'}}))\n"
    )
    agent.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "root=Path('.bananloop_eval'); root.mkdir(exist_ok=True)\n"
        f"root.joinpath('evaluator.py').write_text({evaluator_source!r})\n"
        f"print(json.dumps({_bootstrap_payload(
            status="ready", needs_input=[], summary="noisy fixture evaluator"
        )!r}))\n",
        encoding="utf-8",
    )
    git(["add", "noisy_agent.py"], repo)
    git(["commit", "-m", "add noisy bootstrap fixture"], repo)
    paths = RuntimePaths.for_repo(repo, tmp_path / "noisy-state")

    with pytest.raises(BootstrapError, match="failed readiness after bounded repair"):
        bootstrap_campaign(
            repo=repo,
            goal="improve fixture quality",
            paths=paths,
            agent_config=AgentConfig(
                kind="command", command=[sys.executable, "noisy_agent.py"]
            ),
            max_experiments=1,
            max_attempts=2,
            max_agent_calls=4,
            max_evaluator_calls=2,
            deadline_seconds=300,
        )

    record = json.loads((paths.root / "bootstrap-plan.json").read_text(encoding="utf-8"))
    assert len(record["bootstrap_attempts"]) == 3
    first = record["bootstrap_attempts"][0]["readiness"]
    assert first["status"] == "FAILED"
    assert first["calibration"]["repeat_count"] == 3
    assert first["calibration"]["noise_gate_passed"] is False
    assert "readiness_receipt" in record["bootstrap_attempts"][1]["request"]["repair"]
    assert not paths.db.exists()
