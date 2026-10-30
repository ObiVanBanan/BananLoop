# BananLoop Selector Calibration v1

## Scope and baseline

The calibration used the six saved Proposer A/B responses in `../results/` and the frozen context produced by `runner.py`. No new hypothesis generation or full BananLoop cycle was run. The target branch HEAD was verified as `daafecbb4fb32640b5c9144b138365745b489d1c`.

The historical A/B artifacts and original `selection.json` decisions are retained under `../results/`. The rename replay reconstructs the old final-ID ordering from each saved decision's unchanged criteria and checks that it reproduces the saved winner before testing ID permutations.

## A. Bug reproduction

All **6/6** portfolios changed selected candidate content under at least one ID-only relabeling. Candidate data, hypothesis content, evidence references, and eligibility were unchanged. Reversing candidate order changed **0/6** decisions under the old selector.

| Run | Original winner | Winner after renaming | Changed? | Reason |
|---|---|---|---:|---|
| exploit-1 | `negation-aware-overlap` | `charlie` (`compositional-order-scoring`) | yes | Earlier criteria tied; final candidate ID decided. |
| exploit-2 | `technical-alias-expansion` | `charlie` (`negation-aware-overlap`) | yes | Earlier criteria tied; final candidate ID decided. |
| exploit-3 | `synonym-lexicon-matching` | `charlie` (`acronym-expansion-matching`) | yes | Earlier criteria tied; final candidate ID decided. |
| reframe-1 | `synonym-canonicalization` | `charlie` (`ordered-composition`) | yes | Earlier criteria tied; final candidate ID decided. |
| reframe-2 | `synonym-alias-lexicon` | `charlie` (`acronym-expansion-matching`) | yes | Earlier criteria tied; final candidate ID decided. |
| reframe-3 | `synonym-alias-expansion` | `charlie` (`order-sensitive-composition`) | yes | Earlier criteria tied; final candidate ID decided. |

`charlie` is the renamed identifier assigned to the candidate chosen by the old lexical tie-break; the exact mappings and criterion scores are in `replay.json`.

## B. Patch

`select_candidate` now uses SHA-256 of the candidate's normalized content with `candidate_id` excluded as its final tie-break. This digest is neutral and has no scientific interpretation. Existing ordering by strategy-aware novelty, DEV evidence, falsifiability, feasibility, and estimated cost remains unchanged. Scope, budget, evidence, redundancy, and grounding checks are unchanged. No proposer prompt, evaluation, promotion, validation, holdout, or token-budget rules changed.

## C. Regression tests

- `python -m compileall -q src`: passed.
- `python -m pytest tests/test_research.py -q`: **30 passed**.
- `python -m pytest tests/test_engine.py -q`: **48 passed**.
- `python -m pytest examples/calibration/proposer_ab/test_runner.py -q`: **4 passed**.
- `python -m pytest -q`: **316 passed**.

The first sandboxed full-suite attempt had one environment failure because a registry test writes under `/home/.bananloop`; rerunning with the needed filesystem access passed. No product code was changed to work around that environment restriction.

## D. Historical portfolio replay

| Run | Old winner → new winner | Old/new substantive criteria `(strategy novelty, evidence, falsifiability, feasibility, cost)` | Mechanism changed? | Gates unchanged? | Ineligible selected? |
|---|---|---|---:|---:|---:|
| exploit-1 | negation-aware → compositional-order | `(-1,0,1,1,-4)` → `(-1,0,1,1,-4)` | yes | yes | no |
| exploit-2 | technical-alias → technical-alias | `(-1,3,1,1,-4)` → `(-1,3,1,1,-4)` | no | yes | no |
| exploit-3 | synonym-lexicon → acronym-expansion | `(-1,3,1,1,-3)` → `(-1,3,1,1,-3)` | yes | yes | no |
| reframe-1 | synonym-canonicalization → ordered-composition | `(1,3,1,1,-3)` → `(1,3,1,1,-3)` | yes | yes | no |
| reframe-2 | synonym-alias → synonym-alias | `(1,3,1,1,-3)` → `(1,3,1,1,-3)` | no | yes | no |
| reframe-3 | synonym-alias-expansion → order-sensitive-composition | `(1,0,1,1,-3)` → `(1,0,1,1,-3)` | yes | yes | no |

The final tie field changes from ID to content hash; full scores, hashes, decisions, and gate comparisons are in `replay.json`. A changed mechanism is a policy change, not evidence of improved science.

## E. DEV implementation smoke

Each hypothesis used an independent copy of the same baseline. The Luna implementer received only `TASK.md`, `solution.py`, and its approved hypothesis; its workspace did not contain `evaluator.py` or expected answers. The frozen evaluator ran afterward outside the workspace.

| Hypothesis | Mechanism | DEV reward | Calls | Tokens | Wall time |
|---|---|---:|---:|---|---:|
| synonym-canonicalization | synonym canonicalization | 0.500 | 1 | unavailable | 26.8 s |
| ordered-composition | ordered composition | 0.500 | 1 | unavailable | 16.0 s |
| negation-polarity | negation-aware matching | 0.625 | 1 | unavailable | 24.1 s |

Token usage was unavailable from the adapter response; it was not inferred. The scorer has eight cases and accuracy steps of 0.125, so these values are smoke checks, not reliable generalization estimates. Hypotheses were fixed before seeing DEV results.

## F. Conclusion

1. **Name dependence:** removed for distinct candidate contents because the ID is excluded from the final key.
2. **Determinism:** preserved; repeated runs and candidate-order permutations give the same selected content.
3. **Scientific quality:** the replay does not establish that new winners are better. DEV smoke is too small to support that conclusion.
4. **Limits:** eight DEV cases, coarse reward resolution, and unavailable token telemetry constrain interpretation.
5. **Further evaluator:** the neutral hash is sufficient to fix arbitrary ID bias. A new hypothesis-quality evaluator is not justified by these measurements; scientific ranking needs stronger outcome evidence.

## Reproduction

From the repository root:

```bash
export PYTHONPATH=src
python examples/calibration/proposer_ab/replay_selector.py
python -m pytest tests/test_research.py -q
python -m pytest tests/test_engine.py -q
python -m pytest examples/calibration/proposer_ab/test_runner.py -q
python -m pytest -q
```

The live Luna implementation script is `../run_dev_smoke.py`; its recorded requests, responses, diffs, workspaces, evaluator output, and metrics are included in the analysis archive.
