# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `227744b2e5f4761f94c509a173d83c7ef990dade`
- Champion snapshot: `48f238e8ff9be2fb970d5da882776e64246e7dfe`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`7a01eff4b747ca86d9e26d0509b77aee4c636536`; diff=`fc914233d8f2a5f71b63752b68dd184cbcf4b8af0e1120df00e579a1547ab97c`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `ebe4b58f93efe4e438e45824af21c3862f74824a308b6c6444072c33caf7eb2b`; hash=`93b30336a4ba6f91e9cb92246918fc1c89d403d52df46618fee8bf6b0cdc1c63`
- baseline:evaluate (dev): RECORDED; receipt=`97af73deec5dac8f0fd978255400150fbfd7a2b502f95beec96a1ca87f00a07a`; binding=`ebe4b58f93efe4e438e45824af21c3862f74824a308b6c6444072c33caf7eb2b`
- exp:1:evaluate (dev): RECORDED; receipt=`9d55d0dd859e2e68fdc5ac16fca479f4da8bcf5aa6b1b5574e70d271b77d3d77`; binding=`ebe4b58f93efe4e438e45824af21c3862f74824a308b6c6444072c33caf7eb2b`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / 227744b2e5f4761f94c509a173d83c7ef990dade

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 48f238e8ff9be2fb970d5da882776e64246e7dfe

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
- Action wall time: 26.914 s
- Resource reservations: 3 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes