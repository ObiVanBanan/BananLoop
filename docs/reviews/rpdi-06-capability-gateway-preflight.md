# RPDI-06 capability gateway preflight

**Base:** `origin/main` at `7e54122` (RPDI-05)

## Current state

- v1 authorization is mostly prompt, hidden-file copy filtering, and post-action
  diff validation. That does not provide per-tool permission checks or process
  isolation.
- `WorkspaceManager.prepare_agent_workspace` preserves symlinks while copying;
  changed-path checks happen after the agent has already had read access.
- `CommandAgentAdapter` and `PiAgentAdapter` run local commands without an OS
  sandbox. `CodexAdapter` requests a sandbox but does not attest that it is
  available, and `extra_args` can append sandbox/config overrides after the
  selected mode.
- v2 `AutonomySpecV2` has an opaque scope digest but no structured permission
  policy, grant ID, version, expiry, or revocation epoch. RPDI-05 binds a path
  allowlist, not an enforceable tool grant.

## Contract decisions

- Campaign policy, role policy, and task policy are separate ceilings. Effective
  tools and network permissions are intersections; path authorization must
  match every layer. A child role/task can narrow but cannot broaden a parent.
- Every grant is immutable and versioned, bound to campaign/spec/job and a
  capability digest. The registry database stores revocation state and an
  epoch; each tool call and candidate commit rechecks the current epoch and
  active worker fence.
- A single Tool Gateway owns path read/write, command, and candidate-commit
  entry points. Paths are relative to the job workspace; traversal, symlinks,
  reparse points, protected evaluator/validation/holdout/secret paths, and
  worktree escapes fail closed.
- Local backend exposes only gateway-mediated filesystem operations and cannot
  satisfy an isolated grant or run arbitrary commands. Isolated backend must
  positively probe a supported OS sandbox; missing/unknown/failing sandbox
  yields `BLOCKED_SAFE`, never fallback to local execution.
- RPDI-06 does not transfer RI/causal-memory code. PR #47 (base `b009d6f`),
  #52 (`caafe5e`), and #53 (`fc0ab76`) are Research Intelligence changes.
  `DiagnosticCapability` from #52 scopes evaluator diagnostics only; it is not
  a tool authorization grant. Reconcile those sources in RPDI-10/11.

## Required test IDs

- `test_revoked_grant_denies_tool_and_candidate_commit`
- `test_task_and_role_permissions_cannot_broaden_campaign_grant`
- `test_gateway_denies_hidden_paths_traversal_symlinks_and_worktree_escape`
- `test_local_backend_cannot_satisfy_isolated_grant`
- `test_missing_or_unknown_sandbox_fails_closed_without_local_fallback`
- `test_isolated_backend_disables_network_and_mounts_only_declared_workspace`
- `test_codex_extra_args_cannot_override_supervisor_sandbox`
- v1 campaign spec serialization/hash and existing Engine behavior remain unchanged.

## Platform note

Preflight observed `/usr/bin/bwrap` on the Linux development host, but this is
not CI evidence and does not establish host policy or Windows isolation. Linux
isolated execution must probe the actual namespace operation, not only the
binary version. Unsupported Windows configurations must fail closed for tasks
requiring isolation. Local filesystem gateway tests run cross-platform.
