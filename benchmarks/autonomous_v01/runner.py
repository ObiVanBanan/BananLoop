#!/usr/bin/env python3
"""Offline-first paired ARE runner primitives.

This module deliberately does not start models or Harbor. A live backend must
be supplied by a separately authorized launcher after ``preflight`` is READY.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import inspect
import os
import random
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
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


def validate_task_runtime_descriptors(runtime_root: Path, tasks: list[dict[str, Any]]) -> list[str]:
    """Verify per-task source, fixture, overlay, and launch pins without running them."""
    from benchmarks.autoresearchexam.prepare_safe_harbor_task import validate_local_public_dev_overlay

    blockers: list[str] = []
    root_unsafe = runtime_root.is_symlink() or not runtime_root.is_dir()
    for task in tasks:
        task_id = task.get("task_id", "<unknown>")
        path = runtime_root / f"{task_id}.json"
        if root_unsafe or path.is_symlink() or not path.is_file():
            blockers.append(f"{task_id}: task runtime descriptor is missing: {path}")
            continue
        try:
            descriptor = read_json(path)
            adapter = task.get("adapter", {})
            if (descriptor.get("schema") != "bananloop-autonomous-v01-task-runtime-v1"
                    or descriptor.get("task_id") != task_id
                    or descriptor.get("adapter_id") != adapter.get("adapter_id")
                    or descriptor.get("candidate_path") != adapter.get("candidate_path")
                    or descriptor.get("task_sha256") != task.get("task_sha256")
                    or descriptor.get("instruction_sha256") != task.get("instruction_sha256")
                    or descriptor.get("public_dev_image_id") != task.get("public_dev_image_id")):
                raise ValueError("descriptor does not match the frozen catalogue pins")
            source = Path(descriptor["source_task_root"]).expanduser()
            fixture = Path(descriptor["fixture_path"]).expanduser()
            overlay = Path(descriptor["overlay_path"]).expanduser()
            for location in (source, fixture, overlay):
                if location.is_symlink():
                    raise ValueError("source, fixture, or overlay path is a symlink")
            instruction_path, task_path = source / "instruction.md", source / "task.toml"
            overlay_task = overlay / "task.toml"
            if (not instruction_path.is_file() or instruction_path.is_symlink()
                    or hashlib.sha256(instruction_path.read_bytes()).hexdigest() != task["instruction_sha256"]
                    or not task_path.is_file() or task_path.is_symlink()
                    or hashlib.sha256(task_path.read_bytes()).hexdigest() != task["task_sha256"]):
                raise ValueError("source task instruction/task.toml SHA differs from catalogue")
            if (not fixture.is_file() or fixture.is_symlink()
                    or hashlib.sha256(fixture.read_bytes()).hexdigest() != descriptor.get("fixture_sha256")):
                raise ValueError("evaluator fixture is missing or its SHA differs from descriptor")
            if (not overlay.is_dir() or not overlay_task.is_file() or overlay_task.is_symlink()
                    or hashlib.sha256(overlay_task.read_bytes()).hexdigest()
                    != descriptor.get("overlay_task_sha256")):
                raise ValueError("safe Harbor overlay task.toml is missing or changed")
            validate_local_public_dev_overlay(
                source, overlay, task_id=task_id, public_output_path=adapter["public_output_path"],
            )
            dev_command = descriptor.get("dev_command")
            if (not isinstance(dev_command, list) or not dev_command
                    or any(not isinstance(item, str) or not item for item in dev_command)):
                raise ValueError("task-specific public DEV command is missing")
            if adapter.get("adapter_id") not in {
                "act-tensor-public-v1", "causalpfn-public-v1", "hicard-public-v1",
            }:
                contract_path = Path(descriptor["mcp_contract_path"]).expanduser()
                if (contract_path.is_symlink() or not contract_path.is_file()
                        or hashlib.sha256(contract_path.read_bytes()).hexdigest()
                        != descriptor.get("mcp_contract_file_sha256")):
                    raise ValueError("public MCP contract is missing or its SHA differs")
                contract = read_json(contract_path)
                if contract.get("contract_sha256") != descriptor.get("mcp_contract_sha256"):
                    raise ValueError("public MCP contract content digest differs")
                from benchmarks.autoresearchexam.public_mcp_contract import validate_public_mcp_contract
                validate_public_mcp_contract(
                    contract, task_id=task_id, task_version=task["version"],
                    task_sha256=task["task_sha256"],
                    task_instruction_sha256=task["instruction_sha256"],
                )
                builder = descriptor.get("config_builder_command")
                if (not isinstance(builder, list) or not builder
                        or any(not isinstance(item, str) or not item for item in builder)):
                    raise ValueError("task-specific Harbor child config builder is missing")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            blockers.append(f"{task_id}: invalid task runtime descriptor: {exc}")
    return blockers


def authorize_benchmark_pivot(*, binding: dict[str, Any], pivot: dict[str, Any],
                              arm_deadline_epoch: float, now_epoch: float | None = None) -> bool:
    """Fail closed unless the Core-created child exactly matches this frozen arm."""
    now = time.time() if now_epoch is None else now_epoch
    if not isinstance(binding, dict) or not isinstance(pivot, dict):
        return False
    if (pivot.get("status") != "BENCHMARK_AUTO_CHILD_CREATED"
            or pivot.get("benchmark_binding") != binding
            or pivot.get("benchmark_binding_sha256") != canonical_sha(binding)
            or pivot.get("inherited_deadline_epoch") != arm_deadline_epoch
            or now >= arm_deadline_epoch
            or pivot.get("child_candidate_iteration_cap") is not None
            or pivot.get("child_agent_call_ceiling", 0) > 1000
            or pivot.get("child_evaluator_call_ceiling", 0) > 1000
            or pivot.get("changed_paths") != [binding.get("candidate_path")]
            or not pivot.get("child_campaign_id")
            or pivot.get("pivot_authorization") != "FROZEN_BENCHMARK_FULL_AUTO_SCOPE"):
        return False
    return True


def _implementation_gates() -> dict[str, bool]:
    """Check for linked implementation surfaces required before READY is possible."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from benchmarks.autoresearchexam.campaign_binding import require_frozen_optimization_child
    from benchmarks.autoresearchexam.public_grader import PublicAREGrader

    public_grader_parameters = set(inspect.signature(PublicAREGrader).parameters)
    return {
        "codex_direct_backend": callable(globals().get("CodexDirectBackend")),
        "codex_final_artifact_export": callable(
            getattr(globals().get("CodexDirectBackend"), "snapshot_final_artifacts", None)
        ),
        "bananloop_full_backend": callable(globals().get("BananLoopFullBackend")),
        "benchmark_bound_pivot_authorizer": (
            callable(globals().get("BananLoopFullBackend"))
            and callable(globals().get("authorize_benchmark_pivot", None))
        ),
        "inherited_deadline_public_grader": (
            callable(require_frozen_optimization_child)
            and {"deadline_epoch", "max_candidate_iterations"} <= public_grader_parameters
        ),
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
                      image_inspector: Callable[[str], str | None] | None = None,
                      runtime_root: Path | None = None) -> dict[str, Any]:
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
    resolved_runtime_root = runtime_root or Path(os.environ.get(
        "BANANLOOP_AUTONOMOUS_V01_RUNTIME_ROOT",
        "/home/sonik/bananloop-benchmark-env/autonomous-v01-task-runtime-v1",
    ))
    blockers.extend(validate_task_runtime_descriptors(resolved_runtime_root, tasks))
    for item in tasks:
        if not isinstance(item, dict):
            continue
        adapter = item.get("adapter", {})
        if not adapter.get("adapter_id"):
            blockers.append(f"{item.get('task_id')}: task-specific adapter is missing")
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
        "task_runtime_root": str(resolved_runtime_root),
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
                unknown = bool(turns) or event.get("usage_unknown") is True
                stats["unknown_usage_events"] += int(unknown)
                summary["unknown_usage_events"] += int(unknown)
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
            try:
                result = backend.run(arm_id, task, arm_root, deadline, usage)
            except Exception as exc:
                result = {"status": "BACKEND_EXCEPTION",
                          "failure_type": type(exc).__name__,
                          "detail": str(exc)[:300]}
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
                 initial_repo: Path, base_sha: str,
                 runtime_root: Path | None = None):
        self.codex_binary = codex_binary
        self.model = model
        self.reasoning = reasoning
        self.task_instruction = task_instruction
        self.dev_command = dev_command
        self.initial_repo = initial_repo
        self.base_sha = base_sha
        self.runtime_root = runtime_root.expanduser().resolve(strict=True) if runtime_root else None

    def run(self, arm_id: str, task: dict[str, Any], arm_root: Path,
            deadline_epoch: float, usage: UsageLedger) -> dict[str, Any]:
        deadline_monotonic = time.monotonic() + max(0.0, deadline_epoch - time.time())
        remaining = lambda: deadline_monotonic - time.monotonic()
        # Keep mutable worktrees beside, not inside, the immutable result root:
        # archive_series_results intentionally refuses .git metadata. Only the
        # final task artifacts and logs belong in the arm archive.
        workspace_root = arm_root.parent / ".workspaces"
        repo = workspace_root / arm_id

        def terminal(status: str, *, model_turns_observed: int = 0,
                     **extra: Any) -> dict[str, Any]:
            # Every terminal path, including failures before Codex starts, must
            # archive the as-is deliverable state so the series has no silent gaps.
            artifact = self.snapshot_final_artifacts(task=task, repo=repo, arm_root=arm_root)
            return {"status": status, "model_turns_observed": model_turns_observed,
                    "final_artifact": artifact, **extra}

        if workspace_root.is_symlink():
            return terminal("FAILED_UNSAFE_WORKSPACE_ROOT")
        workspace_root.mkdir(mode=0o700, exist_ok=True)
        if repo.exists() or repo.is_symlink():
            return terminal("FAILED_WORKSPACE_ALREADY_EXISTS")

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
        instruction_path = self.task_instruction
        dev_command = self.dev_command
        if self.runtime_root is not None:
            descriptor_path = self.runtime_root / f"{task['task_id']}.json"
            if descriptor_path.is_symlink() or not descriptor_path.is_file():
                return terminal("FAILED_TASK_RUNTIME_DESCRIPTOR")
            try:
                descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
                source_root = Path(descriptor["source_task_root"]).expanduser()
                instruction_path = source_root / "instruction.md"
                pinned = (descriptor.get("schema") == "bananloop-autonomous-v01-task-runtime-v1"
                          and descriptor.get("task_id") == task["task_id"]
                          and descriptor.get("task_sha256") == task.get("task_sha256")
                          and descriptor.get("instruction_sha256") == task.get("instruction_sha256"))
                if not pinned:
                    return terminal("FAILED_TASK_RUNTIME_PIN")
                configured_dev = descriptor.get("dev_command")
                if configured_dev is not None:
                    if (not isinstance(configured_dev, list) or not configured_dev
                            or any(not isinstance(item, str) or not item for item in configured_dev)):
                        return terminal("FAILED_DEV_COMMAND_PIN")
                    dev_command = configured_dev
            except (OSError, ValueError, KeyError, TypeError):
                return terminal("FAILED_TASK_RUNTIME_DESCRIPTOR")
        try:
            if (instruction_path.is_symlink() or not instruction_path.is_file()
                    or (task.get("instruction_sha256") and hashlib.sha256(
                        instruction_path.read_bytes()).hexdigest() != task["instruction_sha256"])):
                return terminal("FAILED_TASK_INSTRUCTION_PIN")
            instruction = instruction_path.read_text(encoding="utf-8")
        except OSError:
            return terminal("FAILED_TASK_INSTRUCTION_PIN")
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
            + shlex.join(dev_command) + f"\nReasoning setting: {self.reasoning}."
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


