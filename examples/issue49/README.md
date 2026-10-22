# Issue #49 offline dogfood fixtures

These runners exercise the public BananLoop CLI and durable campaign engine on disposable local repositories. They use the command agent adapter and standard-library evaluators; they make no hosted model, network, or paid-service calls.

## Bug-fix / regression reduction

```sh
PYTHONPATH=src python examples/issue49/run_bugfix_dogfood.py --work-dir /tmp/bananloop-issue49-bugfix
```

The frozen DEV test fails on the baseline implementation of `apply_discount`; the command agent changes only `src/discount.py`. Separate validation and final holdout tests are hidden from the agent and protected. The evaluator reports only aggregate pass rate and suite status.

## CPU performance

```sh
PYTHONPATH=src python examples/issue49/run_performance_dogfood.py --work-dir /tmp/bananloop-issue49-performance
```

The evaluator compares a repeated nested scan with a one-pass dictionary index on frozen per-tier workloads. It reports p95 latency, throughput, correctness, and a within-run A/A split diagnostic. Inputs are synthetic and the result is a local fixture result, not a general performance claim.

Evidence from the 2026-10-22 executions is recorded under `qa/issue49-*-dogfood-2026-10-22.json`.
Aggregate-only exports reconstructed from the saved Bank Marketing, product matcher, and WineQT campaign stores are in `qa/issue49-real-project-campaign-aggregates-2026-10-22.json`. They contain protocol/evaluator/receipt fingerprints and no per-case hidden-tier data.

## Matched-budget portfolio comparison

```sh
PYTHONPATH=src python examples/issue49/run_matched_budget_comparison.py --work-dir /tmp/bananloop-issue49-matched
```

This compares the BananLoop portfolio path with a first-candidate loop on the same frozen synthetic fixture, evaluator, ordered proposals, and caps (2 agent calls, 6 evaluator calls, 1 experiment). The evaluator batches repeated lookups to reduce timer overhead and reports an A/A split diagnostic. Results are local evidence from one CPU fixture; they do not establish general agent success rates. Recorded runs are in `qa/issue49-matched-budget-2026-10-22-run*.json`.
