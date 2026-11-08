# BananLoop v0.2 — live role diagnostics, private sealed evaluation, and accounting

**State: EXPLICIT-OPT-IN DIAGNOSTIC IMPLEMENTATION. No live paid/subscription trials have been started by this PR.** Neither this harness nor its synthetic dataset satisfies the independent RPDI-14 Full-vs-iterative-Codex release gate.

The code is stacked on [PR #140](https://github.com/ObiVanBanan/BananLoop/pull/140), which itself targets [PR #135](https://github.com/ObiVanBanan/BananLoop/pull/135). Nothing here automatically merges a branch, launches a model or authorizes spending.

## Threat model and separation

~~~text
INDEPENDENT TASK AUTHOR / PRIVATE OPERATOR HOST
  authored source WITH answer keys + licence proof (never Git)
                |
                v
   benchmark_sealed seal -- salted 256-bit commitment
        /                           \
        | PUBLIC TASK PACKAGE         | PRIVATE ORACLE + NONCE
        | (task input, split, pins)    | (gold, rubrics, salt)
        v                             v
  MODEL WORKER HOST              INDEPENDENT SCORER HOST
  ONLY public files              never model credentials
  only approved batches             |
        |                            |
  real Codex CLI  OR  Responses API |
  no repo/test oracle access        |
        |                            |
  answers + observable usage        |
        +--------------------------->+
                                benchmark_sealed score
                              DEV / VALIDATION / HOLDOUT
                              per-role, failures retained
~~~

The private oracle directory must be held by the independent scorer **on a separate execution host/account**, not merely a different path on the same machine: a read-only Codex sandbox can still potentially read user-owned files. Only copy `public/`, registry/team pins and batch files to the model host. Never transfer `private/`, the authored source, answer keys, nonce, signing keys, HOLDOUT feedback or trusted evaluator executable into the model container/workspace.

**A new cryptographically randomized private synthetic set is not automatically independent.** For actual benchmark evidence, an outside task author must prepare or attest fresh tasks, task rights, gold and scoring rubric; the independent scorer must be isolated from the BananLoop arm owner. The generator here is an E2E *smoke fixture*, not externally adjudicated truth.

## 0. Export the *existing campaign's* exact pinned roster

**Do not substitute a benchmark fixture roster for a real campaign.**

The v2 Exploration CLI writes `role-registry.json` into the selected campaign's state root. The exact `TeamManifestV1` is durably saved in the shared `campaign-registry.sqlite3` SQLite table `team_manifest_pins.manifest_json`. The manifest is not normally a separate exported file; it is usually first pinned when the role queue admits its initial job, so a preflight-only campaign may not have one.

Select the **actual campaign ID**, not a made-up roster:

```bash
python -m bananloop.cli campaigns list --repo /path/to/campaign-source --json

# Read-only recovery: never invent/modify a pin or run a model.
python -m bananloop.benchmark_export_roster \
  --repo /path/to/campaign-source \
  --campaign-id c-EXACT_32_HEX_ID \
  --out /tmp/bananloop-selected-roster
```

If campaign creation used a nondefault state root, provide the **same** `--state-dir /path/to/actual-state-root`. The command refuses to overwrite an output directory, export into the tracked source checkout, or select an implicit campaign.

On success two files are written:

- `/tmp/bananloop-selected-roster/registry.json` — unchanged validated `RoleRegistryV1` roles, digest matching the selected campaign spec.
- `/tmp/bananloop-selected-roster/team.json` — validated `TeamManifestV1` from the **existing** durable `team_manifest_pins` row, checked against immutable role call/token allocations.

Use these two paths as `--registry` and `--team` for the live benchmark and sealed-suite authoring. The printed report includes both digests and `origin=EXISTING_DURABLE_PIN`.

If it fails with `NO_DURABLE_TEAM_PIN`, an **existing** role registry was found but the campaign never persisted its selected team. Do not fabricate a replacement in `team.json`. To explicitly perform the *same deterministic TeamFactoryV1 composition* used at runtime, for this already-frozen spec and registry, run:

```bash
python -m bananloop.benchmark_export_roster \
  --repo /path/to/campaign-source \
  --campaign-id c-EXACT_32_HEX_ID \
  --out /tmp/bananloop-composed-roster \
  --compose-and-pin-if-missing
```

This explicit operation **writes an immutable team pin to campaign-registry.sqlite3**, and the report sets `origin=EXPLICIT_DETERMINISTIC_RUNTIME_PIN_CREATED`. It does not create or rename role definitions, start a campaign job, execute a model, or authorize any spend. For Exploration it mirrors the runtime request for `researcher` plus `independent_verifier` when present; Optimization requests `optimizer` plus `integration_verifier`. This produces only roles requested or required by the actual registry's minimum instances.

If `role-registry.json` is absent or its digest does not match the saved campaign spec, **both modes fail closed**. The missing definitions cannot be reconstructed from a SHA-256 digest or only the team manifest.

An Exploration team may contain only two role instances even though its registry defines four roles. The live benchmark must therefore **filter cases to role IDs actually pinned in the selected team**, not replace the roster with a four-role synthetic fixture.

## 1. Create fresh private test cases on the scoring host (zero LLM calls)

```bash
pip install -e '.[test,benchmark]'

# 40 new synthetic tasks: 10 each for Researcher/Optimizer/two verifier roles.
# Includes hidden answers; DO NOT commit or send this authored source to the worker.
python -m bananloop.benchmark_generate \
  --private-source /secure/private-authored.json --per-role 10

# Use the actual deployed, immutable v0.2 registry/team JSON from your campaign;
# do not pretend the old offline 'command' fixture is a live Codex role roster.
python -m bananloop.benchmark_sealed seal \
  --authored-suite /secure/private-authored.json \
  --registry /secure/actual-role-registry.json \
  --team /secure/actual-team-manifest.json \
  --public-dir /handoff/role-public \
  --private-dir /secure/role-oracle

# Zero-model-call staging. Batch size=2 is a reasonable Plus-safe smoke start.
python -m bananloop.benchmark_batches stage \
  --public-dir /handoff/role-public \
  --out /handoff/role-batches --batch-size 2
```

The public package includes `cases.json`, `registry.json`, `team.json`, and `commitment.json`; private files include `oracle.json` and `nonce.json`. The public suite binds a SHA-256 commitment computed with a **secret random nonce**. All case rights are explicitly authored by the operator; a generator's `INTERNAL_SANITIZED` declaration is not a third-party legal determination.

## 2. Real Codex CLI execution, using existing subscription access

Codex CLI supports `exec --json --ephemeral`, structured output schema, a read-only sandbox and per-turn token usage. See the [official non-interactive CLI documentation](https://developers.openai.com/codex/non-interactive/). The code uses `--ignore-user-config` / `--ignore-rules`, zero requested CLI tools, one fresh subprocess per task and a bounded timeout. It validates returned objects against the **real v0.2 model-reply Pydantic schemas**, rather than substituting the deterministic fixture worker.

**What this measures:** a model's behavior on a pinned public role prompt with real v0.2 output schema. It does **not** execute the entire durable v0.2 Core, worker fence, trusted evaluator or end-to-end capability-gated campaign. The Researcher/Optimizer input prompt is a benchmark prompt, not necessarily identical to the production prompt referenced by the role's `prompt_digest`.

To explicitly authorize a batch, create a **local** `approval.json` with:

```json
{
  "schema_version": 1,
  "mode": "CODEX_SUBSCRIPTION_NONQUALIFYING_DIAGNOSTIC",
  "suite_sha256": "<sha256 of canonical batch cases JSON>",
  "registry_sha256": "<RoleRegistryV1.digest()>",
  "team_manifest_sha256": "<TeamManifestV1.manifest_digest>",
  "max_cases": 2,
  "max_observed_input_tokens": 60000,
  "max_observed_output_tokens": 6000,
  "max_wall_seconds_per_call": 120,
  "acknowledge_subscription_usage_unverified": true,
  "acknowledge_token_caps_observational": true
}
```

All pins must be computed from the exact batch/registry/team, not placeholders. The CLI refuses mismatched or missing pins. The separate `--i-authorize-codex-exec` switch is mandatory, even with the owner approval file.

```bash
python -m bananloop.benchmark_live_codex \
  --cases /worker/batches/batch-000.json \
  --registry /worker/public/registry.json \
  --team /worker/public/team.json \
  --authorization /worker/approval-batch-000.json \
  --out /worker/runs/batch-000 \
  --i-authorize-codex-exec
```

**Limits:** Codex CLI `turn.completed.usage` is real *CLI-observed* token telemetry; it is **not a provider-signed resource receipt**, a guaranteed hard pre-dispatch token ceiling, or a subscription quota balance. An unknown/error/timeout stops further invocations with **no retry**; an over-budget observation stops subsequent invocations, but tokens already consumed cannot be undone. Cost in USD stays **UNKNOWN**, not `$0`. Run in a dedicated pre-isolated Linux VM/container with only public task material and your minimum needed Codex auth.

## 3. Alternatively: separately billed OpenAI Responses API with explicit cash ceiling

**This is a different paid billing product, not included in ChatGPT Plus.** No API call runs without the `OPENAI_API_KEY` environment variable, a pinned pricing file, a protected operator Ed25519 signing key, a numeric positive spend cap, a separate exact-digest owner approval and `--i-authorize-separate-api-charges`.

The host issues one no-tools `POST /v1/responses` with server-enforced `max_output_tokens`, no automatic retries, and extracts `usage.input_tokens`, `usage.output_tokens`, cached tokens, provider response ID and model/status from the authenticated TLS response. It reserves a conservative input-byte-derived upper bound before dispatch and stops on unknown settlement. **The provider does not supply a cryptographic signature in this API payload.** Our Ed25519 signature authenticates the trusted local operator log; pricing is locally calculated from an owner-pinned rate card, **not confirmed invoice spend**. No signed RPDI-14 provider broker credentials are implied.

A rate card must be pinned to the exact model. Example *format only; the numbers below are illustrative, not current official rates*:

```json
{
  "schema_version": 1,
  "model": "gpt-6-luna",
  "input_usd_per_million": "1.0",
  "output_usd_per_million": "2.0"
}
```

The API approval needs these exact keys: `schema_version`, `mode="OPENAI_API_SEPARATELY_BILLED_DIAGNOSTIC"`, `suite_sha256`, `registry_sha256`, `team_manifest_sha256`, `pricing_sha256`, `max_calls`, `max_spend_microusd`, `max_input_token_bound_per_call`, `max_output_tokens_per_call`, `max_wall_seconds_per_call`, `owner_approves_separate_api_billing=true`.

```bash
# Generate a host-only Ed25519 raw key; never commit or expose private bytes.
python - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from pathlib import Path
key = Ed25519PrivateKey.generate()
p = Path("/secure/operator-ed25519.key")
p.write_text(key.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()).hex())
p.chmod(0o600)
print("PUBLIC KEY HEX:", key.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
PY

python -m bananloop.benchmark_metered_api run \
  --cases /worker/batches/batch-000.json \
  --registry /worker/public/registry.json \
  --team /worker/public/team.json \
  --authorization /worker/paid-approval-batch-000.json \
  --pricing /worker/pricing.json \
  --signing-key /secure/operator-ed25519.key \
  --out /worker/runs/api-batch-000 \
  --i-authorize-separate-api-charges

# Independent verifier checks whether the operator log was altered.
python -m bananloop.benchmark_metered_api verify-meter \
  --receipts /worker/runs/api-batch-000/api-meter.json \
  --operator-public-key-hex <operator-public-key>
```

Do not assert that the verifier has checked OpenAI's invoices or billing ledger. Token figures are **provider-reported HTTPS data**; signature is **operator-issued**; cost is a **priced estimate**. Any missing/uncertain settlement is ineligible and must not be retried blindly.

## 4. Collect responses without loading private oracles on the model host

```bash
# Stage all public batches before running. Each batch requires its own
# exact-SHA approval and separately tracked metering.
python -m bananloop.benchmark_batches merge \
  --public-dir /operator/role-public \
  --batches-dir /operator/role-batches \
  --run-dir /handoff/runs/batch-000 \
  --run-dir /handoff/runs/batch-001 \
  --out /operator/merged

python -m bananloop.benchmark_sealed score \
  --public-dir /operator/role-public \
  --private-dir /secure/role-oracle \
  --responses /operator/merged/responses.json \
  --out /secure/role-scores
```

The scorer includes **missing cases as `INCOMPLETE`**, keeps all originally frozen denominators and reports each `split:role` separately. The output files are `results.json`, `summary.json` and `report.md`. They must remain on the independent scorer side until the evaluation has been frozen; do not put HOLDOUT labels/reports back into adaptive Research Ledger.

## Remaining RPDI-14 blockers

- No external task provider has yet furnished and independently signed **fresh, rights-cleared hidden evaluation** in this conversation; synthetic data are only smoke tests.
- These are role-model-reply diagnostics, **not** full durable campaign E2E with all gateway/fence semantics.
- Codex subscription usage is not a signed provider billing receipt; API estimates are not reconciled against official invoices and API timeout/cancellation cannot unspend an in-flight request.
- Independent operator/scorer attestations, full isolated runtime verification, matched budgets/iterative Codex Direct baseline, preregistered power/precision and frozen scoring remain mandatory before a quantitative release claim.

**No token-consuming model run has been executed in CI or by the PR author.**
