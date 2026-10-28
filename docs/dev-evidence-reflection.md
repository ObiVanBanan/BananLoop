# DEV evidence and reflection (AutoResearchExam pilot)

This patch is based on `integration/project-readiness-20261021`. It does not merge the separate R0-R5 draft stack.

## What changes
- Rejected DEV candidates without evaluator `failure_signals` now persist one
  deterministic `FailureCapsule` with the observed aggregate DEV result and
  an explicit `UNKNOWN` cause; no extra model call or evaluator call.
- Subsequent proposer calls receive that capsule via the existing history and
  research-state pipeline. Existing dedicated failure-analyzer behavior remains
  unchanged when structured evaluator `failure_signals` are present.
- When `max_experiments=6` and `plateau_experiments=7`, the planning-mode
  reframe threshold is bounded to 4. This affects *planning only*; the
  budget, champion promotion gates and plateau termination are unchanged.
- Source diagnostics still require an explicit `agent_visible_diagnostics`
  allowlist. To let the agent distinguish zero-clipped reward candidates,
  configure e.g. `"agent_visible_diagnostics": ["primary_raw_metric"]`,
  **only after verifying the evaluator field is safe, aggregated DEV evidence**.
  This patch intentionally does not auto-expose arbitrary diagnostics.

## Trust boundaries and controls
The fallback uses only `agent_decision_view(spec, decision)`. It never
consumes raw failure examples, validation, holdout, or arbitrary evaluator
artifacts. Its `likely_mechanism="UNKNOWN"` avoids invented causal claims.
This is diagnostic evidence, not a proof that the named hypothesis is correct.

## Suggested verification
```powershell
python -m pytest tests/test_research.py tests/test_engine.py -q
python -m pytest -q
```

For A/B, use the same frozen evaluator for each arm, equal token and
wall-clock budgets, and a new untouched holdout. Record proposer and implement
usage separately. Measure accepted DEV score against cumulative tokens in
addition to final score.


## Hotfix V2: bounded proposal repair and evidence semantics
- **GVS5H-inspired fresh perspective:** after an all-invalid portfolio caused by
  correctable scope/grounding errors, the engine makes **at most one** additional
  proposer call in a disposable workspace. It supplies exact machine rejection
  reasons, the frozen approved editable scope, and the previous proposals.
  The same strict validator runs again, with no scope-policy relaxation.
- **Arbor-inspired evidence separation:** a synthetic aggregate-DEV capsule with
  `confidence=0` and `likely_mechanism=UNKNOWN` stays in the durable research
  history but is not treated as a causal `failure_signal` that the next
  hypothesis must explain. Actual high-confidence DEV capsules continue to
  participate in evidence-based planning.
- **Durability:** original and repaired portfolios are stored as separate
  `PORTFOLIO_DECISION` / `PORTFOLIO_REPAIR_DECISION` events. The repair
  action ID is stable (`exp:N:propose-repair`) and replays safely.
- **Cost:** the repair is strictly one extra agent call when otherwise the
  campaign would stop before executing a valid experiment. Token ceilings are
  still a separate, outstanding benchmark concern. Never run an unrestricted
  10-task A/B until explicit resource budgets are frozen.
- References:
  [GVS5H](https://github.com/slee-persis/GVS5H)
  (fresh-context workers with a shared ledger),
  [Arbor](https://github.com/RUC-NLPIR/Arbor)
  (Idea Tree with observed outcomes distinct from inferred insights).

### Provenance of fixes
The uploaded 2026-10-28 AutoResearchExam trace contained:
- Shortest Valid CI: 3 candidates with `files_or_area=["output/interval.py",
  "output helper modules"]`, all rejected by `OUT_OF_SCOPE`.
- ACT Tensor: 3 rejected candidates with references `exp:1` in
  `evidence_refs` but empty `hypothesis.prior_experiment_refs`; all rejected
  as ungrounded. The new bounded correction requests both fields explicitly.

Do not assume this hotfix improves official benchmark quality. First run the
targeted pytest suite, then short CPU-only smoke campaigns. Re-evaluate with
equal frozen compute, token budgets and no shared local memory across arms.
