# BoardGames live pilot report — 2026-11-12

The selected ten-task pilot completed all five configured iterations for each task. There were 50 GPT-6 Luna calls via the existing ChatGPT provider. Provider monetary cost is UNKNOWN.

Limits per task were one hour, five iterations, eight model calls, a 10,000-token per-call reservation, and approval for up to six calls. The successful task runs finished in roughly two minutes each.

## Interpretation

This was a public, operator-composed pilot, not a sealed or official DiscoveryBench score. Task 01 is the upstream question; tasks 02–10 are pilot questions. All nine pilot questions were evaluated against the registered Board Games dataset. The results are associational and do not support causal claims. All campaigns ended at `ITERATION_LIMIT`, meaning the cap was reached; it does not mean the search converged or passed a benchmark threshold.

Task 01 ran from source revision `362507a`; tasks 02–10 ran from `37a0f96` with a clean tracked tree. Earlier failed runs are included in the archive to preserve the history of the reservation and Research Memory fixes.

## Final campaign IDs

- `bg-01-strategies-tournament-win` — `c-3b046f6957ff39c5f314c20dd57e9191` (5 calls; ITERATION_LIMIT)
- `bg-02-tournament-frequency-win` — `c-086e7962bf536007deea9471a3290db7` (5 calls; ITERATION_LIMIT)
- `bg-03-complexity-success` — `c-8bdd380b98fd0f8e3266e1ed9065946d` (5 calls; ITERATION_LIMIT)
- `bg-04-complexity-preference-win` — `c-62f7f002dfa31c22c54292528156a1a7` (5 calls; ITERATION_LIMIT)
- `bg-05-duration-engagement` — `c-02ca601426ba1955f47ccf6aae52353a` (5 calls; ITERATION_LIMIT)
- `bg-06-genre-count-engagement` — `c-47e3b8be8f41e7ceee1c814d990bb63f` (5 calls; ITERATION_LIMIT)
- `bg-07-game-diversity-engagement` — `c-36fea6e822caf6850639fb1c89854aa8` (5 calls; ITERATION_LIMIT)
- `bg-08-borrowing-strategies` — `c-8049bad14013965d9e4a38cac79df6ad` (5 calls; ITERATION_LIMIT)
- `bg-09-forum-tournament-frequency` — `c-be9b664d527ce6b302756a63a141881d` (5 calls; ITERATION_LIMIT)
- `bg-10-age-win-rate` — `c-f6cddd7c1ffb0204c0be87ebd5dd9940` (5 calls; ITERATION_LIMIT)

## Artifacts

Full compressed logs, campaign SQLite state, per-task results, corrected and failed series, plus the separate AutoResearchExam child archive are in `/home/sonik/bananloop-benchmark-env/runs/boardgames-live-artifacts-2026-11-12.zip`. The archive is under 400 MB. The machine-readable summary is `/home/sonik/bananloop-benchmark-env/runs/boardgames-live-artifacts-2026-11-12/summary.json`.
