# Autonomous Benchmark v0.1 — Codex agent implementation handoff

**State: DRAFT / OFFLINE ONLY. Do not run models or Harbor evaluation during implementation. Owner's updated 2026-11-15 TIME-ONLY decisions in [decision memo](AUTONOMOUS-BENCHMARK-V01-DECISIONS-2026-11-15.md) supersede any older 250-call or random-sampling wording below.** This is a *non-RPDI-14 diagnostic*, not a replacement for RPDI-14 or independently certified benchmark. Do not modify RPDI-14 documents. Prepare a PR and return exact SHA/tests/blockers before asking owner to authorize a real series.

## Goal
Compare BananLoop v0.2 Full with *iterative* Codex Direct on 12 curated eligible AutoResearchExam v1.1 tasks with randomized arm order under matched conditions. Separate short Board Games secondary diagnostic. **All comparative judging happens once after the entire benchmark has stopped and both arms are frozen**. The owner will upload immutable artifacts to Google Drive for a subsequent ChatGPT/Astra review. During runs, numerical DEV feedback may be used by either arm under the same rules; the judge is never in the loop.

## Fixed decisions
- 12 DISTINCT ARE tasks, 2 arms/task, 24 arm runs, each **3600 seconds wall clock end-to-end**, including BananLoop Exploration + verification + Optimization child, model and grader waiting, cleanup. One persistent goal and repository per arm; Codex may iteratively edit/run DEV/read feedback. No artificial iteration/call limit: stop on wall time, subscription unavailability, verified evaluator constraints or normal completion.
- **Time-only owner decision 2026-11-15:** no hard model-call/iteration cap; maximum 3600 research seconds per arm. Subscription use is observed, not forcibly limited to 250 calls; no separately billed API. Details in the decision memo.
- Target overall wall clock ~10 hours, NOT a fairness constraint. 24 arm-hours need at least 3 fully resourced concurrent arm slots to approach 8h before overhead. Begin concurrency=1 and prove capacity; do not oversubscribe host/Harbor/provider rate limits. Eligible tasks declare 8 CPUs, 14 GiB RAM and 150 GiB storage each, plus possible grader resources. Use resource measurements, no fabricated ETA.
- Board Games = ten operator-composed questions, **600 seconds per arm** only in a separately approved secondary run manifest and time budget. Of ten questions only bg-01 is the upstream DiscoveryBench question. No shared composite score or budget.
- AutoResearchExam primary outcome = frozen per-task numeric evaluator result. Pin direction and score transform; aggregate paired wins/ties/losses, differences, uncertainty, time-to-best, failures and token/call usage. Never average incompatible raw scores or call CovType static-price proxy an official task score. No private holdout claim without real evidence.
- Freeze exact task instructions/catalogue revision, initial tree, evaluator/DEV image and contract, Full flags, model/reasoning/tool settings, budget and deterministic selection seed before any arm receives a task. Randomize arm order without sharing state; no arm can see other's outputs or feedback.

## Phase 0 — offline eligibility and freeze
1. Verify local Newton baseline commit c00fdbbd7d3ac79b9f27b489fd9f4793288f15bb; this was ahead of remote branch at last check. Do not pretend it is pushed. Branch should incorporate it before the final run freeze.
2. Inventory official ARE catalogue and task rights. Existing benchmarks/autoresearchexam/task-selection.json has TEN tasks only, not 12. Ten task-pinned adapters exist but NOT twelve approved ready job descriptors. Expand actual official/task-version/metric/adapter/fixture support to at least 12 eligible tasks, or stop with PREP_BLOCKED_INSUFFICIENT_TASKS. Sampling must be reproducible from a recorded eligible set and seed, not cherry-picked after results.
3. Validate official image, public DEV, submitted output contract, exact grader feedback and per-task metric orientation. The owner-curated set of 12 is frozen in the updated decision memo; randomize only the paired arm order. Pin source, SHA of assets and frozen protocol. Preserve proper rights. No model calls.

