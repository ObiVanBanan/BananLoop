# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `857b943161ba724ef23d3c7359ab1ae459bfe75f`
- Champion snapshot: `36785ab5c14c86ae8e630cf970939133eaea3c2f`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Parse DN/PN integers with boundary-aware bilingual regexes and map material/joining aliases to canonical values before filtering the catalog.; delta=0.5625; failure=-
  - Candidate proof: tree=`0d709e280f4627b1f1b36285df19b94e33adda0f`; diff=`2d8ece95632a0257ff8a540e89a77fd2da0216478ef1b5e35939e7be56e17f43`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `6a510a86442a332abcc342c0caa5c52c3e147a65258db0dcc9ff287937b45e9b`; hash=`86ae7f96dcf17bdf7fb6230a2a3e833a52fe572b121a1873e88d645e0750e57e`
- baseline:evaluate (dev): RECORDED; receipt=`807577ccbcba4c0b96d7a0ea6c3a1dbe6eaeec52be03256f8102c5e0259dbf42`; binding=`6a510a86442a332abcc342c0caa5c52c3e147a65258db0dcc9ff287937b45e9b`
- exp:1:evaluate (dev): RECORDED; receipt=`b7481e097e961f5dc815fdfb514179724c236f2298bb31aacc353ac5e8841366`; binding=`6a510a86442a332abcc342c0caa5c52c3e147a65258db0dcc9ff287937b45e9b`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / 857b943161ba724ef23d3c7359ab1ae459bfe75f

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 36785ab5c14c86ae8e630cf970939133eaea3c2f

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
- Action wall time: 28.315 s
- Resource reservations: 4 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- planning: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes