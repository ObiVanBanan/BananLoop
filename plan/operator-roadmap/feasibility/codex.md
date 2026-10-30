# Codex integration feasibility (documentation and local inventory)

Date: 2026-10-29. No model, login, inference, or paid request was run.

## Operator chat implementation status
The Pi operator chat now has an explicitly experimental opt-in transport in
`src/bananloop/tui/codexChat.mjs`. It launches its own `codex app-server`
stdio JSONL process, initializes it, discovers the model catalog, starts a
separate thread, and streams the turn response. It does not reuse campaign
`codex exec` turns. `BANANLOOP_OPERATOR_MODEL` selects a model before the
first chat request; an unavailable ID fails closed. No live model turn was
run. The transport and UI were checked with an in-process fake server only.
Leave `BANANLOOP_OPERATOR_MODEL` unset to select the default exposed by
`model/list`; set it to one of that live catalog's IDs before opening the
operator chat to choose another model. The catalog is never hard-coded.

The current official app-server documentation explicitly labels the command
and WebSocket interface experimental and says it is not supported for
production workloads. This implementation is therefore experimental and is
not represented as production-ready. The protocol remains subject to change;
the local fake does not establish compatibility with every installed Codex
version, current account entitlements, or real authentication/limit behavior.
Source reviewed 2026-10-29: https://learn.chatgpt.com/docs/app-server

## Current BananLoop
`src/bananloop/agents/codex.py` invokes `codex exec --ephemeral` for individual role actions, including configured `-m` and reasoning options. `src/bananloop/agent_profiles.py` loads role profiles and `docs/reference/cli-and-campaigns.md` documents user Codex configuration fallback. These do not establish an interactive persistent chat.

## Candidate transports
- `codex exec`: existing per-action integration; use for current role execution. It does not by itself establish a persistent operator chat.
- Codex app-server: official documentation describes JSON-RPC over stdio, initialization, thread start/resume, streaming updates, turn start/steer/interrupt, model/list and account/read. It is a candidate for interactive conversation, not yet verified end-to-end in this workspace. https://learn.chatgpt.com/docs/app-server
- Official integration overview distinguishes bounded `codex exec` tasks, SDK integration and app-server for an agent embedded in a product: https://developers.openai.com/blog/codex-as-a-platform
- Official documentation describes account/read and managed sign-in, but availability, installed-version compatibility and current authenticated session remain unverified. Never copy credentials into logs. A model catalog is not proof a particular model can be used with the current account; see https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server.

### Chat, model selection and errors
The app-server documentation describes `initialize` followed by `thread/start`
or `thread/resume`; `turn/start` starts a turn, `turn/steer` adds input to an
active turn, and notifications stream item deltas and `turn/completed`. The
turn request can override its model. `model/list` is the documented source for
available model IDs, default reasoning effort and supported effort options;
these depend on the client and account and must not be hard-coded. BananLoop's
existing role model settings remain the source for campaign roles. The operator
chat model is a separate pre-launch setting; no in-session model switch is
implemented by this feasibility spike.

Documented turn failures include a failed completion with typed
`codexErrorInfo`, including `UsageLimitExceeded`, `Unauthorized`, connection
and stream failures, and other errors. The app-server also documents account
rate-limit read/update methods. These are protocol capabilities, not verified
behavior of the installed Windows version or this account. The local probe did
not start a turn, so real limit and error behavior remains unknown. Sources:
https://learn.chatgpt.com/docs/app-server/ (model/list, streaming events and
errors sections); https://developers.openai.com/blog/codex-as-a-platform.

## Local observations
Windows `codex --version` reported codex-cli 0.165.0; it also warned that creating PATH aliases under its temporary directory was denied in the present sandbox. A bounded local `codex_probe.py` read-only check completed `initialize`, `model/list` (eight catalog entries) and `account/read` (account present) with exit 0 outside the restricted sandbox. The same probe timed out inside the restricted sandbox. The probe started no thread or turn and printed no account details. Actual limits, role-model entitlement, inference, error classification and pause behavior are unknown. Pi/Codex credential interoperability has not been established and is not relied upon: the proposed Pi extension would use a separate Codex app-server process and Codex-managed account.

## Follow-up acceptance checks
Use isolated synthetic data and local fakes for transport tests. Verify initialization, event streaming and cancellation using the installed version and supported account without exposing credentials. Before any real model smoke, establish a bounded resource allowance and record the selected model, cost/usage, error and outcome; do not equate an infrastructure failure with a model result. Observe model error without another model call, persist only already-available data, and require explicit resume. Retain existing role-profile configuration and test it independently of the operator chat.

## Operator chat implementation boundary
The Pi chat uses a separate `codex app-server` child process and sends
`approvalPolicy: never` plus an explicit `readOnly` sandbox on turns. Read
access is restricted to the campaign repository cwd; thread start is also
configured with the read-only sandbox and no approval requests. Fake-server
tests assert those request fields. This enforces a no-write boundary for the
operator chat; real Windows sandbox availability and account behavior remain
unverified, and an unsupported sandbox must fail closed. It is not a substitute
for the app-server's documented non-production status.

## Operator chat acceptance update (2026-10-30)

A bounded live-turn attempt using codex-cli 0.165.0 stopped before 	hread/start and 	urn/start: the CLI rejected an unsupported 	ools.view_image override. That override was removed. A preflight then found configured MCP servers enabled, so the chat now fails closed before spawning app-server unless codex mcp list --json confirms all servers disabled. The local fake-server suite covers malformed/unknown MCP inventory and active MCP rejection. No live Luna inference or usage was observed; app-server thread/turn and Windows Terminal acceptance remain outstanding. Do not disclose MCP names or configuration values in reports.

## Operator chat acceptance update (2026-10-30)

A bounded live-turn attempt using codex-cli 0.165.0 stopped before `thread/start` and `turn/start`: the CLI rejected an unsupported `tools.view_image` override. That override was removed. A preflight then found configured MCP servers enabled, so the chat refuses launch if `codex mcp list --json` reports enabled servers or cannot verify the inventory. Targeted synthetic UI suite: 27 passed. No live Luna inference or usage was observed; app-server thread/turn and Windows Terminal acceptance remain outstanding.

Security review note: preflight inspection followed by app-server startup has a TOCTOU window. A concurrent Codex config change could enable an MCP after the check; `mcp_servers={}` is not an override that clears configured entries in observed codex-cli 0.165.0. The post-initialize effective-config check cannot prevent an MCP initialized before that check. Treat preflight as best-effort only. No race-free isolation retaining authenticated access has been established; do not claim it.
