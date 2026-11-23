import os
import signal
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="arm watchdog requires POSIX")

from benchmarks.autonomous_v01.arm_watchdog import ArmDeadline, run_arm_command


def _run(command, tmp_path, deadline):
    arm_root = tmp_path / "arm"
    arm_root.mkdir()
    return run_arm_command(
        command,
        cwd=tmp_path,
        arm_root=arm_root,
        deadline=deadline,
    ), arm_root


def test_success_passes_deadline_environment_and_captures_output(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(repo_root), str(repo_root / "src"))))
    script = (
        "import os, time; "
        "from benchmarks.autonomous_v01.arm_watchdog import inherited_arm_deadline; "
        "print('stdout-ok'); "
        "print('stderr-ok', file=__import__('sys').stderr); "
        "epoch = float(os.environ['BANANLOOP_ARM_DEADLINE_EPOCH']); "
        "monotonic = float(os.environ['BANANLOOP_ARM_DEADLINE_MONOTONIC']); "
        "time.time = lambda: -1000000; "
        "inherited = inherited_arm_deadline(epoch); "
        "assert inherited is not None; "
        "assert inherited.monotonic == monotonic"
    )

    result, arm_root = _run([sys.executable, "-c", script], tmp_path, ArmDeadline.start(10))

    assert result["status"] == "COMPLETED"
    assert result["returncode"] == 0
    assert result["deadline_epoch"] > time.time()
    assert (arm_root / "arm-worker.stdout").read_text().strip() == "stdout-ok"
    assert (arm_root / "arm-worker.stderr").read_text().strip() == "stderr-ok"


def test_timeout_kills_child_process_group_and_preserves_partial_output(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('before-timeout', flush=True); "
        "print('error-before-timeout', file=sys.stderr, flush=True); "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)']); "
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid)); "
        "time.sleep(30)"
    )

    result, arm_root = _run([sys.executable, "-c", script], tmp_path, ArmDeadline.start(0.6))

    assert result["status"] == "TIMED_OUT"
    assert result["returncode"] == -signal.SIGKILL
    assert (arm_root / "arm-worker.stdout").read_text().strip() == "before-timeout"
    assert (arm_root / "arm-worker.stderr").read_text().strip() == "error-before-timeout"
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        # A killed orphan may briefly remain as a zombie until init reaps it.
        stat = Path(f"/proc/{child_pid}/stat")
        if stat.exists() and stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"child process {child_pid} survived watchdog timeout")


def test_expired_deadline_does_not_dispatch_command(tmp_path):
    marker = tmp_path / "dispatched"
    script = f"from pathlib import Path; Path({str(marker)!r}).write_text('yes')"

    expired = ArmDeadline(time.time() - 1, time.monotonic() - 1)
    result, arm_root = _run([sys.executable, "-c", script], tmp_path, expired)

    assert result["status"] == "TIMED_OUT_BEFORE_START"
    assert not marker.exists()
    assert not (arm_root / "arm-worker.stdout").exists()
    assert not (arm_root / "arm-worker.stderr").exists()


def test_invalid_executable_fails_to_start_without_partial_worker_files(tmp_path):
    result, arm_root = _run([str(tmp_path / "missing-executable")], tmp_path, ArmDeadline.start(10))

    assert result["status"] == "FAILED_TO_START"
    assert result["returncode"] is None
    assert not (arm_root / "arm-worker.stdout").exists()
    assert not (arm_root / "arm-worker.stderr").exists()


def test_full_backend_stage_keeps_worker_process_group(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(project))
    script = """
import os
import sys
from pathlib import Path

from benchmarks.autonomous_v01.runner import BananLoopFullBackend

deadline = float(os.environ["BANANLOOP_ARM_DEADLINE_EPOCH"])
root = Path.cwd()
stdout = root / "stage.stdout"
status, returncode = object.__new__(BananLoopFullBackend)._run_until_deadline(
    [sys.executable, "-c", "import os; print(os.getpgrp())"],
    cwd=root, deadline_epoch=deadline, stdout_path=stdout,
    stderr_path=root / "stage.stderr",
)
assert (status, returncode) == ("COMPLETED", 0)
assert int(stdout.read_text()) == os.getpgrp()
"""
    result, arm_root = _run([sys.executable, "-c", script], tmp_path, ArmDeadline.start(10))

    assert result["status"] == "COMPLETED", (arm_root / "arm-worker.stderr").read_text()


def test_full_backend_rejects_wrong_inherited_deadline_before_start(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(project))
    script = """
import os
from benchmarks.autonomous_v01.arm_watchdog import inherited_arm_deadline

epoch = float(os.environ["BANANLOOP_ARM_DEADLINE_EPOCH"])
try:
    inherited_arm_deadline(epoch + 1)
except ValueError:
    pass
else:
    raise AssertionError("a mismatched deadline was accepted")
"""
    result, arm_root = _run([sys.executable, "-c", script], tmp_path, ArmDeadline.start(10))
    assert result["status"] == "COMPLETED", (arm_root / "arm-worker.stderr").read_text()
