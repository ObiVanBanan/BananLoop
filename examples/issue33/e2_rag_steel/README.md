# BananLoop issue #33 E2: technical constraint filtering

This local experiment pins a 70-document-derived steel catalog subset, a Qdrant index built with `paraphrase-multilingual-MiniLM-L12-v2` revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, and fixed DEV/validation splits under `fixtures/`. The evaluator verifies the catalog and split hashes, model revision, vector size, and Qdrant alias target before scoring.

The benchmark has 54 DEV cases and 18 validation cases. Each product group contributes seven positive query forms, one generic no-match control, and one deliberately contradictory technical-constraint control. Positive queries must return the expected linked article; negative queries must return no linked articles. Metrics cover hard pass rate, positive recall, overall false matches, contradiction violations, and generic no-match false matches. The tested change suppresses LD results for an article-anchored query only when none of that source’s candidates satisfies every requested technical constraint; broad analogue queries keep their existing mismatch explanations. Diagnostics distinguish retrieval misses, wrong linked-article selection, and constraint mismatches.

Run the evaluator from a compatible `rag_steel` checkout with its project environment and a reachable Qdrant service containing the pinned index:

```sh
QDRANT_URL=http://127.0.0.1:6333 QDRANT_COLLECTION_ALIAS=issue33_e2_base .venv/bin/python examples/issue33/e2_rag_steel/evaluator.py --split dev
```

The data is derived from the repository's public evaluation catalog and query patterns. Contradiction controls are constructed negatives; this experiment does not establish independent human-approved truth or a protected holdout.
