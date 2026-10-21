# M4 completion: multiple campaigns per repository

Completed 2026-10-05.

## Delivered

- A repo-level SQLite index stores campaign ID, request key, goal fingerprint/summary, relative state path and legacy/managed origin. It also stores one active campaign binding. Registry rows never duplicate campaign status or result; campaign SQLite remains authoritative.
- `goal`/`start` reserve the registry identity before bootstrap. Repeating the same normalized goal reuses it; `--request-id` makes identity explicit and can intentionally start another run for the same goal. Reusing an ID with a different goal errors. Paused campaigns resume and terminal campaigns return their recorded status.
- Each managed campaign has an isolated path-safe state directory and its own campaign lock. `campaigns list` enumerates entries; run/resume/status/stop/report/export accept `--campaign-id`; default operation uses the active binding.
- Existing `campaign.sqlite3` state stays in place as `legacy`. The legacy goal is read using SQLite read-only mode; no CampaignStore migration or file rewrite occurs during discovery. Explicit `--state-dir` keeps the previous standalone state behavior.
- Managed/legacy DB symlink and reparse-point paths are refused before normal CLI opens. A corrupt registry is preserved rather than replaced.

## Verification

- Independent combined regression: 44 passed, 1 skipped before the final key-collision regression.
- Post-repair registry/CLI regression: 29 passed, 1 skipped. The skipped symlink case was unavailable because Windows denied symlink creation (`WinError 1314`).
- Full suite: 194 tests collected; 190 passed, 2 failed, 2 skipped. Both failures are the pre-existing Windows child-process cancellation tests: `tests/test_process.py::test_cancellation_stops_child_work[timeout]` and `[stop]`. They also failed in the earlier baseline.
- `python -m compileall -q src/bananloop` and `git diff --check` passed; Git emitted only line-ending conversion warnings.

## Review and security

One review pass found that a legacy campaign could be duplicated on same-goal retry. The fix reads the old spec read-only, records its fingerprint, and uses a synthetic legacy request key to avoid uniqueness collisions. A focused security pass found that a campaign database symlink could redirect SQLite; `lstat`/reparse-point checks now reject it. No second review pass was run. The symlink regression is skipped on this Windows host due to missing link privilege.

## Limitations and rollback

The registry and OS file lock coordinate BananLoop processes that use the same state root; a hostile process with write access to that local state can still alter files. Registry stores routing metadata only; it is not a second outcome database.

Rollback only M4 registry/CLI/tests/docs/ADR/planning changes; preserve legacy state roots and M1–M3 changes. No campaign DB schema migration or new dependency was introduced. No commit or push was performed.
