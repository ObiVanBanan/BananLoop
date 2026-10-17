# ADR: bounded evidence-driven research portfolios

Date: 2026-10-16
Status: proposed for this implementation

## Context

Issue #42 extends the existing campaign supervisor. Planning must remain advisory: the core owns scope, evaluator identity, evaluator input, budgets, campaign state, acceptance, promotion, and recovery. Adaptive input may only include sanitized, allowlisted DEV records. Existing single-hypothesis adapters and old SQLite campaigns must continue to work.

## Decisions

### Contracts and selection

- Add a bounded portfolio response containing at most four structured candidates; a legacy `Hypothesis` response normalizes to a one-candidate portfolio.
- Each candidate carries a stable candidate ID, the existing hypothesis fields, evidence references (including explicit `INSUFFICIENT_EVIDENCE`), suspected failure mode, predicted observable, discriminating/falsifying test, affected area, scope eligibility, approximate work-unit/call cost, budget feasibility, rationale and explicit inference/uncertainty.
- The core validates structure, grounding, scope, evidence references, estimated cost, affordability and mechanism redundancy before implementation. Invalid candidates retain machine-readable rejection reasons. A candidate with a known same-mechanism no-effect history needs a new discriminator and valid refinement lineage.
- A pure deterministic selector orders admissible candidates using only saved DEV evidence, novelty/outcomes, falsifiability, affordability/cost and current strategy. Selection records each criterion and a plain-language explanation; it does not synthesize expected gains or causal facts.

### Durable state and recovery

- Persist a portfolio and selection as one append-only core event keyed by the existing proposal action ID and experiment ordinal. The action response/request already provide replay stability; no Git branch tree or second agent platform is added.
- Keep selected `Hypothesis` in the existing experiment column so current lifecycle, old rows, and downstream code remain compatible. Store the full portfolio and selection in durable research state/events and expose sanitized summaries through status/report.
- On resume, reconstruct the same portfolio and selection from the completed action/event. Do not replan or re-charge a completed plan. A pending/blocked portfolio does not enter implementation.

### Learning and plateau

- After actual DEV evaluation/decision, derive a bounded lesson with separate observed outcome, support/refutation/unknown interpretation, next discriminator, and evidence reference. Operational failures cannot be interpreted as scientific falsification.
- Before the plateau stop gate, permit one policy-selected materially different reframe only when the existing experiment, attempt, agent, evaluator and wall-clock budgets allow it. Do not change or reset limits. If no feasible candidate or budget remains, use the existing stop behavior and record the reason.

### Reporting and isolation

- JSON/Markdown reports include candidate alternatives, rejection reasons, selection criteria/reason, lineage and DEV learning, with budget costs shown as estimates or `unknown`.
- Only sanitized DEV summaries and generalized capsules enter adaptive state. Validation/holdout payloads, labels, private evaluator paths and secrets remain excluded. Deterministic core policy remains the only authority for ACCEPT/REJECT/promotion.

## Alternatives rejected

- New planner service, vector store, MCTS, Git branches per hypothesis, parallel execution, or a new UI: they exceed the bounded vertical slice and duplicate existing infrastructure.
- Replacing `Hypothesis` or rewriting campaign state: breaks adapter and SQLite compatibility without being necessary.
- Letting the agent select and authorize its own winner: conflicts with deterministic core-owned policy and integrity boundaries.
- Treating missing diagnostics or infrastructure errors as evidence against a mechanism: produces false scientific conclusions.

## Consequences

- Planning calls remain bounded (one proposal action per planning opportunity); the portfolio size cap prevents unbounded candidate generation.
- Old single-candidate agents remain usable with reduced alternative coverage.
- Reports and durable events grow by a bounded candidate summary per planning action.
- A local deterministic selector may block all candidates when evidence, scope, testability, or budget is insufficient; that is a valid terminal explanation.

## Verification boundary

CPU-only stub tests exercise malformed/duplicate/blocked portfolios, repeated no-effect, evidence gaps, resume identity, validation/holdout canaries, integrity rejection, legacy responses and two generic fixture domains. A frozen-fixture A/B comparison reports call/trial/mechanism/stop trade-offs without claiming universal quality gain. A local BananLoop end-to-end fixture exercises the operator CLI; no external model or real RAG evaluation is required.

