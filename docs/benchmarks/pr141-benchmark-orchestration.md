# PR #141 benchmark orchestration (diagnostic, no implicit model calls)

This is an **operator-side staging and scoring wrapper** around the already present
`benchmark_generate`, `benchmark_sealed`, `benchmark_batches` and `benchmark_live_codex`
modules. It does not replace the signed RPDI-14 protocol, launch unapproved
ChatGPT requests, or turn synthetic/public tasks into independent release scores.

## A. Prepare the 40 role cases

Use an actual frozen v0.2 campaign roster, exported from the host that owns
the selected SQLite campaign database. Do not invent four roles for a team
which pinned only two. An Exploration and an Optimization campaign may need
**separate** role diagnostic runs to cover all four roles.

```bash
python -m bananloop.benchmark_export_roster \
  --repo . --campaign-id c-EXACT_CAMPAIGN_ID \
  --out /tmp/role-roster-actual

python -m bananloop.benchmark_suite prepare-roles \
  --registry /tmp/role-roster-actual/registry.json \
  --team /tmp/role-roster-actual/team.json \
  --private-root /secure/operator/roles-001 \
  --public-root /tmp/benchmark-public-001 \
  --batches-root /tmp/benchmark-batches-001 \
  --per-role 10 --batch-size 2
```

With all four roles pinned, this generates 40 cases, split into
**8 DEV, 8 VALIDATION, 24 HOLDOUT**. The generation uses cryptographic
randomness; each fresh run gets new case IDs/values and a private 256-bit
oracle nonce. The resulting `public/cases.json` never contains answer
keys. The private root contains `authored.json`, `oracle/oracle.json` and
`oracle/nonce.json`, and **must not be copied to the model host**.
If only two roles are pinned, only those roles' cases are staged and the
manifest reports the reduced denominator explicitly.

The output paths must be fresh, non-overlapping, outside this checkout.
No models are called. These are **synthetic role-contract checks**, not
independent competency claims; optimizer cases do not execute a real training
job, and verifier cases use authored boolean evidence packets.

## B. Run authorized role batches

Transfer **only** the worker-facing public folder, public batch folder and
the minimum required Codex authentication capability to an isolated worker host.
The worker must not have access to the private operator root.

Each batch must receive its own **exact-digest authorization JSON** and an
explicit execution switch. See
[Live sealed role benchmark](../benchmarks/live-sealed-role-benchmark.md)
for the complete authorization fields and resource accounting limitations.

```bash
python -m bananloop.benchmark_live_codex \
  --cases /worker/batches/batch-000.json \
  --registry /worker/public/registry.json \
  --team /worker/public/team.json \
  --authorization /worker/approval-batch-000.json \
  --out /worker/runs/batch-000 \
  --i-authorize-codex-exec
```


For multiple **individually authorized** batches, the wrapper can dispatch
successive batches with no automatic retries, stopping at the first unknown or
failed settlement. It defaults to a **zero-call plan**, with only one batch
selected by default:

~~~bash
python -m bananloop.benchmark_suite run-role-batches \
  --public-root /worker/public \
  --batches-root /worker/batches \
  --authorizations-root /worker/approvals \
  --run-root /worker/new-run-root \
  --max-batches 2
~~~

To actually spend subscription requests, create **one valid owner approval
file per selected batch** (for example `approval-batch-000.json`), review
every SHA and budget, then explicitly add
`--i-authorize-codex-exec`. Every invocation must use a **fresh**
`--run-root`; do not replay partial/unknown batches. This runner cannot
enforce a provider-side hard subscription quota.

The CLI uses the existing ChatGPT subscription where available, not a
separately billed API key. Usage is **CLI-observed**, not provider-signed.
A stopped/unknown call must not be retried automatically. One batch is two
cases by default to avoid burning through a limited subscription quota.

Bring the **recorded public run outputs and metering ledgers** to the
operator/scorer machine. Do not bring back hidden oracles to the worker host.

## C. Merge and score without losing incomplete cases

On an operator machine physically isolated from the model worker:

```bash
python -m bananloop.benchmark_suite score-roles \
  --private-root /secure/operator/roles-001 \
  --public-root /operator/public-001 \
  --batches-root /operator/batches-001 \
  --run-dir /operator/runs/batch-000 \
  --run-dir /operator/runs/batch-001 \
  --merged-root /operator/merged-001 \
  --score-root /secure/operator/scored-001
```

Optional `--meter-ledger /operator/runs/batch-000/usage.observed.json`
can be repeated. The scorer verifies the public suite commitment and private
oracle nonce, preserves frozen case denominators, marks every missing answer
`INCOMPLETE` and reports scores by **split and role**. The status is always
`SEALED_LOCAL_DIAGNOSTIC_NOT_RPDI14`, even with a perfect score. Costs remain
`UNKNOWN` unless specific non-provider-signed estimation evidence exists.

## D. Stage DiscoveryBench for an E2E/public DEV diagnostic

```bash
python -m bananloop.benchmark_suite stage-discoverybench \
  --source-dir examples/exploration_diagnostic_v1/discoverybench_boardgames \
  --out /tmp/discoverybench-pair-001
```

This checks the bundled board-games CSV has exactly **500 rows / 42 columns**,
copies only `TASK.md` and `data.csv` to
`arms/bananloop/` and `arms/codex/`, and records exact SHA-256 digests.
No gold answers, model clients, evaluator credentials or hidden cases are
staged. It is the **same public task** in both isolated arm folders.

For the BananLoop arm, use the **existing** `bananloop v2 explore` / explicit
`v2-pivot decision` / `v2-pivot execute` / `v2 optimize` workflow with its
frozen permissions, evaluator and separately authorized provider calls.
Codex Direct must receive equivalent DEV information and iterative access,
not a one-shot prompt. Staging is not execution and this public DiscoveryBench
sample is not a sealed independent scoring set.

The shell commands and exact role/goal/fixture flags for a full benchmark
cannot be decided by this wrapper; they require a frozen task/evaluator
protocol with a valid independent scorer. The preexisting
`benchmarks/v02_full/` signed gating and scorer must remain the authority
for RPDI-14 Full vs iterative Codex Direct. Do **not** use synthetic role
accuracy or published DiscoveryBench answers as a release comparison.

## E. Remaining external benchmark work

The repo's [source register](../benchmarks/agent_capability_v0.2/dataset-sources.md)
names AutoResearchBench, SWE-bench Verified, MLS-Bench and AutoResearchExam.
It does **not** bundle a license-cleared, fresh, sealed set for paired v0.2
Full-vs-Codex scoring. Before running a qualifying comparison:

1. Select Optimization and Exploration task rosters separately, verify per-task
   rights, freeze source commits, evaluator assets, metrics and feature flags.
2. Preregister matching model/iteration/tool/budget constraints; independently
   attest resource meters, evaluator isolation and minimum sample size.
3. Run both arms in isolated sandboxes under the existing RPDI-14 signed
   authorization and blinded scorer, preserving failures/unknown accounting.
4. Export per-track paired results with signed evidence and reviewable hashes.

**Do not assign a task, launch a paid provider, merge #141 or claim a release
win until those gates pass.**

Offline regression:

```bash
python -m pytest tests/test_benchmark_suite.py \
  tests/test_benchmark_batches.py tests/test_live_sealed_benchmark.py -q
```
