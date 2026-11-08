# RPDI-13R ChatGPT plan setup (official SIWC)

This uses the official [Sign in with ChatGPT for open-source apps](https://developers.openai.com/siwc/token-sharing-open-source).
It is NOT an API key, Codex CLI credential export, LearnHarness code
or ChatGPT backend-api request. All OAuth credentials stay in the private
host owner vault and never enter model-visible task files. This preview
uses the same plan usage as other ChatGPT apps; it has no separate quota.

## Install and connect — no inference yet

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
bananloop-chatgpt status
bananloop-chatgpt login
bananloop-chatgpt models
```

The `login` command starts a loopback HTTP listener on `127.0.0.1`,
opens the system browser at `auth.openai.com`, and requests the
ChatGPT plan permission. Complete the interactive consent yourself.
`status` displays no bearer tokens, and `models` lists only visible
model slugs for the signed-in account. An advertised model does not
guarantee entitlement to a successful request.

**Security:** The vault defaults to `~/.config/bananloop/chatgpt-plan.json`
(or `$XDG_CONFIG_HOME/bananloop/chatgpt-plan.json`), with mode 0600 and
a mode 0700 directory on Linux. Do not include that file in archives,
git commits, diagnostic logs or agent workspaces. If existing directories
are too permissive, inspect them and fix owner-only permissions before use.

## Tests, zero subscription calls

```bash
python -m pytest tests/test_chatgpt_plan.py tests/test_v2_research_runtime.py -q
```

For the full offline Linux acceptance suite (no subscription requests):

```bash
PYTHONPATH=tests/plugins:tests:src:. python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

## Create or resume a general v2 Exploration campaign

The v2 command persists a frozen v2 spec, exact public read-path allowlist,
SQLite graph, durable queue, and budget. It makes **zero model requests** by
default:

```bash
bananloop v2 explore --repo . --state-dir /tmp/bananloop-v2-state \
  --request-id toy-study-001 \
  --goal "Test whether a controlled intervention changes the toy outcome" \
  --read-path README.md
```

After reviewing the frozen goal, model, paths, and call budget, explicitly
authorize one ChatGPT plan request for the same persisted campaign:

```bash
bananloop v2 explore --repo . --state-dir /tmp/bananloop-v2-state \
  --request-id toy-study-001 \
  --goal "Test whether a controlled intervention changes the toy outcome" \
  --read-path README.md --approve-up-to-calls 1
```

The same stable request ID, goal, repo HEAD, paths, model and budget must be
used to resume the campaign. The default total `--max-agent-calls 8` is split
across four pinned roles: researcher, independent protocol verifier, optimizer,
and integration verifier. The current allocation gives four calls to the
researcher, one to protocol review, two to the optimizer, and one to integration
verification. Parent Exploration and the child Optimization each use only their
assigned roles. A command's
`--approve-up-to-calls` allowance covers either role and can be at most that
campaign total. If a protocol proposal is waiting for review, a resumed command
spends its next approved call on the verifier before starting another research
iteration. The pending job and receipt are recovered from SQLite. Uncertain
dispatched calls are not retried. The command runs the v2 scheduler/queue/
Research Ledger path and does not call the legacy Engine. Protocol discovery
and review are wired to this entry point. To create an Optimization child,
provide an exact `--optimization-write-path` that is also listed in
`--read-path`; the reported proposal must then receive an explicit owner
decision through `v2-pivot decision` before `v2-pivot execute` and `v2 optimize`.

## Explicit one-shot owner experiment (optional, consumes ChatGPT plan)

Only after you personally decide to spend one request:

```bash
WORKDIR="$(mktemp -d /tmp/bananloop-siwc-smoke.XXXXXX)"
python examples/v2_research_diagnostic/run_one_chatgpt.py \
  --work-dir "$WORKDIR" --model gpt-6-luna \
  --goal "Ask one concrete, falsifiable research question" --approve-one-call
```

If your account's catalog does not contain `gpt-6-luna`, **do not**
silently change the model used for benchmark comparison. Confirm a
different model explicitly before separate experiments. This command
allows only one durable research worker job, no self-initiated shell
or network tools, no hidden evaluator, and no automatic retries. It
does not prove complete v0.2 Full or satisfy RPDI-14.

The same official Responses transport has protocol-specific
`ChatGPTPlanOptimizer`, `ChatGPTPlanProtocolVerifier`, and
`ChatGPTPlanIntegrationVerifier` adapters. Protocol discovery in `v2 explore`
uses the pinned tool-free verifier role and stores its receipt in SQLite. The
optimizer sees only the Core-provided source allowlist, while the integration
verifier sees the public Core evidence summary and has no tools or paths. The
`v2 optimize` command executes an already-approved Optimization child; the
operator still reviews and approves the pivot through the v2-pivot flow. The
integration verifier reviews trusted Core receipts and never runs the
evaluator itself.

The full v2 CLI route uses the existing SIWC login and has no automatic replay
after an uncertain dispatch. `v2 explore` and `v2 optimize` default to zero
model requests; each invocation that should spend subscription usage needs an
explicit `--approve-up-to-calls N`. The v2 Optimization child is funded from the
parent's frozen role/call vector, and trusted DEV/VALIDATION/HOLDOUT evaluation
is executed by Core after the candidate is committed.

The SIWC Responses preview rejects a server-side `max_output_tokens`
parameter, so the advertised `tokens_per_instance` here is **not**
an enforceable token ceiling for official benchmark purposes.
Only received provider usage counts may be reported; missing counts
must be `UNKNOWN`. No API-key fallback is ever configured.

`bananloop-chatgpt logout` clears usable local tokens. To revoke remote
app access, also use ChatGPT Settings → Apps/Connections (wording may vary).
