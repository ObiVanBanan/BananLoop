import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmarks.autonomous_v01 import bound_preflight
from benchmarks.autonomous_v01.runner import canonical_sha


NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    checkout = tmp_path / "candidate"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
    (checkout / "candidate.py").write_text("candidate = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "candidate.py"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "candidate"], check=True)
    source = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "kind": "ARE_PREPARATION_ONLY", "candidate_source_sha": source,
        "tasks": [{"pair_id": f"are-{i:02}", "task": {"task_id": f"task-{i}"},
                   "arms": [{"system": "bananloop_full"}, {"system": "codex_direct"}]}
                  for i in range(12)],
    }
    manifest["manifest_sha256"] = canonical_sha(manifest)
    manifest_path = tmp_path / "manifest.json"
    _write(manifest_path, manifest)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    output = tmp_path / "evidence" / "preflight.json"
    seen = {}

    def fake_preflight(**kwargs):
        seen.update(kwargs)
        return {"schema": "bananloop-autonomous-v01-preflight-v1",
                "status": "READY_NON_RPDI_DIAGNOSTIC", "blockers": [],
                "task_runtime_root": str(kwargs["runtime_root"]),
                "live_models_started": False, "harbor_started": False,
                "ai_judge_started": False}

    monkeypatch.setattr(bound_preflight.runner, "offline_preflight", fake_preflight)
    args = {"manifest_path": manifest_path,
            "expected_manifest_sha256": manifest["manifest_sha256"],
            "candidate_checkout": checkout, "candidate_source_sha": source,
            "task_id": "task-0", "arm_id": "are-00-bananloop_full",
            "runtime_root": runtime, "generated_at": NOW.isoformat(),
            "output_path": output, "now": lambda: NOW}
    return args, manifest, seen


def test_runs_offline_preflight_with_explicit_runtime_and_writes_gate_evidence(inputs):
    args, manifest, seen = inputs
    result = bound_preflight.create_bound_preflight(**args)
    saved = json.loads(args["output_path"].read_text(encoding="utf-8"))
    assert seen["runtime_root"] == args["runtime_root"]
    assert result["manifest_sha256"] == manifest["manifest_sha256"]
    assert result["candidate_source_sha"] == args["candidate_source_sha"]
    assert result["generated_at"] == "2026-11-23T12:00:00Z"
    assert saved == result


@pytest.mark.parametrize("change", [
    {"expected_manifest_sha256": "f" * 64},
    {"candidate_source_sha": "0" * 40},
    {"generated_at": "2026-11-23T12:00:00"},
    {"generated_at": "2026-11-23T12:00:01+00:00"},
])
def test_untrusted_binding_inputs_fail_closed_without_running_preflight(inputs, change):
    args, _, seen = inputs
    args.update(change)
    with pytest.raises(bound_preflight.BoundPreflightError):
        bound_preflight.create_bound_preflight(**args)
    assert seen == {}


def test_dirty_checkout_fails_closed(inputs):
    args, _, seen = inputs
    (args["candidate_checkout"] / "candidate.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(bound_preflight.BoundPreflightError, match="dirty"):
        bound_preflight.create_bound_preflight(**args)
    assert seen == {}


def test_blocked_real_preflight_is_not_emitted(inputs, monkeypatch):
    args, _, _ = inputs
    monkeypatch.setattr(bound_preflight.runner, "offline_preflight", lambda **_: {
        "schema": "bananloop-autonomous-v01-preflight-v1",
        "status": "PREP_BLOCKED", "blockers": ["not ready"]})
    with pytest.raises(bound_preflight.BoundPreflightError, match="blocked"):
        bound_preflight.create_bound_preflight(**args)
    assert not args["output_path"].exists()
