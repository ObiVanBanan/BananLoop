# R5 portable campaign trace increment

## Scope

Add `bananloop trace export` as a read-only, shareable projection of the existing SQLite campaign store. It emits `events.jsonl`, `summary.json`, `report.md`, and a SHA-256 manifest. It does not create another event store or allow agents to write the scientific trace.

The export uses field allowlists. It includes durable event sequence/type/time and safe action/snapshot/receipt identifiers; campaign status and stop reason; frozen metric/gate/budget summaries; aggregate tier measurements; experiment decisions and fixed failure categories; configured agent roles; evaluator trust signals; receipt/protocol digests; and a validated evaluator binding/runtime fingerprint. For direct Python evaluator runners, the binding records dependency metadata count/hash plus Python implementation/build/platform fingerprints. It never exports package names, argv, or host executable paths. Wrapper runtimes remain `UNKNOWN` rather than being inferred from the supervisor.

The shareable default omits raw goal text (retains its SHA-256), agent rationales, command argv, raw errors, evaluator payloads, case inputs, labels, per-case validation/holdout rows, dependency names, and host paths. Metric/check/profile names and paths are screened before export. Unknown event types are projected as `UNCLASSIFIED_EVENT`. Existing non-BananLoop directories and extra user files in a generated trace directory are preserved by refusing to overwrite them. Bundle refresh is staged in a sibling directory and restores the prior bundle if the directory swap fails. The summary schema and bundle manifest are version 2; existing owned version 1 bundles can be refreshed.

## Verification

- `PYTHONPATH=src pytest -q tests/test_trace.py` passes: CLI parsing/execution, byte-identical repeated export, manifest hash checks, goal/error/holdout canary exclusion, refusal to overwrite user data, rollback after injected swap failure, and stable JSONL keys.
- The resume guard regression confirms that changing a persisted CampaignSpec without changing its approved hash raises before any action or evaluator/agent call. Existing orphaned `RUNNING` actions still require the explicit retry flag, and the action retry path remains covered.
- `PYTHONPATH=src pytest -q` passes on the feature branch.
- Direct Python runner probing verifies Python/build/platform metadata and a stable hash/count of installed distributions; wrapper commands remain explicitly unknown. Trace regression verifies the runtime fingerprint matches the durable evaluator binding while executable paths and dependency names stay out of the shareable bundle.
- `python -m py_compile src/bananloop/trace.py src/bananloop/cli.py tests/test_trace.py` and `git diff --check` pass.
- The exporter was run against the saved product matcher retrieval campaign `c-afbf21b4f610db2db0a97087313f9112`. It exported 96 events and 8 receipt digests; a verifier checked JSONL parsing, manifest hashes, no case records, and no `GOLD_CANARY`. Aggregate evidence is in `qa/issue49-r5-trace-export-2026-10-22.json`.

## Limits

This increment does not complete R5 or the umbrella issue. Older campaign bindings predate dependency metadata probes, and non-Python wrappers remain unverified; archived runtime claims must stay partial/unknown where evidence is absent. The trace omits raw goal text for shareability and does not implement the broader #37 approval/dashboard. Protocol edits require a new approved campaign; there is no in-place protocol editing flow. Hosted CI remains unavailable before runnable steps/logs; the local Linux full test result does not establish Windows CI.
