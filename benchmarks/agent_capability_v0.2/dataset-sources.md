# Dataset source register

This file records candidate sources and the intended role mapping. It does not authorize copying or redistribution. Pin an immutable revision and complete the license review before importing any task data.

## Recommended sources

| Source | Public size / shape | Role fit | Decision |
|---|---|---|---|
| [AutoResearchBench](https://github.com/CherYou/AutoResearchBench) | 1,000 literature-research questions: 600 Deep Research and 400 Wide Research | Researcher | **Primary candidate.** Use a development subset for evidence retrieval and coverage. The project site states CC BY 4.0 for the dataset; the reference GitHub code repository states Apache-2.0. Treat those as separate licenses. Keep a fresh, independently curated private holdout; public questions are not a secret test set. |
| [SWE-bench Verified](https://github.com/SWE-bench/SWE-bench) | 500 human-validated real GitHub issue instances | Implementer | **Primary candidate.** Use a bounded subset for role capability. The SWE-bench repository states MIT for the benchmark project; each underlying repository retains its own license and must be checked before mirroring source snapshots. |
| [MLS-Bench](https://github.com/Imbernoulli/MLS-Bench) / [MLS-Bench-Lite](https://mls-bench.com/) | 140 tasks across 12 ML research domains; Lite is a 30-task subset | Proposer + Implementer, and joint research workflow | **Conditional candidate.** Strong fit for algorithmic research, but it evaluates a proposal/implementation/research loop rather than a pure single role. Pre-freeze proposals for Implementer-only cases; execute all proposals for Proposer utility. Check task/package licenses and hardware requirements individually before use. No blanket redistribution permission is assumed here. |
| [AutoResearchExam](https://benchmarks.bespokelabs.ai/autoresearchexam/) | 29 open-ended ML research tasks, designed for a 24-hour research window and hidden-test AUARC | Team/system; proposer + implementer only as a joint workflow | **System-level candidate, not a pure role dataset.** Keep it for long-horizon integrated evaluation. The public benchmark page does not display a dataset license; do not mirror task contents until terms are verified. Existing BananLoop historical runs are not matched head-to-head evidence. |

## Sources that must be built specifically for BananLoop

### Selector

Create frozen candidate portfolios. Each portfolio needs at least 2–4 admissible candidates with distinct mechanisms. Independently execute every candidate in a separate workspace to produce evaluator-only outcome records. The selector receives only the permitted candidate descriptions and DEV evidence. The oracle outcome table stays private.

### Diagnostic

Build 50–100 fault-injection cases over time. Seed the set from:

- deterministic failures injected into BananLoop fixtures;
- sanitized CI/test failures;
- evaluator protocol failures;
- provider/network/worker failures;
- budget exhaustion and timeout cases;
- ambiguous cases where the correct answer is `UNKNOWN` or `INSUFFICIENT_EVIDENCE`.

Each case requires a reviewed root-cause label, exact supporting evidence, disallowed inferences, and one or more acceptable next probes. Never label an infrastructure failure as scientific falsification.

### Campaign bootstrapper

Build repository/goal fixtures with independently authored expected campaign contracts. Include wrong metric direction, missing evaluator, ambiguous goal, conflicting tests, protected paths, and hidden-split separation.

### Pivot advisor

Build supervised-only cases where the correct behavior is to propose options and trade-offs without granting approval. Include cases where a pivot is unjustified and the correct answer is to continue the existing plan or stop.

## License and provenance policy

- AutoResearchBench: its project site states CC BY 4.0 for the dataset. Preserve attribution and the source revision.
- SWE-bench: the project README states MIT for SWE-bench; verify each upstream repository's license separately.
- MLS-Bench: a root-level blanket dataset license has not been confirmed in this review. Treat as restricted/pending until task data, code, dependencies, and model/data assets have been checked.
- AutoResearchExam: a dataset license was not visible on the public benchmark page reviewed. Treat as pending; do not redistribute.
- BananLoop-derived incident cases: use only sanitized, non-secret, non-customer, non-confidential artifacts. Preserve source hashes and remove credentials, private evaluator material, holdout data, and unrelated personal information.

Do not vendor entire upstream datasets by default. Prefer immutable source references, upstream task IDs, content hashes, and a reproducible acquisition script after license review.

## Existing BananLoop evidence: use restrictions

The current `docs/BENCHMARK_STATUS.md` correctly marks the historical 7:3 AutoResearchExam comparison as reported exploratory DEV evidence whose original receipts and evaluator identities are not independently reverified. It is useful for choosing investigation targets, but it must not be copied into this benchmark as a verified win, a matched-budget comparison, or a role score.

The existing Selector A/B v2 fixture is useful as a protocol example, not as evidence for every role. It evaluates a small selector fixture and explicitly does not establish generalization.
