# BananLoop v0.2 integration status — `ef428ae`

**Runtime code commit:** `ef428ae9aa6ebe5f472e7b54cb97df3c8d4e7d47`  
**Branch:** `codex/v02-full-integration`  
**Base main:** `6c89975870b0da8060146953faea966e203ba857`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is an integration checkpoint, not a v0.2 Full acceptance claim. Machine-readable test and live-smoke results are in [`v02-full-integration-results-ef428ae.json`](v02-full-integration-results-ef428ae.json). The two raw smoke outcome records are [`first response rejected by schema`](v02-live-smoke-first-invalid-ef428ae.json) and [`second response accepted`](v02-live-smoke-second-complete-ef428ae.json).

## Linux verification

- Full Linux suite on this exact code SHA: **934 passed, 0 failed, 0 errors, 2 skipped** (936 collected), exit code 0. Command: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`.
- Skips: unavailable pinned selector source commit and Windows ACL/reparse-point contract on Linux.
- The focused offline ChatGPT protocol suite also passed after the prompt correction. The combined offline Exploration → Optimization → trusted DEV evaluation → independent bubblewrap verification E2E remains covered by the full suite.
- Chromium and mutation-policy CI passed on this SHA. GitHub’s Linux matrix run was cancelled during overlapping push/PR events; a second run was queued when this report was prepared. The local full-suite run above is the exact-SHA Linux result.

## Live ChatGPT-plan smoke

Two one-call campaigns used the existing Sign in with ChatGPT provider and `gpt-6-luna`; no API key or separate OpenAI API was used.

1. The first provider response completed, but the runtime rejected it because all three hypothesis IDs began with uppercase `H`, outside the schema’s lowercase ID pattern. The request and error were persisted; the durable worker job is `UNKNOWN`, so it was not replayed. Usage reported by the provider: 580 input + 1,110 output = **1,690 tokens**.
2. The researcher prompt was updated to state the exact lowercase ID pattern and regression-tested. The second response passed validation; its campaign node completed and wrote the public hypothesis portfolio artifact `c541e0447e38c02afcf841f75b33cf74bf136e7acf0a124849378b1011d7f9d9`. Provider usage: 628 input + 1,018 output = **1,646 tokens**.

The result files are under `/tmp/bananloop-v2-live-one-call-9c5e603/` and `/tmp/bananloop-v2-live-one-call-2-9c5e603/` in campaign SQLite plus `live-smoke-result.json`; sanitized-to-secrets outcome records are included alongside this report. Total observed usage was 1,208 input and 2,128 output tokens (**3,336 total**). Monetary spend was not reported by the transport and is `UNKNOWN`. No automatic retry was made. This smoke proves one real model response can pass through v2 Exploration; it does **not** prove the complete multi-iteration pipeline or RPDI-14 accounting.

## Fixed issue and transport limits

The researcher instruction now spells out the schema’s exact candidate ID pattern (`^[a-z0-9][a-z0-9_-]{0,79}$`), with an offline regression test. This fixed the schema rejection observed in the first live call.

The client enforces a 120-second local timeout but cannot guarantee server-side cancellation after dispatch. ChatGPT-plan Responses rejects a hard output-token cap; the role allowance is therefore not a provider-enforced limit. Usage is recorded from provider receipts when present and otherwise `UNKNOWN`.

## Remaining Full acceptance gates

- Candidate ranking counts trusted evidence references for the exact target claim; it does not independently validate scientific truth or semantic relevance.
- Repeat detection uses a bounded controlled-variable synonym map, not general semantic inference.
- Live smoke was intentionally limited to two calls; it is a transport/one-turn Exploration check, not a live full-campaign E2E.
- Windows CI is deferred for later investigation at the user's direction.

These limitations prevent a v0.2 Full completion claim.
