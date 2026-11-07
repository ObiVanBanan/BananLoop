# BananLoop v0.2 integration status — `16b2ddf`

**Runtime code commit:** `16b2ddfbad1475181b2caed3622ba5c54131cc16`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is an integration checkpoint, not a v0.2 Full acceptance claim. Machine-readable results are in [`v02-full-integration-results-16b2ddf.json`](v02-full-integration-results-16b2ddf.json).

## Verification

- Full Linux suite on the exact runtime tree: **933 passed, 0 failed, 0 errors, 2 skipped** (935 collected). Reproduction: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`. The skipped cases are the unavailable pinned selector source commit and Windows ACL/reparse contract on Linux.
- Integrated offline E2E now exercises two real `V2ResearchRuntime.run_campaign()` turns with durable evidence and second-turn memory, frozen evaluator-protocol approval, an Optimization candidate commit, DEV evaluation, separate independent verification, champion promotion and the final combined report. The model and optimizer are deterministic test adapters; evaluator execution and campaign/runtime wiring use production v2 components.
- The integrated E2E passed using both the deterministic test evaluator (`-k test-runner`) and the production bubblewrap evaluator (`-k real-bwrap`). Reproduce with `PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q tests/test_rpdi13_integrated_acceptance.py` (both evaluator modes).
- Separate clean-worktree offline Exploration diagnostic: `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, public memory on turn two, no live model call. That standalone diagnostic reports `complete_full_pipeline_proven: false`; the integrated pytest above supplies the combined path proof.
- CI on this exact SHA: Ubuntu 3.12 and 3.13, Chromium, and mutation-policy workflow passed. Ubuntu 3.11 and Windows 3.12 were still running when this report was prepared; results will be recorded after completion.
- No subscription model call was made.

## Windows portability

Protected evidence creation uses exclusive file creation beneath a Windows ACL- and reparse-verified root, while POSIX retains descriptor-relative no-follow creation. SQLite privacy checks use trusted Windows ACL verification and retain mode `0600` enforcement on POSIX. The one-shot provider broker fails closed when `AF_UNIX` is unavailable; it does not use a weaker fallback.

## Remaining Full acceptance gates

- Pause/resume and recovery after injected failures have not been established as a complete end-to-end acceptance path in this checkpoint.
- Core candidate ranking counts trusted evidence references for the exact target claim; it does not independently validate scientific truth or semantic relevance.
- Semantic repeat detection uses a bounded controlled-variable synonym map, not general semantic inference.
- Owner-approved one-call SIWC live smoke remains unrun. It requires separate confirmation; no subscription call was made.

These open gates prevent a v0.2 Full completion claim.
