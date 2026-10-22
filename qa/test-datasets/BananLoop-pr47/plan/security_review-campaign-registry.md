# Security review: M4 campaign registry

Date: 2026-10-05

## Finding and repair

**Minor path redirection, fixed:** `_validate_state_path()` validated managed campaign directories but could allow `campaign.sqlite3` itself to be a symlink/reparse point. Registry resolution now calls `lstat` and rejects a symlink/reparse DB before SQLite access, including the legacy DB path. A test exercises this on hosts with symlink creation enabled; it was skipped here because Windows returned `WinError 1314`.

## Other checks

- Request IDs are hashed before storage and never appear in filesystem paths.
- Managed state paths must be a relative `campaigns/c-<hex>` child and are resolved/checked under the project root.
- Registry corruption is surfaced with its path and left intact; no silent replacement was observed.
- Different requests share only the registry index; their campaign DBs/workspaces and campaign locks are separate.

No additional material security finding was reported. OS-level malicious users who can modify the same local state remain outside the product's trust boundary.
