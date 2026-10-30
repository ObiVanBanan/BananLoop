<p align="center"><img src="assets/bananloop-banner.png" alt="BananLoop — a friendly banana astronaut among ASCII planets" width="100%" /></p>

# BananLoop

**Your coding agent. Upgraded.**

**Fresh context. Persistent memory. Measurable progress.**

[![Research Preview](https://img.shields.io/badge/v0.1.0-Research_Preview-f4bd35?style=flat-square)](CHANGELOG.md)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)
[![License: MIT](https://img.shields.io/badge/License-MIT-55a87a?style=flat-square)](LICENSE)

[Quick start](#quick-start) · [Live-style demo](#see-the-loop-in-action) · [How it works](#how-it-works) · [Expert mode](#expert-mode) · [Roadmap](docs/ROADMAP.md)


## Give your coding agent a goal. BananLoop keeps going.

**BananLoop is a local, open-source autonomous improvement loop for software repositories.** Bring a repository, a measurable goal, and a coding agent. BananLoop proposes experiments, selects promising hypotheses, applies candidate changes, independently measures outcomes, and uses the results to decide what to try next.

Instead of forgetting what failed or accepting an agent's self-reported success, it maintains a **durable experiment history**, keeps a **validated champion**, and enforces **evaluation boundaries**.

**For:** developers using Codex and other supported coding-agent backends to improve quality, performance, reliability, retrieval, or other measurable behavior. Research is one use case—not the only one.

### Built for the loop

| Capability | What it means |
| --- | --- |
| **Goal-driven loop** | Keep iterating toward an approved measurable objective. |
| **Fresh context** | Separate role execution and optional fresh-perspective proposal/repair, without a single endless chat transcript. |
| **Research memory** | DEV-only history, failure signals, hypothesis genealogy, and strategy shifts between exploit/explore/reframe. |
| **Model per role** | Configure different agent models for bootstrap, proposal, selection, implementation, and analysis. |
| **Independent evaluation** | Supervisor-owned evaluator, reproducible candidate snapshots, gates, and guardrails. |
| **Budget & resume** | Track attempts, agent calls, evaluator calls, and restore durable action state. |
| **Semantic selection (opt-in)** | Compare admissible hypotheses with an LLM while deterministic gates remain authoritative. |

> **Research Preview:** BananLoop is an experimental developer tool, not a guarantee of improvement. The evaluation contract must be independently trustworthy. Treat the default local workspace isolation as a protection against ordinary accidental leakage, **not** a security boundary for running hostile code.

## How it works

<p align="center"><img src="assets/bananloop-how-it-works.png" alt="BananLoop research loop: goal, propose, gates, select, implement, independent evaluation, decide, learn, and repeat" width="100%" /></p>

The **coding agent** proposes and implements. The **BananLoop supervisor** freezes the evaluation contract, checks scope, records outcomes, and decides whether a candidate may replace the current champion. The selector is advisory; it cannot bypass the deterministic gates.

## Quick start

**Requirements:** Python 3.11+, Git, and an installed/authenticated supported coding-agent CLI (Codex by default). Install BananLoop from this repository—there is no claim of a published PyPI package.

```bash
git clone https://github.com/ObiVanBanan/BananLoop.git
cd BananLoop
python -m pip install -e .
```

From **your own project's repository**:

```bash
cd /path/to/your-project
bananloop doctor --json
bananloop goal "Improve the measured quality of this project without regressions"
```

The goal command prepares an evaluator and starts a bounded campaign. If the project lacks a trustworthy evaluation oracle, BananLoop may pause and ask for operator input instead of inventing ground truth.

**See settings before spending experiment calls:**

```bash
bananloop prepare --repo . --goal "Improve measured quality without regressions"
```

### Simple vs. Expert

| Simple mode | Expert mode |
| --- | --- |
| Start with repository + goal | Explicit metric, evaluator, guards, visibility, budgets |
| Guided project preparation | Agent models and role-specific configuration |
| Fast path for suitable projects | Reproducible frozen experiment contracts |

See [the detailed CLI and campaign guide](docs/reference/cli-and-campaigns.md) for command options, Windows examples, budget controls, and privacy boundaries.

## See the loop in action

The repository includes a **deterministic fixture** that demonstrates accept → reject → accept, and a **CPU-only mini matcher** used for agent integration smoke testing.

**Reproducible offline fixture (no LLM required):**

```bash
python examples/fixture/create_fixture.py /tmp/bananloop-fixture
```

Continue with the copy-and-run CLI sequence in the [fixture walkthrough](docs/DEMO.md).

**Recorded independent smoke (October 29, 2026, one mini-matcher campaign):**

| Stage | DEV quality |
| --- | ---: |
| Baseline | 0.4375 |
| Candidate after the selected hypothesis | 1.0000 |
| Delta | +0.5625 |

The QA run used Luna with the opt-in semantic selector, three agent calls, and two evaluator calls. This is a **single DEV-only fixture result**, not official benchmark leadership or evidence of better outcomes than standalone coding agents. See [the full demonstration and limitations](docs/DEMO.md).

## Expert mode

Bring an existing evaluator contract if you want complete control:

```bash
bananloop init \
  --repo /path/to/project \
  --goal "Improve measured quality without regressions" \
  --eval-cmd "python scripts/evaluate.py" \
  --metric quality \
  --direction maximize \
  --target 0.85 \
  --min-improvement 0.005 \
  --editable "src/**" \
  --protect "scripts/evaluate.py" \
  --protect "tests/**" \
  --agent codex \
  --max-experiments 4

bananloop run --repo /path/to/project
bananloop report --repo /path/to/project
```

**Optional semantic hypothesis selector:** add `--selector-mode llm` to initialization (the default is `deterministic`). It reads the approved proposal portfolio and must return a gate-approved candidate ID and source-backed DEV evidence references.

**Role-specific models:** write a TOML profile, then pass `--agent-profiles path/to/agents.toml` during setup:

```toml
[default]
kind = "codex"

[roles.propose]
model = "your-proposer-model"

[roles.select]
model = "your-selector-model"

[roles.implement]
model = "your-implementation-model"
```

Backends available in the codebase include **Codex CLI**, **Pi RPC**, and a generic JSON command adapter. Compatibility depends on installed providers and model access. See the [full configuration reference](docs/reference/cli-and-campaigns.md).

## Use cases

- **Improve software quality:** make testable changes against a fixed suite or project-specific metric.
- **Optimize retrieval and ML components:** evaluate matching, ranking, and other measured changes.
- **Investigate regressions:** capture why a candidate failed and use the insight in a later hypothesis.
- **Performance tuning:** retain only changes that clear measurable latency or throughput constraints.
- **Compare ideas:** use a reproducible experiment history instead of one-off agent intuition.

## What comes next

**Planned, not shipped:** a visual research UI with a hypothesis tree and metric charts; human-approved major architecture pivots; richer multi-step research plans and benchmark coverage.

Read the [roadmap](docs/ROADMAP.md) and [release status](CHANGELOG.md).

## Community & security

- [Contributing](CONTRIBUTING.md) — development, testing, and PR conventions.
- [Code of conduct](CODE_OF_CONDUCT.md) — respectful collaboration.
- [Security policy](SECURITY.md) — private vulnerability reporting.
- [License](LICENSE) — MIT.
- [Issues](https://github.com/ObiVanBanan/BananLoop/issues) — bugs and proposals.

<sub>Questionable ideas. Measurable experiments. Occasionally production.</sub>
