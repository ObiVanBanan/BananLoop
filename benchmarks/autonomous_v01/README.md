# Autonomous benchmark v0.1 offline preparation

This package is an **offline preparation gate**, not a live benchmark launcher.
It does not construct a BananLoop campaign, start Codex, invoke Harbor, contact
an evaluator, or run an AI Judge. `OFFLINE-PREFLIGHT.json` records the current
evidence and therefore fails closed.

Run the read-only preflight with:

```sh
python -m benchmarks.autonomous_v01.runner preflight \
  --codex-help-file /path/to/captured-codex-exec-help.txt
```

The module contains reusable primitives for an atomic shared reservation
ledger, evaluator-bound verified candidate checkpoints, and 24-arm fake-run
acceptance checks. `build_manifest` reproducibly selects and orders pairs only
when 12 complete task/metric/adapter records are supplied. The fake runner
accepts only injected fake backends; there is no live backend in this package.

## Current blockers

The frozen AutoResearchExam shortlist has nine non-proxy adapters. Its tenth
task (`budgeted-covtype-dual-market-open`) has a static-price proxy evaluator
and is excluded. Three additional task-specific preparations, evaluator
contracts, and reviewed Harbor overlays are needed before a 12-task manifest
can be frozen. Selecting arbitrary upstream tasks would not meet the pinned
adapter/evaluator acceptance criteria.

Policy revised 2026-11-15: time-only 3600s per arm, no 250-call hard cap. The
current Python runner STILL enforces the older cap and must be refactored before
it can be run live. Record usage as observational data. The normal Optimization
handoff pauses for owner approval; only a benchmark-specific preregistered
and scope-bound pivot authorization can replace repeated manual pauses.

For these reasons the status remains `PREP_BLOCKED`. Prior offline-preflight
is historical and must be regenerated under the new time-only policy. See
[updated decisions](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-DECISIONS-2026-11-15.md).
RPDI-14 is unchanged and a real run needs separate owner approval of exact
frozen manifest and launch command.
