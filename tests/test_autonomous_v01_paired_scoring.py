from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.autonomous_v01.paired_scoring import score_frozen_pair
from benchmarks.autonomous_v01.runner import canonical_sha


TASK_ID = "example-task"
ARMS = ("codex_direct", "bananloop_full")


def _archive(tmp_path: Path, *, results: dict[str, dict] | None = None,
             outputs: dict[str, bytes] | None = None) -> Path:
    """Create a small real archive with the same manifest, receipts and hash index."""
    root = tmp_path / "archive"
    root.mkdir()
    result_by_arm = results or {}
    output_by_arm = outputs or {}
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "task_ids": [TASK_ID],
        "arm_ids": [f"{TASK_ID}--{arm}" for arm in ARMS],
    }
    manifest["manifest_sha256"] = canonical_sha(manifest)
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    (root / "experiment-manifest.json").write_bytes(manifest_bytes)
    indexed: list[dict[str, object]] = [{
        "path": "experiment-manifest.json", "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "size_bytes": len(manifest_bytes),
    }]
    for arm in ARMS:
        arm_id = f"{TASK_ID}--{arm}"
        output = output_by_arm.get(arm, b"candidate bytes\x00\xff\n")
        result = result_by_arm.get(arm, {
            "status": "COMPLETED",
            "final_artifact": {
                "status": "CAPTURED",
                "files": [{"path": "answer.bin", "sha256": hashlib.sha256(output).hexdigest(), "size_bytes": len(output)}],
            },
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        })
        arm_root = root / arm_id
        record = {"arm_id": arm_id, "task_id": TASK_ID, "system": arm, "result": result}
        result_bytes = json.dumps(record, sort_keys=True).encode()
        (arm_root / "result.json").parent.mkdir(parents=True, exist_ok=True)
        (arm_root / "result.json").write_bytes(result_bytes)
        indexed.append({"path": f"{arm_id}/result.json", "sha256": hashlib.sha256(result_bytes).hexdigest(), "size_bytes": len(result_bytes)})
        if result.get("final_artifact", {}).get("status") == "CAPTURED":
            artifact_path = arm_root / "final-artifacts" / "answer.bin"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(output)
            indexed.append({"path": f"{arm_id}/final-artifacts/answer.bin", "sha256": hashlib.sha256(output).hexdigest(), "size_bytes": len(output)})
    summary = {"status": "ARCHIVED_24_ARMS", "results": []}
    summary_bytes = json.dumps(summary, sort_keys=True).encode()
    (root / "summary.json").write_bytes(summary_bytes)
    indexed.append({"path": "summary.json", "sha256": hashlib.sha256(summary_bytes).hexdigest(), "size_bytes": len(summary_bytes)})
    (root / "SHA256SUMS.json").write_text(json.dumps({
        "schema": "bananloop-autonomous-v01-archive-index-v1",
        "manifest_sha256": manifest["manifest_sha256"], "files": indexed,
    }), encoding="utf-8")
    return root


def test_scores_both_verified_outputs_with_same_scorer_and_preserves_usage(tmp_path):
    archive = _archive(tmp_path)
    calls: list[tuple[str, dict[str, bytes]]] = []

    def scorer(task_id: str, files: dict[str, bytes]) -> float:
        calls.append((task_id, files))
        return float(len(files["answer.bin"]))

    scored = score_frozen_pair(archive, TASK_ID, scorer)

    assert scored["task_id"] == TASK_ID
    assert set(scored["arms"]) == set(ARMS)
    assert [item[0] for item in calls] == [TASK_ID, TASK_ID]
    assert all(set(files) == {"answer.bin"} for _, files in calls)
    assert all(row["status"] == "SCORED" for row in scored["arms"].values())
    assert all(row["raw_score"] == len(b"candidate bytes\x00\xff\n") for row in scored["arms"].values())
    assert all(row["usage"]["total_tokens"] == 5 for row in scored["arms"].values())
    assert all(row["arm_status"] == "COMPLETED" for row in scored["arms"].values())


@pytest.mark.parametrize(
    ("artifact_status", "expected"),
    [("MISSING_OUTPUT", "MISSING_OUTPUT"), ("PARTIAL_INVALID", "INVALID_OUTPUT"),
     ("UNKNOWN", "UNKNOWN_OUTPUT")],
)
def test_preserves_non_scored_artifact_classification(tmp_path, artifact_status, expected):
    results = {"codex_direct": {"status": "TIMED_OUT", "final_artifact": {"status": artifact_status, "files": []}, "usage": "UNKNOWN"}}
    archive = _archive(tmp_path, results=results)
    calls = []
    scored = score_frozen_pair(archive, TASK_ID, lambda task_id, files: calls.append(task_id) or 1)

    row = scored["arms"]["codex_direct"]
    assert row["status"] == expected
    assert row["raw_score"] is None
    assert row["arm_status"] == "TIMED_OUT"
    assert row["usage"] == "UNKNOWN"
    assert calls == [TASK_ID]


def test_detects_output_hash_corruption_without_scoring_corrupt_bytes(tmp_path):
    archive = _archive(tmp_path)
    output = archive / f"{TASK_ID}--codex_direct" / "final-artifacts" / "answer.bin"
    output.write_bytes(b"tampered")
    seen: list[dict[str, bytes]] = []

    scored = score_frozen_pair(archive, TASK_ID, lambda task_id, files: seen.append(files) or 2)

    assert scored["arms"]["codex_direct"]["status"] == "CORRUPT_OUTPUT"
    assert all(b"tampered" not in files.values() for files in seen)


def test_scorer_failure_is_recorded_without_dropping_pair(tmp_path):
    archive = _archive(tmp_path)

    def scorer(task_id, files):
        if task_id == TASK_ID:
            raise RuntimeError("scorer failure")

    scored = score_frozen_pair(archive, TASK_ID, scorer)

    assert set(scored["arms"]) == set(ARMS)
    assert all(row["status"] == "SCORER_FAILED" for row in scored["arms"].values())
    assert all(row["raw_score"] is None for row in scored["arms"].values())


def test_requires_both_arm_receipts_before_scoring(tmp_path):
    archive = _archive(tmp_path)
    (archive / f"{TASK_ID}--bananloop_full" / "result.json").unlink()
    index_path = archive / "SHA256SUMS.json"
    index = json.loads(index_path.read_text())
    index["files"] = [entry for entry in index["files"] if entry["path"] != f"{TASK_ID}--bananloop_full/result.json"]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    calls = []

    with pytest.raises(ValueError):
        score_frozen_pair(archive, TASK_ID, lambda task_id, files: calls.append(task_id) or 1)
    assert calls == []


def test_timed_out_candidate_bytes_reach_scorer_unchanged(tmp_path):
    raw = b"partial timeout output\x00\xff\r\n"
    results = {"codex_direct": {
        "status": "TIMED_OUT",
        "final_artifact": {"status": "CAPTURED", "files": [{"path": "answer.bin", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}]},
        "usage": "UNKNOWN",
    }}
    archive = _archive(tmp_path, results=results, outputs={"codex_direct": raw})
    received: list[dict[str, bytes]] = []

    scored = score_frozen_pair(archive, TASK_ID, lambda task_id, files: received.append(files) or len(files["answer.bin"]))

    assert received[0]["answer.bin"] == raw
    assert scored["arms"]["codex_direct"]["arm_status"] == "TIMED_OUT"


def test_materializes_both_snapshots_before_invoking_first_scorer(tmp_path):
    original = b"frozen Full bytes"
    archive = _archive(tmp_path, outputs={"bananloop_full": original})
    full_file = archive / f"{TASK_ID}--bananloop_full/final-artifacts/answer.bin"
    observed = []

    def scorer(_task, files):
        observed.append(files["answer.bin"])
        if len(observed) == 1:
            full_file.write_bytes(b"late change")
        return len(files["answer.bin"])

    scored = score_frozen_pair(archive, TASK_ID, scorer)

    assert observed[1] == original
    assert scored["arms"]["bananloop_full"]["raw_score"] == len(original)


def test_nonterminal_second_receipt_prevents_both_scorer_calls(tmp_path):
    archive = _archive(tmp_path, results={"bananloop_full": {"status": "STARTED"}})
    with pytest.raises(ValueError, match="terminal arm receipts"):
        score_frozen_pair(archive, TASK_ID, lambda *_: pytest.fail("scorer ran before both arms finished"))
