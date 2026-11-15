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

The installed `codex exec --help` documents no hard per-run provider-request
cap. A process-level reservation cannot prove a 250-request maximum if one CLI
process makes multiple subscription requests. A separately reviewed adapter
or a supported hard cap is required. BananLoop's current Optimization handoff
also pauses for an owner pivot decision; this package does not bypass it.

For these reasons the only permitted result today is `PREP_BLOCKED`. The 250
calls remain unused. RPDI-14 is unchanged, and a real run requires a separate
owner authorization over a frozen manifest and exact launch command.
