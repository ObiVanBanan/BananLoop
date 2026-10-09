# Issue #33 dogfood evidence

This log records local evidence for the acceptance experiments. `GOAL_MET` is a technical campaign decision; it does not establish evaluator trust or business-level success.

| Experiment | Status | Recorded evidence | Limits |
|---|---|---|---|
| E0 adversarial evaluator sanity | Technical controls passed; independent trust remains open | `tests/test_readiness.py::test_signal_sanity_requires_directed_separation_in_every_aa_run` accepts directed positive/negative separation. `test_signal_sanity_rejects_wrong_inverted_constant_or_missing` rejects four invalid controls: wrong declared metric, inverted direction, constant signal, and missing case. `test_unavailable_signal_sanity_is_explicit_and_does_not_fabricate_examples` keeps unavailable evidence explicit. `test_bootstrap_auto_repairs_false_goal_request` checks the bootstrap trust evidence path. The focused suite passed. | The wrong-objective control changes the declared metric contract; it does not establish semantic independence from a proxy objective. Since evaluator cases and labels are authored inside bootstrap, trust remains `UNVERIFIED`. |
| E1 repeated-space slug bugfix | Completed as a local `DEV_ONLY` campaign and replay | Disposable project baseline commit `713a534e2d6e8677b2023418f3138c450e7abaf5` fails the frozen `Alpha  Beta` → `alpha-beta` regression. `bananloop goal` ran with 1 experiment, 1 attempt, 2 agent calls, and 2 evaluator calls. Baseline quality was 0.0, champion quality 1.0, delta +1.0; mandatory regression check passed. Champion snapshot: `b189f365eae497c4933040577fb4b1fb82262b0a`. Changed path: `slug.py`; source project HEAD stayed at baseline and remained clean. Durable `explain` reports `technical_outcome=GOAL_MET`, `scientific_assessment=DEV_ONLY`, and `evaluator_trust=UNVERIFIED`. | DEV evaluator protocol SHA-256 `e876d39cb2f20f91305aa7e1edb09cd18c696d8272e29db3190e305eef36b339`; case/input manifest SHA-256 `5bf30d669bf5aec6c4f93daa6f00bd4f78d9e0da2f78d3099afdbd878faeec63`. Replay of `exp:1:evaluate` returned `REPRODUCIBLE`: metrics, required check, per-case result, artifact hashes, protocol identity, and runtime fingerprint matched. Replay receipt SHA-256 `6d3604c600ba1ea26fbc66664e8af7599ceab6cbc3ee5db777153d159eb8ca9d`. A separately exported champion patch from the earlier E1 run was applied to a scratch clone and its regression test passed. | The agent was a deterministic local command fixture, not a live model. Evaluator and oracle were bootstrap-authored; validation and holdout were not configured. This proves the bounded workflow and replay path, not evaluator independence or product value. |
| E2 retrieval | Not run; prerequisites unavailable | No fixed retrieval corpus/index/model and independent retrieval benchmark were provisioned. | No retrieval quality claim. |
| E3 performance | Not run | No pre-frozen representative workload and acceptance threshold were available for this turn. | No performance claim. |
| E4 AnimeDiff | Blocked by runtime prerequisites | No GPU/A1111 backend or frozen image benchmark was provisioned. | No image quality or runtime claim. |

## Remaining acceptance gaps

- M1 still needs independent reference provenance and smoke/full evaluator equivalence evidence.
- E0 needs an independently specified objective/provenance control before evaluator trust can be upgraded.
- E2, E3, and E4 remain unproven as detailed above.
- E1 uses DEV-only, bootstrap-authored evidence and does not satisfy validation/holdout acceptance.
- Issue #33 remains open; this log does not claim full acceptance.
