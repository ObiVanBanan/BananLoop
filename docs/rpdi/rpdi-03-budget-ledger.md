# RPDI-03: dimensional campaign budget ledger

## Decision

The shared `CampaignRegistry` SQLite database owns the v2 budget ledger. Parent
and child campaign databases are separate files, so neither can serialize
reservations against a common cap. All accounting remains dimensional: calls,
tokens, evaluation units, experiments, and attempts are independent resources.
The v1 `CampaignStore` counters and settlement behavior remain unchanged.

## Ledger contract

- A root account has an immutable cap and durable `spent`, `reserved`,
  `unknown`, and `revision` values for each configured resource.
- Child admission escrows a complete resource vector in one `BEGIN IMMEDIATE`
  transaction. The allocation has a stable idempotency key and confirmed spec
  hash before it is bound to one child campaign identity.
- Child reservations are checked against both the child's account and its
  immutable allocation. A reservation is durable before dispatch and carries
  action, idempotency, and fence identity.
- Known usage settles once against a receipt digest. Ambiguous dispatch moves
  the upper bound from `reserved` to `unknown`; timeouts and expired leases do
  not refund it. `UNKNOWN` can be reconciled only with authoritative receipt
  evidence and the current fence.
- A reservation is released only when durable evidence proves dispatch did not
  occur. Child closure fails while any action remains `RESERVED` or `UNKNOWN`.
- Child closure requires a terminal status supplied by the lifecycle caller;
  RPDI-04 must read that status from the child campaign store.
- Closing a reconciled child converts the root's full escrow into actual root
  spend and releases the unused difference exactly once.

## APIs and ownership

`CampaignRegistry.budget_ledger()` opens the project-wide `BudgetLedger`.
`create_root`, `reserve_child`, `bind_child`, `reserve_action`, `settle_action`,
`advance_fence`, `release_action`, `close_child`, and `snapshot` are the ledger
operations. Nested child escrow propagates through the ancestor allocation
vector so a grandchild's spend remains charged to the original root envelope.
SQLite transactions protect concurrent callers even when they live in separate
processes. The project file lock still protects the registry's campaign
identity and creation saga.

RPDI-04 must call `reserve_action` before worker dispatch and settle from the
durable provider receipt after dispatch. It must recover a pre-existing ledger
reservation by action/idempotency identity on restart. CampaignStore's current
v1 action path is intentionally not redirected through this API.

## Boundaries and remaining acceptance work

RPDI-03 introduces the production accounting authority and its saga operations;
it does not yet wire the v2 worker dispatch path or child campaign activation.
Those call sites do not exist in the production engine yet and are owned by
RPDI-04/06. A v2 campaign cannot claim enforced budget accounting until those
callers are integrated. RPDI-03 CI must cover multi-process cap contention,
vector independence, allocation/retry crash points, exact-once settlement,
UNKNOWN retention and authoritative reconciliation, stale fences, one-time
escrow return, and unchanged v1 accounting.
