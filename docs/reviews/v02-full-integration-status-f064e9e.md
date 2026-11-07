# BananLoop v0.2 integration status — `f064e9e`

**Tested code commit:** `f064e9e81590ab55c94993d11f1d6328591b34ca`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains a Draft integration checkpoint, not v0.2 Full acceptance. The
machine-readable test and smoke output is in
[`v02-full-integration-results-f064e9e.json`](v02-full-integration-results-f064e9e.json).

## Added since the earlier report

`bananloop v2 explore` now creates or resumes a frozen v2 Full Exploration
campaign, persists its graph, campaign spec, queue and budgets, and never routes
through the legacy Engine. It reads only caller-selected exact files. It does
not change the selected legacy campaign. Auth is untouched by default; the
owner must pass `--approve-up-to-calls N` to make up to `N` SIWC requests in the
current command. The same campaign can be advanced in a later explicitly
authorized command.

Tests cover campaign creation and replay, spec mismatch, path rejection,
owner-call approval, the actual CLI entry point, two-turn mocked transport,
budget reservations, durable jobs and public memory in the resumed turn.

## Exact-head evidence

Focused runtime, evaluator, queue, role transport, research-ledger and CLI
tests on this SHA: **156 passed**.

The offline diagnostic also passed on this SHA with two durable turns, one
evidence artifact per turn, and public Research Memory in the second turn.
No live model call was made. Its result records
`complete_full_pipeline_proven: false`.

Repository-wide result on this SHA: **895 passed, 7 failed, 16 errors, 5
skipped** (923 collected). The 16 API setup errors and all 7 failures are
blocked by the host's loopback socket restriction. A representative test
fails at `ThreadingHTTPServer` with `PermissionError: [Errno 1] Operation not
permitted`; it fails before application request handling. The 5 skips are:
an unavailable pinned selector source commit, three socket-dependent benchmark
checks, and a Windows ACL contract skipped on Linux.

Trusted evaluator isolation is still not verified here. The system `bwrap`
binary cannot create the required network namespace
(`loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted`).
The Optimization integration test keeps evaluator orchestration, protocol
checks, receipts, candidate rejection/promotion and queue behavior real, but
uses a deterministic test runner for the isolated process result. No security
check was disabled.

## Remaining Full acceptance gates

- Exploration does not yet generate and rank multiple competing hypotheses
  per decision, perform convergence detection, or emit a final campaign report.
- There is no one-command full Exploration → Optimization → isolated evaluator
  → independent verification → final report E2E. Current tests cover those
  components in slices; the Optimization evaluator subprocess is the remaining
  host-blocked part.
- CI must complete on this PR head. API socket tests need loopback bind
  permission; trusted evaluator tests need user and network namespaces.
- Live SIWC researcher/optimizer/verifier calls remain unrun. The owner-gated
  command is documented; no ChatGPT plan request was made.

The code advances the requested runtime, but these open gates prevent a Full
completion claim.
