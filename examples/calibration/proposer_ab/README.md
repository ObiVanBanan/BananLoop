# Proposer A/B calibration (GPT-6 Luna)

This is a small, CPU-only harness for comparing proposer behavior. It does not
run an evaluator or campaign and does not write synthetic history to a campaign
database. The three frozen DEV records below are request context only.

## Run

From the repository root, with the project installed or `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m pytest examples/calibration/proposer_ab/test_runner.py -q
PYTHONPATH=src python examples/calibration/proposer_ab/runner.py --output /tmp/proposer-ab-results
```

The live run requires Codex CLI authentication and invokes exactly three fresh
calls per condition (six total) using `CodexAdapter(model="gpt-6-luna")`.
There are no token ceilings. Each call gets a distinct disposable workspace.
The frozen evaluator and scoring fixture are never copied into that workspace.

## Frozen DEV context

Baseline quality is 0.5000. The three public synthetic records are respectively
0.5002, 0.5003, and 0.5004 (positive deltas +0.0002/+0.0003/+0.0004), each
rejected because `min_improvement=0.001`; champion remains 0.5000. They use
three distinct mechanism keys and point to editable `solution.py`. The frozen
CPU fixture is `fixture/evaluator.py`; it is not run by the A/B proposer runner
and is never copied to the proposer workspace. It returns 0.5000 for the starter
solution, leaving substantial headroom. Its eight labeled cases cover acronyms,
synonyms, negation, operation order, quantifiers, and direct matches; labels are
scorer-side only and do not enter the proposer request.

## Artifacts

Each repetition contains `request.json` (exact task payload sent to adapter),
`response.json` (raw structured result), normalized portfolio and selector
record, `run.json` (wall time, exception and usage if present), and the
sanitized workspace. `blind_scores.json` supplies anonymized hypothesis IDs and
a 0–2 human-review rubric for admissibility, specificity, novelty, DEV-evidence
use, and testability. It is a review sheet, not an objective reward. `key.json`
maps anonymous IDs to condition/repetition and is stored separately. Missing
token usage is null; it is not inferred from response size.

Control and treatment requests are identical except for `planning_mode` in the
top-level payload and `research_state.planning_mode` (the same intervention
represented in the two existing API fields). This synthetic comparison must not
be interpreted as two branches of one campaign or as a reward comparison.
