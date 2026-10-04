---
name: bananloop-goal
description: Start or prepare a BananLoop autonomous improvement campaign from the current repository and one human-language goal. Use when the user wants Codex to keep improving a repo with independent measurement rather than a one-shot coding pass.
---

# BananLoop Goal

Treat the user's human-language goal as authoritative. Repository briefs, issue
files, tests, benchmarks, and reference assets enrich the goal; they do not
replace it or force the user to restate it.

BananLoop is the scientific supervisor. Do not recreate its accept/reject loop
inside the chat and do not decide that your own code is better.

## Workflow

1. Work from the repository the user wants to improve.
2. Run `bananloop doctor --json`.
3. If the command is missing, tell the user BananLoop must be installed from its
   checkout. Do not silently substitute a home-grown loop.
4. If the user asked only to inspect or prepare the campaign, run:
   `bananloop goal "<goal>" --prepare-only`.
5. Otherwise run:
   `bananloop goal "<goal>"`.
6. Let BananLoop own evaluator construction, smoke/repair, freezing/hashing,
   baseline, DEV/VALIDATION/FINAL HOLDOUT, budgets, champion promotion,
   rejection, retries, resume, and final evidence.
7. If BananLoop returns `PAUSED_NEEDS_INPUT`, report only the exact requested
   inputs. Do not invent references, credentials, labels, or a replacement
   objective.
8. If preparation fails because BananLoop missed an environment capability that
   `doctor --json` actually reported, treat that as a BananLoop bootstrap bug,
   not as missing operator input.
9. When the campaign finishes, summarize the champion delta, experiments tried,
   stop reason, and export path if one was produced.

## Model selection

Do not add `--model` by default. BananLoop intentionally lets Codex inherit the
user's normal Codex model/configuration when no explicit model override is
provided. Only use the advanced `bananloop start` flags when the user explicitly
asks for a different backend/model/provider or campaign budget.

## Independence boundary

The project-specific evaluator may be generated during bootstrap, but after the
core readiness gate passes it is frozen, hashed, protected, and hidden from
implementation workspaces. Never copy evaluator internals, holdout data, hidden
labels, or protected assets into the implementation context.

Do not replace this boundary with a Codex Stop hook that simply asks the same
agent whether its result is good. Lifecycle hooks can improve UX later, but the
measurement verdict must remain a BananLoop-core action.
