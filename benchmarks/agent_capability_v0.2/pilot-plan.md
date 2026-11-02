# Agent Capability Benchmark v0.2 — pilot and execution gates

## Status

**Not authorized for scored execution yet.** This is a design plan. No agent runs, dataset downloads, or benchmark claims are made by this PR.

## Phase 0 — freeze the benchmark contract

Deliverables:

- authoritative role IDs and versions from RPDI-09;
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

Initial pilot targets:

| Role | Pilot target | Confirmatory target |
|---|---:|---:|
| Campaign bootstrapper | 5–10 | 30–50 |
| Researcher | 5–10 | 30–50 |
| Proposer | 5–10 | 30–50 |
| Selector | 5–10 portfolios | 30–50 portfolios |
| Implementer | 5–10 | 30–50 |
| Diagnostic | 10–15 | 50–100 |
| Pivot advisor | 5–10 | 20–30 |

Use at least three seeds for pilot cases where model stochasticity applies. Confirmatory runs should use a preregistered repetition count, normally five or more for stochastic comparisons where budget allows. Preserve every attempt.

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

- [ ] RPDI-09 supplies the authoritative Versioned Role Registry and Team Manifest.
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