## Phase 1 — build new autonomous runner
1. Add new benchmarks/autonomous_v01 runner, not a silent conversion of the current ARE series_runner.py. Existing series_runner enforces five iterations, 3600s and only dispatches preprepared Harbor jobs. Build actual task/arm preparation and Codex Direct coding adapter.
2. Real Codex Direct uses subscription-backed CLI with editable sandbox, tests, revisions, and persistent single goal across iterations. Do NOT use benchmark_live_codex no-tools one-shot role diagnostics as the baseline.
3. BananLoop Full must execute integrated Exploration -> independent protocol/verification -> Optimization child; count all role/model calls, not only Optimization. Entire cycle shares one 3600s deadline; never reset after Exploration. Use a benchmark-only, frozen manifest-bound preauthorized pivot policy, not an unconditional HIL bypass. Freeze immutable Full feature flags.
4. Isolate two arms' filesystem, contexts, state and logs. Equal initial repo/task, approved DEV/evaluator contract, model capabilities and allotted resources. Record disclosed tooling differences.
5. Persist every verified DEV candidate as immutable source bytes, metric receipt, timestamp and SHA. On timeout choose best VERIFIED in-deadline checkpoint, not latest untested work. If none, mark NO_VERIFIED_CANDIDATE. If grader submission is uncertain, mark UNKNOWN and do not resend blindly.
6. Replace the obsolete hard 250-call reservation enforcement with durable observational metering of subscription calls/tokens/errors/unknowns, and prove deadline enforcement. No paid API.
7. Make fake-agent offline tests for timeout/kill, full-source isolation, cap under concurrency, checkpoint ordering and authenticity, crashes, unknown grader responses, resume and distinct final artifacts.

## Phase 2 — dry-run and explicit approval
1. Run 24 fake-arm offline jobs, zero model/grader calls. Confirm all 12 task adapter preparers produce valid frozen job config, and score same task with same pinned rubric for either arm.
2. Measure host capacity and run concurrency probes without provider calls. Set realistically supportable max parallel arms and disclose if 10h target cannot be met. Ensure failure cannot cause duplicate model/grader usage.
3. Freeze final manifest and SHA-256 lock; produce preflight report READY_NON_RPDI_DIAGNOSTIC or PREP_BLOCKED_* with exact blockers.
4. DO NOT start real models, Harbor or public validation without separate owner approval of exact frozen manifest and run command.

## Phase 3 — real ARE run (later, separately authorized)
Run 12 tasks x 2 arms with independently isolated state. Each has one persistent goal and maximum 3600s. Keep all calls, checkpoints, DEV outcomes, provider receipts, failure states, and per-task scores. Stop at research wall-time deadline or unavailable subscription and freeze results. Never show comparisons or AI Judge outputs during run, never change task pool based on early results.

## Phase 4 — Board Games (optional, separately funded)
Run 10 x 2 arms, 600s per arm, only after a separate owner-approved secondary run manifest and time budget. Historical pilot in benchmarks/BOARDGAMES-LIVE-PILOT-2026-11-12.md / PR #144: 10 tasks x five iterations = 50 BananLoop calls; successful tasks ~2 minutes each; PR #144 reports 417,496 provider-reported tokens. This is historical usage evidence, not a new hard call cap.

## Phase 5 — final archive and retrospective
After ALL arms reach frozen terminal states, write summary.json, experiment-manifest.json, per-task score/usage/checkpoint records, source snapshots, complete Codex and BananLoop logs, ledgers and errors, index with SHA-256 for every artifact. Explicitly flag incomplete campaigns. Exclude auth secrets, private grader data, API keys and any unredacted credentials. Archive safely, verify integrity and give owner a file to upload to Google Drive. Later ChatGPT/Astra checks conclusions once, with the two arms anonymized when a qualitative judgement is needed; official numeric metric stays primary.

## Tests / acceptance criteria
- Exactly the 12 owner-curated official non-proxy tasks with actual adapters and source pins.
- Frozen manifest, two iterative arms, equal end-to-end time, same goal/input/DEV/evaluator and disclosed differences.
- Full workflow duration enforced at 3600 seconds per arm, including Exploration and its Optimization child. Provider turns/tokens are durably observed where reported; there is no hard call or iteration cap. NO paid API or fallback.
- Deadline chooses verified best; timeout/unknown/no-result are faithfully preserved without replay.
- No model/grader calls in CI, offline prep or benchmark archive; explicit real-run authorization.
- Post-run-only judging; no accidental intermediate sharing/cross-arm data leakage.
- Board Games separate budget and separate report. RPDI-14 remains untouched.
- No fabricated 10h wall guarantee, official score, holdout or statistical significance claim.

## Recommended first coding batch
Implement Phases 0–1 **offline only**, beginning with verified eligibility + task adapter inventory, protocol freeze, budget and checkpoint tests. Return PR, exact SHA, test outputs, and a concrete list of remaining blockers. No real experiment starts as part of this assignment.
