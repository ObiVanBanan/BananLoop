from __future__ import annotations

from pathlib import Path

from bananloop.evaluator_binding import runtime_descriptor
from bananloop.evaluators import CommandEvaluator


def test_command_evaluator_executes_the_runtime_receipt_executable(tmp_path: Path):
    expected = runtime_descriptor({"dev": ["python"]})["runners"]["dev"][
        "resolved_executable"
    ]
    assert expected is not None
    code = (
        "import json, pathlib, sys; "
        "matches = pathlib.Path(sys.executable).resolve() == pathlib.Path(sys.argv[1]).resolve(); "
        "print(json.dumps({'status':'ok','metrics':{'runtime_match':float(matches)}}))"
    )

    result = CommandEvaluator(["python", "-c", code, expected]).evaluate(
        tmp_path,
        timeout=10,
    )

    assert result.metrics["runtime_match"].value == 1.0
