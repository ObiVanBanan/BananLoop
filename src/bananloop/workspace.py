from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .process import run_process


class WorkspaceError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise WorkspaceError(f"cannot inspect workspace path component {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _remove_readonly(func, path: str, exc_info) -> None:
    exc = exc_info[1]
    if not isinstance(exc, PermissionError):
        raise exc
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    func(path)


class WorkspaceManager:
    def __init__(self, source_repo: Path, workspace: Path):
        self.source_repo = source_repo.resolve()
        self.workspace = workspace.resolve()
        self.agent_workspace: Path | None = None
        self._agent_base: str | None = None

    def ensure(self, base_commit: str) -> None:
        if not self.workspace.exists():
            self.workspace.parent.mkdir(parents=True, exist_ok=True)
            run_process(["git", "clone", "--no-hardlinks", str(self.source_repo), str(self.workspace)], cwd=self.workspace.parent, timeout=120)
            run_process(["git", "config", "user.email", "bananloop@localhost"], cwd=self.workspace, timeout=10)
            run_process(["git", "config", "user.name", "BananLoop"], cwd=self.workspace, timeout=10)
            self.checkout(base_commit)

    def checkout(self, snapshot: str) -> None:
        run_process(["git", "reset", "--hard", snapshot], cwd=self.workspace, timeout=30)
        run_process(["git", "clean", "-fd"], cwd=self.workspace, timeout=30)

    def snapshot(self, message: str) -> str:
        run_process(["git", "add", "-A"], cwd=self.workspace, timeout=30)
        status = run_process(["git", "status", "--porcelain"], cwd=self.workspace, timeout=10).stdout.strip()
        if status:
            run_process(["git", "commit", "-m", message], cwd=self.workspace, timeout=60)
        return self.head()

    def head(self) -> str:
        return run_process(["git", "rev-parse", "HEAD"], cwd=self.workspace, timeout=10).stdout.strip()

    def changed_paths(self, parent: str, candidate: str) -> list[str]:
        if parent == candidate:
            return []
        out = run_process(["git", "diff", "--name-only", f"{parent}..{candidate}"], cwd=self.workspace, timeout=20).stdout
        return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]

    def diff_text(self, parent: str, candidate: str) -> str:
        if parent == candidate:
            return ""
        return run_process(
            ["git", "diff", "--unified=0", "--no-ext-diff", f"{parent}..{candidate}"],
            cwd=self.workspace,
            timeout=30,
        ).stdout

    def working_tree_changed_paths(self) -> list[str]:
        tracked = run_process(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=self.workspace,
            timeout=20,
        ).stdout.splitlines()
        untracked = run_process(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=self.workspace,
            timeout=20,
        ).stdout.splitlines()
        return sorted(
            {
                line.strip().replace("\\", "/")
                for line in [*tracked, *untracked]
                if line.strip()
            }
        )

    def snapshot_proof(self, parent: str, candidate: str) -> dict[str, object]:
        head = self.head()
        if head != candidate:
            raise WorkspaceError(
                f"candidate checkout mismatch: expected HEAD {candidate}, got {head}"
            )
        try:
            tree = run_process(
                ["git", "rev-parse", f"{candidate}^{{tree}}"],
                cwd=self.workspace,
                timeout=10,
            ).stdout.strip()
            head_tree = run_process(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=self.workspace,
                timeout=10,
            ).stdout.strip()
        except Exception as exc:
            raise WorkspaceError(
                f"candidate snapshot {candidate} is not a readable commit"
            ) from exc
        if not tree or tree != head_tree:
            raise WorkspaceError(
                f"candidate tree mismatch for snapshot {candidate}"
            )
        changed = self.changed_paths(parent, candidate)
        diff = run_process(
            ["git", "diff", "--binary", "--full-index", f"{parent}..{candidate}"],
            cwd=self.workspace,
            timeout=30,
        ).stdout
        return {
            "parent_snapshot": parent,
            "candidate_snapshot": candidate,
            "tree": tree,
            "changed_paths": changed,
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        normalized = path.replace("\\", "/")
        for pattern in patterns:
            pattern = pattern.replace("\\", "/")
            # A trailing slash denotes a directory, not a filename glob.
            # Use a component boundary so "src/foo/" never matches "src/foobar".
            if pattern.endswith("/"):
                prefix = pattern.rstrip("/")
                if prefix and (normalized == prefix or normalized.startswith(prefix + "/")):
                    return True
                continue
            if pattern == "**" or fnmatch.fnmatch(normalized, pattern):
                return True
            if pattern.endswith("/**"):
                prefix = pattern[:-3].rstrip("/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
        return False

    @staticmethod
    def _assert_plain_workspace_path(root: Path, relative: str, label: str) -> None:
        """Reject traversal and any symlink/reparse component below a workspace root."""
        normalized = relative.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(normalized)
        if (posix.is_absolute() or windows.drive or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        )):
            raise WorkspaceError(f"{label} changed path is not a safe relative path: {relative!r}")
        resolved_root = root.resolve()
        current = resolved_root
        for component in posix.parts:
            current = current / component
            if _is_reparse_point(current):
                raise WorkspaceError(
                    f"{label} changed path traverses a symlink or reparse point: {relative!r}"
                )
        try:
            current.resolve(strict=False).relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise WorkspaceError(
                f"{label} changed path escapes its workspace: {relative!r}"
            ) from exc

    def prepare_agent_workspace(self, hidden_paths: list[str]) -> Path:
        self.cleanup_agent_workspace()
        self.agent_workspace = Path(tempfile.mkdtemp(prefix="bananloop-agent-")).resolve()
        hidden = [".git", ".git/**", ".bananloop_eval", ".bananloop_eval/**", *hidden_paths]

        def ignore(directory: str, names: list[str]) -> set[str]:
            current = Path(directory)
            ignored: set[str] = set()
            for name in names:
                path = current / name
                try:
                    relative = path.relative_to(self.workspace).as_posix()
                except ValueError:
                    continue
                if self._matches(relative, hidden):
                    ignored.add(name)
            return ignored

        shutil.copytree(
            self.workspace,
            self.agent_workspace,
            ignore=ignore,
            symlinks=True,
            dirs_exist_ok=True,
        )
        run_process(["git", "init"], cwd=self.agent_workspace, timeout=30)
        run_process(["git", "config", "user.email", "bananloop@localhost"], cwd=self.agent_workspace, timeout=10)
        run_process(["git", "config", "user.name", "BananLoop Agent Sandbox"], cwd=self.agent_workspace, timeout=10)
        run_process(["git", "add", "-A"], cwd=self.agent_workspace, timeout=30)
        run_process(["git", "commit", "-m", "bananloop sanitized agent baseline"], cwd=self.agent_workspace, timeout=60)
        self._agent_base = run_process(["git", "rev-parse", "HEAD"], cwd=self.agent_workspace, timeout=10).stdout.strip()
        return self.agent_workspace

    def apply_agent_changes(self, agent_workspace: Path, hidden_paths: list[str]) -> list[str]:
        if agent_workspace != self.agent_workspace or self._agent_base is None:
            raise WorkspaceError("agent workspace has no supervisor-owned baseline")
        run_process(["git", "add", "-A"], cwd=agent_workspace, timeout=30)
        changed_out = run_process(
            ["git", "diff", "--cached", "--name-only", self._agent_base],
            cwd=agent_workspace,
            timeout=20,
        ).stdout
        changed = [line.strip().replace("\\", "/") for line in changed_out.splitlines() if line.strip()]
        hidden = [".git", ".git/**", ".bananloop_eval", ".bananloop_eval/**", *hidden_paths]
        violations = [path for path in changed if self._matches(path, hidden)]
        if violations:
            raise WorkspaceError(
                "agent attempted to write hidden path(s): " + ", ".join(sorted(violations))
            )
        for path in changed:
            self._assert_plain_workspace_path(agent_workspace, path, "agent")
            self._assert_plain_workspace_path(self.workspace, path, "supervisor")
        patch = run_process(
            ["git", "diff", "--cached", "--binary", "--full-index", self._agent_base],
            cwd=agent_workspace,
            timeout=30,
            binary=True,
        ).stdout
        if patch:
            if not isinstance(patch, bytes):
                raise WorkspaceError("binary git diff returned text instead of bytes")
            run_process(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=self.workspace,
                timeout=60,
                input_bytes=patch,
                binary=True,
            )
        for path in changed:
            self._assert_plain_workspace_path(self.workspace, path, "supervisor")
        return changed

    def cleanup_agent_workspace(self) -> None:
        workspace = self.agent_workspace
        self.agent_workspace = None
        self._agent_base = None
        if workspace is None or not workspace.exists():
            return
        try:
            shutil.rmtree(workspace, onerror=_remove_readonly)
        except OSError as exc:
            raise WorkspaceError(
                f"cannot remove temporary agent workspace {workspace}: {exc}"
            ) from exc

    def verify_scope(self, changed: list[str], editable_scope: list[str], protected_paths: list[str]) -> tuple[bool, list[str]]:
        violations: list[str] = []
        for path in changed:
            if self._matches(path, protected_paths):
                violations.append(f"protected:{path}")
            elif not self._matches(path, editable_scope):
                violations.append(f"outside_scope:{path}")
        return not violations, violations


def assert_clean_source(repo: Path) -> None:
    out = run_process(["git", "status", "--porcelain"], cwd=repo, timeout=10).stdout.strip()
    if out:
        raise WorkspaceError("source repository is dirty; commit/stash changes or pass --allow-dirty")


def current_commit(repo: Path) -> str:
    return run_process(["git", "rev-parse", "HEAD"], cwd=repo, timeout=10).stdout.strip()
