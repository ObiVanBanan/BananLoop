# BananLoop v0.2 Linux acceptance checkpoint — `c288f8b`

**Tested runtime commit:** `c288f8bd205f3248aaacb4ee1aab7c0c6bc33641`  
**Tree:** `7bd8b1a9e8a04baec51c7bf31a387d9eeb3fc108`  
**Branch:** `codex/v02-full-integration`  
**Draft PR:** [#135](https://github.com/ObiVanBanan/BananLoop/pull/135)

This is a Linux acceptance checkpoint, not a v0.2 Full completion claim. Windows investigation remains deferred.

## Linux verification

- Full offline suite: **914 collected, 912 passed, 2 skipped, 0 failed**, exit code 0.
- Command: `PYTHONPATH=tests/plugins:tests:src:. /tmp/bananloop-check/bin/python -m pytest -p isolate_home -q -ra --tb=no`.
- Skips: pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` unavailable; Windows ACL/reparse-point contract on Linux.
- The run used host permissions for loopback and real bubblewrap evaluator isolation. No checks were disabled.
- Focused evaluator, optimization lifecycle, and RPDI-13 acceptance tests passed on both test-runner and real-bubblewrap paths.
- `compileall` and `git diff --check` passed.

## Final HOLDOUT execution

Optimization now runs the frozen trusted HOLDOUT tier only after the scheduler reports `ALL_NODES_COMPLETE`. It evaluates the durable final champion, records the evaluator receipt, and exposes only the protocol's aggregate metric in the owner report. Case IDs, per-case measurements, evaluator output, diagnostics, and artifacts remain private. HOLDOUT is excluded from graph evidence, agent context, verification decisions, and the ordinary DEV/VALIDATION receipt list. Runtime work is rejected after a HOLDOUT receipt exists.

The evaluator jobs use the existing durable queue, budget ledger, exact frozen tier manifest, and deterministic job IDs. A restart reuses completed queue work; unknown or failed work does not produce a verified final report. The integration fixture uses a distinct HOLDOUT manifest and input from DEV/VALIDATION. Tests cover final champion binding, aggregate-only owner output, budget accounting, and non-leakage.

## Bounded live ChatGPT test

- Existing Sign in with ChatGPT connection; `gpt-6-luna`; synthetic public README only; no project source or private data.
- Exactly two provider calls, no automatic retries; usage **4,164 tokens**, monetary cost `UNKNOWN`.
- The first turn persisted an evidence-linked Research Ledger finding. The second turn received that exact finding and cited it in a hypothesis; Core selected it with `CORE_PUBLIC_DEV_EVIDENCE_COVERAGE`.
- Both Exploration nodes committed. The run stopped at its approved two-iteration limit. The synthetic fixture has no executable tests, so no live experiment or evaluation is claimed.
- Durable campaign report: `/tmp/bananloop-v2-live-memory-guard-20261107/state/campaigns/c-7f977d8bed30e14d2b5b061a723b296a/reports/v2-owner-run-a-00f7f6671225498b354609b8.json`.

## Reproduction commands

Full offline Linux suite:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q -ra --tb=no
```

End-to-end Exploration → Optimization → trusted evaluator → independent verification → final HOLDOUT:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q tests/test_rpdi13_integrated_acceptance.py
```

## Remaining limits

- The paid live run validates multi-turn Exploration and durable memory handoff. It does not run a live Optimization role or a live experiment; those execution lanes are verified offline.
- Subscription token usage and monetary cost are not authoritatively provided by the transport; cost remains `UNKNOWN`.
- Research evidence ranking does not prove scientific truth or semantic relevance; repeat detection remains a bounded controlled-variable matcher.
- The pinned selector source commit is unavailable in the environment, and Windows ACL/reparse behavior cannot run on Linux.
- Hosted CI for this commit was pending when this report was written.

PR #135 remains Draft and unmerged. v0.2 Full is not declared complete.
