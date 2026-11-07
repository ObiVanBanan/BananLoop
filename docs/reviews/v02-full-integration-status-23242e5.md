# BananLoop v0.2 Linux integration status — `23242e5`

This is an integration checkpoint, not a v0.2 Full acceptance claim. Draft PR #135 remains open and unmerged.

## Exact-SHA verification

Runtime/test SHA: `23242e577049046cf33b9722be5d11be706ae299`.

- Full offline Linux suite: **950 collected, 948 passed, 0 failed, 0 errors, 2 skipped**; exit code 0.
- Reproduce with `PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -q -ra --tb=short`.
- Skips are the unavailable pinned selector source commit and Windows ACL/reparse-point contract on Linux.
- Machine-readable results: [`v02-full-integration-results-23242e5.json`](v02-full-integration-results-23242e5.json).

## Research-memory regression coverage

`test_two_real_exploration_nodes_use_refutation_memory_and_preserve_pins` executes three durable v2 Exploration iterations. The first records a REFUTES finding. The next model input receives that evidence, yet prefers an unrelated candidate; Core selects the candidate that cites the exact prior evidence. The following iteration executes that selected action. Research evidence remains explicitly unverified, and the campaign stops on a repeated experiment with a persisted report. This proves the memory influences the next choice while avoiding a scientific-truth claim.

`test_malformed_dispatched_response_is_durable_but_never_published` exercises a malformed model response after dispatch. The queue records the attempt as `UNKNOWN`, exposes no committed result, and does not publish evidence or complete the graph node. The budget keeps the dispatched/uncertain call accounted, preventing an automatic redispatch after restart.

`test_expired_dispatched_job_is_not_redriven_after_runtime_restart` simulates process loss after the dispatch boundary. Lease expiry records `UNKNOWN`; after reopening the queue, the runtime refuses redelivery, leaves the node incomplete, and preserves the uncertain call in the budget.

## Other end-to-end evidence

The RPDI-13 cross-layer acceptance test exercises two Exploration turns, a child Optimization handoff, candidate creation, isolated trusted evaluation, independent verification, champion promotion, and terminal HOLDOUT. The Titanic mini-benchmark exercises production Linux bubblewrap for its trusted evaluator and records 78.06% HOLDOUT accuracy (153/196) on a fixed seed-2026 split. Its offline optimizer and parent protocol approval are deterministic fixtures. The exact-SHA full Linux suite above includes the memory-guided follow-up, malformed-response, interrupted-dispatch recovery tests, and Titanic benchmark.

## Live SIWC boundary and remaining limits

Two authorized GPT-6 Luna optimizer attempts used 28,110 observed tokens in total; monetary cost is `UNKNOWN`. They exposed two input-contract defects, now fixed in code: the selected hypothesis ID was omitted from optimizer context, and permitted coefficient names were not published in the candidate starter. Neither failed attempt was retried. The two-call allowance was consumed, so these fixes have offline evidence only. Successful live Optimization and live experiment execution remain unverified. RPDI-14 authoritative subscription-cost accounting is unavailable.
