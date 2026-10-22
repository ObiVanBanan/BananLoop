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
