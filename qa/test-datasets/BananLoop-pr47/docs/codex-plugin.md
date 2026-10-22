# Codex plugin architecture

## Decision

BananLoop uses a hybrid integration:

- Codex plugin/skill owns the chat-facing entry point.
- The skill scouts the repository and narrates observable campaign events in the
  current Codex chat.
- BananLoop core owns the scientific loop and durable state.
- The bootstrap agent may construct a project-specific evaluator adapter.
- The core smoke-checks, freezes and hashes that evaluator before optimization.
- Implementers never receive the frozen evaluator assets.
- DEV, validation and final holdout remain core-owned decisions.

This preserves the useful part of a goal-style Codex workflow without making the
worker its own grader.

## Why not move the whole loop into a Codex skill?

A skill is instructions. It improves discovery and workflow ergonomics, but it
is not an independent trust boundary. If the same Codex turn can inspect the
grader, change the grader, choose the candidate and declare success, BananLoop
loses its main advantage.

Codex lifecycle hooks can continue a turn at Stop and can run commands. The
plugin now bundles an optional thin continuation hook, but not a hook-driven
optimization loop: direct editing in the user's checkout would weaken the
current hidden evaluator workspace and replay model unless every verdict still
belongs to the core.

The current split is therefore:

```text
  user goal
   |
   v
Codex skill ----------------------------- scout + chat UX
   |                                         |
   |                              doctor inventory + live CLI trace
   |
   v
bananloop goal
   |
   +--> bootstrap agent --> .bananloop_eval/** in private workspace
   |                           |
   |                      core smoke gate
   |                           |
   |                      freeze + hash
   |                           |
   +--> campaign core <--------+
           |
           +--> proposer / implementer (sanitized workspace)
           |
           +--> independent evaluator
           |
           +--> DEV -> VALIDATION -> FINAL HOLDOUT
           |
           +--> champion / reject / stop / export
```

## Invocation

From a target repository:

```bash
bananloop doctor --json
bananloop goal "Improve measured quality without regressions"
```

The `bananloop-goal` skill first summarizes repository resources found by
`doctor` (benchmark/evaluator files, CSVs, Compose/config files, and environment
file/variable names only), then runs `goal` in the foreground so `[BananLoop]`
progress is visible in the chat. Resource discovery never prints `.env` values.
Generated bootstrap/evaluator code never receives `.env` values; a required
credential must already be available through the process environment or the
campaign pauses for that genuinely missing access.
The skill explains experiment hypotheses, implementation summaries, aggregate
metric changes, and core decisions from observable output; it does not infer a
verdict from worker claims. A genuine missing credential, asset, external
service, or semantic choice can still pause for user input.

Campaigns are indexed per repository. Repeating the same normalized goal
selects its existing campaign; the same key resumes a paused campaign and
returns a terminal result without rerunning it. Pass `--request-id ID` to make
retry identity explicit or to create a new intentional run for the same goal.
Use `bananloop campaigns list --repo .` to inspect IDs and the active campaign,
then pass `--campaign-id ID` to run/resume/status/stop/report/export. Existing
single-campaign state remains available as `legacy`; explicit `--state-dir`
continues to select a standalone state directory.

Preparation-only:

```bash
bananloop goal "Improve measured quality without regressions" --prepare-only
```

The longer `bananloop start` and expert `bananloop init` surfaces remain for
explicit backend, model, evaluator and budget control.

## Models and providers

The minimal path does not duplicate Codex model configuration. With no
`--model`, the Codex adapter leaves model choice to the user's Codex
configuration. This keeps normal Codex profiles and user-level provider settings
authoritative instead of creating a second configuration hierarchy in
BananLoop.

Pi and command adapters remain explicit advanced backends. For a campaign that
needs repeatable role-specific settings, pass `--agent-profiles agents.toml` to
`goal`, `start`, or expert `init`. The TOML has `[default]` plus optional
`[roles.bootstrap]`, `[roles.propose]`, `[roles.implement]`, and `[roles.analyze]`
tables. BananLoop freezes these settings in the campaign contract and records a
non-secret launch receipt. Omit the file to preserve existing Codex CLI defaults.
Credentials must remain in the provider's normal environment-based store.

## Stop hook bridge

The plugin bundles a synchronous Stop hook as a small continuation bridge:

```text
Codex wants to stop
      |
      v
Stop hook -> read repo/session binding + durable campaign status
      |
      +-- terminal / paused / busy / unknown -> allow stop
      |
      +-- RUNNING with no supervisor lock -> request one bounded continuation
```

The hook reads campaign SQLite state and writes only a hashed session-to-campaign
binding in the registry. It never starts an agent, evaluator, or campaign and
never decides candidate quality. It requires the skill's explicit
`BananLoop campaign: <id>` marker before binding a Codex session to its first
campaign. Session IDs are hashed in the project registry. Ambiguous or
unavailable state fails open. A single `stop_hook_active` continuation gate,
the RUNNING status, and a free campaign lock prevent repeated or concurrent
resume prompts; the continuation message tells Codex to inspect durable receipts
and not retry an ambiguous RUNNING action.

Codex skips untrusted plugin hooks until the user reviews and trusts the current
definition in `/hooks`. Hooks may also be disabled or unsupported in a given
host/orchestration mode; the skill and direct `bananloop resume` command remain
available. The manifest supplies a Windows `commandWindows` override, but local
fixture tests do not substitute for a trust-approved desktop E2E run. See the
[Codex hook contract](https://developers.openai.com/codex/hooks).

## Portable plugin layout

The distributable plugin lives at `plugins/bananloop/`. Its root `plugin.json`
contains the portable Agent Plugins identity (`name`, `version`, and
`description`) and places Codex-specific presentation fields under
`extensions.com.openai`. The bundled `skills/bananloop-goal/SKILL.md` is
discovered from the plugin's `skills/` directory. The existing
`.codex-plugin/plugin.json` remains as a compatibility manifest for Codex
installations that still use the legacy layout.

Install or link the plugin from the `plugins/bananloop` directory using the
Codex plugin workflow. Keep the portable root manifest and legacy manifest's
name, version, and user-facing descriptions aligned when changing plugin
metadata. The plugin only supplies the chat entry point; independent
measurement and campaign accounting remain in the BananLoop CLI/core.

Campaign reports include `attempt_accounting`, which groups attempts into
mutually exclusive outcomes. `scientific_trials` counts valid measured
decisions; no-ops, blocked work, ungrounded proposals, pre-evaluation rejects,
and infrastructure failures are reported separately. Older campaign files
remain readable; `durable_experiment_counter` preserves their stored
counter, and
`counter_matches_classification` makes any historical discrepancy visible
without rewriting the campaign.
