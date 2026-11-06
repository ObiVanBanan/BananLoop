"""Conservative, local intent signatures for repeat-experiment detection.

This is a bounded synonym map rather than an embedding or a claim that two
scientific questions are equivalent. It only flags a semantic repeat when the
same controlled variable family and the same direction are explicit.
"""
from __future__ import annotations

import re


_WORDS = re.compile(r"[a-z0-9]+")
_SCALE_ENTITIES = {
    "data", "dataset", "datasets", "example", "examples", "input", "inputs",
    "observation", "observations", "record", "records", "sample", "samples",
    "training", "batch", "batches",
}
_SCALE_MEASURES = {
    "amount", "count", "counts", "number", "numbers", "quantity", "scale", "scales",
    "size", "sizes", "volume", "volumes",
}
_AXIS_GROUPS = {
    "ordering": {"order", "ordering", "sequence", "sequencing", "shuffle", "shuffling",
                 "permutation", "permutations"},
    "threshold": {"threshold", "thresholding", "cutoff", "cutoffs", "boundary"},
    "learning_rate": {"learningrate", "lr", "step_size"},
    "architecture": {"architecture", "structure", "layers", "depth", "width"},
    "runtime_environment": {"runtime", "platform", "hardware", "device", "environment"},
    "replication": {"replicate", "replication", "reproduce", "reproducibility"},
}
_GENERIC_CHANGE = {
    "adjust", "alter", "change", "compare", "test", "vary", "sweep", "measure",
    "evaluate", "effect", "impact", "affect",
}
_UP = {"increase", "increasing", "raise", "raising", "grow", "growing", "larger", "higher"}
_DOWN = {"decrease", "decreasing", "lower", "lowering", "reduce", "reducing", "smaller"}
_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with", "whether",
    "does", "do", "is", "are", "by", "from", "after", "before", "while", "fixed", "hold",
    "input", "data", "dataset", "example", "examples", "observation", "observations",
    "record", "records", "sample", "samples", "training", "batch", "batches",
    "size", "count", "number", "amount", "quantity", "scale", "volume",
}


def experiment_intent(value: str) -> tuple[tuple[str, ...], str] | None:
    """Return explicit controlled-variable families and direction, if present."""
    words = set(_WORDS.findall(value.casefold()))
    if not words:
        return None
    axes = {name for name, terms in _AXIS_GROUPS.items() if words & terms}
    if words & _SCALE_ENTITIES and words & _SCALE_MEASURES:
        axes.add("sample_scale")
    if not axes:
        return None
    direction = "up" if words & _UP else "down" if words & _DOWN else "unspecified"
    # Opposite directional changes are distinct experiments. Generic changes
    # remain equivalent to a direction-specific variant only when not opposite.
    operation = direction if direction != "unspecified" else (
        "change" if words & _GENERIC_CHANGE else "unspecified"
    )
    residual = tuple(sorted(words - _STOP - _GENERIC_CHANGE - _UP - _DOWN))
    return tuple(sorted(axes)), operation + ":" + " ".join(residual)


def same_experiment_intent(left: str, right: str) -> bool:
    """Conservatively match paraphrases with the same explicit axis and action."""
    left_intent = experiment_intent(left)
    right_intent = experiment_intent(right)
    if left_intent is None or right_intent is None:
        return False
    left_axes, left_operation = left_intent
    right_axes, right_operation = right_intent
    if left_axes != right_axes:
        return False
    left_direction = left_operation.split(":", 1)[0]
    right_direction = right_operation.split(":", 1)[0]
    if (left_direction in {"up", "down"} and right_direction in {"up", "down"}
            and left_direction != right_direction):
        return False
    # The remaining words identify the outcome/condition. Keep a 2/3 token
    # overlap requirement to avoid treating every test of an axis as identical.
    left_terms = set(left_operation.partition(":")[2].split())
    right_terms = set(right_operation.partition(":")[2].split())
    if not left_terms or not right_terms:
        return True
    overlap = len(left_terms & right_terms) / min(len(left_terms), len(right_terms))
    return overlap >= 2 / 3
