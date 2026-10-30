# Operator terminal feasibility

## Scope and criteria

Compare Pi extension and standalone terminal surfaces using the same synthetic
fixture: a fixed goal, stage strip, live events, DEV progress, hypotheses,
budget, epoch and mock input with pause/resume. Compare wide and narrow layout,
Windows compatibility, resize, dependencies, ability to host Codex, and the
maintenance burden. No live campaign, account, model call or network is needed
for these rendering probes. The synthetic data is never benchmark evidence.

The fixture represents the six display stages `PROPOSE`, `SELECT`,
`IMPLEMENT`, `EVALUATE`, `LEARN` and `REPEAT`, a goal, sequenced-looking live
events, a DEV graph, hypotheses, budget, epoch and mock input. Layout checks
cover 40, 60, 80 and 120 columns; 60 and 120 are the saved standalone
snapshots. The comparison criteria are terminal fidelity and resize, Windows
operation, input handling, Codex transport/authentication, dependency and
maintenance cost, and whether all controls remain synthetic and offline.

## Evidence

| Criterion | Standalone spike | Pi extension spike |
| --- | --- | --- |
| Mock layout | Node 24.19.0; snapshots at 60 and 120 columns; shared renderer tested at 40, 60, 80, 120 columns | Same shared renderer; component displayed at 80 columns in a Windows-hosted PTY |
| Mock input and pause | Piped `/pause`, `/resume`, `/quit` produced RUNNING, PAUSED, RUNNING and exited | In a PTY `/bananloop-demo`, `/pause`, `/resume`, `/quit` opened the component, displayed PAUSED then RUNNING, and returned to Pi |
| Resize | Renderer checked with several widths; actual interactive resize not observed | Renderer checked with several widths; PTY tool has no resize operation, interactive resize not observed |
| Cost and dependencies | Node 24, no npm packages; line-based sample, not a complete interactive screen | Installed Pi 1.0.2 and Node; explicit local extension; real full-screen custom Pi component |
| Codex chat and authentication | No chat integration | No chat integration; a separate local Codex app-server read-only handshake succeeded |
| Campaign data and controls | Synthetic data; mock only | Synthetic data; mock only |
| Safety | Shared rendering strips OSC/CSI escape sequences, C0/C1 controls and bidi controls; other ESC-prefixed controls are not covered. Offline regression test covers representative sequences | Shares the same renderer; production security remains unverified |

The Pi PTY used an empty isolated `PI_CODING_AGENT_DIR`, `PI_OFFLINE=1`,
`PI_TELEMETRY=0`, `--offline --no-session --no-extensions --no-skills
--no-prompt-templates --no-themes --no-context-files --no-approve`, and the
explicit local extension. Pi reported no models and `fd` missing; no model call
was made. After leaving the component, Ctrl-D exited Pi with process exit code
1. This does not demonstrate normal product exit behavior. The PTY output is
not a visual inspection in Windows Terminal.

The shared regression check is `node
plan/operator-roadmap/feasibility/shared/mock.test.mjs`. Execution and manual
reproduction instructions are in [prototypes.md](prototypes.md).

The standalone prototype is a Node-only, no-package line renderer. The Pi
prototype is a locally loaded full-screen component, exercised in a Windows
PTY. Both use identical mock state and in-memory pause/resume. Resize was
checked by rendering at several widths, not by interactively resizing a real
Windows Terminal window.

## Decision and remaining validation

Use a **project-local Pi extension** as the initial full-screen operator shell.
Keep BananLoop's Python engine and existing Codex role adapter authoritative.
The operator conversation will use a separate local Codex app-server process,
called by the extension through a narrow, typed bridge. Pi supplies terminal
rendering and input; its own model session is not the campaign's Codex session.
This choice follows the user's Pi preference and the observed Pi custom screen
and command handling. The standalone spike only demonstrates line-oriented
output and would require additional full-screen and input infrastructure.

On Windows with codex-cli 0.165.0, `codex_probe.py` completed read-only
`initialize`, `model/list` (eight catalog entries), and `account/read` (account
present) with exit 0. The same probe timed out inside the restricted sandbox;
it succeeded with normal local access. The probe deliberately prints no model
names or account details and starts no thread or turn. A catalog entry is not
proof that inference on that model succeeds. Role models continue to use the
existing campaign profile mechanism, while the operator's conversational model
will be configured independently before launch.

The Pi extension and app-server have not yet been joined in one working chat.
Actual Codex model turns, limit errors, rendering and resize in Windows
Terminal, installation/packaging, and narrow-window keyboard focus remain
acceptance checks for the production UI. If Pi cannot host the verified Codex
bridge or fails the Windows Terminal resize and input checks, reopen this
decision and compare a standalone full-screen client rather than shipping the
line-oriented mock. No benchmark or account access claim follows from these
offline and read-only probes.
