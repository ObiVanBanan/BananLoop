from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest

from bananloop.workspace import WorkspaceError, WorkspaceManager, _remove_readonly


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


def _patch_manager(tmp_path, *, nested=False):
    source = tmp_path / "source"
    workspace = tmp_path / "supervisor-workspace"
    agent_workspace = tmp_path / "agent-workspace"

    def initialize_repo(path):
        path.mkdir()
        (path / "matcher.py").write_bytes(b"before\r\n")
        if nested:
            (path / "nested").mkdir()
            (path / "nested" / "child.py").write_bytes(b"before\r\n")
        def git(*args):
            subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
        git("init")
        git("config", "core.autocrlf", "false")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("add", "-A")
        git("commit", "-m", "baseline")
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    initialize_repo(source)
    initialize_repo(workspace)
    agent_base = initialize_repo(agent_workspace)
    manager = WorkspaceManager(source, workspace)
    manager.agent_workspace = agent_workspace
    manager._agent_base = agent_base
    return manager, agent_workspace, workspace


def test_apply_agent_changes_preserves_crlf_patch_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-git-config"))
    manager, agent_workspace, workspace = _patch_manager(tmp_path)

    (agent_workspace / "matcher.py").write_bytes(b"after\r\n")
    changed = manager.apply_agent_changes(agent_workspace, [])
    assert changed == ["matcher.py"]
    assert (workspace / "matcher.py").read_bytes() == b"after\r\n"


def _create_symlink_or_skip(link: Path, target: Path, *, directory: bool = False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(
                "Windows symlink test requires Developer Mode or SeCreateSymbolicLinkPrivilege: "
                f"{exc}"
            )
        pytest.skip(f"symlinks are unavailable on this filesystem: {exc}")


def test_apply_agent_changes_rejects_direct_symlink_in_agent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-git-config"))
    manager, agent_workspace, workspace = _patch_manager(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (agent_workspace / "matcher.py").unlink()
    _create_symlink_or_skip(agent_workspace / "matcher.py", outside)

    with pytest.raises(WorkspaceError, match="agent changed path traverses a symlink or reparse point"):
        manager.apply_agent_changes(agent_workspace, [])
    assert (workspace / "matcher.py").read_bytes() == b"before\r\n"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_apply_agent_changes_rejects_symlinked_supervisor_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-git-config"))
    manager, agent_workspace, workspace = _patch_manager(tmp_path, nested=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "child.py").write_bytes(b"outside\r\n")
    (workspace / "nested" / "child.py").unlink()
    (workspace / "nested").rmdir()
    _create_symlink_or_skip(workspace / "nested", outside, directory=True)
    (agent_workspace / "nested" / "child.py").write_bytes(b"after\r\n")

    with pytest.raises(WorkspaceError, match="supervisor changed path traverses a symlink or reparse point"):
        manager.apply_agent_changes(agent_workspace, [])
    assert (outside / "child.py").read_bytes() == b"outside\r\n"
