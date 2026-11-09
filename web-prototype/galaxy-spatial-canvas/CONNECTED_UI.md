# BananLoop Connected UI — 2026-11-08

This is the real-data, **read-only** integration of the Banana Noir Spatial Research Canvas. Branch is stacked on UI-01.3 / PR #139; it does **not** merge or depend on PR #141. All campaign data comes from the local BananLoop API. There is no silent synthetic fallback.

## Run locally (two terminals)

Install BananLoop's Python project in the first terminal, using the existing environment where its CLI works. Start the server for **the repository whose campaigns you want to observe**:

```bash
bananloop serve --repo /absolute/path/to/your/research-project
# Default loopback URL: http://127.0.0.1:8765
```

In the BananLoop source checkout at this PR branch, in another terminal:

```bash
cd web-prototype/galaxy-spatial-canvas
npm install
npm test
npm run typecheck
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite development server forwards only `/api/...` to the BananLoop loopback API. If your API uses another port, start Vite with `BANANLOOP_API_URL=http://127.0.0.1:OTHER_PORT npm run dev`. **Do not bind either dev server to the public network.**

Optional bearer auth: launch `bananloop serve --repo ... --token YOUR_LOCAL_API_TOKEN` and paste that token into Settings. It stays in this browser tab memory and is **not a ChatGPT credential**. Direct requests to port 8765 require `Authorization: Bearer ...`; Vite only proxies the browser requests to the local server.

## ChatGPT subscription mode

Existing BananLoop research execution uses **Codex CLI**, not a new in-browser OAuth client. On the **same computer/user** that runs `bananloop serve`, do:

```bash
codex login status
codex login
# Alternative on headless hosts (if device-code login is enabled for your account):
codex login --device-auth
```

Settings reports **ChatGPT**, **API key**, **unknown**, or **disconnected** by calling `codex login status` without shell invocation. It does *not* read or reveal `.codex/auth.json`, paste OAuth access tokens, or promise that an API-key login is subscription-funded. **Settings now also offers "Подключить ChatGPT"**. This starts the official
Codex app-server device-code flow (`account/login/start` with
`type: "chatgptDeviceCode"`) on the *local API host*. The UI displays only the
approved `https://auth.openai.com/codex/device` URL and one-time `userCode`,
and polls for `account/login/completed`. Codex, not BananLoop, stores and
refreshes credentials. The browser never handles OAuth access or refresh tokens.

- Device-code login may need enabling in ChatGPT security/workspace settings.
- It is scoped to **one local browser/API instance** and has a ten-minute
  local expiration; cancel immediately if the code was not requested.
- On this dev server, JSON POST is restricted to trusted loopback UI origins.
  Do not expose the BananLoop API or Vite to your LAN or the internet.
- This only authenticates the **underlying Codex CLI**. It does not add a new
  ChatGPT chat/LLM execution endpoint to Mission Control or change usage limits.
- For unsupported installations, run `codex login` in a terminal.

Reference: https://learn.chatgpt.com/docs/app-server

## New API endpoints

- `GET /v1/campaigns`: safe active/initialized campaign list from CampaignRegistry.
- `GET /v1/campaigns/{id}/view`: safe graph projection: v2 nodes + typed edges + AND/OR groups, or v1 hypotheses with **no inferred edges**.
- `GET /v1/providers/codex/status`: installed/authenticated **mode**, no secrets, account names, CLI stdout or environment values.
- `POST /v1/providers/codex/login/start`: starts managed Codex app-server device-code login.
- `GET /v1/providers/codex/login/status`: reports `idle`, `pending`, `completed`, `expired`, `failed` or `cancelled` and the temporary user code/verified link only while pending.
- `POST /v1/providers/codex/login/cancel`: cancels an unfinished device-code flow.

Existing `/snapshot` and `/events`/`events/stream` endpoints are reused. SSE reconnects after the last committed sequence number and reads snapshots again on graph changes; poll refresh is the fallback. Any disconnected or partial state is labelled; SSE receipt never means an Engine command succeeded.

## Security/integrity constraints

- The UI does not call any graph mutation, pause/resume, pivot approval or model-execution endpoints. It does not start campaigns automatically or send data to external provider APIs.
- Backend projection explicitly selects columns from `research_nodes` (via scheduling snapshot), `research_edges`, `graph_groups`, and `graph_group_members`; does not serialize node `payload_json`, sealed scores, evaluation results, spec JSON, auth files, or state paths.
- User notes, annotation arrows, node positions, collapsed rail and viewport stay in campaign-namespaced localStorage, separate from authoritative Engine graph.
- v2 `COMPLETED` is rendered as verified because GraphStore guards verified completion; v1 `COMPLETED` is *not* equated to independent verification. READY is schedulable, **not** proof an agent is working.
- The old synthetic exploration stays in a separate PR and is never shown as real data.
- Campaign selection does **not** activate/switch the CLI's active campaign.

## Checks / remaining gates

- Local: 15 Node model/boundary tests, 4 Python read-model contract tests, 4 additional simulated Codex app-server login tests; TS/TSX syntax transpilation and Python `py_compile`. The new login tests use a **fake CLI** and never contact ChatGPT.
- **Pending on operator machine:** validate real ChatGPT device-code sign-in, install npm packages, `npm run typecheck`, `npm run build`, full React Flow browser E2E, test against actual v1/v2 campaign SQLite stores, Codex login/CLI status in your environment, SSE disconnect/recovery, Windows and Manjaro, reduced-motion and resize QA.
- Hosted GitHub CI is not considered passed without an actual run on this PR head.
- This is a functional read-only integration plus an **unverified real-device sign-in flow**, **not yet** a complete interactive control console, mission chat, independent provider billing integration, or v2 human approval workflow.

## After PR #141 is complete

Keep this PR independent and unmerged while #141 is under development. Once #141 and its parent #140 are validated and merged into the intended v0.2 integration branch, merge that **integrated and verified** branch into `codex/ui-live-integration-2026-11-08` (or rebase via a fresh integration branch), resolve API/registry schema differences, then repeat all regression tests with real campaigns. Do not merge unfinished draft PR #141 into this branch now.