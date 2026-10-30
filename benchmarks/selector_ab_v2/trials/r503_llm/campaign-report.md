# BananLoop Campaign Report

- Outcome: **PAUSED_NEEDS_INPUT**
- Actual improvement: **NO_VALIDATED_IMPROVEMENT** (promoted candidate: False)
- Stop reason: **PAUSED_NEEDS_INPUT**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `0.4375` (delta `0.0`)
- Baseline snapshot: `a88d44bd3118d6dc03eea5bd0fade02fe2ae8392`
- Champion snapshot: `a88d44bd3118d6dc03eea5bd0fade02fe2ae8392`

## Evaluation protocol

- Dev: `0.4375` → `0.4375`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **REJECTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.0; failure=PRIMARY_NOT_IMPROVED
  - Candidate proof: tree=`f8d9d0ab58ceae3f787a8ded1c92b4b23937ccb8`; diff=`5233d66275916b2cd530e6b07730cc5f14b0e0792eb35ae25703831435cd0b97`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `5db2d31c9593c9888c071c23173c3931013ca6ea611aefa24fe93b70f4550fc1`; hash=`fca51c139d694a497043c483ff99e72edb7bf8353cd0230d11fef05b407f9d5c`
- baseline:evaluate (dev): RECORDED; receipt=`bfdd319865e1be55ce8ab362c3346a11738f2929f4e3f872bbea353838bc8b6c`; binding=`5db2d31c9593c9888c071c23173c3931013ca6ea611aefa24fe93b70f4550fc1`
- exp:1:evaluate (dev): RECORDED; receipt=`415e7840d7d79336b3b5043ff42eedb80376f977f090be4fda2cb07dcb95cbd8`; binding=`5db2d31c9593c9888c071c23173c3931013ca6ea611aefa24fe93b70f4550fc1`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / a88d44bd3118d6dc03eea5bd0fade02fe2ae8392

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 8193e462de6d975cd47147a34221e09acf94c08e

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|

## Research state

- Planning mode: `reframe`
- DEV non-improve streak: 1
- Failure classes: `{"DEV_NO_EFFECT": 1}`
- Remaining budget: `{"agent_calls": 0, "attempts": 1, "dev_experiments_until_reframe": 0, "evaluator_calls": 0, "experiments": 0}`
- Recent lessons:
  - constraint_intersection: DEV evidence did not meet the required minimum improvement.
- Recent DEV failure capsules:
  - [DEV_INSUFFICIENT_GAIN] Aggregate DEV failure; root cause not established: A DEV outcome is evidence about a candidate, not proof of a specific causal mechanism. Keep untested explanations uncertain.

### Planning portfolios

- Experiment 1 (exploit), selected `constraint_intersection`; trigger: DEV non-improve streak=0; Champion stagnation streak=0
  - `structured_regex` (structured_regex): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `alias_normalization` (alias_normalization): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `constraint_intersection` (constraint_intersection): selected; Selected by verified semantic portfolio comparison (advisory prediction).; estimated cost 1 agent calls + 1 evaluator calls + unknown work units

### DEV lessons

- Experiment 1 / `constraint_intersection`: DEV outcome DEV_NO_EFFECT; primary delta 0.0. Interpretation `unknown`; Available DEV evidence does not determine this mechanism. Next discriminator: The intersection accepts a row without explicit evidence for all four attributes or abstains on valid complete queries. (evidence: exp:1).
- Stop explanation: Plateau reached; no budget remains for one bounded reframe: BUDGET_EXHAUSTED

## Resources

- Agent calls: 2
- Evaluation calls: 2
- Evaluation units: reserved 0 / None; reported 0
- Scientific experiments: 1
- Attempts: 1
- Action wall time: 33.943 s
- Resource reservations: 4 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- planning: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes