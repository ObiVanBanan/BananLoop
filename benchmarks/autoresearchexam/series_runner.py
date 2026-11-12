#!/usr/bin/env python3
"""Durable dispatcher for already-prepared AutoResearchExam Harbor jobs.

This is only a sequential dispatcher. It does not create campaigns, review or
approve evaluator protocols, produce task-specific candidate adapters, or make
the ten-task benchmark runnable by itself. Every task job must already have a
prepared BananLoop config and Harbor job config.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

if os.name == "nt":
    import msvcrt
else:
    import fcntl


ROOT = Path(__file__).resolve().parents[2]
SELECTION_PATH = Path(__file__).with_name("task-selection.json")
SCHEMA = "bananloop-are-prepared-series-v1"
JOB_SCHEMA = "bananloop-are-prepared-harbor-job-v1"
MAX_SECONDS = 3600
MAX_ITERATIONS = 5
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "INTERRUPTED_UNKNOWN", "NOT_STARTED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected a regular non-symlink JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _resolve(base: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty path")
    path = Path(value).expanduser()
    path = (base / path) if not path.is_absolute() else path
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    return path.resolve()


def _verify_digest(path: Path, expected: Any, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    actual = _sha256(path.read_bytes())
    if actual != expected:
        raise ValueError(f"{label} does not match its prepared digest")
    return actual


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """Cross-platform nonblocking single-writer lock for the series ledger."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except (OSError, BlockingIOError) as exc:
        if not locked:
            raise RuntimeError("another AutoResearchExam series process holds the run lock") from exc
        raise
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_selection(selection_path: Path = SELECTION_PATH) -> tuple[dict[str, Any], str]:
    selection = _read_json(selection_path)
    tasks = selection.get("tasks")
    if selection.get("schema_version") != 2 or not isinstance(tasks, list) or len(tasks) != 10:
        raise ValueError("frozen AutoResearchExam selection must contain ten tasks (schema v2)")
    ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
    if len(ids) != 10 or len(set(ids)) != 10 or any(
        not isinstance(task_id, str) or not SAFE_ID.fullmatch(task_id) for task_id in ids
    ):
        raise ValueError("task selection must contain ten distinct safe task IDs")
    for item in tasks:
        if (not isinstance(item.get("version"), str)
                or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", item["version"])
                or any(not isinstance(item.get(key), str)
                       or not re.fullmatch(r"[0-9a-f]{64}", item[key])
                       for key in ("task_sha256", "instruction_sha256"))):
            raise ValueError("task selection contains invalid version or pinned source digest")
    return selection, _sha256(selection_path.read_bytes())


