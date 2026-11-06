# BananLoop v0.2 integration status — `1d2d26a`

**Tested code commit:** `1d2d26a4d25bc2159997254263f4b13cf45ef125`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains a Draft integration checkpoint, not v0.2 Full acceptance. See
[`v02-full-integration-results-1d2d26a.json`](v02-full-integration-results-1d2d26a.json)
for machine-readable evidence.

## Added since the previous checkpoint

Research knowledge export now passes its trusted graph and durable queue to
ResearchLedger so each persisted finding is revalidated against completed
worker evidence. The graph handle is read-only and does not bind or modify
campaign state. The API test now verifies that fabricated self-hashing rows are
rejected; the real Exploration-to-API acceptance path succeeds.

## Verification on this exact code SHA

- Repository suite: **921 passed, 0 failed, 0 errors, 2 skipped** (923
  collected). The skips are an unavailable pinned selector source commit and
  a Windows ACL/reparse-point contract skipped on Linux. Run with loopback
  socket access enabled.
- The earlier isolated loopback API failures are fixed: trusted knowledge
  export and the Exploration research handoff test both pass.
- The prior PR head's `tests` workflow failed on those research API requests;
  its `dashboard-browser` workflow also failed in its live integrated
  acceptance step. Recheck workflows on the current PR head after this update.
- No ChatGPT subscription request was made.

## Remaining Full acceptance gates

- Core validates a model-selected candidate; it does not independently rank
  alternatives or detect convergence.
- There is no one-command full Exploration → Optimization → isolated evaluator
  → Independent Verification → final report E2E.
- Real evaluator isolation remains unverified in this host: `bwrap` cannot
  create its required network namespace (`Failed to create NETLINK_ROUTE
  socket: Operation not permitted`).
- Current-head CI must complete.
- A one-call SIWC smoke remains owner-confirmation gated and has not run.

These gaps prevent a v0.2 Full completion claim.
