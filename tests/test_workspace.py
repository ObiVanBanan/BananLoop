from __future__ import annotations

import os
import stat
import sys

import pytest

from bananloop.workspace import _remove_readonly


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
