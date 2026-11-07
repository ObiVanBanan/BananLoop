# v0.2 Optimization CLI correction — 2026-11-08

Implementation revision: `80dede2169c6d38551855baeaab169628aef0f88`.

## Defect fixed

The shared Exploration role registry pins `optimizer` and `integration_verifier` as optional (`min_instances=0`) because the parent campaign does not execute those roles. The owner-facing `v2 optimize` command incorrectly copied each role's minimum instance count into its child team request. This produced no integration-verifier member and blocked the approved Optimization child before execution.

The CLI now explicitly requests one optimizer and one integration verifier for the Optimization child. The regression test sets both role minimums to zero, follows the actual approved CLI path with an injected one-call transport, and verifies that the command reaches the durable owner-approval allowance boundary without retrying or making a real provider call.

## Verification

Full offline Linux suite on the exact implementation revision:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 64.72 seconds. The skips are the unavailable pinned selector source commit and Windows ACL/reparse-point test. Focused CLI, Optimization runtime/lifecycle, and integrated acceptance suite: **42 passed**.

No subscription calls were made. The previously authorized Titanic benchmark remains 153/196 (78.06%), below the 95% target. CI for this implementation revision is pending publication.

## Remaining limitations

This fixes a blocker in the executable Optimization CLI path but does not close the entire v0.2 Full objective. Windows remains deferred, and PR #135 stays Draft and unmerged. The remaining feature and benchmark limitations are tracked in the previous [verification report](v02-full-integration-status-8b5cab5.md).
