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

As of 2026-10-31, PRs [#61](https://github.com/ObiVanBanan/BananLoop/pull/61), [#72](https://github.com/ObiVanBanan/BananLoop/pull/72), and [#75](https://github.com/ObiVanBanan/BananLoop/pull/75) are integrated in `main` at code SHA `44614ea22bc5967adb7ee607bce2472cc66487ee`. The push-triggered final-main workflow #706 passed on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12; every job reports `448 passed, 1 skipped`. Each job installed the project from the clean checkout with `pip install -e ".[test]"`. The local Windows full-suite attempt was blocked by the sandbox when child Git for Windows tried to create an MSYS named object; hosted CI on the exact code SHA is the completed full-suite and install evidence. A local selected-format history scan checked 4,232 reachable Git objects (1,542 blobs) and reported no private-key header, AWS/GitHub/Slack/Google/Stripe/OpenAI project-key pattern matches. It is not a comprehensive secret/privacy audit and does not include GitHub secret-scanning alerts. Token usage is measured from Codex terminal receipts when available, but this is **not** provider-enforced spending control and does not meter automatic bootstrap in `goal/start`. Seven local CPU-only research E2E tests passed on #75; these do not replace independently scored H2H or a hostile-code sandbox.

### Issue #64 isolation preflight — limited container smoke

On 2026-10-31 a disposable Docker container using official `python:3.13-slim` image digest `sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb` passed a no-network, read-only-root, no-host-mount smoke with all capabilities dropped, `no-new-privileges`, 2 CPU quota, 8 GiB memory limit, 64 process limit, writable 1 GiB `noexec` temporary filesystem, and child-process termination. No candidate, grader, model, or benchmark code was executed; no host files or credentials were mounted. This verifies Docker can enforce basic controls in this environment, **not** the full agent/evaluator boundary required by Issue #64. No disk quota, grader isolation, secret-negative tests against the real task adapter, or isolated model API proxy has been verified. See [the smoke report](reviews/are-isolation-smoke.md).

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

The **benchmark gate is BLOCKED / INELIGIBLE**: Issue #64 specifies a shared $3 pilot cap, but the current token receipt mechanism is post-call and cannot enforce a provider-side hard dollar ceiling; per-invocation USD attribution is also unverified. A basic Docker control smoke passed, but the isolated agent/evaluator boundary, disk quota and restricted model API proxy are not implemented or verified. Existing DEV fixture improvements demonstrate a workflow, not superiority over standalone coding agents. This blocks a v0.1 release recommendation; resume only after the prerequisites are met.
