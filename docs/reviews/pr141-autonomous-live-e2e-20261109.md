# PR #141 autonomous live Exploration → Optimization E2E — 2026-11-09

## Result

The live campaign completed the requested role sequence without a prewritten parent protocol or fixture verifier answers:

`Researcher → Protocol Verifier → owner approval → Optimizer → Integration Verifier → DEV / VALIDATION / HOLDOUT → Final Report`.

Campaign `c-5ba8c2fc90ff620b19c25cb2964c7ffb` began with `evaluation=None`. The live Researcher proposed a protocol intent; Core bound the frozen evaluator protocol from that response. A separate live GPT-6 Luna Protocol Verifier approved it. The owner then approved the Optimization pivot by the exact proposal digest before the child was created. The child was `c-e9d1a7e61554325b87810130adf53357`.

The Optimizer changed only `src/optimizer/model/ranker.py` (threshold 2 → 1). The live Integration Verifier returned PASS. Trusted evaluation completed all tiers. VALIDATION improved from 0.5 to 1.0 (delta +0.5); HOLDOUT was 1.0 over 2 cases and 3 repeatability runs. The final JSON and Markdown report included the explicit baseline/candidate/delta table.

This is an end-to-end smoke on a small synthetic evaluator, not a general benchmark result.

## Live provider receipts

All four calls used the existing ChatGPT subscription with GPT-6 Luna; no automatic retry was made. Monetary cost is UNKNOWN.

| Role | Provider request ID | Tokens |
| --- | --- | ---: |
| Researcher | `resp_0ed4dadf251d0872016af1e313b0a087d2b5c640ca5e1c7ff3` | 2,889 |
| Protocol Verifier | `resp_0e4866da82715b45016af1e332794087d2af3e3f9c713714e1` | 3,106 |
| Optimizer | `resp_0f943c8b029050c3016af1f197260887d29a7d3bf994b369ae` | 660 |
| Integration Verifier | `resp_0476e9328d226712016af1f19e3b3487d2b018ebd97de93251` | 907 |

The protocol approval digest was `b2ac3c6acf1105a765dfaed3293062a5ebb2b4a04d951961051ee763f8e68bb9`. The approved pivot digest was `1f41577b3a546bfb30186d5f6a71e2a6d39063b1fe3a2ca965de8d7e0bc858d9`. The baseline snapshot was `32158428189e00b3137bd5f136855b3cb5dc67a4`; the candidate snapshot was `49f29a2dbcac240e5f6d11bb4bd66787a73c563d`.

## Budget accounting finding and follow-up

The child ledger recorded 2/2 agent calls and 12/12 evaluator jobs. The optimizer's 660-token reservation was reconciled from its durable provider receipt, clearing the prior UNKNOWN amount. The original run did not reserve model tokens for the Integration Verifier, so its 907 reported tokens were absent from the child model-token spend (ledger showed 660/16,384, UNKNOWN 0). The live role sequence and results passed, but the original run's model-token ledger undercounted total provider usage by 907 tokens.

The code now meters model-token usage for Researcher, Protocol Verifier, Optimizer, and Integration Verifier jobs; it also supports receipt-backed reconciliation of UNKNOWN usage on a completed job. A completed Optimization campaign now records `GOAL_MET` and closes its child allocation only after all work is settled. Offline regression and full-suite evidence are recorded separately. This metering correction has not yet been revalidated by a new live campaign.

## Verification

- On the exact existing PR head `88715deeecdb69f42893c86d23e3c62b4e546c16`, GitHub Actions `tests`, `role-benchmark-offline` (Windows included), and `dashboard-browser` were green.
- After the metering and lifecycle changes, the clean PR-head worktree passed **1,037 tests, 2 skipped, 7 subtests** with live flags disabled.
- The targeted API/integrated acceptance passed both test-runner and real-bwrap variants. It verifies the final report, terminal child state, and closed allocation.
- The PR remains Draft. The post-fix code needs CI on its new commit; a new live metering check still requires additional owner-approved calls.
