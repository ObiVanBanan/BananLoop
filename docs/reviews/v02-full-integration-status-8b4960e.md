# BananLoop v0.2 integration status — `8b4960e`

**Tested runtime and HIL code commit:** `8b4960e5187691d8ffd689b849da93d3a5729bef`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This checkpoint verifies Linux integration and the owner-authorized subscription transport. It does not claim v0.2 Full completion.

## Exact-SHA Linux verification

- Full suite at this runtime SHA: **936 passed, 2 skipped** (938 collected), exit code 0.
- Reproduction: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`.
- Skips: pinned selector commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` unavailable; Windows ACL/reparse-point contract on Linux.
- The run used host permissions needed for loopback and bubblewrap; security checks were not disabled.
- The preceding checkpoint records production-bubblewrap integrated E2E evidence at its exact SHA in [`v02-full-integration-status-84035b5.md`](v02-full-integration-status-84035b5.md). Current PR head `557fd283a77bc4bfb4db95f4b95ccfd7ee5bd15c` passed hosted Linux/Python 3.11, 3.12 and 3.13, Chromium, and mutation-policy: [Linux matrix](https://github.com/ObiVanBanan/BananLoop/actions/runs/42357011212) · [Chromium](https://github.com/ObiVanBanan/BananLoop/actions/runs/42357011201) · [mutation-policy](https://github.com/ObiVanBanan/BananLoop/actions/runs/42357011199).

## Provider receipt and failure persistence

Each dispatched Responses call now creates a `V2_PROVIDER_CALL` SQLite event with request ID, status, dispatch state and provider usage (or `UNKNOWN`). Completed response metadata is retained before JSON/schema validation, so malformed model output does not erase the provider receipt. Run reports are atomically written under the campaign reports directory without raw model response or exception text. Runtime failures return `RUN_FAILED`, and the CLI exits nonzero. Automatic retries remain disabled.

## Bounded live ChatGPT-plan run

- Existing Sign in with ChatGPT connection; pinned model `gpt-6-luna`; no separate API key.
- Exactly **2** provider calls, both completed. IDs: `resp_022e0654f33d12f6016aeee43e71a087d2868658f2d9cd1a1e` and `resp_0c71f7fe973983bd016aeee44b6a6c87d281c7d9e2c1c19c5e`.
- Usage: 1,386 and 1,539 tokens; **2,925 total**. Monetary spend is `UNKNOWN`.
- Input was a deliberately synthetic public README in `/tmp/bananloop-live-fixture`, with no project source or private data.
- Both Exploration turns were committed and the second turn completed. The durable campaign stopped as `CONVERGED_REPEATED_EXPERIMENT`; repeat detection prevented further redundant exploration. The live run therefore validates multi-turn model transport, memory handoff and durable call recording, but does not show a distinct second experiment executed by the live model.
- Durable result: `/tmp/bananloop-v2-live-synthetic-fixed-20261107/state/campaigns/c-34411c3a982f04c2da19425f00b66365/reports/v2-owner-run-a-2a9bdad612092b619dde5127.json`; matching SQLite campaign database contains two `V2_PROVIDER_CALL` events.

## Remaining acceptance gates

- Live mode supplied two Exploration turns but stopped on a repeated experiment; the full executable experiment/evaluator/independent-verification path is covered by offline integrated acceptance, not by this live campaign.
- RPDI-14 authoritative subscription usage/cost accounting remains unavailable; reported cost is `UNKNOWN`.
- Evidence-reference ranking does not establish scientific truth or semantic relevance; repeat matching uses a bounded controlled-variable synonym map.
- Windows investigation is deferred by the owner.

PR #135 remains Draft and unmerged. v0.2 Full is not declared complete.
