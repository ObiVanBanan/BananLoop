# QA: Failure → Root Cause → Different Hypothesis on the current Bank Marketing project

Date: 2026-10-21  
BananLoop branch: `codex/dev-failure-reflection-v3-20261021`  
Project snapshot: `qa-runs/failure-reflection-bank-marketing-20261021`, commit `c62a560`  
Campaign ID: `state-c3207d086bb271c4443b199ead9b8fa2`

## Result

The campaign demonstrated a DEV-evidence-driven change of mechanism family, but it did **not** complete the full Failure → Root Cause → Different Hypothesis chain. The project evaluator provides aggregate scores only: it emits no `failure_examples` or `failure_signals`, and BananLoop recorded evaluator signal sanity as `UNAVAILABLE`. A specific root cause therefore could not be established from this project run.

The original Bank Marketing working tree was not modified. The campaign ran on an isolated copy of its current tracked and untracked project files. No final holdout evaluation was run.

## Measurements

Baseline: DEV PR-AUC `0.229681`; validation PR-AUC `0.340177`.

| Experiment | DEV PR-AUC | Validation PR-AUC | Outcome |
|---|---:|---:|---|
| Regularized logistic regression (`C=0.1`) | `0.236637` (+`0.006957`) | `0.340273` (+`0.000095`) | Rejected: validation improvement below its configured threshold |
| Pairwise feature interactions | `0.222494` (-`0.007186`) | Not run | Rejected: DEV regression |
| Histogram gradient boosting | Not measured | Not run | Campaign paused: evaluator crashed before producing a score |

After the first two scientific outcomes, planning mode changed to `reframe`. BananLoop proposed histogram gradient boosting, a materially different model family from the two linear candidates. It did not claim an observed root cause; its portfolio described interactions/nonlinear splits as hypotheses to test.

## Failure and next action

The third candidate used `OneHotEncoder(sparse=False)`, which the installed scikit-learn rejects with `TypeError: unexpected keyword argument 'sparse'`. The campaign ended `PAUSED_INFRA` with two completed scientific experiments and no promoted candidate. This is an implementation/runtime compatibility failure, not evidence against the histogram-gradient-boosting hypothesis. The saved campaign can be inspected with `bananloop status` and `bananloop report` using state directory `qa-runs/state-failure-reflection-bank-marketing-20261021`.

To verify the complete causal reflection chain on this project, the DEV evaluator must expose identity-free failure signals or public DEV error examples. The next campaign should also catch dependency/API incompatibility before evaluator execution, or resume only after the failed candidate has been corrected through an explicit implementation step.

## Regression evidence

Added `test_failure_capsule_and_repeated_dev_failure_reach_different_next_hypothesis`, which verifies the cross-stage deterministic path: a saved generalized failure capsule reaches the next proposal context; two identical DEV outcomes put the planner into `reframe`; the repeated mechanism is rejected; and the different mechanism is selected.

The complete BananLoop test suite passed: **302 passed**.