def validate_prepared_job(
    descriptor_path: Path, *, selection: dict[str, Any], overlay_index: dict[str, Any],
) -> dict[str, Any]:
    """Validate a prepared job descriptor and return its frozen execution record.

    Descriptor format is intentionally explicit and content-pinned::

      {"schema":"bananloop-are-prepared-harbor-job-v1", "task_id":..., 
       "task_version":..., "task_instruction_sha256":..., 
       "overlay_path":..., "overlay_task_sha256":..., 
       "bananloop_config_path":..., "bananloop_config_sha256":..., 
       "harbor_config_path":..., "harbor_config_sha256":...}
    """
    if descriptor_path.is_symlink():
        raise ValueError("prepared job descriptor must not be a symlink")
    descriptor_path = descriptor_path.absolute()
    descriptor = _read_json(descriptor_path)
    if descriptor.get("schema") != JOB_SCHEMA:
        raise ValueError(f"unsupported prepared job schema: {descriptor_path}")
    task_id = descriptor.get("task_id")
    manifest_matches = [t for t in selection["tasks"] if t["task_id"] == task_id]
    overlay_matches = [t for t in overlay_index.get("tasks", []) if t.get("task_id") == task_id]
    if len(manifest_matches) != 1 or len(overlay_matches) != 1:
        raise ValueError(f"job task is not uniquely present in selection and safe overlay index: {task_id}")
    task = manifest_matches[0]
    overlay_record = overlay_matches[0]
    if (descriptor.get("task_version") != task["version"]
            or descriptor.get("task_instruction_sha256") != task["instruction_sha256"]
            or descriptor.get("overlay_task_sha256") != overlay_record.get("overlay_task_sha256")):
        raise ValueError(f"prepared job pins differ from selected task or overlay: {task_id}")

    overlay_path = _resolve(descriptor_path.parent, descriptor.get("overlay_path"), label="overlay_path")
    indexed_overlay_path = _resolve(
        descriptor_path.parent, overlay_record.get("overlay_path"), label="indexed overlay path",
    )
    if overlay_path != indexed_overlay_path:
        raise ValueError(f"prepared job overlay path differs from the safe overlay index: {task_id}")
    overlay_metadata_path = overlay_path / "bananloop-overlay.json"
    overlay_task_path = overlay_path / "task.toml"
    overlay_instruction_path = overlay_path / "instruction.md"
    overlay_meta = _read_json(overlay_metadata_path)
    if (overlay_meta.get("task_id") != task_id
            or overlay_meta.get("version") != task["version"]
            or overlay_meta.get("source_task_sha256") != task["task_sha256"]
            or overlay_meta.get("source_instruction_sha256") != task["instruction_sha256"]
            or overlay_meta.get("overlay_task_sha256") != overlay_record.get("overlay_task_sha256")):
        raise ValueError(f"safe overlay metadata does not match selected task: {task_id}")
    if not overlay_instruction_path.is_file():
        raise ValueError(f"safe overlay public instruction is missing: {task_id}")
    if _sha256(overlay_instruction_path.read_bytes()) != task["instruction_sha256"]:
        raise ValueError(f"safe overlay instruction differs from selected task: {task_id}")
    overlay_sha = _verify_digest(
        overlay_task_path, descriptor.get("overlay_task_sha256"), label=f"{task_id} overlay task digest",
    )

    bl_config_path = _resolve(
        descriptor_path.parent, descriptor.get("bananloop_config_path"), label="bananloop_config_path",
    )
    harbor_config_path = _resolve(
        descriptor_path.parent, descriptor.get("harbor_config_path"), label="harbor_config_path",
    )
    bl_digest = _verify_digest(
        bl_config_path, descriptor.get("bananloop_config_sha256"), label=f"{task_id} BananLoop config digest",
    )
    harbor_digest = _verify_digest(
        harbor_config_path, descriptor.get("harbor_config_sha256"), label=f"{task_id} Harbor config digest",
    )
    bl_config = _read_json(bl_config_path)
    harbor_config = _read_json(harbor_config_path)
    if (bl_config.get("task_id") != task_id or bl_config.get("task_version") != task["version"]
            or bl_config.get("task_instruction_sha256") != task["instruction_sha256"]
            or bl_config.get("max_candidate_iterations") != MAX_ITERATIONS
            or bl_config.get("time_limit_seconds") != MAX_SECONDS):
        raise ValueError(f"BananLoop config is not pinned to task/version/five iterations/one hour: {task_id}")
    harbor_tasks = harbor_config.get("tasks")
    agents = harbor_config.get("agents")
    retry = harbor_config.get("retry", {})
    if (not isinstance(harbor_tasks, list) or len(harbor_tasks) != 1
            or not isinstance(harbor_tasks[0], dict)
            or _resolve(harbor_config_path.parent, harbor_tasks[0].get("path"), label="Harbor task path") != overlay_path):
        raise ValueError(f"Harbor config must run exactly the pinned safe overlay: {task_id}")
    if (not isinstance(agents, list) or len(agents) != 1
            or not isinstance(agents[0], dict)
            or agents[0].get("import_path") != "benchmarks.autoresearchexam.harbor_agent:AREBananLoopOptimizationAgent"
            or not isinstance(agents[0].get("kwargs"), dict)
            or _resolve(harbor_config_path.parent, agents[0]["kwargs"].get("config_path"),
                        label="Harbor agent BananLoop config path") != bl_config_path):
        raise ValueError(f"Harbor config must invoke the BananLoop ARE bridge with its pinned config: {task_id}")
    agent_timeout = agents[0].get("override_timeout_sec")
    if type(agent_timeout) is not int or not 1 <= agent_timeout <= MAX_SECONDS:
        raise ValueError(f"Harbor agent timeout must be at most one hour: {task_id}")
    if (type(harbor_config.get("n_attempts", 1)) is not int
            or harbor_config.get("n_attempts", 1) != 1
            or type(harbor_config.get("n_concurrent_trials", 1)) is not int
            or harbor_config.get("n_concurrent_trials", 1) != 1
            or not isinstance(retry, dict)
            or type(retry.get("max_retries", 0)) is not int
            or retry.get("max_retries", 0) != 0):
        raise ValueError(f"Harbor config must be single-attempt and sequential with no retries: {task_id}")
    job_name = harbor_config.get("job_name")
    if not isinstance(job_name, str) or not SAFE_ID.fullmatch(job_name):
        raise ValueError(f"Harbor config must pin a safe unique job_name: {task_id}")
    jobs_dir = _resolve(harbor_config_path.parent, harbor_config.get("jobs_dir"), label="Harbor jobs_dir")
    job_output_dir = jobs_dir / job_name

    return {
        "task_id": task_id,
        "task_version": task["version"],
        "task_instruction_sha256": task["instruction_sha256"],
        "overlay_path": str(overlay_path),
        "overlay_task_sha256": overlay_sha,
        "bananloop_config_path": str(bl_config_path),
        "bananloop_config_sha256": bl_digest,
        "harbor_config_path": str(harbor_config_path),
        "harbor_config_sha256": harbor_digest,
        "harbor_output_dir": str(job_output_dir),
        "descriptor_path": str(descriptor_path),
        "descriptor_sha256": _sha256(descriptor_path.read_bytes()),
    }


