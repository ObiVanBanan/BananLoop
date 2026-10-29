# Mini analog matcher — CPU calibration

Implement `find_analog(query: str, catalog: list[dict]) -> str | None` in `matcher.py`.

Each catalog entry includes `article`, `dn`, `pn`, `material`, and `joining_type`.
Match only when the query explicitly specifies **all four** attributes.
Use exact integer DN and PN matching, not numeric proximity.
Recognize Russian and English notation: `Ду20`/`DN20`,
`Ру16`/`PN16`, `стальной`/`сталь`/`steel`,
`латунный`/`латунь`/`brass`,
`фланцевый`/`flange`, `резьбовой`/`thread`,
`сварной`/`weld`.
Return the matched `article` string only when exactly one catalog entry
satisfies the full set of attributes. Otherwise return `None`.

Do not hardcode query strings or test-case outputs. Do not modify the evaluator,
the benchmark datasets, or any file other than `matcher.py`.
The evaluator gives only aggregate DEV quality. It is not an allowed source for
the agent. The initial baseline intentionally returns `None`.
All tools must run on CPU; no external services or API calls in evaluator.
