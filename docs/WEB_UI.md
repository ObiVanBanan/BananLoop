# BananLoop Web Viewer (experimental)

An optional desktop-browser dashboard for committed campaign state, derived from the approved black/yellow BananLoop references. The goal and six-stage projection are pinned at the top; the live event feed extends down the left side with a chat-like composer. DEV chart, budget/epoch counters and a searchable hypotheses table are on the right. **No separate Details panel.**

## Start (Windows / Linux)

Install from this checkout (Python 3.11+ and Node.js 18+ required):

```powershell
python -m pip install -e .
# In the repository being optimized (with a registered active campaign):
bananloop-ui --repo "C:\path\to\your-project"
# Or select a specific campaign:
bananloop-ui --repo "C:\path\to\your-project" --campaign-id c-...
# Explicit standalone/legacy state directory:
bananloop-ui --repo "C:\path\to\your-project" --state-dir "C:\path\to\state"
# Demo with invented data, no model calls:
bananloop-ui --demo
```

Open http://127.0.0.1:8787; use `--port 8790` if needed. Stop with Ctrl+C. Node is needed only for the optional UI. The regular `bananloop` CLI has no new runtime dependencies.

**Without installing the BananLoop Python console script**, from the BananLoop source directory:

```powershell
node src/bananloop/web_ui_static/server.mjs --repo "C:\path\to\your-project" --campaign-id "c-..." --python "C:\path\to\BananLoop\.venv\Scripts\python.exe"
```

## Real integration and limitations

- `bananloop-ui` automatically selects the active campaign using the existing `CampaignRegistry` if no campaign ID is given. If none exists, it exits with an explicit error; run `--demo` for mock data.
- Local Node server binds **127.0.0.1 only**, serves allowlisted static assets and exposes **GET-only** `/api/config`, `/api/snapshot`, `/api/events?after=N`. It invokes `python -m bananloop.cli operator-snapshot/operator-events`; no shell command strings or arbitrary write endpoints.
- DEV metrics, stage, budget, hypotheses and committed event history are real only in live mode. Internal fine-grained stages map to the six display stages; unknown stages remain unknown. Do **not** infer full agent thought logs, token costs, CPU/RAM or HOLDOUT results from the UI.
- The chat-like composer in **live mode is local-only**. It does **not** send messages to the model. In demo mode, replies are explicitly simulated. `Pause/Reset` buttons affect demo mode only. Use `bananloop pause/resume` CLI for real control.
- A failed initial live snapshot displays CONNECTING/STALE with empty data instead of showing synthetic results as if they were live. Subsequent polling retries every 3 seconds.
- This is an opt-in experimental read-only UI, **not** a v0.1 release gate. No unrelated Pi TUI files or core evaluator logic are modified.

## QA

```powershell
node --test tests/web_ui_model.test.cjs
node --test tests/web_ui_server.test.cjs
python -m pytest tests/test_web_ui.py -q
```

The Node HTTP test uses a fake CLI and never touches a user campaign. Python launcher tests verify argument selection and fail-closed behavior. Full project regression suite is expected in the target environment and CI.