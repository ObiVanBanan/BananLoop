# BananLoop v0.2 integration status — `4cc5ba3`

**Tested code commit:** `4cc5ba3a36d44a0f844012caf0e41df4ea1d0a23`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains a Draft integration checkpoint, not v0.2 Full acceptance. See
[`v02-full-integration-results-4cc5ba3.json`](v02-full-integration-results-4cc5ba3.json)
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
- At terminal Optimization, a consolidated parent report joins the child
  result with the parent Exploration report, or obtains a bounded Exploration
  summary from the persisted parent graph using a read-only SQLite connection.
  The cross-layer E2E asserts the parent and child identities and PASS evidence.
- Evaluator `RLIMIT_NPROC` leaves a fixed 128-process budget above the observed
  host-user baseline, allowing bubblewrap to start while retaining a process
  ceiling. No local evaluator fallback was added.
- Real bubblewrap Optimization tests execute candidates, score DEV and
  VALIDATION tiers, reject a regression, independently verify a winner, promote
  it, resume after restart, and recover persisted failure memory.
- A combined real-bwrap E2E covers Exploration evidence → approved frozen
  Optimization child → baseline and DEV evaluation → independent verification
  → API, CLI, and consolidated report. The offline Exploration diagnostic
  covers two selected hypotheses and public memory in the next iteration.

## Exact-SHA verification

- Repository suite: **926 passed, 0 failed, 0 errors, 2 skipped** (928
  collected), with loopback and namespace access enabled. The skips are an
  unavailable pinned selector source commit and a Windows ACL/reparse-point
  contract skipped on Linux.
- Offline command:
  `python examples/v2_research_diagnostic/run_offline.py --work-dir /tmp/bananloop-v2-offline-acceptance-4cc5ba3`
- Offline result: `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, two
  evidence records, selected 2-candidate portfolios, public memory in turn two,
  clean worktree at the tested code SHA, no live model request. Campaign report
  is under the run's state directory.
- Real isolated test commands:
  `pytest -q 'tests/test_evaluator_verification.py::test_optimization_lifecycle_integrates_durable_evaluator_and_verification_queues[real-bwrap]'`
  and
  `pytest -q 'tests/test_rpdi13_integrated_acceptance.py::test_exploration_research_handoff_child_agrees_through_api_and_cli[real-bwrap]'`.
- No ChatGPT subscription request was made.

## Remaining Full acceptance gates

- Candidate ranking is still delegated to the researcher’s selected ID; Core
  validates and binds the action but does not independently rank by evidence or
  detect semantic convergence/plateau.
- Current-head CI must complete after this report is pushed.
- The one-call SIWC smoke is prepared but remains gated on owner confirmation.

These gaps prevent a v0.2 Full completion claim.
