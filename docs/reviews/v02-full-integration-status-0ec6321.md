# BananLoop v0.2 integration status — `0ec6321`

**Tested runtime code SHA:** `0ec6321120794358fa783459352e88c72aeea220`  
**Branch:** `codex/v02-full-integration`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is an updated Linux acceptance checkpoint, not a v0.2 Full completion claim. Windows investigation remains deferred.

## Linux verification

- Full suite at this code SHA: **940 passed, 2 skipped** (942 collected), exit code 0.
- Command: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`.
- Skips: pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` unavailable; Windows ACL/reparse contract on Linux.
- The run used host permissions for loopback and bubblewrap. No security checks were disabled.
- CI for this new code SHA is pending. Prior head passed Linux/Python 3.11–3.13, Chromium and mutation-policy; new CI links will be recorded after this commit completes.

## Research Memory enforcement

A live two-turn run exposed that a worker could complete without emitting a Ledger finding, leaving the next turn with an empty `dev_summary` and no evidence references. A subsequent run emitted an uncertain finding and delivered its exact evidence ID to the next worker, but that worker did not cite it; Core still accepted its initial, ungrounded selection.

The runtime now rejects ordinary replies without at least one evidence-linked finding. When prior Research Memory is available, it also requires a later reply to return a hypothesis portfolio and at least one candidate tied to the matching claim and exact evidence ID. Core's existing deterministic ranking then selects among cited candidates. A forgetful researcher can no longer complete successfully while silently dropping available memory. Strategy-change requests still use the separate owner-review path.

Offline regressions cover missing findings, omitted memory citations, persistence and recovery. The offline diagnostic mock now cites the previous finding on its second turn.

## Bounded live ChatGPT run

- Existing Sign in with ChatGPT connection; `gpt-6-luna`; synthetic public README only; no project source or private data.
- Exactly two provider calls; both completed with no automatic retry.
- Request IDs: `resp_03cc64d716a82c38016aeee9e78cd487d290b1c45404de1ad2` and `resp_0d48e83dfcddb021016aeee9f97ac087d29ef83da1ae9efad1`.
- Usage: 1,895 + 2,269 = **4,164 tokens**. Monetary cost is `UNKNOWN`.
- The first turn stored finding `synthetic_readme_scope` with evidence ID `f90e49ce7a025ebb86d3419f1df2e740f4f7bf0fae0cfd888b530bd2994ec443`. The second turn received that ID and statement in `dev_summary`, cited it on a candidate, and Core selected that candidate using `CORE_PUBLIC_DEV_EVIDENCE_COVERAGE`.
- Both Exploration nodes committed; campaign stopped at `ITERATION_LIMIT` after the approved two calls. The fixture has no executable test definitions, so no experiment was claimed or run. The full executable Exploration→Optimization→evaluator/independent verification route remains covered by offline integrated acceptance.
- Durable report: `/tmp/bananloop-v2-live-memory-guard-20261107/state/campaigns/c-7f977d8bed30e14d2b5b061a723b296a/reports/v2-owner-run-a-00f7f6671225498b354609b8.json`. The campaign SQLite contains both provider-call events and both completed jobs.

## Reproduction commands

Full Linux suite:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q -ra --tb=no
```

Integrated offline Exploration → Optimization → production evaluator and independent verification:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q tests/test_rpdi13_integrated_acceptance.py
```

## Remaining acceptance limits

- The live model proposed experiments but the synthetic fixture had no experiment implementation; live execution and evaluation are not claimed.
- RPDI-14 authoritative usage and monetary cost accounting are unavailable; cost remains `UNKNOWN`.
- Evidence-reference ranking does not establish scientific truth or semantic relevance. Repeat detection remains a bounded controlled-variable matcher.
- Windows checks are not investigated at the owner's request.

PR #135 remains Draft and unmerged. v0.2 Full is not declared complete.
