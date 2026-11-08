# Agent Capability Benchmark v0.2 — specification (adapted to PR #135)

**Integration status:** design-only, not scored. The source-of-truth v0.2 runtime bindings are pinned `RoleRegistryV1` / `TeamManifestV1` in [PR #135](https://github.com/ObiVanBanan/BananLoop/pull/135). The design originally described seven proposed roles; four separately pinned roles are exercised by the current integrated offline E2E. The seven-role rubrics below are retained as *capability rubrics* and must not be presented as seven runnable v0.2 agents. Independent system-level A/B is governed by [RPDI-14](../../docs/rpdi/rpdi-14-independent-benchmarks.md), not this suite.

## 1. Objective

Evaluate the capabilities of individual BananLoop v0.2 roles without confusing role quality with model quality, orchestration quality, or the final team's outcome.

The benchmark has two distinct experimental questions:

- **Role capability:** given a fixed role contract, input, permissions, model configuration, and budget, how well does the agent complete the role's task?
- **Role contribution:** once the integrated Full team has been measured, how much does that role change the end-to-end result under a controlled counterfactual?

The first is a role-isolated evaluation. The second is a team experiment and must not be inferred from the first.

## 2. Unit of evaluation

The primary unit is:

`(case_id, role_id, role_version, model_id, prompt_sha256, seed)`

Every scored record also binds:

- BananLoop source commit SHA;
- Role Registry version and digest;
- Team Manifest digest;
- role output-contract version and digest;
- campaign/task capability grant digest;
- evaluator ID, version, and digest;
- dataset source revision and case digest;
- runtime image / sandbox backend and version;
- resource-budget profile;
- repetition seed.

If any identity changes, it is a different experimental condition. Do not combine it with the original run without an explicit paired protocol.

## 3. Role isolation protocol

For a role-capability trial:

1. Start from a fresh, immutable task snapshot.
2. Construct the role request from an allowlisted input/context manifest.
3. Give the agent only the tools and paths granted to that role and task.
4. Do not provide another agent's hidden reasoning, private intermediate state, or result unless that item is explicitly part of the role's contract.
5. Capture the exact structured output, tool trace, artifacts, resource use, and termination status.
6. Run the independent evaluator outside the agent workspace.
7. Store the evaluator receipt and artifact digests in the result record.

The role is tested against its **contract**, not against a prompt string alone. Model, prompt, toolset, role permissions, and output schema are separately versioned.

## 4. Current PR #135 runtime role crosswalk

The canonical role names below are **observed in the integrated E2E**, not a global, always-on roster. Each trial must load its actual `RoleRegistryV1`, freeze its `TeamManifestV1` and bind to the role's immutable definition, model, prompt digest and capability grant.

| Pinned E2E role | Capability | Scoring emphasis |
| --- | --- | --- |
| `researcher` | Evidence-informed Exploration / hypothesis proposal | grounded claims, falsifiability, correct use of prior refutations, permissible follow-ups |
| `optimizer` | Optimization candidate implementation | independent pass rate, regression and scope violations, resource usage |
| `independent_verifier` | Evaluator protocol approval/refusal | rejected invalid proposals, accepted valid proposals, trust/pin integrity |
| `integration_verifier` | Independent candidate/integration verification | false acceptance/rejection, champion/promotion invariants |

The historical PR #125 labels map as follows:

- `campaign_bootstrapper` → Core preflight, not an independently scored model;
- `proposer` → a *capability* of `researcher` in the current E2E;
- `selector` → Core's evidence-linked scheduling/selection in the current E2E (not necessarily an LLM role);
- `implementer` → implementation *capability* tested through `optimizer`;
- `diagnostic` → retained future capability rubric, no current role assumption;
- `pivot_advisor` → supervised pivot discussion; human authorization stays external.

Do not invent a 1:1 model-role mapping when BananLoop's Core performs the decision.

## 5. Current role oracles and legacy capability rubrics

For the four observed roles, freeze independently authored oracles:

- **Researcher:** independently verified source claims, prior-evidence consistency, valid falsifiable next steps.
- **Optimizer:** sealed regression/acceptance evaluator and role scope; never trust self-reported quality.
- **Independent verifier:** independently authored sound/unsound evaluator protocols, expected approve/reject, pin integrity.
- **Integration verifier:** independently authored valid/invalid candidate receipts, expected promotion/denial and CAS invariants.

The following historical sections are **deferred capability test designs**, not evidence of active canonical role IDs or live benchmark results.

### Historical capability rubrics from PR #125 (do not dispatch as role IDs)

### 5.1 Campaign bootstrapper

Input: public goal, repository snapshot, approved context, and task constraints.

Evaluator checks:

- required fields and schema;
- primary metric and direction against the task's independently authored contract;
- evaluation command and smoke command correctness;
- editable/protected path boundaries;
- DEV / VALIDATION / HOLDOUT separation;
- whether essential missing information is surfaced instead of invented;
- whether evaluator access and hidden data remain protected.

A syntactically valid but semantically wrong campaign specification is a failure.

### 5.2 Researcher

Input: research question and an allowlisted source corpus/search interface.

Evaluator checks claims individually against the frozen source set:

- supported claims / all material claims (precision);
- recovered gold facts / all gold facts (recall);
- citation points to the exact supporting source and passage;
- no unsupported numerical claims;
- uncertainty and conflicting evidence are represented;
- duplicate or irrelevant sources do not inflate coverage.

Use deterministic fact checks where possible. A model judge may supplement but cannot replace source-level verification.

### 5.3 Proposer

Input: goal, task specification, and sanitized DEV evidence only.

Evaluator checks:

- hypothesis is specific and bounded;
- mechanism differs materially from prior candidates unless explicitly declared as a refinement;
- prediction names an observable outcome;
- falsification condition can actually reject the hypothesis;
- evidence references exist and support the stated rationale;
- estimated effort/calls are calibrated.

A rubric score alone is insufficient. Where feasible, execute **all** admissible proposals in separate matched workspaces and measure realized outcomes. This reveals whether the proposer produces hypotheses that work, not merely hypotheses that sound plausible.

### 5.4 Selector

Input: identical frozen portfolio and allowed DEV evidence for each replicate.

Before the selector is run, independently execute each candidate in separate isolated workspaces to obtain evaluator outcomes. Keep those outcomes in the evaluator-only oracle. The selector sees only the evidence it is allowed to see; it must not see future candidate rewards or holdout outcomes.

Primary metric: **selection regret** relative to the best admissible candidate under the same budget and guardrails. Also report top-1 selection, invalid choices, ties, budget violations, and calibration. A candidate that violates scope or hard guardrails is not an eligible oracle winner.

### 5.5 Implementer

Input: a frozen approved hypothesis or issue, repository snapshot, role capability grant, and visible tests.

Evaluator checks:

- task-specific hidden tests / official verifier;
- regression tests;
- protected-file and scope compliance;
- artifact integrity and reproducibility;
- resource consumption.

Report resolved/pass rate and regression rate separately. Do not reward a patch that passes visible tests while violating scope or hidden checks.

### 5.6 Diagnostic

Input: sanitized run trace, evaluator status, DEV-only metrics/diagnostics, and allowed artifacts.

Each case needs a separately authored root-cause label and acceptable evidence set. The agent must distinguish at least:

- scientific hypothesis failure;
- implementation defect;
- evaluator defect;
- infrastructure/provider failure;
- budget/timeout termination;
- insufficient evidence / unknown cause.

Primary metrics: root-cause top-1/top-k accuracy, evidence precision, false-falsification rate, and utility of the proposed next probe. Treating an infrastructure failure as evidence against a scientific mechanism is a high-severity error.

### 5.7 Pivot advisor

Input: approved goal/scope, current architecture summary, and sanitized DEV evidence.

The output is a proposal for human review only. Score materiality, evidence grounding, compatibility with existing invariants, risk disclosure, and clarity of trade-offs. The agent must not grant itself authority to approve, enqueue, or execute a pivot.

## 6. Contribution and team evaluation

Do not infer contribution from isolated capability scores.

After the integrated v0.2 Full configuration has a valid paired baseline:

- compare Full against a controlled role replacement or role omission;
- keep tasks, model versions for all remaining roles, tool permissions, budgets, seeds, and evaluator fixed;
- measure paired task-level deltas and uncertainty;
- record interaction effects where roles depend on each other;
- keep all feature flags and the Full configuration hash in the run manifest.

The initial contribution design is a one-role-at-a-time counterfactual, not a full Shapley estimate. Any broader attribution claim requires a preregistered factorial design.

The separate release gate remains the fair comparison of BananLoop v0.2 Full against an ordinary **iterative** baseline. Do not compare against an artificially one-shot baseline.

## 7. Reporting

Publish one report per role and task family. Each report must include:

- eligible / ineligible / incomplete trial counts;
- task-level metrics and raw evaluator outcomes;
- per-domain aggregates, with task counts;
- confidence intervals or paired uncertainty where sample size permits;
- model, prompt, role, Team Manifest, source, evaluator, and runtime hashes;
- input/output/cached tokens, tool/evaluator calls, wall time, and actual cost;
- failures, retries, fallbacks, policy violations, and unknown measurements.

Do not average raw scores from different task metrics. Do not produce one cross-role leaderboard score. If a normalized score is used, its anchor and mapping must be frozen per task family and documented.

## 8. Statistical minimums

- A 5-case-per-role smoke is a protocol check only, not evidence of capability.
- The subscription-limited *live smoke* uses **one seed per case**, at most **two invocations before review**, and no more than **eight total Codex invocations** in the entire explicitly approved first pilot (including retries, internal model turns, and bootstrap). Repeat-seed statistical estimates are **not available** from this smoke. All offline fixtures may run without Codex.
- Confirmatory role suites should target 30–50 independently authored or sampled cases per role; diagnostic should target 50–100 because its failure taxonomy is broader. **Do not run these suites against the default Codex Plus pilot budget; authorize a separate capacity profile first.**
- Use paired comparisons where the same task can be run under two conditions.
- Report per-task deltas and uncertainty; do not pool heterogeneous rewards into a single winner.
- Freeze the task list, splits, evaluator, seeds, model, prompt, budget, and analysis plan before confirmatory runs.

These counts are planning targets, not a claim that a specific sample size guarantees statistical power.

## 9. Out of scope for role capability scoring

Orchestration, Team Composer, Agent Factory, Model Router, Scheduler, budget enforcement, capability enforcement, worker fencing, and resume/idempotency are control-plane mechanisms, not LLM agents. They require separate deterministic conformance, security, and fault-injection suites. A model must not receive credit for a control-plane invariant enforced by Core.
