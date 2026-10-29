# Proposer A/B calibration — QA report

## Environment

- Branch: `codex/proposer-ab-calibration-v1`, based on `codex/calibration-task-context-champion-v1`.
- Base SHA: `009fe8f248631f4f7412eacdf272417925110e4`.
- Python: 3.12.3; Codex CLI: 0.164.0; Linux x86_64.
- Main checkout was left untouched; this work used an isolated worktree.
- The fixture scorer is SHA-256 `64e8a010ddfbc92d7ed4b56440b3036d5b5f76785f806e503bcc67f5eb194e4d`.
- Frozen proposer TASK.md SHA-256: `ebecd6b2774af89952474694a4b58f9a5214d4e48f670af22fda97b84bf74581`.

## Commands and deterministic tests

```bash
PYTHONPATH=src python -m pytest examples/calibration/proposer_ab/test_runner.py -q
PYTHONPATH=src python examples/calibration/proposer_ab/fixture/evaluator.py
PYTHONPATH=src python examples/calibration/proposer_ab/runner.py --output /tmp/proposer-ab-results
```

Unit tests: **4 passed, 0 failed, 0 skipped**. The CPU fixture printed
`quality=0.5000` for its starter solution. `compileall` and `git diff --check`
also completed without errors. The tests use a fake adapter; they make no model
calls.

## Frozen DEV context and isolation

Baseline/champion are both 0.5000. The request contains three synthetic prior
DEV records with preassigned synthetic fixture values 0.5002, 0.5003, and 0.5004. Their
deltas (+0.0002, +0.0003, +0.0004) are positive but below the configured
`min_improvement=0.001`; every record is marked rejected and champion remains
0.5000. The state builder reports `dev_non_improve_streak=0`,
`champion_stagnation_streak=3`, and the calculated fixture mode is `reframe`.
The experiment keys are `token-normalization`, `stopword-filtering`, and
`overlap-tie-break`.

The CPU DEV matcher contains eight cases across direct overlap, acronym,
synonym, negation, operation order, and quantifier distinctions. The candidate
workspace contains only `TASK.md` and `solution.py`; `evaluator.py` is excluded.
The evaluator is not invoked during the live proposer calls. No validation or
holdout material is included. Each A/B request was saved verbatim and contains
the full TASK.md and its SHA-256.

## Live A/B calls

Three independent calls were run per condition through the existing
`CodexAdapter(model="gpt-6-luna")`; implementer calls: **0**. The six
successful responses each contained three normalized candidates. All 18 passed
the core selector's admissibility gates. In every call the selector chose one
candidate by its deterministic strategy-aware ordering; the other two were
admissible but ranked below it. No candidate was implemented or scored, so
there is no measured candidate reward and no basis to claim either mode improves
quality.

| Arm | Selected mechanism keys | Calls | Wall time total |
|---|---|---:|---:|
| exploit | `negation-aware-overlap`; `technical-alias-expansion`; `synonym-lexicon-matching` | 3 | 51.13 s |
| reframe | `synonym-canonicalization`; `synonym-alias-lexicon`; `synonym-alias-expansion` | 3 | 44.45 s |

The specific selector reasons are in each `selection.json`: selected candidates
were chosen by “strategy-aware mechanism novelty, DEV evidence, falsifiability,
budget feasibility, estimated cost, stable candidate ID”; remaining candidates
were admissible and “ranked below selected candidate by the recorded
deterministic criteria.” There were no candidate rejection reasons. These are
portfolio selection decisions, not scientific ACCEPTED/REJECTED experiment
outcomes.

The initial sandboxed attempt failed before a model request because Codex CLI
could not initialize its app-server (`Read-only file system`). The exact stderr
and run records are retained under `results/preflight-readonly-failures/`. The
same six calls completed when run with the required Codex runtime access.

Usage: `CodexAdapter.execute` returns only the structured proposer response and
does not surface token usage. All six `run.json` files record usage as
unavailable/null; no token figures are estimated. Successful-call stderr/stdout
is not exposed by the adapter. Request, response, normalization, selection,
duration, and error artifacts are retained.

## Blind rubric

`results/blind_scores.json` stores the original candidate hypotheses under
anonymous IDs; condition mapping is separate in `results/key.json`. The
0–2 scoring sheet is intentionally left unscored pending blind review. The
criteria and anchors are in `results/rubric.json`: admissibility, specificity,
novelty, use of DEV evidence, and testability. Core admissibility is recorded
separately in selector decisions. Reviewer ratings for semantic specificity and
novelty are judgments, not objective truths or model rewards. No winner is
declared.

## Artifacts

`results/` contains the exact request and raw response for each successful
replicate; `normalized_portfolio.json`, `selection.json`, and `run.json`; the
sanitized workspace; blind scoring sheet, rubric, and separate condition key;
and stderr/run records from the preflight failures. There is intentionally no
campaign SQLite database: the paired prompts are synthetic request snapshots,
not campaign history.

## Result

- **PASS:** deterministic runner tests; frozen context counts and partial gains;
  control/treatment payload equality outside the two mode fields; task content
  and hash; evaluator absent from all six agent workspaces; six successful
  GPT-6 Luna proposer calls; raw and normalized artifacts recorded.
- **NOT TESTED:** candidate reward or implementation quality, blind rubric
  ratings, and whether either arm is statistically better. The harness is a
  proposer hypothesis calibration, not a campaign comparison or AutoResearchExam
  run.
