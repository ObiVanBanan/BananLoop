# BananLoop goal playground: Russian product matching

A tiny standalone catalog matcher with 8 plumbing products and fixed labeled queries. It exercises the Codex plugin and BananLoop's goal/bootstrap/evaluation loop without touching another project. The fixture is synthetic; results are only evidence about this tiny benchmark.

## Goal to try

In Codex, open this folder as the project, then ask: `Use $bananloop-goal to improve top-1 accuracy on the fixed product matching benchmark. Keep validation accuracy from regressing and do not edit the catalog, labels, evaluator, or tests.`

The baseline currently ranks products using token-set Jaccard overlap. The useful work is to improve matching across Russian/English aliases, DN/Du/Du notation, pressure notation, materials, and joining types.

## Existing measurement contract

- Development: `python scripts/evaluate.py --split dev`
- Validation: `python scripts/evaluate.py --split validation`
- Final holdout: `python scripts/evaluate.py --split holdout`
- Smoke tests: `python -m unittest discover -s tests`
- Primary metric: `accuracy` (top-1 exact product ID, maximize; target 1.0).
- Benchmark tables are fixed and must not be edited to improve the score.

Inspect first with `bananloop doctor --repo . --json`. If bootstrap does not reuse the existing evaluator, use the explicit CLI form in the BananLoop README with staged evaluator commands and protected paths. Use a small experiment budget for the first run.
