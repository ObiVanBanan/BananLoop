# Issue #49 R4 dogfood progress

Run date: 2026-10-22 UTC. This is partial evidence for R4, not its completion report.

## Evidence carried forward

`real-project-autonomous-qa-2026-10-21.md` documents real Bank Marketing (ML), product matcher (retrieval), and WineQT campaigns with separate DEV/validation/final-holdout scores. Two saved Bank Marketing campaign specs use the Codex adapter. These runs include both promoted and rejected outcomes; they do not establish a general performance claim.

## New deterministic CPU dogfood

Both fixtures ran through `python -m bananloop.cli` on a host exposing 2 CPUs, 8,138,084 KiB RAM, and 32 GiB free disk. They used the Command adapter, made zero hosted-model calls, retained frozen evaluator/test assets, and recorded DEV receipts.

### Bug fix and regression reduction

The baseline implementation incorrectly returned the percentage amount instead of subtracting the percentage from the price. The DEV test failed at baseline (`pass_rate=0`, check false). The agent changed only `src/discount.py`; the same regression passed at DEV, validation, and final holdout (`pass_rate=1`, check true). Campaign status was `GOAL_MET`; it used 2 agent calls and 5 evaluator calls. Exact commit, snapshots, receipt hashes, and aggregate measurements are in [bug-fix evidence](issue49-bugfix-dogfood-2026-10-22.json).

### CPU p95 performance

On a frozen synthetic workload of 1,200 rows and 240 queries, the baseline repeated a full row scan per query; the candidate built a one-pass index. Three valid local campaigns measured:

| Run | DEV p95 baseline → candidate | Relative change | Terminal status |
|---|---:|---:|---|
| 1 | 16.773888 → 0.302379 ms | −98.20% | `GOAL_MET` |
| 2 | 37.410913 → 0.570080 ms | −98.48% | `BUDGET_EXHAUSTED` (candidate accepted; 0.5 ms DEV target narrowly missed) |
| 3 | 9.737392 → 0.186147 ms | −98.09% | `GOAL_MET` |

In the final run throughput rose from 29,706 to 1,613,823 queries/s, correctness stayed 1.0, validation p95 changed from 13.384226 ms to 0.185728 ms, and final holdout p95 was 0.185036 ms. The within-run A/A half-sample p95 differences were 2.120296 ms for baseline and 0.038629 ms for candidate. That run's DEV improvement exceeded the observed baseline A/A spread. The [performance evidence file](issue49-performance-dogfood-2026-10-22.json) contains exact snapshots, receipts, and aggregate measurements for the final run.

The performance fixture is a controlled synthetic algorithm comparison on one host. It is not evidence of the same speedup on other hardware or workloads.

## R4 still open

- The matched-budget comparison against an actual naive coding-agent loop has not been run; existing issue #42 `compare.py` is a deterministic policy replay, not an end-to-end comparator.
- RAG/service-backed dogfood and image-quality dogfood are not proven here.
- The prior ML/retrieval campaigns need a consolidated same-core receipt table and environment/protocol fingerprint audit for the R4 gate.
- Hosted Linux/Windows CI has not supplied runnable steps/logs, so the new fixture also has only local Linux execution evidence so far.

Therefore the #49 R4 checklist and DoD remain unchecked.
