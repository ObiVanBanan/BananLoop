import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmarks.autonomous_v01.runner import canonical_sha
from benchmarks.autonomous_v01.single_arm_gate import GateError, validate_single_arm_gate


NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def _json(path: Path, data):
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


@pytest.fixture
def evidence(tmp_path):
    checkout = tmp_path / "candidate"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "gate@test"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Gate Test"], check=True)
    (checkout / "candidate.py").write_text("candidate = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "candidate.py"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "candidate"], check=True)
    source_sha = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "kind": "ARE_PREPARATION_ONLY",
        "candidate_source_sha": source_sha,
        "tasks": [{"pair_id": f"are-{i:02}", "task": {"task_id": f"task-{i}"},
                   "arms": [{"system": "bananloop_full", "deadline_seconds": 3600},
                            {"system": "codex_direct", "deadline_seconds": 3600}]}
                  for i in range(12)],
    }
    manifest["manifest_sha256"] = canonical_sha(manifest)
    mpath, ppath = tmp_path / "manifest.json", tmp_path / "preflight.json"
    _json(mpath, manifest)
    preflight = {"schema": "bananloop-autonomous-v01-preflight-v1",
                 "status": "READY_NON_RPDI_DIAGNOSTIC",
                 "blockers": [], "manifest_sha256": manifest["manifest_sha256"],
                 "candidate_source_sha": source_sha, "generated_at": NOW.isoformat()}
    _json(ppath, preflight)
    return {"checkout": checkout, "source": source_sha, "manifest": manifest,
            "mpath": mpath, "ppath": ppath, "preflight": preflight}


def _run(e, **changes):
    args = {"manifest_path": e["mpath"], "preflight_path": e["ppath"],
            "candidate_checkout": e["checkout"],
            "expected_manifest_sha256": e["manifest"]["manifest_sha256"],
            "task_id": "task-0", "candidate_source_sha": e["source"],
            "arm_id": "are-00-bananloop_full", "now": lambda: NOW}
    args.update(changes)
    return validate_single_arm_gate(**args)


def test_positive_passes_but_does_not_claim_authorization(evidence):
    result = _run(evidence)
    assert result["status"] == "GATE_PASSED"
    assert result["authorization"] == "NOT_ESTABLISHED"
    assert result["preflight_status"] == "READY_NON_RPDI_DIAGNOSTIC"


def test_modified_manifest_fails_canonical_hash(evidence):
    data = json.loads(evidence["mpath"].read_text())
    data["model"] = "modified"
    _json(evidence["mpath"], data)
    with pytest.raises(GateError, match="canonical SHA"):
        _run(evidence)


def test_manifest_must_have_exactly_twelve_tasks(evidence):
    data = evidence["manifest"]
    data["tasks"].pop()
    data["manifest_sha256"] = canonical_sha({k: v for k, v in data.items() if k != "manifest_sha256"})
    _json(evidence["mpath"], data)
    with pytest.raises(GateError, match="exactly 12"):
        _run(evidence, expected_manifest_sha256=data["manifest_sha256"])


def test_missing_preflight_binding_fails_closed(evidence):
    data = evidence["preflight"]
    data.pop("candidate_source_sha")
    _json(evidence["ppath"], data)
    with pytest.raises(GateError, match="candidate source SHA"):
        _run(evidence)


def test_stale_preflight_fails(evidence):
    data = evidence["preflight"]
    data["generated_at"] = "2026-11-23T10:00:00+00:00"
    _json(evidence["ppath"], data)
    with pytest.raises(GateError, match="stale"):
        _run(evidence)


def test_preflight_manifest_mismatch_fails(evidence):
    data = evidence["preflight"]
    data["manifest_sha256"] = "f" * 64
    _json(evidence["ppath"], data)
    with pytest.raises(GateError, match="manifest"):
        _run(evidence)


def test_preflight_candidate_mismatch_fails(evidence):
    data = evidence["preflight"]
    data["candidate_source_sha"] = "0" * 40
    _json(evidence["ppath"], data)
    with pytest.raises(GateError, match="candidate source SHA"):
        _run(evidence)


def test_dirty_checkout_fails(evidence):
    (evidence["checkout"] / "candidate.py").write_text("candidate = 2\n", encoding="utf-8")
    with pytest.raises(GateError, match="dirty"):
        _run(evidence)
