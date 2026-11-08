# BananLoop v0.2 integration status — `dbc9eae`

**Tested code commit:** `dbc9eae2445c8f3f355dbfd8691d4e98ab9c6ffb`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains a Draft integration checkpoint, not v0.2 Full acceptance. See
[`v02-full-integration-results-dbc9eae.json`](v02-full-integration-results-dbc9eae.json)
for machine-readable evidence.

## Added on this revision

The one-shot researcher response accepts a portfolio of 2–4 bounded
hypotheses and a selected candidate ID. Core validates uniqueness and selection,
uses the selected hypothesis as the next experiment, and persists the portfolio
as a content-addressed artifact. Legacy single-next-experiment results remain
accepted for already queued work. Provider instructions request the portfolio.

## Verification

- Focused runtime, CLI, exploration, queue, scheduler, trusted evaluator,
  optimization, verification and provider suites: **135 passed**.
- Repository suite: **895 passed, 7 failed, 16 errors, 5 skipped** (923
  collected). The 16 API setup errors and all 7 failures fail at denied
  loopback socket creation (`PermissionError: [Errno 1] Operation not
  permitted`) before application request handling.
- The 5 skips are an unavailable pinned selector source commit, three
  socket-dependent benchmark checks, and a Windows ACL contract skipped on
  Linux.
- No subscription model request was made.

## Remaining Full acceptance gates

- Core validates a model-selected candidate; it does not independently rank
  candidates or detect convergence.
- There is no one-command full Exploration → Optimization → isolated evaluator
  → Independent Verification → final report E2E.
- Real evaluator isolation remains unverified in this host: `bwrap` cannot
  create its required network namespace (`Failed to create NETLINK_ROUTE
  socket: Operation not permitted`).
- The full API suite requires a host permitting loopback bind; CI must complete
  against this PR head.
- A one-call SIWC smoke remains owner-confirmation gated and has not run.

These gaps prevent a v0.2 Full completion claim.