class BananLoopFullBackend:
    """Run the native v2 Exploration -> auto-pivot -> Harbor Optimization path.

    A task runtime descriptor is required for each task. It pins the public
    source task, fixture, safe Harbor overlay, and configuration builder; the
    parent campaign is created by the existing v2 CLI only after this arm
    starts. No model/evaluator dependency is injected into this class.
    """

    def __init__(self, *, initial_repo: Path, base_sha: str, runtime_root: Path,
                 state_root: Path, jobs_root: Path, manifest_sha256: str,
                 model: str = "gpt-6-luna", harbor_binary: Path = HARBOR_BINARY,
                 python_binary: str = sys.executable, docker_binary: str = "docker"):
        self.initial_repo = initial_repo.expanduser().resolve(strict=True)
        self.base_sha = base_sha
        self.runtime_root = runtime_root.expanduser().resolve(strict=True)
        self.state_root = state_root.expanduser().absolute()
        self.jobs_root = jobs_root.expanduser().absolute()
        self.manifest_sha256 = manifest_sha256
        self.model = model
        self.harbor_binary = harbor_binary
        self.python_binary = python_binary
        self.docker_binary = docker_binary
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ValueError("BananLoop Full backend requires the frozen series manifest SHA")

    @staticmethod
    def _kill_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        if os.name != "nt":
            import signal
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _run_until_deadline(self, command: list[str], *, cwd: Path,
                            deadline_epoch: float, stdout_path: Path,
                            stderr_path: Path) -> tuple[str, int | None]:
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            return "TIMED_OUT_BEFORE_START", None
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr,
                                           start_new_session=(os.name != "nt"))
            except OSError:
                return "FAILED_TO_START", None
            while process.poll() is None:
                if time.time() >= deadline_epoch:
                    self._kill_process(process)
                    return "TIMED_OUT", process.returncode
                time.sleep(min(0.1, max(0.01, deadline_epoch - time.time())))
            if time.time() >= deadline_epoch:
                return "TIMED_OUT", process.returncode
            return ("COMPLETED" if process.returncode == 0 else "FAILED", process.returncode)

    def _runtime(self, task_id: str) -> dict[str, Any]:
        path = self.runtime_root / f"{task_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"no frozen Full runtime descriptor for {task_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("task_id") != task_id:
            raise ValueError(f"Full runtime descriptor task identity differs for {task_id}")
        return value

    @staticmethod
    def _fill(value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, str):
            for key, replacement in replacements.items():
                value = value.replace("{" + key + "}", replacement)
            return value
        if isinstance(value, list):
            return [BananLoopFullBackend._fill(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: BananLoopFullBackend._fill(item, replacements)
                    for key, item in value.items()}
        return value

    @staticmethod
    def _read_json_object_from_output(path: Path) -> dict[str, Any]:
        """Parse the CLI's normal pretty JSON or its last complete JSONL record."""
        content = path.read_text(encoding="utf-8", errors="replace")
        try:
            value = json.loads(content)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        lines = content.splitlines()
        for start in range(len(lines) - 1, -1, -1):
            try:
                value = json.loads("\n".join(lines[start:]))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("BananLoop Exploration output contains no complete JSON object")

    def run(self, arm_id: str, task: dict[str, Any], arm_root: Path,
            deadline_epoch: float, usage: UsageLedger) -> dict[str, Any]:
        task_id = task.get("task_id")
        runtime = self._runtime(task_id)
        adapter = task.get("adapter", {})
        candidate_path = adapter.get("candidate_path") if isinstance(adapter, dict) else None
        if (runtime.get("schema") != "bananloop-autonomous-v01-task-runtime-v1"
                or runtime.get("adapter_id") != adapter.get("adapter_id")
                or not isinstance(candidate_path, str) or not candidate_path
                or runtime.get("candidate_path") != candidate_path
                or runtime.get("task_sha256") != task.get("task_sha256")
                or runtime.get("instruction_sha256") != task.get("instruction_sha256")
                or runtime.get("public_dev_image_id") != task.get("public_dev_image_id")):
            raise ValueError("Full runtime candidate or source pins differ from the frozen catalogue")
        workspace_root = arm_root.parent / ".workspaces"
        if workspace_root.is_symlink():
            raise ValueError("Full backend workspace root is a symlink")
        workspace_root.mkdir(mode=0o700, exist_ok=True)
        repo = workspace_root / arm_id
        if repo.exists() or repo.is_symlink():
            raise ValueError("Full backend workspace already exists")
        if self.state_root.is_symlink():
            raise ValueError("Full backend campaign state root is a symlink")
        self.state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        state_dir = self.state_root / arm_id
        state_dir.mkdir(mode=0o700, exist_ok=False)

        observed_calls = 0
        optimization_calls = 0
        usage_unknown = False
        source_overlay: dict[str, str] | None = None
        evaluator_scope: dict[str, Any] | None = None
        def terminal(status: str, **extra: Any) -> dict[str, Any]:
            artifact = CodexDirectBackend.snapshot_final_artifacts(
                task=task, repo=repo, arm_root=arm_root,
            )
            return {"status": status, "final_artifact": artifact,
                    "model_turns_observed": observed_calls + optimization_calls,
                    "usage_unknown": usage_unknown,
                    "deadline_epoch": deadline_epoch,
                    **({"source_overlay": source_overlay} if source_overlay else {}),
                    **({"evaluator_scope": evaluator_scope} if evaluator_scope else {}), **extra}

        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            return terminal("TIMED_OUT_BEFORE_CLONE")
        try:
            clone = subprocess.run(["git", "clone", "--no-hardlinks", "--quiet",
                                    str(self.initial_repo), str(repo)],
                                   capture_output=True, timeout=min(60, remaining))
        except subprocess.TimeoutExpired:
            return terminal("TIMED_OUT_DURING_CLONE")
        if clone.returncode != 0:
            return terminal("FAILED_TO_CLONE_INITIAL_REPOSITORY")
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            return terminal("TIMED_OUT_BEFORE_CHECKOUT")
        try:
            checkout = subprocess.run(["git", "-C", str(repo), "checkout", "--detach", self.base_sha],
                                      capture_output=True, timeout=min(30, remaining))
        except subprocess.TimeoutExpired:
            return terminal("TIMED_OUT_DURING_CHECKOUT")
        if checkout.returncode != 0:
            return terminal("FAILED_TO_PIN_INITIAL_REVISION")

        source_task_in = Path(runtime["source_task_root"]).expanduser()
        fixture_path_in = Path(runtime["fixture_path"]).expanduser()
        overlay_path_in = Path(runtime["overlay_path"]).expanduser()
        if any(path.is_symlink() for path in (source_task_in, fixture_path_in, overlay_path_in)):
            return terminal("UNSAFE_RUNTIME_SYMLINK")
        source_task = source_task_in.resolve(strict=True)
        fixture_path = fixture_path_in.resolve(strict=True)
        overlay_path = overlay_path_in.resolve(strict=True)
        from bananloop.evaluator_fixture import EvaluatorFixtureV1

        instruction_path = source_task / "instruction.md"
        task_toml = source_task / "task.toml"
        if (instruction_path.is_symlink() or not instruction_path.is_file()
                or hashlib.sha256(instruction_path.read_bytes()).hexdigest() != task["instruction_sha256"]
                or task_toml.is_symlink() or not task_toml.is_file()
                or hashlib.sha256(task_toml.read_bytes()).hexdigest() != task["task_sha256"]):
            return terminal("FAILED_TASK_SOURCE_PIN")
        fixture_digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        if runtime.get("fixture_sha256") != fixture_digest:
            return terminal("FAILED_EVALUATOR_FIXTURE_PIN")
        if (overlay_path.is_symlink() or not overlay_path.is_dir()
                or (overlay_path / "task.toml").is_symlink()
                or not (overlay_path / "task.toml").is_file()
                or runtime.get("overlay_task_sha256") != hashlib.sha256(
                    (overlay_path / "task.toml").read_bytes()).hexdigest()):
            return terminal("FAILED_SAFE_OVERLAY_PIN")
        local_instruction = ".benchmark-task-instruction.md"
        if (repo / local_instruction).exists():
            return terminal("FAILED_INSTRUCTION_PATH_COLLISION")
        candidate_file = repo / candidate_path
        if candidate_file.is_symlink() or not candidate_file.is_file():
            return terminal("FAILED_INITIAL_CANDIDATE_MISSING", candidate_path=candidate_path)
        candidate_before_overlay = hashlib.sha256(candidate_file.read_bytes()).hexdigest()
        shutil.copyfile(instruction_path, repo / local_instruction)
        (repo / local_instruction).chmod(0o444)
        try:
            subprocess.run(["git", "-C", str(repo), "add", "--", local_instruction],
                           check=True, capture_output=True, timeout=15)
            subprocess.run([
                "git", "-C", str(repo), "-c", "user.name=BananLoop Benchmark",
                "-c", "user.email=benchmark@localhost", "-c", "commit.gpgsign=false",
                "commit", "--no-verify", "--only", "-m", "Freeze public task instruction",
                "--", local_instruction,
            ], check=True, capture_output=True, timeout=30)
            overlay_commit = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
                stderr=subprocess.PIPE, text=True, timeout=15,
            ).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40}", overlay_commit):
                raise ValueError("Git returned an invalid instruction-overlay commit")
            source_overlay = {
                "base_commit": self.base_sha,
                "overlay_commit": overlay_commit,
                "instruction_path": local_instruction,
                "instruction_sha256": hashlib.sha256(instruction_path.read_bytes()).hexdigest(),
                "candidate_path": candidate_path,
                "candidate_sha256": hashlib.sha256((repo / candidate_path).read_bytes()).hexdigest(),
            }
            if source_overlay["candidate_sha256"] != candidate_before_overlay:
                return terminal("FAILED_CANDIDATE_CHANGED_BY_INSTRUCTION_OVERLAY")
            (arm_root / "source-overlay.json").write_text(
                json.dumps(source_overlay, sort_keys=True, indent=2) + "\n", encoding="utf-8",
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return terminal("FAILED_TO_FREEZE_TASK_INSTRUCTION_OVERLAY")

        source_fixture = EvaluatorFixtureV1.load(fixture_path)
        source_fixture_digest = source_fixture.content_digest()
        dev_tier = next((tier for tier in source_fixture.tiers if tier.tier == "dev"), None)
        if dev_tier is None:
            return terminal("FAILED_PUBLIC_DEV_TIER_MISSING")
        fixture = source_fixture.model_copy(update={"tiers": (dev_tier,)})
        effective_fixture_path = arm_root / "benchmark-public-dev-fixture.json"
        effective_fixture_path.write_bytes(fixture.canonical_bytes())
        effective_fixture_path.chmod(0o400)
        fixture_path = effective_fixture_path
        evaluator_scope = {
            "policy": "PUBLIC_DEV_ONLY_DURING_ARM",
            "source_fixture_sha256": source_fixture_digest,
            "effective_fixture_sha256": fixture.content_digest(),
            "included_tiers": ["dev"],
            "excluded_tiers": [tier.tier for tier in source_fixture.tiers if tier.tier != "dev"],
        }
        (arm_root / "evaluator-scope.json").write_text(
            json.dumps(evaluator_scope, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        bundle = base64.b64decode(fixture.evaluator_bundle_base64, validate=True)
        bundle_sha = hashlib.sha256(bundle).hexdigest()
        binding = {
            "schema": "bananloop-autonomous-v01-pivot-binding-v1",
            "manifest_sha256": self.manifest_sha256,
            "task_id": task_id, "task_sha256": task["task_sha256"],
            "instruction_sha256": task["instruction_sha256"],
            "adapter_id": adapter["adapter_id"],
            "execution_backend_id": fixture.execution_backend_id,
            "evaluator_fixture_sha256": fixture.content_digest(),
            "evaluator_bundle_sha256": bundle_sha,
            "candidate_path": candidate_path, "instruction_path": local_instruction,
            "public_output_path": adapter["public_output_path"],
            "public_dev_image_id": task["public_dev_image_id"],
            "metric_field": adapter["metric_field"],
            "metric_direction": adapter["direction"], "model": self.model,
            "permissions": {"read": sorted([candidate_path, local_instruction]),
                            "write": [candidate_path], "network_access": False},
            "deadline_seconds": max(1, min(3600, int(deadline_epoch-time.time()))),
        }
        goal = ("BANANLOOP_BENCHMARK_BINDING_V1:" + json.dumps(
            binding, sort_keys=True, separators=(",", ":"),
        ) + "\nSolve the frozen AutoResearchExam task. Read the public instruction file "
            + local_instruction + "; improve only the declared candidate and use the approved public evaluator."
            + f" The frozen DEV metric is {adapter['metric_id']} (field {adapter['metric_field']}, "
            + f"{adapter['direction']}, {adapter['aggregation']}).")
        repeatability = adapter.get("repeatability_runs") or {
            "cpudecoder-public-v1": 1, "sopcc-public-v1": 4,
        }.get(adapter["adapter_id"])
        if repeatability is not None:
            goal += f" The pinned public protocol requires repeatability_runs={repeatability}."
        goal += (" The final comparison uses the same frozen public DEV scorer, task data and "
                 "image for BananLoop Full and the archived Codex Direct candidates. "
                 "Evidence is limited to public DEV.")
        if adapter["adapter_id"] == "carps-public-v1":
            goal += (" Set timeout_seconds=300 for the CARPS protocol. The eight Docker "
                     "evaluations share a 180-second panel budget, with remaining time for cleanup.")
        if adapter["adapter_id"] == "imputation-public-v2":
            evidence_path = ROOT / "benchmarks/autoresearchexam/imputation-public-dev-image-evidence.json"
            evidence = read_json(evidence_path)
            if evidence["image_id"] != task["public_dev_image_id"]:
                raise ValueError("imputation DEV image evidence does not match the catalogue")
            goal += (" The local image audit compared every /app file byte-for-byte with the "
                     "public environment/app build inputs. The runner uses only the six "
                     "data/dev NPZ files and their manifest. The audit measured these hashes: "
                     + json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        if adapter["adapter_id"] == "sopcc-public-v1":
            goal += " The pinned SOPCC protocol requires timeout_seconds=3600."
        if adapter.get("adapter_id") == "sketched-newton-public-v1":
            goal += (" The pinned public DEV scorer requires timeout_seconds=1200 and "
                     "repeatability_runs=1; its score is task_score, MAXIMIZE, MEAN. "
                     "The bundle includes the Codex Direct public_dev_cli.py and Full backend "
                     "source, so the reviewer can inspect their shared DEV scoring path. "
                     "This is a public DEV diagnostic and does not claim independent holdout.")
        remaining_seconds = max(1, min(3600, int(deadline_epoch-time.time())))
        explore_command = [
            self.python_binary, "-m", "bananloop.cli", "v2", "explore",
            "--repo", str(repo), "--state-dir", str(state_dir), "--goal", goal,
            "--read-path", candidate_path, "--read-path", local_instruction,
            "--optimization-write-path", candidate_path,
            "--benchmark-auto-pivot", "--benchmark-time-only",
            "--evaluator-fixture", str(fixture_path), "--model", self.model,
            "--max-agent-calls", "1000", "--max-evaluator-calls", "1000",
            "--max-model-tokens", "100000", "--max-iterations", "1000",
            "--deadline-seconds", str(remaining_seconds),
            "--task-deadline-epoch", str(deadline_epoch),
            "--approve-up-to-calls", "1000", "--timeout-seconds", "120",
        ]
        explore_status, explore_return = self._run_until_deadline(
            explore_command, cwd=self.initial_repo, deadline_epoch=deadline_epoch,
            stdout_path=arm_root / "bananloop-exploration.jsonl",
            stderr_path=arm_root / "bananloop-exploration.stderr",
        )
        try:
            explore_report = self._read_json_object_from_output(
                arm_root / "bananloop-exploration.jsonl",
            )
        except (OSError, ValueError, IndexError):
            usage_unknown = True
            usage.record(arm_id, "bananloop_full_exploration", {
                "model_turns_observed": 0, "usage": None, "usage_unknown": True,
            })
            return terminal("EXPLORATION_REPORT_INVALID")
        request_history = explore_report.get("request_history", [])
        observed_calls = (sum(bool(item.get("dispatched")) for item in request_history
                              if isinstance(item, dict))
                          if isinstance(request_history, list) else 0)
        observed_usage = explore_report.get("usage")
        usage_unknown = not isinstance(observed_usage, dict)
        usage.record(arm_id, "bananloop_full_exploration", {
            "model_turns_observed": observed_calls,
            "usage": observed_usage if isinstance(observed_usage, dict) else None,
            "usage_unknown": not isinstance(observed_usage, dict),
        })
        if explore_status != "COMPLETED":
            return terminal(
                "TIMED_OUT_DURING_EXPLORATION" if explore_status.startswith("TIMED_OUT")
                else "EXPLORATION_FAILED", exploration_returncode=explore_return,
                failure_type=explore_report.get("failure_type"),
                failure_diagnostics=explore_report.get("failure_diagnostics", []),
            )
        pivots = [item for item in explore_report.get("optimization_pivot", [])
                  if isinstance(item, dict) and item.get("status") == "BENCHMARK_AUTO_CHILD_CREATED"]
        if len(pivots) != 1:
            return terminal(explore_report.get("status", "NO_OPTIMIZATION_CHILD"),
                            exploration_status=explore_report.get("status"))
        pivot = pivots[0]
        if not authorize_benchmark_pivot(
                binding=binding, pivot=pivot, arm_deadline_epoch=deadline_epoch):
            return terminal("BENCHMARK_PIVOT_SCOPE_MISMATCH")
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            return terminal("TIMED_OUT_DURING_PIVOT")

        prepared_dir = arm_root / "harbor-prepared"
        jobs_dir = arm_root / "harbor-jobs"
        try:
            if adapter["adapter_id"] in {
                "act-tensor-public-v1", "causalpfn-public-v1", "hicard-public-v1",
            }:
                from benchmarks.autoresearchexam.prepare_public_task_job import prepare as prepare_public_job
                prepared = prepare_public_job(
                    repo=repo, state_dir=state_dir, campaign_id=pivot["child_campaign_id"],
                    role_registry=Path(pivot["role_registry_path"]), source_task=source_task,
                    fixture_path=fixture_path, overlay=overlay_path, jobs_dir=jobs_dir,
                    output_dir=prepared_dir, adapter_id=adapter["adapter_id"],
                    deadline_epoch=deadline_epoch,
                )
            else:
                template = runtime.get("config_builder_command")
                if (not isinstance(template, list) or not template
                        or any(not isinstance(item, str) for item in template)):
                    raise ValueError("task runtime has no frozen child configuration builder")
                replacements = {
                    "python": self.python_binary, "repo": str(repo),
                    "state_dir": str(state_dir), "campaign_id": pivot["child_campaign_id"],
                    "role_registry": pivot["role_registry_path"],
                    "source_task": str(source_task), "fixture": str(fixture_path),
                    "mcp_contract_path": runtime["mcp_contract_path"],
                    "overlay": str(overlay_path), "jobs_dir": str(jobs_dir),
                    "output_dir": str(prepared_dir), "adapter_id": adapter["adapter_id"],
                    "arm_id": arm_id, "task_id": task_id,
                    "candidate_path": candidate_path,
                    "public_output_path": adapter["public_output_path"],
                    "public_dev_image_id": task["public_dev_image_id"],
                    "deadline_epoch": str(deadline_epoch),
                }
                remaining = deadline_epoch - time.time()
                if remaining <= 0:
                    return terminal("TIMED_OUT_BEFORE_HARBOR_PREPARATION")
                prepare_result = subprocess.run(
                    self._fill(template, replacements), cwd=self.initial_repo,
                    capture_output=True, text=True,
                    timeout=min(30, remaining),
                )
                if prepare_result.returncode != 0:
                    raise ValueError("frozen child configuration builder failed")
                prepared = json.loads(prepare_result.stdout)
        except subprocess.TimeoutExpired:
            return terminal("TIMED_OUT_DURING_HARBOR_PREPARATION")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return terminal("CHILD_CONFIGURATION_FAILED", error_type=type(exc).__name__)
        bl_config_path = Path(prepared["bananloop_config"])
        harbor_config_path = Path(prepared["harbor_config"])
        bl_config = json.loads(bl_config_path.read_text(encoding="utf-8"))
        bl_config["deadline_epoch"] = deadline_epoch
        bl_config["time_limit_seconds"] = max(1, min(3600, int(remaining)))
        bl_config["repo"] = str(repo)
        bl_config["state_dir"] = str(state_dir)
        bl_config_path.write_text(json.dumps(bl_config, sort_keys=True, indent=2) + "\n")
        harbor_config = json.loads(harbor_config_path.read_text(encoding="utf-8"))
        harbor_config["agents"][0]["override_timeout_sec"] = max(1, int(remaining))
        harbor_config_path.write_text(json.dumps(harbor_config, sort_keys=True, indent=2) + "\n")
        harbor_status, harbor_return = self._run_until_deadline(
            [str(self.harbor_binary), "run", "--config", str(harbor_config_path), "--disable-verification"],
            cwd=self.initial_repo, deadline_epoch=deadline_epoch,
            stdout_path=arm_root / "bananloop-optimization.stdout",
            stderr_path=arm_root / "bananloop-optimization.stderr",
        )
        final_artifact = CodexDirectBackend.snapshot_final_artifacts(
            task=task, repo=repo, arm_root=arm_root,
        )
        status = "TIMED_OUT" if harbor_status == "TIMED_OUT" else (
            "COMPLETED" if harbor_status == "COMPLETED" and final_artifact["status"] == "CAPTURED"
            else "FAILED")
        bridge_receipt = None
        if harbor_status == "COMPLETED":
            from benchmarks.autoresearchexam.series_runner import inspect_harbor_result
            try:
                bridge_receipt = inspect_harbor_result(
                    Path(harbor_config["jobs_dir"]) / harbor_config["job_name"],
                    task_id=task_id, overlay_path=str(overlay_path), record_failed_run=True,
                )
            except (OSError, ValueError, json.JSONDecodeError):
                status = "HARBOR_RECEIPT_INVALID"
                usage_unknown = True
                usage.record(arm_id, "bananloop_full_optimization", {
                    "model_turns_observed": 0, "usage": None, "usage_unknown": True,
                })
            else:
                raw_calls = bridge_receipt.get("model_calls")
                optimization_calls = raw_calls if isinstance(raw_calls, int) and not isinstance(raw_calls, bool) else 0
                raw_usage = bridge_receipt.get("token_usage")
                usage.record(arm_id, "bananloop_full_optimization", {
                    "model_turns_observed": optimization_calls,
                    "usage": raw_usage if isinstance(raw_usage, dict) else None,
                    "usage_unknown": (raw_calls == "UNKNOWN" or not isinstance(raw_usage, dict)
                                      or any(not isinstance(raw_usage.get(key), int)
                                             for key in ("input_tokens", "output_tokens"))),
                })
                usage_unknown = usage_unknown or raw_calls == "UNKNOWN" or not isinstance(raw_usage, dict)
                if bridge_receipt.get("model_calls") == 1000 or bridge_receipt.get("bananloop_status") in {
                    "LIMIT_EXHAUSTED", "STEP_LIMIT", "ITERATION_LIMIT",
                }:
                    status = "LIMIT_EXHAUSTED"
                elif bridge_receipt.get("public_submission_unknown"):
                    status = "INTERRUPTED_UNKNOWN"
                elif bridge_receipt.get("bananloop_status") == "RUN_FAILED":
                    status = "OPTIMIZATION_FAILED"
        else:
            usage_unknown = True
            usage.record(arm_id, "bananloop_full_optimization", {
                "model_turns_observed": 0, "usage": None, "usage_unknown": True,
            })
        return {"status": status, "exploration_status": explore_report.get("status"),
                "model_turns_observed": observed_calls + optimization_calls,
                "usage_unknown": usage_unknown,
                "optimization_returncode": harbor_return, "deadline_epoch": deadline_epoch,
                "source_overlay": source_overlay,
                "evaluator_scope": evaluator_scope,
                "pivot_binding_sha256": canonical_sha(binding),
                "child_campaign_id": pivot["child_campaign_id"],
                "prepared_config_sha256": {
                    "bananloop": hashlib.sha256(bl_config_path.read_bytes()).hexdigest(),
                    "harbor": hashlib.sha256(harbor_config_path.read_bytes()).hexdigest(),
                }, "final_artifact": final_artifact,
                "harbor_bridge_receipt": bridge_receipt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight",))
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--codex-auth-status")
    parser.add_argument("--codex-version")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--harbor-binary", type=Path, default=HARBOR_BINARY)
    parser.add_argument("--runtime-root", type=Path, default=Path(os.environ.get(
        "BANANLOOP_AUTONOMOUS_V01_RUNTIME_ROOT",
        "/home/sonik/bananloop-benchmark-env/autonomous-v01-task-runtime-v1",
    )))
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
                               image_inspector=lambda image: inspect_docker_image(image, args.docker_binary),
                               runtime_root=args.runtime_root)
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
