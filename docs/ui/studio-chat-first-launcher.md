# UI-05 — Chat-first New Campaign / Local Studio

**Scope:** stacked draft PR #142, independent of #141. No claims of production readiness or complete v0.2 autonomy/HITL.

## Start without a project directory

From the BananLoop checkout (editable Python install and Node.js/Codex CLI installed):

```sh
bananloop serve --studio
# separate terminal in the same checkout
cd web-prototype/galaxy-spatial-canvas
npm install
npm test
npm run typecheck
npm run build
npm run dev
```

Open `http://127.0.0.1:5173`. The landing screen works with zero campaigns. Click **New Campaign**; you never have to manually create or select a Git repository. `--studio` is mutually exclusive with `--repo`. Existing `bananloop serve --repo /path/to/research` remains supported for viewing existing project campaigns; it does not enable Studio launch.

On first start, `serve --studio` creates a clearly marked, minimal Git source workspace in the user's application data directory (Linux: `$XDG_DATA_HOME/bananloop/studio` or `~/.local/share/bananloop/studio`; Windows: `%LOCALAPPDATA%/BananLoop/Studio`). It never deletes or takes over existing unrelated files at that path. A minimal repository is **not a benchmark**. It has no fabricated dataset, evaluator, or success metric.

## Discussion and launching

1. The user sends a goal in the Studio chat. A **real Codex CLI model call**, via the existing hardened `operator-ui/codex-chat.mjs` app-server bridge, responds with questions, an optional draft goal and suggested bounded budgets. No mock AI response is used. The Codex bridge disables tools, extensions, MCP and networking, and fails closed if it cannot verify those restrictions. Browser and BananLoop do not receive OAuth tokens.
2. The user discusses measurable outcomes, evaluation assets and budget changes with the model until satisfied. The right-hand proposal card is **not authoritative**: it is a suggested budget and goal, validated on the server.
3. **Review & Start** opens a confirmation view. Only after the second click is the existing `python -m bananloop.cli start` launched, with fixed server-generated argv (no arbitrary command, path, agent executable or shell from browser). Calls can consume the existing Codex/ChatGPT subscription limits; any API-key login may bill separately.
4. **Full Auto** starts bootstrap and, if bootstrap can prove evaluation readiness, runs the existing v1 Engine. **Supervised** invokes `--prepare-only`, waits for successful preparation, then requires a separate explicit **Подтвердить выполнение** click. It is a UI preparation gate, **not** the full v0.2 human-in-the-loop authorization on pivots.
5. If the default empty source repo lacks a trustworthy evaluator or assets, bootstrap must return `NEEDS_INPUT` (exit status 3). UI shows the missing-input state; it must not invent metrics, tests, data or pretend an experimental run succeeded. New substantive resources will require an explicit later project-material/import workflow.

The UI stores only session messages and plan state in browser memory. The CLI creates durable campaign state and outputs under BananLoop's normal managed project data; the local launcher does not replace those stores. It reports only process phase/status and a stable campaign ID, never raw Codex output, credentials, evaluation results, local paths, or unredacted logs. After a server restart, the launcher may report `UNKNOWN` for an in-flight request; the campaign is still discoverable in the campaign index. Do not treat status polling as a command receipt.

## API additions, loopback only

- `GET /v1/studio/info` — feature support, no user paths.
- `POST /v1/studio/discuss` — bounded user message and conversation history, one real, read-only Codex planning turn.
- `POST /v1/studio/launch` — explicit goal, mode, bounded budgets and idempotency request ID. Will execute only the Engine's fixed CLI entry point.
- `POST /v1/studio/launch/confirm` — exact matching request ID, only after successful supervised preparation.
- `GET /v1/studio/launch/status?request_id=…` — local process status, campaign ID, no log contents.

POST requires `application/json` and an allowed local UI Origin; all endpoints inherit existing bearer auth and loopback bind restrictions. No endpoint accepts arbitrary source repo, workspace path, eval command, startup flags, model invocation method, or untrusted shell text. Origin checks do not substitute for a mandatory bearer token if this were ever moved to remote or multi-user deployment. **Never expose the dev server publicly.**

## Verification and known limitations

- Five focused backend Studio tests run with fake subprocesses plus a real `git init` in a temporary directory, covering input bounds, clean scaffold, request ID reuse, supervised preparation/confirmation, and unavailable-model reporting.
- `node --check operator-ui/studio-chat-worker.mjs` and TypeScript syntax transpilation of the `Studio.tsx` prototype pass.
- **Not yet verified:** a real ChatGPT model turn, full React/React Flow Vite build, Browser E2E, live CLI bootstrap on the user's own Linux machine, evaluator preparation, or Windows runner. No benchmark improvement is claimed.
- `npm run dev` is development-only. A single packaged BananLoop desktop or one-command production UI server is a separate release task.
- PR #141 and its parent v0.2 changes remain independent. Merge the verified v0.2 integration branch into this UI branch later, resolve API/autonomy semantics, and re-run all tests before merging to main.