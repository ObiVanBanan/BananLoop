# Research report: operator roadmap

Scope: local `main` checkout, commit 76db695, inspected 2026-10-29; remote freshness was not verified.

## Existing product and interface
- `README.md` and `docs/ROADMAP.md` describe a local, bounded campaign engine and mark a visual interface, human-approved pivots and composite hypotheses as future work.
- `src/bananloop/cli.py:406` wires progress to printing; :420 holds the campaign lock during run/resume; :460 exposes status and :515 requests stop. There is no implemented TUI.
- `src/bananloop/agents/codex.py:500` uses separate `codex exec --ephemeral` invocations with role model and reasoning parameters. This is not a persistent interactive chat. `src/bananloop/agent_profiles.py:14` loads role profiles; `docs/reference/cli-and-campaigns.md:439` documents Codex configuration fallback. `src/bananloop/agents/pi.py:449` implements Pi RPC.
- Official Codex documentation describes app-server streaming and lifecycle management: https://developers.openai.com/blog/codex-as-a-platform and https://learn.chatgpt.com/docs/app-server. Exact compatibility with installed version and account remains unverified. Pi documents extensions and terminal components at https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/extensions.md.

## Execution and persistence
- `docs/reference/cli-and-campaigns.md:16` and `src/bananloop/engine.py:2161` describe and implement baseline, propose, execute, snapshot, verify, evaluate, decide, optional analysis and validation, promotion/rejection, record. Six UI stages require an explicit projection; SELECT is not a separately persisted stage.
- `src/bananloop/store.py:14` persists campaigns, experiments, actions, decisions, evaluations and sequenced events. Actions have durable IDs, request hashes and retry states; `src/bananloop/engine.py:324` handles recovery, and `store.py:380` handles reservations. Failed/unknown reservations are not automatically refunded.
- `docs/reference/cli-and-campaigns.md:381` documents stop and Ctrl+C. STOPPED is terminal (`engine.py:2200`), whereas infrastructure pauses can resume with explicit retry controls (`engine.py:2206`, CLI reference :350). Stop-then-resume is not established behavior.
- Agent failures are mapped to PAUSED_INFRA in `engine.py:1164` and :1466; classification and notification for every possible model/limit error are unverified.
- A new attempt starts from champion (`engine.py:1127`, :1368, :2243); neutral intermediate changes do not automatically accumulate. Scope enforcement occurs before evaluation (`engine.py:1510`), evaluation integrity checks at :494 and :2197, holdout budget and final evaluation at :274 and :618. Context filtering isolates validation and holdout (`engine.py:155`).
- Plateau can trigger a bounded reframe or stop (`engine.py:2117`); approval and expansion of scope or budget for a new architecture remain roadmap proposals.

## Tests and benchmark
- `pyproject.toml` specifies Python 3.11+, pydantic and pytest test extra; `.github/workflows/tests.yml` runs pytest on Linux and Windows. No mutation configuration was found.
- Relevant tests include `tests/test_completion.py`, `test_engine.py`, `test_artifact_evaluation.py`, `test_codex_adapter.py`, and `test_cli.py`.
- `main/hf` contains ten untracked task directories; `hf/README.md` identifies the source collection, and `docs/dev-evidence-reflection.md:55` calls for fixed resources before a ten-task comparison. Directory count alone does not establish valid outcomes.

## Unknowns
- Actual live Codex model access, limits, authentication, streaming and error behavior on the target installation.
- Whether Pi extension, standalone terminal UI, or another integration best meets the interaction and portability goals; decide after a bounded prototype.
- Exact capability, consistency and freshness of stored live progress data and all interruption points require targeted verification.

## Local feasibility observations
- Windows reports Node v24.19.0, Python 3.13.1, codex-cli 0.165.0 and pi 1.0.2 using version commands. Codex warned about creating PATH aliases and Pi help warned of a settings lock permission error under the current sandbox; these do not establish normal user-session behavior.
- Pi help exposes offline mode, extension loading, session and context disabling, and theming. Rich is installed locally but is not a project dependency; Textual was not installed. No model calls were made.
- `docs/codex-plugin.md` documents an existing chat-facing Codex skill and core separation. Whether Pi can use Codex CLI credentials and selected models is unverified.
