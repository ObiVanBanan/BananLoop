# v0.2 Full integration follow-up — 2026-11-08

Implementation commit: `c7887ec761bd472a6de25fb080fa906bf886f696`.

## Changes

- Owner Exploration now reserves a frozen four-role budget for researcher, independent protocol verifier, optimizer, and integration verifier, and persists the exact role registry for child execution.
- Completed durable protocol drafts are handed to the pinned independent verifier through the durable queue. Approved receipts are recovered after restarts and converted into a supervised Optimization pivot proposal.
- Optimization write scope is explicit and constrained to paths also readable by the child. Parent and child call/token budgets are checked against the frozen role vector.
- Pivot review remains human controlled. The parent pauses, records a durable proposal, and reports the owner decision/execute handoff; API/CLI pivot commands can use a custom state directory consistently.
- Added integrated acceptance coverage for Exploration → durable protocol review → owner-approved Optimization child → trusted evaluation, with API and CLI read-model agreement.

## Verification

Full offline Linux suite on the exact implementation commit:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 67.57 seconds. The skips are an unavailable pinned selector source commit and the Windows ACL/reparse-point contract.

Focused Exploration, Optimization, verifier, ChatGPT transport-boundary, role, and integrated acceptance tests passed: **82 passed, 1 skipped**. The two integrated acceptance modes (`test-runner` and `real-bwrap`) passed. No live model calls were made for this implementation commit.

`git diff --check` passed before commit. CI for this pushed revision is pending its GitHub check run at report creation time.

## Retained live benchmark evidence

The separately authorized Titanic run remains **153/196 = 78.06%** on trusted HOLDOUT, below the aspirational 95% target. It was not retried. Provider-reported tokens and monetary cost are recorded in the prior benchmark evidence; unavailable cost remains `UNKNOWN`.

## Remaining limitations

- The Titanic result does not meet the 95% target; no additional live calls were made.
- Windows remains deferred and its ACL/reparse-point contract was skipped on Linux.
- Exact monetary cost/cancellation guarantees depend on the ChatGPT transport and are not claimed when unavailable.
- This integration slice does not prove every v0.2 Full acceptance criterion (including all recovery/failure scenarios); the goal remains active. PR #135 stays Draft and unmerged.
