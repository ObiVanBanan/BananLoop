# BananLoop v0.2 integration status — `0791490`

**Runtime code commit:** `07914900afc7d621c358c97e796eab05d2231c71`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This is an integration checkpoint, not a v0.2 Full acceptance claim. The
machine-readable evidence is in
[`v02-full-integration-results-0791490.json`](v02-full-integration-results-0791490.json).

## Verification

- Full suite on the runtime code: **930 passed, 0 failed, 0 errors, 2 skipped**
  (932 collected), with loopback and Linux namespace access. Skips: unavailable
  pinned selector source commit and Windows-only ACL/reparse-point contract.
- Command after installing `.[test]`:
  `PYTHONPATH=tests/plugins python -m pytest -p isolate_home -q -ra --tb=no`
- Offline diagnostic on clean runtime commit `07914900afc7d621c358c97e796eab05d2231c71`:
  `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, public memory received
  in turn two, no live model call.
- Regression tests show conservative local controlled-variable matching stops
  a repeat expressed as “vary the input size” → “change the number of
  observations,” while keeping opposite directions and distinct variables
  separate. This is deterministic synonym matching, not a general semantic
  language model.
- No paid model request was made.

## Windows test isolation

The Windows job at prior PR head `e3e593b0f8f56c2aea8f2419965b1bc72ed82c11`
still failed (64 failed, 857 passed, 9 skipped). Its ACL preparation step
returned success, but the protected-path verifier rejected the pytest root.
Commit `b8e9de82e444fb292c1b4349d63a2da62e00b397` replaces the `icacls` edits
with a protected DACL rebuilt from an empty rule set, asserts the exact three
trusted entries, and places the isolated home beneath that root. This repair
has not yet been exercised by the next Windows CI run.

## Remaining Full acceptance gates

- Core does not yet independently rank hypothesis candidates by evidence.
- The Windows ACL repair must pass CI.
- Owner-approved one-call SIWC smoke remains unrun. The prepared command is in
  the PR body; transport cancellation and usage-metering limits remain as
  described in prior status reports.

These gaps prevent a v0.2 Full completion claim.
