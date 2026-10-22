# BananLoop bootstrap incident — 2026-10-21

## Summary

The Bank Marketing quality campaign did not reach model experiments. BananLoop
remains in `RESERVED / BOOTSTRAP`; the campaign has not been initialized and no
model candidate was evaluated.

- Campaign: `c-3b6aa0d5c6ae135328cc91fff5cab8b2`
- Goal: improve chronological Bank Marketing `pr_auc`, preserve validation
  performance, exclude post-call information, and keep evaluation on CPU.
- BananLoop used a clean temporary clone at
  `/tmp/bananloop-bank-marketing-clean` because the original project checkout
  had local README changes and this untracked log. Those original files were
  left untouched.
- The first bootstrap run ended with `agent call failed`.
- A subsequent bootstrap reached evaluator readiness, then failed during the
  DEV smoke command (`python .bananloop_eval/evaluate.py --split dev`).
  Scikit-learn raised `ValueError: Expected 2D array, got 1D array instead`;
  the received input was a one-dimensional array of feature dictionaries.
- The likely issue is in the evaluator adapter's conversion of records to the
  tabular input expected by the model pipeline. This is a diagnosis from the
  traceback, not a confirmed root cause.
- The campaign bootstrap record contains three attempts, all with failed
  readiness. BananLoop's default bounded repair limit was three total attempts.
  `BananLoop/src/bananloop/bootstrap.py` was changed to allow five total
  bootstrap attempts (two additional repairs), and the same campaign was
  resumed.
- At the time of this entry, the latest recorded status is still
  `RESERVED / BOOTSTRAP`, with three recorded readiness failures. The resumed
  CLI session has not returned a result yet.

## Impact and next diagnostic steps

No model code was changed and no benchmark result or ACCEPT/REJECT decision was
produced. When investigating, inspect the bootstrap attempt receipts and the
generated evaluator adapter in the campaign workspace, especially how training
and split records are converted before calling the estimator. Keep the
benchmark splits, labels, and scientific checks unchanged. Resume the same
campaign after fixing the adapter; do not treat evaluator readiness failure as
a model experiment result.

## Follow-up fix — 2026-10-21

Investigation found two interface mismatches behind the readiness failure:

1. `src/data.py` returns a list of feature dictionaries, while the model's
   `ColumnTransformer` selected named DataFrame columns. `src/model.py` now
   converts record input to a stable two-dimensional matrix and selects
   numeric/categorical fields by position. The same change is present in the
   temporary clean baseline commit `802a39c` used for BananLoop.
2. The evaluator result includes a string `diagnostics.split` and case identity
   records without per-case metrics. BananLoop's result schema now accepts a
   string split label and identity-only cases; readiness still checks that a
   paired comparison has the configured per-case metric before using such
   records.

The stalled bootstrap process was interrupted before experiments. The same
campaign was then resumed with these compatibility fixes. The bootstrap limit
was five total attempts.

## Retry outcome — campaign completed

Campaign `c-3b6aa0d5c6ae135328cc91fff5cab8b2` reached evaluator readiness and
completed four experiments. BananLoop stopped with `BUDGET_EXHAUSTED`; no
candidate passed validation, so the baseline remained champion.

| Trial | DEV PR-AUC | DEV balanced accuracy | BananLoop outcome |
|---:|---:|---:|---|
| Baseline | 0.229681 | 0.500000 | Champion |
| 1 | 0.236637 (+0.006957) | 0.500000 | DEV accepted; validation rejected |
| 2 | 0.231381 (+0.001701) | 0.500000 | Rejected; below the required primary improvement |
| 3 | 0.231381 (+0.001701) | 0.500000 | Rejected; below the required primary improvement |
| 4 | 0.236637 (+0.006957) | 0.500000 | DEV accepted; validation rejected |

Baseline validation PR-AUC was 0.340177 and remained the champion value because
all candidates were rejected. Final holdout was evaluated after experiment
selection only: PR-AUC 0.492159, balanced accuracy 0.507256. This final-only
measurement was not used to select or tune a candidate. The holdout evaluator
reported `target_met=true`, but the campaign's improvement objective was not
met because no candidate was promoted.

The BananLoop core compatibility fixes were:

- `bootstrap.py`: allow five total bootstrap attempts instead of three.
- `contracts.py`: accept string split labels in diagnostics and identity-only
  case records with empty metrics. Paired-comparison readiness still requires
  its configured per-case metric.
- `policy.py`: allow numeric diagnostics such as `elapsed_seconds` to satisfy
  numeric guardrails, and route `validation_*` guards to the corresponding
  metric on the validation tier only.
- `engine.py`: validate final holdout results against the holdout manifest.

The model input fix is in `src/model.py`; the BananLoop campaign's experimental
changes were held in its campaign workspace. No candidate was promoted to
champion.

## Follow-up autonomous run — campaign `c-226fc50bd99ad640be98ead83495700a`

The later campaign used the fixed project adapter and ran five evaluator-backed
experiments. It ended with `BUDGET_EXHAUSTED`; the champion remained the
baseline.

| Trial | DEV PR-AUC delta | Outcome |
|---:|---:|---|
| 1 | -0.003460 | Rejected: no primary improvement |
| 2 | +0.006957 | DEV improvement; validation rejected |
| 3 | +0.006646 | Validation rejected, including validation non-regression |
| 4 | -0.002470 | Rejected: no primary improvement |
| 5 | -0.012290 | Rejected: no primary improvement |
| 6 | — | Implementation blocked; no evaluator result |

Baseline and final champion PR-AUC were both `0.229681`; validation remained
`0.340177`. Final holdout PR-AUC was `0.492159`, evaluated after selection only.
The run used 18 agent calls, 12 evaluator calls, and six attempts (five
experiments plus one blocked implementation). The campaign report and related
BananLoop fixes are consolidated in
`BananLoop/qa/real-project-autonomous-qa-2026-10-21.md`.
