# Agent Capability Benchmark v0.2 — pilot and execution gates

## Status

**Not authorized for scored execution yet; not authorization for any Codex/Plus model call.** This is a design plan. No agent runs, dataset downloads, or benchmark claims are made by this PR.

## Relation to PR #135 and RPDI-14

The active integrated E2E pins `researcher`, `optimizer`, `independent_verifier` and `integration_verifier`. The legacy names in PR #125 are not currently guaranteed independent v0.2 roles. The RPDI-14 independent A/B gate is **separate and stricter** than this small role-capability pilot: signed run authorization, task rights, sealed evaluator, exact-SHA trust boundary, provider-verified accounting and enforceable ceilings must all pass. The numeric eight-call limit below is a **maximum proposal for a later, separately authorized, non-qualifying diagnostic**, not a launch approval or a release benchmark.

## Phase 0 — freeze the benchmark contract

Deliverables:

- the runtime's exact RoleRegistryV1 role definitions, registry digest and TeamManifestV1 digest (RPDI-09 API exists; each campaign still needs pins);
- role output contracts and capability profiles;
- stable Team Manifest format and digest;
- source and license register;
- public DEV case manifest and private VALIDATION/HOLDOUT manifest digests;
- independent evaluator identities and threat model;
- exact resource ceilings and accounting;
- pre-registered metrics, seeds, analysis plan, and eligibility rules.

Exit condition: all required identities are pinned and reviewed. A provisional role-catalog entry is not sufficient.

## Phase 1 — contract smoke

Create a tiny deterministic smoke set (up to five cases per role) to verify:

- schema validation;
- role dispatch and model binding;
- tool/path permissions;
- output envelope and artifact hashing;
- evaluator isolation;
- budget accounting and failure classification;
- reproducible report generation.

A smoke set is only an integration check. It is not a capability score and must not be described as statistically meaningful.

## Phase 2 — isolated role-capability pilot

Run only after the v0.2 Full architecture and Role Registry are stable enough to instantiate each role with its frozen contract.

### Codex Plus quota protection — default execution plan

The owner's Codex Plus subscription has a limited, variable usage allowance; there is no reliable fixed conversion from a benchmark invocation to subscription quota. **Do not assume a token cap equals a subscription-quota cap.** No autonomous recurring real-model benchmark runs are approved.

1. Run *all* contract, schema, security, budget, and report checks with deterministic fixtures, mocks/replays, and local Python first: **0 Codex calls**.
2. For the first live smoke, run **at most two short, single-case, single-seed trials total**, one at a time. This is a wiring test, not capability evidence.
3. If both succeed and the owner explicitly approves further quota use, extend the initial live pilot to **at most eight Codex model invocations in total across all roles**, inclusive of the first two trials, all retries, format-repair turns, helper roles, and any bootstrap calls.
4. Per batch: **maximum two Codex invocations**; check the subscription usage remaining before starting the next batch. A batch does not start when usage is unknown or a configured quota warning is active.
5. Default **one seed per live pilot case**. Do not do three/five repetitions or 30–100-case live suites on the Plus subscription by default. The pilot must report that uncertainty was not estimated.
6. Disable speculative parallel agent calls and automatic retries; record failures without silently rerunning them. If the adapter internally consumes multiple model requests, count all of them toward the ceiling. If reliable request counting or pre-dispatch cap enforcement is unavailable, **fail closed / stop after the current safe check**, rather than treating an estimate as a hard guarantee.
7. Heavy SWE-bench, MLS-Bench, or long-horizon AutoResearchExam runs are **not authorized** by this plan. They need their own budget, execution environment, and explicit owner decision.

The numbers above are **pilot design ceilings, not guaranteed Codex Plus quota consumption**. A single long Codex invocation may use more quota than several short ones. Stop on provider usage-limit warnings or uncertainty about remaining allowance. Never automatically reschedule a batch when quota renews.

### Target coverage, without committing Plus quota

| Current integrated role | Offline fixture/contract target | First live pilot | Later confirmatory target |
| --- | ---: | --- | ---: |
| `researcher` | 5–10 | requires separate authorization | independently powered |
| `optimizer` | 5–10 | requires separate authorization | independently powered |
| `independent_verifier` | 5–10 positive/negative protocols | requires separate authorization | independently powered |
| `integration_verifier` | 5–10 acceptance/denial cases | requires separate authorization | independently powered |

