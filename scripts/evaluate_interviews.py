"""Frozen local-rule cases; not a semantic interview quality benchmark."""

import argparse
import json
from pathlib import Path

from careerpilot.interviews import RULE, turn_feedback

ROOT = Path(__file__).resolve().parents[1]


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run():
    data = json.loads((ROOT / "evaluation/interviews-v2.json").read_text(encoding="utf-8"))
    records = []
    for case in data["cases"]:
        text = case.get("text", "") * case.get("repeat", 1)
        feedback = turn_feedback(
            {
                "id": case["id"],
                "kind": case.get("kind", "project"),
                "expected_choice": 1,
                "explanation": "固定题库答案",
            },
            {"skipped": case.get("skipped", False), "choice": case.get("choice"), "text": text},
        )
        for dimension, expected in case["expected"].items():
            assert feedback[dimension] == expected, (case["id"], dimension, feedback)
        for span in feedback["spans"]:
            assert text[span["start"] : span["end"]] == span["quote"], case["id"]
        records.append({"id": case["id"], "passed": True, "feedback": feedback})
    return {
        "scope": data["scope"],
        "rule_version": RULE,
        "total": len(records),
        "passed": len(records),
        "cases": records,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        result = run()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False))
