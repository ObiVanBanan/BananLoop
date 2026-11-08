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
- `GET /v1/campaigns` (safe local registry summary)
- `GET /v1/providers/codex/status` (installed/authenticated CLI mode only; no credentials)
- `POST /v1/providers/codex/login/start` (explicit local-only ChatGPT device login)
- `GET /v1/providers/codex/login/status` (device login progress)
- `POST /v1/providers/codex/login/cancel` (cancel pending device login)
- `GET /v1/campaigns/{campaign_id}/view` (UI read-only v1/v2 graph projection)
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

The UI consumes the campaign `/view` read model, never the raw graph store:
for v2, the endpoint presents public node id, phase, status, readiness,
blockers, typed edges and AND/OR groups. It never emits raw node payload,
sealed evaluation data, state paths, provider tokens or holdout/validation
scores. For legacy v1, it presents committed hypothesis titles/statuses as
**unconnected** nodes because v1 does not have the v2 dependency graph.
The projection checks the graph revision before returning to avoid silently
mixing nodes and edges from different commits. The UI only controls local
layout and annotations; engine mutation controls remain out of scope.

The Codex status endpoint executes only `codex login status` on the local
machine using a bounded subprocess, and returns a narrow authentication
mode (ChatGPT, API key, unknown, or disconnected). It never reads credential
files or returns account tokens. A separate explicit POST device-login route
starts `codex app-server` and uses the official `chatgptDeviceCode` RPC to
return a one-time code and the official verification URL. This endpoint only
accepts JSON POSTs from the local UI origin and supports status/cancel; Codex
itself owns credential persistence and refresh. Device login may be disabled
by account or workspace policy; `codex login` remains a fallback. A ChatGPT
account is not an API key, and API-key billing is separate from ChatGPT plan
usage.

Example:

```sh
bananloop serve --repo .
curl http://127.0.0.1:8765/openapi.json
bananloop campaigns list --repo . --json
curl 'http://127.0.0.1:8765/v1/campaigns/c-0123456789abcdef0123456789abcdef/snapshot'
```

