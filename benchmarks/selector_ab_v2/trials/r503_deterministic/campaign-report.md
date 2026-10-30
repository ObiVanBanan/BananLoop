# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `3f3fa9a79166829d4a11f0b3815ee57551c2f5a0`
- Champion snapshot: `acf42328278cf2d69a45d948664565b4883ec5d0`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`dec01a35a3f5751fe4cd20c4941837fd1ed07b90`; diff=`f667dc2f4e2055a9be3e62f7554a8c978020ea80696ac6131f10af414b629bad`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `86008465ea1b273910e2525e2c73a53331a0f36a3a56f2efacf442828c31032c`; hash=`6692549f3729d1717a6ffa58c28dea7c645bcfee28c985947b5e6c67ae710123`
- baseline:evaluate (dev): RECORDED; receipt=`6f86e1b2c1450e648a8f3a8f19ef4f5343a8ddfecea496b761d139f8333b51cb`; binding=`86008465ea1b273910e2525e2c73a53331a0f36a3a56f2efacf442828c31032c`
- exp:1:evaluate (dev): RECORDED; receipt=`ea3612f9e6e6da963d0b0f983455cf13c038137b5ac626947365894ca00c04ce`; binding=`86008465ea1b273910e2525e2c73a53331a0f36a3a56f2efacf442828c31032c`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / 3f3fa9a79166829d4a11f0b3815ee57551c2f5a0

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / acf42328278cf2d69a45d948664565b4883ec5d0

Aggregate: `{"quality": {"n": null, "unit": null, "value": 1.0}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|

## Research state

- Planning mode: `exploit`
- DEV non-improve streak: 0
- Failure classes: `{"DEV_IMPROVEMENT": 1}`
- Remaining budget: `{"agent_calls": 1, "attempts": 1, "dev_experiments_until_reframe": 0, "evaluator_calls": 0, "experiments": 0}`
- Recent lessons:
  - constraint_intersection: DEV evidence showed a measurable improvement signal.

### Planning portfolios

- Experiment 1 (exploit), selected `constraint_intersection`; trigger: DEV non-improve streak=0; Champion stagnation streak=0
  - `structured_regex` (structured_regex): admissible; Ranked below selected candidate by the recorded deterministic criteria.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `alias_normalization` (alias_normalization): admissible; Ranked below selected candidate by the recorded deterministic criteria.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `constraint_intersection` (constraint_intersection): selected; Selected by deterministic ordering: strategy-aware mechanism novelty, DEV evidence, falsifiability, budget feasibility, estimated cost, then a neutral stable content hash (candidate ID excluded; hash is not a quality score).; estimated cost 1 agent calls + 1 evaluator calls + unknown work units

### DEV lessons

- Experiment 1 / `constraint_intersection`: DEV outcome DEV_IMPROVEMENT; primary delta 0.5625. Interpretation `supported`; The result is consistent with the stated mechanism. Next discriminator: The intersection accepts a row without explicit evidence for all four attributes or abstains on valid complete queries. (evidence: exp:1).

## Resources

- Agent calls: 1
- Evaluation calls: 2
- Evaluation units: reserved 0 / None; reported 0
- Scientific experiments: 1
- Attempts: 1
- Action wall time: 34.472 s
- Resource reservations: 3 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes