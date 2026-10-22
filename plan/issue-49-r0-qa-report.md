Welcome to Cloud Shell! Type "help" to get started.
To set your Cloud Platform project in this session use `gcloud config set project [PROJECT_ID]`.
You can view your projects by running `gcloud projects list`.
# Issue #49 R0 QA report

**Status: incomplete.** This report records current evidence and the remaining R0 acceptance gates. It does not recommend merging PR #47 or closing issue #49.

- PR: [#47](https://github.com/ObiVanBanan/BananLoop/pull/47)
- Current source branch: `codex/dev-failure-reflection-v3-20261021`
- Current code revision: `7472ae3f9d1d409ab08cd0ecce3d53619ee1d0cb`
- Current local environment: Linux, Python 3.12.3

## Current local verification

| Check | Evidence | Result |
|---|---|---|
| Full pytest | `python -m pytest -q -o addopts=''` at the tree committed as `7472ae3`; exit code 0. | 314 passed in 79.45s |
| Scoped symbol locator regression | `test_portfolio_scope_accepts_symbol_locator_for_an_editable_file`; candidate `src/model.py:build_model` is accepted only when `src/model.py` itself is in editable scope. | Passed in the full suite |
| Aggregate-only DEV rejection | Regression in `tests/test_engine.py` verifies an explicit unknown/insufficient-evidence capsule, safe DEV-only proposal context, and no invented root cause. | Passed in the full suite |

The scope fix addresses an observed `OUT_OF_SCOPE` rejection caused by a function locator appended to an otherwise editable path. It does not relax path traversal, absolute-path, or actual file-scope checks.

## Bank Marketing campaign evidence

The local campaign artifacts support useful partial evidence, but the exact R0 E2E acceptance is **not yet met in one reproducible campaign**:

| Campaign | Evidence | Limitation |
|---|---|---|
| `c-6efe75beb12d426d11e3d3bcb12f4e28` | Three scientific DEV experiments completed; all were rejected and the campaign ended at plateau. Terminal resume was checked: counters and evaluator/agent call totals did not change. | The aggregate-only evaluator emitted no `failure_signals` or `failure_examples`; this run therefore did not persist a causal failure capsule. |
| `c-5e39c1df87de1932743263b4de6a1993` | Two scientific DEV rejections produced two generalized DEV-only capsules. The next planned experiment used a different mechanism family and cited prior DEV experiments. | Its implementation reached the evaluator but raised a `TypeError` before a score was produced. The campaign paused as infrastructure/implementation failure; this is not a third scientific experiment. |
| `c-3deb7ddb27f3cff57cb1be8f7a395a16` | Diagnostic run exposed the scoped-symbol-locator issue; no science was measured. The locator handling is covered by the current regression test. | No campaign acceptance evidence. |

Across these runs, no single campaign proves **at least three scientific experiments plus DEV failure → persisted capsule → different hypothesis family → measured DEV result, followed by retry/resume**. The operational failure remains distinct from a scientific rejection. Aggregate-only evidence remains explicitly unknown rather than being promoted to a root-cause claim.

Historical QA reports also describe Bank Marketing campaigns, including DEV-only reflection, but the runs above are the relevant current artifacts. Historical reports are not substituted for the acceptance gate.

## GitHub Actions

Commit `7472ae3f9d1d409ab08cd0ecce3d53619ee1d0cb` was pushed to the existing PR #47 source branch. The GitHub workflow-runs lookup returned no PR-triggered run for this commit at the time of this report. Earlier runs on PR #47 failed before any job received a runner (`runner_id=0`, no runner name, no steps), so they do not establish a Linux or Windows test result.

**Conclusion:** Linux and Windows hosted CI remain unverified. A completed run must show runner assignment and test steps for both operating systems before this gate can be marked passed.

## Other R0 evidence

- Logic Gold v0.3 previously passed 51/51 vectors on an earlier revision. It is a deterministic policy/research oracle, not a replacement for campaign E2E or current-head hosted CI; it has not been rerun on `7472ae3`.
- Earlier Windows Python 3.13.1 full-suite and API preflight results are historical evidence only. The current 314-test run is a separate Linux run.
- The report records the baseline/QA decision state. PR #47 remains a draft pending reviewer judgment and the outstanding campaign and CI gates.

## Remaining R0 gates

1. Obtain runner-assigned Linux and Windows matrix results for the current PR head.
2. Complete a reproducible Bank Marketing campaign with at least three scientific experiments, DEV-only capsule-to-different-family-to-DEV lineage, and verified retry/resume, without feeding validation or final holdout evidence to adaptive research.
3. Reassess PR #47 after those receipts; keep issue #49 open until its R0 acceptance conditions are satisfied.
