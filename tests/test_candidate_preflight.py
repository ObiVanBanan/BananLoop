from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import bananloop.candidate_preflight as preflight

from bananloop.candidate_preflight import python_api_compatibility_errors, python_syntax_errors


def test_python_preflight_accepts_valid_source_without_writing_pycache(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x: int = 1\n", encoding="utf-8")
    assert python_syntax_errors(tmp_path, ["ok.py", "deleted.py", "readme.md"]) == []
    assert not (tmp_path / "__pycache__").exists()


def test_python_preflight_classifies_syntax_error_without_running_code(tmp_path: Path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")
    errors = python_syntax_errors(tmp_path, ["broken.py"])
    assert len(errors) == 1
    assert errors[0].startswith("broken.py:")
    assert "line 1" in errors[0]


def test_python_preflight_does_not_import_or_execute_candidate(tmp_path: Path):
    script = tmp_path / "code.py"
    script.write_text("raise AssertionError('not executed')\n", encoding="utf-8")
    assert python_syntax_errors(tmp_path, ["code.py"]) == []


def test_python_preflight_rejects_external_symlink(tmp_path: Path):
    import pytest

    target = tmp_path.parent / (tmp_path.name + "-outside.py")
    target.write_text("x = 3\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unsupported in this environment")
    assert "not an internal regular file" in python_syntax_errors(tmp_path, ["linked.py"])[0]



def test_api_preflight_catches_removed_ohe_sparse_using_evaluator_python(tmp_path: Path, monkeypatch):
    path = tmp_path / "model.py"
    path.write_text(
        "from sklearn.preprocessing import OneHotEncoder as OHE\n"
        "raise AssertionError('never execute candidate')\n"
        "encoder = OHE(sparse=False)\n",
        encoding="utf-8",
    )
    probes = []

    def fake_probe(command, **kwargs):
        probes.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1.5.2\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_probe, raising=False)
    errors = python_api_compatibility_errors(
        tmp_path, ["model.py"], evaluator_command=[sys.executable, "evaluate.py"],
    )
    assert len(errors) == 1
    assert "sparse_output=" in errors[0]
    assert "line 3" in errors[0]
    assert probes[0][0][:2] == [sys.executable, "-I"]
    assert "importlib.metadata" in probes[0][0][3]
    assert probes[0][1]["cwd"] == tmp_path
    assert not (tmp_path / "__pycache__").exists()


def test_api_preflight_preserves_windows_python_launcher_runtime(tmp_path: Path, monkeypatch):
    path = tmp_path / "model.py"
    path.write_text(
        "from sklearn.preprocessing import OneHotEncoder as OHE\n"
        "encoder = OHE(sparse=False)\n",
        encoding="utf-8",
    )
    probes = []

    def fake_probe(command, **kwargs):
        probes.append(command)
        return SimpleNamespace(returncode=0, stdout="1.4.0\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_probe, raising=False)
    errors = python_api_compatibility_errors(
        tmp_path, ["model.py"],
        evaluator_command=["C:\\Windows\\py.exe", "-3.12", "evaluate.py"],
    )
    assert errors and "sparse_output=" in errors[0]
    assert probes[0][:3] == ["C:\\Windows\\py.exe", "-3.12", "-I"]


def test_api_preflight_accepts_windows_python_executable_path(tmp_path: Path, monkeypatch):
    (tmp_path / "model.py").write_text(
        "from sklearn.preprocessing import OneHotEncoder as OHE\n"
        "encoder = OHE(sparse=False)\n",
        encoding="utf-8",
    )
    probes = []

    def fake_probe(command, **kwargs):
        probes.append(command)
        return SimpleNamespace(returncode=0, stdout="1.4.0\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_probe, raising=False)
    errors = python_api_compatibility_errors(
        tmp_path, ["model.py"],
        evaluator_command=["C:\\Python312\\python.exe", "evaluate.py"],
    )
    assert errors and "sparse_output=" in errors[0]
    assert probes[0][0] == "C:\\Python312\\python.exe"


def test_api_preflight_does_not_guess_from_unrelated_or_unknown_runtimes(tmp_path: Path, monkeypatch):
    (tmp_path / "model.py").write_text(
        "def OneHotEncoder(**kwargs): return kwargs\n"
        "OneHotEncoder(sparse=False)\n",
        encoding="utf-8",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("must not probe when sklearn is not referenced")

    monkeypatch.setattr(preflight.subprocess, "run", fail_if_called, raising=False)
    assert python_api_compatibility_errors(
        tmp_path, ["model.py"], evaluator_command=[sys.executable, "evaluate.py"]
    ) == []

    (tmp_path / "model.py").write_text(
        "import sklearn.preprocessing as preprocessing\n"
        "preprocessing.OneHotEncoder(sparse=False)\n",
        encoding="utf-8",
    )
    assert python_api_compatibility_errors(
        tmp_path, ["model.py"], evaluator_command=["uv", "run", "python", "evaluate.py"]
    ) == []

    monkeypatch.setattr(
        preflight.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="1.3.2\n", stderr=""),
        raising=False,
    )
    assert python_api_compatibility_errors(
        tmp_path, ["model.py"], evaluator_command=[sys.executable, "evaluate.py"]
    ) == []
