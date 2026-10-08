from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_generator(repo_root: Path):
    path = repo_root / "examples" / "stdlib_classification" / "create_fixture.py"
    spec = importlib.util.spec_from_file_location("stdlib_dogfood_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdlib_dogfood_fixture_is_offline_deterministic_and_improvable(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    generator = _load_generator(repo_root)
    target = tmp_path / "dogfood"

    summary = generator.create_fixture(target)
    result = subprocess.run(
        [sys.executable, "evaluate.py"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload == summary["baseline"]
    assert payload["count"] == 128
    assert 0.70 < payload["balanced_accuracy"] < 0.85
    assert not subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # A general linear rule learned from training data can solve this fixture,
    # so the baseline leaves substantial room for autonomous improvement.
    probe = (
        "from model import predict\n"
        "assert predict((2.0,-2.0,2.0,0.0)) in {0,1}\n"
    )
    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
