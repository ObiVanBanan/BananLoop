# RPDI-05 exploration adapter preflight

**Reviewed base:** `origin/main` at `a8e753c` (RPDI-04)

## Findings

- The existing CLI and `Engine` dispatch v1 `CampaignSpec` only. V2
  `Optimization` requires a later evaluator/verifier contract and must fail
  closed here.
- The campaign graph database and registry queue are separate SQLite files.
  Queue completion must retain its full result so graph projection can replay
  after a crash; graph writes must be idempotent and must not claim a shared
  transaction with queue settlement.
- The existing v1 workspace manager reuses mutable state and is unsuitable for
  parallel fenced jobs. RPDI-05 must build a fresh immutable source snapshot
  and per-fence artifact directory.
- Worker output must be evidence-only and artifact bytes must be hashed by
  trusted Core. A worker-supplied score or digest alone is not sufficient.
- RPDI-05 adds a narrow explicit file allowlist. Full capability enforcement,
  roles, and OS-level sandbox integration remain subsequent requirements.
- Base refs must resolve to a full immutable commit ID at enqueue time, and
  state-root subdirectories must reject symlinks before writing per-fence data.

## Implementation disposition

This branch adds a dedicated `EvidenceOnlyAttemptAdapter`, strict output
contract, durable queue result payload, and replay-safe graph completion. It
keeps legacy CLI/Engine dispatch unchanged and rejects non-exploration v2
campaigns. The path gateway documents its limits rather than treating it as a
complete sandbox.
