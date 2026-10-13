from __future__ import annotations

import os
import stat
import sys

import pytest

from bananloop.workspace import WorkspaceManager, _remove_readonly


def test_directory_scope_with_trailing_slash_matches_nested_files(tmp_path):
    workspace = WorkspaceManager(tmp_path, tmp_path / "workspace")

    valid, violations = workspace.verify_scope(
        ["src/nomenclature_matcher/matcher.py", "src/nomenclature_matcher/nested/index.py"],
        ["src/nomenclature_matcher/"],
        [],
    )

    assert valid
    assert violations == []


def test_directory_scope_with_trailing_slash_does_not_match_sibling(tmp_path):
    workspace = WorkspaceManager(tmp_path, tmp_path / "workspace")

    valid, violations = workspace.verify_scope(
        ["src/nomenclature_matcher_extra/matcher.py"],
        ["src/nomenclature_matcher/"],
        [],
    )

    assert not valid
    assert violations == ["outside_scope:src/nomenclature_matcher_extra/matcher.py"]


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
