from __future__ import annotations

from pathlib import Path

from bananloop.candidate_preflight import python_syntax_errors


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
