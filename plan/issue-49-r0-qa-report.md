Welcome to Cloud Shell! Type "help" to get started.
To set your Cloud Platform project in this session use `gcloud config set project [PROJECT_ID]`.
You can view your projects by running `gcloud projects list`.
# Issue #49 R0 QA report

**Status: incomplete because hosted CI has not reached a runner.** The Bank Marketing E2E and current local pytest gates have evidence below. Do not merge PR #47 or close issue #49 until the remaining CI/review gate is resolved.

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

## Bank Marketing campaign E2E

**Acceptance: met locally on one reproducible campaign.**

- Campaign: `c-68cc0b1cec90818c229983ca7ddbbbb1`
- Isolated Bank Marketing snapshot: commit `c078969`
- BananLoop source under test: `7472ae3f9d1d409ab08cd0ecce3d53619ee1d0cb`
- Outcome: `STOPPED/PLATEAU`; four scientific experiments, four attempts, 12 agent calls, 8 evaluator calls. No candidate passed the independent promotion requirements, so the baseline remained champion.
- Approved budget: 4 experiments, 7 attempts, 18 agent calls, 16 evaluator calls. Observed counts remained within each limit.

| Experiment | DEV intervention | DEV PR-AUC delta | Decision |
|---|---|---:|---|
| 1 | Logistic regression regularization, C=0.1 | +0.00695671 | DEV accepted; then rejected by independent validation gate |
| 2 | Regularization refinement, C=0.5 | +0.00170081 | Rejected as below the meaningful-improvement threshold |
| 3 | Group rare categorical values | 0.00000000 | Rejected; no meaningful DEV effect |
| 4 | Increase positive-class loss weight | -0.00346003 | Rejected; no meaningful DEV improvement |

After experiment 2's DEV rejection, ANALYZE persisted two generalized DEV capsules with separate observation/inference fields. The experiment 3 proposal received both capsule structures, selected the new `feature_representation` family, targeted `capsule:metric_misalignment`, and referenced experiments 1 and 2. Its DEV result was then measured and rejected. Experiment 4 tested a third family, `class_asymmetric_objective`.

A structural audit of all four saved proposal requests found no validation, holdout, case-ID, or label key paths. All proposer-visible `failure_examples` arrays were empty; raw public DEV examples were supplied only to ANALYZE. The campaign made one terminal holdout evaluation after plateau; no holdout metrics, labels, or examples are included here.

Durable resume was verified after terminal stop with `bananloop resume`: it returned `STOPPED/PLATEAU` with the same 4 experiments, 4 attempts, 12 agent calls, and 8 evaluator calls. No completed action was repeated.

The campaign ended honestly without validated improvement. This is evidence for the R0 research loop and its isolation behavior, not evidence that the Bank Marketing model improved.

## GitHub Actions

Latest observed run: [39732310076](https://github.com/ObiVanBanan/BananLoop/actions/runs/39732310076), on report-only commit `3b359050924f2c7822663dfe141d4ccc2231bfb5`, after the code commit `7472ae3`. All four matrix jobs (Ubuntu 3.11/3.12/3.13 and Windows 3.12) failed with empty step lists and no downloadable logs. An explicit rerun of run `39731801213` produced the same no-step failure. Prior runs reported `runner_id=0`, no runner name, and no steps.

**Conclusion:** hosted Linux and Windows CI remain unverified because no job reached checkout or test execution. Local Linux pytest and earlier Windows results do not substitute for a runner-assigned run on the current code.

## Other R0 evidence

- Logic Gold v0.3 previously passed 51/51 vectors on an earlier revision. It is a deterministic policy/research oracle, not a replacement for campaign E2E or current-head hosted CI; it has not been rerun on `7472ae3`.
- Earlier Windows Python 3.13.1 full-suite and API preflight results are historical evidence only. The current 314-test run is a separate Linux run.
- The report records the baseline/QA decision state. PR #47 remains a draft pending reviewer judgment and the outstanding campaign and CI gates.

## Remaining R0 gates

1. Obtain runner-assigned Linux and Windows Actions results for the current PR head.
2. Reassess PR #47 after those results; keep #49 open until the CI gate and reviewer decision are resolved.

The full Bank Marketing experiment/capsule/resume gate and current local pytest gate are now evidenced above.