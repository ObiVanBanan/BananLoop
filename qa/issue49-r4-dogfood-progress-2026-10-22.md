# Issue #49 R4 dogfood progress

Run date: 2026-10-22 UTC. This is partial evidence for R4, not its completion report.

## Evidence carried forward

`real-project-autonomous-qa-2026-10-21.md` documents real Bank Marketing (ML), product matcher (retrieval), and WineQT campaigns. I reconstructed aggregate-only reports from their durable campaign stores and verified each campaign spec, adapter, snapshots, split protocol hashes, evaluator binding, and receipt hashes. The export [issue49-real-project-campaign-aggregates-2026-10-22.json](issue49-real-project-campaign-aggregates-2026-10-22.json) omits per-case records and labels.

| Campaign | Adapter | DEV baseline → champion | Validation baseline → champion | Final holdout (terminal aggregate) | Outcome / assessment |
|---|---|---:|---:|---:|---|
| Bank Marketing `c-226fc50bd99ad640be98ead83495700a` | Codex | PR-AUC 0.229681 → 0.229681 | 0.340177 → 0.340177 | 0.492159; baseline snapshot only | 5 scientific trials, 1 implementation-blocked attempt, no promotion; `DEV_ONLY` |
| Product matcher `c-afbf21b4f610db2db0a97087313f9112` | Codex | accuracy 0.875 → 1.000 | 0.375 → 0.750 | 0.750; target not met | champion promoted after validation; terminal outcome `STOPPED`, assessment remains `DEV_ONLY` |
| WineQT `c-6482e268b37321166b5ffbaf04807e43` | Codex | ROC-AUC 0.865591 → 0.865591 | 0.865591 → 0.865591 | 0.865591; target not met | 3 scientific trials, plateau, no promotion; `DEV_ONLY` |

The Codex adapter is explicit in the two campaign specs; command adapter is used by the offline bug-fix/performance fixtures and the matched-budget comparator. This supplies same-core ML/retrieval, bug-fix, and performance evidence with both promoted and rejected outcomes. `DEV_ONLY` is preserved: the archived campaign reports do not prove independent business-level evaluator validity. These results do not establish a general success-rate or performance claim.

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

- Service-backed RAG and image-quality dogfood are not proven here. No external service/model was called for this offline QA increment.
- The aggregate export includes base commit, campaign-spec hash, snapshots, evaluator-binding hash, per-tier protocol hashes, and receipt hashes. A full effective runtime/dependency fingerprint audit across the archived campaigns is still missing.
- Hosted Linux/Windows CI has not supplied runnable steps/logs, so the new fixture also has only local Linux execution evidence so far.

Therefore the grouped ML/retrieval/RAG checkbox and the R4 DoD remain unchecked; the bug-fix, performance, adapter-diversity, and lightweight/matched-budget items now have evidence in this branch.

## Matched-budget portfolio versus first-candidate loop

Two repeated runs used the same frozen synthetic fixture commit, candidate order, command agent, evaluator, target/gates, and maximum budget (2 agent calls, 6 evaluator calls, 1 experiment). Each path consumed 2 agent calls and 5 evaluator calls. The portfolio selected `z-build-index-once`, promoted it, and met the goal on both runs. The naive loop selected the first candidate, `a-linear-scan-rewrite`, and rejected it on both runs. In run 1 its DEV p95 delta was +4.390 ms, below the 5 ms gate, and throughput regressed; validation improved +17.910 ms. In run 2 its DEV/validation p95 deltas were −32.178/−8.858 ms, so it regressed on both. Both candidates preserved correctness. Full measurements, A/A diagnostics, output hashes, calls, and receipt hashes are in [run 1](issue49-matched-budget-2026-10-22-run1.json) and [run 2](issue49-matched-budget-2026-10-22-run2.json).

An initial unbatched probe showed apparent acceptance of the naive candidate while baseline A/A noise exceeded the configured 5 ms gate. That exposed timer overhead and measurement instability, so the recorded comparison batches five lookups per timing sample. The two batched runs still show high variance in the synthetic workload; the results are directional fixture evidence only and do not support a broad claim about coding agents or real workloads.
