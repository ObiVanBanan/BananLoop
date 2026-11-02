from __future__ import annotations

import sys
import sqlite3

import pytest
from pydantic import ValidationError

from bananloop.budget_ledger import BudgetLedger
from bananloop.capabilities import (
    BubblewrapBackend,
    CapabilityAuthority,
    CapabilityDenied,
    CapabilityGrantV1,
    LocalBackend,
    PermissionEngine,
    SandboxUnavailable,
    ToolGateway,
)
from bananloop.contracts import CapabilityPolicyV2
from bananloop.worker_queue import DurableWorkerQueue


def _fixture(tmp_path, *, backend="LOCAL", tools=("read_file", "write_file", "commit_candidate")):
    db = tmp_path / "registry.sqlite3"
    ledger = BudgetLedger(db)
    ledger.create_root("campaign", {"agent_calls": 5})
    ledger.close()
    policy = CapabilityPolicyV2(policy_version=1, tools=tools,
                                read_paths=("src/**", "README.md"),
                                write_paths=("artifacts/**",), backend=backend)
    grant = PermissionEngine.derive(
        campaign_id="campaign", job_id="job-1", spec_hash="b" * 64,
        scope_digest="c" * 64, campaign=policy, role=policy, task=policy,
    )
    queue = DurableWorkerQueue(db)
    queue.enqueue(job_id="job-1", enqueue_key="job-1", campaign_id="campaign",
                  action_id="explore:n1", resource="agent_calls", upper_bound=1,
                  request={"capability_digest": grant.grant_digest})
    lease = queue.claim("job-1", "worker-1", lease_seconds=120)
    queue.mark_dispatched("job-1", "worker-1", lease["fence"])
    authority = CapabilityAuthority(db)
    grant = authority.issue(grant)
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "main.py").write_text("print('hi')\n")
    return db, queue, authority, grant, workspace


def test_revoked_grant_denies_tool_and_candidate_commit(tmp_path):
    _, queue, authority, grant, workspace = _fixture(tmp_path)
    gateway = ToolGateway(queue=queue, authority=authority, grant=grant, fence=1,
                          workspace=workspace, backend=LocalBackend())
    assert gateway.read_file("src/main.py") == "print('hi')\n"
    authority.revoke(grant.grant_id)
    with pytest.raises(CapabilityDenied, match="revoked"):
        gateway.read_file("src/main.py")
    committed = []
    with pytest.raises(CapabilityDenied, match="revoked"):
        gateway.commit_candidate(["artifacts/result.txt"], lambda paths: committed.append(paths))
    assert not committed
    queue.close()
    authority.close()


def test_candidate_commit_guard_serializes_revoke_with_commit(tmp_path):
    db, queue, authority, grant, _workspace = _fixture(tmp_path)
    competing = CapabilityAuthority(db)
    competing.conn.execute("PRAGMA busy_timeout=1")
    with authority.grant_guard(grant, tool="commit_candidate", relative_path=None):
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            competing.revoke(grant.grant_id)
    assert competing.revoke(grant.grant_id) == 1
    with pytest.raises(CapabilityDenied, match="revoked"):
        authority.check_and_audit(grant, tool="commit_candidate", relative_path=None)
    competing.close()
    authority.close()
    queue.close()


def test_effective_permissions_cannot_broaden_campaign_scope(tmp_path):
    campaign = CapabilityPolicyV2(tools=("read_file", "write_file"), read_paths=("src/**",),
                                  write_paths=("src/**",), backend="LOCAL")
    role = CapabilityPolicyV2(tools=("read_file",), read_paths=("src/**",),
                              write_paths=("src/**",), backend="LOCAL")
    task = CapabilityPolicyV2(tools=("read_file", "write_file"), read_paths=("**",),
                              write_paths=("**",), backend="LOCAL")
    grant = PermissionEngine.derive(campaign_id="c", job_id="j", spec_hash="a" * 64,
                                    scope_digest="b" * 64, campaign=campaign, role=role, task=task)
    assert grant.tools == ("read_file",)
    assert grant.allows_path("read", "src/main.py")
    assert not grant.allows_path("write", "src/main.py")
    assert not grant.allows_path("read", "private.txt")


