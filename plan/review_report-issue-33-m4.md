# Issue #33 M4 review: evidence-aware planning

## Implemented

- Research state selects `explore` after a DEV guard/check rejection, reframes when separate DEV experiments repeat a suspected cause at sufficient confidence, and otherwise retains the existing streak-driven strategy. Infrastructure failures do not increase the scientific streak or trigger cause-based reframe.
- Planning state records its rationale, latest scientific DEV outcome, evaluator evidence strength, minimum expected call/work-unit cost, remaining call budgets, and whether the bounded reframe check can actually run. Deadline, evaluator-call reservations, final-only reserve, agent-call budget, and evaluation work units are checked explicitly.
- Proposal context tells the agent to separate operational failures from scientific evidence, cite evidence IDs, include observation/cause confidence/proposed test, and state when the cause is unknown. It keeps validation/holdout payloads and tier names out of the adaptive context.
- Hypothesis selection is durably recorded with planning mode, rationale, evidence strength, estimated minimum cost, relation, prior ordinals, and the deduplication decision.
- Exact duplicate detection now requires the same mechanism, expected effect, and overlapping normalized file/area scope. Same mechanism names with a different expected effect or distinct area are allowed; `refine/retry/supersede` still require a matching predecessor reference. Canonical areas and candidate diff signatures appear in research history so future proposals can compare the actual test surface.
- Durable reports attach evidence/action IDs and diff signatures to the structured observation, suspected cause/confidence, and proposed regression test.

## Verification

- Focused research, engine, CLI and accounting tests pass, including validation/holdout non-leakage, operational-failure separation, repeated-cause reframing, distinct hypothesis acceptance, available-work-unit and deadline checks.
- Full local suite and cross-platform CI are pending for this increment.

## Remaining

M4 implementation is partial pending dogfood evidence. E0/E1/E2/E3 are not claimed as completed experiments, and E2/E4 runtime prerequisites remain unprovisioned. M1 independent reference provenance and smoke/full evaluator equivalence remain open. Issue #33 stays open.
