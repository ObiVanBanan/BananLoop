"""Keep repository tests from reading or writing the developer's home directory."""
from __future__ import annotations

import shutil
import os
import subprocess
import tempfile
from pathlib import Path


if os.name == "nt":
    _isolated_home = Path(os.environ["USERPROFILE"]) / ".bananloop-pytest-home"
    _isolated_home.mkdir(parents=True, exist_ok=True)
    current_sid = subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"], text=True,
    ).strip().strip('"').split('","')[-1]
    subprocess.run(["icacls", str(_isolated_home), "/inheritance:r"],
                   check=True, capture_output=True, text=True)
    subprocess.run([
        "icacls", str(_isolated_home), "/grant:r",
        f"*{current_sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
    ], check=True, capture_output=True, text=True)
else:
    _isolated_home = Path(tempfile.mkdtemp(prefix="bananloop-pytest-home-"))


def pytest_configure() -> None:
    Path.home = classmethod(lambda cls: _isolated_home)


def pytest_unconfigure() -> None:
    shutil.rmtree(_isolated_home, ignore_errors=True)
