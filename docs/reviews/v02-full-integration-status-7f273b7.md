# BananLoop v0.2 integration status — `7f273b7`

**Runtime code commit:** `7f273b7a1aadca64881f96dc487481581c56f279`  
**Branch:** `codex/v02-full-integration`  
**Base:** GitHub `main` at `6c89975870b0da8060146953faea966e203ba857`

This remains an integration checkpoint, not a v0.2 Full acceptance claim. See
[`v02-full-integration-results-7f273b7.json`](v02-full-integration-results-7f273b7.json)
for machine-readable evidence.

## Verification

- Full repository suite on this runtime code: **931 passed, 0 failed, 0 errors,
  2 skipped** (933 collected), with loopback sockets and Linux namespaces
  enabled. Skips: unavailable pinned selector source commit and the
  Windows-only ACL/reparse-point contract.
- Reproduction after installing `.[test]`:
  `PYTHONPATH=tests/plugins python -m pytest -p isolate_home -q -ra --tb=no`
- Offline diagnostic on clean commit `7f273b7a1aadca64881f96dc487481581c56f279`:
  `OFFLINE_V2_EXPLORATION_PASS`, two durable iterations, public memory in turn
  two, no live model call.
- Core now independently ranks later portfolio candidates by the number of
  distinct, trusted prior evidence IDs attached to the candidate's exact
  `target_claim_key`. Unknown IDs and citations mismatched to a claim are
  rejected. Ties use a stable content digest excluding the candidate ID. With
  no prior evidence, the initial researcher preference is retained.
- The offline semantic repeat detector recognizes a small, explicit
  controlled-variable synonym map; it conservatively separates opposite
  directions and unrelated variables. It does not perform general semantic
  inference.
- The end-to-end ranking regression test confirms Core chooses the evidence
  grounded candidate over the model's stated preference.
- No paid model request was made.

## CI and Windows repair

The previous Windows job at head `e3e593b0f8f56c2aea8f2419965b1bc72ed82c11`
failed with 64 ACL-related failures. Commit
`b8e9de82e444fb292c1b4349d63a2da62e00b397` rebuilds a protected DACL from an
empty rule set, checks its three trusted entries, and puts the isolated test
home beneath it. The updated Windows job has completed setup and is executing
pytest; the run for the current runtime commit has not yet started.

## Remaining Full acceptance gates

- Candidate ranking is based on verified reference coverage, not independent
  validation of the scientific truth or semantic relevance of each cited
  finding.
- Latest Windows CI must pass; Linux Chromium and mutation-policy checks must
  also complete on the current runtime head.
- Owner-approved one-call SIWC smoke remains unrun. The prepared command and
  transport limits remain documented in the PR body.

These limitations prevent a v0.2 Full completion claim.
