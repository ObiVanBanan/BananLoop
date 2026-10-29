# Technical position matcher

Improve `solution.py`, which matches short technical position statements to
their canonical engineering concepts. The intended metric is exact-match
quality on a fixed CPU-only DEV set. Editable scope is only `solution.py`.
Use the provided public task description and DEV research context to propose a
falsifiable next mechanism. Do not claim measured results that are not present
in the request. The proposer is only asked for hypotheses; this harness does
not implement candidates or run evaluation.

Current implementation uses normalized token overlap. Potential error classes
include synonyms, negation, acronym expansion, and compositional word order.
