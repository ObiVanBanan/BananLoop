"""Parent-process supervision for one prepared benchmark arm."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ARM_SECONDS = 3600
DEADLINE_EPOCH_ENV = "BANANLOOP_ARM_DEADLINE_EPOCH"
DEADLINE_MONOTONIC_ENV = "BANANLOOP_ARM_DEADLINE_MONOTONIC"


@dataclass(frozen=True)
class ArmDeadline:
    epoch: float
    monotonic: float

    @classmethod
    def start(cls, seconds: float = ARM_SECONDS) -> ArmDeadline:
        if type(seconds) not in (float, int) or not math.isfinite(seconds) or not 0 < seconds <= ARM_SECONDS:
            raise ValueError("arm duration must be positive and at most 3600 seconds")
        monotonic = time.monotonic() + seconds
        return cls(time.time() + seconds, monotonic)

    def remaining(self) -> float:
        return self.monotonic - time.monotonic()


def inherited_arm_deadline(epoch: float) -> ArmDeadline | None:
    """Recover the parent's monotonic deadline in a worker in its own session."""
    if DEADLINE_EPOCH_ENV not in os.environ and DEADLINE_MONOTONIC_ENV not in os.environ:
        return None
    if os.name == "nt" or os.getsid(0) != os.getpgrp():
        raise ValueError("arm worker is outside the watchdog process group")
    try:
        inherited_epoch = float(os.environ[DEADLINE_EPOCH_ENV])
        monotonic = float(os.environ[DEADLINE_MONOTONIC_ENV])
    except (KeyError, ValueError) as exc:
        raise ValueError("arm worker deadline environment is incomplete or invalid") from exc
    if (inherited_epoch != epoch or not math.isfinite(epoch) or not math.isfinite(monotonic)
            or monotonic - time.monotonic() > ARM_SECONDS):
        raise ValueError("arm worker deadline does not match its parent")
    return ArmDeadline(epoch, monotonic)


def terminate_arm_process(process: subprocess.Popen[Any], *, own_group: bool = True,
                          graceful: bool = True) -> None:
    """Stop the session group, or just the leader when the parent owns the group."""
    if os.name == "nt" or not own_group:
        if process.poll() is None:
            if graceful:
                process.terminate()
            else:
                process.kill()
    else:
        if graceful:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        # A leader can exit on SIGTERM while descendants still hold the group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_arm_command(command: Sequence[str], *, cwd: Path, arm_root: Path,
                    deadline: ArmDeadline) -> dict[str, Any]:
    """Run a prepared worker in its own POSIX session under the parent's timer.

    Final-artifact recovery and stopping external services are the launcher's
    responsibility. Workers must keep their local subprocesses in the session.
    """
    if os.name == "nt":
        raise RuntimeError("whole-arm process-group supervision requires POSIX")
    if not math.isfinite(deadline.epoch) or not math.isfinite(deadline.monotonic):
        raise ValueError("arm deadline must be finite")
    remaining = deadline.remaining()
    if remaining > ARM_SECONDS:
        raise ValueError("arm deadline exceeds 3600 seconds")
    result: dict[str, Any] = {"deadline_epoch": deadline.epoch, "returncode": None}
    if remaining <= 0:
        return {**result, "status": "TIMED_OUT_BEFORE_START"}
    env = {
        **os.environ,
        DEADLINE_EPOCH_ENV: str(deadline.epoch),
        DEADLINE_MONOTONIC_ENV: str(deadline.monotonic),
    }
    stdout_path = arm_root / "arm-worker.stdout"
    stderr_path = arm_root / "arm-worker.stderr"
    created = []
    try:
        with stdout_path.open("xb") as stdout:
            created.append(stdout_path)
            with stderr_path.open("xb") as stderr:
                created.append(stderr_path)
                if deadline.remaining() <= 0:
                    raise TimeoutError
                process = subprocess.Popen(
                    command, cwd=cwd, stdout=stdout, stderr=stderr,
                    env=env, start_new_session=True,
                )
    except (OSError, ValueError) as exc:
        for path in created:
            path.unlink()
        status = "TIMED_OUT_BEFORE_START" if isinstance(exc, TimeoutError) else "FAILED_TO_START"
        return {**result, "status": status}

    status = "INTERRUPTED"
    try:
        process.wait(timeout=max(0.0, deadline.remaining()))
        if deadline.remaining() <= 0:
            status = "TIMED_OUT"
        else:
            status = "COMPLETED" if process.returncode == 0 else "FAILED"
    except subprocess.TimeoutExpired:
        status = "TIMED_OUT"
    finally:
        terminate_arm_process(process, graceful=status != "TIMED_OUT")
    return {**result, "status": status, "returncode": process.returncode}
