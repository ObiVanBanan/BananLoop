from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bananloop.process import ProcessError, run_process


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
