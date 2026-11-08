# BananLoop v0.2 integration status — `9f683cd`

**Runtime and HIL code commit:** `9f683cd7da5b016612bcb59b42fcc0e7d5a2ab6b`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is an acceptance checkpoint, not a v0.2 Full completion claim. Prior authorized live smoke results remain documented in [`v02-full-integration-status-ef428ae.md`](v02-full-integration-status-ef428ae.md) and [`v02-full-integration-results-ef428ae.json`](v02-full-integration-results-ef428ae.json). No additional paid model call was made for this checkpoint.

## Linux verification

- The PR CI suite passed on the commit: Python 3.11 **932 passed, 5 skipped, 7 subtests passed**; Python 3.12 and 3.13 also passed. The skip set includes host-dependent bubblewrap and loopback cases, unavailable pinned selector source, and a Windows ACL contract.
- Chromium and mutation-policy checks passed.
- Actions: [Linux/Python matrix, run 42354428626](https://github.com/ObiVanBanan/BananLoop/actions/runs/42354428626), [Chromium, run 42354428523](https://github.com/ObiVanBanan/BananLoop/actions/runs/42354428523), [mutation-policy, run 42354428590](https://github.com/ObiVanBanan/BananLoop/actions/runs/42354428590).
- Local focused runtime/storage verification passed across 71 tests in `test_v2_research_runtime.py`, `test_exploration_adapter.py`, `test_research.py`, and `test_graph_store.py`.
- The local full-suite attempt could not be used as acceptance evidence because this managed host denies loopback socket binding and bubblewrap namespace access. The PR Linux runners completed the full suite.

## Human-in-the-loop strategy change

The researcher may return a bounded `strategy_change_request` containing the current and proposed strategy, rationale, expected effect, and changed scope. Core computes a deterministic request ID, writes the request through ToolGateway as a content-addressed artifact, persists it in graph completion evidence, pauses the durable campaign, exposes it in the campaign report, and suppresses automatic follow-up creation. On restart, the pending request is detected and the model is not called again. The new offline regression covers durable artifact integrity, pause state, report visibility, no child node, and no replay after SQLite reopen.

This is a request-only checkpoint: the proposal does not authorize a pivot or experiment. Owner-approved pivot execution continues through the existing controlled pivot API; this report makes no claim that the proposal itself executes an approved pivot.

## Previously authorized live smoke

Exactly two calls were made to `gpt-6-luna` through the existing Sign in with ChatGPT provider, with no separate API key. The first response exposed an uppercase hypothesis-ID contract mismatch and was not replayed; the second completed successfully after the prompt fix. Combined usage was 3,336 tokens, and monetary spend remains `UNKNOWN`. See the prior exact records linked above. This validates the one-turn provider path, not a live multi-iteration campaign.

## Remaining Full acceptance gates

- Candidate ranking uses trusted evidence references for the exact target claim; it does not independently validate scientific truth or semantic relevance.
- Repeat detection uses a bounded controlled-variable synonym map rather than general semantic inference.
- Live smoke covered two one-turn requests, not full live multi-iteration completion or RPDI-14 accounting.
- Windows is left for later investigation at the user's direction.

These limits mean v0.2 Full is not declared complete. The PR remains Draft and unmerged.
