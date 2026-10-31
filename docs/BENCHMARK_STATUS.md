# Benchmark evidence register — v0.1.0 closed Research Preview

**Status: BLOCKED / INELIGIBLE FOR A NEW RUN.** This register separates historical exploratory signals from the required release-gate head-to-head. It is not a leaderboard or a claim of general superiority over Codex.

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

[Issue #60](https://github.com/ObiVanBanan/BananLoop/issues/60) documents an exploratory pair: ordinary Codex reported 706,403 tokens and DEV quality 0.70718697; BananLoop reported 751,298 tokens against a nominal 750,000, overshooting by 1,298. The BananLoop arm was **ineligible** and its next candidate was not measured. The observed champion scores matched; this does not show an advantage.

## Required pre-preview benchmark: ordinary iterative Codex vs Codex + BananLoop

As of 2026-11-01, `main` is `44614ea22bc5967adb7ee607bce2472cc66487ee`; PR #81 integrated the campaign cursor fix from [#75](https://github.com/ObiVanBanan/BananLoop/pull/75), opt-in Codex token accounting from [#61](https://github.com/ObiVanBanan/BananLoop/pull/61), and release documentation from [#72](https://github.com/ObiVanBanan/BananLoop/pull/72). On this code base, Linux/Python 3.14.7 full pytest passed **448 tests with 1 skipped**, and seven CPU-only research E2E cases passed. A regular wheel install and offline fixture run also succeeded on Linux/Python 3.14. PR #85 head `d7bf9d055da1557e9a5f584a5b9b834e82129894` passed hosted run [#41238895406](https://github.com/ObiVanBanan/BananLoop/actions/runs/41238895406) across Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12; the Windows full suite reported **448 passed, 1 skipped in 642.13s**. PR #85 changes no runtime or evaluator code. The exact release SHA is not selected.

The token limit is opt-in post-call accounting from Codex terminal receipts when available, not provider-enforced spend control; one opaque call can overshoot and automatic bootstrap in `goal/start` is not metered or admitted against the campaign token cap. No confirmed spend budget or verified hostile-code evaluator boundary is available. Draft PRs [#83](https://github.com/ObiVanBanan/BananLoop/pull/83) and [#84](https://github.com/ObiVanBanan/BananLoop/pull/84) remain outside `main`; #84 is not integrated with `CommandEvaluator`/readiness, and evaluator/ACL isolation, model proxy safety, token/USD accounting, total Docker disk use and complete negative preflight remain unproven. The current host has no Docker CLI. Do not run the Issue #64 arms until these gates are verified. Current Linux E2E evidence, the DEV selector fixture, and prior Docker inert probes do not substitute for independently scored matched-resource H2H.

- Same pinned model/version/reasoning effort, initial repositories, task text, approved DEV information, tool permissions, evaluator and resource constraints.
- Codex Direct **may iterate**, run approved DEV checks and repair its solution. Do not compare BananLoop against an artificially restricted one-shot baseline.
- Freeze the task IDs, source/evaluator hashes, score direction, seeds, per-arm call/token/time ceilings and success definition **before** the run.
- Account for **all** BananLoop role calls (including proposal, selection and analysis), retries, input/output/cached tokens and any attributable monetary cost. Missing cost is `UNKNOWN`, not zero.
- Only DEV observations may guide changes; independent VALIDATION and terminal HOLDOUT results must not return to agent-visible memory or prompts.
- Execute untrusted candidate code only with tested isolation from host credentials, protected data and evaluator; no unisolated fallback.
- Export per-task outcomes and counts for **wins / ties / losses**, quality delta, token/cost efficiency, regressions, wall time and failures, including disqualified/incomplete arms.
- Attach the run manifest, exact code and evaluator SHAs, sanitized logs, reproducible commands, frozen final predictions and eligibility decisions.
- Report any material protocol mismatches as **ineligible**, not as wins. Results need not favor BananLoop to be honest evidence for a closed preview.

## Release gate

- [ ] Repeat paired head-to-head with actual independently reviewable receipts.
- [ ] Verify equal-budget and security eligibility, or clearly mark each pair's limitations.
- [ ] Produce a per-task comparison and limitations section.
- [ ] Link evidence and review the final report before owner approval.

The **benchmark gate is BLOCKED / INELIGIBLE**: there is no approved spend budget or verified isolated evaluator. Optional Codex token accounting is integrated into `main` through PR #81, but it is post-call receipt accounting—not a provider-side hard spending cap—and `main` bootstrap calls are not metered. Draft PR #87 adds opt-in bootstrap admission and receipt accounting; its exact head `8bd6987f7ebc7a4223c6ef0ba4993212074b50ac` passed hosted run [#41236491009](https://github.com/ObiVanBanan/BananLoop/actions/runs/41236491009) on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. PR #88 run [#41237333538](https://github.com/ObiVanBanan/BananLoop/actions/runs/41237333538) passed all four matrix jobs; the Windows suite reported **456 passed, 1 skipped**. PR #89 run [#41237375210](https://github.com/ObiVanBanan/BananLoop/actions/runs/41237375210) also passed all four jobs, including the clean-install smoke; Windows reported **457 passed, 1 skipped**. These PRs remain drafts outside `main`, and bootstrap accounting is not provider-enforced or USD accounting. Existing DEV fixture improvements demonstrate a workflow, not superiority over standalone coding agents. This blocks a v0.1 release recommendation.
