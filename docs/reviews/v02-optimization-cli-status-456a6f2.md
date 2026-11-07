# v2 Optimization CLI integration — 2026-11-07

Implementation SHA: `456a6f22f935fb2a1704db1af4e3a289b20e8563`.

## Implemented

- Added `bananloop v2 optimize` as a separate route that does not invoke the legacy Engine. It resumes an existing supervised Optimization child created by the approved Exploration-to-Optimization pivot saga.
- The command loads the frozen v2 spec and requires a role registry whose digest matches the child. Before any provider readiness check or dispatch, it verifies the child’s evaluator approval and handoff against the exact campaign ID and spec hash.
- Default `--approve-up-to-calls 0` performs a no-auth preflight. A positive allowance is shared between optimizer and independent integration-verifier SIWC requests. Candidate-visible and writable paths are separate exact allowlists.
- Candidate work, trusted DEV/VALIDATION/HOLDOUT evaluation, scheduler state, and final report remain on `V2OptimizationRuntime` and Core’s trusted evaluator. Provider receipts are recorded in SQLite; failed and allowance-exhausted runs write atomic JSON reports and do not retry uncertain requests.
- Added offline CLI coverage for parser routing, read/write path separation, preflight without auth, frozen evaluator binding, provider receipt persistence, and the shared call allowance. The call-budget plumbing test uses mock transports/runtime; existing RPDI-13 and Titanic tests cover the real v2 Optimization/evaluator execution path.

## Verification on this implementation tree

Full offline suite:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **952 passed, 2 skipped, 7 subtests passed** in 67.59 seconds. Skips: unavailable pinned selector source commit and the Windows ACL/reparse-point contract on Linux. The full run includes loopback API acceptance and the production bubblewrap evaluator path.

CLI discoverability check:

```sh
PYTHONPATH=src .venv/bin/python -m bananloop.cli v2 optimize --help
```

`git diff --check` and `py_compile` passed. GitHub CI is pending publication of this implementation SHA.

## Usage and limits

First preview an already approved Optimization child:

```sh
bananloop v2 optimize --repo . --campaign-id "$OPTIMIZATION_CHILD_ID" \
  --role-registry ./roles.json \
  --visible-path data/train.csv --visible-path src/model.json \
  --write-path src/model.json
```

Only after explicit owner approval should the command be repeated with a
bounded `--approve-up-to-calls N`. This route executes an existing child; it
does not yet make `bananloop v2 explore` automatically discover/verify a
protocol, create the Optimization child, and continue into it. The single
command full parent-to-child orchestration remains an acceptance gap.

No live model call was made for this change. The previously authorized Titanic
live result remains **153/196 = 0.7806122448979592**, below the 0.95 stretch
target; monetary cost remains `UNKNOWN`. PR #135 remains Draft and unmerged.
