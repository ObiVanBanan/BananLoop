# BananLoop v0.2 multi-epoch offline acceptance

## Tested revision

Implementation and test SHA: `106807ecfd99f8942fea4076295c7c1991c77252`  
Branch: `codex/v02-full-integration`  
PR: [#135](https://github.com/ObiVanBanan/BananLoop/pull/135), Draft, unmerged.

## Acceptance route

The composed acceptance begins with a parsed `v2 explore` preflight, runs three
Exploration iterations, persists two `REFUTES` findings, verifies that Core
chooses the evidence-linked follow-up over the researcher's unrelated choice,
then records a narrowed `SUPPORTS` finding. A separately pinned verifier
approves the evaluator protocol; the owner pivot is approved through the API;
the Optimization child runs through the parsed CLI and writes the final report.

Optimization now exercises three epochs in this same parent/child campaign:

1. Candidate one is DEV-evaluated and independently rejected as a regression.
2. Candidate two is DEV-evaluated and independently rejected as a regression.
3. Candidate three is durably published, then execution stops before DEV
   evaluation. A fresh CLI invocation reopens SQLite and resumes the same
   candidate, evaluates and accepts it, and performs final HOLDOUT verification.

The final Optimization report retains a `decision_trace` for each candidate:
hypothesis, rationale, changed paths, source/candidate snapshots, decision
guard, and evidence references. The resumed candidate has exactly one DEV
receipt. Replaying the completed campaign neither spends additional model
budget nor creates evaluator receipts.

## Test evidence

The complete offline suite was run against the exact source tree committed at
this SHA:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 70.47 seconds.

Both integrated acceptance variants passed: deterministic evaluator runner
and production bubblewrap evaluator. The only skipped tests are the
unavailable pinned selector commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd`
and the deferred Windows ACL/reparse-point contract.

All model transports in this offline acceptance are deterministic mocks; no
live or paid calls were made. The synthetic metric checks runtime decisions,
not model quality. The prior live Titanic score remains 153/196 = 78.06%; no
new live run was authorized or attempted. Windows remains deferred.

GitHub CI for `106807e` is expected to start after the branch push; this report
does not claim CI results for the new revision.
