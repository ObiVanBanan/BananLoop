# BL-040 · Full offline acceptance

Status is open. At `be24a260`, the fake 24-arm suite exercises the actual
backend orchestration with substituted commands. It verifies the frozen pivot
scope and preserves the same deadline across Exploration, Harbor preparation
and Optimization. The campaign CLI's offline safety tests cover exact scope,
fail-closed pivot checks and the ordinary Supervised policy.

The previous Full archive at
`/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p2-prepared-i4fjpx2i/final-archive`
pins `ada1f18`. Its shortest-CI receipt records
`BENCHMARK_AUTO_CHILD_CREATED` and `ALL_NODES_COMPLETE`; its report and
launcher checksums match the archive index. Newton and SOPCC ended in
`OPTIMIZATION_FAILED`. These were archived results from the earlier harness.

A clean Full run against the current code with the real Harbor adapter
remains blocked. The archived runtime's 12 descriptors validate, but this
environment denies the Docker socket and the current preflight remains
`PREP_BLOCKED`. BL-042 still requires an outer watchdog and verified
container cleanup. Live gate remains closed.
