# BananLoop v0.2 integration status — `84035b5`

**Tested runtime and HIL code commit:** `84035b58e8f83a29696c9102291a29f4ac5b5cde`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is a verified Linux acceptance checkpoint, not a v0.2 Full completion claim. The prior two-call authorized ChatGPT-plan smoke is recorded in [`v02-full-integration-status-ef428ae.md`](v02-full-integration-status-ef428ae.md); no additional subscription call was made here.

## Exact-SHA Linux verification

- Full suite at this exact code SHA: **935 passed, 2 skipped** (937 collected), exit code 0.
- Reproduction: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`.
- The two skips were the unavailable pinned selector source commit and the Windows ACL/reparse-point contract on Linux.
- The run used the approved host permissions required by loopback and bubblewrap. No sandbox or security checks were disabled.
- Hosted CI on this exact PR head passed Python 3.11, 3.12, 3.13, Chromium, and mutation-policy: [Linux/Python matrix](https://github.com/ObiVanBanan/BananLoop/actions/runs/42355436256), [Chromium](https://github.com/ObiVanBanan/BananLoop/actions/runs/42355436238), [mutation-policy](https://github.com/ObiVanBanan/BananLoop/actions/runs/42355436235).
- The production-isolation cross-layer test also passed directly: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q tests/test_rpdi13_integrated_acceptance.py -k real-bwrap` → **1 passed**.
- HIL/runtime-focused tests passed, including the strategy-request test that closes and reopens both worker-queue and graph SQLite databases before asserting the request survives and the model is not called again.

## End-to-end path exercised

The integrated acceptance test covers two durable Exploration turns with public Research Ledger memory passed into the second researcher context; then an approved protocol is handed to a separate Optimization child, candidate code is changed and committed, the frozen evaluator is run in bubblewrap, and an independent verifier and champion-selection logic process the trusted receipts. API/CLI read models agree with persisted state. The exact-SHA suite passed both the test-runner and production-bubblewrap parameterizations.

The Exploration model is still not given arbitrary shell access: the research role can read only its exact allowlist and write artifacts through ToolGateway. Executable candidate experiments occur in the separate Optimization/evaluator lane under the approved capability and verification boundary.

## Human-in-the-loop strategy change

A researcher may return a bounded strategy-change proposal. Core computes a deterministic ID, writes the proposal through ToolGateway as a content-addressed artifact, stores it in graph completion evidence, pauses the campaign and includes it in the report. It does not create a follow-up experiment. The regression reopens both SQLite stores, confirms the same durable request remains visible, and confirms no model replay or child node occurs. The proposal itself does not authorize a pivot; approved pivot execution stays behind the supervised pivot API.

## Previously authorized live smoke

Exactly two `gpt-6-luna` calls used the existing Sign in with ChatGPT connection, no standalone API key, and no automatic retries. The first completed at the provider but failed runtime validation on uppercase hypothesis IDs and was not replayed. The second passed validation and persisted its portfolio. Usage was 3,336 total tokens; spend remains `UNKNOWN`. The raw records and per-call details are in the earlier report linked above. These were one-turn transport smokes, not a live multi-iteration campaign.

## Remaining Full acceptance gates

- The limited live smoke did not exercise a complete live multi-iteration campaign or prove RPDI-14 usage accounting.
- Evidence ranking counts trusted references attached to an exact claim; it does not establish scientific truth or semantic relevance.
- Repeat detection uses a bounded controlled-variable synonym map rather than general semantic inference.
- Windows investigation is deferred for later at the user's direction.

The PR remains Draft and unmerged; v0.2 Full is not declared complete.
