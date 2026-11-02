"""Versioned capability grants and fail-closed worker Tool Gateway."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import CapabilityPolicyV2, CapabilityToolV2
from .process import ProcessError, run_process
from .worker_queue import DurableWorkerQueue, WorkerQueueError


class CapabilityDenied(RuntimeError):
    """The requested operation is outside the current, non-revoked grant."""


class SandboxUnavailable(CapabilityDenied):
    """An operation that requires isolation cannot run on this host/backend."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CapabilityGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(pattern=r"^grant-[0-9a-f]{32}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=120)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal[1]
    revocation_epoch: int = Field(default=0, ge=0)
    tools: tuple[CapabilityToolV2, ...]
    read_path_layers: tuple[tuple[Annotated[str, Field(min_length=1, max_length=500)], ...], ...]
    write_path_layers: tuple[tuple[Annotated[str, Field(min_length=1, max_length=500)], ...], ...]
    backend: Literal["LOCAL", "ISOLATED"]
    network_access: bool
    expires_at: float | None = Field(default=None, allow_inf_nan=False)
    grant_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def allows_path(self, mode: str, relative: str) -> bool:
        layers = self.read_path_layers if mode == "read" else self.write_path_layers
        return bool(layers) and all(
            bool(patterns) and any(_matches(relative, pattern) for pattern in patterns)
            for patterns in layers
        )


class PermissionEngine:
    """Intersect campaign, role, and task ceilings into one immutable grant."""

    @staticmethod
    def derive(*, campaign_id: str, job_id: str, spec_hash: str, scope_digest: str,
               campaign: CapabilityPolicyV2 | None, role: CapabilityPolicyV2 | None,
               task: CapabilityPolicyV2 | None, expires_at: float | None = None) -> CapabilityGrantV1:
        layers = (campaign, role, task)
        if any(layer is None for layer in layers):
            raise CapabilityDenied("campaign, role, and task capability policies are all required")
        typed_layers = tuple(layer for layer in layers if layer is not None)
        tools = tuple(sorted(set(typed_layers[0].tools).intersection(*(set(x.tools) for x in typed_layers[1:]))))
        backend = "ISOLATED" if any(layer.backend == "ISOLATED" for layer in typed_layers) else "LOCAL"
        payload = {
            "campaign_id": campaign_id, "job_id": job_id, "spec_hash": spec_hash,
            "scope_digest": scope_digest, "policy_version": max(x.policy_version for x in typed_layers),
            "revocation_epoch": 0,
            "tools": tools, "read_path_layers": tuple(x.read_paths for x in typed_layers),
            "write_path_layers": tuple(x.write_paths for x in typed_layers), "backend": backend,
            "network_access": all(x.network_access for x in typed_layers), "expires_at": expires_at,
        }
        grant_digest = _digest(payload)
        return CapabilityGrantV1.model_validate({
            **payload, "grant_id": f"grant-{grant_digest[:32]}", "grant_digest": grant_digest,
        })


