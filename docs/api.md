# Local API

`bananloop serve` starts the local REST API, OpenAPI document, and campaign SSE
stream. The API is restricted to loopback (`127.0.0.1` or `localhost`) until a
TLS transport is available. An optional bearer token can be supplied with
`--token` or `BANANLOOP_API_TOKEN`.

`http://127.0.0.1:8765/` serves the same-origin operator Dashboard. Static
assets are available without a bearer token; campaign/API reads still require
the configured token. The browser keeps entered credentials only in memory.
Dashboard mutations include a same-origin request marker and are checked
against the request Host and browser Origin. Non-browser clients without an
Origin remain supported, and bearer-token mode still requires authorization.

The OpenAPI 3.1 document is available at `/openapi.json`. API reads and writes
name a campaign explicitly, so switching a URL between campaigns does not
change the CLI's active campaign. The CLI's status, snapshot, event, operator
control, and pivot-decision commands use the same `CampaignApplication`
service as the HTTP handlers.

## Routes

- `GET /healthz`
- `GET /` (local Dashboard; static assets)
- `GET /v1/campaigns?limit=50&cursor=...` (bounded, redacted project campaign index; does not change the active CLI campaign)
- `GET /v1/campaigns/{campaign_id}/snapshot`
- `GET /v1/campaigns/{campaign_id}/graph` (v2 campaigns)
- `GET /v1/campaigns/{campaign_id}/research?limit=50&cursor=...` (campaign-scoped PUBLIC_DEV scope index)
- `GET /v1/campaigns/{campaign_id}/research?source_snapshot_id=...&protocol_digest=...` (bounded redacted knowledge projection)
- `GET /v1/campaigns/{campaign_id}/observability` (persisted campaign-scoped queue, budget, and bounded scheduler-plan reason)
- `GET /v1/campaigns/{campaign_id}/lifecycle` (persisted phase, pivot, child, evaluator and protocol handoff receipts)

Observability queue transitions are derived from at most the latest 10,000
campaign queue events and labeled with that window; the response includes the
campaign-filtered worker event cursor. Queue state counts, budget
dimensions, and scheduler planning inputs are campaign-filtered. Scheduler
reasons are computed read-only from the v2 graph, persisted budget snapshot,
and a bounded active-worker view. The scheduler summary reports the campaign
event cursor and campaign-filtered worker event cursor that frame those inputs;
incomplete sources are unavailable, not an inferred reason. Missing authority
tables are represented as unavailable, not as zero; prompt, result, worker
identity, and lease owner fields are not returned.
- `GET /v1/campaigns/{campaign_id}/events?after=0&limit=100`
- `GET /v1/campaigns/{campaign_id}/events/stream?after=0`
- `POST /v1/campaigns/{campaign_id}/graph/commands` (v2 campaigns)
- `POST /v1/campaigns/{campaign_id}/commands/{pause|resume|stop}`
- `POST /v1/campaigns/{campaign_id}/pivot-decisions`

Campaign discovery returns safe summaries for legacy v1, v2, and reserved
campaigns. It omits goal text, goal fingerprints, request keys, and filesystem
state paths. The opaque continuation cursor follows registry creation order;
listing does not change the selected campaign. V2 profile and flag hashes are
reported only from the persisted approved spec, and `FULL` is never inferred
from a partial feature list.

Every POST requires an `Idempotency-Key` header. For control commands it must
match the body's `request_id`. Pivot decisions require the header to match the
typed command's `request_id` and its `campaign_id` to match the URL.

Control endpoints persist operator intent and return a durable receipt. They do
not run an Engine in the HTTP request thread; an explicit supervisor invocation
such as `bananloop resume` performs campaign execution.

Campaign events use the campaign database's `events.seq` cursor. SSE `id`
values therefore resume through `Last-Event-ID`; they are not worker-queue
event IDs. Event payloads pass through the existing allowlist projection, so
unrecognized event payloads are represented with an empty object. V2 graph
snapshots expose node identity, phase, status, readiness, and blockers; node
payloads remain private. Snapshot and event endpoints never expose validation
or holdout scores.

Event pages include `cursor_floor` and `latest_seq`. A cursor below the retained
range returns `410 event_cursor_expired`; fetch a fresh snapshot before
reconnecting. A cursor ahead of the latest committed sequence returns
`409 event_cursor_ahead` with `current_cursor`. An SSE stream that becomes stale
after it starts emits `resync_required` and closes.

The v2 graph command body is the canonical typed graph mutation envelope,
including `command_id`, `idempotency_key`, `expected_graph_revision`, and
`changes`. The idempotency header must equal the envelope key. Replays return
the persisted receipt and reuse with different content is rejected. The CLI
offers equivalent `bananloop graph snapshot` and `bananloop graph mutate`
commands.

Example:

```sh
bananloop serve --repo .
curl http://127.0.0.1:8765/openapi.json
bananloop campaigns list --repo . --json
curl 'http://127.0.0.1:8765/v1/campaigns/c-0123456789abcdef0123456789abcdef/snapshot'
```

