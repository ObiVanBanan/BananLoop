# Codex plugin architecture

## Decision

BananLoop uses a hybrid integration:

- Codex plugin/skill owns the chat-facing entry point.
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

Codex lifecycle hooks can continue a turn at Stop and can run commands, so a
future hook-driven mode is technically possible. It is not the default MVP
because direct editing in the user's checkout would weaken the current hidden
evaluator workspace and replay model unless the hook delegates every verdict to
the core anyway.

The current split is therefore:

```text
user goal
   |
   v
Codex skill ----------------------------- UX only
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

Pi and command adapters remain explicit advanced backends for now. Role-specific
model routing is deliberately not part of this UX patch; adding it should be a
core contract change with durable role configuration rather than hidden skill
prompting.

## Hook direction

A later experimental mode may use a trusted synchronous Codex Stop hook only as
a continuation trigger:

```text
Codex wants to stop
      |
      v
Stop hook -> BananLoop core evaluates candidate
      |
      +-- accepted/terminal -> allow stop
      |
      +-- rejected/continue -> core returns bounded continuation reason
```

That mode is acceptable only if the evaluator still lives outside the worker's
editable/visible workspace, the hook cannot mutate the frozen contract, and all
verdicts/budgets remain durable in BananLoop state. Until those invariants are
implemented and dogfooded, the plugin is intentionally skill-only.
