# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `99db4bb147ad0635ff91d0d95d4375bcdb53f91c`
- Champion snapshot: `064f012360479226dfc9fc9f4a8991909352ba95`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`bfbed969a2589989ec7b9d55deb3a7a820d9f358`; diff=`b4eeafd67e583bc7e92ff0482d571a44d66cf641d347941ddd6acef271a337a5`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `2cc25430e6db883034456febd95177493d95841a45906edb45ffdf5da56ceaf8`; hash=`7bc0902d199865a3e206b0294ab36d714670430e15cb818e897f23f5de30fbd3`
- baseline:evaluate (dev): RECORDED; receipt=`807b24828b98ac4dbca91c2e8cb7b92cf97079b272ef1be18068bd23a1efe814`; binding=`2cc25430e6db883034456febd95177493d95841a45906edb45ffdf5da56ceaf8`
- exp:1:evaluate (dev): RECORDED; receipt=`ea1f9d10851c56cdf15aa703116e517befffa9fc2837e3686edef57fa1c821aa`; binding=`2cc25430e6db883034456febd95177493d95841a45906edb45ffdf5da56ceaf8`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / 99db4bb147ad0635ff91d0d95d4375bcdb53f91c

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 064f012360479226dfc9fc9f4a8991909352ba95

Aggregate: `{"quality": {"n": null, "unit": null, "value": 1.0}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|

## Research state

- Planning mode: `exploit`
- DEV non-improve streak: 0
- Failure classes: `{"DEV_IMPROVEMENT": 1}`
- Remaining budget: `{"agent_calls": 0, "attempts": 1, "dev_experiments_until_reframe": 0, "evaluator_calls": 0, "experiments": 0}`
- Recent lessons:
  - constraint_intersection: DEV evidence showed a measurable improvement signal.

### Planning portfolios

- Experiment 1 (exploit), selected `constraint_intersection`; trigger: DEV non-improve streak=0; Champion stagnation streak=0
  - `structured_regex` (structured_regex): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `alias_normalization` (alias_normalization): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `constraint_intersection` (constraint_intersection): selected; Selected by verified semantic portfolio comparison (advisory prediction).; estimated cost 1 agent calls + 1 evaluator calls + unknown work units

### DEV lessons

- Experiment 1 / `constraint_intersection`: DEV outcome DEV_IMPROVEMENT; primary delta 0.5625. Interpretation `supported`; The result is consistent with the stated mechanism. Next discriminator: The intersection accepts a row without explicit evidence for all four attributes or abstains on valid complete queries. (evidence: exp:1).

## Resources

- Agent calls: 2
- Evaluation calls: 2
- Evaluation units: reserved 0 / None; reported 0
- Scientific experiments: 1
- Attempts: 1
- Action wall time: 33.065 s
- Resource reservations: 4 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- planning: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes