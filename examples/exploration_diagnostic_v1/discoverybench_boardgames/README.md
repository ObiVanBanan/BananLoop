# DiscoveryBench board-games sample (diagnostic only)

Downloaded as part of BananLoop RPDI-14 exploratory research *task preparation*, 2026-11-05. Public source: [allenai/discoverybench](https://github.com/allenai/discoverybench) commit [`c31fcf011e070f021a5f5b906896d0821f6880e8`](https://github.com/allenai/discoverybench/commit/c31fcf011e070f021a5f5b906896d0821f6880e8).

- Source data: `discoverybench/synth/test/board-games_0_2/data.csv`
- Source question: `discoverybench/synth/test/board-games_0_2/metadata_1.json`, qid 10
- Upstream repository license: [Open Data Commons Attribution License (ODC-BY)](https://github.com/allenai/discoverybench/blob/c31fcf011e070f021a5f5b906896d0821f6880e8/LICENSE). Credit: Majumder et al., *DiscoveryBench: Towards Data-Driven Discovery with Large Language Models* (2024 / ICLR 2025).
- Local dataset: 500 rows, 42 CSV columns; all rows/columns kept; no transformations.
- Reuse is for diagnostic evaluation only. **No official HMS score** or qualifying independent result is asserted. No agent run has occurred.
- The upstream public repository includes answers and evaluation code; therefore this cannot be treated as a sealed or uncontaminated test without separate controls.
- Source metadata includes supplementary information. We stage only one public question in `TASK.md`, without gold/answer keys. Scoring must be operator-only.

Quick local preflight from the source directory:

```bash
python -c 'import csv; p="data.csv"; r=list(csv.DictReader(open(p, encoding="utf-8"))); print(len(r), len(r[0]), list(r[0])[-5:])'
```

For actual controlled experiments, use independent immutable copies for BananLoop Full and Codex Direct, keep the same visible DEV information, freeze a hypothesis evaluation rubric, preserve model outputs, and obtain RPDI-14 run authorization first.
