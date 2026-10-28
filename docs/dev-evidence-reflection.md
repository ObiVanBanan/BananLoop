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
