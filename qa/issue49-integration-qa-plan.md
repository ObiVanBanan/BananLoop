# Issue #49 — integrated R0–R5 QA plan

**Branch:** `integration/issue49-r0-r5-qa-20261022`  
**Scope:** test-only integration of open PRs #47, #52, #53, #54, #55, #56. **Not main, no automated merge or deployment.**

## Provenance / merge audit

| Stage | PR | Exact source HEAD included |
|---|---|---|
| R0 — DEV reflection / scope | #47 | `b009d6fe980bf7656cd7663a384d986562523b1f` |
| R1 — DEV diagnostic evidence | #52 | `caafe5e68e49291d40262a83138ecc14c662447b` |
| R2 — structural causal memory | #53 | `fc0ab76967a4fd58e15bf1f144beb2ab4ccb1350` |
| R3 — bounded preflight/runtime repair | #54 | `a122d48cf58dafcfce053d6867c3d521624b7907` |
| R4 — bugfix/performance/Qdrant fixtures | #55 | `b917d0ca618decf92178dc22c2bb459592640f31` |
| R5 — portable traces/decision receipts | #56 | `5186ec923fa3633ee2dd4c91e57b3af816182a53` |

The branch starts from R5 head. Integration-only merge PR #57 included R0's late read-only scope fix; integration-only merge PR #58 included four R3 commits absent from R4/R5 (repair attempt budget, smoke setup, candidate traceback remediation and tests). GitHub verified that the integrated branch is an *ahead-only descendant* of all six listed source heads. Both merges were conflict-free according to GitHub. **No integrated full pytest run has been performed yet; historical passing PR tests do not count for this new merge head.**

## Local test instructions (Windows PowerShell, from BananLoop root)

```powershell
git fetch origin
git switch --track origin/integration/issue49-r0-r5-qa-20261022
# For an existing local branch:
# git switch integration/issue49-r0-r5-qa-20261022
# git pull --ff-only

git rev-parse HEAD
python -m pip install -e ".[test]"
python -m pytest -q -o addopts='' --tb=short

# Focused stop/recovery, scientific research, scope, receipts and trace:
python -m pytest -q -o addopts='' --tb=short tests/test_completion.py tests/test_engine.py tests/test_research.py tests/test_metric_semantics.py tests/test_candidate_preflight.py tests/test_failures.py tests/test_trace.py
```

Repeat `tests/test_completion.py::test_stop_cli_cancels_running_agent_and_preserves_source` three times in the same Windows/Python 3.12 environment as the original intermittent failure. Confirm durable `USER_STOPPED`, cancelled in-flight action, cleared active attempt and zero open reservations. **Never auto-retry an unknown orphaned external agent action.**

## Disposable end-to-end fixtures

These run public CLI paths and isolated synthetic data; the command adapter requires no cloud model.

```powershell
$qa = Join-Path $env:TEMP "bananaloop-issue49-integrated"
New-Item -ItemType Directory -Force $qa | Out-Null

python examples/issue49/run_bugfix_dogfood.py --work-dir (Join-Path $qa "bugfix")
python examples/issue49/run_performance_dogfood.py --work-dir (Join-Path $qa "performance")
python examples/issue49/run_matched_budget_comparison.py --work-dir (Join-Path $qa "matched")
```

Optional Qdrant retrieval test (requires Docker Desktop running and free TCP 6333):

```powershell
docker run --rm --name bananloop-issue49-qdrant -p 6333:6333 -d qdrant/qdrant:v1.15.4
python examples/issue49/run_rag_dogfood.py --work-dir (Join-Path $qa "rag")
docker stop bananloop-issue49-qdrant
```

Known scope: synthetic 8-product retrieval with a local service; not production RAG or generated-answer quality. If you already have Qdrant on 6333, do not start another container; use the existing compatible service only if the fixture and other collections can be kept isolated.

Gold Logic v0.3 is a separate external archive, **not part of this branch**; run its `run_gold.py --env` to confirm that imported modules come from this integration checkout, then rerun all 51 cases. This tests core policy/research decisions only, not the 18 full Gold Journeys.

## Acceptance & release blockers

- [ ] Full pytest on this exact merge head (report Python version, SHA and failed node IDs; no guessing test counts).
- [ ] 51/51 Gold v0.3 with correct `--env` module paths and compatibility.
- [ ] Stop and interruption/retry E2E passes repeatedly on Windows.
- [ ] Candidate repair, repaired snapshot smoke and resumed repair do not mutate evaluator/frozen data.
- [ ] Independent DEV/VALIDATION/HOLDOUT, anti-leakage and scope protections.
- [ ] Bugfix, CPU performance and (if local Qdrant available) retrieval dogfood receipts and final outcomes.
- [ ] `bananloop trace export` has hashed manifest, sanitized fields, and safe handling of old campaigns.
- [ ] Resolve hosted CI no-runner issue or document a separate explicit operator waiver. Current Actions jobs show `runner_id=0`, `steps=[]` and do not constitute executed CI.
- [ ] Human review of integrated diff before any merge into a shared product branch.

**Do not infer reliability from the earlier 336-passed R5 standalone suite:** this branch additionally merges post-divergence R3 and R0 code and must be tested independently. No production state or hidden labels should be uploaded to GitHub; share aggregate summaries / hashes / redacted traces only.

## Known limits

- Offline dogfood uses a scripted Command adapter; it does **not** prove realistic autonomous Pi/Codex research in all domains.
- R3 is a bounded Python candidate preflight/smoke repair slice, not the complete approval manager or operator dashboard in #37.
- Gold Journeys J10/J16 and the rest require explicit whole-campaign adapter certification rather than reclassifying partial subsystem tests.
- Cross-platform GitHub Actions may fail before runner assignment independently of Python test correctness.
