# Benchmark evidence register — v0.1.0 private Research Preview

**Status: EVIDENCE PENDING.** This register separates historical exploratory signals from the required release-gate head-to-head. It is not a leaderboard or a claim of general superiority over Codex.

## Historical experiments

### AutoResearchExam — exploratory 10-task DEV comparison

A prior internal run was **reported** as **BananLoop 7 vs ordinary Codex 3** on best DEV rewards across ten tasks. The accompanying discussion reported approximately **20.63 million vs 8.63 million model tokens**, respectively. **The original per-run logs, final artifact hashes, evaluator identities and task receipts are not linked or independently reverified in this document.**

Consequently:
- This is a *reported exploratory DEV result*, **not** a verified matched-budget result or an independent HOLDOUT score.
- The resource budgets and conditions differed; higher BananLoop total token consumption prevents a cost-efficiency claim.
- Treat 7:3 as an investigation lead. Do **not** present it as `pass@1`, a public benchmark win, or release qualification.

The new paired protocol is tracked in [Issue #64](https://github.com/ObiVanBanan/BananLoop/issues/64). Results from the planned repeat must be attached with manifests and evaluator receipts before changing the status above.

### LiveCodeBench v6 hard-60 — two BananLoop configurations, *not* Codex direct vs BananLoop

In the [public-only pilot report](https://github.com/ObiVanBanan/BananLoop/blob/codex/livecodebench-pilot-v1/examples/livecodebench/results/pilot-v6-hard-60-report.md), ordinary BananLoop Control passed all public examples on **58/60** tasks; optional Fresh passed them on **53/60** tasks. This compares two **BananLoop configurations**, both using Codex. No official hidden score, `pass@1`, precise billed tokens or matched direct-Codex baseline was measured.

### Shared-account Codex pilot — budget-ineligible

[Issue #60](https://github.com/ObiVanBanan/BananLoop/issues/60) documents an exploratory paired run where the best DEV quality matched, while BananLoop exceeded its nominal 750,000-token cap. That pair was **ineligible** for an advantage claim; keep this negative result visible.

## Required pre-preview benchmark: ordinary iterative Codex vs Codex + BananLoop

- Same pinned model/version/reasoning effort, initial repositories, task text, approved DEV information, tool permissions, evaluator and resource constraints.
- Codex Direct **may iterate**, run approved DEV checks and repair its solution. Do not compare BananLoop against an artificially restricted one-shot baseline.
- Freeze the task IDs, source/evaluator hashes, score direction, seeds, per-arm call/token/time ceilings and success definition **before** the run.
- Account for **all** BananLoop role calls (including proposal, selection and analysis), retries, input/output/cached tokens and any attributable monetary cost. Missing cost is `UNKNOWN`, not zero.
- Only DEV observations may guide changes; independent VALIDATION and terminal HOLDOUT results must not return to agent-visible memory or prompts.
- Execute untrusted candidate code only with tested isolation from host credentials, protected data and evaluator; no unisolated fallback.
- Export per-task outcomes and counts for **wins / ties / losses**, quality delta, token/cost efficiency, regressions, wall time and failures, including disqualified/incomplete arms.
- Attach the run manifest, exact code and evaluator SHAs, sanitized logs, reproducible commands, frozen final predictions and eligibility decisions.
- Report any material protocol mismatches as **ineligible**, not as wins. Results need not favor BananLoop to be an honest private-preview benchmark.

## Release gate

- [ ] Repeat paired head-to-head with actual independently reviewable receipts.
- [ ] Verify equal-budget and security eligibility, or clearly mark each pair's limitations.
- [ ] Produce a per-task comparison and limitations section.
- [ ] Link evidence and review the final report before owner approval.

Until these boxes are complete, the **benchmark gate is OPEN**. Existing DEV fixture improvements demonstrate a workflow, not superiority over standalone coding agents.
