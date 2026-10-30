# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `b718370c501f090e841f79ad8c8b23f7a5767c39`
- Champion snapshot: `21824460d16351c7af24a15f57cf13ff29ec2e63`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`fd63cc3b4fb5416e84355b7534c08775a7c49d7d`; diff=`fbab2e24e6f72c7820de547ca8c928f308b8c8c2a47045e565740d74b6a33d65`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `ea5c8017f2113cd949258b5cd3a7963ffbc4a00d95ef823c98883ccb384d7954`; hash=`8f29c29dcf03cd2b6374a0485c27b6a8ce602e6ce409d0ecb5d503d97e94d391`
- baseline:evaluate (dev): RECORDED; receipt=`b0efc24e3ff82e0d01cd8a114799d32fe538f09a262b28179360fbb665deda9d`; binding=`ea5c8017f2113cd949258b5cd3a7963ffbc4a00d95ef823c98883ccb384d7954`
- exp:1:evaluate (dev): RECORDED; receipt=`71494c8ec312b9e88bd6871784cfcc3692ae2f4ed3fe6d80f958b0048fb8c117`; binding=`ea5c8017f2113cd949258b5cd3a7963ffbc4a00d95ef823c98883ccb384d7954`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / b718370c501f090e841f79ad8c8b23f7a5767c39

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 21824460d16351c7af24a15f57cf13ff29ec2e63

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
- Action wall time: 27.360 s
- Resource reservations: 4 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- planning: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes