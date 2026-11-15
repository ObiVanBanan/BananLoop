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
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = Path(__file__).with_name("task-catalogue.json")
SOURCE_ACCESS_DECLARATION = Path(__file__).with_name("source-access-declaration.json")
ARM_SECONDS = 3600
HARBOR_BINARY = Path("/home/sonik/bananloop-benchmark-env/harbor-venv/bin/harbor")
EXPECTED_TASK_COUNT = 12
TASK_IDS = (
    "budgeted-imputation-mcar50", "carps-star-discrepancy-subset-select",
    "cpu-decoder-graph-executor", "cpu-llm-decode-throughput",
    "dctabeval-aeac-pooled-cat-statistics", "label-efficient-risk-estimator",
    "shortest-valid-ci-l2-ece", "sketched-newton-cov-estimator",
    "sopcc-online-chance-constrained-policy",
    "act-tensor-sparse-panel-imputation-r2",
    "causalpfn-cate-pehe-ihdp-surfaceb", "hicard-latent-encoder",
)


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def host_capacity_report() -> dict[str, Any]:
    logical_cpus = os.cpu_count()
    total_memory_mb = available_memory_mb = None
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, raw = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable"}:
                memory[name] = int(raw.strip().split()[0]) // 1024
        total_memory_mb = memory.get("MemTotal")
        available_memory_mb = memory.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    cpu_slots = logical_cpus // 8 if isinstance(logical_cpus, int) else 0
    memory_slots = available_memory_mb // 14336 if isinstance(available_memory_mb, int) else 0
    slots = min(cpu_slots, memory_slots) if memory_slots else cpu_slots
    return {
        "logical_cpus": logical_cpus, "total_memory_mb": total_memory_mb,
        "available_memory_mb": available_memory_mb,
        "per_task_requested_cpus": 8, "per_task_requested_memory_mb": 14336,
        "cpu_bound_arm_slots": cpu_slots, "currently_memory_bound_arm_slots": memory_slots,
        "conservative_arm_slots": slots,
        "minimum_series_wall_hours_at_one_slot": 24,
        "disk_free_bytes": shutil.disk_usage(ROOT).free,
    }


def inspect_docker_image(image: str, docker_binary: str = "docker") -> str | None:
    try:
        result = subprocess.run([docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if len(value) == 71 and value.startswith("sha256:") else None


def inspect_harbor(binary: Path = HARBOR_BINARY) -> str | None:
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return None
    try:
        result = subprocess.run([str(binary), "--version"], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_catalogue(catalogue: dict[str, Any]) -> None:
    """Validate the content address and required source/evaluator pins."""
    declared = catalogue.get("catalogue_sha256")
    body = {key: value for key, value in catalogue.items() if key != "catalogue_sha256"}
    actual = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if not isinstance(declared, str) or declared != actual:
        raise ValueError("task catalogue content digest does not match its contents")
    tasks = catalogue.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("task catalogue has no tasks list")
    ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
    if len(ids) != EXPECTED_TASK_COUNT or set(ids) != set(TASK_IDS):
        raise ValueError("task catalogue must contain exactly 12 distinct curated tasks")
    for item in tasks:
        adapter, resources = item.get("adapter", {}), item.get("resources", {})
        storage = resources.get("storage_mb")
        if (not all(isinstance(item.get(key), str) and len(item[key]) == 64
                    for key in ("task_sha256", "instruction_sha256", "dockerfile_sha256"))
                or not adapter.get("adapter_id")
                or adapter.get("direction") not in {"MAXIMIZE", "MINIMIZE"}
                or resources.get("cpus") != 8 or resources.get("memory_mb") != 14336
                or resources.get("gpus") != 0 or isinstance(storage, bool)
                or not isinstance(storage, int)):
            raise ValueError(f"task catalogue pins are incomplete or malformed: {item.get('task_id')}")


def validate_source_access_declaration(path: Path = SOURCE_ACCESS_DECLARATION) -> dict[str, Any]:
    declaration = read_json(path)
    expected_affected = {
        "act-tensor-sparse-panel-imputation-r2",
        "causalpfn-cate-pehe-ihdp-surfaceb",
        "hicard-latent-encoder",
    }
    if (declaration.get("schema") != "bananloop-autonomous-v01-source-access-disclosure-v1"
            or declaration.get("decision") != "ACCEPTED_FOR_INTERNAL_NON_RPDI14_DIAGNOSTIC_WITH_LIMITATION"
            or set(declaration.get("affected_task_ids", ())) != expected_affected
            or declaration.get("candidate_or_scorer_execution") is not False
            or declaration.get("model_or_harbor_evaluation_calls") is not False):
        raise ValueError("source-access disclosure is missing, changed, or does not match the owner disposition")
    return declaration


def _implementation_gates() -> dict[str, bool]:
    """Check for linked implementation surfaces required before READY is possible."""
    return {
        "codex_direct_backend": callable(globals().get("CodexDirectBackend")),
        "codex_final_artifact_export": callable(
            getattr(globals().get("CodexDirectBackend"), "snapshot_final_artifacts", None)
        ),
        "bananloop_full_backend": callable(globals().get("BananLoopFullBackend")),
        "benchmark_bound_pivot_authorizer": callable(globals().get("authorize_benchmark_pivot", None)),
        "24_arm_result_archiver": callable(globals().get("archive_series_results", None)),
    }


def build_manifest(*, catalogue: dict[str, Any], seed: int,
                   model: str, reasoning: str, codex_version: str,
                   full_feature_flags: dict[str, Any]) -> dict[str, Any]:
    """Freeze the owner-curated 12 tasks and reproducibly randomize arm order."""
    validate_catalogue(catalogue)
    access_declaration = validate_source_access_declaration()
    tasks_in = catalogue["tasks"]
    task_map = {item.get("task_id"): item for item in tasks_in if isinstance(item, dict)}
    if set(task_map) != set(TASK_IDS):
        raise ValueError("catalogue must contain exactly the 12 owner-curated ARE tasks")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("selection seed must be an integer")
    if not model or not reasoning or not codex_version or not full_feature_flags:
        raise ValueError("model, reasoning, Codex version, and Full flags are required")
    rng = random.Random(seed)
    tasks = []
    for index, task_id in enumerate(TASK_IDS, start=1):
        task = task_map[task_id]
        adapter = task.get("adapter", {})
        if (not all(task.get(key) for key in ("task_sha256", "instruction_sha256", "dockerfile_sha256"))
                or not adapter.get("adapter_id") or adapter.get("direction") not in {"MAXIMIZE", "MINIMIZE"}):
            raise ValueError(f"task lacks source pins or scorer contract: {task_id}")
        arms = ["bananloop_full", "codex_direct"]
        rng.shuffle(arms)
        tasks.append({"pair_id": f"are-{index:02d}", "task": dict(task),
                      "arm_order": arms,
                      "arms": [{"system": system, "deadline_seconds": ARM_SECONDS,
                                "usage_policy": "OBSERVE_ONLY"}
                               for system in arms]})
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "kind": "ARE_PRIMARY_ONLY",
        "seed": seed,
        "catalogue_sha256": catalogue.get("catalogue_sha256"),
        "source_revision": catalogue.get("source_revision"),
        "model": model,
        "reasoning": reasoning,
        "codex_version": codex_version,
        "full_feature_flags": full_feature_flags,
        "seconds_per_arm": ARM_SECONDS,
        "call_limit_policy": "OBSERVE_ONLY",
        "judge_policy": "after_all_24_arms_terminal_and_frozen",
        "integrity_note": (
            "Local are-grader-ctx source snippets for the three new tasks were inspected during offline contract research. "
            "Owner accepted continued use for an internal non-RPDI-14 diagnostic with disclosure; no blind/independent holdout claim is permitted."
        ),
        "operator_private_source_exposure": True,
        "source_access_declaration_sha256": hashlib.sha256(
            SOURCE_ACCESS_DECLARATION.read_bytes()).hexdigest(),
        "source_access_affected_task_ids": sorted(access_declaration["affected_task_ids"]),
        "tasks": tasks,
        "board_games": "separate_secondary_experiment_requires_separate_approval_and_budget",
    }
    manifest["manifest_sha256"] = canonical_sha(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    return manifest


def offline_preflight(*, catalogue_path: Path = CATALOGUE,
                      codex_auth_status: str | None = None,
                      codex_version: str | None = None,
                      harbor_version: str | None = None,
                      image_inspector: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """Check frozen task inputs and local runtime pins without running any agent."""
    catalogue = read_json(catalogue_path)
    try:
        validate_catalogue(catalogue)
    except ValueError as exc:
        return {
            "schema": "bananloop-autonomous-v01-preflight-v1",
            "status": "PREP_BLOCKED",
            "catalogue_sha256": catalogue.get("catalogue_sha256"),
            "catalogue_file_sha256": hashlib.sha256(catalogue_path.read_bytes()).hexdigest(),
            "planned_distinct_tasks": EXPECTED_TASK_COUNT,
            "arms": EXPECTED_TASK_COUNT * 2,
            "arm_deadline_seconds": ARM_SECONDS,
            "capacity": host_capacity_report(),
            "call_limit_policy": "OBSERVE_ONLY",
            "blockers": [f"PREP_BLOCKED_CATALOGUE_INTEGRITY: {exc}"],
            "live_models_started": False, "harbor_started": False,
            "ai_judge_started": False,
        }
    tasks = catalogue.get("tasks", [])
    blockers: list[str] = []
    try:
        access_declaration = validate_source_access_declaration()
        access_declaration_sha256 = hashlib.sha256(SOURCE_ACCESS_DECLARATION.read_bytes()).hexdigest()
    except ValueError as exc:
        access_declaration = None
        access_declaration_sha256 = None
        blockers.append(f"PREP_BLOCKED_SOURCE_ACCESS_DISCLOSURE: {exc}")
    implementation_gates = _implementation_gates()
    for gate, ready in implementation_gates.items():
        if not ready:
            blockers.append(f"PREP_BLOCKED_IMPLEMENTATION: {gate} is not implemented and linked to the series runner")
    ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
    if set(ids) != set(TASK_IDS) or len(ids) != EXPECTED_TASK_COUNT:
        blockers.append("PREP_BLOCKED_TASK_CATALOGUE: curated 12 task set/pins are incomplete")
    if catalogue.get("source_revision") != "23c0d92e11af1dea821f972b5f0a739060958002":
        blockers.append("PREP_BLOCKED_SOURCE_REVISION: AutoResearchExam source pin changed")
    for item in tasks:
        if not isinstance(item, dict):
            continue
        adapter = item.get("adapter", {})
        if not adapter.get("adapter_id"):
            blockers.append(f"{item.get('task_id')}: task-specific adapter is missing")
        if item.get("integration_status") != "PINNED_DEV_VALIDATION_ADAPTER":
            blockers.append(f"{item.get('task_id')}: public DEV scorer exists but trusted fixture/protocol/Harbor adapter is incomplete")
        image, image_id = item.get("public_dev_image"), item.get("public_dev_image_id")
        if not image or not image_id:
            blockers.append(f"{item.get('task_id')}: public DEV image has not been content-pinned")
        elif image_inspector is not None and image_inspector(image) != image_id:
            blockers.append(f"{item.get('task_id')}: local public DEV image ID differs from catalogue pin")
    if codex_auth_status != "Logged in using ChatGPT":
        blockers.append("Codex Direct subscription authentication has not been verified")
    if not codex_version:
        blockers.append("Codex CLI version is not frozen")
    if not harbor_version:
        blockers.append("Pinned Harbor executable is unavailable or its version could not be read")
    block_unique = list(dict.fromkeys(blockers))
    blockers = block_unique
    return {
        "schema": "bananloop-autonomous-v01-preflight-v1",
        "status": "PREP_BLOCKED" if blockers else "READY_NON_RPDI_DIAGNOSTIC",
        "catalogue_sha256": catalogue.get("catalogue_sha256"),
        "catalogue_file_sha256": hashlib.sha256(catalogue_path.read_bytes()).hexdigest(),
        "eligible_task_ids": sorted(ids),
        "planned_distinct_tasks": EXPECTED_TASK_COUNT,
        "arms": EXPECTED_TASK_COUNT * 2,
        "arm_deadline_seconds": ARM_SECONDS,
        "capacity": host_capacity_report(),
        "implementation_gates": implementation_gates,
        "call_limit_policy": "OBSERVE_ONLY",
        "codex_version": codex_version,
        "codex_auth_status": codex_auth_status,
        "operator_private_source_exposure": {
            "accepted_for_internal_diagnostic": True,
            "affected_task_ids": [
                "act-tensor-sparse-panel-imputation-r2",
                "causalpfn-cate-pehe-ihdp-surfaceb",
                "hicard-latent-encoder",
            ],
            "pristine_blind_or_independent_holdout_claim_allowed": False,
            "disclosure_path": "benchmarks/autonomous_v01/source-access-declaration.json",
            "disclosure_sha256": access_declaration_sha256,
            "disclosure_valid": access_declaration is not None,
        },
        "integrity_note": (
            "Local are-grader-ctx source snippets for the three new tasks were inspected during offline contract research. "
            "Owner accepted continued use for an internal non-RPDI-14 diagnostic with disclosure; no blind/independent holdout claim is permitted."
        ),
        "source_access_declaration_sha256": access_declaration_sha256,
        "source_access_affected_task_ids": (
            sorted(access_declaration["affected_task_ids"]) if access_declaration is not None else []
        ),
        "harbor_binary": str(HARBOR_BINARY),
        "harbor_version": harbor_version,
        "blockers": blockers,
        "live_models_started": False,
        "harbor_started": False,
        "ai_judge_started": False,
    }


class UsageLedger:
    """Durable observed usage log; it records, but never caps, provider calls."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "arm_id TEXT NOT NULL, source TEXT NOT NULL, event_json TEXT NOT NULL, recorded_at REAL NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def record(self, arm_id: str, source: str, event: dict[str, Any]) -> int:
        if not arm_id or not source or not isinstance(event, dict):
            raise ValueError("usage event requires arm, source, and object payload")
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as db:
            cursor = db.execute("INSERT INTO usage_events(arm_id,source,event_json,recorded_at) VALUES(?,?,?,?)",
                                (arm_id, source, payload, time.time()))
            return int(cursor.lastrowid)

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT source,event_json FROM usage_events ORDER BY seq").fetchall()
        summary: dict[str, Any] = {"observed_events": len(rows), "model_turns_observed": 0,
                                   "input_tokens": 0, "output_tokens": 0,
                                   "unknown_usage_events": 0, "by_source": {}}
        for source, raw in rows:
            event = json.loads(raw)
            stats = summary["by_source"].setdefault(source, {"events": 0, "model_turns_observed": 0,
                "input_tokens": 0, "output_tokens": 0, "unknown_usage_events": 0})
            stats["events"] += 1
            turns = event.get("model_turns_observed", event.get("model_calls", 1 if event.get("dispatched") else 0))
            if isinstance(turns, int) and not isinstance(turns, bool) and turns >= 0:
                stats["model_turns_observed"] += turns
                summary["model_turns_observed"] += turns
            usage = event.get("usage")
            if not isinstance(usage, dict):
                stats["unknown_usage_events"] += int(bool(turns))
                summary["unknown_usage_events"] += int(bool(turns))
                continue
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    stats[key] += value
                    summary[key] += value
                else:
                    stats["unknown_usage_events"] += 1
                    summary["unknown_usage_events"] += 1
        return summary


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
    def run(self, arm_id: str, task: dict[str, Any], arm_root: Path,
            deadline_epoch: float, usage: UsageLedger) -> dict[str, Any]: ...


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_fake_series(tasks: list[dict[str, Any]], root: Path,
                    backend_factory: Callable[[str], ArmBackend]) -> dict[str, Any]:
    """Run 24 isolated injected fake arms; no real backend is imported or launched."""
    if len(tasks) != EXPECTED_TASK_COUNT or len({t.get("task_id") for t in tasks}) != EXPECTED_TASK_COUNT:
        raise ValueError("offline acceptance run requires exactly 12 distinct tasks")
    if root.exists() or root.is_symlink():
        raise ValueError("offline fake-series state root must be new")
    root.mkdir(parents=True, mode=0o700)
    usage = UsageLedger(root / "observed-usage.sqlite3")
    runs = []
    for task in tasks:
        for system in task.get("arm_order", ("bananloop_full", "codex_direct")):
            if system not in {"bananloop_full", "codex_direct"}:
                raise ValueError("manifest contains unknown system")
            arm_id = f"{task['task_id']}--{system}"
            arm_root = root / arm_id
            arm_root.mkdir(mode=0o700)
            deadline = time.time() + ARM_SECONDS
            backend = backend_factory(system)
            result = backend.run(arm_id, task, arm_root, deadline, usage)
            record = {"arm_id": arm_id, "task_id": task["task_id"], "system": system,
                      "deadline_epoch": deadline, "result": result}
            _atomic_json(arm_root / "result.json", record)
            runs.append(record)
    report = {"status": "OFFLINE_FAKE_COMPLETE", "runs": runs,
              "usage": usage.summary(), "model_calls": 0,
              "harbor_evaluations": 0, "ai_judge_calls": 0}
    _atomic_json(root / "summary.json", report)
    manifest = {
        "schema": "bananloop-autonomous-v01-fake-manifest-v1",
        "kind": "OFFLINE_FAKE_ONLY",
        "seconds_per_arm": ARM_SECONDS,
        "task_ids": sorted(t["task_id"] for t in tasks),
        "arm_ids": sorted(record["arm_id"] for record in runs),
        "manifest_sha256": None,
    }
    manifest["manifest_sha256"] = canonical_sha(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    archive_series_results(manifest=manifest, run_records=runs, result_root=root,
                           archive_root=root / "final-archive")
    return report


def archive_series_results(*, manifest: dict[str, Any], run_records: list[dict[str, Any]],
                           result_root: Path, archive_root: Path) -> dict[str, Any]:
    """Freeze all terminal arm records and artifacts into a hash-indexed archive.

    This function is intentionally agnostic to model/evaluator execution. It
    refuses incomplete or duplicate series so a partial archive cannot be
    mistaken for a complete 24-arm result.
    """
    if archive_root.exists() or archive_root.is_symlink():
        raise ValueError("series archive root must be new")
    if not isinstance(manifest, dict) or not isinstance(run_records, list):
        raise ValueError("series archive requires manifest and run records")
    digest = manifest.get("manifest_sha256")
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if not isinstance(digest, str) or digest != canonical_sha(body):
        raise ValueError("series manifest digest does not match its contents")
    task_entries = manifest.get("tasks", ())
    if task_entries and all(isinstance(item, dict) for item in task_entries):
        expected_pairs: dict[str, set[str]] = {}
        for entry in task_entries:
            task = entry.get("task", {})
            task_id = task.get("task_id") if isinstance(task, dict) else None
            arms = entry.get("arms", ())
            if not isinstance(task_id, str) or not isinstance(arms, list):
                raise ValueError("series manifest task/arm records are malformed")
            expected_pairs[task_id] = {arm.get("system") for arm in arms if isinstance(arm, dict)}
        expected_task_ids = set(expected_pairs)
        expected = {f"{task_id}--{system}" for task_id, systems in expected_pairs.items()
                    for system in systems}
    else:
        expected_task_ids = set(manifest.get("task_ids", ()))
        expected = set(manifest.get("arm_ids", ()))
        expected_pairs = {task_id: {"bananloop_full", "codex_direct"}
                          for task_id in expected_task_ids}
    observed = [record.get("arm_id") for record in run_records if isinstance(record, dict)]
    observed_pairs: dict[str, set[str]] = {}
    for record in run_records:
        if not isinstance(record, dict):
            continue
        task_id, system = record.get("task_id"), record.get("system")
        if not isinstance(task_id, str) or system not in {"bananloop_full", "codex_direct"}:
            raise ValueError("every series arm must identify its task and system")
        observed_pairs.setdefault(task_id, set()).add(system)
    if (len(expected) != EXPECTED_TASK_COUNT * 2 or len(observed) != EXPECTED_TASK_COUNT * 2
            or set(observed) != expected or len(set(observed)) != len(observed)
            or expected_task_ids != set(TASK_IDS) or expected_pairs != {
                task_id: {"bananloop_full", "codex_direct"} for task_id in TASK_IDS}
            or observed_pairs != expected_pairs):
        raise ValueError("series archive requires exactly the 24 unique manifest arms")
    if any(not isinstance(record.get("result"), dict)
           or not isinstance(record["result"].get("status"), str)
           for record in run_records):
        raise ValueError("every archived arm must have a terminal result status")
    if result_root.is_symlink() or not result_root.is_dir():
        raise ValueError("result root must be a regular directory")
    secret_pattern = re.compile(
        rb"(?:sk-[A-Za-z0-9]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|"
        rb"(?i:OPENAI_API_KEY|AZURE_OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*[:=]\s*\S+|"
        rb"(?i:authorization\s*:\s*bearer\s+)\S+)"
    )
    archive_root.mkdir(parents=True, mode=0o700)
    try:
        for record in run_records:
            arm_id = record["arm_id"]
            source = result_root / arm_id
            target = archive_root / arm_id
            if source.is_symlink() or not source.is_dir():
                raise ValueError(f"arm artifact root missing or unsafe: {arm_id}")
            target.mkdir(mode=0o700)
            for path in source.rglob("*"):
                relative = path.relative_to(source)
                if path.is_symlink():
                    raise ValueError(f"symlink found in arm artifacts: {arm_id}/{relative}")
                if not path.is_file():
                    continue
                if any(part == ".git" or part.lower().startswith((".env", "secret", "credential"))
                       for part in relative.parts):
                    raise ValueError(f"sensitive artifact path refused: {arm_id}/{relative}")
                content = path.read_bytes()
                if secret_pattern.search(content):
                    raise ValueError(f"credential-like content refused from archive: {arm_id}/{relative}")
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with output.open("xb") as stream:
                    stream.write(content)
                os.chmod(output, 0o600)
        _atomic_json(archive_root / "experiment-manifest.json", manifest)
        summary = {
            "status": "ARCHIVED_24_ARMS",
            "arm_count": len(run_records),
            "task_count": EXPECTED_TASK_COUNT,
            "results": sorted(run_records, key=lambda item: item["arm_id"]),
            "model_turns_observed_total": sum(
                int(item["result"].get("model_turns_observed", 0) or 0)
                for item in run_records
            ),
            "ai_judge_calls": 0,
        }
        _atomic_json(archive_root / "summary.json", summary)
        files = sorted(path for path in archive_root.rglob("*") if path.is_file())
        index = {
            "schema": "bananloop-autonomous-v01-archive-index-v1",
            "manifest_sha256": digest,
            "files": [{"path": path.relative_to(archive_root).as_posix(),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "size_bytes": path.stat().st_size}
                      for path in files],
        }
        _atomic_json(archive_root / "SHA256SUMS.json", index)
        return index
    except Exception:
        shutil.rmtree(archive_root, ignore_errors=True)
        raise


def parse_codex_jsonl_usage(path: Path) -> dict[str, Any]:
    """Summarize observed Codex JSONL turn and token events; malformed tail is unknown."""
    turns = 0
    input_tokens = output_tokens = 0
    unknown = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            unknown = True
            continue
        if not isinstance(event, dict):
            unknown = True
            continue
        if event.get("type") == "turn.completed":
            turns += 1
            usage = event.get("usage") or event.get("token_usage")
            if isinstance(usage, dict):
                for key, target in (("input_tokens", "input"), ("output_tokens", "output")):
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        if target == "input":
                            input_tokens += value
                        else:
                            output_tokens += value
                    else:
                        unknown = True
            else:
                unknown = True
    return {"model_turns_observed": turns, "usage": None if unknown else {
        "input_tokens": input_tokens, "output_tokens": output_tokens},
        "usage_unknown": unknown}


class CodexDirectBackend:
    """Persistent iterative Codex subscription run with a wall-clock stop."""

    def __init__(self, *, codex_binary: str, model: str, reasoning: str,
                 task_instruction: Path, dev_command: list[str],
                 initial_repo: Path, base_sha: str):
        self.codex_binary = codex_binary
        self.model = model
        self.reasoning = reasoning
        self.task_instruction = task_instruction
        self.dev_command = dev_command
        self.initial_repo = initial_repo
        self.base_sha = base_sha

    def run(self, arm_id: str, task: dict[str, Any], arm_root: Path,
            deadline_epoch: float, usage: UsageLedger) -> dict[str, Any]:
        deadline_monotonic = time.monotonic() + max(0.0, deadline_epoch - time.time())
        remaining = lambda: deadline_monotonic - time.monotonic()
        repo = arm_root / "repo"

        def terminal(status: str, *, model_turns_observed: int = 0,
                     **extra: Any) -> dict[str, Any]:
            # Every terminal path, including failures before Codex starts, must
            # archive the as-is deliverable state so the series has no silent gaps.
            artifact = self.snapshot_final_artifacts(task=task, repo=repo, arm_root=arm_root)
            return {"status": status, "model_turns_observed": model_turns_observed,
                    "final_artifact": artifact, **extra}

        if remaining() <= 0:
            return terminal("TIMED_OUT_BEFORE_CLONE")
        try:
            clone = subprocess.run(["git", "clone", "--no-hardlinks", "--quiet",
                                    str(self.initial_repo), str(repo)],
                                   capture_output=True, timeout=min(90, remaining()))
        except subprocess.TimeoutExpired:
            return terminal("TIMED_OUT_DURING_CLONE")
        if clone.returncode != 0:
            return terminal("FAILED_TO_CLONE_INITIAL_REPOSITORY")
        if remaining() <= 0:
            return terminal("TIMED_OUT_BEFORE_CHECKOUT")
        try:
            checkout = subprocess.run(["git", "-C", str(repo), "checkout", "--detach", self.base_sha],
                                      capture_output=True, timeout=min(30, remaining()))
        except subprocess.TimeoutExpired:
            return terminal("TIMED_OUT_DURING_CHECKOUT")
        if checkout.returncode != 0:
            return terminal("FAILED_TO_PIN_INITIAL_REVISION")
        instruction = self.task_instruction.read_text(encoding="utf-8")
        (repo / "TASK-INSTRUCTION.md").write_text(instruction, encoding="utf-8")
        prompt = (
            "You are the Codex Direct arm in a preregistered AutoResearchExam study. "
            "Work only in this repository. Iteratively inspect the task, implement and improve "
            "the required candidate, run the pinned public DEV evaluator, read its numeric feedback, "
            "and continue improving while useful until the absolute task deadline. Keep your "
            "final deliverable in the task's declared output directory; the harness captures those "
            "bytes as-is when time expires. Never access private "
            "grader or hidden data, never contact external services, and do not compare against "
            "another arm. Task:\n\n" + instruction + "\n\nPinned DEV command: "
            + shlex.join(self.dev_command) + f"\nReasoning setting: {self.reasoning}."
        )
        log = arm_root / "codex-events.jsonl"
        stderr = arm_root / "codex-stderr.log"
        env = {k: v for k, v in os.environ.items() if k not in {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"}}
        if remaining() <= 0:
            return terminal("TIMED_OUT_BEFORE_START")
        command = [self.codex_binary, "exec", "--json", "--sandbox", "workspace-write",
                   "--model", self.model, "-c", f"model_reasoning_effort={self.reasoning}",
                   "-C", str(repo), "--output-last-message",
                   str(arm_root / "codex-last-message.txt"), "-"]
        with log.open("wb") as stdout_file, stderr.open("wb") as stderr_file:
            process = subprocess.Popen(command, cwd=repo, stdin=subprocess.PIPE,
                                       stdout=stdout_file, stderr=stderr_file, env=env,
                                       start_new_session=(os.name != "nt"))
            try:
                process.communicate(prompt.encode("utf-8"),
                                    timeout=max(0.1, remaining()))
                status = "COMPLETED" if process.returncode == 0 else "FAILED"
            except subprocess.TimeoutExpired:
                status = "TIMED_OUT"
                if os.name != "nt":
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
        observed = parse_codex_jsonl_usage(log)
        usage.record(arm_id, "codex_direct", observed)
        final_artifact = self.snapshot_final_artifacts(task=task, repo=repo, arm_root=arm_root)
        return {"status":status, "returncode":process.returncode,
                "model_turns_observed":observed["model_turns_observed"],
                "usage_unknown":observed["usage_unknown"],
                "repo":str(repo), "codex_events":str(log),
                "final_artifact":final_artifact}

    @staticmethod
    def snapshot_final_artifacts(*, task: dict[str, Any], repo: Path,
                                 arm_root: Path) -> dict[str, Any]:
        """Copy the task output directory as-is, preserving missing/invalid status."""
        adapter = task.get("adapter", {})
        relative_entry = adapter.get("candidate_path") if isinstance(adapter, dict) else None
        if not isinstance(relative_entry, str) or not relative_entry or "\\" in relative_entry:
            return {"status": "INVALID_TASK_OUTPUT_PATH", "files": []}
        entry = Path(relative_entry)
        if entry.is_absolute() or any(part in {"", ".", ".."} for part in entry.parts):
            return {"status": "INVALID_TASK_OUTPUT_PATH", "files": []}
        repo_real = repo.resolve()
        output_dir = repo.joinpath(*entry.parts[:-1])
        if output_dir.is_symlink() or not output_dir.resolve(strict=False).is_relative_to(repo_real):
            return {"status": "INVALID_TASK_OUTPUT_PATH", "files": []}
        target = arm_root / "final-artifacts"
        if target.exists() or target.is_symlink():
            return {"status": "ARTIFACT_SNAPSHOT_ALREADY_EXISTS", "files": []}
        target.mkdir(mode=0o700)
        entry_exists = False
        files: list[dict[str, Any]] = []
        violations: list[str] = []
        total_bytes = 0
        source_files = sorted(output_dir.rglob("*")) if output_dir.is_dir() else []
        if len(source_files) > 256:
            violations.append("too_many_output_entries")
            source_files = source_files[:256]
        for path in source_files:
            if path.is_dir() and not path.is_symlink():
                continue
            rel = path.relative_to(output_dir)
            if path.is_symlink():
                violations.append(f"symlink:{rel.as_posix()}")
                continue
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError:
                violations.append(f"unreadable:{rel.as_posix()}")
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                violations.append(f"nonregular_or_hardlinked:{rel.as_posix()}")
                continue
            if metadata.st_size > 64_000_000 or total_bytes + metadata.st_size > 128_000_000:
                violations.append(f"oversized:{rel.as_posix()}")
                continue
            try:
                content = path.read_bytes()
            except OSError:
                violations.append(f"unreadable:{rel.as_posix()}")
                continue
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with out.open("xb") as stream:
                stream.write(content)
            os.chmod(out, 0o400)
            files.append({"path": rel.as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
                          "size_bytes": len(content)})
            total_bytes += len(content)
            if rel.as_posix() == entry.name:
                entry_exists = True
        status = "CAPTURED" if entry_exists and not violations else (
            "PARTIAL_INVALID" if files or violations else "MISSING_OUTPUT"
        )
        return {"status": status, "entrypoint": relative_entry,
                "entrypoint_sha256": next((item["sha256"] for item in files
                                           if item["path"] == entry.name), None),
                "files": files, "violations": violations, "total_bytes": total_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight",))
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--codex-auth-status")
    parser.add_argument("--codex-version")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--harbor-binary", type=Path, default=HARBOR_BINARY)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    auth_status = args.codex_auth_status
    if auth_status is None:
        try:
            auth = subprocess.run(["codex", "login", "status"], capture_output=True,
                                  text=True, timeout=10, check=False)
            auth_status = (auth.stdout or auth.stderr).strip() if auth.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            auth_status = None
    codex_version = args.codex_version
    if codex_version is None:
        try:
            version = subprocess.run(["codex", "--version"], capture_output=True,
                                     text=True, timeout=10, check=False)
            codex_version = version.stdout.strip() if version.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            codex_version = None
    harbor_version = inspect_harbor(args.harbor_binary)
    report = offline_preflight(catalogue_path=args.catalogue,
                               codex_auth_status=auth_status,
                               codex_version=codex_version,
                               harbor_version=harbor_version,
                               image_inspector=lambda image: inspect_docker_image(image, args.docker_binary))
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.out:
        if args.out.is_symlink() or (args.out.exists() and not args.out.is_file()):
            parser.error("--out must be a regular nonsymlink file path")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{args.out.name}.", suffix=".tmp", dir=args.out.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, args.out)
        finally:
            temporary.unlink(missing_ok=True)
    print(encoded, end="")
    return 0 if report["status"] == "READY_NON_RPDI_DIAGNOSTIC" else 2


if __name__ == "__main__":
    raise SystemExit(main())
