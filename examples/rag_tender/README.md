# rag_tender bridge

BananLoop core does not import RAG/Qdrant/nomenclature code. Connect `rag_tender` through its existing reproducible evaluator.

The bridge contract is only stdout JSON:

```json
{
  "schema_version": 1,
  "status": "ok",
  "metrics": {
    "primary_metric_name": 0.81,
    "latency_ms": 220
  },
  "checks": {
    "tests": true
  }
}
```

Recommended integration:

1. Add a small script **inside rag_tender** that runs the same frozen holdout/evaluation command you already trust.
2. Make it print the BananLoop JSON contract above and exit non-zero only for infrastructure/protocol failures.
3. Protect that bridge, its dataset/config and acceptance tests with repeated `--protect` flags.
4. Restrict `--editable` to the implementation/config paths Codex may change.
5. Start with 2–3 experiments and explicit call/deadline budgets before a long campaign.

Example shape (replace metric/path names with the real evaluator):

```powershell
bananloop init `
  --repo C:\work\rag_tender `
  --goal "Improve hidden holdout quality without breaking tests" `
  --eval-cmd "python scripts/bananloop_eval.py" `
  --metric quality `
  --direction maximize `
  --target 0.85 `
  --min-improvement 0.005 `
  --guard "latency_ms<=500" `
  --check tests `
  --editable "src/**" `
  --editable "config/**" `
  --protect "scripts/bananloop_eval.py" `
  --protect "tests/**" `
  --agent codex `
  --max-experiments 3

bananloop run --repo C:\work\rag_tender
```

The evaluator remains the source of truth. Codex's own statement that a change improved the project is never used for acceptance.
