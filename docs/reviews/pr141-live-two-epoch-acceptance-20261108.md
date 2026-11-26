# PR #141 live two-epoch Optimization acceptance

Date: 2026-11-08  
Code tree: `f5f47bb77a29a4494ee4bc1353338ab825acc5f1`  
Campaign: `c-22222222222222222222222222222222`  
Provider route: Sign in with ChatGPT, GPT-6 Luna; no separately billed API key.

## Result

The opt-in Titanic Optimization end-to-end test completed in two live optimizer
epochs: `1 passed in 21.17s`. The campaign reached `ALL_NODES_COMPLETE`, and
the trusted HOLDOUT evaluator verified the final candidate on 196 cases twice.
Accuracy was `0.7806122448979592` (78.06%); the 95% stretch target was not met.

To test rejection-memory handoff deterministically, the test verifier fixture
forced exactly one rejection after the first real Luna proposal reached
independent verification. It did not alter the proposal. Before dispatching the
second real optimizer request, the harness asserted that the persisted role
context contained `previous candidate did not pass independent verification`.
The test passed that assertion, and the second candidate completed the
remaining independent verification, validation, and HOLDOUT route.

## Live requests and budget

Two real requests completed; there were no automatic or test-level retries.
Provider token usage was returned and reconciled to the campaign SQLite ledger:

| Request | Input | Output | Total |
|---|---:|---:|---:|
| `resp_02db40c8cb53db8a016af06eef23a887d2a15f3fdf6881bee5` | 13,741 | 355 | 14,096 |
| `resp_0364c432d79874bc016af06ef8a70087d2bbbea76eb7b2e94e` | 13,873 | 323 | 14,196 |
| **Total** | **27,614** | **678** | **28,292** |

The 65,000-token campaign cap settled at 28,292 spent, 0 unknown, with no
budget breach. Monetary cost is `UNKNOWN` because the subscription provider
does not expose a billing amount in this route.

## Reproduction

```bash
BANANLOOP_TITANIC_LIVE=1 \
BANANLOOP_TITANIC_LIVE_EPOCHS=2 \
.venv/bin/python -m pytest -o addopts= -q -ra \
  tests/test_titanic_benchmark.py::test_titanic_v2_optimization_campaign_runs_to_terminal_holdout
```

This setting permits at most two live optimizer inferences and does not retry.
The live test has a dedicated opt-in flag; ordinary offline test runs do not
send model requests. `BANANLOOP_TITANIC_LIVE_EPOCHS` accepts only `1` or `2`.

## Limits

This is a live multi-epoch Optimization acceptance, not a fully autonomous live
Exploration-to-Optimization campaign. Parent campaign approvals and the
independent integration-verifier response are deterministic fixtures. The
forced verifier rejection validates durable rejection memory handoff; it does
not measure naturally occurring verifier rejection rates. The final model
score is below the requested 95% stretch target. Subscription monetary cost is
unavailable.
