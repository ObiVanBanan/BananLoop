"""Keep repository tests from reading or writing the developer's home directory."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


if os.name == "nt":
    # The workflow prepares a protected DACL on this root before pytest starts.
    # Descendants inherit only the runner, SYSTEM, and Administrators entries.
    _isolated_home = Path(os.environ["BANANLOOP_TEST_BASETEMP"]) / "isolated-home"
    _isolated_home.mkdir(parents=True, exist_ok=True)
else:
    _isolated_home = Path(tempfile.mkdtemp(prefix="bananloop-pytest-home-"))


def pytest_configure() -> None:
    Path.home = classmethod(lambda cls: _isolated_home)


def pytest_unconfigure() -> None:
    shutil.rmtree(_isolated_home, ignore_errors=True)
