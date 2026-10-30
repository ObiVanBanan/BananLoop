# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `b805dcd3d2b4eae187edd251c3f7e8672bc7a4ab`
- Champion snapshot: `097276713a6031f41869253e7b19e47218026cac`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`d3de1e1271b4b62662c23610287c1daeb9b6fae1`; diff=`0dd17b76add62ef760aed73e2ccf4e05cb54912bdfae7900c36d4d09419a5fc3`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `168c4180c75c4be00ab52ef6ed1a0181c4d1650d6d69f7ea1a05f097d6e4e9f6`; hash=`f06f8e9d39f0eb078e7ea808108f15307210b3b1f3575674000646c106cf79f9`
- baseline:evaluate (dev): RECORDED; receipt=`e36fbd12fa611487b3c6a1fa1919978e4e7ecc58d65886aae3f526d8b9f413f1`; binding=`168c4180c75c4be00ab52ef6ed1a0181c4d1650d6d69f7ea1a05f097d6e4e9f6`
- exp:1:evaluate (dev): RECORDED; receipt=`b5598439ce2889d6c59353ca38c4167955aee72813e8bda1062d2464f9b5abe7`; binding=`168c4180c75c4be00ab52ef6ed1a0181c4d1650d6d69f7ea1a05f097d6e4e9f6`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / b805dcd3d2b4eae187edd251c3f7e8672bc7a4ab

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 097276713a6031f41869253e7b19e47218026cac

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
- Action wall time: 30.479 s
- Resource reservations: 3 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes