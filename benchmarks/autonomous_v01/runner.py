#!/usr/bin/env python3
"""Offline-first paired ARE runner primitives.

This module deliberately does not start models or Harbor. A live backend must
be supplied by a separately authorized launcher after ``preflight`` is READY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

ROOT = Path(__file__).resolve().parents[2]
SELECTION = ROOT / "benchmarks/autoresearchexam/task-selection.json"
MAX_CALLS = 250
ARM_SECONDS = 3600
PER_ARM_CALLS = 10


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def task_readiness(selection: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return eligible pinned task IDs and evidence-based exclusions."""
    tasks = selection.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("selection.tasks must be a list")
    proxy_ids = {"budgeted-covtype-dual-market-open"}
    eligible = [t["task_id"] for t in tasks
                if isinstance(t, dict) and isinstance(t.get("task_id"), str)
                and t["task_id"] not in proxy_ids]
    excluded = [f"{task_id}: evaluator is static-price proxy, not official task score"
                for task_id in sorted(proxy_ids)
                if any(isinstance(t, dict) and t.get("task_id") == task_id for t in tasks)]
    return eligible, excluded


def build_manifest(*, eligible_tasks: list[dict[str, Any]], seed: int,
                   source_revision: str, evaluator_digest: str,
                   model: str, reasoning: str) -> dict[str, Any]:
    """Freeze 12 preregistered task pairs from a complete eligible pool."""
    required = {"task_id", "task_sha256", "instruction_sha256", "metric", "direction",
                "adapter_sha256"}
    if len({item.get("task_id") for item in eligible_tasks}) < 12:
        raise ValueError("PREP_BLOCKED_INSUFFICIENT_TASKS: need 12 eligible task adapters")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("selection seed must be an integer")
    if not source_revision or not evaluator_digest or not model or not reasoning:
        raise ValueError("source, evaluator, model and reasoning pins are required")
    for item in eligible_tasks:
        if not required <= item.keys() or item["direction"] not in {"higher", "lower"}:
            raise ValueError("eligible task lacks pinned source, adapter, or metric contract")
    ordered = sorted(eligible_tasks, key=lambda item: item["task_id"])
    rng = random.Random(seed)
    rng.shuffle(ordered)
    selected = ordered[:12]
    tasks = []
    for index, task in enumerate(selected, start=1):
        arms = ["bananloop_full", "codex_direct"]
        rng.shuffle(arms)
        tasks.append({"pair_id": f"are-{index:02d}", "task": dict(task),
                      "arm_order": arms,
                      "arms": [{"system": system, "deadline_seconds": ARM_SECONDS,
                                "call_reservation": PER_ARM_CALLS}
                               for system in arms]})
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "kind": "ARE_PRIMARY_ONLY",
        "seed": seed,
        "source_revision": source_revision,
        "evaluator_digest": evaluator_digest,
        "model": model,
        "reasoning": reasoning,
        "global_call_cap": MAX_CALLS,
        "reserved_calls": 240,
        "reserve_calls": 10,
        "judge_policy": "after_all_24_arms_terminal_and_frozen",
        "tasks": tasks,
        "board_games": "separate_secondary_experiment_requires_separate_approval_and_budget",
    }
    manifest["manifest_sha256"] = canonical_sha(manifest)
    return manifest


