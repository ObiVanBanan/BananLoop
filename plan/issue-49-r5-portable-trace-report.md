# R5 portable campaign trace increment

## Scope

Add `bananloop trace export` as a read-only, shareable projection of the existing SQLite campaign store. It emits `events.jsonl`, `summary.json`, `report.md`, and a SHA-256 manifest. It does not create another event store or allow agents to write the scientific trace.

The export uses field allowlists. It includes durable event sequence/type/time and safe action/snapshot/receipt identifiers; campaign status and stop reason; frozen metric/gate/budget summaries; aggregate tier measurements; experiment decisions and fixed failure categories; configured agent roles; evaluator trust signals; and receipt/protocol digests. Cost and effective model remain `unknown` unless supplied by the provider/runtime.

The shareable default omits raw goal text (retains its SHA-256), agent rationales, command argv, raw errors, evaluator payloads, case inputs, labels, and per-case validation/holdout rows. Metric/check/profile names and paths are screened before export. Unknown event types are projected as `UNCLASSIFIED_EVENT`. Existing non-BananLoop directories and extra user files in a generated trace directory are preserved by refusing to overwrite them. Bundle refresh is staged in a sibling directory and restores the prior bundle if the directory swap fails.

## Verification

- `PYTHONPATH=src pytest -q tests/test_trace.py` passes: CLI parsing/execution, byte-identical repeated export, manifest hash checks, goal/error/holdout canary exclusion, refusal to overwrite user data, rollback after injected swap failure, and stable JSONL keys.
- The resume guard regression confirms that changing a persisted CampaignSpec without changing its approved hash raises before any action or evaluator/agent call. Existing orphaned `RUNNING` actions still require the explicit retry flag, and the action retry path remains covered.
- `PYTHONPATH=src pytest -q` passes on the feature branch.
- `python -m py_compile src/bananloop/trace.py src/bananloop/cli.py tests/test_trace.py` and `git diff --check` pass.
- The exporter was run against the saved product matcher retrieval campaign `c-afbf21b4f610db2db0a97087313f9112`. It exported 96 events and 8 receipt digests; a verifier checked JSONL parsing, manifest hashes, no case records, and no `GOLD_CANARY`. Aggregate evidence is in `qa/issue49-r5-trace-export-2026-10-22.json`.

## Limits

This increment does not complete R5 or the umbrella issue. The trace omits raw goal text for shareability, has no effective runtime/dependency fingerprint, and does not implement the broader #37 approval manager/dashboard. Protocol edits require a new approved campaign; there is no in-place protocol editing flow. Hosted CI remains unavailable before runnable steps/logs; the local Linux full test result does not establish Windows CI.
