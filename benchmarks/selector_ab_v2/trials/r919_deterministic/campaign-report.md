# BananLoop Campaign Report

- Outcome: **GOAL_MET**
- Actual improvement: **CHAMPION_IMPROVED** (promoted candidate: True)
- Stop reason: **GOAL_MET**
- Final assessment: **DEV_ONLY**
- Goal: Read TASK.md and improve analog matching on DEV
- Primary metric: `quality`
- Baseline → final: `0.4375` → `1.0` (delta `0.5625`)
- Baseline snapshot: `2d2a41892aa4503362a5ba1dbc195ead8be2a030`
- Champion snapshot: `8d7bff79c1ce32800371b2b9ef6b92ce25edb885`

## Evaluation protocol

- Dev: `0.4375` → `1.0`
- Validation: not configured
- Final holdout: not configured

## Experiments


## Evaluator signal sanity

- Status: `legacy/unspecified`; evidence level: `legacy/unspecified`
- Readiness receipt SHA-256: `-`
- #1 **ACCEPTED** — Extract possible values for each of the four attributes independently, intersect those constraints with catalog rows, and return only a unique surviving article.; delta=0.5625; failure=-
  - Candidate proof: tree=`96008690e91b017378b23a5094a4d29936605db3`; diff=`969bcd0264a5166216e9a0759ef20495bac5efb02540e2d23cc43459dad3bae3`; paths=`["matcher.py"]`

## Evaluator provenance

- Binding: `0e968f6dbf6f9168fe9f74a797a8d564c10120e89a077e62d48fa9b0ed21da16`; hash=`ab6b2b9629252326074ef9e23ecdbedd968158a44203b2071f4ad432f07fc645`
- baseline:evaluate (dev): RECORDED; receipt=`2683c7e3bcab8160f422043e2fde64df4798e22e679f7efb1b5fcce942cacade`; binding=`0e968f6dbf6f9168fe9f74a797a8d564c10120e89a077e62d48fa9b0ed21da16`
- exp:1:evaluate (dev): RECORDED; receipt=`1d0c7bfa0b40b0533c442dce1894d4f4ebee99635435b6acf7ff98ce456f7433`; binding=`0e968f6dbf6f9168fe9f74a797a8d564c10120e89a077e62d48fa9b0ed21da16`

## Attempt accounting

- Total attempts: 1
- Scientific trials: 1
- Stored experiment counter: 1 (matches classified rows: True)
- Ungrounded / redundant / no hypothesis: 0 / 0 / 0
- No changes / implementation blocked: 0 / 0
- Pre-evaluation rejects / infrastructure failures: 0 / 0
- Active / cancelled / other: 0 / 0 / 0

## Measurements (operator-only)

### dev / experiment None / 2d2a41892aa4503362a5ba1dbc195ead8be2a030

Aggregate: `{"quality": {"n": null, "unit": null, "value": 0.4375}}`; reported units: `None`

| Case | Repetition | Input SHA-256 | Measurements |
|---|---:|---|---|
### dev / experiment 1 / 8d7bff79c1ce32800371b2b9ef6b92ce25edb885

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
- Action wall time: 25.993 s
- Resource reservations: 3 (0 open)
- Unknown action outcomes: 0


### Calls by stage

- implement: 1 agent calls, 0 evaluator calls, 0 unknown outcomes
- evaluate: 0 agent calls, 2 evaluator calls, 0 unknown outcomes