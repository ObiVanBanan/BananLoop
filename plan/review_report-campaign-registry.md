# Review report: M4 campaign registry

Date: 2026-10-05

## Finding and repair

**P1 compatibility/idempotency, fixed:** a lazily registered legacy campaign initially had an empty goal fingerprint, so repeating its goal could create another managed campaign. Registry now reads legacy `spec_json` read-only, stores the fingerprint/summary, and reuses the legacy entry on a same-goal retry. Legacy retains a synthetic request key so a same-goal managed entry cannot prevent lazy discovery. Explicit `--request-id` remains a way to intentionally distinguish a new run.

## Validation

One review pass only. The repair added legacy paused/terminal retry and managed/legacy key-collision tests. Post-repair registry/CLI tests passed: 29 passed, 1 skipped. No second review pass was performed.

The full suite retains only the two Windows child-process cancellation failures already present in the previous baseline, plus platform-skipped symlink tests.
