# BananLoop v0.2 integration status — `14cb0a4`

**Tested code commit:** `14cb0a44a3db64eb522aab23d7dc52b9c669cf23`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This is a Draft integration checkpoint, not a v0.2 Full acceptance claim. See
[`v02-full-integration-results-14cb0a4.json`](v02-full-integration-results-14cb0a4.json)
for machine-readable evidence.

## Verification

- Full repository suite on this code: **928 passed, 0 failed, 0 errors, 2
  skipped** (930 collected). The selector preregistration source commit was
  unavailable and the Windows ACL/reparse-point contract is Windows-only.
- Reproduction after installing `.[test]`:
  `PYTHONPATH=tests/plugins python -m pytest -p isolate_home -q -ra --tb=no`
- Offline diagnostic:
  `python examples/v2_research_diagnostic/run_offline.py --work-dir /tmp/bananloop-v2-offline-acceptance-14cb0a4`
- Diagnostic status: `OFFLINE_V2_EXPLORATION_PASS`; two durable iterations,
  public memory in the second turn, no live model call. It completed on clean
  repository revision `23e01eecd35ef7e677e9b7627af314fcabf2d686`; runtime code
  was unchanged from tested code commit `14cb0a4`.
- No ChatGPT subscription request was made.

## Windows CI fixes

The previous Windows run failed because pytest fixture paths inherited a
temporary-directory ACL that the production verifier correctly rejected. The
workflow now creates an explicit runner/SYSTEM/Administrators-only pytest
temporary root. A repository-owned pytest plugin similarly isolates
`Path.home()` on Windows. The dispatch fingerprint test now exercises a worker
executable inside its frozen bundle instead of hardcoding `/usr/bin/python3`.
The new workflow run has not yet completed, so Windows validation remains
pending.

## Remaining Full acceptance gates

- Exact normalized matching does not detect semantic paraphrases, and Core
  does not independently rank hypotheses by evidence.
- Current-head CI, including the Windows job, must pass.
- Owner-approved one-call SIWC smoke remains unrun. No paid model request was
  made; transport cancellation and metering limits remain documented in prior
  status reports.

These gaps prevent a v0.2 Full completion claim.