def offline_preflight(*, selection_path: Path = SELECTION,
                      codex_help: str | None = None) -> dict[str, Any]:
    """Verify task count and key run-control prerequisites without external calls."""
    selection = read_json(selection_path)
    eligible, exclusions = task_readiness(selection)
    expected = 12
    blockers: list[str] = []
    if len(set(eligible)) < expected:
        blockers.append(f"PREP_BLOCKED_INSUFFICIENT_TASKS: {len(set(eligible))} eligible adapters; require {expected}")
    if codex_help is not None and "max-turns" not in codex_help and "max-requests" not in codex_help:
        blockers.append("Codex CLI exposes no hard per-run provider-request cap; 250-call global enforcement is unproven")
    return {
        "schema": "bananloop-autonomous-v01-preflight-v1",
        "status": "PREP_BLOCKED" if blockers else "READY_NON_RPDI_DIAGNOSTIC",
        "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        "eligible_task_ids": sorted(set(eligible)),
        "excluded": exclusions,
        "planned_distinct_tasks": expected,
        "arms": expected * 2,
        "arm_deadline_seconds": ARM_SECONDS,
        "global_chatgpt_call_cap": MAX_CALLS,
        "per_arm_reservation": PER_ARM_CALLS,
        "all_calls_reserved": expected * 2 * PER_ARM_CALLS,
        "reserve_calls": MAX_CALLS - expected * 2 * PER_ARM_CALLS,
        "blockers": blockers,
        "live_models_started": False,
        "harbor_started": False,
        "ai_judge_started": False,
    }


class GlobalCallLedger:
    """Atomic reservation ledger; ambiguous calls permanently consume budget."""

    def __init__(self, path: Path, *, cap: int = MAX_CALLS, arm_cap: int = PER_ARM_CALLS):
        if isinstance(cap, bool) or cap < 1 or isinstance(arm_cap, bool) or arm_cap < 1:
            raise ValueError("caps must be positive")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cap = cap
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, arm_id TEXT NOT NULL, "
                       "status TEXT NOT NULL, reserved_at REAL NOT NULL, receipt_json TEXT)")
            row = db.execute("SELECT value FROM meta WHERE key='cap'").fetchone()
            if row is None:
                db.execute("INSERT INTO meta VALUES('cap', ?)", (str(cap),))
            elif int(row[0]) != cap:
                raise ValueError("ledger cap differs from its frozen cap")
            row = db.execute("SELECT value FROM meta WHERE key='arm_cap'").fetchone()
            if row is None:
                db.execute("INSERT INTO meta VALUES('arm_cap', ?)", (str(arm_cap),))
            elif int(row[0]) != arm_cap:
                raise ValueError("ledger per-arm cap differs from its frozen cap")
        self.arm_cap = arm_cap

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, arm_id: str) -> str:
        if not arm_id:
            raise ValueError("arm_id is required")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            if count >= self.cap:
                db.rollback()
                raise RuntimeError("global ChatGPT call budget exhausted")
            arm_count = db.execute("SELECT COUNT(*) FROM calls WHERE arm_id=?", (arm_id,)).fetchone()[0]
            if arm_count >= self.arm_cap:
                db.rollback()
                raise RuntimeError("per-arm ChatGPT call budget exhausted")
            call_id = "call-" + uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES(?,?, 'RESERVED_UNKNOWN', ?, NULL)",
                       (call_id, arm_id, time.time()))
            db.commit()
            return call_id
        finally:
            db.close()

    def settle(self, call_id: str, *, status: str, receipt: dict[str, Any] | None = None) -> None:
        if status not in {"COMPLETED", "FAILED", "UNKNOWN"}:
            raise ValueError("invalid call settlement status")
        with self._connect() as db:
            changed = db.execute("UPDATE calls SET status=?, receipt_json=? WHERE id=? AND status='RESERVED_UNKNOWN'",
                                 (status, json.dumps(receipt, sort_keys=True) if receipt else None,
                                  call_id)).rowcount
            if changed != 1:
                raise ValueError("call reservation missing or already settled")

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM calls").fetchone()[0])


@dataclass(frozen=True)
class VerifiedCandidate:
    arm_id: str
    source: bytes
    score: float
    verified_at_epoch: float
    evaluator_receipt: dict[str, Any]
    direction: str = "higher"


