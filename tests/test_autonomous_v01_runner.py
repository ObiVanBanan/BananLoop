from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from benchmarks.autonomous_v01.runner import (
    ARM_SECONDS,
    CATALOGUE,
    TASK_IDS,
    CandidateArchive,
    CodexDirectBackend,
    BananLoopFullBackend,
    UsageLedger,
    VerifiedCandidate,
    archive_series_results,
    build_manifest,
    authorize_benchmark_pivot,
    canonical_sha,
    offline_preflight,
    parse_codex_jsonl_usage,
    run_fake_series,
)


def test_manifest_freezes_exact_curated_tasks_and_randomizes_only_arm_order():
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    manifest = build_manifest(catalogue=catalogue, seed=91, model="pinned-model",
                              reasoning="high", codex_version="0.169.0",
                              full_feature_flags={"mode": "Full", "benchmark_pivot_policy": "v1"})
    replay = build_manifest(catalogue=catalogue, seed=91, model="pinned-model",
                            reasoning="high", codex_version="0.169.0",
                            full_feature_flags={"mode": "Full", "benchmark_pivot_policy": "v1"})
    assert manifest == replay
    assert tuple(item["task"]["task_id"] for item in manifest["tasks"]) == TASK_IDS
    assert len(manifest["tasks"]) == 12
    assert sum(len(pair["arms"]) for pair in manifest["tasks"]) == 24
    assert all(arm["deadline_seconds"] == ARM_SECONDS
               for pair in manifest["tasks"] for arm in pair["arms"])
    assert manifest["call_limit_policy"] == "OBSERVE_ONLY"
    assert "reserved_calls" not in manifest


def test_manifest_rejects_missing_or_duplicate_catalogue_entries():
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="content digest"):
        build_manifest(catalogue={**catalogue, "tasks": catalogue["tasks"][:-1]}, seed=1,
                       model="m", reasoning="r", codex_version="v", full_feature_flags={"full": True})
    with pytest.raises(ValueError, match="integer"):
        build_manifest(catalogue=catalogue, seed=True, model="m", reasoning="r",
                       codex_version="v", full_feature_flags={"full": True})


