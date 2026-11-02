# RPDI-04: durable jobs, leases, and fencing

## Storage and atomicity decision

The project registry database owns v2 worker jobs, leases, budget reservations,
results, and the authoritative worker event stream. RPDI-03 places the shared
root/child ledger there too. A claim plus its budget reservation and
`JOB_LEASED` event therefore commit in one SQLite `BEGIN IMMEDIATE`
transaction. Per-campaign graph state remains in the campaign database; RPDI-04
does not claim one transaction across those separate SQLite files. Any campaign
event projection is replayable from the registry worker event stream.

Workers receive immutable job inputs and a fence token. They do not receive a
database connection or authority to settle/refund budgets. The trusted Core
persists the dispatch boundary before invoking a worker, verifies its result or
provider receipt, and then commits the result and budget settlement.

## State transitions

`READY → LEASED → DISPATCHED → COMPLETED`

- A claim reserves the per-resource upper bound, advances the generation fence,
  assigns an owner and lease deadline, and appends an event atomically.
- Expired `LEASED` work is proven pre-dispatch by the durable state; its
  reservation is released and the job returns to `READY`. The next claim gets
  a higher fence.
- `DISPATCHED` is committed before external work begins. Expiry or cancellation
  after this point moves the reservation and job to `UNKNOWN`; it never refunds
  or auto-retries.
- Only the current unexpired fence can renew or commit a direct result. A late
  worker result is rejected; a trusted verifier may resolve `UNKNOWN` from an
  immutable provider receipt.
- If verified actual usage exceeds its estimate, the ledger records the full
  actual amount even when this breaches the configured cap. Further claims
  fail closed because `spent` is now above `cap`; settlement never rolls back
  into an undercount of an already-incurred external charge.
- A pre-dispatch cancellation releases its hold and becomes `CANCELLED`.
  Post-dispatch cancellation becomes `UNKNOWN` until reconciled.

## Cross-database boundary

The registry worker event stream is authoritative for job and budget
transitions. Per-campaign `events` and graph projections are separate state and
must be updated by replayable idempotent commands. RPDI-04 must not describe a
registry write and a campaign-database write as one atomic commit. RPDI-05/06
can consume the event stream using a stored cursor and repair projection gaps.

## Acceptance

Tests cover duplicate enqueue/claim, atomic claim + budget reservation + worker
event rollback, monotonic fences, lease renewal races, pre-dispatch release,
post-dispatch `UNKNOWN`, cancellation at both boundaries, stale result
rejection, authoritative unknown reconciliation, exact-once settlement,
worker restart and resume on Linux and Windows, and unchanged v1 action history
and counters. Provider receipt validation and terminal campaign status remain
trusted-Core responsibilities; queue callers do not supply an unchecked
`authoritative` or terminal-status boolean.
