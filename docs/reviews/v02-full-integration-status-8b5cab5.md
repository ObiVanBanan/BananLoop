# v0.2 Full integration verification — 2026-11-08

Verified source revision: `8b5cab50bbd3a917ed7fe99e221c59a5dc782d5b`.

## Full offline suite

Command executed on this exact revision:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 64.34 seconds. One skip is the unavailable pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd`; the other is the Windows ACL/reparse-point contract, deferred with Windows.

## User-facing CLI smoke, zero model calls

Ran `bananloop.cli v2 explore` twice against the same temporary state directory, request ID, goal, repository HEAD, and paths. Both invocations returned `READY_FOR_OWNER_APPROVAL`, `live_model_called: false`, campaign ID `c-cfed02142d013322e83d0dc37ffee103`, and spec hash `ae435df1dde255318c35176eb7b20abcc9d3e2623bd104f59c927e3a24957375`. This verifies actual CLI campaign creation and idempotent resume without using the subscription.

The runtime and integrated acceptance details are in the prior [implementation report](v02-full-integration-status-c7887ec.md) and its [machine-readable results](v02-full-integration-results-c7887ec.json).

## CI and remaining limits

At report creation, checks for PR #135 on this revision were queued or in progress: Linux Python 3.11–3.13, Windows Python 3.12, Chromium, and mutation-policy. CI is not claimed as passed here.

The previously authorized Titanic HOLDOUT remains 153/196 (78.06%), below the aspirational 95% target. No live model calls were made on this revision. The v0.2 Full goal remains active; Windows and remaining recovery/failure acceptance gaps are not claimed complete.