Legacy proposer/selector/diagnostic/pivot-advisor/bootstrapper cases remain design ideas; do not dispatch unregistered role IDs.

The first live pilot will **not necessarily cover all roles** under the eight-invocation ceiling. Uncovered roles remain `NOT_TESTED`; do not impute scores. Confirmatory suites and paired-seed repetition require a **separately authorized** budget/capacity profile (e.g. dedicated metered API funds or otherwise available quota). All attempts and missing usage must be reported.

The pilot can identify broken contracts and obvious weak areas. Do not use it to claim a universal role ranking.

## Phase 3 — integrated Full baseline

Before contribution/ablation experiments:

1. Build and verify the integrated v0.2 Full configuration.
2. Freeze its feature-flag hash, Team Manifest, role registry, models, prompts, permissions, and budgets.
3. Run the independent end-to-end baseline on frozen tasks.
4. Verify evaluator isolation, task eligibility, and complete receipts.
5. Publish the Full baseline report with failures and limitations.

This preserves the project decision: prove the complete system first; do not disable mechanisms prematurely.

## Phase 4 — role contribution experiments

Only after Phase 3 is valid, run preregistered paired counterfactuals:

- Full team;
- Full team with one role replaced by a fixed baseline/fallback;
- optionally, Full team with one role omitted where the workflow remains semantically valid.

Keep all other role models, prompts, task inputs, capabilities, evaluator, seeds, and resource ceilings fixed. Record when removing a role changes the task semantics; such pairs are not directly comparable.

Start with one-role-at-a-time estimates. Do not claim Shapley attribution from simple leave-one-out tests. Report per-task paired deltas and uncertainty.

## Phase 5 — separate system release gate

The release gate remains the independent comparison of BananLoop v0.2 Full against ordinary iterative Codex (or another preregistered baseline), with identical model/version where the comparison is intended to isolate the harness, same tasks, tools, permissions, evaluator, and matched resource limits.

The baseline may iterate, run approved DEV checks, and repair its solution. Account for all BananLoop role calls, including research, proposal, selection, analysis, retries, and orchestration overhead. This is a separate experiment from role-capability scoring.

## Hard execution gates

A scored run is blocked until all applicable items pass:

- [ ] The exact deployed RoleRegistryV1 and TeamManifestV1 are pinned and role outputs/permissions verified.
- [ ] Role/task/campaign permissions are enforced at execution time; derived roles cannot expand parent permissions.
- [ ] RPDI-11 evaluator verification and integration-verifier requirements are satisfied.
- [ ] RPDI-06 capability gateway is integrated with the actual worker runtime.
- [ ] Isolation is independently tested on the exact execution platform; no unisolated fallback is possible.
- [ ] The current Windows limitation is respected: do not run an isolation-required trial on a platform without a supported isolation provider.
- [ ] DEV, VALIDATION, and HOLDOUT data are physically/logically separated and hash-bound.
- [ ] License review is complete for every imported source and dependency.
- [ ] Model, prompt, adapter, role, team, source, evaluator, and runtime identities are pinned.
- [ ] Resource ceilings are known and enforced or the run is explicitly marked ineligible.
- [ ] Independent evaluator receipts and complete per-attempt logs are reproducible.
- [ ] Live Codex pilot has explicit owner authorization, a hard pre-dispatch call ceiling, no automatic retries or parallel launches, and a quota-availability check; unknown subscription quota means do not start the next batch.

## Stop conditions

Stop and mark the affected trial ineligible on:

- evaluator/oracle leakage;
- unapproved tool or path access;
- changed source/evaluator/task hashes;
- model or role identity drift;
- unbounded or unmetered resource use contrary to the preregistration;
- missing result/evaluator receipt;
- unsupported isolation backend;
- hidden/holdout data appearing in agent-visible prompts, memory, files, or tool results.

Do not repair the benchmark data, labels, or evaluator in place after seeing a result. Issue a new version and preregistration.