class CapabilityAuthority:
    """Durable grant/revocation authority colocated with the worker queue."""

    def __init__(self, registry_path: str | Path):
        self.path = Path(registry_path)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS capability_grants (
          grant_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, job_id TEXT NOT NULL,
          grant_digest TEXT NOT NULL, grant_json TEXT NOT NULL,
          revocation_epoch INTEGER NOT NULL DEFAULT 0 CHECK(revocation_epoch>=0),
          revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
          created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capability_audit (
          audit_id INTEGER PRIMARY KEY AUTOINCREMENT, grant_id TEXT NOT NULL,
          campaign_id TEXT NOT NULL, job_id TEXT NOT NULL, tool TEXT NOT NULL,
          relative_path TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL,
          revocation_epoch INTEGER NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS capability_audit_job_idx ON capability_audit(job_id,audit_id);
        """)

    def close(self) -> None:
        self.conn.close()

    def issue(self, grant: CapabilityGrantV1) -> CapabilityGrantV1:
        grant_payload = grant.model_dump(mode="json")
        signed_body = {key: value for key, value in grant_payload.items() if key not in {"grant_id", "grant_digest"}}
        computed_digest = _digest(signed_body)
        if (computed_digest != grant.grant_digest or grant.grant_id != f"grant-{computed_digest[:32]}"
                or grant.revocation_epoch != 0):
            raise CapabilityDenied("grant digest, identity, or initial revocation epoch is invalid")
        encoded = _canonical(grant_payload)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM capability_grants WHERE grant_id=?", (grant.grant_id,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO capability_grants(grant_id,campaign_id,job_id,grant_digest,grant_json,created_at) VALUES(?,?,?,?,?,?)",
                    (grant.grant_id, grant.campaign_id, grant.job_id, grant.grant_digest, encoded, time.time()),
                )
            elif (row["grant_digest"], row["grant_json"], row["campaign_id"], row["job_id"]) != (
                    grant.grant_digest, encoded, grant.campaign_id, grant.job_id):
                raise CapabilityDenied("grant ID is already bound to different immutable permissions")
            if row is not None and row["revoked"]:
                raise CapabilityDenied("a revoked grant cannot be reissued")
            self.conn.commit()
            return grant
        except Exception:
            self.conn.rollback()
            raise

    def revoke(self, grant_id: str) -> int:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT revocation_epoch,revoked FROM capability_grants WHERE grant_id=?", (grant_id,)).fetchone()
            if row is None:
                raise CapabilityDenied("unknown capability grant")
            if not row["revoked"]:
                self.conn.execute("UPDATE capability_grants SET revoked=1,revocation_epoch=revocation_epoch+1 WHERE grant_id=?", (grant_id,))
                epoch = int(row["revocation_epoch"]) + 1
            else:
                epoch = int(row["revocation_epoch"])
            self.conn.commit()
            return epoch
        except Exception:
            self.conn.rollback()
            raise

    def check_and_audit(self, grant: CapabilityGrantV1, *, tool: str, relative_path: str | None,
                        now: float | None = None) -> None:
        with self.grant_guard(grant, tool=tool, relative_path=relative_path, now=now):
            pass

    @contextmanager
    def grant_guard(self, grant: CapabilityGrantV1, *, tool: str, relative_path: str | None,
                    now: float | None = None):
        """Hold the registry writer reservation across one authorized effect.

        Revocation takes the same SQLite writer lock. A call already inside
        this guard linearizes before revoke; a revoke that acquires the lock
        first advances the epoch and causes this guard to deny.
        """
        moment = time.time() if now is None else now
        self.conn.execute("BEGIN IMMEDIATE")
        row = self.conn.execute("SELECT * FROM capability_grants WHERE grant_id=?", (grant.grant_id,)).fetchone()
        reason = "authorized"
        if row is None:
            reason = "unknown grant"
        elif (row["grant_digest"] != grant.grant_digest or row["grant_json"] != _canonical(grant.model_dump(mode="json"))
              or row["campaign_id"] != grant.campaign_id or row["job_id"] != grant.job_id):
            reason = "grant snapshot mismatch"
        elif row["revoked"] or row["revocation_epoch"] != grant.revocation_epoch:
            reason = "revoked grant"
        elif grant.expires_at is not None and moment >= grant.expires_at:
            reason = "expired grant"
        elif tool not in grant.tools:
            reason = "tool not granted"
        decision = "ALLOW" if reason == "authorized" else "DENY"
        epoch = int(row["revocation_epoch"]) if row is not None else 0
        self.conn.execute(
            "INSERT INTO capability_audit(grant_id,campaign_id,job_id,tool,relative_path,decision,reason,revocation_epoch,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (grant.grant_id, grant.campaign_id, grant.job_id, tool, relative_path, decision, reason, epoch, moment),
        )
        if decision == "DENY":
            self.conn.commit()
            raise CapabilityDenied(reason)
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def audit(self, job_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM capability_audit WHERE job_id=? ORDER BY audit_id", (job_id,)
        )]


class LocalBackend:
    """Trusted local gateway backend; deliberately offers no arbitrary command."""
    kind = "LOCAL"

    def probe(self, workspace: Path) -> bool:
        return workspace.is_dir()

    def run(self, argv: list[str], *, workspace: Path, artifacts: Path,
            timeout: int, network_access: bool,
            read_path_layers: tuple[tuple[str, ...], ...]) -> str:
        raise SandboxUnavailable("LOCAL backend does not execute arbitrary commands")


class BubblewrapBackend:
    """Linux namespace backend: read-only source, write-only artifact mount, no fallback."""
    kind = "ISOLATED"

    def __init__(self, binary: str | None = None):
        self.binary = binary or shutil.which("bwrap") or ""

    def build_argv(self, argv: list[str], workspace: Path, *, network_access: bool = False) -> list[str]:
        if not self.binary:
            raise SandboxUnavailable("bubblewrap is unavailable")
        source_raw = Path(workspace)
        if _is_reparse(source_raw):
            raise SandboxUnavailable("isolated mount root cannot be a symlink/reparse point")
        source = source_raw.resolve()
        if not source.is_dir():
            raise SandboxUnavailable("isolated mount roots must be real directories")
        command = [self.binary, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--clearenv"]
        if not network_access:
            command.append("--unshare-net")
        mounts = ("/usr", "/bin", "/lib", "/lib64")
        for mount in mounts:
            if Path(mount).exists():
                command.extend(["--ro-bind", mount, mount])
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--ro-bind", str(source), "/workspace"])
        command.extend(["--chdir", "/workspace", "--setenv", "HOME", "/tmp",
                        "--setenv", "PATH", "/usr/bin:/bin", *argv])
        return command

    def probe(self, workspace: Path) -> bool:
        if sys.platform != "linux" or not self.binary or not Path(self.binary).is_file():
            return False
        executable = shutil.which("true") or "/bin/true"
        try:
            result = run_process(self.build_argv([executable], workspace), cwd=workspace,
                                 timeout=5, check=False,
                                 env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
            return result.returncode == 0
        except (ProcessError, OSError, SandboxUnavailable):
            return False

    @staticmethod
    def _materialize_read_view(workspace: Path, destination: Path,
                               path_layers: tuple[tuple[str, ...], ...]) -> None:
        source_root = workspace.resolve()
        destination.mkdir(mode=0o700)
        for source in source_root.rglob("*"):
            if _is_reparse(source):
                relative = source.relative_to(source_root).as_posix()
                try:
                    _safe_relative(relative)
                except CapabilityDenied:
                    continue
                if all(patterns and any(_matches(relative, pattern) for pattern in patterns)
                       for patterns in path_layers):
                    raise CapabilityDenied("authorized command view contains a symlink/reparse point")
                continue
            if not source.is_file():
                continue
            try:
                relative = _safe_relative(source.relative_to(source_root).as_posix())
            except CapabilityDenied:
                continue
            if not all(patterns and any(_matches(relative, pattern) for pattern in patterns)
                       for patterns in path_layers):
                continue
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(source.read_bytes())
            target.chmod(0o400)

    def run(self, argv: list[str], *, workspace: Path, artifacts: Path,
            timeout: int, network_access: bool,
            read_path_layers: tuple[tuple[str, ...], ...]) -> str:
        if not self.probe(workspace):
            raise SandboxUnavailable("required Linux bubblewrap isolation is unavailable; refusing local fallback")
        try:
            with tempfile.TemporaryDirectory(prefix="bananloop-capability-view-") as directory:
                view = Path(directory) / "workspace"
                self._materialize_read_view(workspace, view, read_path_layers)
                result = run_process(self.build_argv(argv, view, network_access=network_access),
                                     cwd=view, timeout=timeout, check=True,
                                     env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
                if not isinstance(result.stdout, str):
                    raise CapabilityDenied("isolated tool returned non-text output")
                return result.stdout
        except ProcessError as exc:
            raise CapabilityDenied(f"isolated command failed: {exc}") from exc


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


_HIDDEN_SEGMENTS = {".git", ".bananloop_eval", "holdout", "validation", "evaluator", "secret", "secrets", "credentials"}


def _safe_relative(value: str, *, allow_root: bool = False) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise CapabilityDenied("path must use POSIX relative syntax")
    if allow_root and value in {"", "."}:
        return ""
    path = PurePosixPath(value)
    raw_parts = value.split("/")
    if (path.is_absolute() or PureWindowsPath(value).drive or not path.parts or path.as_posix() == "."
            or any(x in {"", ".", ".."} for x in raw_parts)):
        raise CapabilityDenied("path traverses outside the job workspace")
    parts = [part.lower() for part in path.parts]
    if any(part in _HIDDEN_SEGMENTS or part.startswith(".env") or
           any(marker in part for marker in ("evaluator", "evaluation", "validation", "holdout", "secret", "credential"))
           for part in parts):
        raise CapabilityDenied("protected path is never visible through the Tool Gateway")
    return path.as_posix()


def _matches(relative: str, pattern: str) -> bool:
    path = relative.replace("\\", "/")
    glob = pattern.replace("\\", "/")
    if glob.endswith("/**"):
        prefix = glob[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if glob.endswith("/"):
        prefix = glob.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return glob == "**" or fnmatch.fnmatchcase(path, glob)


class ToolGateway:
    """Single permission-checked filesystem/process/commit surface for a job."""

    def __init__(self, *, queue: DurableWorkerQueue, authority: CapabilityAuthority,
                 grant: CapabilityGrantV1, fence: int, workspace: str | Path,
                 artifacts: str | Path | None = None, backend: LocalBackend | BubblewrapBackend):
        self.queue, self.authority, self.grant, self.fence = queue, authority, grant, fence
        workspace_raw = Path(workspace)
        artifacts_raw = Path(artifacts) if artifacts is not None else None
        if _is_reparse(workspace_raw) or (artifacts_raw is not None and _is_reparse(artifacts_raw)):
            raise CapabilityDenied("workspace/artifact roots cannot be symlinks or reparse points")
        self.workspace = workspace_raw.resolve()
        self.artifacts = artifacts_raw.resolve() if artifacts_raw is not None else self.workspace / "artifacts"
        self.backend = backend
        if grant.backend == "ISOLATED" and backend.kind != "ISOLATED":
            raise CapabilityDenied("isolated grant cannot run on a local backend")
        if not backend.probe(self.workspace):
            raise SandboxUnavailable("selected execution backend is unavailable")

    @contextmanager
    def _authorized(self, tool: CapabilityToolV2, relative: str | None = None, *, mode: str | None = None,
                    allow_completed: bool = False):
        if mode is not None and (relative is None or not self.grant.allows_path(mode, relative)):
            raise CapabilityDenied(f"{mode} path is outside the intersected capability scope")
        with self.authority.grant_guard(self.grant, tool=tool, relative_path=relative):
            job = self._check_job_fence(allow_completed=allow_completed)
            yield job

    def _check_job_fence(self, *, allow_completed: bool) -> dict[str, Any]:
        try:
            job = self.queue.get_job(self.grant.job_id)
        except WorkerQueueError as exc:
            raise CapabilityDenied("worker job no longer exists") from exc
        states = {"DISPATCHED", "COMPLETED"} if allow_completed else {"DISPATCHED"}
        if (job["state"] not in states or int(job["fence"]) != self.fence
                or (job["state"] == "DISPATCHED" and (job["lease_until"] is None or float(job["lease_until"]) <= time.time()))):
            raise CapabilityDenied("worker dispatch fence is stale or expired")
        if job["request"].get("capability_digest") != self.grant.grant_digest:
            raise CapabilityDenied("job request is not bound to this immutable capability grant")
        return job

    @staticmethod
    def _assert_plain_parent_chain(root: Path, target: Path) -> None:
        try:
            relative = target.parent.relative_to(root)
        except ValueError as exc:
            raise CapabilityDenied("write path escapes its root") from exc
        current = root
        for component in relative.parts:
            current = current / component
            if _is_reparse(current) or not current.resolve().is_relative_to(root.resolve()):
                raise CapabilityDenied("write path traverses a symlink/reparse point")

    def _resolve(self, root: Path, relative: str, *, allow_missing: bool = False) -> Path:
        normalized = _safe_relative(relative)
        current = root
        if current.is_symlink() or not current.is_dir():
            raise CapabilityDenied("workspace root is missing or is a symlink")
        for component in PurePosixPath(normalized).parts:
            current = current / component
            if _is_reparse(current):
                raise CapabilityDenied("symlink/reparse paths are not allowed")
        try:
            resolved = current.resolve(strict=not allow_missing)
            resolved.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise CapabilityDenied("path escapes the job workspace") from exc
        return current

    def read_file(self, relative: str, *, encoding: str = "utf-8") -> str:
        relative = _safe_relative(relative)
        with self._authorized("read_file", relative, mode="read"):
            target = self._resolve(self.workspace, relative)
            if not target.is_file():
                raise CapabilityDenied("read_file target must be a regular file")
            return target.read_text(encoding=encoding)

    def list_files(self, relative: str = ".") -> list[str]:
        directory = _safe_relative(relative, allow_root=True)
        with self._authorized("list_files", directory or None):
            root = self.workspace if not directory else self._resolve(self.workspace, directory)
            if not root.is_dir():
                raise CapabilityDenied("list_files target must be a directory")
            found: list[str] = []
            for path in root.rglob("*"):
                if _is_reparse(path):
                    continue
                if path.is_file():
                    rel = path.relative_to(self.workspace).as_posix()
                    try:
                        _safe_relative(rel)
                    except CapabilityDenied:
                        continue
                    if self.grant.allows_path("read", rel):
                        found.append(rel)
            return sorted(found)

    def write_file(self, relative: str, content: str | bytes) -> None:
        relative = _safe_relative(relative)
        with self._authorized("write_file", relative, mode="write"):
            target = self._resolve(self.workspace, relative, allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._assert_plain_parent_chain(self.workspace, target)
            if not isinstance(content, (str, bytes)):
                raise CapabilityDenied("file content must be text or bytes")
            data = content.encode("utf-8") if isinstance(content, str) else content
            if len(data) > 25_000_000:
                raise CapabilityDenied("Tool Gateway file writes are limited to 25 MB")
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)

    def write_artifact(self, relative: str, content: str | bytes) -> None:
        relative = _safe_relative(relative)
        with self._authorized("write_artifact", relative, mode="write"):
            target = self._resolve(self.artifacts, relative, allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._assert_plain_parent_chain(self.artifacts, target)
            if not isinstance(content, (str, bytes)):
                raise CapabilityDenied("artifact content must be text or bytes")
            data = content.encode("utf-8") if isinstance(content, str) else content
            if len(data) > 25_000_000:
                raise CapabilityDenied("Tool Gateway artifacts are limited to 25 MB")
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)

    def run_command(self, argv: list[str], *, timeout: int = 60) -> str:
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\x00" not in x for x in argv):
            raise CapabilityDenied("commands must be a nonempty argv list; shell strings are forbidden")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise CapabilityDenied("command timeout must be between 1 and 3600 seconds")
        if len(argv) > 128 or sum(len(arg) for arg in argv) > 65_536 or any(len(arg) > 8192 for arg in argv):
            raise CapabilityDenied("command argument vector exceeds bounded limits")
        with self._authorized("run_command") as job:
            remaining = int(float(job["lease_until"]) - time.time())
            effective_timeout = min(timeout, 60, remaining - 1)
            if effective_timeout < 1:
                raise CapabilityDenied("worker lease is too short for another isolated command")
            return self.backend.run(argv, workspace=self.workspace, artifacts=self.artifacts,
                                    timeout=effective_timeout, network_access=self.grant.network_access,
                                    read_path_layers=self.grant.read_path_layers)

    def commit_candidate(self, changed_paths: Iterable[str], commit: Callable[[tuple[str, ...]], Any]) -> Any:
        with self._authorized("commit_candidate", allow_completed=True):
            paths = tuple(sorted(set(_safe_relative(path) for path in changed_paths)))
            if not paths:
                raise CapabilityDenied("candidate commit must include changed paths")
            for path in paths:
                if not self.grant.allows_path("write", path):
                    raise CapabilityDenied("write path is outside the intersected capability scope")
                self._resolve(self.workspace, path)
            return commit(paths)

    def commit_evidence(self, commit: Callable[[], Any]) -> Any:
        """Recheck grant epoch/fence immediately before trusted evidence commit."""
        with self._authorized("commit_candidate", allow_completed=True):
            return commit()
