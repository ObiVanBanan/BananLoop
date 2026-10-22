from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from bananloop.process import ProcessCancelled, ProcessError, process_control, run_process


def test_process_error_includes_stdout_and_stderr(tmp_path: Path):
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\n"
        "print('visible stdout')\n"
        "print('visible stderr', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )

    with pytest.raises(ProcessError) as excinfo:
        run_process([sys.executable, str(script)], cwd=tmp_path, timeout=5)

    message = str(excinfo.value)
    assert "exit code 7" in message
    assert "visible stdout" in message
    assert "visible stderr" in message
    assert excinfo.value.result is not None
    assert excinfo.value.result.returncode == 7


@pytest.mark.parametrize("reason", ["timeout", "stop"])
def test_cancellation_stops_child_work(tmp_path: Path, reason):
    ready = tmp_path / "ready"
    escaped = tmp_path / "escaped"
    child = tmp_path / "child.py"
    child.write_text(
        "from pathlib import Path\nimport time\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(2)\n"
        f"Path({str(escaped)!r}).write_text('child survived')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    control = lambda: "USER_STOPPED" if reason == "stop" and ready.exists() else None
    with process_control(control):
        with pytest.raises(ProcessCancelled if reason == "stop" else ProcessError):
            run_process([sys.executable, str(parent)], cwd=tmp_path,
                        timeout=10 if reason == "stop" else 1)
    assert ready.exists()
    time.sleep(2.1)
    assert not escaped.exists(), "child kept running after its action was cancelled"


def test_stop_while_child_ignores_large_stdin_is_prompt(tmp_path: Path):
    """Stop must stay responsive when communicate() is blocked on pipe input.

    This was not covered by the small request in the campaign stop test.
    On Windows the child may ignore CTRL_BREAK and taskkill may be denied;
    cleanup must still kill the direct process within the CLI stop budget.
    """
    ready = tmp_path / "stdin-not-read"
    script = tmp_path / "sleep_without_reading.py"
    script.write_text(
        "import time\\nfrom pathlib import Path\\n"
        + f"Path({str(ready)!r}).write_text('ready')\\n"
        + "time.sleep(30)\\n",
        encoding="utf-8",
    )
    control = lambda: "USER_STOPPED" if ready.exists() else None
    started = time.monotonic()
    with process_control(control):
        with pytest.raises(ProcessCancelled) as excinfo:
            run_process(
                [sys.executable, str(script)],
                cwd=tmp_path,
                timeout=15,
                input_text="x" * (2 * 1024 * 1024),
            )
    assert excinfo.value.reason == "USER_STOPPED"
    assert time.monotonic() - started < 4.5, "stop spent too long cleaning up a blocked pipe"