def build_command(*, harbor_binary: str, harbor_config: Path) -> list[str]:
    if not isinstance(harbor_binary, str) or not harbor_binary:
        raise ValueError("harbor_binary must be a nonempty executable path")
    return [harbor_binary, "run", "--config", str(harbor_config), "--disable-verification"]


def inspect_harbor_result(job_dir: Path, *, task_id: str, overlay_path: str) -> dict[str, Any]:
    """Return a minimal completion receipt, rejecting errored/incomplete trials."""
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise ValueError("Harbor job output directory is missing or unsafe")
    result_path = job_dir / "result.json"
    job_result = _read_json(result_path)
    stats = job_result.get("stats")
    if (not job_result.get("finished_at")
            or job_result.get("n_total_trials") != 1
            or not isinstance(stats, dict)
            or any(type(stats.get(key)) is not int or stats[key] != expected for key, expected in (
                ("n_completed_trials", 1), ("n_errored_trials", 0),
                ("n_running_trials", 0), ("n_pending_trials", 0),
                ("n_cancelled_trials", 0), ("n_retries", 0),
            ))):
        raise ValueError("Harbor job result is unfinished or contains an errored/retried trial")
    trial_results = [path for path in job_dir.glob("*/result.json") if path.is_file() and not path.is_symlink()]
    if len(trial_results) != 1:
        raise ValueError("Harbor job must contain exactly one regular trial result")
    trial = _read_json(trial_results[0])
    trial_id = trial.get("task_id")
    if (not trial.get("finished_at") or trial.get("exception_info") is not None
            or not isinstance(trial_id, dict) or trial_id.get("path") != overlay_path):
        raise ValueError("Harbor trial result is unfinished, errored, or belongs to another task")
    agent_result = trial.get("agent_result")
    metadata = agent_result.get("metadata") if isinstance(agent_result, dict) else None
    bananloop = metadata.get("bananloop_are") if isinstance(metadata, dict) else None
    if (not isinstance(bananloop, dict) or bananloop.get("task_id") != task_id
            or not isinstance(bananloop.get("status"), str)):
        raise ValueError("Harbor trial lacks the BananLoop bridge terminal status")
    status = bananloop["status"]
    if status in {"RUN_FAILED", "FAILED", "ERROR", "CANCELLED"}:
        raise ValueError(f"BananLoop bridge reported terminal failure ({status})")
    if status not in {"ALL_NODES_COMPLETE", "STEP_LIMIT", "ITERATION_LIMIT",
                      "DEADLINE_EXCEEDED", "CONVERGED_REPEATED_EXPERIMENT", "COMPLETED"}:
        raise ValueError("BananLoop bridge returned an unrecognized terminal status")
    calls = bananloop.get("provider_call_attempts")
    if type(calls) is not int or calls < 0:
        calls = "UNKNOWN"
    usage = bananloop.get("usage")
    if not isinstance(usage, dict):
        usage = "UNKNOWN"
    else:
        usage = {key: (value if type(value) is int and value >= 0 else "UNKNOWN")
                 for key, value in usage.items() if key in {"input_tokens", "output_tokens", "total_tokens"}}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            usage.setdefault(key, "UNKNOWN")
    public_submissions = bananloop.get("public_submissions")
    public_submissions = public_submissions if isinstance(public_submissions, list) else []
    return {
        "harbor_job_id": job_result.get("id"),
        "harbor_trial_id": trial.get("id"),
        "harbor_trial_status": "COMPLETED",
        "bananloop_status": status,
        "model_calls": calls,
        "token_usage": usage,
        "public_submission_count": len(public_submissions),
        "public_submission_unknown": any(
            isinstance(item, dict) and item.get("state") == "SUBMISSION_UNKNOWN"
            for item in public_submissions
        ),
    }


class AutoResearchExamSeriesRunner:
    """Run validated prepared jobs sequentially with crash-safe no-replay semantics."""

    def __init__(
        self, *, selection_path: Path, overlay_index_path: Path,
        job_config_dir: Path, series_dir: Path, run_id: str,
        harbor_binary: str = "harbor", task_ids: Sequence[str] | None = None,
        process_runner: Callable[..., Any] = subprocess.run,
    ):
        if not SAFE_ID.fullmatch(run_id):
            raise ValueError("run_id must be a short safe identifier")
        self.selection, self.selection_sha256 = load_selection(selection_path)
        self.selection_path = selection_path.expanduser().resolve()
        self.overlay_index_path = overlay_index_path.expanduser().resolve()
        self.overlay_index = _read_json(overlay_index_path)
        if self.overlay_index.get("schema") != "bananloop-are-safe-harbor-overlay-index-v1":
            raise ValueError("unsupported safe overlay index schema")
        selected = [task["task_id"] for task in self.selection["tasks"]]
        overlay_tasks = self.overlay_index.get("tasks")
        overlay_ids = [item.get("task_id") for item in overlay_tasks if isinstance(item, dict)] \
            if isinstance(overlay_tasks, list) else []
        if (self.overlay_index.get("source_revision") != self.selection.get("source_revision")
                or self.overlay_index.get("task_count") != 10
                or len(overlay_ids) != 10 or len(set(overlay_ids)) != 10
                or set(overlay_ids) != set(selected)):
            raise ValueError("safe overlay index must pin exactly the ten selected tasks at the same source revision")
        requested = list(task_ids) if task_ids is not None else selected
        if not requested or len(set(requested)) != len(requested) or any(item not in selected for item in requested):
            raise ValueError("requested task IDs must be distinct members of the frozen ten-task selection")
        self.task_ids = requested
        self.job_config_dir = job_config_dir.expanduser().resolve()
        self.series_dir = series_dir.expanduser().resolve()
        self.run_id = run_id
        self.harbor_binary = harbor_binary
        self.process_runner = process_runner
        descriptors = {}
        for task_id in selected:
            path = self.job_config_dir / f"{task_id}.json"
            descriptors[task_id] = validate_prepared_job(
                path, selection=self.selection, overlay_index=self.overlay_index,
            )
        self.jobs = {task_id: descriptors[task_id] for task_id in requested}
        self.identity_sha256 = _sha256(_canonical({
            "run_id": run_id,
            "selection_sha256": self.selection_sha256,
            "overlay_index_sha256": _sha256(_canonical(self.overlay_index)),
            "task_ids": requested,
            "jobs": self.jobs,
            "max_seconds": MAX_SECONDS,
            "max_iterations": MAX_ITERATIONS,
        }))
        self.report_path = self.series_dir / "series.json"

    def _initial_report(self) -> dict[str, Any]:
        now = _now()
        return {
            "schema": SCHEMA, "run_id": self.run_id,
            "identity_sha256": self.identity_sha256,
            "selection_sha256": self.selection_sha256,
            "created_at": now, "updated_at": now,
            "limits": {"task_timeout_seconds": MAX_SECONDS, "max_candidate_iterations": MAX_ITERATIONS},
            "series_status": "PENDING",
            "tasks": [{**self.jobs[task_id], "status": "PENDING", "attempt_count": 0,
                       "command_argv": None, "started_at": None, "finished_at": None,
                       "elapsed_seconds": None, "returncode": None,
                       "model_calls": "UNKNOWN", "token_usage": "UNKNOWN",
                       "error_type": None, "error": None, "stdout_path": None, "stderr_path": None}
                      for task_id in self.task_ids],
        }

    def _save(self, report: dict[str, Any]) -> None:
        states = {record["status"] for record in report["tasks"]}
        report["series_status"] = (
            "RUNNING" if "RUNNING" in states else
            "PENDING" if "PENDING" in states else
            "COMPLETED" if states <= {"COMPLETED"} else
            "COMPLETED_WITH_FAILURES"
        )
        report["updated_at"] = _now()
        _atomic_json(self.report_path, report)

    def _load_or_create(self) -> dict[str, Any]:
        self.series_dir.mkdir(parents=True, exist_ok=True)
        if self.series_dir.is_symlink():
            raise ValueError("series directory must not be a symlink")
        if not self.report_path.exists():
            result = self._initial_report()
            self._save(result)
            return result
        result = _read_json(self.report_path)
        if result.get("schema") != SCHEMA or result.get("identity_sha256") != self.identity_sha256:
            raise ValueError("existing series file does not match frozen run identity/config digests")
        return result

    def run(self) -> dict[str, Any]:
        # Recheck every prepared input immediately before launch and every
        # resume; descriptor/config changes invalidate the run identity.
        current_selection, selection_digest = load_selection(self.selection_path)
        current_overlays = _read_json(self.overlay_index_path)
        if selection_digest != self.selection_sha256 or _sha256(_canonical(current_overlays)) != _sha256(_canonical(self.overlay_index)):
            raise ValueError("frozen task selection or safe overlay index changed")
        current_jobs = {
            task_id: validate_prepared_job(
                self.job_config_dir / f"{task_id}.json",
                selection=current_selection, overlay_index=current_overlays,
            )
            for task_id in [task["task_id"] for task in current_selection["tasks"]]
        }
        if any(current_jobs[task_id] != self.jobs[task_id]
               for task_id in self.jobs):
            raise ValueError("prepared task config or overlay digest changed after run identity was frozen")
        self.series_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.series_dir / ".series.lock"
        with _exclusive_lock(lock_path):
            report = self._load_or_create()
            by_id = {record["task_id"]: record for record in report["tasks"]}
            for task_id in self.task_ids:
                record = by_id[task_id]
                if record["status"] in TERMINAL:
                    continue
                if record["status"] == "RUNNING":
                    record.update({
                        "status": "INTERRUPTED_UNKNOWN", "finished_at": _now(),
                        "error_type": "InterruptedRun",
                        "error": "prior dispatcher stopped while Harbor job was RUNNING; no replay",
                        "model_calls": "UNKNOWN", "token_usage": "UNKNOWN",
                    })
                    self._save(report)
                    continue
                self._run_one(record, report)
            self._save(report)
            return report

    def _run_one(self, record: dict[str, Any], report: dict[str, Any]) -> None:
        task_id = record["task_id"]
        artifact_dir = self.series_dir / "tasks" / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if artifact_dir.is_symlink():
            record.update({"status": "NOT_STARTED", "finished_at": _now(),
                           "error_type": "UnsafeArtifactDirectory", "error": "task artifact directory is a symlink"})
            self._save(report)
            return
        harbor_output = Path(record["harbor_output_dir"])
        if harbor_output.exists() or harbor_output.is_symlink():
            record.update({"status": "NOT_STARTED", "finished_at": _now(),
                           "error_type": "HarborOutputExists",
                           "error": "prepared Harbor output directory already exists; refusing possible replay"})
            self._save(report)
            return
        stdout_path, stderr_path = artifact_dir / "stdout.log", artifact_dir / "stderr.log"
        argv = build_command(harbor_binary=self.harbor_binary,
                             harbor_config=Path(record["harbor_config_path"]))
        record.update({"status": "RUNNING", "attempt_count": record["attempt_count"] + 1,
                       "command_argv": argv, "started_at": _now(), "started_epoch": time.time(),
                       "finished_at": None, "elapsed_seconds": None, "returncode": None,
                       "model_calls": "UNKNOWN", "token_usage": "UNKNOWN", "error_type": None,
                       "error": None, "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)})
        self._save(report)  # durable before any process/provider/grader activity
        started = time.monotonic()
        try:
            completed = self.process_runner(
                argv, cwd=ROOT, capture_output=True, text=True,
                timeout=MAX_SECONDS, check=False,
            )
            stdout = completed.stdout if isinstance(completed.stdout, str) else ""
            stderr = completed.stderr if isinstance(completed.stderr, str) else ""
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            record["returncode"] = completed.returncode
            if completed.returncode != 0:
                record.update({"status": "FAILED", "error_type": "HarborExit",
                               "error": f"Harbor exited with code {completed.returncode}"})
            else:
                try:
                    receipt = inspect_harbor_result(
                        harbor_output, task_id=task_id, overlay_path=record["overlay_path"],
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    record.update({"status": "FAILED", "error_type": "HarborResultInvalid",
                                   "error": "Harbor exited successfully but its finished non-error trial receipt was missing or invalid"})
                else:
                    if receipt.get("public_submission_unknown") is True:
                        record.update({"status": "INTERRUPTED_UNKNOWN", **receipt,
                                       "error_type": "PublicSubmissionUnknown",
                                       "error": "public validation delivery is uncertain; no replay"})
                    else:
                        record.update({"status": "COMPLETED", **receipt,
                                       "error_type": None, "error": None})
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(_safe_text(exc.stdout), encoding="utf-8")
            stderr_path.write_text(_safe_text(exc.stderr), encoding="utf-8")
            record.update({"status": "TIMED_OUT", "error_type": "TaskTimeout",
                           "error": "Harbor process exceeded the one-hour task limit; no replay"})
        except Exception as exc:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            record.update({"status": "INTERRUPTED_UNKNOWN", "error_type": type(exc).__name__,
                           "error": "Harbor process outcome is uncertain; no replay"})
        finally:
            record["finished_at"] = _now()
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self._save(report)


def _safe_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=SELECTION_PATH)
    parser.add_argument("--overlay-index", type=Path, required=True)
    parser.add_argument("--job-config-dir", type=Path, required=True,
                        help="directory containing one <task_id>.json prepared job descriptor for each selected task")
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--harbor", default="harbor")
    parser.add_argument("--task-id", action="append", dest="task_ids",
                        help="optional subset of selected task IDs; repeat to add IDs")
    args = parser.parse_args(argv)
    try:
        report = AutoResearchExamSeriesRunner(
            selection_path=args.selection, overlay_index_path=args.overlay_index,
            job_config_dir=args.job_config_dir, series_dir=args.series_dir,
            run_id=args.run_id, harbor_binary=args.harbor, task_ids=args.task_ids,
        ).run()
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    return 0 if report["series_status"] in {"COMPLETED", "COMPLETED_WITH_FAILURES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
