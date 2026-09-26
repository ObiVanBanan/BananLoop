from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import tempfile
from pathlib import Path

from .process import run_process


class WorkspaceError(RuntimeError):
    pass


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

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        normalized = path.replace("\\", "/")
        for pattern in patterns:
            pattern = pattern.replace("\\", "/")
            if pattern == "**" or fnmatch.fnmatch(normalized, pattern):
                return True
            if pattern.endswith("/**"):
                prefix = pattern[:-3].rstrip("/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
        return False

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
        patch = run_process(
            ["git", "diff", "--cached", "--binary", "--full-index", self._agent_base],
            cwd=agent_workspace,
            timeout=30,
        ).stdout
        if patch:
            run_process(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=self.workspace,
                timeout=60,
                input_text=patch,
            )
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