def test_preflight_reports_missing_content_pins_without_launching_anything(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    report = offline_preflight(codex_auth_status="Logged in using ChatGPT",
                               codex_version="0.169.0", image_inspector=lambda _: None,
                               runtime_root=runtime_root)
    assert report["status"] == "PREP_BLOCKED"
    assert report["planned_distinct_tasks"] == 12
    assert report["arms"] == 24
    assert report["call_limit_policy"] == "OBSERVE_ONLY"
    assert report["live_models_started"] is False
    assert report["harbor_started"] is False
    assert report["ai_judge_started"] is False
    for task_id in ("act-tensor-sparse-panel-imputation-r2",
                    "causalpfn-cate-pehe-ihdp-surfaceb", "hicard-latent-encoder"):
        assert any(task_id in item and "local public DEV image ID differs" in item
                   for item in report["blockers"])
    assert sum("task runtime descriptor is missing" in item
               for item in report["blockers"]) == 12
    assert report["operator_private_source_exposure"]["accepted_for_internal_diagnostic"] is True
    assert report["operator_private_source_exposure"]["disclosure_valid"] is True
    assert set(report["source_access_affected_task_ids"]) == {
        "act-tensor-sparse-panel-imputation-r2",
        "causalpfn-cate-pehe-ihdp-surfaceb",
        "hicard-latent-encoder",
    }
    assert report["implementation_gates"]["codex_direct_backend"] is True
    assert report["implementation_gates"]["codex_final_artifact_export"] is True
    assert report["implementation_gates"]["bananloop_full_backend"] is True
    assert report["implementation_gates"]["benchmark_bound_pivot_authorizer"] is True
    assert report["implementation_gates"]["inherited_deadline_public_grader"] is True
    assert report["implementation_gates"]["24_arm_result_archiver"] is True
    assert report["capacity"]["per_task_requested_cpus"] == 8
    assert report["capacity"]["per_task_requested_memory_mb"] == 14336


def test_benchmark_pivot_authorizer_binds_exact_frozen_scope_and_deadline():
    binding = {"candidate_path": "src/candidate.py", "manifest_sha256": "a" * 64}
    deadline = 5000.0
    pivot = {
        "status": "BENCHMARK_AUTO_CHILD_CREATED",
        "benchmark_binding": binding,
        "benchmark_binding_sha256": canonical_sha(binding),
        "inherited_deadline_epoch": deadline,
        "child_candidate_iteration_cap": None,
        "child_agent_call_ceiling": 1000,
        "child_evaluator_call_ceiling": 1000,
        "changed_paths": ["src/candidate.py"],
        "child_campaign_id": "c-" + "a" * 32,
        "pivot_authorization": "FROZEN_BENCHMARK_FULL_AUTO_SCOPE",
    }
    assert authorize_benchmark_pivot(
        binding=binding, pivot=pivot, arm_deadline_epoch=deadline, now_epoch=4999,
    ) is True
    changed = {**pivot, "changed_paths": ["src/other.py"]}
    assert authorize_benchmark_pivot(
        binding=binding, pivot=changed, arm_deadline_epoch=deadline, now_epoch=4999,
    ) is False
    assert authorize_benchmark_pivot(
        binding=binding, pivot=pivot, arm_deadline_epoch=deadline, now_epoch=deadline,
    ) is False


def test_usage_ledger_is_observational_and_does_not_enforce_a_call_cap(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    for _ in range(260):
        ledger.record("arm-a", "fake", {"model_turns_observed": 1,
                                           "usage": {"input_tokens": 2, "output_tokens": 3}})
    result = ledger.summary()
    assert result["observed_events"] == result["model_turns_observed"] == 260
    assert result["input_tokens"] == 520
    assert result["output_tokens"] == 780


def test_usage_ledger_preserves_turn_count_when_token_usage_is_unknown(tmp_path):
    ledger = UsageLedger(tmp_path / "usage-unknown.sqlite3")
    ledger.record("arm-a", "codex_direct", {
        "model_turns_observed": 3, "usage": None, "usage_unknown": True,
    })
    result = ledger.summary()
    assert result["model_turns_observed"] == 3
    assert result["unknown_usage_events"] == 1
    assert result["input_tokens"] == result["output_tokens"] == 0
    assert result["by_source"]["codex_direct"]["model_turns_observed"] == 3


def test_archive_selects_best_verified_in_deadline_candidate_and_rejects_forgery(tmp_path):
    archive = CandidateArchive(tmp_path / "archive")
    for source, score in ((b"worse", 0.2), (b"best", 0.8)):
        digest = hashlib.sha256(source).hexdigest()
        archive.save(VerifiedCandidate("arm-a", source, score, 90.0,
                                       {"candidate_sha256": digest, "evaluator": "fake-v1"}),
                     deadline_epoch=100.0)
    assert archive.best("arm-a")["score"] == 0.8
    late = b"late"
    with pytest.raises(ValueError, match="after arm deadline"):
        archive.save(VerifiedCandidate("arm-a", late, 1.0, 101.0,
                                       {"candidate_sha256": hashlib.sha256(late).hexdigest()}),
                     deadline_epoch=100.0)
    with pytest.raises(ValueError, match="does not bind"):
        archive.save(VerifiedCandidate("arm-a", b"forged", 1.0, 90.0, {}), deadline_epoch=100.0)


def test_fake_series_runs_24_isolated_arms_without_external_calls(tmp_path):
    tasks = [{"task_id": task_id, "arm_order": ["bananloop_full", "codex_direct"]}
             for task_id in TASK_IDS]

    class FakeBackend:
        def __init__(self, system):
            self.system = system

        def run(self, arm_id, task, arm_root, deadline_epoch, usage):
            assert self.system in arm_id
            assert task["task_id"] in arm_id
            assert arm_root.is_dir()
            assert deadline_epoch > 0
            return {"status": "FAKE_COMPLETE"}

    report = run_fake_series(tasks, tmp_path / "series", FakeBackend)
    assert report["status"] == "OFFLINE_FAKE_COMPLETE"
    assert len(report["runs"]) == 24
    assert report["usage"]["observed_events"] == 0
    assert report["harbor_evaluations"] == report["ai_judge_calls"] == 0
    roots = [tmp_path / "series" / run["arm_id"] for run in report["runs"]]
    assert len(set(roots)) == 24
    assert all(root.is_dir() and (root / "result.json").is_file() for root in roots)
    assert (tmp_path / "series" / "summary.json").is_file()
    archive = tmp_path / "series" / "final-archive"
    assert (archive / "experiment-manifest.json").is_file()
    assert (archive / "summary.json").is_file()
    index = json.loads((archive / "SHA256SUMS.json").read_text(encoding="utf-8"))
    assert len(index["files"]) == 26  # 24 arm records plus manifest and summary
    assert all(len(item["sha256"]) == 64 for item in index["files"])


@pytest.mark.parametrize("exploration_fails", [False, True])
def test_fake_24_arm_series_uses_real_backend_wiring_and_archives_all_outputs(tmp_path, monkeypatch, exploration_fails):
    """Exercise both production backend classes with subprocess-only fakes."""
    from bananloop.evaluator_fixture import EvaluatorFixtureV1
    from benchmarks.autoresearchexam import prepare_public_task_job

    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    tasks = [json.loads(json.dumps(item)) for item in catalogue["tasks"]]
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    subprocess.run(["git", "init", "-q", str(baseline)], check=True)
    subprocess.run(["git", "-C", str(baseline), "config", "user.email", "fake@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(baseline), "config", "user.name", "Offline Fake"], check=True)
    for task in tasks:
        candidate = baseline / task["adapter"]["candidate_path"]
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("# original candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(baseline), "add", "."], check=True)
    subprocess.run(["git", "-C", str(baseline), "commit", "-qm", "fake baseline"], check=True)
    base_sha = subprocess.run(["git", "-C", str(baseline), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    source_root = tmp_path / "source"
    overlay_root = tmp_path / "overlays"
    fixtures_root = tmp_path / "fixtures"
    source_root.mkdir(); overlay_root.mkdir(); fixtures_root.mkdir()
    fixture = EvaluatorFixtureV1.model_validate({
        "schema_version": 1,
        "evaluator_bundle_base64": "eyJmYWtlIjoxfQ==",
        "execution_backend_id": "autoresearchexam-public-v1",
        "entrypoint": ["python3", "{evaluator}/evaluate.py"],
        "tiers": [
            {"tier": "dev", "cases": [{"case_id": "dev", "input_base64": "ZGV2"}]},
            {"tier": "validation", "cases": [{"case_id": "validation", "input_base64": "dmFs"}]},
        ],
    })
    fixture_bytes = fixture.canonical_bytes()
    fixture_path = fixtures_root / "fixture.json"
    fixture_path.write_bytes(fixture_bytes)
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    for task in tasks:
        task_id = task["task_id"]
        source = source_root / task_id
        source.mkdir()
        instruction = f"Solve fake task {task_id}.\n".encode()
        task_toml = f"[task]\nid = '{task_id}'\n".encode()
        (source / "instruction.md").write_bytes(instruction)
        (source / "task.toml").write_bytes(task_toml)
        task["instruction_sha256"] = hashlib.sha256(instruction).hexdigest()
        task["task_sha256"] = hashlib.sha256(task_toml).hexdigest()
        overlay = overlay_root / task_id
        overlay.mkdir()
        (overlay / "task.toml").write_text("[task]\n", encoding="utf-8")
        config_builder = tmp_path / f"builder-{task_id}.py"
        config_builder.write_text(
            "import json, pathlib, sys\n"
            "out, jobs, task, candidate, repo, state, deadline, overlay = sys.argv[1:]\n"
            "out=pathlib.Path(out); jobs=pathlib.Path(jobs); out.mkdir(parents=True, exist_ok=True)\n"
            "bl=out/'bananloop.json'; harbor=out/'harbor.json'\n"
            "bl.write_text(json.dumps({'repo':repo,'state_dir':state,'candidate_path':candidate,'deadline_epoch':float(deadline)}))\n"
            "job='job-'+task\n"
            "harbor.write_text(json.dumps({'jobs_dir':str(jobs),'job_name':job,'task_id':task,'overlay_path':sys.argv[8],'candidate_path':candidate,'repo':repo,'bananloop_config':str(bl),'agents':[{}]}))\n"
            "print(json.dumps({'bananloop_config':str(bl),'harbor_config':str(harbor)}))\n",
            encoding="utf-8",
        )
        # Pass each path through the production template substitution path.
        task["runtime_descriptor"] = {
            "source_task_root": str(source), "overlay_path": str(overlay),
            "config_builder_command": [sys.executable, str(config_builder), "{output_dir}",
                                       "{jobs_dir}", task_id, "{candidate_path}", "{repo}",
                                       "{state_dir}", "{deadline_epoch}", str(overlay)],
        }
        adapter = task["adapter"]
        descriptor = {
            "schema": "bananloop-autonomous-v01-task-runtime-v1", "task_id": task_id,
            "adapter_id": adapter["adapter_id"], "candidate_path": adapter["candidate_path"],
            "task_sha256": task["task_sha256"], "instruction_sha256": task["instruction_sha256"],
            "public_dev_image_id": task["public_dev_image_id"],
            "fixture_sha256": fixture_sha,
            "overlay_task_sha256": hashlib.sha256((overlay / "task.toml").read_bytes()).hexdigest(),
            "source_task_root": str(source), "fixture_path": str(fixture_path),
            "overlay_path": str(overlay),
            "mcp_contract_path": str(tmp_path / f"{task_id}-contract.json"),
            "dev_command": ["fake-dev", "--task", task_id],
            "config_builder_command": task["runtime_descriptor"]["config_builder_command"],
        }
        (runtime_root / f"{task_id}.json").write_text(json.dumps(descriptor), encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env python3\nimport json, pathlib, sys\n"
        "args=sys.argv[1:]; goal=args[args.index('--goal')+1]\n"
        "binding=json.loads(goal.split('BANANLOOP_BENCHMARK_BINDING_V1:',1)[1].split('\\n',1)[0])\n"
        "state=pathlib.Path(args[args.index('--state-dir')+1]); state.mkdir(parents=True, exist_ok=True)\n"
        "registry=state/'roles.json'; registry.write_text('{}')\n"
        "deadline=float(args[args.index('--task-deadline-epoch')+1]); child='c-'+'a'*32\n"
        "pivot={'status':'BENCHMARK_AUTO_CHILD_CREATED','benchmark_binding':binding,"
        "'benchmark_binding_sha256':__import__('hashlib').sha256(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).hexdigest(),"
        "'inherited_deadline_epoch':deadline,'child_candidate_iteration_cap':None,"
        "'child_agent_call_ceiling':1000,'child_evaluator_call_ceiling':1000,"
        "'changed_paths':[binding['candidate_path']],'child_campaign_id':child,"
        "'pivot_authorization':'FROZEN_BENCHMARK_FULL_AUTO_SCOPE','role_registry_path':str(registry)}\n"
        "print(json.dumps({'status':'BENCHMARK_AUTO_CHILD_CREATED','usage':{'input_tokens':0,'output_tokens':0},'request_history':[],'optimization_pivot':[pivot]}))\n",
        encoding="utf-8",
    ); fake_python.chmod(0o755)
    if exploration_fails:
        fake_python.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "print(json.dumps({'status':'RUN_FAILED','failure_type':'V2CampaignCLIError',"
            "'request_history':[{'dispatched':True},{'dispatched':True}],"
            "'usage':{'input_tokens':123,'output_tokens':45}}))\nsys.exit(2)\n"
        )
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\nimport json, pathlib, sys\n"
        "repo=pathlib.Path(sys.argv[sys.argv.index('-C')+1]); inst=pathlib.Path(repo/'TASK-INSTRUCTION.md').read_text()\n"
        "path=repo/'benchmarks/autoresearchexam/solutions'; path.mkdir(parents=True, exist_ok=True)\n"
        "# The task-specific entry point is already present in the pinned baseline.\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n",
        encoding="utf-8",
    ); fake_codex.chmod(0o755)
    fake_harbor = tmp_path / "fake-harbor"
    fake_harbor.write_text(
        "#!/usr/bin/env python3\nimport json, pathlib, sys, time\n"
        "cfg=json.loads(pathlib.Path(sys.argv[sys.argv.index('--config')+1]).read_text())\n"
        "job=pathlib.Path(cfg['jobs_dir'])/cfg['job_name']; trial=job/'trial-001'; trial.mkdir(parents=True)\n"
        "now=time.time(); (job/'result.json').write_text(json.dumps({'id':cfg['job_name'],'finished_at':now,'n_total_trials':1,'stats':{'n_completed_trials':1,'n_errored_trials':0,'n_running_trials':0,'n_pending_trials':0,'n_cancelled_trials':0,'n_retries':0}}))\n"
        "(trial/'result.json').write_text(json.dumps({'id':'trial-001','finished_at':now,'task_id':{'path':cfg['overlay_path']},'agent_result':{'metadata':{'bananloop_are':{'task_id':cfg['task_id'],'status':'COMPLETED','provider_call_attempts':7,'usage':{'input_tokens':11,'output_tokens':13,'total_tokens':24}}}}}))\n",
        encoding="utf-8",
    ); fake_harbor.chmod(0o755)

    # Public-task config preparation is replaced only at its filesystem edge;
    # the backend's real pivot authorizer and Harbor receipt validator remain active.
    def fake_public_prepare(**kwargs):
        out, jobs = kwargs["output_dir"], kwargs["jobs_dir"]
        out.mkdir(parents=True, exist_ok=True)
        bl, harbor = out / "bananloop.json", out / "harbor.json"
        task_id = kwargs["source_task"].name
        adapter = kwargs["adapter_id"]
        # Resolve exact task record from frozen closure.
        task = next(item for item in tasks if item["task_id"] == task_id)
        bl.write_text(json.dumps({"repo": str(kwargs["repo"]), "state_dir": str(kwargs["state_dir"]),
                                  "candidate_path": task["adapter"]["candidate_path"]}))
        harbor.write_text(json.dumps({"jobs_dir": str(jobs), "job_name": "job-" + task_id,
                                      "task_id": task_id, "overlay_path": str(kwargs["overlay"]),
                                      "candidate_path": task["adapter"]["candidate_path"],
                                      "repo": str(kwargs["repo"]), "bananloop_config": str(bl), "agents": [{}]}))
        return {"bananloop_config": str(bl), "harbor_config": str(harbor)}

    monkeypatch.setattr(prepare_public_task_job, "prepare", fake_public_prepare)

    instruction = tmp_path / "codex-instruction.md"
    instruction.write_text("Write any candidate.\n", encoding="utf-8")
    manifest_sha = "f" * 64
    root = tmp_path / "real-backend-fakes"

    def backend_factory(system):
        if system == "bananloop_full":
            return BananLoopFullBackend(
                initial_repo=baseline, base_sha=base_sha, runtime_root=runtime_root,
                state_root=tmp_path / "states", jobs_root=tmp_path / "jobs",
                manifest_sha256=manifest_sha, python_binary=str(fake_python),
                harbor_binary=fake_harbor,
            )
        return CodexDirectBackend(codex_binary=str(fake_codex), model="fake", reasoning="low",
                                  task_instruction=instruction, dev_command=["fake-dev"],
                                  initial_repo=baseline, base_sha=base_sha,
                                  runtime_root=runtime_root)

    report = run_fake_series(tasks, root, backend_factory)
    assert len(report["runs"]) == 24
    assert report["model_calls"] == report["harbor_evaluations"] == report["ai_judge_calls"] == 0
    if exploration_fails:
        full = [item for item in report["runs"] if item["system"] == "bananloop_full"]
        assert len(full) == 12
        assert all(item["result"]["status"] == "EXPLORATION_FAILED" for item in full)
        assert all(item["result"]["model_turns_observed"] == 2 for item in full)
        assert all(item["result"]["failure_type"] == "V2CampaignCLIError" for item in full)
        assert report["usage"]["by_source"]["bananloop_full_exploration"]["input_tokens"] == 12 * 123
        assert report["usage"]["unknown_usage_events"] == 0
        return
    assert report["usage"]["model_turns_observed"] == 96
    assert report["usage"]["by_source"]["bananloop_full_optimization"]["model_turns_observed"] == 84
    assert all(item["result"]["status"] == "COMPLETED" for item in report["runs"]), [(item["arm_id"], item["result"]) for item in report["runs"] if item["result"]["status"] != "COMPLETED"]
    full = [item for item in report["runs"] if item["system"] == "bananloop_full"]
    direct = [item for item in report["runs"] if item["system"] == "codex_direct"]
    assert len(full) == len(direct) == 12
    assert all(item["result"].get("child_campaign_id") == "c-" + "a" * 32 for item in full)
    assert all(item["result"]["evaluator_scope"]["policy"] == "PUBLIC_DEV_ONLY_DURING_ARM"
               and item["result"]["evaluator_scope"]["included_tiers"] == ["dev"]
               and item["result"]["evaluator_scope"]["excluded_tiers"] == ["validation"]
               for item in full)
    assert all(item["result"]["source_overlay"]["base_commit"] == base_sha
               and item["result"]["source_overlay"]["instruction_sha256"] == next(
                   task["instruction_sha256"] for task in tasks if task["task_id"] == item["task_id"]
               ) for item in full)
    assert all(item["result"]["source_overlay"]["candidate_sha256"]
               == item["result"]["final_artifact"]["entrypoint_sha256"] for item in full)
    for item in full:
        repo = root / ".workspaces" / item["arm_id"]
        overlay = item["result"]["source_overlay"]
        frozen_instruction = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{overlay['overlay_commit']}:{overlay['instruction_path']}"],
        )
        assert hashlib.sha256(frozen_instruction).hexdigest() == overlay["instruction_sha256"]
    assert all(item["result"]["final_artifact"]["status"] == "CAPTURED" for item in direct)
    archive = root / "final-archive"
    index = json.loads((archive / "SHA256SUMS.json").read_text(encoding="utf-8"))
    assert index["manifest_sha256"] == canonical_sha({
        key: value for key, value in json.loads((archive / "experiment-manifest.json").read_text()).items()
        if key != "manifest_sha256"
    })


def test_series_archiver_refuses_credential_paths_and_content(tmp_path):
    tasks = [{"task_id": task_id, "arm_order": ["bananloop_full", "codex_direct"]}
             for task_id in TASK_IDS]

    class FakeBackend:
        def run(self, arm_id, task, arm_root, deadline_epoch, usage):
            (arm_root / ".env.local").write_text("OPENAI_API_KEY=redacted", encoding="utf-8")
            return {"status": "FAKE_COMPLETE"}

    with pytest.raises(ValueError, match="sensitive artifact path"):
        run_fake_series(tasks, tmp_path / "series", lambda _: FakeBackend())

    class LeakingBackend:
        def run(self, arm_id, task, arm_root, deadline_epoch, usage):
            (arm_root / "events.log").write_text("Authorization: Bearer fake-secret-token", encoding="utf-8")
            return {"status": "FAKE_COMPLETE"}

    with pytest.raises(ValueError, match="credential-like content"):
        run_fake_series(tasks, tmp_path / "series-content", lambda _: LeakingBackend())


def test_series_archiver_rejects_missing_duplicate_or_modified_manifest(tmp_path):
    tasks = [{"task_id": task_id, "arm_order": ["bananloop_full", "codex_direct"]}
             for task_id in TASK_IDS]

    class FakeBackend:
        def run(self, arm_id, task, arm_root, deadline_epoch, usage):
            return {"status": "FAKE_COMPLETE"}

    report = run_fake_series(tasks, tmp_path / "source", lambda _: FakeBackend())
    manifest = json.loads((tmp_path / "source" / "final-archive" / "experiment-manifest.json")
                          .read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="exactly the 24"):
        archive_series_results(manifest=manifest, run_records=report["runs"][:-1],
                               result_root=tmp_path / "source", archive_root=tmp_path / "partial")
    unpaired = [dict(record) for record in report["runs"]]
    unpaired[0]["system"] = "codex_direct"
    with pytest.raises(ValueError, match="exactly the 24"):
        archive_series_results(manifest=manifest, run_records=unpaired,
                               result_root=tmp_path / "source", archive_root=tmp_path / "unpaired")
    manifest["seconds_per_arm"] = 1
    with pytest.raises(ValueError, match="manifest digest"):
        archive_series_results(manifest=manifest, run_records=report["runs"],
                               result_root=tmp_path / "source", archive_root=tmp_path / "tampered")


def test_fake_series_requires_new_state_root_and_exactly_12_distinct_tasks(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    with pytest.raises(ValueError, match="must be new"):
        run_fake_series([{"task_id": key} for key in TASK_IDS], root, lambda _: None)
    with pytest.raises(ValueError, match="exactly 12"):
        run_fake_series([{"task_id": key} for key in TASK_IDS[:-1]], tmp_path / "new", lambda _: None)


def test_codex_jsonl_counts_turns_as_observed_not_provider_calls(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}),
    ]) + "\n", encoding="utf-8")
    usage = parse_codex_jsonl_usage(path)
    assert usage == {"model_turns_observed": 2,
                     "usage": {"input_tokens": 13, "output_tokens": 6},
                     "usage_unknown": False}


def test_codex_jsonl_marks_malformed_tail_as_unknown(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":1}}\n{bad',
                    encoding="utf-8")
    usage = parse_codex_jsonl_usage(path)
    assert usage["model_turns_observed"] == 1
    assert usage["usage_unknown"] is True
    assert usage["usage"] is None


def test_codex_direct_backend_uses_fake_cli_and_enforces_deadline(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    subprocess.run(["git", "init", "-q", str(initial)], check=True)
    subprocess.run(["git", "-C", str(initial), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(initial), "config", "user.name", "Offline Test"], check=True)
    (initial / "README.md").write_text("initial tree\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(initial), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(initial), "commit", "-qm", "initial"], check=True)
    base_sha = subprocess.run(["git", "-C", str(initial), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()
    instruction = tmp_path / "instruction.md"
    instruction.write_text("fake task", encoding="utf-8")
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys,time\n"
        "prompt=sys.stdin.read()\n"
        "os.makedirs('benchmarks/fake',exist_ok=True)\n"
        "open('benchmarks/fake/solution.py','w').write('final candidate')\n"
        "if 'sleep past deadline' in prompt: time.sleep(5)\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':3}}),flush=True)\n",
        encoding="utf-8")
    fake_codex.chmod(0o755)
    backend = CodexDirectBackend(
        codex_binary=str(fake_codex), model="fake-model", reasoning="high",
        task_instruction=instruction, dev_command=["python", "fake_dev.py"],
        initial_repo=initial, base_sha=base_sha,
    )
    usage = UsageLedger(tmp_path / "usage.sqlite3")
    task = {"task_id": "task-fake", "adapter": {"candidate_path": "benchmarks/fake/solution.py"}}
    expired_root = tmp_path / "expired"
    expired_root.mkdir()
    expired = backend.run("arm-expired", task, expired_root,
                          time.time() - 1, usage)
    assert expired["status"] == "TIMED_OUT_BEFORE_CLONE"
    assert expired["final_artifact"]["status"] == "MISSING_OUTPUT"
    assert (expired_root / "final-artifacts").is_dir()

    complete_root = tmp_path / "complete"
    complete_root.mkdir()
    completed = backend.run("arm-fake", task, complete_root,
                            time.time() + 10, usage)
    assert completed["status"] == "COMPLETED"
    assert completed["model_turns_observed"] == 1
    assert (complete_root.parent / ".workspaces" / "arm-fake" / "README.md").read_text(encoding="utf-8") == "initial tree\n"
    assert completed["final_artifact"]["status"] == "CAPTURED"
    assert (complete_root / "final-artifacts" / "solution.py").read_text(encoding="utf-8") == "final candidate"

    (instruction).write_text("sleep past deadline", encoding="utf-8")
    timeout_root = tmp_path / "timeout"
    timeout_root.mkdir()
    timed_out = backend.run("arm-timeout", task, timeout_root,
                            time.time() + 0.5, usage)
    assert timed_out["status"] == "TIMED_OUT"
    assert timed_out["model_turns_observed"] == 0
    assert timed_out["final_artifact"]["status"] == "CAPTURED"
    (timeout_root / "missing-snapshot").mkdir()
    missing = CodexDirectBackend.snapshot_final_artifacts(
        task=task, repo=timeout_root / "missing-repo", arm_root=timeout_root / "missing-snapshot",
    )
    assert missing["status"] == "MISSING_OUTPUT"


def test_all_catalogue_candidates_exist_in_repository():
    from benchmarks.autonomous_v01.runner import ROOT
    tasks = json.loads(CATALOGUE.read_text())["tasks"]
    assert len(tasks) == 12
    for task in tasks:
        candidate = ROOT / task["adapter"]["candidate_path"]
        assert candidate.is_file() and not candidate.is_symlink(), task["task_id"]
        compile(candidate.read_bytes(), str(candidate), "exec")
