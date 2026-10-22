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
    """Terminate only the process group created for this invocation."""
    def wait_for_communication(timeout: float) -> bool:
        if communicate_thread is not None:
            communicate_thread.join(timeout=timeout)
            return not communicate_thread.is_alive()
        try:
            proc.communicate(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    if os.name == "nt":
        # Windows sandbox/job boundaries can deny taskkill even for a child we
        # started. CTRL_BREAK reaches the CREATE_NEW_PROCESS_GROUP members first
        # and lets wrappers such as cmd.exe and their descendants exit together.
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
        if wait_for_communication(2):
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        # The leader may have exited while a child still holds an output pipe.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if wait_for_communication(5):
            return
    if proc.poll() is None:
        proc.kill()
    if not wait_for_communication(5):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


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
        if communicate_thread.is_alive():
            terminate_process_tree(proc, communicate_thread=communicate_thread)
        else:
            terminate_process_tree(proc)
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
