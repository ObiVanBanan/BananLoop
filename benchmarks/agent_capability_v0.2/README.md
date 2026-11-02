# BananLoop Agent Capability Benchmark v0.2

**Status:** DESIGN ONLY — not executed, not a leaderboard, not release evidence.  
**Target:** BananLoop v0.2 Full. v0.1 results are excluded from this benchmark.

## Purpose

Measure each model-backed role independently, then measure its contribution to the integrated team. Keep three questions separate:

1. **Capability:** can this role perform its assigned contract on a frozen task?
2. **Contribution:** does this role improve the integrated team's result when it is present?
3. **Team performance:** does BananLoop v0.2 Full outperform a fair, iterative baseline under matched conditions?

A role score is not a system score. Scores from different roles or incompatible task metrics must not be pooled into one leaderboard number.

## Design status and architecture boundary

The benchmark is designed for the v0.2 **Dynamic Team Composition + Versioned Role Registry** workstream. Role IDs in `role-catalog.yaml` are provisional benchmark identifiers, not an implementation of the Role Registry. RPDI-09 owns the authoritative registry and Team Manifest contract; this benchmark must bind to those exact versions and hashes before any scored run.

The current `main` adapter exposes legacy task-role strings such as `bootstrap`, `propose`, `select`, `analyze`, `pivot_advice`, and `implement`. These are compatibility aliases only. They must not silently become the canonical v0.2 role IDs.

## Three benchmark layers

| Layer | Unit under test | Main question |
|---|---|---|
| Agent Capability | One role, one frozen role contract | Can the role do its own job? |
| Agent Contribution | Full team vs. controlled role replacement/removal | Does the role add measurable value? |
| Team Performance | BananLoop v0.2 Full vs. fair iterative baseline | Does the integrated system work better? |

The contribution/ablation layer is deliberately scheduled **after** the integrated Full configuration has a valid baseline. It must not be used to justify disabling features before the Full system has been proven.

## Initial source strategy

- **Researcher:** AutoResearchBench for objectively checkable scientific-literature retrieval.
- **Implementer:** SWE-bench Verified for real repository fixes; selected CPU-feasible MLS-Bench-Lite tasks for algorithmic implementation.
- **Proposer:** curated task briefs from MLS-Bench / AutoResearchExam, with frozen context and downstream execution of every candidate where feasible.
- **Selector:** a purpose-built portfolio set with independently measured outcomes for every candidate; the selector never sees oracle outcomes.
- **Diagnostic:** purpose-built fault-injection and sanitized historical incident cases.
- **Bootstrapper:** purpose-built repository/goal fixtures with independently authored expected campaign contracts.
- **Pivot advisor:** supervised-only cases; approval authority remains with the human/Core policy.

See [dataset-sources.md](dataset-sources.md) for source fit and licensing restrictions.

## Repository layout

```text
benchmarks/agent_capability_v0.2/
├── README.md
├── spec.md
├── role-catalog.yaml
├── manifest.template.yaml
├── evaluation-contract.md
├── dataset-sources.md
├── pilot-plan.md
├── cases/
│   └── README.md
└── schemas/
    ├── case.schema.json
    └── result.schema.json
```

## Non-negotiable rules

- Freeze the role registry version, Team Manifest digest, model/provider/version, prompt hash, output contract, capability profile, evaluator digest, source revision, seeds, and budgets.
- Give each role only its approved context and tools. A derived specialization cannot gain permissions beyond its parent role/task/campaign grant.
- The evaluator/oracle is outside the agent workspace. No expected answer, hidden tests, validation labels, or holdout payloads are committed to this public repository.
- The agent cannot grade itself. The evaluator cannot be modified by the agent.
- Infrastructure failures, evaluator failures, policy violations, and scientific failures are distinct outcomes.
- Unknown token/cost values remain `UNKNOWN`; they are never recorded as zero.
- A capability or security-policy violation makes the trial ineligible; it is not averaged away.
- Do not run scored trials until the gates in [pilot-plan.md](pilot-plan.md) are met.

## Current deliverable

This PR establishes the benchmark contract, provisional role map, schemas, source register, and execution gates. It intentionally does **not** download or vendor third-party datasets, create hidden gold labels, run models, or claim benchmark results.
