# BananLoop v0.2 integration status — `0aae2fd`

**Tested code commit:** `0aae2fd704d6677347e392c97d166339a528ab4a`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains a Draft integration checkpoint, not v0.2 Full acceptance. See
[`v02-full-integration-results-0aae2fd.json`](v02-full-integration-results-0aae2fd.json)
for machine-readable evidence.

## Implemented and verified

- Researcher hypotheses now have bounded, typed fields and unique candidate IDs.
  Core requires the selected ID to exist and derives the next action from that
  candidate; contradictory actions fail closed. Full portfolio bytes are
  persisted as content-addressed research evidence.
- Exploration detects an exact normalized repeat of the current experiment,
  stops scheduling follow-ups, and writes an atomic report with evidence
  references and limitations. It states that unverified PUBLIC_DEV evidence is
  not a scientific conclusion.
- Optimization writes an atomic per-campaign report containing public DEV and
  VALIDATION receipt digests, independent integration evidence and decision,
  champion outcome, graph node outcomes, and parent lineage. Non-public
  evaluator tiers are excluded.
- Terminal Optimization writes a consolidated parent report joining the child
  result with the parent Exploration report, or obtains a bounded Exploration
  summary from the persisted parent graph using a read-only SQLite connection.
  The cross-layer E2E asserts both campaign identities and PASS evidence.
- The owner-gated one-call SIWC smoke persists both success and post-dispatch
  failure atomically, including status, request ID when available, error type,
  and usage (`UNKNOWN` when omitted). It issues no automatic retry. No call was
  made during this work.
- Evaluator `RLIMIT_NPROC` leaves a fixed 128-process budget above the observed
  host-user baseline, allowing bubblewrap to start while retaining a process
  ceiling. No local evaluator fallback was added.
- Real bubblewrap Optimization tests execute candidates, score DEV and
  VALIDATION tiers, reject a regression, independently verify a winner, promote
  it, resume after restart, and recover persisted failure memory.
- The combined real-bwrap E2E covers Exploration evidence → approved frozen
  Optimization child → baseline and DEV evaluation → independent verification
  → API, CLI, and consolidated report. Offline Exploration covers two selected
  hypotheses and public memory in the next iteration.

## Exact-SHA verification

- Repository suite: **927 passed, 0 failed, 0 errors, 2 skipped** (929
  collected), with loopback and namespace access enabled. Skips: unavailable
  pinned selector source commit and Windows ACL/reparse-point contract on Linux.
- Offline command:
  `python examples/v2_research_diagnostic/run_offline.py --work-dir /tmp/bananloop-v2-offline-acceptance-0aae2fd`
- Offline result: `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, evidence
  counts `[1, 1]`, public memory in turn two, clean worktree at tested code SHA,
  no live model request. The campaign report is under the run state directory.
- Live command prepared but not run pending owner confirmation:
  `python examples/v2_research_diagnostic/run_one_chatgpt.py --work-dir /tmp/bananloop-v2-live-one-call --model gpt-6-luna --approve-one-call`
- Transport limit: the client has a local 120-second timeout and closes its
  HTTPS connection, but the provider API offers no explicit server-side cancel
  guarantee here. A timeout after dispatch is therefore an indeterminate
  request; the smoke records the error and does not retry. The provider also
  rejects a hard output-token cap; missing usage remains `UNKNOWN`.
- No ChatGPT subscription request was made.

## Remaining Full acceptance gates

- Candidate ranking is still delegated to the researcher's selected ID; Core
  validates and binds the action but does not independently rank by evidence or
  detect semantic convergence/plateau.
- Current-head CI must complete after this commit is pushed.
- Owner-approved one-call SIWC smoke remains unrun.

These gaps prevent a v0.2 Full completion claim.
