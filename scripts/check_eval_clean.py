from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "eval.json"


def _normalized(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("generated_at", None)
    return payload


def main() -> int:
    committed = _normalized(RESULT)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_eval.py")],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    generated = _normalized(RESULT)
    if generated != committed:
        print(
            "Evaluation output differs from results/eval.json after ignoring generated_at.",
            file=sys.stderr,
        )
        return 1
    print("Evaluation metrics and evidence match the committed result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
