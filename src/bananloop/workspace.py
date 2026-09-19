from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from .process import run_process


class WorkspaceError(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(self, source_repo: Path, workspace: Path):
        self.source_repo = source_repo.resolve()
        self.workspace = workspace.resolve()

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

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        normalized = path.replace("\\", "/")
        return any(pattern == "**" or fnmatch.fnmatch(normalized, pattern) for pattern in patterns)

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
