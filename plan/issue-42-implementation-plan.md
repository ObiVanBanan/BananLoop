# Implementation plan: Experimentation Intelligence v2

Research facts: [research-planner-v2-research.md](research-planner-v2-research.md)
Architecture: [research-planner-v2-design.md](research-planner-v2-design.md)

## Milestone 1 — portfolio contracts and deterministic policy

- Add bounded candidate/portfolio/selection/lesson contracts in `src/bananloop/contracts.py`.
- Add normalization for legacy single-hypothesis results, validation/rejection reasons, deterministic evidence-aware selection, affordability checks, and bounded DEV lesson derivation in `src/bananloop/research.py`.
- Add unit cases in `tests/test_research.py` for repeated no-effect, insufficient evidence, duplicate vs. justified refinement, deterministic tie-break, bad scope/cost, and no fabricated gain.
- Done when identical saved inputs yield identical selection and malformed candidates are rejected before side effects.

## Milestone 2 — durable engine integration and recovery

- Extend proposer task instructions to request a bounded portfolio while keeping legacy output accepted in `src/bananloop/engine.py` and Codex/Pi adapter prompts.
- Validate the whole portfolio before implementation; persist all candidates, rejections, selected ID and rationale through core events/research state, retaining the selected legacy `Hypothesis` column.
- Reconstruct the same decision on resume without another proposal call or charge. Integrate strategy selection before plateau stop without extending any approved limit.
- Add engine tests for no-effect reframing, budget blockers, dedup before implementation/evaluation, exact resume replay, DEV-only lessons, holdout canaries, champion snapshot and integrity behavior.
- Done when existing accept/reject/promotion and stop invariants remain unchanged except for a fully budgeted justified reframe.

## Milestone 3 — report, deterministic fixtures and A/B evidence

- Render portfolio/lineage/lessons/selection/blocked alternatives in JSON and Markdown in `src/bananloop/report.py`.
- Add a tiny deterministic CLI fixture and instructions under `examples/issue42/`; include materially different bugfix and CPU-performance toy domains without domain-specific planner code.
- Compare legacy single proposal and portfolio policy on the same frozen inputs, seed, evaluator and budgets. Record proposals, duplicates, trials, mechanisms, strategy switches, calls, champion and stop reason in `plan/issue-42-ab.md`.
- Run focused tests, full pytest, compile/import and existing deterministic CLI dogfood; report environment limits and any CI runs accurately.
- Done when the report gives a bounded explanation, issue-specific proof covers required scenarios, and all generated benchmark outputs identify their deterministic fixture scope.

## Delivery

- Review diffs for compatibility, budgets, unsafe path handling and DEV/validation/holdout leakage.
- Create a draft PR referencing #42 and #28 with the audit, design choice, tests, dogfood and A/B evidence. Do not merge or deploy.
- Preserve pre-existing untracked files and worktrees.

