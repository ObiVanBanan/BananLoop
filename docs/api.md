# Local API

`bananloop serve` starts the local REST API, OpenAPI document, and campaign SSE
stream. The API is restricted to loopback (`127.0.0.1` or `localhost`) until a
TLS transport is available. An optional bearer token can be supplied with
`--token` or `BANANLOOP_API_TOKEN`.

The OpenAPI 3.1 document is available at `/openapi.json`. API reads and writes
name a campaign explicitly, so switching a URL between campaigns does not
change the CLI's active campaign. The CLI's status, snapshot, event, operator
control, and pivot-decision commands use the same `CampaignApplication`
service as the HTTP handlers.

## Routes

- `GET /healthz`
- `GET /v1/campaigns/{campaign_id}/snapshot`
- `GET /v1/campaigns/{campaign_id}/graph` (v2 campaigns)
- `GET /v1/campaigns/{campaign_id}/events?after=0&limit=100`
- `GET /v1/campaigns/{campaign_id}/events/stream?after=0`
- `POST /v1/campaigns/{campaign_id}/graph/commands` (v2 campaigns)
- `POST /v1/campaigns/{campaign_id}/commands/{pause|resume|stop}`
- `POST /v1/campaigns/{campaign_id}/pivot-decisions`

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

