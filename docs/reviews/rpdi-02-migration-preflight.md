# RPDI-02 SQLite migration preflight

**Review date:** 2026-11-02  
**Current `main`:** `82db20cbc178ccf541dd8bb435bbafe055a0a4d9`  
**Status:** preflight evidence retained; RPDI-02 implementation is underway on `codex/rpdi02-sqlite-v5-and-dag-cas`.

**Governance note:** the earlier independent RPDI-00 reviewer gate was explicitly waived by the project owner on 2026-11-02 after the owner's review of PR #117. This is an owner exception, not independent approval. Per-RPDI review, CI, tests, and DoD remain required.

## Existing v4 store behavior

- `src/bananloop/store.py:13` sets `journal_mode=WAL` in the `SCHEMA` script.
- `CampaignStore.__init__` reads `PRAGMA user_version`, rejects versions above 4 (`store.py:159-163`), runs `executescript(SCHEMA)`, then calls `_migrate_schema()` (`:222-223`).
- `_migrate_schema()` performs additive ALTER/CREATE statements and writes `PRAGMA user_version=4` (`store.py:225-331`). There is no explicit `BEGIN IMMEDIATE` covering the complete current migration sequence.
- Existing backups cover old upgrades (v1/v2/v3 branches at `store.py:163-221`), but a v4→v5 implementation still needs a fresh backup policy and exact restore/failure semantics.
- Existing compatibility tests are in `tests/test_operator_store.py`, `tests/test_composite.py`, and `tests/test_integrity.py`. They cover legacy versions, preservation of selected rows/hashes, and future-schema rejection. `tests/test_lock.py` verifies the lock is single-writer; it does not verify migrations acquire it.

## Migration lock integration risk

`CampaignStore.__init__` is already a write path, so migrating inside it affects every store-open call, including readers. Current opens are not consistently under `CampaignLock`:

- `cli.py:368` opens an existing campaign before the locked recheck path at `:380`.
- Read/status/event/report routes open stores around `cli.py:527`, `:549`, `:561`, and `:688`; campaign listing opens stores around `:307`.
- `CampaignLock` is explicitly used around run/export and some child-creation paths (`cli.py:462,706`; `engine.py:176,237`), not all constructor calls.
- The lock is nonblocking and uses `flock` on POSIX or `msvcrt.locking` on Windows (`src/bananloop/lock.py:15-90`). Existing code does not prove nested acquisition/reentrancy.

Before implementation, choose one centralized protocol that distinguishes lock ownership and avoids a second acquisition when a caller already holds the campaign lock. Do not silently put a nested nonblocking lock into `CampaignStore.__init__` or assume a read-only open is migration-free.

## RPDI-02 acceptance evidence

1. Build a representative real v4 database through `CampaignStore`, including campaign spec/hash, action and reservation history, evaluator binding and hash, evaluation receipt bytes, events, and foreign keys.
2. Acquire the application `CampaignLock`; prove a migration caller cannot race or migrate while the lock is owned, and that the v4 database is unchanged on rejection. Prove migration succeeds when the authorized lock owner invokes it without nested-lock failure.
3. Migrate v4→v5 under an explicit SQLite writer reservation; verify a durable backup, WAL behavior, `integrity_check`, `foreign_key_check`, indexes, and byte/value preservation of legacy rows.
4. Reopen the migrated database through the supported v2 adapter. The v1 `CampaignStore` should continue to reject an unknown future DB version without modifying it.
5. Inject failure before schema changes, during additive DDL, and before version commit. Assert recoverable version/state, backup retention/removal policy, and absence of partial v5 authority.
6. Exercise concurrent CLI/read/write opens on Linux and Windows. Confirm locks and SQLite busy handling produce explicit safe outcomes rather than silently racing the migration.

## Gate

The RPDI-00 spike's `BEGIN IMMEDIATE` backup-ordering test is useful isolated evidence, but it does not establish application-level lock integration or v2-adapter reopen. RPDI-02 must still prove both with its own tests. The owner waiver removes only the additional independent RPDI-00 reviewer step; it does not waive these RPDI-02 acceptance checks or later release gates.
