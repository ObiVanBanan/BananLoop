# RPDI-06: capability grants and Tool Gateway

## Frozen campaign policy

`AutonomySpecV2.capability_policy` is an optional, strict version-1 permission
ceiling. It pins tool names, read/write path patterns, network access, and the
minimum execution backend. Omitted policy means no v2 worker grant and fails
closed at enqueue. Older v2 specs that omit it retain their prior approved
hash; an explicit policy is part of the approved hash. v1 serialization and
Engine behavior are unchanged.

`PermissionEngine` intersects the campaign, role, and task tool sets, path
patterns, and network permissions. Path access must match every policy layer;
an empty layer denies access. Any layer requiring `ISOLATED` makes the grant
isolated. Grant IDs and digests bind the immutable policy to campaign, spec,
and job.

## Grant lifecycle and operations

The registry database stores immutable grants, expiry and revocation state,
the revocation epoch, and an audit row for each accepted/denied tool check.
Every gateway operation checks the immutable grant snapshot, current epoch,
expiry, durable queue state, and current worker fence. The registry writer lock
is held through each authorized effect and final evidence commit, so revocation
either wins before an operation starts or waits until that already-authorized
effect finishes; it cannot race between the authorization check and commit.

`ToolGateway` is the only supported v2 file/process surface:

- Reads and listings expose only granted files; writes and artifact writes
  require the corresponding tool and path scope.
- Paths are relative to per-job roots. Traversal, Windows drive/UNC forms,
  symlinks/reparse points, evaluator/validation/holdout/secret paths, and paths
  outside the workspace fail closed.
- `LocalBackend` supports gateway-mediated file operations only and cannot run
  arbitrary commands or satisfy an isolated grant.
- Linux `BubblewrapBackend` uses user/PID namespaces, denies network by default,
  and mounts a temporary read-only view containing only files that match every
  granted read-path layer. The artifact directory and the rest of the host
  workspace are not mounted. It has no local fallback. Writes go through
  `ToolGateway`, where path grants are checked.
- No supported Windows isolation provider is configured in this change. An
  isolated grant on Windows therefore fails closed; this is an explicit
  platform limit, not evidence that Windows isolation has been tested.

Codex extra arguments cannot replace the supervisor-selected sandbox/profile
or inject sandbox/config policy overrides. The RPDI-05 evidence adapter now
requires a frozen capability policy and invokes its trusted execution hook
through `ToolGateway`; the hook itself remains trusted Core code rather than
an OS-isolated Python plugin.

## Transfer decisions

PR #47 (`b009d6f`), #52 (`caafe5e`), and #53 (`fc0ab76`) are Research
Intelligence changes and stay out of RPDI-06. In particular, #52's
`DiagnosticCapability` filters evaluator diagnostics and does not authorize
tool or filesystem access. These sources will be reconciled in RPDI-10/11.

## Acceptance

Tests cover legacy v2 hash preservation, explicit permission hash binding,
campaign/role/task narrowing, revoked tool and commit denial, hidden/traversal/
symlink/escape denial, local-vs-isolated backend selection, no-fallback sandbox
failure, read-only/network-isolated mount arguments, Codex override rejection,
and revocation during exploration result publication.
