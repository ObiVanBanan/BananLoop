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

## Follow-up: diagnostic DEV-only evaluator

The first run showed that this project could test `reframe`, but its evaluator did not expose example-level failures. To test the complete chain without changing the user's working tree or the benchmark score, a second isolated snapshot (`qa-runs/failure-reflection-bank-marketing-dev-diagnostics-20261021`, initial commit `c078969`) added up to five public DEV-only `failure_examples`. Validation and holdout continued to emit no examples; labels, splits, and metric calculations were unchanged. The DEV result retained the same baseline PR-AUC (`0.229681`) and balanced accuracy (`0.500000`). BananLoop's bootstrap added manifest support to the evaluator and correctly recorded `signal_sanity=UNAVAILABLE`, because aggregate PR-AUC did not include per-case metric receipts.

The campaign completed two experiments and stopped at `PLATEAU` with the baseline still champion:

| Experiment | Hypothesis | DEV PR-AUC | Outcome |
|---|---|---:|---|
| 1 | HistGradientBoosting with ordinal-encoded categories | `0.184708` (-`0.044972`) | Rejected: DEV regression |
| 2 | Group infrequent categories before one-hot encoding | `0.229681` (`0.000000`) | Rejected: no meaningful DEV change |

The analyzer received five public DEV mistakes from experiment 1 and stored two generalized capsules. It identified the observed pattern as false negatives with low positive scores at the fixed decision threshold. It explicitly said varied feature values did not establish a common feature-level cause, and kept calibration, class weighting, encoding, or missing signal as alternatives rather than facts.

The next proposal request contained both capsules and the DEV reference, but none of the raw example objects. It selected `rare_category_regularization`, distinct from `hist_gradient_boosting_categorical_encoding`, and marked its claim about unstable rare-category estimates as conjecture. The experiment produced no DEV change. Its follow-up analysis said the examples did not support rare-level frequency as the cause and recommended separating ranking from cutoff behavior. The planner reached `reframe`; the configured two-experiment plateau then stopped the campaign before another proposal.

This is an end-to-end observed Failure → Root Cause hypothesis → Different Hypothesis → DEV test cycle. The cause remains a qualified inference, not a proven fact; the second candidate did not validate its proposed mechanism. The final holdout evaluator ran only on the unchanged baseline champion as the terminal campaign assessment; holdout examples were never provided to the agent.

### Bootstrap instruction fix

The initial diagnostic run's bootstrap configured signal sanity from public labels even though no per-case primary metrics existed. Readiness returned `missing_primary_metric`, and its repair call timed out. The Codex/Pi bootstrap instructions now require per-case primary metric receipts for configured known-example signal sanity; public labels and failure examples alone are not enough. Those examples remain usable for DEV failure analysis. Adapter/bootstrap tests passed (**57 passed**), followed by the full suite (**302 passed**).
