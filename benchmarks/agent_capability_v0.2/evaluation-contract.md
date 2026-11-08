# Independent evaluator contract

**PR #135 binding:** the active observable role IDs are `researcher`, `optimizer`, `independent_verifier`, and `integration_verifier`, as pinned in an individual campaign's `RoleRegistryV1` and `TeamManifestV1`. Historical names in the table in §3 are capability rubrics, not standalone runtime roles. System-level blinded scoring and run authorization belong exclusively to RPDI-14's `benchmarks/v02_full` boundary. This contract provides **no** independent run approval or scorer implementation.

## 1. Trust boundary

The agent worker and evaluator are separate trust domains.

- The agent receives only the case's authorized `agent_input`, context references, role output contract, and granted tools.
- The evaluator owns hidden expected outputs, gold labels, hidden tests, private source annotations, and holdout inputs.
- The evaluator is immutable for the duration of a run and identified by a content SHA-256.
- The agent cannot read, modify, invoke, or infer private evaluator internals through mounted paths, environment variables, logs, or tool output.
- The evaluator does not trust agent-reported scores, success claims, or self-assigned labels.
- The evaluator consumes a verified artifact/result envelope and returns a signed or content-addressed receipt.

The public repository must not contain private oracle data. Store private data in a separate access-controlled evaluator store. Publish only opaque IDs and hashes in the run manifest.

## 2. Evaluator types

Use the strongest objective evaluator available for each task:

1. **Deterministic structured checks:** schema, exact IDs, field constraints, allowlists, path/scope compliance.
2. **Hidden tests / official verifier:** code execution, regression, benchmark-native checks.
3. **Source verification:** claim-to-passage support, exact values, source coverage, duplicate detection.
4. **Oracle regret:** compare a selector's choice against independently measured outcomes for all admissible candidates.
5. **Human rubric:** use for inherently qualitative dimensions such as hypothesis usefulness or pivot materiality.
6. **Paired counterfactual:** compare team outcomes with a frozen role replacement/removal after the Full baseline exists.

A model-based judge is supplementary. If used, it must be blinded to model/provider/role identity, use a frozen rubric and prompt, and be calibrated against human-labeled examples. It cannot be the only judge for a release-critical claim.

## 3. Role-specific oracles

| Role | Evaluator-owned oracle |
|---|---|
| Campaign bootstrapper | Independently authored expected campaign contract, protected paths, metric direction, and evaluator fixture |
| Researcher | Gold claims, exact supporting passages/source IDs, expected coverage set, and contradiction labels |
| Proposer | Human-reviewed rubric plus measured outcomes from independently executed candidate hypotheses |
| Selector | Outcome table for every candidate, unavailable to the selector; best admissible candidate under the frozen objective and budget |
| Implementer | Hidden tests, regression suite, scope checks, official task verifier, artifact integrity |
| Diagnostic | Fault-injection cause labels, allowed evidence references, acceptable discriminating probes, and action-utility labels |
| Pivot advisor | Human-reviewed rubric for materiality, evidence, risk, scope, and preservation of invariants |

For selector cases, candidate outcomes must be measured before the selector run in isolated workspaces. The selector must not receive the oracle table. The evaluator uses the same frozen objective and constraints to calculate regret.

For proposer cases, do not equate a polished explanation with a useful hypothesis. Execute every candidate that is within the pre-registered candidate cap when feasible. If not all candidates can be executed, mark the case as rubric-only and do not include it in realized-utility metrics.

For diagnostic cases, preserve the distinction between scientific failure, implementation defect, evaluator defect, infrastructure/provider failure, budget/timeout, and unknown cause.

## 4. Split and leakage rules

- **DEV:** agent-visible feedback is allowed; only DEV results may enter adaptive research memory.
- **VALIDATION:** evaluator-only until the declared validation checkpoint; no result or derived label may enter agent prompts, memory, or tools before the run is frozen.
- **HOLDOUT:** terminal evaluator-only split; never exposed to the agent or adaptive memory.
- Do not create a holdout by merely renaming public DEV files.
- Use source timestamps/model knowledge cutoffs and fresh task variants where contamination is plausible.
- Hash every split manifest and ensure no case or near-duplicate crosses splits.
- Do not put validation/holdout answers, test patches, evaluator code, or labels in public GitHub files.

## 5. Outcome classification

Every attempt must end in one of the result-schema statuses:

- `COMPLETED`: agent and evaluator completed normally.
- `AGENT_ERROR`: agent failed its own protocol or execution.
- `EVALUATOR_ERROR`: trusted evaluator failed; not a scientific failure.
- `INFRA_ERROR`: provider, worker, sandbox, network, or infrastructure failure.
- `POLICY_VIOLATION`: attempted or completed forbidden action.
- `DISQUALIFIED`: protocol mismatch or ineligible environment/budget.
- `INCOMPLETE`: run did not reach a valid terminal state.

Never convert evaluator or infrastructure errors into evidence that a hypothesis is false. Never silently drop failed or incomplete trials.

## 6. Security and eligibility

A scored run is ineligible unless:

- the exact runtime isolation backend is tested on the execution platform;
- no local/unisolated fallback is possible for an isolation-required grant;
- evaluator, oracle, validation, holdout, and credentials are inaccessible from the agent worker;
- role/task/campaign permissions are intersected at the Tool Gateway;
- output artifacts are bound to the job, current worker fence, and content digests;
- budget ceilings and resource accounting are enforced or limitations are explicitly marked;
- all identities in the manifest are frozen and complete.

RPDI-06 is merged on the current main, but its documented platform boundary still matters: an isolated grant on Windows fails closed where no supported isolation provider is configured. Do not treat a Linux isolation test as proof of Windows isolation.

## 7. Cost and resource accounting

Record input, output, cached tokens, model calls, tool calls, evaluator calls, wall time, and actual provider cost where available. Include retries and all role calls.

- Missing cost is `UNKNOWN`, not zero.
- Subscription quota consumption is not automatically equivalent to zero economic cost.
- Token receipts are not a provider-enforced hard spend cap.
- If the provider cannot enforce a ceiling, record that as a limitation and apply an eligibility rule before the run.

## 8. Human adjudication

Human adjudicators receive anonymized outputs and a frozen rubric. They must not see model name, provider, arm, or prior score while labeling. Record disagreement, adjudicator IDs in a private record, and adjudication resolution. Do not use a judge model to create gold labels for the same cases it later scores.
