# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `f94b893df34913ade6b742df46eae106aa4e0a3b`
- Champion snapshot: `83751a3fb5515e545ef857e9a53e65b9b40d823b`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`3350ae4386f42a8de58886e9a01c77c4f5521ab1`; diff=`ebdc3af323b6165f2ff3ee28c8a9d61b146a44461452262537b7c3bdae48353d`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `807d2522df9f154dc727ebe91e342987609321421ee4d5d26ad90c152a9bd15d`; hash=`5ac363473916d820ae00376e7b07d64ec774c60d677af542981569672bba5eaa`
- baseline:evaluate (dev): RECORDED; receipt=`ec025c5b27dc766b61b05a88fe25d30e02d50285518f7136420dd620d13b8779`; binding=`807d2522df9f154dc727ebe91e342987609321421ee4d5d26ad90c152a9bd15d`
- exp:1:evaluate (dev): RECORDED; receipt=`570d30ab6b305fef7001c01d3ee68402d5d302e7d23852d38b635c0b0b93a74f`; binding=`807d2522df9f154dc727ebe91e342987609321421ee4d5d26ad90c152a9bd15d`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / f94b893df34913ade6b742df46eae106aa4e0a3b

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 83751a3fb5515e545ef857e9a53e65b9b40d823b

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
- Action wall time: 22.258 s
- Resource reservations: 3 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes