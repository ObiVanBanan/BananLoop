# BL-044/041 safe overlay investigation — 2026-11-23

## Scope and safety

Prepared isolated local public DEV overlays for the nine tasks named in
`benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-23.json`. Used the existing
`benchmarks.autoresearchexam.prepare_safe_harbor_task` builder with
`--local-public-dev-only`, against the pinned public AutoResearchExam source
checkout. No private grader source was opened. No Harbor task, model, or paid
request was started.

Artifacts are under
`/home/sonik/bananloop-benchmark-env/runs/bl044-safe-20261123/`:

- `overlays/<task-id>/` contains each generated overlay and
  `bananloop-overlay.json` builder record.
- `runtime/<task-id>.json` contains an isolated copy of each of the 12 runtime
  descriptors. The nine in scope reference the isolated overlay and its task
  TOML hash; the other three descriptors were copied unchanged for a complete
  catalogue preflight.
- `runtime/contracts/` contains no MCP contract copies; descriptors keep their
  already pinned contract paths and hashes.
- `preflight.json` records the offline preflight using this isolated runtime
  root.

The existing runtime root and its descriptors were left untouched.

## Results

The overlay builder completed for all nine tasks. The focused safe overlay test
passed: `tests/test_are_safe_harbor_overlay.py` — 8 passed.

The initial descriptor copies exposed an independent fixture binding issue:
their pinned `fixture_path` files carried a different embedded contract from
the contract file selected by those descriptors. The selected contract files
were not wrong: they matched byte-for-byte contracts embedded in other archived
public DEV fixtures. Repointed only the isolated descriptors to archived public
DEV fixture artifacts whose embedded contract bytes exactly equal the existing
descriptor contract, and updated each isolated fixture SHA. For eight tasks the
matching artifacts are `benchmark-public-dev-fixture.json` in
`runs/autonomous-v01-p2-prepared-i4fjpx2i`; for sketched Newton it is
`runs/sketched-newton-new-campaign-20261114-004/sketched-newton-fixture.json`.
The fixture contract was checked as the exact single ZIP entry
`public-mcp-contract.json`; no contract contents or claimed task pins were
rewritten. This resolves the fixture/contract binding and preserves each
fixture's descriptor pin. The three remaining catalogue descriptors were copied
unchanged from the existing runtime root so that the full catalogue could be
validated together.

Full offline preflight using the isolated runtime root now reports
`READY_NON_RPDI_DIAGNOSTIC` with no blockers. `harbor_started` and
`live_models_started` are both false. This only establishes offline preparation
readiness under the already disclosed internal diagnostic limitation; it does
not authorize a run or permit a blind/independent holdout claim. The original
dated preflight snapshot was not edited.

An independent repeat of preflight against the same isolated runtime root
also returned `READY_NON_RPDI_DIAGNOSTIC`, with no blockers. This does not
establish production launcher or controlled Harbor execution acceptance.
