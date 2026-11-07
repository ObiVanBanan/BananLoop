# Titanic survival mini-benchmark

This is a small, reproducible binary-classification benchmark for exercising
the v2 Exploration → supervised Optimization → trusted evaluation → independent
verification → terminal HOLDOUT path.

## Dataset

- Source: [OpenML dataset 40945, Titanic v1](https://www.openml.org/d/40945).
- OpenML metadata lists the dataset license as `Public`, 1,309 rows, and
  `survived` as the target. The downloaded ARFF file's MD5 is
  `60ac7205eee0ba5045c90b3bba95b1c4`.
- The upstream attributes include `boat` and `body`, which disclose or closely
  proxy the outcome. They must be excluded before data is made visible to the
  researcher/optimizer and before evaluator features are built. Also exclude
  person-identifying or record-like fields (`name`, `ticket`, `cabin`, and
  `home.dest`).
- Allowed predictors: `pclass`, `sex`, `age`, `sibsp`, `parch`, `fare`, and
  `embarked`. Missing values must be handled inside the candidate pipeline.

The canonical Kaggle Titanic task scores percentage of correct predictions
(accuracy) on 418 unlabeled competition rows. This benchmark uses OpenML's
publicly labeled copy instead, so it can create fixed offline DEV, VALIDATION,
and HOLDOUT tiers without accessing Kaggle or accepting competition rules.

## Protocol

- Freeze one deterministic, stratified, ticket-group-aware split before any
  model call. A ticket group must occur in exactly one tier to reduce family or
  booking leakage across tiers.
- Keep `data/train.csv` in the candidate workspace. Keep all DEV, VALIDATION,
  and HOLDOUT rows and labels in the trusted evaluator artifact store, outside
  the candidate repository and model context.
- The researcher may see only a bounded DEV aggregate and public schema. The
  optimizer may read only the candidate implementation and designated training
  partition. It may write `src/model.json` only. Neither role receives
  validation or holdout rows.
- Run the frozen evaluator on DEV for candidate iteration, VALIDATION for
  independent verification, and HOLDOUT once after the scheduler finishes all
  optimization nodes. Expose only the final aggregate accuracy to the owner.
- Accuracy **0.95 is the requested stretch target**, not a guaranteed result or
  an excuse to change the split, inspect protected tiers, or add outcome-leaking
  fields. Report the actual score and stop honestly if the target is not met.

## Current integration status

The shared v2 runtime supports terminal trusted HOLDOUT and owner-only
aggregate reporting. The Linux RPDI-13 cross-layer acceptance test exercises
Exploration → Research Memory → Optimization → isolated evaluator → independent
verification → terminal HOLDOUT. It passed in this checkout with both its
test-runner and real-`bubblewrap` variants. That E2E uses a synthetic evaluator;
it is evidence for the runtime path, not a Titanic score.

`tests/test_titanic_benchmark.py` now exercises the data protocol, evaluator
asset freezer, and a complete v2 Optimization campaign. The E2E creates a
candidate, runs the trusted DEV evaluator, asks a separate pinned integration
verifier to accept/reject it, selects the champion, and runs independent
VALIDATION and terminal HOLDOUT evaluation. The optimizer sees only
`data/train.csv` and can write only `src/model.json`; evaluator inputs stay
outside its workspace. The evaluator uses the production Linux bubblewrap
runner, and the test skips with a specific reason when host namespaces are
unavailable. The parent protocol approval is fixture-seeded, and the optimizer
proposal is deterministic/offline; this demonstrates actual v2 execution and
isolation, not a live research/model call.

The first frozen run (seed 2026) completed with **HOLDOUT accuracy 0.7806
(153/196)**. This is below the requested 0.95
stretch target and is reported without changing the split or using protected
fields. The reproducible result and receipt digests are committed in
`results/linux-offline-e2e.json`; the test also emits `benchmark-result.json`
under its private temporary campaign state. Token usage is `UNKNOWN`; there
were zero live model calls.

To prepare a private run directory:

```sh
python benchmarks/titanic_survival/prepare.py \
  --source benchmarks/titanic_survival/openml-40945.arff \
  --output /tmp/bananloop-titanic-benchmark
```

Current verified Linux commands:

```sh
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -q \
  tests/test_titanic_benchmark.py tests/test_rpdi13_integrated_acceptance.py \
  -ra --tb=short
```

The real-bubblewrap Titanic test and cross-layer API test need Linux namespace
permissions; without them, pytest reports a skip for the affected production
isolation case. The independent RPDI-13 E2E covers the broader
Exploration → Research Memory → Optimization handoff; this Titanic mini
benchmark covers the real Optimization/evaluator/verification/HOLDOUT path.
95% remains a stretch target, not a guarantee. A live ChatGPT model-driven
Titanic campaign has not yet been run.
