# Issue #42 deterministic A/B policy replay

The replay command is `python examples/issue42/compare.py`. Seed, two prior no-effect DEV receipts, candidate set, fixed evaluator mapping and total budgets are identical for the legacy and portfolio policies. Each scenario has a 2-agent-call / 2-evaluator-call ceiling, including the baseline evaluator call. The old planner emits only the first (repeated-mechanism) candidate; the v2 planner sees that candidate plus one materially different alternative. Both use the same deterministic core duplicate/scope/budget rules.

| Domain / metric | Policy | Candidates considered | Duplicate proposals | Scientific trials | Executed mechanisms | Reframe candidate executed | Agent / evaluator calls | Best DEV value | Stop |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Bugfix / correctness (0.5 → 0.6) | legacy single | 1 | 1 | 0 | 0 | 0 | 1 / 1 | 0.5 | `NO_ADMISSIBLE_HYPOTHESIS` |
| Bugfix / correctness (0.5 → 0.6) | portfolio | 2 | 1 | 1 | 1 | 1 | 2 / 2 | 0.6 | `GOAL_MET` |
| CPU performance / latency (10 → 7 ms) | legacy single | 1 | 1 | 0 | 0 | 0 | 1 / 1 | 10 | `NO_ADMISSIBLE_HYPOTHESIS` |
| CPU performance / latency (10 → 7 ms) | portfolio | 2 | 1 | 1 | 1 | 1 | 2 / 2 | 7 | `GOAL_MET` |

These are deterministic policy replays with synthetic fixed evaluator outcome mappings, not independent benchmark runs or a universal quality claim. They show that the portfolio selector rejects the repeated mechanism before implementation and can spend one bounded trial on a different mechanism under the same ceilings. The real CLI engine path is exercised separately by [the offline dogfood](../examples/issue42/README.md).

Strategy state was `reframe` in both rows of each pair. The portfolio policy records the selected reframe candidate; the legacy policy had no portfolio selection event. No separate numerical strategy-switch count is inferred.