class CandidateArchive:
    """Immutable source+receipt checkpoints; chooses best verified in-deadline result."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, candidate: VerifiedCandidate, *, deadline_epoch: float) -> dict[str, Any]:
        if candidate.verified_at_epoch > deadline_epoch:
            raise ValueError("candidate verification completed after arm deadline")
        if not isinstance(candidate.score, (int, float)):
            raise ValueError("verified score must be numeric")
        if candidate.direction not in {"higher", "lower"}:
            raise ValueError("direction must be higher or lower")
        source_sha = hashlib.sha256(candidate.source).hexdigest()
        receipt = dict(candidate.evaluator_receipt)
        if receipt.get("candidate_sha256") != source_sha:
            raise ValueError("evaluator receipt does not bind the candidate source bytes")
        checkpoint_id = f"{candidate.arm_id}-{source_sha}"
        directory = self.root / checkpoint_id
        directory.mkdir(mode=0o700)
        (directory / "candidate.bin").write_bytes(candidate.source)
        (directory / "evaluator-receipt.json").write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        manifest = {"arm_id": candidate.arm_id, "candidate_sha256": source_sha,
                    "score": candidate.score, "direction": candidate.direction,
                    "verified_at_epoch": candidate.verified_at_epoch,
                    "evaluator_receipt_sha256": hashlib.sha256(
                        (directory / "evaluator-receipt.json").read_bytes()).hexdigest()}
        (directory / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n")
        return manifest

    def best(self, arm_id: str) -> dict[str, Any] | None:
        candidates = []
        for path in self.root.glob(f"{arm_id}-*/manifest.json"):
            item = read_json(path)
            candidate = path.parent / "candidate.bin"
            receipt_path = path.parent / "evaluator-receipt.json"
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != item["candidate_sha256"]:
                raise ValueError(f"candidate bytes changed: {path.parent}")
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != item["evaluator_receipt_sha256"]:
                raise ValueError(f"evaluator receipt changed: {path.parent}")
            receipt = read_json(receipt_path)
            if receipt.get("candidate_sha256") != item["candidate_sha256"]:
                raise ValueError(f"evaluator receipt is not bound to candidate: {path.parent}")
            candidates.append(item)
        if not candidates:
            return None
        directions = {item.get("direction") for item in candidates}
        if len(directions) != 1 or next(iter(directions)) not in {"higher", "lower"}:
            raise ValueError("candidate checkpoints have incompatible metric directions")
        return (max if next(iter(directions)) == "higher" else min)(
            candidates, key=lambda item: item["score"])


class ArmBackend(Protocol):
    def run(self, arm_id: str, task: dict[str, Any], deadline_epoch: float,
            call_budget: int) -> dict[str, Any]: ...


def run_fake_series(tasks: list[dict[str, Any]], root: Path,
                    backend_factory: Callable[[str], ArmBackend]) -> dict[str, Any]:
    """Run deterministic fake backends only; never constructs a live backend."""
    if len(tasks) != 12 or len({t.get("task_id") for t in tasks}) != 12:
        raise ValueError("offline acceptance run requires exactly 12 distinct tasks")
    ledger = GlobalCallLedger(root / "budget.sqlite3")
    runs = []
    for task in tasks:
        for system in ("bananloop_full", "codex_direct"):
            arm_id = f"{task['task_id']}--{system}"
            backend = backend_factory(system)
            result = backend.run(arm_id, task, time.time() + ARM_SECONDS, PER_ARM_CALLS)
            runs.append({"arm_id": arm_id, "result": result})
    return {"runs": runs, "call_reservations": ledger.count(), "model_calls": 0,
            "harbor_evaluations": 0, "ai_judge_calls": 0, "status": "OFFLINE_FAKE_COMPLETE"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight",))
    parser.add_argument("--selection", type=Path, default=SELECTION)
    parser.add_argument("--codex-help-file", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    help_text = args.codex_help_file.read_text(encoding="utf-8") if args.codex_help_file else None
    report = offline_preflight(selection_path=args.selection, codex_help=help_text)
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.out:
        if args.out.exists() or args.out.is_symlink():
            parser.error("--out must be a new path")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "READY_NON_RPDI_DIAGNOSTIC" else 2


if __name__ == "__main__":
    raise SystemExit(main())
