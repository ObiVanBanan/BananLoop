# RPDI-10: evidence ledger, causal memory, and knowledge export

## Contract

RPDI-10 derives research knowledge only from a completed v2 exploration job
whose immutable worker result matches a committed `EXPLORATION_COMPLETED`
event, current campaign spec, pinned role binding,
worker fence, request hash, output digest, and full source commit. Each finding
is content-addressed and retains its campaign, node, spec, source snapshot,
role-protocol digest, source job/fence, event cursor, and evidence references.
The SQLite records are append-only; the worker result and graph event remain
the canonical source records.

An evidence record can carry only `PUBLIC_DEV` classification. The strict
finding schema has no validation, holdout, secret, scorer, reward, or evaluator
fields. Findings are still authored by a role and stay marked
`UNVERIFIED_ROLE_FINDING`; a content-addressed artifact reference confirms
which published bytes they cite, not whether the statement is true. Typed
structural intervention signatures aggregate paths, algorithmic layer, and
intervention type while keeping support/refutation counts explicitly
unverified. Causal findings remain `UNVERIFIED_INFERENCE`; agent confidence is
stored as the agent's estimate and never becomes verifier approval. A causal
claim is an inference attached to evidence, not a causal conclusion.

`ResearchLedger.project` is a pure deterministic rebuild from immutable
records. Its scope requires an exact source commit and role-protocol digest;
records from stale snapshots or another protocol are excluded. Opposite
`SUPPORTS` and `REFUTES` findings for one claim key become an explicit
contradiction projection while every source record remains independently
available. Rebuilding from the same records returns identical bytes and
digests.

`export_knowledge` packages only scoped public DEV records, the deterministic
causal memory, structural intervention memory, and contradiction links.
Typed evaluator diagnostics from RPDI-52 are deferred until RPDI-11 provides a
trusted evaluator protocol; this stage does not treat worker-authored summaries
as trusted diagnostics. Multiple campaigns can be exported
together only under the same exact source snapshot and role protocol. Hidden
classes cannot be serialized by this schema. The RPDI-09 researcher receives
the aggregate only through role-allowlisted `dev_summary` / `evidence_refs`
fields with a content-addressed `research://ledger/...` provenance reference.

## Compatibility and recovery

The ledger adds tables to the existing schema-v5 campaign database without
changing v1 campaign serialization or approved-hash bytes. Existing completed
jobs remain replayable: output digests are checked against the exact stored
worker payload, so fields added to the current Pydantic model do not alter the
identity of older result bytes. If graph completion commits but ledger
projection is interrupted, repeating `publish_completed` verifies the same
durable source and idempotently inserts the missing content-addressed records.

## Acceptance

- Stale source commits and mismatched role protocols cannot be ingested into a
  requested knowledge scope.
- Opposing findings are retained and produce a deterministic contradiction
  record; causal status stays explicitly unverified.
- Findings cannot supply protected evaluation classifications, and export
  contains only public DEV records.
- Projection and knowledge-package digests rebuild identically after a
  database close/reopen.
- Same-completion replay does not duplicate evidence; a completed job without
  a matching graph event, result receipt, fence, role binding, or source commit
  is rejected.
- Existing v1 Engine, campaign hashes, and v2 queue completion replay remain
  compatible.

## Limits

Claim keys and statements originate from a role's research output. Contradiction
matching is an exact-key polarity comparison, not semantic entailment. The
projection keeps that evidence visible and marks causal claims unverified; it
does not infer that a supported claim is true. Cross-source or cross-protocol
comparison requires a later explicit bridge contract and is never silently
combined here.
