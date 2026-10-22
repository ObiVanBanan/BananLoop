from __future__ import annotations

import os
import stat
import sys

import pytest

from bananloop.workspace import WorkspaceManager, _remove_readonly


def test_remove_readonly_makes_path_writable_before_retry(tmp_path):
    path = tmp_path / "readonly-object"
    path.write_text("git object", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)
    retried = []

    def remove(target):
        retried.append(target)
        assert os.stat(target).st_mode & stat.S_IWUSR
        os.unlink(target)

    error = PermissionError("read-only")
    _remove_readonly(remove, str(path), (PermissionError, error, None))

    assert retried == [str(path)]
    assert not path.exists()


def test_remove_readonly_reraises_non_permission_error(tmp_path):
    path = tmp_path / "object"
    path.write_text("x", encoding="utf-8")
    error = OSError("different failure")

    with pytest.raises(OSError, match="different failure"):
        _remove_readonly(os.unlink, str(path), (OSError, error, None))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/nomenclature_matcher/matcher.py", True),
        ("src/nomenclature_matcher/nested/module.py", True),
        ("src/nomenclature_matcher", True),
        ("src/nomenclature_matcher_extra/matcher.py", False),
        ("src/nomenclature_matcher.py", False),
    ],
)
def test_trailing_slash_editable_scope_is_directory(tmp_path, path, expected):
    manager = WorkspaceManager(tmp_path, tmp_path / "isolation")
    allowed, violations = manager.verify_scope(
        [path], ["src/nomenclature_matcher/"], []
    )
    assert allowed is expected
    assert bool(violations) is not expected


def test_trailing_slash_protected_directory_blocks_nested_files(tmp_path):
    manager = WorkspaceManager(tmp_path, tmp_path / "isolation")
    allowed, violations = manager.verify_scope(
        ["tests/suite/test_private.py"], ["**"], ["tests/"]
    )
    assert not allowed
    assert violations == ["protected:tests/suite/test_private.py"]
