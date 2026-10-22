from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EVALUATOR = r'''import json
from pathlib import Path
value = float(Path("value.txt").read_text().strip())
regression = float(Path("guard.txt").read_text().strip())
print(json.dumps({
    "schema_version": 1,
    "status": "ok",
    "metrics": {"quality": value, "regressions": regression},
    "checks": {"tests": True}
}))
'''

AGENT = r'''import json
import sys
from pathlib import Path
req = json.load(sys.stdin)
role = req["role"]
idx = len(req.get("history", [])) + 1
if role == "propose":
    print(json.dumps({
        "hypothesis": f"fixture step {idx}",
        "rationale": "exercise accept/reject/promotion deterministically",
        "expected_effect": "quality should increase",
        "files_or_area": ["value.txt", "guard.txt"],
        "falsification_condition": "quality does not improve or regressions exceeds zero",
        "mechanism_key": f"fixture-step-{idx}"
    }))
else:
    hypothesis = req["hypothesis"]["mechanism_key"]
    if hypothesis == "fixture-step-1":
        Path("value.txt").write_text("1\n")
        Path("guard.txt").write_text("0\n")
    elif hypothesis == "fixture-step-2":
        Path("value.txt").write_text("2\n")
        Path("guard.txt").write_text("1\n")
    else:
        Path("value.txt").write_text("3\n")
        Path("guard.txt").write_text("0\n")
    print(json.dumps({"summary": hypothesis, "changed_paths": ["value.txt", "guard.txt"], "verification_notes": "fixture", "blockers": []}))
'''


def run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True)


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "fixture-project").resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "evaluator.py").write_text(EVALUATOR, encoding="utf-8")
    (target / "agent.py").write_text(AGENT, encoding="utf-8")
    (target / "value.txt").write_text("0\n", encoding="utf-8")
    (target / "guard.txt").write_text("0\n", encoding="utf-8")
    run(["git", "init"], target)
    run(["git", "config", "user.email", "fixture@example.com"], target)
    run(["git", "config", "user.name", "Fixture"], target)
    run(["git", "add", "."], target)
    run(["git", "commit", "-m", "fixture baseline"], target)
    print(target)


if __name__ == "__main__":
    main()
