# BananLoop operator screen: layout milestone

This is an opt-in Pi extension. `/bananloop demo` shows explicitly marked
synthetic data. `/bananloop <campaign-id>` reads committed campaign state and
events from the BananLoop CLI in the current repository. `/pause` submits the
typed CLI pause command and waits for
the committed `PAUSED_USER` status; acceptance alone is shown as pending.
`/resume` explicitly launches the existing synchronous CLI resume in a detached
process and observes campaign snapshots. Resume may invoke configured agents
and evaluators, and can continue after the screen closes. No retry flags are
passed.

If no resulting status is observed within 30 seconds, the UI reports `UNKNOWN`
and continues watching snapshots. A later status change resolves that notice
as a late observation; no timeout is treated as success or failure.

From this BananLoop checkout with Pi installed, for the demo:

```sh
pi --extension ./operator-ui/pi-extension.ts --theme ./.pi/themes/bananloop.json --use-theme bananloop
```

For a real campaign, run Pi from that campaign's repository, passing absolute
paths to `pi-extension.ts` and `bananloop.json` with `--extension` and `--theme`.
Use `/bananloop <campaign-id>`
to open its view, or `/bananloop demo` for synthetic data. `Tab`
switches live and progress panels in narrow terminals; `Esc` exits. Enter
`/pause` or `/resume` as an explicit control command. Other input remains local
unless it begins with the explicit `/chat <message>` command. The screen is
two-column at 100+ terminal columns and uses panels at smaller widths. The theme file is in
`.pi/themes/bananloop.json`; Pi requires project trust before it loads project
themes when those themes are loaded by discovery; the explicit `--theme` path
above loads the BananLoop palette without copying it into the campaign repo.
If the theme is unavailable, the extension remains readable with Pi's current
theme.

Pending architecture pivots are shown in the progress panel with their full
proposal ID and digest, architecture summary, rationale, expected effect,
changed paths, and resource estimates. Decide only by entering the exact
displayed identity: `/pivot approve <proposal-id> <sha256>`, or
`/pivot reject <proposal-id> <sha256> <continue_old_plan|change_conditions|end_campaign>`.
Before submitting, the extension rereads the snapshot and refuses if the
proposal changed, became stale, or no longer has a valid digest. The typed local
CLI records the decision; it does not resume the old plan, create a child, or
end the campaign. Those actions remain separate. A rejection choice is durable
operator intent, and a changed proposal requires a new review and digest.

The live view polls `operator-snapshot` and `operator-events` every two seconds
through `python -m bananloop.cli`, passing the repo path and campaign ID as
separate process arguments. It does not invoke a shell. Set `BANANLOOP_PYTHON`
to the project's Python executable if `python` is not on PATH. DEV points,
hypotheses, goal, budget and epoch come from the committed snapshot. Missing,
stale and disconnected data are labeled; the event cursor resumes from the last
accepted page. Event lines show sequence number and type, without exposing raw
payloads.

The extension starts the separate Codex app-server only after the first
`/chat <message>`. It displays a notice before each send: the message and a
bounded committed snapshot (goal, stage, status, DEV metric and budget) will be
sent to the user's configured Codex account. Event payloads, repository files,
validation/HOLDOUT data and secrets are not attached. Codex chat is separate
from BananLoop control; no campaign-control API is exposed to it.

The bridge starts an explicitly ephemeral app-server thread, so user turns are not added to persistent Codex thread history. It uses
an isolated temporary working directory, no writable roots, restricted read
access to that temporary directory, no sandbox command networking, and startup
overrides that disable web search, MCP/apps, shell/unified execution, plugins,
browser/computer tools and image viewing. Before creating a thread it reads the
effective app-server config and fails closed unless those settings are active.
The app-server still makes the requested model call to the configured account.
This is a local integration boundary, not a claim that all host-level security
risks have been eliminated. A failed, interrupted or uncertain turn is shown
without automatic replay; closing the screen kills the child process.

Chat returns a strict object with only `intent: "pause" | null` and a short
message. A pause intent creates a proposal; it never calls the control CLI.
The screen shows the effect explicitly: request the supervisor to cancel active
managed local work, preserve the last committed campaign/champion state, retain
uncertain outcomes as uncertain, and leave the campaign resumable. Confirm with
`/confirm-pause <token>` or reject with `/reject-pause <token>` within 30 seconds.
Only an exact, unexpired confirmation calls the existing typed pause CLI with a
stable request ID, then waits for the M3.4 status observation. Invalid output,
invalid tokens, rejection, timeout, stale state, or a changed campaign status
does not submit a control request. The model cannot invoke controls or propose
resume/end actions.

The screen polls committed snapshots and event pages every two seconds. A pause
request is not considered complete until a snapshot reports `PAUSED_USER`.
The CLI has no receipt-status command, so the UI cannot expose durable receipt
details and reports the observed campaign status instead. Resume's CLI process
is detached so it can finish a campaign cycle without blocking UI refresh.

Run `node operator-ui/layout.test.mjs`, `node operator-ui/campaign.test.mjs`,
`node --test operator-ui/codex-chat.test.mjs`, and
`node --test operator-ui/chat-adapter.test.mjs`, and
`node --test operator-ui/chat-pause-flow.test.mjs`
to check width handling, real-shaped JSON projection, safe process arguments,
typed pause/resume invocation, event cursor validation and disconnected display.
These use synthetic fixtures and an injected fake chat process; no model turn
is made. Real Windows Terminal resize, live control behavior, actual account
availability, sandbox enforcement and app-server error handling still need
end-to-end checks.