def test_gateway_denies_hidden_paths_traversal_symlinks_and_escape(tmp_path):
    _, queue, authority, grant, workspace = _fixture(tmp_path)
    (workspace / ".bananloop_eval").mkdir()
    (workspace / ".bananloop_eval" / "gold.json").write_text("hidden")
    outside = tmp_path / "outside.txt"
    outside.write_text("host secret")
    (workspace / "src" / "escape.txt").symlink_to(outside)
    gateway = ToolGateway(queue=queue, authority=authority, grant=grant, fence=1,
                          workspace=workspace, backend=LocalBackend())
    for path in ("../outside.txt", "src/./main.py", "src//main.py", ".bananloop_eval/gold.json", "src/escape.txt", "/etc/passwd", "C:/secret"):
        with pytest.raises(CapabilityDenied):
            gateway.read_file(path)
    with pytest.raises(CapabilityDenied):
        gateway.write_file("src/../escape.py", "bad")
    queue.close()
    authority.close()


def test_isolated_command_view_contains_only_granted_read_paths(tmp_path):
    _, queue, authority, grant, workspace = _fixture(tmp_path)
    (workspace / "private.txt").write_text("ungranted")
    (workspace / ".bananloop_eval").mkdir()
    (workspace / ".bananloop_eval" / "gold.txt").write_text("hidden")
    view = tmp_path / "sandbox-view"
    BubblewrapBackend._materialize_read_view(workspace, view, grant.read_path_layers)
    assert sorted(path.relative_to(view).as_posix() for path in view.rglob("*") if path.is_file()) == ["src/main.py"]
    queue.close()
    authority.close()


def test_local_backend_cannot_satisfy_isolated_grant(tmp_path):
    _, queue, authority, grant, workspace = _fixture(tmp_path, backend="ISOLATED", tools=("run_command",))
    with pytest.raises(CapabilityDenied, match="isolated"):
        ToolGateway(queue=queue, authority=authority, grant=grant, fence=1,
                    workspace=workspace, backend=LocalBackend())
    queue.close()
    authority.close()


def test_missing_sandbox_fails_closed_without_local_fallback(tmp_path, monkeypatch):
    _, queue, authority, grant, workspace = _fixture(tmp_path, backend="ISOLATED", tools=("run_command",))
    backend = BubblewrapBackend(binary="missing-bwrap")
    monkeypatch.setattr(backend, "probe", lambda cwd: False)
    with pytest.raises(SandboxUnavailable):
        ToolGateway(queue=queue, authority=authority, grant=grant, fence=1,
                    workspace=workspace, backend=backend)
    queue.close()
    authority.close()


def test_isolated_backend_disables_network_and_passes_workspace_only(tmp_path):
    backend = BubblewrapBackend(binary="/usr/bin/bwrap")
    argv = backend.build_argv([sys.executable, "-c", "pass"], tmp_path)
    assert "--unshare-net" in argv
    assert "--ro-bind" in argv and str(tmp_path.resolve()) in argv
    assert "/artifacts" not in argv
    assert "--clearenv" in argv
    online = backend.build_argv([sys.executable, "-c", "pass"], tmp_path, network_access=True)
    assert "--unshare-net" not in online


def test_isolated_backend_fails_closed_on_windows(tmp_path, monkeypatch):
    backend = BubblewrapBackend(binary="/usr/bin/bwrap")
    monkeypatch.setattr("bananloop.capabilities.sys.platform", "win32")
    assert backend.probe(tmp_path) is False


def test_capability_policy_rejects_protected_scope_and_local_commands():
    with pytest.raises(ValidationError):
        CapabilityPolicyV2(tools=("read_file",), read_paths=(".bananloop_eval/**",))
    with pytest.raises(ValidationError):
        CapabilityPolicyV2(tools=("run_command",), backend="LOCAL")
