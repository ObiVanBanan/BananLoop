# RPDI-05: evidence-only exploration adapter

RPDI-05 introduces the first v2 execution adapter for `EXPLORATION` campaigns.
It does not adapt v2 `OPTIMIZATION`; that mode remains rejected until its
frozen evaluator and trusted verification stages are implemented.

## Boundaries

- `EvidenceOnlyAttemptAdapter` accepts an explicit, nonempty source-path
  allowlist, binds it and the approved scope digest into the durable job
  request, resolves the approved base ref to a full immutable Git commit ID at
  enqueue time, and snapshots only that commit.
- Each lease fence receives a fresh workspace and artifact directory. The
  source snapshot contains no `.git` directory. Archive links, special files,
  path traversal, secrets, evaluator material, validation and holdout paths are
  rejected. Workers receive no queue or campaign database handle.
- State directories reject symlinked ancestors and are checked to remain
  beneath the configured state root before creating per-fence data. The local
  state root is trusted; these path checks do not defend against a hostile
  same-user process racing directory replacement.
- Worker output is a strict evidence envelope: node identity, references,
  artifact SHA-256 manifest, limitations, and the next experiment. Scores,
  rewards, and optimization metrics are rejected as unknown fields.
- Trusted Core hashes artifact bytes, checks the exact manifest, binds the
  output digest to the dispatched job and current fence, and stores the full
  result with budget settlement in the registry database. Before graph
  publication, Core rechecks the bytes and copies them into a content-addressed
  store; the graph records `artifact://sha256/<digest>` references.
- Graph completion is a second-database projection. It advances only a pending
  exploration node and appends its evidence event in one campaign-database
  transaction. A stable job/fence idempotency key makes replay safe after a
  crash; no cross-database atomicity is claimed.

## Limit of the RPDI-05 gateway

The explicit path list and denylist are a bounded evidence-only adapter
boundary, not the complete capability policy planned for RPDI-06/12. A worker
implementation must still be launched in an appropriate OS-level sandbox by
the integrating runtime; the current callback is a trusted execution hook,
and passing it only the envelope and paths is not itself process isolation.
Long-running execution must be given a lease long enough to finish; automatic
heartbeat renewal is not part of this adapter. Broader role permissions,
network capabilities, and credential isolation remain later work.

## Acceptance evidence

Tests cover strict score-field rejection, traversal and protected-path denial,
per-fence source isolation without `.git`, duplicate-manifest rejection,
artifact-byte digest verification at both completion and publication, durable
queue result replay, and idempotent graph publication. The existing v1 CLI and
Engine path is not changed by this adapter.
