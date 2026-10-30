# Offline terminal prototypes

Both candidates render the same fixed synthetic state from `shared/mock.mjs`.
There is no engine, campaign database, authentication, network, or model access.
The plotted points and numbers are illustrative. Input is local mock handling:
`/pause` or `stop experiment` changes only an in-memory flag; `/resume` restores
it; `/quit` exits. Arbitrary text is echoed as a mock input, not sent to a model.

## Standalone (Node)

```
node plan/operator-roadmap/feasibility/standalone/prototype.mjs --snapshot --width=120
node plan/operator-roadmap/feasibility/standalone/prototype.mjs --snapshot --width=60
node plan/operator-roadmap/feasibility/standalone/prototype.mjs
node plan/operator-roadmap/feasibility/shared/mock.test.mjs
```

This is a line-oriented smoke prototype, not a full-screen UI: it prints a new
frame after input or resize. It needs Node but has no npm dependencies. Layout is
two-column at 100 or more columns, stacked below that.

## Pi extension

Run from the repository root in an interactive terminal, after choosing a new
empty, writable local config directory `PI_CODING_AGENT_DIR`:

```
pi --offline --no-session --no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-approve -e ./plan/operator-roadmap/feasibility/pi/prototype.ts
```

Enter `/bananloop-demo` inside Pi. Escape or `/quit` closes the component.
Do not submit ordinary prompts to Pi during this offline demonstration. An empty
Pi config avoids loading user packages or credentials; it can cause Pi startup
to fail for lack of a configured model. The component uses Pi `ctx.ui.custom`
and the same renderer as the standalone prototype. The Pi UI path and resizing
must be observed in a real Windows terminal before claiming success.

## Known limitations

Neither candidate proves chat, Codex authentication, actual campaign updates,
real stop/resume, cost tracking, colour, or Windows Terminal layout. The graph is
relative to the minimum and maximum scores, and no attempt is made to imitate
terminal pixel artwork. Text displayed from mock input strips OSC/CSI escape
sequences, C0/C1 controls and bidi controls; other ESC-prefixed controls are
not covered. A Windows-hosted PTY smoke check at 80 columns
executed the Pi component; this is not a visual check or resize test in Windows
Terminal. Both are throwaway
feasibility artifacts, not product code or a selected framework.

To reproduce the line-oriented input check from the repository root, pipe the mock commands:

```powershell
'/pause','/resume','/quit' | node plan/operator-roadmap/feasibility/standalone/prototype.mjs
```
