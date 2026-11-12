#!/usr/bin/env python3
"""Durable sequential runner for the ten registered Board Games pilot tasks.

The runner never interprets a completed CLI process as a scientific success. It
records the BananLoop campaign status separately from process status, captures
the exact argv and logs, and avoids resubmitting an interrupted/ambiguous task.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:  # Windows CI should be able to import the runner too.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows CI
    fcntl = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).with_name("pilot_manifest.json")
SCHEMA = "bananloop-boardgames-series-v1"
MAX_ITERATIONS = 5
DEADLINE_SECONDS = 3600
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ACCEPTABLE_TERMINAL_CAMPAIGN_STATUSES = frozenset({
    "ITERATION_LIMIT", "DEADLINE_EXCEEDED", "CONVERGED_REPEATED_EXPERIMENT", "COMPLETED",
})
_INCOMPLETE_CAMPAIGN_STATUSES = frozenset({
    "PROTOCOL_REVIEW_PENDING", "PROTOCOL_APPROVED_AWAITING_OWNER",
    "CALL_ALLOWANCE_EXHAUSTED", "RESEARCH_ROLE_ALLOCATION_EXHAUSTED",
    "ROLE_ALLOCATION_EXHAUSTED", "BUDGET_EXHAUSTED", "BUDGET_RESERVATION_REJECTED",
    "PAUSED_NEEDS_INPUT", "PAUSED", "STOP_REQUESTED",
})
_PILOT_INPUTS = (
    "benchmarks/discoverybench_boardgames/runner.py",
    "benchmarks/discoverybench_boardgames/pilot_manifest.json",
    "examples/exploration_diagnostic_v1/discoverybench_boardgames/data.csv",
)


def _lock_nonblocking(fd: int) -> None:
    """Acquire the series lock without blocking on POSIX or Windows."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    import msvcrt  # type: ignore[import-not-found]

    # msvcrt.locking locks a byte range starting at the current file offset.
    # Ensure byte zero exists before taking the one-byte process lock.
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        raise BlockingIOError("series lock is already held") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_manifest(path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], bytes, str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema") != "bananloop-discoverybench-boardgames-pilot-v1":
        raise ValueError("Board Games task manifest has an unsupported schema")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 10:
        raise ValueError("Board Games series requires exactly ten manifest tasks")
    ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
    if len(ids) != 10 or len(set(ids)) != 10:
        raise ValueError("Board Games manifest must contain ten distinct task IDs")
    for task in tasks:
        if (not isinstance(task.get("task_id"), str)
                or not _SAFE_COMPONENT.fullmatch(task["task_id"])
                or not isinstance(task.get("question"), str)
                or not isinstance(task.get("version"), str)
                or not isinstance(task.get("x"), str)
                or not isinstance(task.get("y"), str)):
            raise ValueError("Board Games manifest contains an invalid task entry")
    return manifest, raw, hashlib.sha256(raw).hexdigest()


def _task_request_id(run_id: str, task_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{task_id}".encode()).hexdigest()[:24]
    return f"boardgames-{digest}"


def _committed_input_errors(repo: Path) -> list[str]:
    """Mirror Core's frozen-input check so uncommitted pilots never reach CLI."""
    errors = []
    for relative in _PILOT_INPUTS:
        candidate = repo / relative
        if (candidate.is_symlink() or not candidate.is_file()
                or not candidate.resolve().is_relative_to(repo)):
            errors.append(f"{relative}: missing or unsafe working-tree file")
            continue
        try:
            committed = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError):
            errors.append(f"{relative}: absent from frozen Git HEAD")
            continue
        if candidate.read_bytes() != committed:
            errors.append(f"{relative}: working-tree bytes differ from frozen Git HEAD")
    return errors


def build_command(
    *, cli_prefix: Sequence[str], repo: Path, series_dir: Path, run_id: str,
    task: dict[str, Any], model: str, max_agent_calls: int,
    max_model_tokens: int, approve_up_to_calls: int,
) -> list[str]:
    """Build the exact argv, with no shell interpolation."""
    task_id = task["task_id"]
    request_id = _task_request_id(run_id, task_id)
    task_state = series_dir / "campaign-state" / task_id
    source = "examples/exploration_diagnostic_v1/discoverybench_boardgames"
    return [
        *cli_prefix, "v2", "explore",
        "--repo", str(repo),
        "--state-dir", str(task_state),
        "--request-id", request_id,
        "--goal", task["question"],
        "--read-path", f"{source}/TASK.md",
        "--read-path", f"{source}/README.md",
        "--read-path", "benchmarks/discoverybench_boardgames/pilot_manifest.json",
        "--read-path", "benchmarks/discoverybench_boardgames/runner.py",
        "--boardgames-task-id", task_id,
        "--model", model,
        "--max-agent-calls", str(max_agent_calls),
        "--max-model-tokens", str(max_model_tokens),
        "--max-iterations", str(MAX_ITERATIONS),
        "--deadline-seconds", str(DEADLINE_SECONDS),
        "--timeout-seconds", "300",
        "--approve-up-to-calls", str(approve_up_to_calls),
    ]


def _find_result(stdout: str) -> dict[str, Any] | None:
    """Find the last complete JSON object in CLI output without assuming framing."""
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(stdout, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((end, value))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _usage_and_calls(result: dict[str, Any] | None) -> tuple[int | str, dict[str, int | str] | str]:
    if result is None:
        return "UNKNOWN", "UNKNOWN"
    history = result.get("request_history")
    if isinstance(history, list):
        dispatched = [item for item in history if isinstance(item, dict) and item.get("dispatched") is True]
        calls: int | str = len(dispatched)
        counters = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
        unknown = set()
        for item in dispatched:
            usage = item.get("usage")
            for key in counters:
                value = usage.get(key) if isinstance(usage, dict) else None
                if type(value) is int and value >= 0:
                    counters[key] += value
                else:
                    unknown.add(key)
        aggregate: dict[str, int | str] = {
            key: ("UNKNOWN" if key in unknown else value)
            for key, value in counters.items()
        }
        if not dispatched and result.get("live_model_called") is True:
            calls = "UNKNOWN"
            aggregate = "UNKNOWN"
        return calls, aggregate
    explicit = result.get("model_calls_dispatched")
    if type(explicit) is int and explicit >= 0:
        calls = explicit
    elif result.get("live_model_called") is False:
        calls = 0
    else:
        calls = "UNKNOWN"
    usage = result.get("usage", "UNKNOWN")
    if isinstance(usage, dict):
        clean = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            clean[key] = value if type(value) is int and value >= 0 else "UNKNOWN"
        usage = clean
    elif usage != "UNKNOWN":
        usage = "UNKNOWN"
    return calls, usage


def _identity(*, run_id: str, repo: Path, manifest_sha256: str,
              config: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    payload = {
        "schema": SCHEMA,
        "run_id": run_id,
        "repo": str(repo),
        "manifest_sha256": manifest_sha256,
        "config": config,
        "tasks": [{"task_id": task["task_id"], "version": task["version"]} for task in tasks],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


class BoardGamesSeriesRunner:
    """Run/resume the ten tasks sequentially, retaining a durable result per task."""

    def __init__(
        self, *, repo: Path, series_dir: Path, run_id: str,
        cli_prefix: Sequence[str] = ("bananloop",), model: str = "gpt-6-luna",
        max_agent_calls: int = 8, max_model_tokens: int = 10_000,
        approve_up_to_calls: int = 6, manifest_path: Path = MANIFEST_PATH,
        task_ids: Sequence[str] | None = None,
        task_timeout_seconds: int = DEADLINE_SECONDS,
        process_runner: Callable[..., Any] = subprocess.run,
        input_preflight: Callable[[Path], list[str]] = _committed_input_errors,
    ):
        if not _SAFE_COMPONENT.fullmatch(run_id):
            raise ValueError("run-id must be a safe 1-80 character identifier")
        if not cli_prefix or any(not isinstance(item, str) or not item for item in cli_prefix):
            raise ValueError("CLI prefix must contain executable tokens")
        for name, value, upper in (
            ("max-agent-calls", max_agent_calls, 1000),
            ("max-model-tokens", max_model_tokens, 10_000_000),
            ("approve-up-to-calls", approve_up_to_calls, 1000),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be from 1 to {upper}")
        if task_timeout_seconds != DEADLINE_SECONDS:
            raise ValueError("each Board Games pilot task is capped at exactly 3600 wall seconds")
        self.repo = repo.expanduser().resolve()
        self.series_dir = series_dir.expanduser().resolve()
        self.run_id = run_id
        self.cli_prefix = tuple(cli_prefix)
        self.model = model
        self.max_agent_calls = max_agent_calls
        self.max_model_tokens = max_model_tokens
        self.approve_up_to_calls = approve_up_to_calls
        self.manifest_path = manifest_path
        self.task_timeout_seconds = task_timeout_seconds
        self.process_runner = process_runner
        self.input_preflight = input_preflight
        self.manifest, self.manifest_bytes, self.manifest_sha256 = _load_manifest(manifest_path)
        all_tasks = self.manifest["tasks"]
        selected_ids = list(task_ids) if task_ids is not None else [item["task_id"] for item in all_tasks]
        if (not selected_ids or len(selected_ids) != len(set(selected_ids))
                or any(not isinstance(item, str) for item in selected_ids)):
            raise ValueError("task-ids must be a nonempty list of unique manifest IDs")
        by_id = {item["task_id"]: item for item in all_tasks}
        if any(item not in by_id for item in selected_ids):
            raise ValueError("task-ids contains an ID absent from the frozen manifest")
        self.tasks = [by_id[item] for item in selected_ids]
        try:
            self.source_revision = subprocess.check_output(
                ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            ).decode("ascii").strip()
            tracked_diff = subprocess.check_output(
                ["git", "-C", str(self.repo), "diff", "--binary", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            self.working_tree_diff_sha256 = hashlib.sha256(tracked_diff).hexdigest()
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            self.source_revision = "UNKNOWN"
            self.working_tree_diff_sha256 = "UNKNOWN"
        self.config = {
            "cli_prefix": list(self.cli_prefix),
            "model": model,
            "max_agent_calls": max_agent_calls,
            "max_model_tokens": max_model_tokens,
            "approve_up_to_calls": approve_up_to_calls,
            "max_iterations": MAX_ITERATIONS,
            "deadline_seconds": DEADLINE_SECONDS,
            "task_timeout_seconds": task_timeout_seconds,
            "task_ids": selected_ids,
            "source_revision": self.source_revision,
            "working_tree_diff_sha256": self.working_tree_diff_sha256,
        }
        self.identity_sha256 = _identity(
            run_id=run_id, repo=self.repo, manifest_sha256=self.manifest_sha256,
            config=self.config, tasks=self.tasks,
        )
        self.plan_path = self.series_dir / "series.json"
        self._report: dict[str, Any] | None = None

    def _initial_report(self) -> dict[str, Any]:
        created = _now()
        entries = []
        for task in self.tasks:
            task_id = task["task_id"]
            entries.append({
                "task_id": task_id,
                "task_version": task["version"],
                "question": task["question"],
                "request_id": _task_request_id(self.run_id, task_id),
                "status": "PENDING",
                "command_status": None,
                "campaign_status": None,
                "scientific_success": False,
                "command_started": False,
                "command_argv": None,
                "started_at": None,
                "started_epoch": None,
                "finished_at": None,
                "elapsed_seconds": None,
                "returncode": None,
                "model_calls": "UNKNOWN",
                "token_usage": "UNKNOWN",
                "error_type": None,
                "error": None,
                "stdout_path": None,
                "stderr_path": None,
                "result_path": None,
                "attempt_count": 0,
            })
        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "identity_sha256": self.identity_sha256,
            "manifest_sha256": self.manifest_sha256,
            "repo": str(self.repo),
            "config": self.config,
            "created_at": created,
            "updated_at": created,
            "series_status": "PENDING",
            "tasks": entries,
        }

    def _load_or_create(self) -> dict[str, Any]:
        self.series_dir.mkdir(parents=True, exist_ok=True)
        if self.series_dir.is_symlink():
            raise ValueError("series directory must not be a symlink")
        if self.plan_path.exists():
            if self.plan_path.is_symlink():
                raise ValueError("series state file must not be a symlink")
            report = json.loads(self.plan_path.read_text(encoding="utf-8"))
            if (report.get("schema") != SCHEMA
                    or report.get("identity_sha256") != self.identity_sha256
                    or report.get("manifest_sha256") != self.manifest_sha256
                    or len(report.get("tasks", [])) != len(self.tasks)):
                raise ValueError("existing series state does not match this run identity or task manifest")
            expected_ids = [task["task_id"] for task in self.tasks]
            if [item.get("task_id") for item in report["tasks"]] != expected_ids:
                raise ValueError("existing series state task order differs from the frozen manifest")
        else:
            report = self._initial_report()
            self._save(report)
        return report

    def _save(self, report: dict[str, Any]) -> None:
        report["updated_at"] = _now()
        report["series_status"] = self._series_status(report["tasks"])
        _atomic_json(self.plan_path, report)
        self._report = report

    @staticmethod
    def _series_status(tasks: list[dict[str, Any]]) -> str:
        states = {item["status"] for item in tasks}
        if "RUNNING" in states:
            return "RUNNING"
        if "PENDING" in states:
            return "PENDING"
        if states <= _ACCEPTABLE_TERMINAL_CAMPAIGN_STATUSES:
            return "COMPLETED"
        if states & {"INCOMPLETE", "INTERRUPTED_UNKNOWN"}:
            return "INCOMPLETE"
        if states & {"FAILED", "NOT_STARTED"}:
            return "COMPLETED_WITH_FAILURES"
        return "PENDING"

    def _finish_interrupted(self, task: dict[str, Any]) -> None:
        if task["status"] != "RUNNING":
            return
        task["status"] = "INTERRUPTED_UNKNOWN"
        task["command_status"] = "INTERRUPTED_UNKNOWN"
        task["finished_at"] = _now()
        started_epoch = task.get("started_epoch")
        if isinstance(started_epoch, (int, float)) and not isinstance(started_epoch, bool):
            task["elapsed_seconds"] = round(max(0.0, time.time() - started_epoch), 3)
        task["error_type"] = "InterruptedRun"
        task["error"] = "previous process stopped while task state was RUNNING; not replayed to avoid duplicate model calls"
        task["model_calls"] = "UNKNOWN"
        task["token_usage"] = "UNKNOWN"
        for field in ("stdout_path", "stderr_path"):
            relative = task.get(field)
            if isinstance(relative, str):
                target = self.series_dir / relative
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("", encoding="utf-8")

    def run(self) -> dict[str, Any]:
        lock_path = self.series_dir / ".series.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                _lock_nonblocking(lock_fd)
            except BlockingIOError:
                raise RuntimeError("another Board Games series process holds the run lock") from None
            report = self._load_or_create()
            for task, record in zip(self.tasks, report["tasks"]):
                if record["status"] in (_ACCEPTABLE_TERMINAL_CAMPAIGN_STATUSES | {
                    "FAILED", "INCOMPLETE", "INTERRUPTED_UNKNOWN", "NOT_STARTED",
                }):
                    continue
                if record["status"] == "RUNNING":
                    self._finish_interrupted(record)
                    self._save(report)
                    continue
                self._run_task(task, record, report)
            self._save(report)
            return report
        finally:
            os.close(lock_fd)

    def _run_task(self, task: dict[str, Any], record: dict[str, Any],
                  report: dict[str, Any]) -> None:
        task_id = task["task_id"]
        artifact_dir = self.series_dir / "tasks" / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if artifact_dir.is_symlink():
            record.update({"status": "NOT_STARTED", "finished_at": _now(),
                           "error_type": "UnsafeArtifactDirectory",
                           "error": "task artifact directory is a symlink"})
            self._save(report)
            return
        argv = build_command(
            cli_prefix=self.cli_prefix, repo=self.repo, series_dir=self.series_dir,
            run_id=self.run_id, task=task, model=self.model,
            max_agent_calls=self.max_agent_calls,
            max_model_tokens=self.max_model_tokens,
            approve_up_to_calls=self.approve_up_to_calls,
        )
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"
        result_path = artifact_dir / "result.json"
        try:
            input_errors = self.input_preflight(self.repo)
        except Exception as exc:
            input_errors = [f"preflight failed: {type(exc).__name__}: {exc}"]
        if input_errors:
            message = "Board Games campaign not started because registered pilot inputs are not frozen: " + "; ".join(input_errors)
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(message + "\n", encoding="utf-8")
            record.update({
                "status": "NOT_STARTED",
                "command_status": "NOT_STARTED",
                "command_started": False,
                "command_argv": argv,
                "started_at": _now(),
                "started_epoch": time.time(),
                "finished_at": _now(),
                "elapsed_seconds": 0.0,
                "returncode": None,
                "model_calls": 0,
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "error_type": "PilotInputsNotCommitted",
                "error": message[:3000],
                "stdout_path": str(stdout_path.relative_to(self.series_dir)),
                "stderr_path": str(stderr_path.relative_to(self.series_dir)),
                "result_path": None,
                "attempt_count": 0,
            })
            self._save(report)
            return
        record.update({
            "status": "RUNNING",
            "command_status": "RUNNING",
            "command_started": False,
            "command_argv": argv,
            "started_at": _now(),
            "started_epoch": time.time(),
            "finished_at": None,
            "elapsed_seconds": None,
            "returncode": None,
            "model_calls": "UNKNOWN",
            "token_usage": "UNKNOWN",
            "error_type": None,
            "error": None,
            "stdout_path": str(stdout_path.relative_to(self.series_dir)),
            "stderr_path": str(stderr_path.relative_to(self.series_dir)),
            "result_path": None,
            "attempt_count": int(record.get("attempt_count", 0)) + 1,
        })
        self._save(report)
        started_clock = time.monotonic()
        command_started = False
        try:
            completed = self.process_runner(
                argv, cwd=self.repo, capture_output=True, text=True,
                timeout=self.task_timeout_seconds, check=False,
            )
            command_started = True
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            command_started = True
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            returncode = None
            record["status"] = "INTERRUPTED_UNKNOWN"
            record["command_status"] = "TIMED_OUT"
            record["error_type"] = "TaskProcessTimeout"
            record["error"] = "CLI exceeded the 3600 second wall limit; outcome and model dispatch may be ambiguous"
        except OSError as exc:
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}\n"
            returncode = None
            record["status"] = "NOT_STARTED"
            record["command_status"] = "NOT_STARTED"
            record["error_type"] = type(exc).__name__
            record["error"] = str(exc)[:1000]
        except Exception as exc:
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}\n"
            returncode = None
            record["status"] = "INTERRUPTED_UNKNOWN"
            record["command_status"] = "INTERRUPTED_UNKNOWN"
            record["error_type"] = type(exc).__name__
            record["error"] = "process invocation failed ambiguously; not replayed to avoid duplicate model calls"
        elapsed = round(time.monotonic() - started_clock, 3)
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        result = _find_result(stdout)
        record["command_started"] = command_started
        record["elapsed_seconds"] = elapsed
        record["returncode"] = returncode
        if record["command_status"] == "RUNNING":
            record["command_status"] = "EXITED_ZERO" if returncode == 0 else "EXITED_NONZERO"
        if result is not None:
            _atomic_json(result_path, result)
            record["result_path"] = str(result_path.relative_to(self.series_dir))
            record["campaign_status"] = result.get("status") if isinstance(result.get("status"), str) else None
            record["campaign_id"] = result.get("campaign_id")
            record["report_path_from_cli"] = result.get("report_path")
            record["model_calls"], record["token_usage"] = _usage_and_calls(result)
        elif record["status"] != "INTERRUPTED_UNKNOWN" and command_started:
            record["model_calls"] = "UNKNOWN"
            record["token_usage"] = "UNKNOWN"
            record["error_type"] = record["error_type"] or "MissingJsonResult"
            record["error"] = record["error"] or "CLI produced no parseable JSON result; model-call outcome is unknown"
        if record["status"] not in {"INTERRUPTED_UNKNOWN", "NOT_STARTED"}:
            raw_campaign_status = record.get("campaign_status")
            if returncode != 0 or result is None:
                record["status"] = "FAILED"
                record["error_type"] = record["error_type"] or "BananLoopCommandFailed"
                if returncode not in (None, 0):
                    record["error"] = f"BananLoop CLI exited with status {returncode}"
                elif result is None:
                    record["error"] = record["error"] or "BananLoop CLI result JSON is missing"
            elif raw_campaign_status in _ACCEPTABLE_TERMINAL_CAMPAIGN_STATUSES:
                record["status"] = raw_campaign_status
            elif raw_campaign_status in _INCOMPLETE_CAMPAIGN_STATUSES:
                record["status"] = "INCOMPLETE"
                record["error_type"] = "CampaignDidNotReachAcceptableTerminalStatus"
                record["error"] = f"campaign stopped with nonterminal status {raw_campaign_status}"
            elif raw_campaign_status in {"FAILED", "RUN_FAILED"}:
                record["status"] = "FAILED"
                record["error_type"] = "BananLoopCampaignFailed"
                record["error"] = f"campaign reported {raw_campaign_status}"
            else:
                record["status"] = "INCOMPLETE"
                record["error_type"] = "UnknownCampaignStatus"
                record["error"] = (
                    "CLI exited successfully but did not report an accepted terminal campaign status"
                )
        record["finished_at"] = _now()
        self._save(report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cli", default="bananloop",
                        help="executable or argv prefix (shell-style tokenization, not executed through a shell)")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--max-agent-calls", type=int, default=8)
    parser.add_argument("--max-model-tokens", type=int, default=10_000,
                        help="per-call token reservation; includes prompt and response usage")
    parser.add_argument("--approve-up-to-calls", type=int, default=6,
                        help="approve five Researcher iterations plus one protocol-review call")
    parser.add_argument("--task-id", action="append", dest="task_ids",
                        help="run only this registered task ID; repeat to select several")
    args = parser.parse_args(argv)
    try:
        runner = BoardGamesSeriesRunner(
            repo=args.repo, series_dir=args.series_dir, run_id=args.run_id,
            cli_prefix=shlex.split(args.cli), model=args.model,
            max_agent_calls=args.max_agent_calls,
            max_model_tokens=args.max_model_tokens,
            approve_up_to_calls=args.approve_up_to_calls,
            task_ids=args.task_ids,
        )
        report = runner.run()
    except Exception as exc:
        print(json.dumps({"status": "SERIES_RUNNER_FAILED", "error_type": type(exc).__name__,
                          "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": report["series_status"],
        "run_id": report["run_id"],
        "identity_sha256": report["identity_sha256"],
        "report_path": str(runner.plan_path),
        "task_counts": {status: sum(item["status"] == status for item in report["tasks"])
                        for status in ("SUCCEEDED", "FAILED", "INTERRUPTED_UNKNOWN", "NOT_STARTED", "PENDING")},
    }, sort_keys=True))
    return 0 if report["series_status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
