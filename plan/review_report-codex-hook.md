# M6 review: Codex lifecycle bridge

## Scope

One review pass of the Codex Stop dispatcher, registry session binding, CLI and plugin wiring, skill/docs, and M6 tests.

## Finding

- **Major, repaired:** registries created before M6 do not have `codex_sessions`; the first read-only lookup raised before the hook could bind a marker. The lookup now treats only `no such table: codex_sessions` as an unbound session, after which the validated bind path creates the table. `test_hook_binds_from_marker_in_pre_bridge_registry` covers the complete hook path.

## Result

The finding is closed by a targeted regression test. No second review pass was run. Initial review validation: 35 passed, 1 skipped (symlink privilege unavailable). The orchestrator reran the targeted suite after the fix and will record final full-suite status in `codex-hook-completion.md`.
