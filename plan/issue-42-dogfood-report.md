# Issue #42 deterministic CLI dogfood results

Run date: 2026-10-18. BananLoop source: PR #43 head `bc8bfb83508c76caf41413d238985ebc65723841`. Python: 3.13.1. Both campaigns used the real BananLoop CLI and engine, a deterministic local command agent, and fixed CPU evaluators. No hosted model, Ollama, RAG service, network API, or paid inference was used.

| Fixture | DEV baseline → accepted candidate | Candidates | Scientific trials | Agent calls | Evaluator calls | Result |
|---|---:|---:|---:|---:|---:|---|
| Value bugfix (`run_dogfood.py`) | quality 0 → 1 | 2 | 1 | 2 | 2 | `GOAL_MET`; selected `z-direct-fix` |
| Synthetic binary classification (`run_classifier_dogfood.py`) | balanced accuracy 0.7509803922 → 1.0 on the same 128-row frozen eval split | 2 | 1 | 2 | 2 | `GOAL_MET`; selected `z-additive-boundary` |

The classifier experiment changes only `model.py`. The evaluator adapter and eval split are protected; the candidate agent cannot see `data/eval.csv`. The score is computed by executing the evaluator on baseline and accepted candidate, not by a policy outcome map.

Focused verification on this source checkout: `tests/test_codex_adapter.py`, `tests/test_research.py`, and `tests/test_engine.py` — 63 collected, all passed. The test process was run with `PYTHONPATH` explicitly pointing to this checkout's `src/`; a first diagnostic run had imported an older editable install and was discarded. GitHub Actions run #379 passed Linux Python 3.11/3.12/3.13 and Windows Python 3.12 for code head `bc8bfb8`. At report time, CI for the follow-up evidence commit `bb29672` had not appeared yet.

`examples/issue42/compare.py` remains a deterministic selector policy replay using fixed synthetic outcome maps. It is not an empirical A/B campaign comparison. The two CLI campaigns above establish separate deterministic end-to-end domains, but do not establish that a real Codex model improves either task, nor universal quality gains. A real model run and independent fixed-budget A/B remain outstanding.

Logs captured outside the repository during this run:

- `outputs/bananloop-pr43-focused-20261018-1747-latest.log`
- `outputs/bananloop-pr43-dogfood-20261018-1747-latest.log`
- `outputs/bananloop-pr43-classifier-20261018-1747-latest.log`

The report contains only aggregate synthetic fixture metrics and no private project data.
