# BananLoop v0.2 multi-epoch autonomous acceptance

## Tested revision

Runtime/test SHA: `a6b0ae44d24de2b77fde2aa1a7fb5e12b8660774`  
Branch: `codex/v02-full-integration`  
PR: [#135](https://github.com/ObiVanBanan/BananLoop/pull/135), Draft, unmerged.

This revision supersedes the earlier `106807e` multi-epoch report by checking a
real durable `PAUSED` control state and resuming it via the Optimization CLI.

## End-to-end campaign

The composed offline acceptance begins at parsed `v2 explore` preflight and
finishes with the final parent campaign report:

- Three Exploration iterations record two `REFUTES` findings. Research Memory
  changes the next action: Core chooses an evidence-linked follow-up over the
  researcher's unrelated preference. The third iteration records a narrowed
  `SUPPORTS` finding.
- A separately pinned verifier approves the evaluator protocol. Owner pivot
  approval and Optimization-child activation are exercised through the API.
- Optimization performs three epochs. The first two candidates are DEV
  evaluated and independently rejected as regressions. Candidate three is
  published durably, then the campaign control is set to `PAUSED` before its
  DEV evaluation. The test closes the runtime connections and starts a fresh
  CLI invocation with `--resume`; the same candidate is DEV evaluated once,
  independently accepted, promoted, and checked on terminal HOLDOUT.
- The final report includes a per-candidate `decision_trace`: hypothesis ID
  and text, rationale, changed paths, source/candidate snapshot IDs, accepted
  or rejected decision, guard, and evidence references.
- The test confirms exactly one DEV receipt for the resumed candidate. A later
  replay of the completed campaign adds no provider dispatch, model-budget
  spend, or evaluator receipt.

The Exploration, parent-to-child approval and Optimization route is a single
controlled test campaign. The existing CLI still exposes those supervised
stages as separate commands because architecture pivot approval is an owner
decision; there is not yet one unattended user command for arbitrary campaigns.

## Test evidence

Full Linux offline command on this exact runtime/test revision:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 70.61 seconds.

The composed acceptance passed with both its deterministic test evaluator and
production bubblewrap evaluator. The skips are the unavailable pinned selector
commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` and the deferred Windows
ACL/reparse-point contract. All model transports were deterministic offline
doubles; no SIWC, paid, or external model calls occurred.

CI on this revision is pending after push. Windows remains outside the accepted
scope and is deferred.

## Limits

This verifies runtime control and durable recovery, not model quality across
epochs: the three optimizer outputs are fixed test fixtures. The previous live
Titanic result remains 153/196 = 78.06%, below the 95% stretch target; no
additional live call was authorized or attempted. Independent RPDI-14
benchmarking against Codex and the missing pinned selector revision remain
open. The supervised architecture pivot still requires owner approval.
