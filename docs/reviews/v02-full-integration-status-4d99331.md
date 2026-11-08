# BananLoop v0.2 integration status — `4d99331`

**Runtime code commit:** `4d99331a5ff7563a928ddcb94c4250a1052343d8`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`

This is an integration checkpoint, not a v0.2 Full acceptance claim. Machine-
readable results are in [`v02-full-integration-results-4d99331.json`](v02-full-integration-results-4d99331.json).

## Verification

- Full local Linux suite on this exact commit: **933 passed, 0 failed, 0 errors,
  2 skipped** (935 collected), with loopback and Linux namespace access.
- Reproduction: `PYTHONPATH=tests/plugins:src:. python -m pytest -p isolate_home -q -ra --tb=no`
- Linux skips: unavailable pinned selector source commit and the Windows-only
  ACL/reparse-point contract.
- Windows CI on this exact commit: **923 passed, 0 failed, 12 skipped** and
  7 subtests passed. Three socket-dependent test cases skip because this Windows
  Python lacks `AF_UNIX`; the broker reports this limitation without fallback.
- Offline Exploration diagnostic ran from a clean worktree:
  `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, public research memory
  in turn two, no live model call. It correctly records
  `complete_full_pipeline_proven: false`.
- Actions tests, mutation-policy smoke, and Chromium workflow all pass on this
  exact head. No paid model request was made.

## Windows fixes

Protected evidence creation now uses exclusive file creation beneath a
Windows ACL- and reparse-verified root, while POSIX keeps descriptor-relative
no-follow creation. SQLite privacy checks use trusted ACL verification on
Windows and retain mode `0600` enforcement on POSIX. The one-shot provider
broker fails closed if `AF_UNIX` is unavailable; it never substitutes a less
private transport.

## Remaining Full acceptance gates

- The offline diagnostic proves Exploration only; the combined Exploration →
  Optimization → isolated DEV evaluation → independent verification → final
  report path has not passed as one offline E2E.
- Core candidate ranking counts trusted evidence references for the exact
  target claim; it does not independently validate scientific truth or
  semantic relevance.
- Semantic repeat detection uses a bounded controlled-variable synonym map,
  not general semantic inference.
- Owner-approved one-call SIWC smoke remains unrun. No subscription call was
  made; authorization must be separate.

These gates prevent a v0.2 Full completion claim.
