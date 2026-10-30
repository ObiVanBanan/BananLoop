# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `ad5209db519573b599fd67601a7359646ac8c2a0`
- Champion snapshot: `2d9b9c5400c55b5cf21ede61e5e2ce65509140a5`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Parse DN/PN integers with boundary-aware bilingual regexes and map material/joining aliases to canonical values before filtering the catalog.; delta=0.5625; failure=-
  - Candidate proof: tree=`8c02f64024895945a8f5e7c6f93fb702e7c31187`; diff=`4d8c65f54e41ab345d85d4235bf51dc5fa8fa726b2401a86b7fb4c2b0031b398`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `b4bb038b8aad0ff61aa34a44c1dc4ea6f6af6d0d67d518a9fa26cc244c840e06`; hash=`c129ad43f9673505b7602a19c4ad07840c0717906d4b64d96eb4512fcc22be7c`
- baseline:evaluate (dev): RECORDED; receipt=`16f71451d90e096d28468470c6e89f5e0f6ecab18572d082c3fd452494bff522`; binding=`b4bb038b8aad0ff61aa34a44c1dc4ea6f6af6d0d67d518a9fa26cc244c840e06`
- exp:1:evaluate (dev): RECORDED; receipt=`c7b8bb7ce4dbc038cb9552857d2a62ab9ea3c31785d4521f2f0bfc56617d2df5`; binding=`b4bb038b8aad0ff61aa34a44c1dc4ea6f6af6d0d67d518a9fa26cc244c840e06`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / ad5209db519573b599fd67601a7359646ac8c2a0

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 2d9b9c5400c55b5cf21ede61e5e2ce65509140a5

Aggregate: `{"quality": {"n": null, "unit": null, "value": 1.0}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|

## Research state

- Planning mode: `exploit`
- DEV non-improve streak: 0
- Failure classes: `{"DEV_IMPROVEMENT": 1}`
- Remaining budget: `{"agent_calls": 0, "attempts": 1, "dev_experiments_until_reframe": 0, "evaluator_calls": 0, "experiments": 0}`
- Recent lessons:
  - structured_regex: DEV evidence showed a measurable improvement signal.

### Planning portfolios

- Experiment 1 (exploit), selected `structured_regex`; trigger: DEV non-improve streak=0; Champion stagnation streak=0
  - `structured_regex` (structured_regex): selected; Selected by verified semantic portfolio comparison (advisory prediction).; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `alias_normalization` (alias_normalization): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units
  - `constraint_intersection` (constraint_intersection): admissible; Admissible; semantic portfolio comparison preferred another candidate.; estimated cost 1 agent calls + 1 evaluator calls + unknown work units

### DEV lessons

- Experiment 1 / `structured_regex`: DEV outcome DEV_IMPROVEMENT; primary delta 0.5625. Interpretation `supported`; The result is consistent with the stated mechanism. Next discriminator: DEV quality does not improve or a partial/ambiguous query returns an article. (evidence: exp:1).

## Resources

- Agent calls: 2
- Evaluation calls: 2
- Evaluation units: reserved 0 / None; reported 0
- Scientific experiments: 1
- Attempts: 1
- Action wall time: 27.352 s
- Resource reservations: 4 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- planning: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes