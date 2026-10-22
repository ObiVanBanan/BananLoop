from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessError(RuntimeError):
    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


class ProcessCancelled(RuntimeError):
    """Campaign control, deliberately distinct from an infrastructure failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_control: ContextVar[Callable[[], str | None] | None] = ContextVar("process_control", default=None)


@contextmanager
def process_control(check: Callable[[], str | None]):
    token = _control.set(check)
    try:
        yield
    finally:
        _control.reset(token)


def check_process_control() -> None:
    check = _control.get()
    reason = check() if check else None
    if reason:
        raise ProcessCancelled(reason)


def process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_process_tree(
    proc: subprocess.Popen,
    *,
    communicate_thread: threading.Thread | None = None,
) -> None:
    """Stop this invocation's process group with bounded, race-free cleanup.

    Windows may deny CTRL_BREAK or taskkill (for example under a job object).
    Do not let their independent grace periods accumulate past the operator's
    stop timeout. Only the communicate worker may read/write the pipes.
    """
    def wait_for_communication(timeout: float) -> bool:
        if communicate_thread is not None:
            communicate_thread.join(timeout=timeout)
            return not communicate_thread.is_alive() and proc.poll() is not None
        try:
            proc.communicate(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    if proc.poll() is not None and (
        communicate_thread is None or not communicate_thread.is_alive()
    ):
        return

    if os.name == "nt":
        if proc.poll() is None:
            try:
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                pass
        if wait_for_communication(0.35):
            return
        # Best-effort descendant cleanup; a denied/hung taskkill must not
        # block the campaign's STOP_REQUESTED reconciliation indefinitely.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=1.5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if wait_for_communication(0.25):
            return
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if wait_for_communication(0.5):
            return

    # Kill the direct child even if signaling its group was denied. A pipe
    # reader can remain alive briefly if descendants still own output handles;
    # never close those streams concurrently with communicate().
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    if wait_for_communication(0.5):
        return
    if proc.poll() is None:
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if communicate_thread is not None:
        communicate_thread.join(timeout=0.25)


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_text: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    check_process_control()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
            **process_group_options(),
        )
    except OSError as exc:
        raise ProcessError(f"cannot start process: {argv!r}: {exc}") from exc
    deadline = time.monotonic() + timeout
    communication_result: list[tuple[str | None, str | None]] = []
    communication_errors: list[Exception] = []

    def communicate() -> None:
        try:
            communication_result.append(proc.communicate(input=input_text))
        except Exception as exc:
            communication_errors.append(exc)

    communicate_thread = threading.Thread(target=communicate, daemon=True)
    communicate_thread.start()
    try:
        while communicate_thread.is_alive():
            check_process_control()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessError(f"process timed out after {timeout}s: {argv!r}")
            communicate_thread.join(timeout=min(0.1, remaining))
        check_process_control()
        if communication_errors:
            raise communication_errors[0]
        stdout, stderr = communication_result[0]
    finally:
        # Also clean children left behind after a successful parent exit.
        terminate_process_tree(proc, communicate_thread=communicate_thread)
        # close() can wait on the same buffered pipe lock held by the
        # communicate worker. If Windows descendants retained pipe handles,
        # allow the daemon worker to finish without blocking stop/recovery.
        if not communicate_thread.is_alive():
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
    result = ProcessResult(proc.returncode, stdout, stderr)
    if check and result.returncode != 0:
        details: list[str] = []
        if result.stdout.strip():
            details.append("stdout:\n" + result.stdout[-4000:].rstrip())
        if result.stderr.strip():
            details.append("stderr:\n" + result.stderr[-4000:].rstrip())
        suffix = "\n" + "\n".join(details) if details else ""
        raise ProcessError(
            f"process failed with exit code {result.returncode}: {argv!r}{suffix}",
            result,
        )
    return result


def parse_json_output(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProcessError(f"expected JSON output, got: {text[:500]!r}") from exc
    if not isinstance(data, dict):
        raise ProcessError("expected top-level JSON object")
    return data
