# PR #141 live Titanic Optimization smoke — 2026-11-08

## Result

The corrected v2 Titanic Optimization E2E test completed successfully with exactly one real SIWC GPT-6 Luna optimizer inference and no retry:

- Pytest: `1 passed in 12.96s`
- Campaign: `c-22222222222222222222222222222222`
- Campaign status: `ALL_NODES_COMPLETE`
- Trusted HOLDOUT: `VERIFIED`, 196 cases, two repeatability runs
- HOLDOUT accuracy: `0.7806122448979592` (78.06%); the 0.95 stretch target was not met
- Provider-reported usage: 13,722 input + 343 output = 14,065 total tokens, including 164 reasoning tokens
- Budget ledger: 14,065 tokens spent; cap 65,000; reserved 0; unknown 0; no breach
- Monetary cost: `UNKNOWN`
- Provider request ID: `resp_0335d554bba82d03016af063244b5887d2a96dc8f6da39e24f`

The actual optimizer reply passed through the Optimization runtime and produced a champion that the trusted evaluator scored. The model received the public training CSV and candidate model file; private evaluator tiers stayed in the local trusted test runtime. The protocol-researcher and protocol-verifier setup in this test is deterministic test code, so this is a live Optimization smoke, not a fully autonomous live Exploration-to-Optimization campaign.

## Reproduction

From the repository root with the SIWC account connected:

```bash
BANANLOOP_TITANIC_LIVE=1 python -m pytest tests/test_titanic_benchmark.py::test_titanic_v2_optimization_campaign_runs_to_terminal_holdout -ra
```

The test's live wrapper refuses a second optimizer dispatch and does not retry. Its SQLite database, private evaluator files, and raw provider response were kept in pytest's temporary state under `/tmp/pytest-of-sonik/pytest-current/test_titanic_v2_optimization_c0/campaign-state/`; this report contains only the public summary.

## Offline multi-epoch check

The same E2E fixture also passed offline with three Optimization epochs (`1 passed in 2.92s`): the first two intentionally poor candidates were rejected; the next optimizer context contained the persisted rejection reason each time; the third candidate passed independent verification and reached trusted HOLDOUT. This verifies multi-epoch runtime behavior with deterministic model replies, not multiple live LLM epochs.

## Limits and follow-up

Earlier versions of this test exposed two harness defects: the child spec initially omitted `max_model_tokens`, so provider usage was uncharged; then the one-shot wrapper dropped the adapter's `last_usage`, causing the ledger to reserve 20,000 tokens as `UNKNOWN`. Both are fixed. This successful run verifies that provider usage propagates through the wrapper and is settled into SQLite. The SIWC Responses adapter cannot enforce a provider-side output cap, so the allowance remains a local reservation and post-response accounting mechanism; the 65,000 campaign cap and 20,000 per-call reservation bounded this completed run.

This run tested one Optimization epoch only. It does not establish multiple live epochs, autonomous live Exploration, or 95% Titanic accuracy.

Source worktree: `a7e93aae719bf3a7c5fbeb5f9c53380357c47063` plus uncommitted changes; the current working-tree diff SHA-256 after the offline three-epoch test is `0a5755d965cc8672afadfeb1ae31a46df315d4d2126ebfbd9c0b593113251e5e`. The final live run used the same live path; the subsequent edit only expanded the offline reply sequence.

## Additional authorized live run

A fresh one-call run was executed after restoring network access for the SIWC model catalog. Pytest passed (`1 passed in 12.24s`); the campaign completed with no retries. Provider request `resp_0389c70843f206bc016af067df486487d288fc31920e8c0739` reported 13,722 input + 303 output = 14,025 tokens (127 reasoning). SQLite recorded exactly 14,025 spent, 0 unknown, against the 65,000-token campaign cap; no budget breach. Trusted HOLDOUT again verified 196 cases with two repeatability runs and 78.06% accuracy; the 95% target remains unmet. Monetary cost is unknown. The failed initial attempt was limited to model-catalog discovery under restricted DNS and dispatched no inference.
