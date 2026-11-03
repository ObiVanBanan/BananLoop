# Agent Capability Benchmark v0.2 — specification

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

## 4. Provisional role capability matrix

The IDs below are benchmark-facing placeholders. RPDI-09 must reconcile them with the authoritative Versioned Role Registry before the dataset is frozen.

| Role ID | Contract boundary | Primary measurements | Preferred task source |
|---|---|---|---|
| `campaign_bootstrapper` | Turn a goal/repository snapshot into a valid measurable campaign contract; cannot approve its own scope or evaluator trust | Schema validity, metric/direction correctness, scope and protected-path correctness, evaluator command validity, missing-input detection | BananLoop-owned fixtures |
| `researcher` | Find and synthesize evidence from explicitly allowed sources; must cite source IDs and distinguish fact from inference | Claim precision/recall, evidence coverage, citation entailment, unsupported-claim rate, duplicate rate | AutoResearchBench |
| `proposer` | Produce bounded, distinct, falsifiable hypotheses grounded in supplied DEV evidence | Falsifiability, novelty/mechanism diversity, evidence grounding, predicted observable, cost calibration, downstream realized utility | MLS-Bench / AutoResearchExam-derived briefs + owned cases |
| `selector` | Select among gate-approved candidates using only permitted evidence and budget | Oracle regret, top-1/top-k hit, pairwise ranking, cost-aware utility, invalid-choice rate, calibration | Purpose-built frozen portfolios |
| `implementer` | Implement one approved hypothesis inside the granted scope | Resolved/pass rate, hidden-test pass, regression rate, scope violations, patch quality, time/tokens/cost | SWE-bench Verified; CPU-feasible MLS-Bench-Lite |
| `diagnostic` | Classify a failed or ambiguous run and propose a discriminating next probe | Root-cause top-k accuracy, evidence precision, false-falsification rate, probe information gain, next-action utility | BananLoop fault-injection + sanitized incidents |
| `pivot_advisor` | Prepare a material alternative approach for human review; cannot authorize or execute the pivot | Evidence-groundedness, materiality, scope/risk awareness, clarity of trade-offs, human-review agreement | BananLoop-owned supervised fixtures |

### Current adapter compatibility map

These aliases were observed in the current `main` Codex adapter at the benchmark's starting revision. They are not the v0.2 Role Registry:

| Provisional benchmark role | Current adapter alias |
|---|---|
| `campaign_bootstrapper` | `bootstrap` |
| `proposer` | `propose` |
| `selector` | `select` |
| `diagnostic` | `analyze` |
| `pivot_advisor` | `pivot_advice` |
| `implementer` | `implement` |
| `researcher` | no dedicated legacy alias identified |

The adapter and profile configuration do not currently provide a complete versioned role registry. Do not treat this table as proof that all roles are independently deployable in v0.2.

## 5. Role-specific evaluation rules

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
