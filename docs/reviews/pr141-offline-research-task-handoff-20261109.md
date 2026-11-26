# PR #141 offline Researcher-to-Optimization handoff — 2026-11-09

## Result

The offline integrated handoff now binds the human-approved Optimization child
to the Core-ranked hypothesis in the completed Researcher portfolio. The child
spec carries the hypothesis ID and text, parent campaign/node/job/snapshot,
portfolio digest, and content-addressed evidence references.

The Researcher writes cited evidence as `research_evidence` record IDs. The
handoff resolves those IDs only through persisted `PUBLIC_DEV` rows, checks the
source worker completion fence and output digest, checks the committed graph
event, then verifies each referenced artifact's path and SHA-256 before putting
artifact references in the child spec. An unresolvable, non-public, or changed
reference aborts the pivot.

## Verification

Command (live mode disabled; no subscription requests):

```sh
BANANLOOP_LIVE_INTEGRATED_OPTIMIZATION=0 \
  .venv/bin/python -m pytest -o addopts= -q \
  tests/test_v2_campaign_cli.py tests/test_v2_optimization_runtime.py \
  tests/test_rpdi13_integrated_acceptance.py -k 'not live'
```

Result: **34 passed, 1 deselected**. The integrated acceptance ran both the
deterministic test runner and real bubblewrap isolation variants. It asserts
that the selected task's evidence references equal the underlying persisted
public artifacts plus the portfolio artifact.

`git diff --check` passed. Code-and-test diff SHA-256 at verification time:
`ab47e36a6a2ce0ef419581797fa357c95accb230900fceb4d13d9242b34692a1`.
Base checkout: `a7e93aae719bf3a7c5fbeb5f9c53380357c47063` plus local changes.

## Scope limit

This is offline handoff evidence. The integrated harness still uses a
deterministic Researcher to produce its protocol proposal, and a deterministic
integration-verifier response. It does not prove the requested autonomous live
Researcher → Protocol Verifier → owner approval → Optimizer → Integration
Verifier campaign. That remains an acceptance gap.
