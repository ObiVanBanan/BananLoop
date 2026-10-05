# M6 security review: Codex lifecycle bridge

## Findings and repairs

- **P2, repaired:** lock probing could follow a symlinked `campaign.lock` and open its target for writing. The hook now rejects reparse/symlink state directories and lock paths before acquiring the lock.
- **P2, repaired:** registry SQLite access could follow a symlinked `campaign-registry.sqlite3`. The registry now checks the registry path before normal, read-only, and session-binding opens.
- **P2, repaired:** plugin docs said the hook only reads registry/campaign state. They now state that it writes only a hashed session-to-campaign binding in the registry.

## Verified boundaries

SQL values are parameterized; managed campaign IDs are format-checked and resolved through the registry; session IDs are bounded and NUL-checked, with only SHA-256 hashes persisted. Campaign state opens read-only. The hook requires a known RUNNING campaign, no stop request, a free validated lock, and no previous Stop continuation. It launches no subprocess, model, agent, evaluator, or campaign and emits no session ID or credential data.

Targeted regression tests cover the symlink refusal paths, but three symlink tests are skipped on this Windows host because symlink creation fails with `WinError 1314`. The guards are therefore code-reviewed here but those symlink branches need execution on a host with symlink privileges. Security review ran once; after its findings were repaired, targeted tests and the full suite were rerun rather than repeating the security pass.
